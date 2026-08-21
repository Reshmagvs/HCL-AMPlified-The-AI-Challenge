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

---

## Phase 11 — Local models, a shipped product, and a new interface

Three changes were asked for: run on local models because API keys proved
unreliable, replace the interface with something calm and beige, and turn a
demo into something usable.

### Embeddings moved into `core/`, and off the network entirely

**This is the most consequential change in the build.** Embeddings used to come
from whichever provider generated text, which meant an API key controlled
*search quality*, a daily quota could silently degrade retrieval, and every
provider needed its own committed matrix because vectors from two models are not
comparable.

Embeddings are not a language-model concern. They are a similarity function over
a fixed vocabulary of 152 skills and 426 resources. So `LLMProvider.embed` was
deleted from the interface and `core/embeddings.py` now owns it:

- `FastEmbedder` — `BAAI/bge-small-en-v1.5` through ONNX Runtime. 384 dims,
  ~130 MB downloaded once, **13 ms per text** on this laptop's CPU. Only the
  learner's goal string is embedded at request time.
- `HashingEmbedder` — signed hashing over words and character trigrams. No
  model, no download, works offline for ever. Materially worse, never absent.

**Retrieval got better, not worse.** Against Gemini's embeddings,
`ml.gradient_descent` bound to a generic "Machine Learning" page and
`prog.functions` bound to a *JavaScript* functions page. Locally both bind
correctly, and goal resolution puts `ml.engineer`, `sec.pentester` and
`da.analyst` first for the obvious phrasings. Machine specifics: Ryzen 7 3700U,
four cores, integrated graphics, no Ollama installed.

**`model2vec` was tested and rejected.** 30 MB and 10 ms for 426 texts, but the
similarity spread was too compressed to discriminate (0.135 for a true match
against 0.035 for an unrelated one). `fastembed` is 4× the size and gives a
usable margin.

### Two thresholds were replaced because they were tuning noise

The relevance floor (`cosine >= 0.70`) and the claim-match margin (`best/second
>= 1.35`) were both calibrated against one embedding model and neither
transferred. The floor rejected everything under the new model; the claim margin
rejected "Docker" while accepting "stuff".

- **Relevance is now relative**: a resource is kept if it lands within 0.10 of
  the best candidate *for that skill*. Ordering within a candidate set is stable
  across models even when absolute values are not.
- **Claim matching has no threshold at all.** The safety property was always the
  0.4 cap — a claim cannot remove a step from the plan — so a filter was
  protecting against a harm that does not exist, while discarding real signal.
  Matches are now shown to the learner instead, which is a better correction
  mechanism than a hidden cut-off.

### The placement check no longer needs a model

144 questions, one per assessable skill, generated once and committed as
`data/questions.json`. Quiz items never varied by learner — only *which* ones get
asked does — so generating them per request bought latency and an API
dependency, and produced wording nobody could review.

The audit caught a real defect: **49% of correct answers were at position B and
exactly one was at position D**, a bank beatable by always picking the second
option. `balance_positions` rotates each question's options so the answer lands
at `index % 4`, giving an exact 36/36/36/36 split without touching any wording.

Answering a question went from ~3 s to **65 ms**.

### The offline assistant now answers instead of echoing

Chat previously fell through to the mock provider, whose canned reply dumped the
raw context block at the learner. Since no model is the *default*, that was the
default experience. `core/answers.py` resolves what people actually ask — what
next, how long, why this, how far along, what does it cost — from the same rows,
in full sentences. The mock provider is now explicitly bypassed for chat.

### ReactFlow was removed after it silently dropped every edge

With 90 valid nodes and 137 valid edges, all 90 nodes rendered and **not one
edge** did; the edge container was empty with no error. Selector-safe edge ids
did not fix it, node dimensions measured correctly, and zustand was properly
isolated in a nested install.

The layout was always computed here — depth is the longest prerequisite chain,
so x position means "how far into the order this sits", and a physics layout
would destroy the one property the picture exists to show. Once the positions
are ours, the library was only drawing lines, and doing it unreliably. Drawing
the SVG directly costs ~150 lines, **removed 140 kB from the bundle** (769 → 629
kB), matches the rest of the design, and cannot half-fail.

### Interface

Warm paper (`#FBF8F2`), warm ink, one clay accent, serif headings from a system
stack so nothing is fetched. The four steps are named for what the learner does
— Describe, Measure, Follow, Track — and the header rail doubles as a progress
indicator with ticks for completed steps.

Guidance is treated as part of the product, not decoration: a three-step
explainer on first visit, three real example sentences that fill the box on
click, a "How to read this" callout on the plan, per-button tooltips in the
learner's language ("I already know this", not "too_easy"), and suggested
questions in the assistant.

