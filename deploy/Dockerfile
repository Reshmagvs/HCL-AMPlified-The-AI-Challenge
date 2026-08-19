# Lodestar API on a Hugging Face Space (SDK: Docker, hardware: CPU basic).
#
# Three platform facts drive this file:
#   * the platform routes to port 7860 and nothing else,
#   * the filesystem is ephemeral -- wiped on every rebuild and on wake, and
#   * a cold start should not download anything.
#
# So the database is created at container start rather than baked in, and the
# embedding model is downloaded at *build* time and cached in the image. The
# skill graph, the verified catalogue, the question bank and the embedding
# matrices all ship in the image too, which is why a cold start needs no network
# and no API key at all.
#
# Build context is the repository root:
#   docker build -f deploy/Dockerfile -t lodestar .
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=7860 \
    HF_HOME=/home/user/.cache/huggingface

# Non-root, and uid 1000 specifically -- that is the uid Spaces mounts as.
RUN useradd -m -u 1000 user
WORKDIR /app

COPY --chown=user backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY --chown=user backend/ ./
RUN chown -R user:user /app
USER user

# Download the sentence-embedding model now, so the first request is instant and
# a Space with no outbound network still works.
RUN python -c "from fastembed import TextEmbedding; TextEmbedding(model_name='BAAI/bge-small-en-v1.5')"

# The database lives under the writable home directory, not an image layer.
ENV DATABASE_URL=sqlite:////home/user/lodestar.db \
    LLM_PROVIDER=mock \
    EMBEDDER=auto \
    LOG_LEVEL=INFO

EXPOSE 7860

HEALTHCHECK --interval=60s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:7860/health',timeout=4).status==200 else 1)"

# Seed on every start, because the disk does not survive a rebuild.
CMD ["sh", "-c", "python -m scripts.seed && exec uvicorn app.main:app --host 0.0.0.0 --port 7860"]
