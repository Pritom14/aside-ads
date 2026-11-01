# Hosted demo image for Render (render.yaml at the repo root points here).
# Same as the `demo` target in the root Dockerfile, as a single-stage file because Render's
# Blueprint spec builds a Dockerfile without choosing a stage.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    FASTEMBED_CACHE_PATH=/models \
    ASIDE_DATA_DIR=/tmp/aside-demo \
    HF_HOME=/tmp/hf \
    HOST=0.0.0.0 \
    PORT=10000

WORKDIR /app
COPY pyproject.toml README.md LICENSE alembic.ini ./
COPY src ./src
COPY sdks/python ./sdks/python
COPY examples/chat-demo ./examples/chat-demo
RUN pip install ".[embeddings]" ./sdks/python \
 && python -c "from fastembed import TextEmbedding; TextEmbedding('BAAI/bge-small-en-v1.5', cache_dir='/models')" \
 && useradd --create-home --uid 10001 aside \
 && mkdir -p /tmp/aside-demo && chown -R aside /tmp/aside-demo && chmod -R a+rwX /models

USER aside
EXPOSE 10000
CMD ["python", "examples/chat-demo/hosted.py"]