**Learner-facing copy no longer leaks internal ids.** The diff banner said
"prog.control_flow leaves the path"; every message from `core/adapt.py` now
resolves skill names.

**The weekly hours figure was misleading.** The timeline summed the length of
everything *starting* that week, so a 16-hour resource showed "20.0h" against a
6-hour budget and looked broken. `week_allocations` replays the packing ledger
so the header reads "6.0h of your 6h".

### Still open
- Code-splitting the dashboard charts. 629 kB raw, 186 kB gzipped.
- `Docker` as a claimed skill matches `cloud.compose` rather than `cloud.docker`.
  Harmless at the 0.4 cap and now visible to the learner, but imperfect.
- The gold path lengths behind the 0.97× figure are still one person's estimates.

---

## Phase 12 — the closed world, opened

The product had a defect it could not express: asked for quantum computing, it
returned a plan for a programming topic. Nothing was broken. Cosine search over a
152-node graph did exactly what it was built to do and returned the nearest node,
because nearest-neighbour over a closed set has no way to say *I do not know
this*. A learner would have followed that plan.

Everything below follows from deciding that a curriculum which cannot admit
ignorance is worse than one that can.

### The local model had never actually run

`LLM_PROVIDER=auto` falls back to the offline provider when Ollama is absent, and
Ollama was not installed. Every claim about "running on a local model" was
therefore true only in the sense that nothing had contradicted it. Measured, once
it was installed on a four-core Ryzen 7 3700U with no usable GPU:

| Model | Throughput | Load | Verdict |
|---|---|---|---|
| `qwen2.5:0.5b` | 25.7 tok/s | 7 s | Too weak for structure |
| `qwen2.5:3b-instruct` | 11.0 tok/s | 68 s cold | **The default** |
| `qwen2.5:7b-instruct-q4_K_M` | 4.8 tok/s | 119 s | Better answers, unusable here |

Three changes made a 3B model dependable rather than decorative.

**Decoding is constrained by a JSON schema.** Asked in English for a syllabus it
returned `{"topic": "quantum-computing", "skills": []}` — 23 tokens, valid,
useless. The same request expressed as a schema with `minItems` produced thirteen
skills with a real dependency structure. Pydantic's own schema was not enough,
because every field has a default and a default makes the field optional.
Generation and validation now use different schemas on purpose: strict for the
sampler, forgiving for the parser.

**The model stays resident.** A cold load cost 68 s and Ollama unloads after five
idle minutes, so the first request of the day spent longer loading than
generating. `keep_alive` plus a warm-up inside `scripts.seed` moves that cost
into the visible setup step.

**Slow work is never put in front of a learner.** Providers now report measured
throughput and callers do the arithmetic. This was found the hard way: enabling
Ollama made path generation take **5.5 minutes**, because narration ran once per
item. Narration is polish on a reason that is already computed deterministically,
so it now runs only when the projected cost fits a latency budget — a hosted
model narrates, this one does not. Path generation returned to **1.9 s**.

The budget is a rule rather than a switch, so the same code does the right thing
on a machine with a GPU without anyone changing a setting.

### Deciding whether a subject is already covered

Three approaches were tried. The two that failed are more informative than the
one that worked.

*Absolute cosine, calibrated against the graph.* Each curated node's name scored
against its own vector gives the distribution of a genuine name-match: median
0.813, 5th percentile 0.754. But real goals are phrased loosely. "learn python
programming" scored 0.769 and "I want to build websites" 0.664 — below goals that
are not covered at all. No threshold splits them.

*A z-score against the graph's own spread.* Ranked "medieval european history"
(2.95) above "cybersecurity penetration testing" (3.16). Worse than the raw
score.

*Asking the model.* Constrained to a shortlist so it could not hallucinate, it
got 10 of 12 and took about 15 seconds each time. It rejected `prog.python_basics`
for "learn python programming".

What works is two cheap signals together. Similarity says "something like this is
here"; **IDF-weighted lexical familiarity** says "this curriculum uses these
words". The weighting is what makes it work: an unweighted word count called
"civil engineering structural analysis" covered, because the graph uses
"engineering" and "analysis". Weighted by rarity across the graph's own nodes,
quantum computing, organic chemistry, medieval history and piano all score near
zero, while every covered goal clears a half.

**17 of 18, in 0.3 s, with no model call** — faster *and* more accurate than the
model. The remaining error calls "music theory and composition" covered, because
"theory" and "composition" are rare words the graph happens to use. That is not
fixed with more arithmetic. It is fixed by showing the learner which skill was
matched and offering a button to build their subject anyway: a wrong guess a
person can see and overrule beats a confident one they cannot.

