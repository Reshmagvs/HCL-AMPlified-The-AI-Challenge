# Lodestar (AI Powered Learning Agent)

**Learning is a dependency graph, not a search result.**

Lodestar takes a goal in plain English, measures what you actually know with a
short placement check, works out the shortest valid route to that goal, packs it
into the hours you really have each week, attaches a traceable reason to every
step, and re-plans the moment anything changes — using only real, HTTP-verified,
almost entirely free resources.

**Ask it for a subject it has never seen and it builds one.** The curated graph
covers 152 skills across six technology tracks. Ask for quantum computing,
organic chemistry or a class 12 board exam and it says so plainly, then designs
a prerequisite structure for the subject, searches the live web for material,
fetches every page before using it, and merges the result into the same graph
the planner already works on. Nothing downstream knows the difference.

**It needs no API key to run.** A free OpenRouter key makes every conversation
in it fast — without one it falls back to a local model, then to offline
templates, so it always answers, just not always quickly. The only other
network use is looking for learning material the first time a new subject is
asked for.

---

## 60-second quickstart

```bash
git clone https://github.com/retr0alfred/PathFinder.git
cd PathFinder
start.bat
```

That is the whole thing. `start.bat` finds Python and Node, creates the virtual
environment, installs dependencies **only when `requirements.txt` actually
changed**, writes `.env`, and — the first time only — asks for a free
[OpenRouter key](https://openrouter.ai/keys) so conversations run at hosted
speed rather than laptop-CPU speed; pressing Enter skips it and the app still
runs, just slower. It then prepares the database, downloads the sentence-
embedding model once (~130 MB), checks the local model as a fallback (starting
its daemon if it is installed but idle), starts both servers, waits for the
health check and opens a browser.

| Command | Effect |
|---|---|
| `start.bat` | Normal start |
| `start.bat --reset` | Wipe venv, node_modules and the database, rebuild from scratch |
| `start.bat --backend` | API only (useful before Node is installed) |
| `start.bat --no-browser` | Do not open a browser |
| `./run.sh` | macOS / Linux equivalent, same flags |

`run.bat` is the same script under its older name; both work.

It refuses to start when something already holds port 8000, rather than
reporting a stranger's server as healthy — which is exactly what a stale process
from a previous run looks like from a health check.

Web `http://localhost:5173` · API `http://127.0.0.1:8000` · docs `/docs`

---

## What runs where

Nothing in the critical path needs a hosted service.

| Job | How it is done | Needs a network? |
|---|---|---|
| Search and matching | `bge-small-en-v1.5` locally through ONNX Runtime, 384-dim | No, after a one-time model download |
| Sequencing, scheduling, gap analysis | Ordinary Python. Graph traversal and bin-packing | No |
| Deciding whether a subject is already covered | Two measured signals, no model — see below | No |
| Placement questions, curated subjects | A bank of 144 items, generated once offline and committed | No |
| Explanations | Provenance computed as data, rendered from a template | No |
| Assistant answers | Rule-based from your own plan rows, phrased by a model when one is fast enough | No |
| **Designing a new subject** | A model proposes structure only — never a fact, never a link | Model is local unless a free hosted one is configured |
| **Finding material for a new subject** | Live search, then every page fetched and checked | Yes, once per subject |

Everything above the bold rows works with no model at all. The bold rows are the
open-world half, and they are the only thing that needs one.

### The model chain

`LLM_PROVIDER=auto` builds a chain rather than picking one, and each link falls
through to the next:

```
OpenRouter (free models)  →  Ollama (local)  →  offline templates
```

The order is the point. **Latency is what makes a conversation possible.** The
same intake message takes ~3 s on a hosted free model and ~43 s on a 3B model
running on a four-core laptop CPU — and the latency budgets below correctly
refuse to make anyone wait 43 seconds, so the assistant fell back to templates
and repeated itself. **Ollama is second because it is the thing that cannot run
out**: free hosted models are throttled without warning, and when they are, a
local model that takes a minute beats an error. Templates are last so that with
nothing installed at all, every screen still answers.

Only models that cost **exactly zero** are ever selected. The list is
discovered from OpenRouter's own catalogue rather than hardcoded, so a model
that stops being free stops being used; every response is checked for a
reported cost, and any model that charges is dropped immediately. A configured
`OPENROUTER_MODEL` is ignored unless the catalogue agrees it is free — the
setting cannot start a bill by accident.

`GET /api/usage`, and the panel in the header, show which provider is
answering, the fallback order, requests and tokens spent, and the cost so far.
It deliberately does **not** draw a progress bar: a free-tier key has no
published daily allowance, so there is no denominator, and a bar reading "12%
used" would be a number nobody measured.

```bash
# Free, no card: https://openrouter.ai/keys
OPENROUTER_API_KEY=sk-or-v1-...
```

### The local model

```bash
# https://ollama.com/download, then:
ollama pull qwen2.5:3b-instruct
```

`LLM_PROVIDER=ollama` uses it and nothing else; `auto` uses it when the daemon
is answering and stays deterministic when it is not. Neither ever reaches for a
hosted model — `gemini` happens only when it is named explicitly.

The default model was chosen by measurement on a four-core Ryzen 7 3700U with no
usable GPU, not by reputation:

| Model | Throughput | Verdict |
|---|---|---|
| `qwen2.5:0.5b` | ~26 tok/s | Too weak to produce a coherent prerequisite structure |
| **`qwen2.5:3b-instruct`** | **3.8–11 tok/s** | **The default** |
| `qwen2.5:7b-instruct-q4_K_M` | ~5 tok/s | Better answers; 119 s just to load. Unusable here |

The 3B figure is a range because it is one: the same model on the same laptop
measures 11 tok/s idle and 3.8 tok/s with the API, the dev server and a browser
running. Nothing here assumes a number — throughput is read from the last real
generation and callers budget against that. Decoding threads were measured too,
and the obvious-sounding rule was wrong: all eight logical cores beat the four
physical ones by 36%.

Two things make a model this small good enough to depend on.

**Decoding is constrained by a JSON schema, not asked politely in words.** Told
in English to return a syllabus, this model returned `{"topic": "quantum-
computing", "skills": []}` — valid, parseable, useless. Given the same request
as a schema with `minItems`, it produced a full syllabus with a sensible
dependency structure. That one change is the difference between the local model
being a toy and being the thing the product runs on.

**Slow work is never put in front of a learner.** Providers report their
measured throughput and callers do the arithmetic: `provider.affords(tokens,
seconds)`. Narrating forty path items is worth doing at 200 tok/s and absurd at
4, so it happens on a hosted model and is skipped on this one. The same rule
governs intake, chat and goal resolution, each of which has a deterministic
answer already computed — so exceeding the budget costs phrasing, never
correctness.

This is measured, not assumed. Unbudgeted and unconstrained, one intake message
took **246 seconds** on this laptop; budgeted, it takes **0.5 s** and the reply
is better, because the deterministic extractor had the goal the model kept
missing. Intake declines the model on a second ground too: when the rules
already have the goal and the weekly hours there is nothing left to find.

Building a subject is the work only the model can do, so it is never budgeted
away — it runs as a background job with real progress instead, and takes several
minutes on a CPU this slow.

If the model is not installed, everything except *building a new subject* works
exactly as before, and the interface says so rather than silently degrading.

### Where new material comes from

Discovery is a chain of sources, tried in order, each with its own health state.

| Source | What it is | Why it is there |
|---|---|---|
| `wikimedia` | The MediaWiki search API across Wikipedia, Wikibooks and Wikiversity | A documented public API with a published access policy and no key. Its coverage of academic subjects is close to total, so it is the source that can be *relied* on |
| `duckduckgo` | The open web, scraped from the Lite endpoint | Where tutorials and exercise sets actually live. Best-effort: paced, and stood down for a cooldown the moment it starts refusing |

The first version used DuckDuckGo alone. It worked beautifully for about forty
queries and then returned HTTP 202 with an empty challenge page for everything,
and stayed that way through a cooldown. A product built on one scraped endpoint
stops working under exactly the load that means people are using it, so a
blocked source now degrades the result instead of emptying it.

**Nothing found is taken on trust.** A search result is a claim that a page
exists. Every URL is fetched before it can be used; the title, description,
provider, format and cost are read from the page that actually answered, never
from the search snippet and never from a model. Pages that answer 200 with a bot
wall are discarded by a content-length gate calibrated on real pages. Reading
time is computed from a measured word count. Nothing carries a rating, because
nobody has rated these pages and inventing a number would be fabricating a
statistic about a real third party.

---

## The idea in one paragraph

Recommendation asks *"what is relevant to me"* and answers with similarity.
Sequencing asks *"what must come first, and what am I allowed to start only
after"* and answers with a constrained topological ordering over a dependency
graph. Almost every "learning path" is a ranked list wearing a path's clothes:
relevant items in an impossible order, with no prerequisite guarantees, no time
model, and no idea where the learner currently stands. Lodestar does the second
problem.

---

## Architecture

```
┌──────────────────────────────────────────────────────────────────────┐
│  React 18 + TypeScript + Vite + Tailwind                             │
│  Describe · Measure · Follow (plan + skill map) · Track · Assistant   │
└───────────────────────────────┬──────────────────────────────────────┘
                                │  typed JSON over HTTP
┌───────────────────────────────▼──────────────────────────────────────┐
│  FastAPI routers                                                     │
│  /intake /diagnostic /path /adaptation /chat /dashboard /graph        │
├──────────────────────────────────────────────────────────────────────┤
│  core/   ← all the reasoning. no network, no framework, no model      │
│  skill_graph · mastery · retrieval · planner · explain · adapt        │
│  embeddings · questions · answers · text_profile                     │
├──────────────────────────────────────────────────────────────────────┤
│  llm/    ← optional phrasing only. mock · ollama · gemini             │
├──────────────────────────────────────────────────────────────────────┤
│  data/   skills.json · courses.json · questions.json    ← the seed    │
│          *_embeddings.*.npy · personas.json                           │
│  data/generated/   subjects discovered at runtime       ← the overlay │
│  SQLite  Learner · Mastery · LearningPath · PathItem · Event · Quiz   │
└──────────────────────────────────────────────────────────────────────┘
```

Every arrow points downward. `core/` never imports from `routers/` and never
imports from `llm/`. That single rule is what makes the whole reasoning layer
unit-testable with no network — and it is why embeddings live in `core/`, not in
the model layer.

### The algorithm

```
POST /api/path/generate/{learner_id}

1. REQUIRED   required = ancestors_closure(goal_nodes) ∪ goal_nodes
2. GAP        gap = { n ∈ required : mastery(n) < 0.7 }        unmeasured = 0
3. ORDER      topological sort of gap, ties broken by
              (fewer prereqs in gap) → (lower difficulty)
              → (more downstream unlocks) → (node id)
4. BIND       hard filters FIRST (free-only, language, bandwidth, relevance),
              then 0.45·topic + 0.20·level + 0.15·format + 0.10·cost + 0.10·rating
              keep top 3 → rank 1 binds, 2–3 are the swap options
5. PACK       greedy first-fit into weeks at the real weekly capacity,
              never before a prerequisite finishes; a checkpoint per track
6. EXPLAIN    build a provenance object (pure data), then optionally narrate it —
              the model sees ONLY that object
7. PERSIST    LearningPath v(n+1) + PathItems + Event
```

Step 3 is what a similarity-ranked list cannot produce. Step 4's
hard-filters-first ordering is what makes "free only" actually mean free only.
Step 6's constraint is what makes a hallucinated justification structurally
impossible rather than merely unlikely.

### When the goal is not in the graph

Steps 1–7 assume the goal resolves to a node. The interesting question is what
happens when it does not, and the old answer was the worst possible one: cosine
search returned the *nearest* node, so "quantum computing" quietly became a
programming topic and the learner got a confident plan for the wrong subject.
Nearest-neighbour over a closed set has no way to say "I don't know this".

So coverage is decided first, by two signals, because measurement showed neither
works alone.

*Similarity* answers "is something like this here". Scored against the curated
graph, "I want to build websites" (covered) sits at 0.664 and "I want to
understand quantum computing" (not covered) at 0.657. No threshold splits them. A
z-score against the graph's own spread was tried too, and ranked "medieval
european history" above "cybersecurity penetration testing".

*Lexical familiarity* answers "does this curriculum even use these words",
weighted by inverse document frequency so that rare words count and generic ones
do not. Unweighted, "civil engineering structural analysis" read as covered
because the graph uses "engineering" and "analysis". Weighted, quantum
computing, organic chemistry, medieval history and piano all score near zero.

Together they are right on 17 of the 18 goals used to check them, in about a
third of a second, with no model call. The one miss calls "music theory and
composition" covered, because "theory" and "composition" are both rare words the
graph happens to use. That residual error is caught by the interface rather than
by more arithmetic: the learner is told which skill was matched and offered a
button to build their subject properly instead. A wrong guess a person can see
and overrule is worth more than a confident one they cannot.

When the subject is genuinely new:

```
POST /api/topics/build          (returns immediately; poll GET /api/topics/build)

1. DESIGN     the model proposes 8–12 skills and, for each, which earlier
              skills it depends on. Structure only — never a fact, never a link.
              A prerequisite may only reference an EARLIER index, so a cycle is
              not expressible rather than merely rejected. It also classifies
              the subject on three axes — technical, quantitative, practical —
              which shape steps 2 and 6.
2. SEARCH     one live query per skill, built from the skill's own keywords,
              ending in a word chosen by what kind of subject it is
3. VERIFY     every URL fetched; non-2xx, non-HTML, and bot walls discarded;
              title, description, provider, format and cost read off the page
4. EMBED      new skills and resources vectorised locally
5. MERGE      written to data/generated/ and layered onto the graph. The seed
              is never modified, and a node with an unresolvable prerequisite is
              dropped rather than allowed to break the graph for everyone
6. QUESTIONS  placement questions written in the background, one batched call
```

About 80 s on a hosted free model, or two to four minutes on a laptop CPU —
once, ever. The result is shared, so the second person to ask waits 80 ms.

### Subjects must not leak into each other

A learner asked for **business studies** and was given a curriculum of
statistics, SQL, data visualisation and machine learning, then placement-tested
on pandas. Three separate defects combined to produce that, and it is worth
naming all three because only one of them is the obvious one.

**The prompt invited it.** The syllabus instruction said to include
prerequisites "from other subjects (the mathematics a topic requires, for
instance)". Asked for prerequisites, a language model reaches for the ones it
has seen most often, and that clause gave it permission. It now says the
opposite: import from another field only when the learner genuinely cannot
proceed without it, and *a subject that is not technical must not acquire
programming*.

