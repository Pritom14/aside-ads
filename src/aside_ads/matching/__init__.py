"""Contextual matching that runs entirely on your server.

Two backends implement the same interface:

* ``EmbeddingMatcher`` uses a small local ONNX model through ``fastembed``
  (CPU only, downloaded once). Relevance is cosine similarity.
* ``BM25Matcher`` is a pure-Python keyword fallback used when fastembed is not
  installed, offline, or explicitly selected (``ASIDE_MATCHER=bm25``).

Both return relevance in [0, 1]. The serving code only shows an ad when its
relevance clears a threshold: no ad beats an irrelevant ad.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Protocol

from aside_ads.text import normalized_phrase_text, tokenize

log = logging.getLogger("aside.matching")


@dataclass
class AdDoc:
    """Text describing one ad candidate. Relevance is the best match over ``lines``."""

    key: str
    lines: list[str] = field(default_factory=list)


class ContextAnalysis:
    """Per-request view of the conversation.

    Privacy: instances live only for the duration of one ``/v1/serve`` call and
    are never persisted, logged or cached across requests. That includes the
    context embedding held in ``vector``.
    """

    __slots__ = ("text", "tokens", "phrase_text", "vector")

    def __init__(self, text: str, max_chars: int = 4000) -> None:
        # Keep the most recent part of long conversations.
        self.text = text[-max_chars:] if len(text) > max_chars else text
        self.tokens = tokenize(self.text, drop_stopwords=True)
        self.phrase_text = normalized_phrase_text(self.text)
        self.vector: list[float] | None = None


class Matcher(Protocol):
    name: str
    default_threshold: float

    def relevance(self, ctx: ContextAnalysis, docs: Sequence[AdDoc]) -> list[float]: ...

    def exemplar_similarity(self, ctx: ContextAnalysis, exemplars: Sequence[str]) -> float | None:
        """Max similarity to example utterances, or None if the backend cannot compare meaning."""
        ...


def build_matcher(
    kind: str, model_name: str = "BAAI/bge-small-en-v1.5", cache_dir: str | None = None
) -> Matcher:
    from aside_ads.matching.bm25 import BM25Matcher

    if kind == "bm25":
        return BM25Matcher()
    try:
        from aside_ads.matching.embeddings import EmbeddingMatcher, FastEmbedEmbedder

        cache = os.environ.get("FASTEMBED_CACHE_PATH") or cache_dir
        return EmbeddingMatcher(FastEmbedEmbedder(model_name, cache_dir=cache))
    except Exception as exc:  # ImportError, download failure, bad model name
        if kind == "embedding":
            raise
        log.warning(
            "Embedding matcher unavailable (%s). Falling back to BM25 keyword matching. "
            "Install with: pip install 'aside-ads[embeddings]'",
            exc.__class__.__name__,
        )
        return BM25Matcher()
