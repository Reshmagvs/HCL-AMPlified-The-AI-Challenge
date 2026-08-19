# Deep Analysis — AI-Powered Personalized Learning Path Recommender

_Round 2, AMPlified · working product name: **Lodestar** (rename freely)_

---

## 1. What the problem actually is

The brief reads like a recommender-system problem. It isn't. Read the background paragraph
again: _"learners often struggle to identify the right **sequence** of learning resources."_

Recommendation and sequencing are different problems:

| | Recommendation | Sequencing |
|---|---|---|
| Question | "What is relevant to me?" | "What do I do **first**, and what am I allowed to do only after?" |
| Math | similarity in embedding space | constrained topological ordering over a dependency graph |
| Failure mode | irrelevant items | relevant items in an impossible order |

Almost every team in this round will build cosine similarity over course descriptions, return
the top 10, and label it a "learning path." That output is a **ranked list wearing a path's
clothes**. It has no prerequisite guarantees, no time model, no notion of the learner's
current position.

**The core construct is therefore:** given a goal, a dependency graph of skills, and an
estimate of what the learner already knows, produce the shortest valid ordering of learning
resources that closes the gap within the learner's real time budget — and keep it valid as
the learner's state changes.

That reframing is the single highest-leverage thing in this document. It is worth 20% of the
score on its own (Problem Understanding & Solution Design) and it shapes every other decision.

---

## 2. Decomposition into sub-problems

The brief's six bullets map onto five real engineering problems:

1. **Goal → skill decomposition.** "I want to be an ML engineer" must resolve to a concrete
   set of skill nodes. Free-text goal → graph node(s). Cannot be done by an LLM alone
   reliably; do embedding retrieval over a curated node set, then LLM disambiguation.
2. **Learner state estimation.** What do they already know? The brief says "capturing
   experience level" — i.e. self-report. Self-report is the weakest signal in all of
   education technology. See §3.1.
3. **Gap computation.** Ancestor closure of the goal node(s) in the DAG, minus the mastered
   set. Pure graph work, deterministic, fast, explainable.
4. **Resource binding + path assembly.** For each gap node, retrieve candidate resources,
   pick under constraints (cost, format preference, duration), then pack the ordered nodes
   into a weekly schedule under the time budget.
5. **Adaptation.** Events change learner state → recompute → show the **diff**.

Notice that only steps 1, 4 (retrieval) and the explanation layer need AI. Steps 3 and 5 are
deterministic graph algorithms. **This is a feature, not a shortfall** — it is what makes the
system explainable and non-hallucinating. Say this explicitly in the deck; judges scoring
"AI/ML Implementation" reward knowing *where* ML belongs, not maximal ML everywhere.

---

## 3. What is missing from the problem statement

These are the gaps. Each one is a scoring opportunity because most teams will not notice them.

### 3.1 There is no placement mechanism
The background says the system should "identify skill gaps," but the "What to build" list
contains no way to measure the learner. It only says *capture* experience level — self-report.

**Fix:** an adaptive diagnostic. 6–10 LLM-generated multiple-choice items, seeded from the
skill nodes on the candidate path, graded deterministically against a stored answer key.
Update per-node mastery (0–1). Stop early once confidence is sufficient. Result: the path
starts where the learner *actually* is, not where they *claim* to be. This is the difference
between "personalized" as a marketing word and as a measurement.

### 3.2 There is no time budget
A path with no calendar is a wish list. "6 hours/week for 12 weeks" is 72 hours — that
changes everything about what fits.

**Fix:** capture hours/week + target date at intake. Bin-pack ordered nodes into weekly
milestones. Show "you will reach your goal in week 14" and make it recompute live. A
"what-if" control (double my hours) is a 20-second demo moment that lands hard.

### 3.3 No course catalog is specified — and this is a trap
The brief never says where courses come from. Teams will let the LLM generate course names
and URLs. **Those URLs will be hallucinated**, judges will click one, and it will 404.

**Fix (non-negotiable):** a curated static catalog (`data/courses.json`, 400–800 real
entries: freeCodeCamp, NPTEL/SWAYAM, MIT OCW, Khan Academy, Coursera audit tracks, official
docs). The LLM is **never** allowed to emit a resource that isn't in the catalog — it selects
by ID and explains. Every recommendation in the UI carries a real, clickable, working link.
Enforce it with a schema validator on the LLM output. Put this on a slide titled
"Zero hallucinated resources."

### 3.4 "Explain why" is under-specified
The weak version is asking an LLM to write a paragraph. It sounds plausible and proves
nothing.

**Fix:** structured provenance. Every recommendation carries a machine-generated trace:
`[covers skill: Vectorization] → [prerequisite of: Gradient Descent] → [your diagnostic:
1/5] → [fits: 4h of your 6h week] → [chosen over 3 alternatives because: free + video]`.
The LLM narrates that trace; it does not invent it. Render it as an expandable chip under
each card. This is simultaneously the explainability feature, the trust feature, and the
debugging tool.

