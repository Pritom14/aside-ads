"""Local embedding matcher (fastembed ONNX model on CPU)."""

from __future__ import annotations

import math
import threading
from collections import OrderedDict
from collections.abc import Sequence
from typing import Protocol

from aside_ads.matching import AdDoc, ContextAnalysis


class Embedder(Protocol):
    def embed_queries(self, texts: Sequence[str]) -> list[list[float]]: ...

    def embed_passages(self, texts: Sequence[str]) -> list[list[float]]: ...


class FastEmbedEmbedder:
    """Wraps ``fastembed.TextEmbedding``. The model is downloaded on first use and cached."""

    def __init__(self, model_name: str, cache_dir: str | None = None) -> None:
        from fastembed import TextEmbedding

        self.model_name = model_name
        self._model = TextEmbedding(model_name, cache_dir=cache_dir)
        self._lock = threading.Lock()

    def embed_queries(self, texts: Sequence[str]) -> list[list[float]]:
        with self._lock:
            return [v.tolist() for v in self._model.query_embed(list(texts))]

    def embed_passages(self, texts: Sequence[str]) -> list[list[float]]:
        with self._lock:
            return [v.tolist() for v in self._model.passage_embed(list(texts))]


def _unit(v: Sequence[float]) -> list[float]:
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


def _dot(a: Sequence[float], b: Sequence[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=False))


class EmbeddingMatcher:
    """Relevance = max cosine similarity between the context and each ad text line.

    Ad and exemplar embeddings (advertiser-provided text, not user data) are
    cached in memory by text. The context embedding is stored only on the
    request-scoped ``ContextAnalysis`` object.
    """

    name = "embedding"
    # Calibrated for BAAI/bge-small-en-v1.5 with multi-line targeting text: on-topic
    # pairs scored 0.76 to 0.9, unrelated pairs 0.45 to 0.62 in our checks.
    # Re-tune ASIDE_RELEVANCE_THRESHOLD for other models.
    default_threshold = 0.65

    def __init__(self, embedder: Embedder, cache_size: int = 20000) -> None:
        self.embedder = embedder
        self._cache: OrderedDict[tuple[str, str], list[float]] = OrderedDict()
        self._cache_size = cache_size
        self._lock = threading.Lock()

    def _context_vector(self, ctx: ContextAnalysis) -> list[float]:
        if ctx.vector is None:
            ctx.vector = _unit(self.embedder.embed_queries([ctx.text])[0])
        return ctx.vector

    def _vectors(self, texts: Sequence[str], mode: str) -> list[list[float]]:
        with self._lock:
            missing = [t for t in dict.fromkeys(texts) if (mode, t) not in self._cache]
        if missing:
            embed = self.embedder.embed_passages if mode == "p" else self.embedder.embed_queries
            fresh = embed(missing)
            with self._lock:
                for t, v in zip(missing, fresh, strict=True):
                    self._cache[(mode, t)] = _unit(v)
                while len(self._cache) > self._cache_size:
                    self._cache.popitem(last=False)
        with self._lock:
            out = []
            for t in texts:
                v = self._cache.get((mode, t))
                if v is None:  # evicted between calls under heavy churn
                    v = _unit(
                        (self.embedder.embed_passages if mode == "p" else self.embedder.embed_queries)([t])[0]
                    )
                out.append(v)
            return out

    def relevance(self, ctx: ContextAnalysis, docs: Sequence[AdDoc]) -> list[float]:
        if not docs:
            return []
        q = self._context_vector(ctx)
        all_lines = [line for d in docs for line in d.lines if line.strip()]
        vecs = dict(zip(all_lines, self._vectors(all_lines, "p"), strict=True))
        scores = []
        for d in docs:
            sims = [_dot(q, vecs[line]) for line in d.lines if line.strip()]
            scores.append(max(0.0, min(1.0, max(sims))) if sims else 0.0)
        return scores

    def exemplar_similarity(self, ctx: ContextAnalysis, exemplars: Sequence[str]) -> float | None:
        if not exemplars:
            return 0.0
        q = self._context_vector(ctx)
        return max(_dot(q, v) for v in self._vectors(list(exemplars), "q"))
