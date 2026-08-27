"""Building a curriculum for a topic nobody curated.

The seed graph knows 152 skills across six technology tracks. A learner who asks
for quantum computing, organic chemistry or a school exam is outside it, and the
old behaviour was the worst possible one: cosine search returned the *nearest*
node, so "quantum computing" silently became a programming topic and the learner
was given a confident, wrong plan. Nearest-neighbour over a closed set has no way
to say "I do not know this", which is the answer that was needed.

So the closed set is opened. This module does three things in order.

**Decide whether the graph really covers the goal.** Not "is something similar
here" -- *is this subject in here*. The test is calibrated against the graph
itself rather than against a number someone picked: every curated node's name is
embedded and scored against its own node, which measures what a genuine
name-match looks like *for the embedder currently loaded*. A goal scoring below
the weak end of that distribution is not in the graph, whatever the nearest hit
happens to be. When a language model is available it then confirms the shortlist
in words, which catches the case similarity gets wrong in the other direction.

**Design the syllabus.** The model proposes skills for the topic and, for each,
which earlier skills it depends on. It proposes *structure*, never facts and
never links. Acyclicity is guaranteed structurally rather than checked: a
prerequisite may only reference an earlier index, so a cycle cannot be expressed.
Everything else is clamped into range.

**Find real material.** One live search per skill, every URL fetched, everything
that does not serve discarded, and title, description, provider, format and cost
read off the page that answered. Duration is a measured reading time, not a
guess. Nothing here is invented, because a learning path made of plausible dead
links is worse than no learning path.

The result is merged into the graph and the catalogue through ``core.store``, so
the planner, the diagnostic and the dashboard all treat a discovered topic
exactly as they treat a curated one. No code downstream of this module knows the
difference, which is the point: sequencing was always the product, and the graph
was only ever its input.
"""

from __future__ import annotations

import logging
import math
import re
import time
import unicodedata
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any

import numpy as np
from pydantic import BaseModel, Field

from app.core import retrieval, store, websearch
from app.core.embeddings import get_embedder
from app.core.skill_graph import curated_skills, load_graph, reset_graph_cache
from app.llm import get_provider
from app.llm.base import ProviderUnavailable, SchemaViolation, call_with_schema
from app.llm.prompts import COVERAGE_CHECK, SYLLABUS_DESIGN

logger = logging.getLogger(__name__)

MIN_SKILLS = 4
# What decoding is told to produce. The floor is above the useful minimum
# because the sampler stops as soon as ``minItems`` is satisfied, and the tail of
# a syllabus is where the interesting skills live. The ceiling is what bounds how
# long a learner waits: the model fills to ``maxItems`` given the chance, and on
# a CPU at roughly 5 tokens a second each extra skill costs about ten seconds.
TARGET_SKILLS = 8
MAX_SKILLS = 12
MAX_DIFFICULTY = 5
MIN_HOURS = 0.5
MAX_HOURS = 40.0
RESOURCES_PER_SKILL = 3
SEARCH_WORKERS = 4

# Reading speed used to turn a measured word count into hours. 200 words a
# minute is the conventional figure for adult non-fiction; it is an estimate,
# and it is labelled as one, but it is an estimate *of a measurement* rather
# than a number invented per resource.
WORDS_PER_HOUR = 12_000
MIN_DURATION_HOURS = 0.25

# Where the calibrated coverage cut-off sits inside the name-match distribution.
# The 5th percentile means: a goal is only declared "already covered" when it
# matches at least as well as the weakest genuine name-match in the whole graph.
COVERAGE_PERCENTILE = 5
# How many nearest skills the model may choose from when coverage is unclear.
CANDIDATE_COUNT = 8

# Half the goal's content words having to appear in the curriculum is a natural
# reading of "this curriculum talks about this subject", not a tuned number.
FAMILIARITY_THRESHOLD = 0.5
# Crude stemming: "web" and "websites" share a three-character prefix.
_PREFIX_MIN = 3

# Only similarity scores this close to the threshold are worth a model call.
# Clearly-covered and clearly-uncovered goals are decided by the calibrated
# number alone, which keeps the check inside an interactive request: on this
# hardware a confirmation costs about twenty seconds, and paying that on every
# intake message to re-confirm "learn Python" would be indefensible.
CONFIRM_BAND = 0.06

_SLUG_RE = re.compile(r"[^a-z0-9]+")
_SPACE_RE = re.compile(r"\s+")
_WORD_RE = re.compile(r"[a-z0-9+#]+")
NEWLINE = chr(10)

