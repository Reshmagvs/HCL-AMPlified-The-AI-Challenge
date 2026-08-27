"""Goal resolution: free text in, skill-graph node ids out.

Neither half of this can be done alone.

A pure embedding search returns the *most similar* node, which for "I want to
build websites" is as likely to be `web.css` as `web.fullstack_engineer` --
similarity has no notion of which node is a destination. A pure LLM invents node
ids that do not exist, and an invented id is worse than a wrong one because it
fails downstream instead of visibly.

So: **retrieve, then constrain.** Cosine search over the precomputed skill matrix
produces eight candidates; the model chooses one to three *from that list, by id*;
and any id not present verbatim in the candidate list is rejected outright. The
model can only ever pick something real. With no provider at all, the top cosine
hit is used, which is less nuanced but always valid.

The same retrieval maps a learner's claimed prior knowledge ("I know Python and
git") onto node ids, except that no model is involved and the resulting mastery
is capped at 0.4 -- a claim shortens the diagnostic, it never replaces it. That
cap is why no confidence threshold is applied to the match; see
``match_claimed_skills``.

Embeddings are computed locally (``core.embeddings``), so the retrieval half of
this works with no API key, no quota and no network.
"""

from __future__ import annotations

import logging
import re
from typing import Any

import numpy as np
from pydantic import BaseModel, Field

from app.core import retrieval
from app.core.embeddings import embed_one
from app.core.skill_graph import SkillGraph, load_graph
from app.llm import get_provider
from app.config import get_settings
from app.llm.base import ProviderUnavailable, SchemaViolation, call_with_schema
from app.llm.prompts import GOAL_RESOLUTION

logger = logging.getLogger(__name__)

CANDIDATE_COUNT = 8
# The reply is a handful of ids picked off a shortlist, not prose.
SELECTION_MAX_TOKENS = 200
EXPECTED_SELECTION_TOKENS = 60

MAX_GOAL_NODES = 3
CLAIM_CANDIDATES = 3
_WORD_RE = re.compile(r"[a-z0-9+#.]+")


class GoalSelection(BaseModel):
    skill_ids: list[str] = Field(default_factory=list, max_length=6)
    reason: str = ""


def _lexical_scores(graph: SkillGraph, text: str) -> dict[str, float]:
    """Token-overlap fallback for when no embedding matrix is available."""
    wanted = set(_WORD_RE.findall(text.lower()))
    if not wanted:
        return {}
    scores: dict[str, float] = {}
    for node in graph.nodes.values():
        haystack = set(_WORD_RE.findall(f"{node.name} {node.description} {' '.join(node.keywords)}".lower()))
        overlap = len(wanted & haystack)
        if overlap:
            scores[node.id] = overlap / (len(wanted) ** 0.5)
    return scores


def embed_query(text: str) -> np.ndarray | None:
    """Embed one string locally. Never touches the network."""
    try:
        return embed_one(text)
    except Exception as exc:  # noqa: BLE001 -- a broken model must not 500 intake
        logger.warning("local embedding failed: %s", str(exc)[:160])
        return None


def candidate_skills(goal_text: str, limit: int = CANDIDATE_COUNT) -> list[dict[str, Any]]:
    """The shortlist a model is allowed to choose from, best first."""
    graph = load_graph()
    if not graph:
        return []

    matrices = retrieval.load_matrices()
    ranked: list[tuple[str, float]] = []
    query = embed_query(goal_text) if matrices["skills"] is not None else None

    if query is not None and query.shape[0] == matrices["skills"].shape[1]:
        hits = retrieval.cosine_search(query, matrices["skills"], limit * 2)
        ranked = [(matrices["skill_ids"][row], score) for row, score in hits]
    else:
        lexical = _lexical_scores(graph, goal_text)
        ranked = sorted(lexical.items(), key=lambda kv: (-kv[1], kv[0]))[: limit * 2]

    # Prefer nodes that are plausible destinations: deeper nodes gate less and
    # sit further from the foundations, which is what a stated goal usually means.
    scored = [
        (skill_id, score + 0.02 * graph.depth(skill_id))
        for skill_id, score in ranked
        if skill_id in graph
    ]
    scored.sort(key=lambda kv: (-kv[1], kv[0]))

    return [
        {
            "skill_id": skill_id,
            "name": graph.require(skill_id).name,
            "track": graph.require(skill_id).track,
            "score": round(float(score), 4),
        }
        for skill_id, score in scored[:limit]
    ]


def _candidate_block(candidates: list[dict[str, Any]]) -> str:
    return "\n".join(
        f"  - {c['skill_id']} | {c['name']} | track: {c['track']}" for c in candidates
    )