**Resolution had no way to say "not here".** Nearest-neighbour over a closed set
always returns something, so a goal with no home in the curriculum was handed
the closest node available. Resolution now refuses when both coverage signals
reject the goal, and the learner is told the subject has to be built. The
refusal deliberately needs the *strong* verdict — an earlier version refused
anything not confidently covered and rejected goals the curriculum genuinely
teaches, which is the same failure wearing the opposite sign.

**Skills lost their subject.** "Interpret a Balance Sheet" reads as generic
once it is out of context, so question generation drifted. Every discovered
skill now carries the subject it belongs to and that subject's kind, and both
are stated on every line the question writer sees.

Measured afterwards, "business studies" produces: the business environment,
legal structures and ownership, financial statements, marketing principles,
operations management, budgeting, strategy, HR, investment appraisal, market
research, and business model design — and is placement-tested on the 4Ps, SWOT,
NPV and supply chains. No code anywhere in it.

### Where the AI is, and where it deliberately is not

| Component | Technique | Why not something heavier |
|---|---|---|
| Goal → skill nodes | Local embedding retrieval, then constrained selection from a fixed shortlist | A model alone invents nodes that do not exist; similarity alone misses intent |
| Is this subject covered? | IDF-weighted lexical familiarity + calibrated similarity | The 3B model got 10/12 and took 15 s; the arithmetic gets 17/18 in 0.3 s |
| **New subject → prerequisite structure** | **Schema-constrained generation, then structural validation** | **This is the one thing only a language model can do. It proposes; the code decides what is admissible** |
| New subject → material | Live search, then HTTP verification of every page | A model asked for links invents them. Fetching is the only way to know |
| Learner state | Adaptive selection by `uncertainty × downstream unlocks` | A fixed 20-question quiz wastes the learner's time |
| Resource binding | Hybrid: semantic relevance + rule-based feature scoring | Pure semantics ignores cost, format and level — most of what a learner cares about |
| **Sequencing** | **Topological sort with a weighted tie-break** | **Not an ML problem. Using ML here would be worse and unexplainable** |
| Explanation | Structured provenance → one batched constrained narration | Free-form explanation is plausible-sounding and unverifiable. Batched because a dozen round trips never fit a latency budget; one does |
| Which subject a skill belongs to | Declared at build time, stored per node | Inferring it later from the skill name is exactly how a business course acquired a Python exam |
| Adaptation | Event-driven recompute + diff | Online learning has no data at this scale and cannot be demonstrated |

