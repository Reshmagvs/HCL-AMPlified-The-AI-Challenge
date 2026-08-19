# Decisions, assumptions and things to check

A running log of every judgement call made during the build, kept so a reviewer
who did not watch it happen can audit it. Newest phase last.

---

## Phase 0 — Scaffold

**Repository root.** The brief's layout sketch shows a `lodestar/` folder. The
build ran in an existing working directory that already held the planning
documents, so the repo root *is* that directory: `backend/`, `frontend/`,
`deploy/`, `demo/` sit at the top level alongside `run.bat`. Nothing depends on
the folder being named `lodestar`.

**Python 3.12, not 3.11.** The machine has 3.12.10. The brief says 3.11+, so
this is in-spec; `run.bat`/`run.sh` accept anything ≥3.11.

**Pinned versions.** `requirements.txt` pins exact versions. `package.json` uses
caret ranges, which is the npm norm and keeps `npm install` working as
transitive security patches land. The lockfile is committed, so builds are still
reproducible.

**`vitest` 3, not 2.** vitest 2.x bundles its own Vite 5, which conflicts with
the project's Vite 6 and produces a type error in `vite.config.ts`. Upgrading
vitest to 3.x resolves it — same API for our purposes.

**Modules written earlier than their phase.** `main.py` imports the graph, the
retrieval layer and the provider at startup, and "leave the repo runnable at
every commit" outranks strict phase purity. So `core/skill_graph.py`,
`core/retrieval.py` and the whole `llm/` package were written during Phase 0 with
empty-data tolerance (a missing `skills.json` yields an empty graph and a logged
warning, not a crash). Their **data, validation depth and tests** still land in
the phases that own them.

**`core/text_profile.py` exists so the mock is honest.** The offline provider has
to answer the intake prompt with something real, and the degraded fallback in the
intake router needs the same behaviour. Rather than write that twice, the
deterministic regex extractor lives in `core/` and both call it. It only ever
fires on an explicit statement — an unmentioned field stays `None`.

**Mock embeddings are a hashing vectoriser, not random noise.** A seeded random
unit vector satisfies "deterministic" but makes goal resolution meaningless
offline, and the brief requires a *complete demo* with no API key. Hashing words
plus character trigrams into 768 signed buckets makes cosine similarity track
real lexical overlap, so offline goal resolution returns sensible nodes.

**The mock never proposes a catalog resource.** `MockProvider` returns an empty
list for the harvest prompt. A fabricated URL from the mock is indistinguishable
from a fabricated URL from the real model, and the entire catalog pipeline exists
to make that impossible.

**`app/narration.py` will hold the LLM call for rationales, not `core/explain.py`.**
The layering rule is that `core/` never imports `llm/`. `core/explain.py`
therefore builds the provenance object and renders the deterministic template;
the model call that turns that object into two sentences lives one layer up.

**Windows test teardown is best-effort.** Deleting the SQLite test file can raise
`PermissionError` on Windows even after `engine.dispose()`. A leftover file is
harmless (the next session recreates it), so teardown swallows that specific
error rather than failing a green suite.

### Noted and deliberately not built (non-goals)
- Authentication. Tempting the moment there is more than one learner row; the
  brief rules it out and a profile id in the URL is enough for a prototype.
- Alembic migrations. `create_all` is correct for a single-file SQLite app whose
  deployment target wipes the disk on every rebuild.

---

## Phase 1 — Skill graph

**152 nodes, not "~120".** The brief says ~120; the curated graph landed at 152
because five tracks with 4–7 genuine levels of depth plus a shared foundation
layer does not compress below that without either flattening a track or deleting
a foundation. More nodes cost nothing at runtime (traversal is O(V+E) on a graph
this size) and make the sequencing demonstrably non-trivial.

**Six track labels, not five.** The five domain tracks are exactly as specified.
A sixth label, `foundations`, holds the 29 shared nodes (`prog.*`, `math.*`,
`cs.*`, `data.sql_*`) that more than one track depends on. Giving them their own
label rather than arbitrarily filing them under, say, machine-learning keeps the
mastery radar and the track-boundary milestones meaningful. **Check this if you
expected `graph_tracks` to read 5.**

**The graph is authored in `scripts/build_skills.py`, not typed as JSON.**
`skills.json` is a build artifact. Reviewing 255 prerequisite edges is only
practical when each node is one line, and the generator re-validates through
`build_graph` before writing, so a bad edge cannot reach disk.

**Two edges added specifically because a test caught their absence.**
`cs.data_structures` initially fed nothing outside `foundations`, which the
"shared foundations reach ≥2 tracks" test flagged. Two genuine edges were added
rather than relaxing the test:
- `data.database_design` now requires `cs.data_structures` — index design is
  B-trees and hash tables; you cannot reason about it otherwise.
- `sec.exploitation` now requires `cs.data_structures` — memory-corruption work
  requires knowing stack and heap layout.

**Arguable edges, flagged for review.** These are the calls most worth a second
opinion:
- `prog.numpy` requires `math.linear_algebra`. Defensible (broadcasting and dot
  products are linear algebra) but you *can* learn array syntax first.
- `ml.llm_applications` requires `web.rest_api_design` — a cross-track edge
  justified by the fact that building with an LLM means calling an API.
- `ml.model_deployment` and `web.deployment` both require `cloud.docker`, and
  `cloud.architect` requires `sec.cloud_security`. These cross-track edges are
  what make a DevOps learner's ML path shorter, but they do lengthen a pure-ML
  beginner's path by the container chain.
- `web.html` and `da.spreadsheets` have no prerequisites at all. Intentional:
  they are genuine entry points.

**Role nodes are `assessable: false`.** The eight terminal role nodes
(`ml.engineer`, `web.fullstack_engineer`, `da.analyst`, `da.data_engineer`,
`sec.analyst`, `sec.pentester`, `cloud.devops_engineer`, `cloud.architect`) are
capstones, not quizzable skills, so the diagnostic never targets them. They are
still bound to resources where the catalog has them.

**Tie-break clarification.** The brief's first tie-break is "fewer unmet
prerequisites". At the moment Kahn's algorithm makes a choice, every ready node
has zero *unmet* prerequisites by definition, so the implemented key uses the
node's prerequisite count **within the gap subset** — a stable static property
that expresses the same intent (simpler nodes first) and keeps the ordering
total.