# Words that carry no subject meaning in a stated goal. Everything here is a
# framing word a learner wraps around the actual topic.
_GOAL_STOPWORDS = frozenset(
    "i want to learn a an the for my with and of in on how do become build "
    "understand get started using use exam exams class grade level".split()
)


class ProposedSkill(BaseModel):
    """One skill as the model proposes it. Every field is re-checked below."""

    name: str = Field(min_length=2, max_length=90)
    keywords: list[str] = Field(default_factory=list)
    difficulty: int = 2
    hours: float = 6.0
    requires: list[int] = Field(default_factory=list)


class ProposedSyllabus(BaseModel):
    """A whole topic. ``track`` groups the skills the way curated tracks do.

    The three flags are what stop every subject drifting technical. A learner
    asking for business studies was handed statistics, SQL and machine
    learning, then placement-tested on pandas -- because a language model asked
    for "prerequisites" reaches for the ones it has seen most often, and the
    prompt used to invite exactly that. Naming the axes forces the judgement to
    be made explicitly, and the answer then shapes what is searched for and how
    the learner is measured.
    """

    topic: str = Field(min_length=2, max_length=90)
    track: str = ""
    technical: bool = False
    quantitative: bool = False
    practical: bool = False
    skills: list[ProposedSkill] = Field(default_factory=list)

    def domain(self) -> dict[str, bool]:
        return {
            "technical": self.technical,
            "quantitative": self.quantitative,
            "practical": self.practical,
        }


def syllabus_schema(min_skills: int, max_skills: int) -> dict[str, Any]:
    """The schema decoding is constrained to.

    Stricter than ``ProposedSyllabus`` on purpose. Pydantic gives every field a
    default so a half-formed reply can still be repaired, but a default also
    makes the field optional -- and a 3B model handed an optional array returns
    an empty one. Here every field is required and ``minItems`` forces an actual
    syllabus out of the sampler rather than a well-formed shrug.
    """
    return {
        "type": "object",
        "properties": {
            "topic": {"type": "string"},
            "track": {"type": "string"},
            "technical": {"type": "boolean"},
            "quantitative": {"type": "boolean"},
            "practical": {"type": "boolean"},
            "skills": {
                "type": "array",
                "minItems": min_skills,
                "maxItems": max_skills,
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "keywords": {
                            "type": "array",
                            "items": {"type": "string"},
                            "minItems": 2,
                            "maxItems": 3,
                        },
                        "difficulty": {"type": "integer"},
                        "hours": {"type": "number"},
                        "requires": {"type": "array", "items": {"type": "integer"}},
                    },
                    "required": ["name", "keywords", "difficulty", "hours", "requires"],
                },
            },
        },
        "required": ["topic", "track", "technical", "quantitative", "practical", "skills"],
    }


def _describe_skill(skill: ProposedSkill, topic: str) -> str:
    """A description built from the proposal, not a second generation.

    Asking the model for a sentence per skill cost about 25 tokens each -- at
    11 tokens a second that is most of a minute for prose the interface barely
    uses, because *why* a skill is in the path is computed from the graph rather
    than written by a model. The name and its keywords carry the meaning.
    """
    if skill.keywords:
        return f"{skill.name} in {topic}. Covers {', '.join(skill.keywords[:5])}."
    return f"{skill.name}, part of {topic}."


class CoverageJudgement(BaseModel):
    """The model's verdict on a shortlist. ``skill_id`` must come from the list."""

    covered: bool = False
    skill_id: str = ""


# Both fields required, so the sampler must commit to an answer rather than
# emit the empty object that satisfies a schema full of defaults.
COVERAGE_SCHEMA = {
    "type": "object",
    "properties": {"covered": {"type": "boolean"}, "skill_id": {"type": "string"}},
    "required": ["covered", "skill_id"],
}


@dataclass
class Coverage:
    """Why the graph was or was not considered to already know this goal."""

    covered: bool
    best_id: str | None
    best_score: float
    threshold: float
    reason: str
    # True only when *both* signals reject the goal: the curriculum does not use
    # these words and nothing in it is semantically near. That is a different
    # and much stronger claim than "not confidently covered", and it is the one
    # resolution needs. Refusing to resolve on the weaker claim broke goals the
    # curriculum genuinely teaches -- "I want to build websites end to end"
    # scores 100% familiar and lands exactly on the similarity floor.
    definitely_absent: bool = False


@dataclass
class Expansion:
    """The outcome of building a topic, successful or not."""

    ok: bool
    topic: str = ""
    goal_skill_ids: list[str] = field(default_factory=list)
    skill_count: int = 0
    resource_count: int = 0
    seconds: float = 0.0
    cached: bool = False
    reason: str = ""