---

## API contract

| Endpoint | Purpose |
|---|---|
| `GET /health` | status, provider, embedder, catalogue size, graph size, question bank |
| `GET /api/topics/coverage` | Is this goal already taught? Fast enough to call while typing |
| `POST /api/topics/build` | Start building a subject; returns immediately. `force` overrides a coverage verdict |
| `GET /api/topics/build` | Progress: stage, detail, fraction, elapsed |
| `GET /api/topics/sources` | Which discovery sources are answering |
| `GET /api/usage` | Which model is answering, the fallback order, and what has been spent |
| `POST /api/intake/message` | assistant reply + partial profile + `ready` |
| `POST /api/intake/commit` | creates the learner, resolves the goal to node ids |
| `GET /api/diagnostic/next/{lid}` | next question, or `{done: true}` |
| `POST /api/diagnostic/answer` | grades, updates mastery, returns confidence |
| `POST /api/path/generate/{lid}` | builds a new path version |
| `GET /api/path/{lid}?version=` | items, weeks, provenance, per-week load |
| `POST /api/path/whatif` | recomputed at a hypothetical capacity, **never persisted** |
| `POST /api/path/event` | applies one of seven events → new version + diff |
| `GET /api/path/{lid}/diff/{v1}/{v2}` | added / removed / moved / swapped |
| `POST /api/chat/{lid}` | answers about this learner's own plan |
| `GET /api/dashboard/{lid}` | progress, hours, mastery spread, next 3 actions |
| `GET /api/graph/{lid}` | nodes and edges annotated with mastery |

