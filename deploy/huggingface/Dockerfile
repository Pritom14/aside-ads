# Hugging Face Space for the Aside demo. Copy this file and README.md from deploy/huggingface/ into a new
# Docker Space. It builds the same image as `docker build --target demo .` from the GitHub source at
# ASIDE_REF (a branch or tag, for example v0.1.0).
FROM python:3.12-slim

ARG ASIDE_REF=main
ARG ASIDE_TARBALL=https://github.com/Pritom14/aside-ads/archive/${ASIDE_REF}.tar.gz
ADD ${ASIDE_TARBALL} /tmp/aside.tar.gz

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    FASTEMBED_CACHE_PATH=/models \
    ASIDE_DATA_DIR=/tmp/aside-demo \
    HF_HOME=/tmp/hf \
    HOST=0.0.0.0 \
    PORT=7860

WORKDIR /app
RUN tar -xzf /tmp/aside.tar.gz --strip-components=1 && rm /tmp/aside.tar.gz \
 && pip install ".[embeddings]" ./sdks/python \
 && python -c "from fastembed import TextEmbedding; TextEmbedding('BAAI/bge-small-en-v1.5', cache_dir='/models')" \
 && chmod -R a+rwX /models

# Spaces run the container as uid 1000.
RUN useradd --create-home --uid 1000 user
USER user
EXPOSE 7860
CMD ["python", "examples/chat-demo/hosted.py"]