def resolve_goal(goal_text: str) -> tuple[list[str], list[dict[str, Any]], bool]:
    """Return (chosen node ids, the candidate shortlist, whether we degraded).

    A topic built for this goal wins outright. It was constructed *for* this
    sentence -- its terminal skills are the goal by definition -- so running
    similarity over the merged graph afterwards could only do worse, and could
    plausibly return a curated node that has nothing to do with the subject the
    learner waited two minutes for.
    """
    from app.core import store

    built = store.find_topic(goal_text)
    if built:
        graph = load_graph()
        goals = [g for g in built.get("goal_skill_ids", []) if g in graph]
        if goals:
            shortlist = [
                {
                    "skill_id": g,
                    "name": graph.require(g).name,
                    "track": graph.require(g).track,
                    "score": 1.0,
                }
                for g in goals
            ]
            return goals, shortlist, False
        logger.warning("built topic %r no longer resolves", built.get("topic"))

    candidates = candidate_skills(goal_text)
    if not candidates:
        return [], [], True

    # Nearest-neighbour over a closed set cannot say "I do not teach this", so
    # asked for business studies it returned the closest thing it had, which
    # was Python -- and the learner was then placement-tested on pandas for a
    # subject with no code in it. Similarity within a domain is a refinement;
    # similarity across domains is a category error, and no amount of ranking
    # fixes it because the right answer is not in the list.
    #
    # So the coverage check that already guards the *interface* now also
    # guards resolution -- but on its strongest verdict only. "Not confidently
    # covered" is far too weak a reason to refuse: it rejects goals the
    # curriculum really does teach whenever the two signals merely disagree.
    # ``definitely_absent`` means both signals rejected it, which is the case
    # that actually produces a plan for the wrong subject.
    from app.core.expansion import assess_coverage

    verdict = assess_coverage(goal_text)
    if verdict.definitely_absent:
        logger.info(
            "refusing to resolve %r onto the curated graph: %s",
            goal_text[:60], verdict.reason[:120],
        )
        return [], candidates, False

    allowed = {c["skill_id"] for c in candidates}
    fallback = [candidates[0]["skill_id"]]

    provider = get_provider()
    # The shortlist is already ordered by similarity and its first entry is the
    # fallback, so declining the call costs a refinement, not the answer. Worth
    # spending a few seconds on at commit time; not worth a minute.
    if not provider.affords(EXPECTED_SELECTION_TOKENS, get_settings().interactive_budget_seconds):
        return fallback, candidates, True

    prompt = GOAL_RESOLUTION.format(goal_text=goal_text, candidates=_candidate_block(candidates))
    try:
        selection = call_with_schema(
            provider, prompt, GoalSelection, temperature=0.1, max_tokens=SELECTION_MAX_TOKENS
        )
    except (SchemaViolation, ProviderUnavailable) as exc:
        logger.info("goal resolution degraded: %s", str(exc)[:140])
        return fallback, candidates, True

    chosen = [s for s in dict.fromkeys(selection.skill_ids) if s in allowed][:MAX_GOAL_NODES]
    rejected = [s for s in selection.skill_ids if s not in allowed]
    if rejected:
        # A hard rejection, not a repair: an id outside the shortlist is exactly
        # the hallucination this design exists to make impossible.
        logger.warning("rejected off-list goal ids from the model: %s", rejected)

    return (chosen or fallback), candidates, not chosen


def match_claimed_skills(claims: list[str]) -> dict[str, float]:
    """Map plain-English claims onto node ids, with the similarity that matched.

    This takes the nearest node for each claim, without a confidence threshold,
    and that is a deliberate reversal of an earlier design.

    Two attempts at a threshold were made -- an absolute cosine cut-off and then
    a within-set separation test -- and both misfired in *both* directions
    across embedding models: "Docker" was rejected while "stuff" was accepted.
    The rule was tuning noise dressed up as rigour.

    What makes the threshold unnecessary is the 0.4 cap. A matched claim seeds
    mastery below the 0.7 mastery threshold, so it can never remove a skill from
    the path; the worst a wrong match can do is nudge the order in which the
    diagnostic asks questions. Set against that, dropping a *correct* claim
    throws away real signal about the learner.

    So the safety property is the cap, not a filter -- and the matches are
    returned to the caller so the interface can show the learner exactly what
    was recorded, which is a better correction mechanism than a hidden cut-off.
    """
    graph = load_graph()
    if not graph or not claims:
        return {}

    matrices = retrieval.load_matrices()
    matched: dict[str, float] = {}

    for claim in claims:
        text = claim.strip()
        if len(text) < 2:
            continue
        best_id: str | None = None
        best_score = 0.0

        query = embed_query(text) if matrices["skills"] is not None else None
        if query is not None and query.shape[0] == matrices["skills"].shape[1]:
            hits = retrieval.cosine_search(query, matrices["skills"], CLAIM_CANDIDATES)
            if hits:
                best_id, best_score = matrices["skill_ids"][hits[0][0]], hits[0][1]
        else:
            lexical = _lexical_scores(graph, text)
            ordered = sorted(lexical.items(), key=lambda kv: (-kv[1], kv[0]))
            if ordered:
                best_id, best_score = ordered[0]

        if best_id:
            matched[best_id] = max(matched.get(best_id, 0.0), round(float(best_score), 4))
        else:
            logger.debug("claim %r matched no skill at all", text)
    return matched