Every model-shaped response is schema-validated before it leaves the server. On
failure: retry once with the error appended, then fall back deterministically
with `llm_degraded: true`. Never a 500.

### The seven adaptation events

| Event | Effect |
|---|---|
| `milestone_failed` | Lower mastery across the block it covered; re-open those steps |
| `too_easy` | Raise mastery to 0.8; drop the step; the schedule pulls forward |
| `too_hard` | Reinstate the groundwork it assumed, ahead of it |
| `behind_schedule` | Repack weeks; return explicit scope-reduction options |
| `goal_changed` | Re-resolve; preserve mastery for overlapping completed work |
| `resource_disliked` | Rebind to the rank-2 resource for the same skill |
| `completed_item` | Mark done; raise mastery; advance progress |

---

## Evaluation — measured, not asserted

`python -m scripts.evaluate` runs the planner against 20 synthetic personas with
hand-written gold paths and writes [`EVAL_RESULTS.md`](EVAL_RESULTS.md). Targets
are fixed in the harness and are never adjusted to match a result.

| Metric | Result | Target | Verdict |
|---|---|---|---|
| Prerequisite-order violations | **0.0%** | 0% | PASS |
| Goal-skill coverage | **100.0%** | ≥95% | PASS |
| Redundancy (repeated skills) | **0.0%** | 0% | PASS |
| Path length vs gold (mean) | **0.97×** | 0.80–1.30× | PASS |
| Free-only compliance | **100.0%** | 100% | PASS |
| Grounding (resources in catalogue) | **100.0%** | 100% | PASS |
| p95 warm path generation | **3.8 ms** | < 2000 ms | PASS |

