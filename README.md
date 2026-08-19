# Lodestar

**Learning is a dependency graph, not a search result.**

Lodestar takes a goal in plain English, measures what you actually know with a
short placement check, works out the shortest valid route to that goal through a
curated skill graph, packs it into the hours you really have each week, attaches
a traceable reason to every step, and re-plans the moment anything changes —
using only real, HTTP-verified, almost entirely free resources.

**It runs entirely on your machine.** No API key, no account, no quota, no
network calls at request time.

---

## 60-second quickstart

```bash
git clone https://github.com/retr0alfred/PathFinder.git
cd PathFinder
run.bat
```

That is the whole thing. `run.bat` finds Python and Node, creates the virtual
environment, installs dependencies **only when `requirements.txt` actually
changed**, writes `.env`, prepares the database, downloads the sentence-
embedding model once (~130 MB), starts both servers, waits for the health check
and opens a browser.

| Command | Effect |
|---|---|
| `run.bat` | Normal start |
| `run.bat --reset` | Wipe venv, node_modules and the database, rebuild from scratch |
| `run.bat --backend` | API only (useful before Node is installed) |
| `run.bat --no-browser` | Do not open a browser |
| `./run.sh` | macOS / Linux equivalent, same flags |

Web `http://localhost:5173` · API `http://127.0.0.1:8000` · docs `/docs`

---

## What runs where

Nothing in the critical path needs a hosted service.

| Job | How it is done | Needs a network? |
|---|---|---|
| Search and matching | `bge-small-en-v1.5` locally through ONNX Runtime, 384-dim | No, after a one-time model download |
| Sequencing, scheduling, gap analysis | Ordinary Python. Graph traversal and bin-packing | No |
| Placement questions | A bank of 144 items, generated once offline and committed | No |
| Explanations | Provenance computed as data, rendered from a template | No |
| Assistant answers | Rule-based, resolved from your own plan rows | No |
| Nicer phrasing (optional) | Ollama locally, or Gemini if you have a key | Only if enabled |

The last row is the only place a language model appears at request time, and the
product is fully functional without it. That is the point of the design, not a
fallback bolted on afterwards.

### Optional: nicer wording with a local model

```bash
# https://ollama.com, then:
ollama pull llama3.2:1b
```

Set `LLM_PROVIDER=auto` (the default) and Lodestar uses it if the daemon is
running, or stays on templates if not. A 1B model on a four-core laptop CPU is
comfortable for intake replies and chat answers.

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
│  data/   skills.json · courses.json · questions.json                  │
│          *_embeddings.*.npy · personas.json                           │
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

### Where the AI is, and where it deliberately is not

| Component | Technique | Why not something heavier |
|---|---|---|
| Goal → skill nodes | Local embedding retrieval, then constrained selection from a fixed shortlist | A model alone invents nodes that do not exist; similarity alone misses intent |
| Learner state | Adaptive selection by `uncertainty × downstream unlocks` | A fixed 20-question quiz wastes the learner's time |
| Resource binding | Hybrid: semantic relevance + rule-based feature scoring | Pure semantics ignores cost, format and level — most of what a learner cares about |
| **Sequencing** | **Topological sort with a weighted tie-break** | **Not an ML problem. Using ML here would be worse and unexplainable** |
| Explanation | Structured provenance → constrained narration | Free-form explanation is plausible-sounding and unverifiable |
| Adaptation | Event-driven recompute + diff | Online learning has no data at this scale and cannot be demonstrated |

---

## API contract

| Endpoint | Purpose |
|---|---|
| `GET /health` | status, provider, embedder, catalogue size, graph size, question bank |
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
cd backend && .venv/Scripts/python -m pytest        # 129 tests, no network, no key
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
  person could memorise it.
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
