"""Deterministic reasoning layer.

Nothing in this package imports from ``app.routers`` or ``app.llm``. Graph
traversal, gap analysis, ordering, scoring and scheduling are ordinary code and
must stay testable with no network and no framework.
"""
