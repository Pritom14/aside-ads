"""Relevance scoring, thresholds and the embedding matcher (with a fake embedder, no network)."""

from __future__ import annotations

import os

import pytest

from aside_ads.matching import AdDoc, ContextAnalysis
from aside_ads.matching.bm25 import BM25Matcher
from aside_ads.matching.embeddings import EmbeddingMatcher
from aside_ads.text import tokenize
from tests.conftest import build_world, make_settings

TOPICS = ["postgres", "travel", "bread", "gpu", "poem"]


class FakeEmbedder:
    """Deterministic bag-of-topics vectors: one dimension per topic word plus a small shared bias."""

    def _vec(self, text: str) -> list[float]:
        words = set(tokenize(text))
        return [1.0 if t in words else 0.0 for t in TOPICS] + [0.3]

    def embed_queries(self, texts):  # type: ignore[no-untyped-def]
        return [self._vec(t) for t in texts]

    def embed_passages(self, texts):  # type: ignore[no-untyped-def]
        return [self._vec(t) for t in texts]


def test_stemming_matches_simple_variants() -> None:
    assert tokenize("Queries indexing builds") == tokenize("query index build")


def test_bm25_prefers_the_matching_ad() -> None:
    m = BM25Matcher()
    docs = [
        AdDoc("db", ["slow postgres queries", "database indexing"]),
        AdDoc("travel", ["travel insurance for international trips"]),
    ]
    scores = m.relevance(ContextAnalysis("why are my postgres queries so slow?"), docs)
    assert scores[0] >= m.default_threshold
    assert scores[1] == 0.0


def test_bm25_unrelated_context_scores_zero() -> None:
    m = BM25Matcher()
    scores = m.relevance(
        ContextAnalysis("what is the capital of australia"), [AdDoc("db", ["slow postgres queries"])]
    )
    assert scores == [0.0]


def test_embedding_matcher_uses_best_line_and_caches_ads() -> None:
    m = EmbeddingMatcher(FakeEmbedder())
    docs = [AdDoc("a", ["postgres tuning", "something else"]), AdDoc("b", ["bread baking"])]
    s = m.relevance(ContextAnalysis("postgres is slow"), docs)
    assert s[0] > 0.9 and s[1] < 0.5
    assert ("p", "postgres tuning") in m._cache  # ad text cached, keyed by ad text only


async def test_serve_respects_relevance_threshold(world) -> None:  # type: ignore[no-untyped-def]
    cid = await world.campaign("DB Tool", "slow postgres queries\ndatabase indexing")
    hit = await world.serve("my postgres queries are slow, should I add an index?")
    assert hit["ad"]["campaign_id"] == cid
    assert hit["ad"]["relevance"] >= world.rt.relevance_threshold
    assert hit["ad"]["label"] == "Sponsored"
    miss = await world.serve("write me a poem about autumn leaves")
    assert miss["ad"] is None
    assert miss["reason"] == "below_relevance_threshold"


async def test_campaign_min_relevance_override(world) -> None:  # type: ignore[no-untyped-def]
    await world.campaign("Strict", "slow postgres queries\ndatabase indexing", min_relevance=0.99)
    out = await world.serve("my postgres queries are slow")
    assert out["ad"] is None


async def test_embedding_matcher_end_to_end(tmp_path) -> None:  # type: ignore[no-untyped-def]
    w, rt = await build_world(
        make_settings(tmp_path, relevance_threshold=0.8), EmbeddingMatcher(FakeEmbedder())
    )
    try:
        cid = await w.campaign("GPU Cloud", "rent a gpu", headline="GPUs by the hour")
        assert (await w.serve("which gpu should I rent?"))["ad"]["campaign_id"] == cid
        assert (await w.serve("tell me about bread"))["ad"] is None
    finally:
        await w.client.aclose()
        await rt.close()


@pytest.mark.skipif(
    not os.environ.get("ASIDE_TEST_FASTEMBED"), reason="set ASIDE_TEST_FASTEMBED=1 (downloads a model)"
)
def test_real_fastembed_model_separates_relevant_from_irrelevant() -> None:
    from aside_ads.matching import build_matcher

    m = build_matcher("embedding")
    docs = [AdDoc("travel", ["planning an international trip or vacation abroad", "travel insurance"])]
    relevant = m.relevance(ContextAnalysis("planning a trip to Japan next month, what should I pack"), docs)[
        0
    ]
    unrelated = m.relevance(ContextAnalysis("what's the difference between a list and a tuple"), docs)[0]
    assert relevant >= m.default_threshold > unrelated
