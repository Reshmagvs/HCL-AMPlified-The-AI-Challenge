# Deploying Lodestar for zero cost

Frontend on Vercel, API on a Hugging Face Space. No card, no trial clock.

The reason for this pairing rather than the obvious one: a free Render web
service sleeps after **15 minutes** of inactivity and takes about a minute to
wake, so a judge opening the link at 11pm gets a spinner as their first
impression. A free HF Space (CPU basic, 2 vCPU / 16 GB) sleeps only after **48
hours**, which in a judging window means never.

---

## 1. Backend -> Hugging Face Space

Create the Space: **New Space -> SDK: Docker -> Hardware: CPU basic (free) ->
Public**.

The Space expects its `Dockerfile` at the repository root, so copy this one up
one level in the Space repo (or point the Space at a subdirectory build):

```bash
cp deploy/Dockerfile ./Dockerfile
git add Dockerfile && git commit -m "chore: dockerfile for hugging face space"
git remote add space https://huggingface.co/spaces/<user>/lodestar
git push space main
```

**Secrets** (Space settings -> Variables and secrets). They arrive as
environment variables; none of them belong in the repository:

| Key | Value |
|---|---|
| `GEMINI_API_KEY` | your key from https://aistudio.google.com/apikey |
| `LLM_PROVIDER` | `gemini` (omit entirely to run in mock mode) |
| `CORS_ORIGINS` | `https://<your-app>.vercel.app` |

Your API is then at `https://<user>-lodestar.hf.space`. Check
`/health` — `catalog_size` should read 426 and `graph_nodes` 152.

**The ephemeral-disk gotcha.** The Space's filesystem is wiped on every rebuild
and on wake, so the SQLite file does not survive and learner profiles reset. The
container re-seeds on start, so this is a clean reset rather than a failure, and
it is stated in the README's known limitations. If you want persistence, point
`DATABASE_URL` at a free hosted Postgres (Supabase or Neon) — SQLModel makes it
a one-variable change.

## 2. Frontend -> Vercel

1. Import the GitHub repo at vercel.com.
2. **Root directory: `frontend`.** Framework preset: Vite. Build: `npm run build`.
   Output: `dist`. (`deploy/vercel.json` documents the same settings; copy it to
   `frontend/vercel.json` if you prefer it in the repo.)
3. Environment variable: `VITE_API_BASE=https://<user>-lodestar.hf.space`
4. Deploy. Every push to `main` redeploys.

Then add the Vercel URL to the Space's `CORS_ORIGINS` and restart the Space.
The API also allows any `*.vercel.app` origin by regex, so preview deployments
work without reconfiguring anything.

## 3. Before judging

- [ ] Open the Vercel URL on a phone, on mobile data, in a private window. Time it.
- [ ] Hit the Space once ~2 hours before judging so it is warm.
- [ ] Confirm `/health` reports `llm_available: true` in production.
- [ ] Run a full journey on the deployed build, not just locally.
- [ ] Seed a demo learner in production (`python -m scripts.seed_demo`) so the
      first screen a judge sees is populated rather than empty.
- [ ] Put **both** URLs in the README and the submission form.