### Performance

Measured over 50 runs on a Ryzen 7 3700U laptop, four cores, no GPU:

| Operation | Mean | p50 | p95 |
|---|---|---|---|
| `GET /health` | 1.9 ms | 1.8 ms | 2.7 ms |
| Path generation (warm) | 4.1 ms | 3.4 ms | 3.9 ms |
| Path generation (cold caches) | 12.7 ms | 12.8 ms | 13.8 ms |
| Diagnostic question | ~65 ms | — | — |
| `GET /api/path/{lid}` | 12.5 ms | 11.7 ms | 13.1 ms |
| `GET /api/dashboard/{lid}` | 11.7 ms | 11.3 ms | 13.8 ms |
| Embedding one goal string | ~13 ms | — | — |

The whole journey is sub-100 ms per interaction with no model configured. With a
model enabled, its call time dominates everything else.

### The data

| | |
|---|---|
| Skill graph | **152 nodes**, 255 prerequisite edges, 5 domain tracks + shared foundations |
| Depth | 5–11 levels per track |
| Catalogue | **426 resources**, every URL verified with a real HTTP request |
| Free | **99.8%** |
| Coverage | every one of the 152 skills has at least one resource |
| Question bank | **144 items**, one per assessable skill, answer positions balanced 36/36/36/36 |
| Providers | freeCodeCamp, MIT OCW, Khan Academy, MDN, NPTEL, The Odin Project, Kaggle, official docs, … |