# --------------------------------------------------------------------------- #
# Naming
# --------------------------------------------------------------------------- #
def slug(text: str) -> str:
    """A stable, ascii, filesystem- and id-safe form of a name."""
    folded = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return _SLUG_RE.sub("_", folded.lower()).strip("_")[:48] or "topic"


def normalised(name: str) -> str:
    """Case- and space-insensitive form, for deciding two names are the same."""
    return _SPACE_RE.sub(" ", name.strip().lower())


# Joining words that stay lower-case inside a title. Never the first word.
_MINOR_WORDS = frozenset(
    "a an the and or of for in on to with from into via vs".split()
)


def readable(name: str) -> str:
    """Turn a machine-shaped name into one a learner should see.

    Asked for skill names the model sometimes answers in the shape of the ids
    around it -- "quantum-gates", "binary_numbers" -- and those went straight
    onto the learner's plan. Separators become spaces, and a name with no
    capitals at all is title-cased. A name that already reads like prose is left
    exactly as written, so "Qubits and Superposition" keeps its lower-case
    "and" -- and so does a title-cased one, because "Photosynthesis And Cell
    Biology" reads like a filename, not like a subject.
    """
    cleaned = _SPACE_RE.sub(" ", name.replace("-", " ").replace("_", " ")).strip()
    if not cleaned:
        return name.strip()
    if cleaned != cleaned.lower():
        return cleaned
    words = cleaned.split()
    return " ".join(
        word
        if (index and word in _MINOR_WORDS) or (len(word) <= 2 and word not in {"ai", "ml"})
        else word[:1].upper() + word[1:]
        for index, word in enumerate(words)
    )


# --------------------------------------------------------------------------- #
# Coverage
# --------------------------------------------------------------------------- #
@lru_cache(maxsize=4)
def _coverage_band(embedder_name: str) -> tuple[float, float]:
    """The two similarity scores that bound "obviously covered" and "obviously not".

    An absolute cosine cut-off is meaningless across embedding models -- the same
    correct pairing scores 0.82 under one and 0.14 under another, a lesson this
    codebase already learned once in ``retrieval``. So the bounds are not chosen,
    they are measured: embed each curated node's *name*, score it against the
    graph, and read the resulting distribution. That distribution is "what it
    looks like when a phrase names a skill the graph has", in the units of
    whichever model is loaded.

    Two bounds rather than one, because a single cut-off does not work and the
    measurements say why. Scoring real goals against the curated graph:

        become a machine learning engineer   0.847   covered
        learn python programming             0.769   covered
        cybersecurity penetration testing    0.745   covered
        I want to build websites             0.664   covered
        class 12 CBSE integration by parts   0.639   not covered
        medieval european history            0.613   not covered
        learn to play the piano              0.553   not covered

    Covered and uncovered goals overlap in the middle, and no threshold splits
    them -- a z-score against the graph's own spread was tried and ordered
    "medieval european history" above "cybersecurity penetration testing". The
    honest conclusion is that this is a judgement about language, not about
    distance, so similarity decides only the two ends and defers in between.
    """
    matrices = retrieval.load_matrices()
    matrix, rows = matrices["skills"], matrices["skill_row"]
    if matrix is None:
        return 0.0, 0.0

    curated = [
        (entry["id"], entry["name"]) for entry in curated_skills() if entry["id"] in rows
    ]
    if len(curated) < 10:
        return 0.0, 0.0

    vectors = get_embedder().embed_batch([name for _, name in curated])
    own = [
        float(np.dot(vectors[index], matrix[rows[node_id]]))
        for index, (node_id, _) in enumerate(curated)
    ]
    weak = float(np.percentile(own, COVERAGE_PERCENTILE))
    certain = float(np.median(own))
    # Below the weak end by the same margin retrieval uses for "not a contender".
    hopeless = weak - retrieval.RELEVANCE_MARGIN

    logger.info(
        "coverage band for %s: certain >= %.3f, new < %.3f (from %d name matches)",
        embedder_name, certain, hopeless, len(own),
    )
    return certain, hopeless