### 3.5 "Adapt based on feedback" has no trigger model
Teams will add a thumbs-up button that writes to a table and does nothing.

**Fix:** define the event → response table explicitly.

| Event | System response |
|---|---|
| Milestone quiz failed | Insert remediation node before continuing; lower mastery |
| Marked "too easy" | Raise mastery, skip node, pull the path forward |
| Marked "too hard" | Insert the missing prerequisite the diagnostic under-weighted |
| Fell 2 weeks behind | Re-pack schedule; offer scope reduction options |
| Goal changed | Recompute; preserve overlapping completed nodes |
| Resource disliked | Rebind that node to the next-best resource, same skill |

Then **show the diff** — "3 items added, 2 removed, finish date moved +1 week." Adaptation
you can see is adaptation the judge believes.

### 3.6 There is no evaluation of the recommender
Nobody in a student hackathon evaluates their model. "AI/ML Implementation" is 20%.

**Fix:** a tiny offline harness. 20 synthetic personas with hand-written gold paths. Report:
prerequisite-order violation rate (target 0%), goal-skill coverage %, redundancy rate,
mean path length vs gold, p95 latency. One table in the deck. It costs an afternoon and it
is the single most credible slide you will have.

### 3.7 Cold start is unaddressed
No history for a new user — the classic recommender failure. Your conversational intake plus
the diagnostic *is* the cold-start solution. Name it as such; don't let it go unclaimed.

### 3.8 Context, cost and access
Indian learners on limited data plans, on Android, often needing free-only resources.
Add: a **free-only filter**, a bandwidth-light mode (text/PDF resources over video), and
NPTEL/SWAYAM in the catalog. Cheap to build, strong under Innovation & Creativity, and it is
a real answer to "who is this actually for."

### 3.9 Nothing about safety, privacy, or failure
- The learner profile is personal data. Local-first SQLite, exportable, deletable.
- The LLM will fail (rate limit, downtime). The system must degrade gracefully: the graph
  and retrieval layers work with **zero** LLM calls. Prove it live by pulling the API key
  in the demo. That is a memorable 15 seconds.

---

## 4. What to deliberately NOT build

Scope discipline is a judged quality. Skip:

- User accounts, OAuth, password resets → one local profile, or a profile ID in the URL.
- A course player / video hosting → link out.
- Live scraping of Coursera/Udemy at demo time → brittle, rate-limited, will fail on stage.
- Mobile app → responsive web is enough.
- Fine-tuning anything → no time, no data, no benefit here.
- A microservices architecture → one FastAPI app. "Performance & Code Quality" rewards a
  clean modular monolith, not distributed complexity you cannot justify.

---

## 5. Mapping to the judging rubric

| Criterion | Weight | What wins it |
|---|---|---|
| Problem Understanding & Solution Design | 20% | The sequencing-vs-recommendation reframe (§1); the gap analysis in §3; explicit non-goals (§4) |
| Functionality & Feature Completeness | 25% | All six brief bullets working end-to-end, plus diagnostic, time budget, adaptation diff |
| AI/ML Implementation | 20% | Hybrid retrieval + DAG algorithms + grounded generation with schema enforcement + the eval harness (§3.6) |
| Innovation & Creativity | 15% | Diagnostic placement, what-if replanning, path diff view, free-only/low-bandwidth mode, LLM-optional degradation |
| User Experience & Interface | 10% | The skill-graph visualization, one-screen dashboard, empty/loading/error states |
| Performance & Code Quality | 10% | Cached embeddings, p95 latency numbers, tests, typed API contracts, honest README |

---

## 6. Two non-obvious submission risks

1. **"Commit history should reflect the development process."** A repo with one commit dumped
   at the end reads as either a last-minute panic or as generated wholesale. Building solo
   does not change this — what matters is that commits are incremental and honestly named, not
   that there are many authors. Commit at every phase boundary, starting with an empty
   scaffold before you write a line of application code. Ten meaningful commits across a build
   tell the right story on their own.

2. **The demo video is 3-5 minutes for a system with six subsystems.** You cannot show
   everything. Pre-seed a demo profile so nothing is typed live, and budget: 20s problem,
   30s intake, 30s diagnostic, 60s path + explanation, 45s adaptation diff, 30s dashboard,
   25s architecture + close. Script it to the second.

A third, specific to building alone: **you are your own code reviewer.** The offline
evaluation harness in section 3.6 and the test pass in `02_FINAL_TEST_PROMPT.md` are doing the
job a second pair of eyes would otherwise do. On a team they are good practice; solo they are
the only thing standing between you and a confident, broken demo. Do not skip them.

## 7. The one-sentence pitch

> Lodestar treats learning as a dependency graph, not a search result: it measures where you
> actually are with a short adaptive diagnostic, computes the shortest valid route to your
> goal, packs it into the hours you actually have, explains every step with a traceable
> reason, and re-plans the moment reality changes — using only real, working, mostly free
> resources.
