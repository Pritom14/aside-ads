"""The sensitive-context guard: no ads next to crises, illness, minors, grief and similar."""

from __future__ import annotations

import json

import pytest

from aside_ads.classify import build_sensitive_guard
from aside_ads.config import DEFAULT_SENSITIVE_CATEGORIES
from aside_ads.matching import ContextAnalysis
from aside_ads.matching.bm25 import BM25Matcher
from aside_ads.matching.embeddings import EmbeddingMatcher
from tests.conftest import build_world, make_settings


@pytest.mark.parametrize(
    "text,category",
    [
        ("I don't want to live anymore, I keep thinking about suicide", "self_harm"),
        ("I've been hurting myself again", "self_harm"),
        ("my dad has chest pain and his arm is numb", "medical_emergency"),
        ("I'm 14 years old, how do I travel alone", "minors"),
        ("I'm in 8th grade and need help planning a trip", "minors"),
        ("my mother passed away, how do I plan the funeral trip", "grief"),
        ("I was diagnosed with cancer last week", "serious_illness"),
        ("my husband hits me when he drinks", "abuse_violence"),
        ("I relapsed after six months", "addiction"),
        ("I'm being evicted and can't pay rent", "financial_distress"),
        ("मैं आत्महत्या के बारे में सोच रहा हूँ", "self_harm"),
    ],
)
def test_guard_detects_default_categories(text: str, category: str) -> None:
    guard = build_sensitive_guard(DEFAULT_SENSITIVE_CATEGORIES.split(","), BM25Matcher(), 0.7)
    hits = guard.classify(ContextAnalysis(text))
    assert category in {h.category for h in hits}


@pytest.mark.parametrize(
    "text",
    [
        "how do I kill a zombie process in linux",
        "this bug is killing me, the build keeps failing",
        "plan a 5 day trip to Tokyo",
        "what is a stroke in tennis",
    ],
)
def test_guard_ignores_everyday_language(text: str) -> None:
    guard = build_sensitive_guard(DEFAULT_SENSITIVE_CATEGORIES.split(","), BM25Matcher(), 0.7)
    assert guard.classify(ContextAnalysis(text)) == []


async def test_no_ad_on_sensitive_context_even_when_relevant(world) -> None:  # type: ignore[no-untyped-def]
    await world.campaign("Trip Cover", "travel insurance for an international trip\nplanning a trip abroad")
    ok = await world.serve("planning an international trip abroad, need travel insurance")
    assert ok["ad"] is not None
    blocked = await world.serve("my mother passed away abroad, planning the international trip home")
    assert blocked["ad"] is None and blocked["reason"] == "sensitive_context"


async def test_request_flag_forces_no_ad(world) -> None:  # type: ignore[no-untyped-def]
    await world.campaign("Trip Cover", "travel insurance for an international trip")
    out = await world.serve("travel insurance for an international trip", sensitive=True)
    assert out["ad"] is None and out["reason"] == "sensitive_context"


async def test_extra_terms_file_and_category_selection(tmp_path) -> None:  # type: ignore[no-untyped-def]
    terms = tmp_path / "terms.json"
    terms.write_text(json.dumps({"elections": ["ballot", "polling booth"]}))
    w, rt = await build_world(
        make_settings(tmp_path, sensitive_terms_file=terms, sensitive_categories="medical_emergency")
    )
    try:
        await w.campaign("Trip Cover", "travel insurance for an international trip")
        out = await w.serve("travel insurance for an international trip after I cast my ballot")
        assert out["reason"] == "sensitive_context"
        # grief is not enabled in this configuration, so it no longer blocks
        out = await w.serve("travel insurance for an international trip to the funeral")
        assert out["ad"] is not None
    finally:
        await w.client.aclose()
        await rt.close()


def test_semantic_exemplars_used_when_embeddings_available() -> None:
    class Emb:
        def _v(self, t: str) -> list[float]:
            crisis = any(
                w in t.lower()
                for w in ("disappear", "live anymore", "ending my life", "hurt myself", "miss me")
            )
            return [1.0, 0.0] if crisis else [0.0, 1.0]

        def embed_queries(self, texts):  # type: ignore[no-untyped-def]
            return [self._v(t) for t in texts]

        embed_passages = embed_queries

    guard = build_sensitive_guard(["self_harm"], EmbeddingMatcher(Emb()), 0.7)
    hits = guard.classify(ContextAnalysis("sometimes I just want to disappear"))
    assert hits and hits[0].via == "semantic"
    assert guard.classify(ContextAnalysis("recommend a sci-fi novel")) == []