@lru_cache(maxsize=1)
def _curriculum_vocabulary() -> dict[str, float]:
    """Every content word the curriculum uses, weighted by how specific it is.

    A plain word count is fooled by generic vocabulary. "Civil engineering
    structural analysis" shares "engineering" and "analysis" with the graph and
    was judged already covered on that basis, which is exactly the confident
    wrong answer this module exists to prevent.

    So each word carries its inverse document frequency across the graph's
    nodes. "python" appears in a handful of skills and counts for a lot;
    "analysis" appears across many and counts for little. The weights come out
    of the graph, so they change as it grows and nobody maintains a list of
    words to ignore.
    """
    nodes = load_graph().nodes
    if not nodes:
        return {}

    frequency: dict[str, int] = {}
    for node in nodes.values():
        text = f"{node.name} {' '.join(node.keywords)}".lower()
        for token in {t for t in _WORD_RE.findall(text) if len(t) > 2}:
            frequency[token] = frequency.get(token, 0) + 1

    total = len(nodes)
    return {token: math.log(total / count) for token, count in frequency.items()}


def _unknown_word_weight() -> float:
    """What a word the curriculum has never used is worth as evidence.

    The weight a word would carry if it appeared in half a node -- rarer than
    anything real, because a word the curriculum has never used is the strongest
    evidence available that this subject is new.
    """
    total = len(load_graph().nodes) or 1
    return math.log(total / 0.5)


def _resolve_word(token: str, vocabulary: dict[str, float]) -> str | None:
    """Find this word in the curriculum, allowing a shared prefix to count.

    Prefix matching stands in for stemming: "websites" resolves to "web".
    """
    if token in vocabulary:
        return token
    for word in vocabulary:
        if (len(word) >= _PREFIX_MIN and token.startswith(word)) or (
            len(token) >= _PREFIX_MIN and word.startswith(token)
        ):
            return word
    return None


def _lexical_familiarity(goal_text: str) -> float:
    """How much of the goal's meaning the curriculum's vocabulary already carries.

    The share of the goal's total word weight that the curriculum accounts for.
    Words it has never seen count against, at the weight of a maximally rare
    term, which is what makes "quantum", "photosynthesis" and "piano" decisive.
    """
    tokens = [
        token
        for token in _WORD_RE.findall(goal_text.lower())
        if len(token) > 2 and token not in _GOAL_STOPWORDS
    ]
    if not tokens:
        return 0.0

    vocabulary = _curriculum_vocabulary()
    if not vocabulary:
        return 0.0
    unknown = _unknown_word_weight()

    known_weight = total_weight = 0.0
    for token in tokens:
        resolved = _resolve_word(token, vocabulary)
        weight = vocabulary[resolved] if resolved else unknown
        total_weight += weight
        if resolved:
            known_weight += weight
    return known_weight / total_weight if total_weight else 0.0


