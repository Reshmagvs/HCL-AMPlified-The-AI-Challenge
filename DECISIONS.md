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

---

## Phase 8 — Interface

**React Query pauses instead of failing, and it stranded every screen.** With
the backend stopped, queries sat at `status: 'pending'`, `fetchStatus: 'paused'`
for ever. `isError` never became true, so the screens rendered their loading
skeleton indefinitely — and the earlier `data!` non-null assertion turned that
into a full white screen (`Cannot read properties of undefined`). Three changes,
each needed:
1. `lib/queryState.ts` derives failure from `failureReason` as soon as *one*
   attempt has failed, rather than trusting `status`.
2. `retry` never fires for an unreachable host (`ApiError.isOffline`) — that
   retry is precisely what got paused. `networkMode: 'always'` and a pinned
   `onlineManager` are set too, but nothing depends on them holding.
3. `ErrorBoundary` wraps the routes so no render-time exception can ever blank
   the page again.
Verified by stopping uvicorn mid-session, seeing "Backend unreachable / Retry",
restarting it and recovering through the Retry button.

**Resource duration is not scheduling time.** CS50 is 20 hours and covers six
skills; Khan Academy's Algebra 2 is 60 hours and covers two. Charging the whole
course to one node produced a 60-hour week inside a 6-hour budget and a finish
week of 77. `planner.scheduled_hours` now divides a resource's length across the
skills it covers and bounds the result by the node's curated estimate: the same
path finished at week 47 with 287 hours, and the card still shows the resource's
real length next to the budgeted time.

**A relevance floor was needed on binding.** "Data Structures and Algorithms"
was being bound to Linux Administration. The cause was arithmetic, not data: the
naive `(cosine + 1) / 2` rescale compressed the real spread (genuine matches
0.75–0.85, mis-mappings 0.65–0.68) into a few points of score, which format and
rating could outvote. Fixed twice over — a hard `RELEVANCE_FLOOR = 0.70` filter,
and a `(cosine - 0.5) * 2` rescale that gives the term its range back. The
brief's stated weights are unchanged.

**Confidence is measured over the required set, not the gap.** Over the gap, a
correctly answered skill leaves the set immediately, so the meter read 0% for
the first four questions and then jumped — the opposite of what the learner is
being shown. Caught by running the diagnostic by hand, not by a test.

**Chat context carries the dependency chain.** Asked "why is linear algebra in
my path?", the assistant correctly refused to answer because the context only
listed *what* was in the path. It now includes each step's `path_to_goal` and
measured level, which is the difference between a grounded assistant that is
useless and one that is useful.

**Known cosmetic issue.** All four track checkpoints land in weeks 46–47 on a
long ML path. That is correct behaviour — a topological order interleaves tracks
until the end, so no track genuinely *finishes* early — but it reads oddly on
the dashboard. Left as is rather than faking earlier checkpoints.

---

## Phase 9 — Evaluation, deployment and demo assets

**Every metric passed on the first run**, which is unusual enough to be worth
saying plainly rather than quietly: 0% prerequisite violations, 100% goal
coverage, 0% redundancy, 0.97x path length against gold, 100% free-only
compliance, 100% grounding, p95 path generation 3.9 ms. The numbers are in
`EVAL_RESULTS.md` and were produced by `scripts/evaluate.py`, not typed.

**Gold path lengths are hand-estimated, and that is the weakest number here.**
The 0.97x ratio compares generated step counts against my own judgement of what
a competent planner would produce for each persona. Nobody else reviewed those
twenty estimates. It is a sanity check against padding or skipping, not an
external benchmark, and the report says so.

**The eval harness runs against a scratch database and never calls a model.**
`DATABASE_URL` is redirected before any app import and the file is deleted at
the end, so running the harness cannot pollute a developer's own data or make a
metric depend on network conditions.

**The demo script was cut from 4:59 to 4:08.** The first draft would have run
right up against the five-minute submission limit with no room for edits.
`make_voiceover.py` parses the scene headers and prints per-scene durations, so
the length is checked rather than guessed.

**`seed_demo.py` exists because typing into a form on camera wastes thirty
seconds** of a four-minute budget and invites a typo on take five. It builds a
learner with a completed diagnostic, a generated path and three completed steps
in about two seconds, with no model calls.

---

## Phase 10 — Full system verification

**Two bugs found by executing the product that the test suite had not caught.**
Both are written up in `TEST_REPORT.md`; the short version is that
`milestone_failed` was effectively a no-op whenever the skill it pointed at was
already in the gap, and `run.bat` used `timeout /t`, which fails outright when
stdin is redirected. Neither was visible from reading the code or from `pytest`.

**The one "dead" catalog link is alive.** `nmap.org/book/man.html` failed the
concurrent audit with a TLS handshake timeout and returns 200 on an individual
re-check. It was left in the catalog and the report says exactly that, rather
than quietly removing an entry to make a number read 100%.

**`git archive` is the recommended way to produce the submission ZIP.** Zipping
the working directory by hand would include `.venv` and `node_modules`;
`git archive --format=zip -o lodestar-submission.zip HEAD` emits exactly what is
committed, which is 2.2 MB and verified to bootstrap from nothing.

**`/health` reporting `llm_available: true` for an unverified key is
deliberate.** Verifying at startup would spend an API request on every boot and
make a sub-50ms endpoint depend on the network. The flag self-corrects after the
first failed call, which is fast enough for the badge to be honest in practice.

### Still-open temptations, noted and not built
- Code-splitting the ReactFlow and Recharts routes. The bundle is 760 kB raw,
  228 kB gzipped. Worth doing for a real product, out of scope here.
- A second reviewer for the twenty gold path lengths. They are one person's
  judgement, which is the weakest number in the evaluation.
- Verifying catalog metadata (duration, level, rating) rather than only the URL.
  That needs a human opening 426 pages, which was the constraint the
  propose-then-verify pipeline exists to work around.