### One scraped search engine is not a foundation

The first implementation used DuckDuckGo's Lite endpoint and worked beautifully
for about forty queries. Then every request returned HTTP 202 with an empty
challenge page, instantly, and stayed blocked through a cooldown — the failure
arriving exactly when usage picks up.

Discovery is now a chain. **Wikimedia** first: a documented public API across
Wikipedia, Wikibooks and Wikiversity, no key, near-total coverage of academic
subjects, and it works where a browser user-agent gets 403 because the request
sends the identifying `Api-User-Agent` their robot policy asks for. **DuckDuckGo**
second, best-effort, paced, and stood down for fifteen minutes the moment it
starts refusing — so a blocked engine degrades the result instead of making a
subject look like one nobody has ever written about.

Two further corrections came from reading what it actually returned:

- **Wikimedia searches were polluted by the words that help a web search.**
  "Integration by parts practice" returns better articles as "integration by
  parts": a wiki has no page called "tutorial".
- **Appending the topic to every query was actively harmful.** "Binary Numbers
  quantum computing" returned a page on quantum cryptography, because a
  foundational prerequisite is precisely the step that is *not* about the topic
  yet. Queries are now built from the skill's own proposed keywords.

Relevance is then decided by the same local embedder that binds catalogue
resources, so one notion of relevance governs everywhere. Before that, provider
diversity was enforced absolutely and returned "Virtues/Moral Integration" for a
calculus skill; diversity is now a preference, not a rule.

### Nothing found is taken on trust

A search result is a claim that a page exists. Every URL is fetched, and the
title, description, provider, format and cost are read from the response — never
from the search snippet, never from a model. Three rules came out of pages that
lied:

- **A 200 is not proof of content.** Khan Academy answers a bot check with HTTP
  200 and a page reading "Client Challenge". Measured on real pages, four usable
  ones carried 9,825 to 184,792 characters of visible text while the two junk
  responses carried 228 and 17; the gate sits an order of magnitude clear of
  both.
- **`format` is decided by the document, not by a list of known video sites** —
  an `og:type` of video, a `<video>` element, an embedded player. A host list
  would be exactly the hardcoding this work exists to remove.
- **Nothing discovered carries a rating.** `rating` became nullable and the
  scorer treats absence as neutral. Inventing a plausible 4.2 would be
  fabricating a statistic about a real third party, which the brief forbids.

`duration_hours` is computed from a measured word count at 200 words a minute: an
estimate *of a measurement*, rather than a number invented per resource.

### Consequences elsewhere

**A short resource does not make a skill quick.** Scheduling took the resource's
length bounded above by the skill's estimate. That was fine for curated
multi-hour courses and absurd for a twenty-minute Wikipedia article: a nine-skill
quantum computing plan finished in **week 1**. The skill estimate now bounds the
schedule from below as well — a resource shorter than the skill needs means the
resource is insufficient, not that the skill got easier. The same plan now
finishes in week 8 across 45.5 hours.

**Embedding matrices are addressed by id, not by row order.** Row order was an
implicit contract — "sorted ids, as they were when the matrix was built" — that
held only while the id set was frozen. A companion `.ids.json` now names the id
in every row, curated and generated vectors are merged by id, and a skill with no
vector is simply absent from search rather than silently shifting every row after
it.

**The seed is immutable.** Discovered content lives in `data/generated/` and is
layered on top. A generated node may never redefine a curated one, and one whose
prerequisites do not resolve is dropped with a log line rather than allowed to
raise. A bad generation degrades the overlay and is fixed with `rm -r`.

**The placement check tells the truth about itself.** Terminating with nothing
asked used to render as "0 questions was enough" — a lie, and a consequential
one, because the learner would believe they had been placed. The API now reports
*why* it stopped, and a brand-new subject says plainly that its questions are
still being written and that the plan assumes a fresh start.

**Skill names are made readable.** The model sometimes answers in the shape of
the ids around it — "quantum-gates", "binary_numbers" — and those went straight
onto the learner's plan.

### Still open
- The syllabus for a discovered subject is a 3B model's opinion. Structure is
  validated; pedagogy is not. Nobody checks that "Quantum Circuit" really
  requires "Quantum Gates".
- Coverage detection is 17/18 on eighteen goals. That is a validation set, not a
  benchmark.
- Questions generated for discovered skills are not position-balanced the way the
  committed 144 are.
- When DuckDuckGo is blocked, a maths subject gets Wikipedia explanation and no
  exercise sets.
- A first build costs about two minutes, roughly 80 s of it generation.
