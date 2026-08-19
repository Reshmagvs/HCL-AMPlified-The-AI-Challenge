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

---

## Phase 2 — Catalog pipeline

**The brief's model ids no longer exist.** `gemini-2.0-flash` returns 404 ("no
longer available") and `text-embedding-004` is not served for `embedContent`.
The live account offers `gemini-3.x` generation models and
`gemini-embedding-001` / `gemini-embedding-2`. This is a factual change in the
provider, not a design decision, so the brief's exact ids could not be honoured.
Current settings: **`gemini-3.5-flash-lite`** for generation and
**`gemini-embedding-2`** for embeddings, both overridable in `.env`.

**Why flash-lite.** `gemini-flash-latest` resolves to `gemini-3.7-flash`, whose
free tier allows **5 requests per minute** — 152 skill nodes would have taken
over half an hour of pure rate-limit waiting. `gemini-3.5-flash-lite` sustained
8 rapid calls with no 429 and produced comparably good recall of stable URLs.
Verification discards anything it got wrong regardless.

**Why `gemini-embedding-2`.** `gemini-embedding-001` hit its **daily** free-tier
quota partway through the build. Quota is per-model, so switching models
unblocked it. Output is capped at 768 dimensions via `output_dimensionality`,
which keeps the committed `.npy` files at ~1.3 MB.

**Six harvest passes, not one.** A single pass returned 213 candidates — roughly
one per skill, and the same three obvious links every time. Passes are additive
(`--append`, merged by URL) and steerable (`--emphasis video|docs|india`), which
took the raw pool to 856 candidates with real variety: 42 video, 77 interactive,
58 course, 249 text. Without the emphasis passes the catalog had **one** video
resource in it, which would have made the format preference decorative.

**Two verifier bugs found by testing, both real.**
- *HEAD is not conclusive.* Kaggle answers `HEAD` with 404 and `GET` with 200 on
  the same URL. The original code only retried GET on 403/405/400/501, silently
  discarding 29 working resources. It now always tries GET before discarding.
- *Dedup was case- and scheme-sensitive.* `https://…/@StatQuest` and
  `https://…/@statquest` were treated as different resources, and one candidate
  redirected from https to http and was stored as http. The normaliser now drops
  the scheme and lowercases the path, and any final URL that is not https is
  discarded outright.

**Final numbers.** 856 proposed → **426 verified**, 152/152 skills covered,
every assessable node has at least one resource, **99.8% free**. The 400+ target
is met. Discards: 92× 404, 50× 403, 3× 429, 3× redirect-to-homepage, 2× 500,
2× unreachable. The 403s are mostly bot-protected hosts serving real content;
they are dropped anyway, because "returns 2xx" is the only check that cannot be
argued with.

**Embedding matrices are per provider.** Mock and Gemini vectors live in
different spaces, so a query embedded by one cannot be compared against a matrix
built by the other. Both sets are committed (`*_embeddings.npy` for Gemini,
`*_embeddings.mock.npy` for the offline vectoriser) and `core.retrieval` picks
by active provider. Without this, a clone with no API key would have been
comparing its query against noise.

**`build_embeddings` was silently not caching.** It never called `init_db()`, so
the `EmbeddingCache` table did not exist and every read and write failed into a
debug-level log. Fixed; re-runs now cost only the changed rows.

## Phases 3–7 — boundary, intake, diagnostic, planner, adaptation

**`routers/path.py` was split into `path.py` + `adaptation.py`.** Generation and
adaptation share the `/api/path` prefix but answer different questions, and
keeping them together would have meant Phase 6's commit importing `core/adapt.py`
from Phase 7 — a commit with a forward dependency. The split is better structure
independently: two ~90-line routers instead of one 200-line one.

**Milestones fire once per track, not on every track switch.** The first
implementation inserted a checkpoint whenever consecutive items changed track,
which produced **seventeen** quizzes in one ML path — a topological order
interleaves foundations with domain work constantly. A checkpoint now goes after
the *last* item of each track, which is the boundary that actually means
something. Caught by a test asserting `len(milestones) == len(tracks)`.

**Packing spills rather than overflowing.** "Never split a resource across more
than two weeks" is unsatisfiable at `hours_per_week=1` with a 14-hour resource.
The packer charges an item's hours across as many consecutive weeks as its own
duration requires, and an item's `week_number` is where it *starts*. The
invariant tested is on the ledger: no week is ever allocated more than
`hours_per_week`. At any sane capacity the two-week rule holds naturally.

**Claim matching uses a relative margin, not an absolute threshold.** A fixed
cosine cut-off does not transfer between providers — mock puts "Python Basics"
at 0.47 against the right node and Gemini much higher. A claim is now accepted
only when the best match is ≥1.35× the runner-up, which expresses "unambiguous"
in a provider-independent way. A vague claim matches nothing and is dropped.

**A recognised claim seeds exactly 0.4**, not a similarity-scaled fraction of
it. Scaling implied a precision self-report does not have; what matters is that
0.4 is below the 0.7 threshold either way.

**Milestone items are not sent to the model for narration.** They carry their
own copy and no provenance record, and passing one to `render_template` raised
`KeyError: 'why_needed'` — found by the first end-to-end test.

**`_earliest_week` uses prerequisite *finish* weeks, not start weeks**, so a
long prerequisite that spills across three weeks does not get overlapped by its
dependent.
