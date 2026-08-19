# Build Lodestar solo, start to finish

> **Want the hands-off version?** Paste `MASTER_PROMPT_ALL_IN_ONE.md` into Claude Code and it
> runs all ten phases autonomously, pausing only at the six stop points. This file is the
> manual, phase-gated route — use it if you want to inspect between steps, or as a reference
> for what the all-in-one prompt is doing under the hood. The two produce the same product.

One person. No feature cuts. Sequenced so nothing ever blocks on anything else.

Everything below is a **session**, not a day. Do them back to back if you want. Total focused
time is roughly **18–24 hours**. The order matters — each session's output is the next
session's input.

---

## Before session 1 — the two decisions

**1. Where you run the agent.** Claude Code in a terminal is the right tool here: it can
create files, run tests, and read its own errors. Cursor or Windsurf work too. The phase
prompts in `01b_PHASE_PROMPTS.md` assume an agent that can write files and run commands.

**2. What "solo" changes.** Nothing about the product. It changes *how you sequence*:
- You can't parallelise curation, so you automate it (session 3 does exactly this).
- You commit as one author — that's fine. What matters is that commits are **incremental and
  meaningful**, not that there are many contributors. Ten honest commits across a build beat
  one dump at the end.
- You review your own work, so the test prompt in session 9 does more heavy lifting than it
  would on a team. Don't skip it.

> One practical note: the brief states teams are 3–5 people from the same college. You can
> build the whole thing yourself, but you'll still need registered teammates on the submission
> form. Sort that out separately — it doesn't affect anything below.

---

## Session 0 — Setup (20 minutes)

```bash
# 1. Tools
#    Python 3.11+  https://www.python.org/downloads/  (tick "Add to PATH")
#    Node LTS      https://nodejs.org/
#    Git           https://git-scm.com/downloads
#    GitHub CLI    https://cli.github.com/   (optional but makes push.bat one command)

# 2. Verify
python --version && node --version && git --version

# 3. Free Gemini key (2 minutes, no card)
#    https://aistudio.google.com/apikey  -> copy it somewhere safe

# 4. Make the folder and drop in the kit files
mkdir lodestar && cd lodestar
#    copy run.bat, run.sh, push.bat, .gitignore, .env.example here

# 5. First commit — do this NOW, not at the end
push.bat init
```

**Why the first commit before any code:** your repo history should start when your work
starts. A repo created hours before submission looks exactly like what it is.

---

## Session 1 — Read the analysis (30 minutes)

Read `00_PROBLEM_ANALYSIS.md` end to end. Not skim — read. Every architectural decision in
the phase prompts traces back to something in that document, and when the agent proposes an
alternative in session 4 you need to know which parts are load-bearing.

The one sentence to internalise: **learning is a dependency graph, not a search result.**
Everything else follows.

---

## Session 2 — Phases 0–1: scaffold and data spine (2–3 hours)

Open your agent in the `lodestar` folder. Paste `01_MASTER_BUILD_PROMPT.md` as the first
message. It ends by asking the agent to confirm its understanding and build Phase 0 only —
read that confirmation carefully before saying continue.

Then run **Prompt 0** and **Prompt 1** from `01b_PHASE_PROMPTS.md`.

At the end of this session you have: a running FastAPI health endpoint, a Vite frontend that
loads, `run.bat` working, and the skill graph loading with cycle validation.

```bash
run.bat --backend          # should serve /health
push.bat "chore: scaffold backend, frontend and bootstrap scripts"
push.bat "feat: skill graph loader with DAG validation"
```

---

## Session 3 — Phase 2: the catalog, automated (2–3 hours)

**This is the session that would have been split across four teammates. Solo, you automate
it instead — and the automated version is actually better.**

The loop is: the model *proposes* candidate resources, a script *verifies* every URL with a
real HTTP request, dead links are discarded automatically, and you spot-check a sample. No
hallucinated link can survive, and you get 400+ entries in an afternoon instead of a week.

Run **Prompt 2** from `01b_PHASE_PROMPTS.md`. It builds:
- `scripts/harvest_catalog.py` — generates candidates per skill node, in batches
- `scripts/verify_catalog.py` — HEAD/GET every URL, drop non-2xx, report a summary
- `scripts/build_embeddings.py` — precompute and commit the embedding matrix

Then actually run them:

```bash
cd backend
.venv\Scripts\python -m scripts.harvest_catalog --track machine-learning
.venv\Scripts\python -m scripts.harvest_catalog --track web-development
# ... one per track
.venv\Scripts\python -m scripts.verify_catalog        # discards dead links
.venv\Scripts\python -m scripts.build_embeddings
```

**Spot-check 15 random entries yourself.** Open them. A URL returning 200 can still be a
paywall or a redirect to a homepage. Fifteen minutes here protects the whole demo.

```bash
push.bat "feat: verified resource catalog with automated link checking"
```

---

## Session 4 — Phase 3: the LLM boundary (1 hour)

Run **Prompt 3**. Provider interface, Gemini implementation, MockProvider, prompt registry.

Test both paths before moving on:
```bash
# .env: LLM_PROVIDER=mock    -> everything works, canned text
# .env: LLM_PROVIDER=gemini  -> everything works, generated text
```

If the mock path breaks later, you've lost your ability to test offline and your demo's
failure-resilience story. Verify it now.

```bash
push.bat "feat: pluggable LLM provider with deterministic mock"
```

---

## Session 5 — Phases 4–5: intake and diagnostic (2–3 hours)

Run **Prompt 4**, then **Prompt 5**. Conversational extraction, goal resolution, adaptive
diagnostic, mastery model.

