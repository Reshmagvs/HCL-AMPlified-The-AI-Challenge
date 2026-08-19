"""The skill graph, annotated with one learner's state, for visualisation.

The frontend draws this with ReactFlow. To stay performant at 150 nodes the
payload is trimmed to the subgraph that matters to this learner: everything
their goal requires, plus one hop of context around it, rather than the whole
graph. A learner aiming at a web goal does not need 32 machine-learning nodes
rendered behind their path.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends
from sqlmodel import Session

from app.core.skill_graph import load_graph
from app.db import get_session
from app.pathing import latest_path, load_items
from app.routers.diagnostic import load_learner, load_mastery
from app.schemas import GraphEdgeOut, GraphNodeOut, GraphResponse

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/graph", tags=["graph"])

CONTEXT_HOPS = 1


@router.get("/{learner_id}", response_model=GraphResponse)
def learner_graph(learner_id: int, db: Session = Depends(get_session)) -> GraphResponse:
    """Nodes and edges around this learner's goal, coloured by mastery."""
    learner = load_learner(db, learner_id)
    graph = load_graph()
    table = load_mastery(db, learner_id)

    goals = [g for g in learner.goal_node_ids if g in graph]
    visible = set(graph.required_for(goals)) if goals else set(graph.nodes)

    # One hop of context so the path does not float in empty space.
    for _ in range(CONTEXT_HOPS):
        neighbours = {
            child for node_id in visible for child in graph.children.get(node_id, ())
        }
        visible |= neighbours

    path = latest_path(db, learner_id)
    items = load_items(db, path.id) if path else []
    week_of = {item.skill_id: item.week_number for item in items if item.kind == "resource"}

    nodes = [
        GraphNodeOut(
            id=node_id,
            name=graph.require(node_id).name,
            track=graph.require(node_id).track,
            difficulty=graph.require(node_id).difficulty,
            est_hours=graph.require(node_id).est_hours,
            mastery=round(table.score(node_id), 3),
            source=table.get(node_id).source,
            in_path=node_id in week_of,
            is_goal=node_id in goals,
            week=week_of.get(node_id),
        )
        for node_id in sorted(visible)
    ]
    edges = [
        GraphEdgeOut(
            source=prereq,
            target=node_id,
            in_path=prereq in week_of and node_id in week_of,
        )
        for node_id in sorted(visible)
        for prereq in graph.require(node_id).prerequisites
        if prereq in visible
    ]
    return GraphResponse(nodes=nodes, edges=edges)
