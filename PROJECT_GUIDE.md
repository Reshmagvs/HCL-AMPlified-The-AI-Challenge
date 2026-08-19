# Lodestar — Complete Project Guide

Everything about this system in one file: how it works end to end, how to run it, how to
deploy it for ₹0, and how to produce the demo video with free AI tooling.

- [Part 1 — How Lodestar works](#part-1--how-lodestar-works-top-to-bottom)
- [Part 2 — Running it locally](#part-2--running-it-locally)
- [Part 3 — Free hosting](#part-3--hosting-it-live-for-zero-cost)
- [Part 4 — The demo video](#part-4--making-the-demo-video-with-free-ai)
- [Part 5 — Troubleshooting](#part-5--troubleshooting)

---

# Part 1 — How Lodestar works, top to bottom

## 1.1 The one-paragraph model

A learner describes a goal in plain English. Lodestar resolves that goal to node(s) in a
curated **skill dependency graph**, measures what the learner actually knows with a short
adaptive diagnostic, computes the gap as a graph operation, orders that gap so no skill is
ever taught before its prerequisites, binds each skill to a **real** learning resource from a
curated catalog, packs the whole thing into the hours the learner actually has per week, and
attaches a machine-generated reason to every single item. When something changes, it re-plans
and shows you exactly what changed.

The critical design decision: **deterministic algorithms do the reasoning, the LLM does the
language.** Graph traversal, ordering, scoring and scheduling are ordinary code. The LLM only
extracts structure from conversation, disambiguates goals, writes diagnostic questions, and
narrates reasons that were computed for it. This is why the system is explainable, cheap,
fast, testable — and why it still works when the LLM is unavailable.

## 1.2 The layer cake

```
┌──────────────────────────────────────────────────────────────────┐
│  React + TypeScript                                              │
│  Intake · Diagnostic · Path(+graph) · Dashboard · ChatPanel      │
└──────────────────────────────┬───────────────────────────────────┘
                               │ JSON over HTTP (typed contracts)
┌──────────────────────────────▼───────────────────────────────────┐
│  FastAPI routers                                                 │
│  /intake  /diagnostic  /path  /chat  /dashboard  /graph  /health │
├──────────────────────────────────────────────────────────────────┤
│  core/  ← all the reasoning, zero LLM dependency                 │
│  skill_graph · mastery · retrieval · planner · explain · adapt   │
├──────────────────────────────────────────────────────────────────┤
│  llm/  ← language only, swappable, mockable                      │
│  base(ABC) · gemini · mock · prompts                             │
├──────────────────────────────────────────────────────────────────┤
│  data/            skills.json · courses.json · embeddings.npy    │
│  SQLite           Learner · Mastery · Path · PathItem · Event    │
└──────────────────────────────────────────────────────────────────┘
```

Every arrow points downward. `core/` never imports from `routers/`. `core/` never imports
from `llm/` — the planner receives already-resolved data. That single rule is what makes the
whole system unit-testable without a network connection.

## 1.3 The five data structures that matter

**The skill graph** (`data/skills.json`) — ~120 nodes, each with id, name, track,
prerequisites, difficulty (1–5), estimated hours, and keywords. It is a DAG: edges point from
prerequisite to dependent, and cycles are rejected at startup. This file is the product's
intellectual core. Everything else is machinery around it.

**The resource catalog** (`data/courses.json`) — 400+ real, manually verified learning
resources. Every entry maps to one or more skill ids. This is the *only* place a resource can
come from. The LLM selects by id; it never writes a URL.

**Catalog embeddings** (`data/catalog_embeddings.npy`) — a precomputed, L2-normalized matrix
of every catalog item's embedding, generated once by `scripts/build_embeddings.py` and
committed to the repo. At runtime only the learner's goal text needs embedding, so a fresh
clone works with essentially zero API calls.

**Mastery** — one row per (learner, skill) with a score in 0–1 and a source. Self-report caps
at 0.4 (never enough to skip content outright); a passed diagnostic or milestone can reach
1.0. The threshold for "you don't need this" is 0.7.

**The event log** — every state change appends to `Event`. It powers adaptation, the activity
feed, the dashboard analytics, and your demo narrative. Nothing mutates silently.

## 1.4 The four request lifecycles

### A. Intake — conversation to structured profile

```
user types  →  POST /api/intake/message
               ├─ LLM extracts {interests, experience, completed, goal, hours/week,
               │  target_date, format_pref, cost_pref} as strict JSON
               ├─ validated against a Pydantic schema; on failure retry once, then ask
               │  a clarifying question instead of guessing
               └─ returns assistant reply + partial profile + ready:bool
user confirms → POST /api/intake/commit
               ├─ embed(goal_text) → cosine vs skill-node embeddings → top 8
               ├─ LLM picks 1–3 terminal node IDS FROM THAT LIST (schema-validated)
               │  (LLM down? take top-1 by cosine — still correct, just less nuanced)
               ├─ self-reported skills → Mastery rows at ≤0.4
               └─ Learner row created
```

The frontend shows the profile card filling in live as the conversation goes. Structure
emerging visibly from free text is the most persuasive thing in the whole demo.

### B. Diagnostic — measuring instead of assuming

```
GET /api/diagnostic/next/{learner_id}
  ├─ candidate skills = gap nodes with the highest uncertainty × downstream impact
  │  (measuring a node that unblocks 12 others is worth more than a leaf)
  ├─ LLM writes one 4-option MCQ for that skill; answer key stored server-side
  └─ returns question (never the answer)

POST /api/diagnostic/answer
  ├─ graded deterministically against the stored key
  ├─ mastery[skill] updated; correlated ancestors nudged (getting backprop right
  │  implies you know derivatives)
  ├─ confidence recomputed
  └─ stops early when confidence is sufficient, or at 10 questions
```

"I don't know" is a first-class answer button. It is clean signal, not a failure, and it
keeps the diagnostic honest — guessing pollutes the measurement.

### C. Path generation — the core algorithm

```
POST /api/path/generate/{learner_id}

1. REQUIRED   required = ancestors_closure(goal_nodes) ∪ goal_nodes
2. GAP        gap = { n ∈ required : mastery(n) < 0.7 }
3. ORDER      topological sort of gap; ties broken by
              (fewer unmet prereqs) → (lower difficulty)
              → (more downstream unlocks) → (node id)
4. BIND       for each node, score catalog items covering it:
                0.45·cosine + 0.20·level_match + 0.15·format_pref
              + 0.10·cost_pref + 0.10·rating
              hard filters (free_only, language, bandwidth) applied FIRST
              keep top 3 → rank 1 is bound, 2–3 are the swap options
5. PACK       greedy first-fit into weeks at hours_per_week capacity,
              never before a prerequisite's week; milestone quiz at each
              track boundary
6. EXPLAIN    build a provenance JSON per item (pure data), then have the LLM
              narrate it in two sentences — it sees ONLY that JSON
7. PERSIST    LearningPath v1 + PathItems + Event(path_generated)
```

Step 3 is the thing every other team's project doesn't have. Step 4's hard-filters-first
ordering is what makes "free only" actually mean free only. Step 6's constraint — the LLM
sees only the provenance object — is what makes hallucinated justifications structurally
impossible rather than merely unlikely.

### D. Adaptation — replanning you can see

```
POST /api/path/event  {type, payload}

  milestone_failed   → lower mastery, insert remediation node before the blocked node
  too_easy           → raise mastery to 0.8, drop the node, pull the schedule forward
  too_hard           → insert the under-weighted prerequisite
  behind_schedule    → repack; offer scope-reduction options
  goal_changed       → re-resolve goal; PRESERVE overlapping completed nodes
  resource_disliked  → rebind to rank-2 resource, same skill
  completed_item     → mark done, mastery += , advance progress

  → always creates path version N+1 (never mutates N)
  → returns diff {added, removed, moved_weeks, resource_swapped, finish_week_delta}
```

The UI renders that diff as a banner: _"Path updated: +2 items, −1 item, finish moved from
week 12 to week 13."_ Versioning is what makes the diff possible, and the diff is what makes
adaptation believable to a judge instead of an invisible database write.

## 1.5 Where the AI actually is

| Component | Technique | Why this and not something heavier |
|---|---|---|
| Goal → skill nodes | Embedding retrieval + constrained LLM selection | Pure LLM invents nodes that don't exist; pure cosine misses intent |
| Learner state | Adaptive item selection by uncertainty × impact | A fixed 20-question quiz wastes the learner's time |
| Resource binding | Hybrid: semantic + rule-based feature scoring | Pure semantic ignores cost/format/level, which is most of what the learner cares about |
| Sequencing | Topological sort with weighted tie-break | Not an ML problem. Using ML here would be worse and unexplainable |
| Explanation | Structured provenance → constrained narration | Free-form LLM explanation is plausible-sounding and unverifiable |
| Adaptation | Event-driven recompute + diff | Online learning has no data at this scale and can't be demoed |

Say the fourth row out loud in your presentation. Knowing where **not** to use ML is a
stronger signal of ML competence than using it everywhere.

## 1.6 Graceful degradation (rehearse this for the demo)

| Failure | Behaviour |
|---|---|
| No API key | Mock provider; full journey works; honest banner in the UI |
| LLM rate-limited (429) | One retry, then deterministic fallback, `llm_degraded: true` |
| Malformed LLM JSON | Schema validation fails → retry → fallback. Never a 500 |
| Embedding API down | Cached embeddings serve; new goal text falls back to keyword match |
| Backend down | Frontend shows a retry state, not a white screen |

Pulling the API key live on camera and watching the app keep working is a genuinely strong
15 seconds of demo.

---

# Part 2 — Running it locally

```
git clone https://github.com/<user>/lodestar.git
cd lodestar
run.bat
```

That's it. `run.bat` finds Python and Node, creates the venv, installs dependencies only when
`requirements.txt` actually changed, generates `.env`, seeds the database, builds embeddings,
starts both servers, waits for the health check, and opens the browser.

| Command | Effect |
|---|---|
| `run.bat` | Normal start |
| `run.bat --reset` | Wipe venv, node_modules and DB, rebuild from scratch |
| `run.bat --backend` | API only (useful before Node is installed) |
| `run.bat --no-browser` | Don't open a browser |
| `./run.sh` | macOS / Linux equivalent |

Live AI is optional: get a free key at `https://aistudio.google.com/apikey`, put it in `.env`
as `GEMINI_API_KEY`, and set `LLM_PROVIDER=gemini`. Without it, everything still runs in mock
mode.

URLs: web `http://localhost:5173` · API `http://127.0.0.1:8000` · docs `/docs`

---

# Part 3 — Hosting it live for zero cost

**The stack: Vercel (frontend) + Hugging Face Spaces (backend). Total cost ₹0, no credit
card, no trial clock.**

## 3.1 Why this pairing

The obvious choice is Render, and it works — but free Render web services spin down after
**15 minutes** of inactivity and take about a minute to wake. A judge opening your link at
11pm on 30 August gets a one-minute loading spinner as their first impression of your product.

Hugging Face Spaces on the free CPU Basic tier gives you 2 vCPU / 16GB RAM and only sleeps
after **48 hours** of idle time. Between your final deploy and judging, your app is realistically
never asleep. It is also an AI-hosting platform, which reads correctly for an AI project.

| | Vercel (Hobby) | HF Spaces (CPU Basic) | Render (Free) |
|---|---|---|---|
| Use for | Frontend | Backend API | Backend alternative |
| Idle sleep | n/a (static/edge) | 48 hours | 15 minutes |
| Cold start | none | 30–90s | 30–60s |
| Card required | no | no | no |
| Storage | n/a | ephemeral | ephemeral |
| Custom domain | yes | Pro only | yes |

## 3.2 Backend → Hugging Face Spaces

Create a Space: **New Space → SDK: Docker → Hardware: CPU basic (free) → Public.**

`Dockerfile` in the repo root of the Space (HF hardcodes port **7860**):

```dockerfile
FROM python:3.11-slim
WORKDIR /app
RUN useradd -m -u 1000 user
COPY --chown=user backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY --chown=user backend/ .
USER user
ENV PORT=7860 PYTHONUNBUFFERED=1
EXPOSE 7860
RUN python -m scripts.seed
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "7860"]
```

Then:
- **Secrets**: Space Settings → Variables and secrets → add `GEMINI_API_KEY`, `LLM_PROVIDER=gemini`,
  `CORS_ORIGINS=https://<your-app>.vercel.app`. They arrive as env vars; never commit them.
- **Ephemeral storage is the one real gotcha.** The disk is wiped on every rebuild and on
  wake, so a SQLite file does not survive. Two acceptable answers:
  1. *Demo-appropriate:* re-seed on startup (the `RUN python -m scripts.seed` line above) and
     accept that learner profiles reset on rebuild. Note it honestly in your README.
  2. *Better:* point `DATABASE_URL` at a free hosted Postgres (Supabase or Neon free tier).
     SQLModel makes this a one-line change. Do this only if you have time to spare.
- Push: `git remote add space https://huggingface.co/spaces/<user>/lodestar && git push space main`
- Your API is at `https://<user>-lodestar.hf.space`

## 3.3 Frontend → Vercel

1. Import the GitHub repo at vercel.com. Root directory: `frontend`.
2. Framework preset: Vite. Build: `npm run build`. Output: `dist`.
3. Environment variable: `VITE_API_BASE=https://<user>-lodestar.hf.space`
4. Deploy. Every push to `main` redeploys automatically.

Add the Vercel URL to the backend's `CORS_ORIGINS` secret, then restart the Space.

## 3.4 Pre-judging checklist

- [ ] Open the Vercel URL on a phone, on mobile data, in a private window. Time it.
- [ ] Hit the Space once ~2 hours before judging so it's warm.
- [ ] Confirm `/health` returns `llm_available: true` in production.
- [ ] Full journey on the deployed build, not just locally.
- [ ] Put **both** URLs in the README and in the submission form.
- [ ] Seed one demo learner in production so a judge sees a populated dashboard immediately —
      an empty state is a bad first screen even when it's a correct one.

---

# Part 4 — Making the demo video with free AI

**Toolchain (all genuinely free, no watermark):**

| Job | Tool | Notes |
|---|---|---|
| Screen capture | **OBS Studio** | Open source, no limits, no watermark |
| AI narration | **Edge TTS** (`pip install edge-tts`) | Microsoft neural voices, unlimited, free, offline-ish, no account |
| Editing | **CapCut Desktop** or **Shotcut** | CapCut: free, no watermark on export. Shotcut: fully open source |
| Diagrams / title cards | **Canva** free or **Excalidraw** | No watermark on either |
| Background music | **YouTube Audio Library** / **Pixabay Music** | Royalty-free, keep it under −25dB |
| Captions | CapCut auto-captions | Free; judges often watch muted first |

Avoid AI *avatar* tools (HeyGen, Synthesia, D-ID) — free tiers watermark, cap at 2–3 minutes,
and a synthetic presenter reads as filler. A clean screen recording with crisp narration beats
an AI talking head every time for a technical judge.

## 4.1 Why AI narration beats live recording here

You will re-record the script four times. Recording your own voice four times in a hostel room
at 2am produces four different audio qualities and a lot of background noise. Edge TTS gives
you: consistent quality, no ambient noise, instant re-generation when you tweak a word, and
precise control over pacing. Write the script, generate the audio, cut the video to the audio.

## 4.2 Generate the voiceover

```bash
pip install edge-tts
edge-tts --list-voices | grep en-      # pick a voice
```

Good voices for technical demos: `en-US-AriaNeural` (clear, neutral),
`en-GB-RyanNeural` (calm, authoritative), `en-IN-NeerjaNeural` /
`en-IN-PrabhatNeural` (Indian English — often the right choice for an Indian judging panel).

Save `demo/script.txt` with one paragraph per scene, then:

```bash
python demo/make_voiceover.py
```

(script provided alongside this guide as `make_voiceover.py` — it splits the script by scene,
renders each to its own MP3, and prints the duration of each so you can cut video to match.)

Use SSML-ish control via the CLI flags when a line rushes: `--rate=-8%` slows delivery,
`--pitch=-2Hz` deepens it slightly. Slightly slower than feels natural is right for demos.

## 4.3 The script — 4:00, scene by scene

Total budget 3–5 minutes; aim for **4:00** so edits have room.

| # | Time | On screen | Narration beat |
|---|---|---|---|
| 1 | 0:00–0:20 | Title card, then a wall of course thumbnails | The problem: thousands of courses, no idea what order. Recommenders return *relevant*; learners need *sequence* |
| 2 | 0:20–0:50 | Intake chat; profile card filling live | "I'm a 2nd-year CS student, know Python, want to be an ML engineer, 6 hours a week" → structure appears |
| 3 | 0:50–1:20 | Diagnostic, answer one wrong on purpose | We *measure* instead of trusting self-report. Watch the confidence meter |
| 4 | 1:20–2:20 | Path screen + skill graph | The DAG. Prerequisites before dependents, guaranteed. Open a **Why?** chip and read the real provenance |
| 5 | 2:20–2:50 | Mark "too easy", fail a milestone | The diff banner. Adaptation you can see |
| 6 | 2:50–3:10 | WhatIf slider 6h → 12h | Finish week recomputes live |
| 7 | 3:10–3:30 | Dashboard | Progress, mastery radar, next 3 actions |
| 8 | 3:30–3:50 | Architecture diagram + eval table | Where the AI is and isn't. Zero prerequisite violations, 100% grounded resources, p95 under 2s |
| 9 | 3:50–4:00 | Live URL + repo | Close on the deployed link |

Scene 5 is your strongest 30 seconds. Scene 8 is what wins the AI/ML score. If you have to
cut, cut scene 7 — not 5 or 8.

## 4.4 Recording rules that save the most time

- **Pre-seed a demo learner.** Never type into a form on camera. Have `demo_learner_1` ready
  with a completed diagnostic; jump straight to the interesting screens.
- **Record at 1920×1080, 30fps, browser at 100% zoom** with bookmarks bar hidden and a clean
  desktop. Judges notice a cluttered taskbar.
- **Record scenes separately** so you re-shoot one scene, not the whole video.
- **Record silent.** Narration is added in editing. This lets you cut dead air freely.
- **Zoom in on the Why? chip and the diff banner** in the editor (CapCut: keyframe scale to
  1.4×). Text that's readable on your monitor is unreadable in a compressed upload.
- **Show one failure recovering.** Pull the API key, show the degraded banner, keep going.
- **Export**: 1080p, H.264, 8–10 Mbps, then upload unlisted to YouTube and put the link in the
  submission. Also keep the MP4 in case they want a file.

## 4.5 Realistic time budget

Script 45min · voiceover 20min · screen capture 90min · edit 2h · review + fix 45min.
**About 5–6 hours.** Book a full day. The video is a required deliverable and a bad one
undersells work you already did.

---

# Part 5 — Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `run.bat` says no Python found | Not on PATH | Reinstall Python with "Add to PATH" ticked, open a **new** terminal |
| pip install fails | Proxy / antivirus | Run the pip command manually to see the real error; try a mobile hotspot |
| Backend window opens then closes | Import error or port in use | Run uvicorn manually in that folder to see the traceback |
| `EADDRINUSE` on 5173 | Old dev server alive | `npx kill-port 5173` or change the port in `run.bat` |
| Frontend loads, all requests fail | CORS or wrong `VITE_API_BASE` | Check `CORS_ORIGINS` includes the exact frontend origin, scheme included |
| "llm_degraded" banner in prod | Key missing or rate-limited | Check the Space secret; free Gemini tiers have daily caps |
| Path missing prerequisites | Broken edge in `skills.json` | Run the graph validator test — it names the offending node |
| Deployed app is blank | Build output dir wrong | Vercel root = `frontend`, output = `dist` |
| Space stuck "Building" | Dockerfile port | Must be 7860, and `--host 0.0.0.0` |
| SQLite "database is locked" | Concurrent writes | Enable WAL mode at startup: `PRAGMA journal_mode=WAL` |
| Data gone after redeploy | HF ephemeral storage | Expected — re-seed on boot, or move to hosted Postgres (§3.2) |

---

## Appendix — final week checklist

- [ ] `run.bat` works from a fresh clone in an empty folder (delete .venv and node_modules first)
- [ ] All catalog URLs return 2xx (run the link checker)
- [ ] Eval table generated and pasted into README + deck
- [ ] Deployed frontend + backend both live, tested from a phone
- [ ] Commit history is incremental — one commit per build phase, honestly named
- [ ] ZIP contains no `.venv`, `node_modules`, `.env`, or `*.db`
- [ ] Demo video uploaded, unlisted link tested in a private window
- [ ] PPT and PDF exported and attached
- [ ] Submitted, with all five deliverables attached and both URLs verified
