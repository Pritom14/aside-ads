# Aside images.
#   docker build .                 server: PostgreSQL, Redis and local embeddings enabled (default)
#   docker build --target demo .   hosted demo: ad server + chat demo on one port (deploy/README.md)
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    ASIDE_DATA_DIR=/data \
    FASTEMBED_CACHE_PATH=/models

WORKDIR /app
COPY pyproject.toml README.md LICENSE alembic.ini ./
COPY src ./src

ARG EXTRAS=postgres,redis,embeddings
RUN pip install ".[${EXTRAS}]"

RUN useradd --create-home --uid 10001 aside && mkdir -p /data /models && chown aside /data /models


# Public demo. Seeds fictional campaigns on boot, prints a fresh admin key to the logs, hides the
# dashboard, rate-limits the chat and resets activity daily. Data lives in /tmp: it is disposable.
FROM base AS demo
COPY sdks/python ./sdks/python
COPY examples/chat-demo ./examples/chat-demo
RUN pip install ./sdks/python \
 && python -c "from fastembed import TextEmbedding; TextEmbedding('BAAI/bge-small-en-v1.5', cache_dir='/models')" \
 && chmod -R a+rwX /models
# Any user id works (Hugging Face Spaces runs containers as uid 1000).
ENV ASIDE_DATA_DIR=/tmp/aside-demo \
    HF_HOME=/tmp/hf \
    HOST=0.0.0.0 \
    PORT=8080
USER aside
EXPOSE 8080
HEALTHCHECK --interval=15s --timeout=3s --start-period=60s --retries=5 \
  CMD python -c "import os, urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ[\"PORT\"]}/api/health', timeout=2)"
CMD ["python", "examples/chat-demo/hosted.py"]


FROM base AS server
# Set PRELOAD_MODEL=1 to bake the embedding model into the image (otherwise it downloads on first start).
ARG PRELOAD_MODEL=0
USER aside
RUN if [ "$PRELOAD_MODEL" = "1" ]; then \
      python -c "from fastembed import TextEmbedding; TextEmbedding('BAAI/bge-small-en-v1.5', cache_dir='/models')"; \
    fi

VOLUME ["/data", "/models"]
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=3s --start-period=60s --retries=5 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2)"

# `aside serve` applies Alembic migrations on startup when DATABASE_URL points at PostgreSQL.
CMD ["aside", "serve", "--host", "0.0.0.0", "--port", "8000"]