def assess_coverage(goal_text: str, *, confirm_with_model: bool = False) -> Coverage:
    """Does the curriculum already contain this subject?

    Two signals, because neither works alone and the measurements say so.

    *Similarity* answers "is something like this here". On its own it cannot
    separate covered from uncovered goals: scored against the curated graph,
    "I want to build websites" (covered) sits at 0.664 and "I want to understand
    quantum computing" (not covered) at 0.657. A z-score against the graph's own
    spread was tried too and ordered "medieval european history" above
    "cybersecurity penetration testing". There is no threshold that splits them.

    *Lexical familiarity* answers "does this curriculum even use these words",
    weighted so that rare words count and generic ones do not. It is almost
    orthogonal to similarity: quantum computing, organic chemistry, medieval
    history and piano score near zero because the graph has never used those
    words, while every covered goal clears a half.

    Together they are decisive, and deliberately asymmetric. A goal is covered
    when similarity is unambiguous on its own, or when the curriculum both uses
    the learner's words and is semantically in range. Everything else is a new
    topic. On the eighteen goals used to check it the rule is right seventeen
    times; it calls "music theory and composition" covered, because "theory" and
    "composition" are both rare words the graph happens to use. That is a small
    validation set and the rule is not claimed to be better than that.

    A model may be consulted when the two signals disagree, but it is off by
    default: at 3B it got ten of twelve of the same cases and took fifteen
    seconds each time, so it was slower *and* worse than the arithmetic. What
    catches the residual error is the interface, which shows the learner which
    skill was matched and offers to build the subject fresh instead -- a wrong
    guess a person can see and correct beats a confident one they cannot.
    """
    graph = load_graph()
    matrices = retrieval.load_matrices()
    if matrices["skills"] is None or not graph:
        return Coverage(False, None, 0.0, 0.0, "the curriculum is not embedded")

    certain, hopeless = _coverage_band(matrices["embedder"])

    # Raw cosine, ranked without the depth bonus ``candidate_skills`` applies.
    # That bonus exists to prefer destinations when *choosing a goal*, and it is
    # wrong here: it ranked "Building with Language Models" above "Python
    # Basics" for "learn python programming", so coverage was being judged
    # against a skill nobody would call the same subject.
    query = get_embedder().embed_batch([goal_text])[0]
    similarity = matrices["skills"] @ query
    order = np.argsort(-similarity)[:CANDIDATE_COUNT]
    candidates = [
        {
            "skill_id": matrices["skill_ids"][row],
            "name": graph.require(matrices["skill_ids"][row]).name,
            "score": float(similarity[row]),
        }
        for row in order
        if matrices["skill_ids"][row] in graph
    ]
    if not candidates:
        return Coverage(False, None, 0.0, 0.0, "the curriculum is empty")

    best = candidates[0]
    raw = best["score"]
    familiarity = _lexical_familiarity(goal_text)

    # The reason is read aloud to a learner in the interface, so it is written
    # for them. It used to be the arithmetic -- "the two signals disagree --
    # similarity 0.67 against a 0.65 floor, and 23% of the words are familiar"
    # -- which is honest, unarguable, and completely meaningless to the person
    # being told it. The numbers still exist; they belong in the log line
    # below, where the person who needs them will look.
    if raw >= certain:
        return Coverage(
            True, best["skill_id"], raw, certain,
            f"this looks like {best['name']}, which we already teach",
        )

    semantically_close = raw >= hopeless
    lexically_known = familiarity >= FAMILIARITY_THRESHOLD

    if semantically_close and lexically_known:
        verdict, why = True, (
            f"this looks close to {best['name']}, which we already teach"
        )
    elif not semantically_close and not lexically_known:
        logger.info(
            "coverage: %r is absent (similarity %.2f, familiarity %.0f%%)",
            goal_text[:60], raw, familiarity * 100,
        )
        return Coverage(
            False, best["skill_id"], raw, hopeless,
            "this subject is not in our curriculum yet",
            definitely_absent=True,
        )
    else:
        verdict = False
        why = "we only partly cover this, so a plan built from it would have gaps"
        logger.info(
            "coverage: %r is uncertain (similarity %.2f vs floor %.2f, familiarity %.0f%%)",
            goal_text[:60], raw, hopeless, familiarity * 100,
        )
        if confirm_with_model:
            judged = _confirm_with_model(goal_text, candidates)
            if judged is not None:
                identifier, agreed = judged
                return Coverage(
                    agreed, identifier or best["skill_id"], raw, certain,
                    "the model was asked because the signals disagreed, and said "
                    + ("this subject is already covered" if agreed else "it is not"),
                )

    return Coverage(verdict, best["skill_id"], raw, certain, why)


def _confirm_with_model(
    goal_text: str, candidates: list[dict[str, Any]]
) -> tuple[str, bool] | None:
    """Ask the model whether the shortlist names this subject. None if it cannot.

    The answer is constrained to the shortlist, so the model can confirm a real
    skill or decline, and can never invent coverage that retrieval did not find.
    """
    provider = get_provider()
    if provider.name == "mock" or not provider.available():
        return None

    listing = NEWLINE.join(f"  - {c['skill_id']} | {c['name']}" for c in candidates)
    try:
        judgement = call_with_schema(
            provider,
            COVERAGE_CHECK.format(goal_text=goal_text, candidates=listing),
            CoverageJudgement,
            temperature=0.0,
            max_tokens=120,
            json_schema=COVERAGE_SCHEMA,
        )
    except (SchemaViolation, ProviderUnavailable) as exc:
        logger.info("coverage confirmation degraded: %s", str(exc)[:120])
        return None

    allowed = {c["skill_id"] for c in candidates}
    if judgement.covered and judgement.skill_id in allowed:
        return judgement.skill_id, True
    return "", False