The catalogue is built by a propose-then-verify loop: `harvest_catalog.py` asks a
model for candidates, and `verify_catalog.py` fetches every URL and **discards
every non-2xx entry**. 856 candidates became 426 verified resources. A
hallucinated link cannot survive that, which is the point.

---

## Repository layout

```
.
├── run.bat  run.sh  push.bat  .env.example
├── README.md  DECISIONS.md  EVAL_RESULTS.md  TEST_REPORT.md
├── backend/
│   ├── app/
│   │   ├── main.py config.py db.py models.py schemas.py
│   │   ├── routers/    intake diagnostic path adaptation chat dashboard graph
│   │   ├── core/       skill_graph mastery retrieval planner explain adapt
│   │   │               embeddings questions answers text_profile
│   │   └── llm/        base mock ollama gemini prompts
│   ├── data/           skills.json courses.json questions.json
│   │                   *_embeddings.*.npy personas.json
│   ├── scripts/        build_skills harvest_catalog verify_catalog check_links
│   │                   build_embeddings build_questions build_personas
│   │                   seed seed_demo evaluate journeys graph_report
│   └── tests/
├── frontend/src/       pages/ components/ lib/ App.tsx
├── deploy/             Dockerfile (Hugging Face Space) vercel.json README.md
└── demo/               script.txt make_voiceover.py
```

### Regenerating the data

```bash
cd backend
.venv/Scripts/python -m scripts.build_skills          # skills.json from the curated source
.venv/Scripts/python -m scripts.graph_report          # depth + unlock review, for a human
.venv/Scripts/python -m scripts.harvest_catalog --all --append --emphasis docs
.venv/Scripts/python -m scripts.verify_catalog        # HTTP-checks every URL
.venv/Scripts/python -m scripts.build_questions       # the placement question bank
.venv/Scripts/python -m scripts.build_embeddings --both
.venv/Scripts/python -m scripts.build_personas
.venv/Scripts/python -m scripts.evaluate              # writes EVAL_RESULTS.md
.venv/Scripts/python -m scripts.check_links           # audits the catalogue for rot
.venv/Scripts/python -m scripts.seed_demo --reset     # a populated learner for demos
```

Harvesting and question generation need a model (`LLM_PROVIDER=gemini` or
`ollama`); everything else is local. Their outputs are committed, so a clone
never has to run them.

### Tests

```bash
cd backend && .venv/Scripts/python -m pytest        # 243 tests, no network, no key
cd backend && .venv/Scripts/python -m scripts.journeys   # 80 end-to-end assertions
cd frontend && npm run build                        # strict TypeScript, zero errors
```

---

## Deployment

Frontend on Vercel, API on a Hugging Face Space, total cost ₹0 — see
[`deploy/README.md`](deploy/README.md). The Space is Docker, port **7860**, and
its filesystem is ephemeral, so the container re-seeds on every start. The
embedding model is baked into the image at build time, so a cold start needs no
network.

---

## Known limitations

An honest account, because the boundaries are as informative as the features.

- **A free hosted model is a courtesy, not a guarantee.** OpenRouter's free
  tier is throttled without warning and publishes no daily allowance, so there
  is no number to show you and no promise to make. The chain falls back to the
  local model when it happens, which is slower but cannot run out — the failure
  mode is a wait, not an error.