Checkpoint: create a learner through the API with curl or `/docs`, run the diagnostic, and
confirm mastery rows actually change in the DB. If self-report is landing above 0.4, the cap
isn't wired.

```bash
push.bat "feat: conversational intake and goal resolution"
push.bat "feat: adaptive diagnostic placement and mastery model"
```

---

## Session 6 — Phase 6: the planner (2–3 hours)

Run **Prompt 6**. This is the heart of the product and the longest single phase.

Do not move on until this assertion passes: **generate 100 paths across random goals and get
zero prerequisite-order violations.** Prompt 6 tells the agent to write that test. If it
fails, the graph has a bad edge or the topological sort has a bug — fix it here, because
every later feature sits on top of it.

```bash
push.bat "feat: learning path generation with provenance traces"
```

---

## Session 7 — Phase 7: adaptation (1–2 hours)

Run **Prompt 7**. All seven events, path versioning, diff computation.

Checkpoint: fire `too_easy` on an item and confirm you get a *new version* plus a correct
diff — not a mutated row.

```bash
push.bat "feat: event-driven replanning with versioned path diffs"
```

---

## Session 8 — Phase 8: the frontend (3–4 hours)

Run **Prompt 8**. Four screens, the skill graph, the Why? chips, the diff banner, the what-if
slider, the dashboard.

This is the longest session and the one where an agent drifts most. Build **one screen at a
time**, look at it in the browser, then move on. Don't let it generate all four before you've
seen any of them.

```bash
push.bat "feat: intake, diagnostic, path and dashboard interface"
```

---

## Session 9 — Phase 9 + full test pass (2–3 hours)

Run **Prompt 9** (eval harness, tests, README), then work through
`02_FINAL_TEST_PROMPT.md` section by section.

Section 0 is the one that matters most: clone into a *fresh folder* and run `run.bat`. That
is exactly what an evaluator does. Everything else is downstream of that working.

Expect to find 5–15 real bugs here. That's the point.

```bash
push.bat "test: offline evaluation harness and integration tests"
push.bat "docs: README with architecture, quickstart and eval results"
```

---

## Session 10 — Deploy (1–2 hours)

Follow `PROJECT_GUIDE.md` Part 3. Backend to a Hugging Face Space (Docker, port 7860),
frontend to Vercel, then wire `CORS_ORIGINS` and `VITE_API_BASE` at each other.

Verify from your phone, on mobile data, in a private window. Not from the machine you built it
on — that machine lies to you about caching and localhost.

```bash
push.bat "chore: deployment configuration for Spaces and Vercel"
```

---

## Session 11 — The video (4–5 hours, budget the whole block)

Follow `PROJECT_GUIDE.md` Part 4.

1. Seed a demo learner in production so nothing is typed on camera.
2. Write `demo/script.txt` against the nine-scene table.
3. `python make_voiceover.py` — free Edge neural voices, per-scene durations printed.
4. Record scenes silently in OBS at 1080p.
5. Assemble in CapCut, cut video to the audio, zoom on the Why? chip and the diff banner.
6. Export 1080p H.264, upload unlisted to YouTube.

Solo tip: record scenes **out of order**, hardest first, while you're fresh. Scene 5
(adaptation) and scene 4 (path + provenance) carry the most weight.

---

## Session 12 — Documents and submission (1–2 hours)

1. Run `scripts/evaluate.py`. Copy the **actual** numbers.
2. Open `deck.js` and `docpdf.py`. Replace every `[TEAM NAME]`, every placeholder URL, and
   the evaluation **targets** with your measured results.
3. Regenerate:
   ```bash
   npm install pptxgenjs && node deck.js
   pip install reportlab && python docpdf.py
   ```
4. Zip the repo. Extract to a clean folder. Run `run.bat`. Confirm it works and contains no
   `.venv`, `node_modules`, `.env` or `*.db`.
5. Submit all five deliverables.

---

## The order in one screen

| # | Session | Output | Hours |
|---|---|---|---|
| 0 | Setup + first commit | Tooling, empty repo | 0.3 |
| 1 | Read the analysis | Shared understanding with yourself | 0.5 |
| 2 | Phases 0–1 | Scaffold + skill graph | 2–3 |
| 3 | Phase 2 | Verified catalog + embeddings | 2–3 |
| 4 | Phase 3 | LLM provider + mock | 1 |
| 5 | Phases 4–5 | Intake + diagnostic | 2–3 |
| 6 | Phase 6 | Planner (the core) | 2–3 |
| 7 | Phase 7 | Adaptation + diffs | 1–2 |
| 8 | Phase 8 | Full frontend | 3–4 |
| 9 | Phase 9 + tests | Eval harness, bugs fixed | 2–3 |
| 10 | Deploy | Live URLs | 1–2 |
| 11 | Video | 4-minute demo | 4–5 |
| 12 | Docs + submit | Everything filed | 1–2 |

---

## Four rules for building this alone

**Commit at every session boundary.** Not at the end. Your history is a graded artifact and
it's also your undo button when the agent makes a mess in session 8.

**Never let the agent build two phases at once.** It will offer. The offer is always
tempting and always produces something you can't debug. One phase, verify, commit, next.

**When the agent proposes an alternative architecture, check it against
`00_PROBLEM_ANALYSIS.md` before accepting.** Some of its suggestions will be good. The ones
that quietly remove the skill graph, the catalog constraint, or the provenance object are
the ones that cost you the score.

**Verify by running, not by reading.** The single most common solo failure is reading code
that looks right and marking it done. Every checkpoint above is a command you execute.
