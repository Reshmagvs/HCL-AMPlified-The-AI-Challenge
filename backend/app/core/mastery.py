"""The learner state model: what we believe they know, and how sure we are.

Self-report is the weakest signal in education technology, and the brief's
"capture experience level" is exactly that. This module treats it as one source
among three, ranked by how much evidence stands behind it::

    milestone  >  diagnostic  >  self

Three rules follow from that ranking, and together they are the whole model:

**Self-report is capped at 0.4.** The mastery threshold is 0.7, so a claim can
never on its own remove a skill from the path. It shortens the diagnostic by
telling us where to look; it does not replace it.

**A weaker source never overwrites a stronger one.** Once a diagnostic has
measured a skill, a later self-report cannot raise it back up. Without this the
model would drift towards whatever the learner last said about themselves.

**Correct answers nudge prerequisites, they do not set them.** Answering a
backpropagation question correctly is real evidence that you know derivatives --
but it is indirect. Ancestors receive a decaying nudge (0.35 at one hop, 0.175
at two) applied as a floor, never as an assignment, and the nudge itself is
recorded as `diagnostic` evidence with lowered confidence.

"I don't know" is recorded distinctly from a wrong answer. Both leave the score
low, but a guess that happens to be wrong and an honest abstention carry
different information about the learner, and the diagnostic's confidence
estimate treats abstention as the cleaner signal.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.core.skill_graph import SkillGraph

SOURCE_RANK: dict[str, int] = {"unmeasured": 0, "self": 1, "diagnostic": 2, "milestone": 3}

SELF_REPORT_CAP = 0.4
MASTERY_THRESHOLD = 0.7

CORRECT_SCORE = 1.0
WRONG_SCORE = 0.15
DONT_KNOW_SCORE = 0.05

ANCESTOR_NUDGE = 0.35
ANCESTOR_DECAY = 0.5
ANCESTOR_MAX_HOPS = 2


@dataclass(frozen=True)
class MasteryValue:
    """One belief about one skill."""

    score: float
    source: str = "unmeasured"
    confidence: float = 0.0
    evidence_q_ids: tuple[int, ...] = field(default=())

    @property
    def is_mastered(self) -> bool:
        return self.score >= MASTERY_THRESHOLD


class MasteryTable:
    """An in-memory view of one learner's mastery, with the update rules applied.

    Routers load rows into this, apply updates, and write back whatever changed.
    Keeping the rules here rather than in the router means they are unit-testable
    without a database.
    """

    def __init__(self, values: dict[str, MasteryValue] | None = None) -> None:
        self._values: dict[str, MasteryValue] = dict(values or {})
        self.dirty: set[str] = set()

    # -- reads --------------------------------------------------------------
    def get(self, skill_id: str) -> MasteryValue:
        """Unmeasured is 0.0 -- absence of evidence is not evidence of skill."""
        return self._values.get(skill_id, MasteryValue(score=0.0, source="unmeasured"))

    def score(self, skill_id: str) -> float:
        return self.get(skill_id).score

    def is_mastered(self, skill_id: str) -> bool:
        return self.get(skill_id).is_mastered

    def items(self) -> list[tuple[str, MasteryValue]]:
        return sorted(self._values.items())

    def gap(self, required: set[str]) -> set[str]:
        """The skills in ``required`` the learner has not demonstrated."""
        return {skill for skill in required if not self.is_mastered(skill)}

    # -- writes -------------------------------------------------------------
    def set(
        self,
        skill_id: str,
        score: float,
        source: str,
        *,
        confidence: float = 1.0,
        evidence_q_ids: tuple[int, ...] = (),
        force: bool = False,
    ) -> bool:
        """Apply the precedence rules. Returns True when the value changed.

        ``force`` exists for adaptation events (``too_easy``, ``milestone_failed``)
        which are deliberate corrections rather than fresh measurements.
        """
        if source == "self":
            score = min(score, SELF_REPORT_CAP)
        score = max(0.0, min(1.0, score))

        current = self.get(skill_id)
        if not force and SOURCE_RANK[source] < SOURCE_RANK[current.source]:
            return False
        if not force and SOURCE_RANK[source] == SOURCE_RANK[current.source] and score == current.score:
            return False

        merged_evidence = tuple(dict.fromkeys([*current.evidence_q_ids, *evidence_q_ids]))
        self._values[skill_id] = MasteryValue(
            score=score, source=source, confidence=confidence, evidence_q_ids=merged_evidence
        )
        self.dirty.add(skill_id)
        return True

    def nudge(self, skill_id: str, floor: float, *, confidence: float, question_id: int) -> bool:
        """Raise a skill to at least ``floor`` as indirect evidence. Never lowers.

        A nudge may not overwrite a direct measurement of equal or higher rank,
        and it may not push a skill past the mastery threshold on its own.
        """
        current = self.get(skill_id)
        capped = min(floor, MASTERY_THRESHOLD - 0.01)
        if current.source == "milestone" or capped <= current.score:
            return False
        if current.source == "diagnostic" and current.confidence >= confidence:
            return False

        self._values[skill_id] = MasteryValue(
            score=capped,
            source="diagnostic",
            confidence=confidence,
            evidence_q_ids=tuple(dict.fromkeys([*current.evidence_q_ids, question_id])),
        )
        self.dirty.add(skill_id)
        return True


def grade_answer(*, correct: bool, dont_know: bool) -> float:
    """Score a single diagnostic response.

    An abstention scores lower than a wrong answer: a wrong guess still carries
    a chance the learner half-knows the material, whereas "I don't know" is an
    unambiguous statement that they do not.
    """
    if dont_know:
        return DONT_KNOW_SCORE
    return CORRECT_SCORE if correct else WRONG_SCORE


def apply_answer(
    table: MasteryTable,
    graph: SkillGraph,
    *,
    skill_id: str,
    correct: bool,
    dont_know: bool,
    question_id: int,
    source: str = "diagnostic",
) -> None:
    """Record one answer, then propagate indirect evidence to prerequisites."""
    table.set(
        skill_id,
        grade_answer(correct=correct, dont_know=dont_know),
        source,
        confidence=1.0,
        evidence_q_ids=(question_id,),
    )
    if correct and not dont_know:
        _nudge_ancestors(table, graph, skill_id, question_id)


def _nudge_ancestors(
    table: MasteryTable, graph: SkillGraph, skill_id: str, question_id: int
) -> None:
    """Decaying positive evidence for the prerequisites of a correct answer."""
    frontier = list(graph.require(skill_id).prerequisites)
    seen: set[str] = {skill_id}

    for hop in range(1, ANCESTOR_MAX_HOPS + 1):
        strength = ANCESTOR_NUDGE * (ANCESTOR_DECAY ** (hop - 1))
        next_frontier: list[str] = []
        for ancestor in frontier:
            if ancestor in seen:
                continue
            seen.add(ancestor)
            table.nudge(ancestor, strength, confidence=0.4 / hop, question_id=question_id)
            next_frontier.extend(graph.require(ancestor).prerequisites)
        frontier = next_frontier
        if not frontier:
            break


def confidence(table: MasteryTable, gap: set[str], graph: SkillGraph) -> float:
    """How well the current beliefs cover the skills that actually matter.

    Weighted by downstream unlock count, because being unsure about a bottleneck
    skill is more costly than being unsure about a leaf. Returns 1.0 for an empty
    gap so the diagnostic terminates rather than looping on nothing.
    """
    if not gap:
        return 1.0
    total_weight = 0.0
    measured_weight = 0.0
    for skill in gap:
        weight = 1.0 + graph.downstream_unlock_count(skill)
        total_weight += weight
        measured_weight += weight * table.get(skill).confidence
    return round(measured_weight / total_weight, 4) if total_weight else 1.0