- **The subject classification is the model's own opinion of the subject.**
  Whether business studies is "quantitative" is a judgement, and it is made
  once, at build time, by the model writing the syllabus. It is used to shape
  searches and questions, so a wrong call there quietly shapes both.
- **A discovered subject is only as good as the model's idea of it.** The
  structure is validated — acyclic, in range, deduplicated — but nobody checks
  that "Quantum Circuit" really does depend on "Quantum Gates". For a curated
  track that judgement was made by a person; for a discovered one it was not.
- **Coverage detection is right 17 times out of 18 on a small sample.** Eighteen
  goals is a validation set, not a benchmark. It calls "music theory and
  composition" covered. The interface exposes the verdict and offers an
  override, which is the actual mitigation.
- **Discovery leans on Wikimedia when DuckDuckGo is blocked**, which it often
  is. Wikipedia and Wikibooks are excellent references and poor exercise sets, so
  a maths subject built during a block gets explanation without practice.
- **A build takes about four minutes on this CPU** — measured 223–230 s across
  three subjects, roughly 100 s of it generating the syllabus at under four
  tokens a second. It is a background job with real progress and the result is
  cached forever and shared, but the first person to ask does wait.
- **Placement questions for a brand-new subject are not ready when the plan
  is.** They are written behind the learner, so the check ends having asked
  nothing and says so (`done_reason: questions_not_ready`) rather than claiming
  a placement it did not make.
- **A generated placement question can be incoherent.** One asked which note a
  flat sign represents and offered four note names. The structure is validated;
  the musicianship is a 3B model's.
- **The skill graph is hand-authored and therefore opinionated.** 152 nodes and
  255 edges curated by one person. Several edges are defensible rather than
  incontestable — `prog.numpy` requiring `math.linear_algebra`, for instance.
  The arguable ones are listed in [`DECISIONS.md`](DECISIONS.md) so a reviewer
  can challenge them directly.
- **Catalogue metadata is model-proposed; only the URL is verified.** Every link
  returns 2xx, but the duration, level and rating attached to it are estimates,
  not measurements. Skill-to-resource mapping is filtered by a relevance floor,
  which removes gross mis-mappings but not every borderline one.
- **A 2xx response is not a guarantee of quality.** A verified URL can still be a
  paywall, a login page or a stub. About 50 candidates were dropped for 403s
  from bot-protected hosts that probably do serve real content — the
  conservative call.
- **Self-reported skills are matched without a confidence threshold.** Two
  threshold designs misfired in both directions, so the match is now taken at
  face value and shown to the learner. The safety property is the 0.4 cap: a
  claim can never remove a step from the plan.
- **The placement check is short by design.** Eight questions cannot measure
  forty skills directly; correct answers propagate partial credit to
  prerequisites, and anything unmeasured is assumed unknown. The plan errs
  towards teaching something you may already know rather than skipping something
  you do not.
- **The question bank is fixed.** Every learner sees the same wording for a given
  skill. That makes it reviewable and instant, but it also means a determined
  person could memorise it. Questions for discovered skills are written by the
  local model and are not position-balanced the way the committed 144 are.
- **A brand-new subject cannot be placement-tested for its first minute or two.**
  Its questions are still being written, so the check ends having asked nothing
  and the plan assumes you are starting fresh. The interface says exactly that
  rather than claiming you have been placed.
- **Deployed state is ephemeral.** A Hugging Face Space wipes its disk on
  rebuild, so learner profiles reset. Point `DATABASE_URL` at hosted Postgres if
  that matters.
- **No authentication.** A learner is a row id. A deliberate non-goal for a
  prototype, and it means anyone with the id can read that learner's plan.
- **English only.** The language filter exists and is enforced, but the
  catalogue is entirely `en`, so setting anything else returns nothing.
- **All track checkpoints land near the end of a long plan.** Correct — a
  topological order interleaves tracks until the end, so no track genuinely
  finishes early — but it reads oddly on the dashboard.

---

## Attribution

Built for the AMPlified Round 2 prototype brief, *AI-Powered Personalized
Learning Path Recommender*. The skill graph, catalogue pipeline, planner and
evaluation harness are original work; learning resources are third-party and
linked, never rehosted.

Designed and built end to end, solo, by **Alfred Mathew**.