# --------------------------------------------------------------------------- #
# Syllabus
# --------------------------------------------------------------------------- #
def _sanitise(syllabus: ProposedSyllabus) -> list[ProposedSkill]:
    """Clamp every field into range and make a cycle unrepresentable.

    ``requires`` may only point at an *earlier* index. That is not a check that
    can fail open: any reference to a later or equal index is dropped, so the
    dependency relation is a strict partial order by construction and the DAG
    validation downstream cannot reject what this produces.
    """
    seen: set[str] = set()
    kept: list[ProposedSkill] = []
    remap: dict[int, int] = {}

    for original_index, skill in enumerate(syllabus.skills[: MAX_SKILLS * 2]):
        key = normalised(skill.name)
        if not key or key in seen:
            continue
        seen.add(key)
        remap[original_index] = len(kept)
        kept.append(skill)
        if len(kept) >= MAX_SKILLS:
            break

    cleaned: list[ProposedSkill] = []
    for index, skill in enumerate(kept):
        requires = sorted(
            {
                remap[r]
                for r in skill.requires
                if isinstance(r, int) and r in remap and remap[r] < index
            }
        )
        cleaned.append(
            ProposedSkill(
                name=readable(skill.name)[:90],
                keywords=[k.strip()[:40] for k in skill.keywords[:8] if k and k.strip()],
                difficulty=max(1, min(MAX_DIFFICULTY, int(skill.difficulty or 2))),
                hours=max(MIN_HOURS, min(MAX_HOURS, float(skill.hours or 6.0))),
                requires=requires,
            )
        )

    # A syllabus with no stated dependencies is a reading list, not a path. When
    # the model gives none at all, fall back to a chain: strictly weaker than a
    # real DAG, still a defensible ordering, and honest about being derived.
    if cleaned and not any(skill.requires for skill in cleaned[1:]):
        logger.info("syllabus had no dependencies -- falling back to a linear chain")
        cleaned = [
            skill if index == 0 else skill.model_copy(update={"requires": [index - 1]})
            for index, skill in enumerate(cleaned)
        ]
    return cleaned


def design_syllabus(goal_text: str) -> tuple[str, str, list[ProposedSkill], dict[str, bool]]:
    """Ask the model for a prerequisite structure and its domain. Raises if it cannot."""
    provider = get_provider()
    if provider.name == "mock" or not provider.available():
        raise ProviderUnavailable(
            "designing a syllabus for a new topic needs a language model; "
            "install Ollama and pull the configured model"
        )

    syllabus = call_with_schema(
        provider,
        SYLLABUS_DESIGN.format(goal_text=goal_text, max_skills=MAX_SKILLS),
        ProposedSyllabus,
        temperature=0.2,
        max_tokens=2600,
        json_schema=syllabus_schema(TARGET_SKILLS, MAX_SKILLS),
    )
    skills = _sanitise(syllabus)
    if len(skills) < MIN_SKILLS:
        raise SchemaViolation(
            f"syllabus for {goal_text!r} had only {len(skills)} usable skills"
        )
    # Through ``readable`` for the same reason the skill names are: the model
    # answers in the shape of the ids around it, and "quantum-computing" went
    # straight onto the learner's screen as the name of their subject.
    topic = readable(_SPACE_RE.sub(" ", syllabus.topic.strip())[:90]) or goal_text[:90]
    # Named after the topic rather than the model's suggestion. Asked for a
    # grouping it answered "fundamentals", which would put quantum computing and
    # organic chemistry in the same track and make every id ambiguous.
    track = slug(topic)
    return topic, track, skills, syllabus.domain()


# --------------------------------------------------------------------------- #
# Material
# --------------------------------------------------------------------------- #
def _estimated_level(difficulty: int) -> str:
    return retrieval.expected_level(difficulty)


def _duration_hours(page: websearch.VerifiedPage) -> float:
    """Reading time from the measured word count, rounded to a quarter hour."""
    hours = page.word_count / WORDS_PER_HOUR
    return max(MIN_DURATION_HOURS, round(hours * 4) / 4)


def _search_query(skill: ProposedSkill, topic: str, domain: dict[str, bool] | None = None) -> str:
    """What to actually type into a search box for this skill.

    The skill's own keywords, not the topic name. Appending the topic seemed
    obviously right and was actively harmful: "Binary Numbers quantum computing"
    returned a page on quantum cryptography, because the topic words dominate the
    ranking and a foundational prerequisite is precisely the step that is *not*
    about the topic yet. The keywords the syllabus proposed for the skill --
    "binary, base-2, numbers" -- describe the thing itself.

    The topic is still appended when the skill name is short enough to be
    ambiguous on its own and the keywords do not already disambiguate it.

    The trailing word is chosen by what kind of subject this is. "Tutorial" is
    the right word for something you do and the wrong one for something you
    study: it pulls a history skill towards software walkthroughs, because that
    is what the open web means by the word. A subject that is drilled wants
    exercises, a quantitative one wants worked problems, and one that is read
    wants an explanation.
    """
    keywords = " ".join(skill.keywords[:3])
    terms = f"{skill.name} {keywords}".strip()
    combined = terms.lower()
    topic_words = [w for w in _WORD_RE.findall(topic.lower()) if len(w) > 3]
    if len(terms.split()) < 4 and not any(word in combined for word in topic_words):
        terms = f"{terms} {topic}"
    return f"{terms} {_material_word(domain)}"


