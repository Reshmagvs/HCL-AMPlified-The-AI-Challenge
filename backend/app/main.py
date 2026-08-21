"""FastAPI application entry point.

Assembles the app: logging, CORS, a request-id middleware, the routers, and a
``/health`` endpoint that answers from already-loaded process state so it stays
well under 50ms and never touches the LLM.
"""

from __future__ import annotations

import logging
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.config import get_settings
from app.db import init_db
from app.logging_config import configure_logging, request_id_var
from app.schemas import HealthResponse

settings = get_settings()
configure_logging(settings.log_level)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Create tables and warm the read-only data layers before serving."""
    init_db()
    from app.core import questions, retrieval, skill_graph
    from app.core.embeddings import get_embedder
    from app.llm import get_provider

    graph = skill_graph.load_graph()
    catalog = retrieval.load_catalog()
    retrieval.load_matrices()
    bank = questions.load_questions()
    logger.info(
        "lodestar ready: %d skills, %d resources, %d questions, embedder=%s, provider=%s",
        len(graph.nodes), len(catalog), len(bank),
        get_embedder().name, get_provider().name,
    )
    yield


app = FastAPI(
    title="Lodestar API",
    version=__version__,
    description="Sequenced, explainable learning paths over a curated skill DAG.",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_origin_regex=r"https://.*\.vercel\.app",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def attach_request_id(request: Request, call_next):
    """Give every request an id, echo it back, and bind it to the log context."""
    rid = request.headers.get("x-request-id") or uuid.uuid4().hex[:8]
    token = request_id_var.set(rid)
    try:
        response = await call_next(request)
    finally:
        request_id_var.reset(token)
    response.headers["x-request-id"] = rid
    return response


@app.get("/health", response_model=HealthResponse, tags=["meta"])
def health() -> HealthResponse:
    """Liveness plus a snapshot of what the process actually loaded.

    Reads only in-memory state -- no database round trip, no LLM call.
    """
    from app.core import questions, retrieval, skill_graph
    from app.core.embeddings import get_embedder
    from app.llm import get_provider

    graph = skill_graph.load_graph()
    catalog = retrieval.load_catalog()
    provider = get_provider()
    return HealthResponse(
        status="ok",
        version=__version__,
        llm_available=provider.available(),
        llm_provider=provider.name,
        embedder=get_embedder().name,
        catalog_size=len(catalog),
        graph_nodes=len(graph.nodes),
        graph_tracks=len(graph.tracks),
        question_bank=len(questions.load_questions()),
    )


def _register_routers() -> None:
    """Import and mount routers. Kept in a function so import errors are loud."""
    from app.routers import (
        adaptation,
        chat,
        dashboard,
        diagnostic,
        graph,
        intake,
        path,
        topics,
    )

    for module in (intake, diagnostic, path, adaptation, chat, dashboard, graph, topics):
        app.include_router(module.router)


_register_routers()