def _material_word(domain: dict[str, bool] | None) -> str:
    """The kind of material this subject is learned from."""
    axes = domain or {}
    if axes.get("technical"):
        return "tutorial"
    if axes.get("quantitative"):
        return "worked examples practice problems"
    if axes.get("practical"):
        return "exercises practice"
    return "explained introduction"


def gather_resources(
    skills: list[tuple[str, ProposedSkill]],
    topic: str,
    progress: Callable[[str, str, float], None] | None = None,
    domain: dict[str, bool] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, list[str]]]:
    """One live search per skill, concurrently. Returns entries and coverage.

    Searches are network-bound and independent, so they run in a small pool;
    four at a time is enough to hide the latency without looking like a scrape.
    """
    def one(item: tuple[str, ProposedSkill]) -> tuple[str, list[websearch.VerifiedPage]]:
        skill_id, skill = item
        return skill_id, websearch.find_resources(
            _search_query(skill, topic, domain), want=RESOURCES_PER_SKILL
        )

    done = 0
    results: list[tuple[str, list[websearch.VerifiedPage]]] = []
    with ThreadPoolExecutor(max_workers=SEARCH_WORKERS) as pool:
        for skill_id, pages in pool.map(one, skills):
            results.append((skill_id, pages))
            done += 1
            if progress:
                name = next(s.name for i, s in skills if i == skill_id)
                progress(
                    "Finding materials",
                    f"{name} ({done} of {len(skills)})",
                    0.45 + 0.5 * done / max(1, len(skills)),
                )

    difficulty_of = {skill_id: skill.difficulty for skill_id, skill in skills}
    found_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    by_url: dict[str, dict[str, Any]] = {}
    covered: dict[str, list[str]] = {}

    for skill_id, pages in results:
        for page in pages:
            entry = by_url.get(page.final_url)
            if entry is None:
                entry = {
                    "id": f"gen_{slug(page.provider)}_{abs(hash(page.final_url)) % 10**10:010d}",
                    "title": page.title,
                    "provider": page.provider,
                    "url": page.final_url,
                    "format": page.format,
                    "cost": page.cost,
                    "duration_hours": _duration_hours(page),
                    "level": _estimated_level(difficulty_of[skill_id]),
                    "skills_covered": [],
                    # No rating: nobody has rated this page, and a number here
                    # would be a fabricated statistic about a real third party.
                    "rating": None,
                    "language": page.language,
                    "description": page.description,
                    "discovered": True,
                    "found_at": found_at,
                    "word_count": page.word_count,
                    "http_status": page.status,
                }
                by_url[page.final_url] = entry
            if skill_id not in entry["skills_covered"]:
                entry["skills_covered"].append(skill_id)
            covered.setdefault(skill_id, []).append(entry["id"])

    return list(by_url.values()), covered


# --------------------------------------------------------------------------- #
# The whole operation
# --------------------------------------------------------------------------- #
def _assign_ids(track: str, skills: list[ProposedSkill]) -> list[str]:
    """Give every proposed skill a unique id, reusing a curated node by name.

    Exact name reuse only. A looser rule would need another similarity threshold,
    and the cost of getting that wrong -- silently attaching a chemistry syllabus
    to a programming node -- is far worse than the cost of a near-duplicate node.
    """
    graph = load_graph()
    by_name = {normalised(node.name): node.id for node in graph.nodes.values()}
    taken = set(graph.nodes)
    ids: list[str] = []

    for skill in skills:
        existing = by_name.get(normalised(skill.name))
        if existing:
            ids.append(existing)
            continue
        base = f"{track}.{slug(skill.name)}"
        candidate, suffix = base, 2
        while candidate in taken:
            candidate, suffix = f"{base}_{suffix}", suffix + 1
        taken.add(candidate)
        ids.append(candidate)
    return ids


def _skill_entries(
    ids: list[str], skills: list[ProposedSkill], track: str, topic: str,
    domain: dict[str, bool] | None = None,
) -> list[dict[str, Any]]:
    """Turn the proposal into skills.json records, keeping only new ids."""
    graph = load_graph()
    entries: list[dict[str, Any]] = []
    for index, (skill_id, skill) in enumerate(zip(ids, skills, strict=True)):
        if skill_id in graph:
            continue  # reused a curated node; never rewrite the seed
        entries.append(
            {
                "id": skill_id,
                "name": skill.name,
                "track": track,
                "description": _describe_skill(skill, topic),
                "prerequisites": [ids[r] for r in skill.requires],
                "difficulty": skill.difficulty,
                "est_hours": skill.hours,
                "assessable": True,
                "keywords": skill.keywords or [skill.name.lower()],
                "discovered": True,
                "topic": topic,
                # Stored per skill rather than per topic so it survives into
                # the merged graph, where nothing else knows which topic a node
                # came from. Question generation and material search both read it.
                "domain": dict(domain or {}),
            }
        )
    return entries


def _terminal_ids(ids: list[str], skills: list[ProposedSkill]) -> list[str]:
    """The skills nothing else in the topic depends on -- the goal of the path."""
    depended_on = {r for skill in skills for r in skill.requires}
    terminal = [ids[i] for i in range(len(ids)) if i not in depended_on]
    return terminal or ids[-1:]


def expand(
    goal_text: str,
    *,
    force: bool = False,
    progress: Callable[[str, str, float], None] | None = None,
) -> Expansion:
    """Build a topic and commit it. Cached topics return immediately.

    The cache is the reason this is usable in a product: the first learner to
    ask about quantum computing waits for a syllabus and fifteen live searches,
    and every learner after them waits for a dictionary lookup.
    """
    started = time.perf_counter()
    report = progress or (lambda stage, detail, fraction: None)

    if not force:
        cached = store.find_topic(goal_text)
        if cached:
            graph = load_graph()
            goals = [g for g in cached.get("goal_skill_ids", []) if g in graph]
            if goals:
                return Expansion(
                    ok=True, topic=cached["topic"], goal_skill_ids=goals,
                    skill_count=len(cached.get("skill_ids", [])),
                    resource_count=len(cached.get("course_ids", [])),
                    seconds=round(time.perf_counter() - started, 2),
                    cached=True, reason="already built",
                )
            logger.warning("cached topic %r no longer resolves -- rebuilding", cached.get("topic"))

    report("Designing the syllabus", "Working out what this subject depends on", 0.05)
    try:
        topic, track, skills, domain = design_syllabus(goal_text)
    except (ProviderUnavailable, SchemaViolation) as exc:
        return Expansion(ok=False, reason=str(exc)[:200], seconds=round(time.perf_counter() - started, 2))

    ids = _assign_ids(track, skills)
    skill_entries = _skill_entries(ids, skills, track, topic, domain)
    report("Finding materials", f"{len(skills)} skills to cover", 0.45)
    courses, _ = gather_resources(
        list(zip(ids, skills, strict=True)), topic, progress, domain
    )

    if not courses:
        return Expansion(
            ok=False,
            reason=f"designed a syllabus for {topic!r} but no live resource could be verified",
            seconds=round(time.perf_counter() - started, 2),
        )

    report("Indexing", f"{len(courses)} verified resources", 0.95)
    embedder = get_embedder()
    skill_vectors: dict[str, np.ndarray] = {}
    if skill_entries:
        texts = [
            f"{e['name']}. {e['description']} Topics: {', '.join(e['keywords'])}."
            for e in skill_entries
        ]
        skill_vectors = dict(zip(
            [e["id"] for e in skill_entries], embedder.embed_batch(texts), strict=True
        ))
    course_vectors = dict(zip(
        [c["id"] for c in courses],
        embedder.embed_batch([
            f"{c['title']}. {c['description']} Provider: {c['provider']}. Format: {c['format']}."
            for c in courses
        ]),
        strict=True,
    ))

    goal_ids = _terminal_ids(ids, skills)
    store.append_topic(
        goal_text=goal_text,
        topic_name=topic,
        track=track,
        goal_skill_ids=goal_ids,
        skills=skill_entries,
        courses=courses,
        skill_vectors=skill_vectors,
        course_vectors=course_vectors,
        embedder=embedder.name,
        stats={
            "skills_reused": len(ids) - len(skill_entries),
            "providers": sorted({c["provider"] for c in courses}),
        },
    )
    invalidate()

    elapsed = round(time.perf_counter() - started, 2)
    logger.info(
        "expanded %r into %d skills and %d verified resources in %.1fs",
        topic, len(skill_entries), len(courses), elapsed,
    )
    return Expansion(
        ok=True, topic=topic, goal_skill_ids=goal_ids, skill_count=len(skill_entries),
        resource_count=len(courses), seconds=elapsed, reason="built",
    )


def invalidate() -> None:
    """Drop every cache that a new topic invalidates, in dependency order."""
    reset_graph_cache()
    retrieval.reset_caches()
    _coverage_band.cache_clear()
