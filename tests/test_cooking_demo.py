"""The cooking demo scenario: ``aside seed-demo --scenario cooking`` and the Rasoi AI chat page."""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

import pytest
from sqlalchemy import select

from aside_ads.models import Advertiser, Campaign, Publisher
from aside_ads.seed import COOKING_ADS, seed_demo
from tests.conftest import build_world, make_settings

REPO = Path(__file__).resolve().parents[1]
LANDING, SERVER = "http://127.0.0.1:8001/landing", "http://127.0.0.1:8000"

# The three questions the cooking demo is built around.
RECIPE = "Paneer butter masala for 4 people?"
KADHAI = "My kadhai keeps sticking, how do I fix it?"
HISTORY = "Who invented the pressure cooker?"


async def _serve(w, key: str, context: str) -> dict:  # type: ignore[no-untyped-def]
    r = await w.client.post(
        "/v1/serve",
        json={"context": context, "placement": "chat-sidebar"},
        headers={"Authorization": f"Bearer {key}"},
    )
    assert r.status_code == 200, r.text
    return r.json()


async def test_cooking_seed_creates_rasoi_ai_with_inr_campaigns(tmp_path: Path) -> None:
    w, rt = await build_world(make_settings(tmp_path))
    try:
        out = await seed_demo(rt.sessionmaker, tmp_path, LANDING, SERVER, scenario="cooking")
        again = await seed_demo(rt.sessionmaker, tmp_path, LANDING, SERVER, scenario="cooking")
        assert out["ASIDE_API_KEY"] == again["ASIDE_API_KEY"]  # idempotent, keys reused
        async with rt.sessionmaker() as s:
            pub = (await s.execute(select(Publisher).where(Publisher.name == "Rasoi AI"))).scalar_one()
            names = set((await s.execute(select(Advertiser.name))).scalars())
            campaigns = (await s.execute(select(Campaign))).scalars().all()
        assert names == {ad.advertiser for ad in COOKING_ADS}
        assert len(campaigns) == len(COOKING_ADS)
        thaila = next(c for c in campaigns if c.name.startswith("Thaila Now"))
        assert (thaila.bid_type, thaila.bid_micros) == ("cpc", 6_000_000)  # INR 6 per click
        assert thaila.daily_budget_micros == 1_500_000_000

        env = (tmp_path / "demo.env").read_text()
        assert "ASIDE_DEMO_SCENARIO=cooking" in env and "ASIDE_CURRENCY=INR" in env

        # Keyword fallback matcher (the embedding model is exercised below when available).
        assert (await _serve(w, out["ASIDE_API_KEY"], KADHAI))["ad"]["advertiser"] == "Lohasutra Cookware"
        assert (await _serve(w, out["ASIDE_API_KEY"], RECIPE))["ad"]["advertiser"] == "Thaila Now"
        chai = await _serve(w, out["ASIDE_API_KEY"], "How do I make chai masala at home?")
        assert chai["ad"]["advertiser"] == "Jeera Lane"
        assert (await _serve(w, out["ASIDE_API_KEY"], "What is the capital of Australia?"))["ad"] is None

        # Switching scenarios in the same data dir issues a key for the other publisher.
        dev = await seed_demo(rt.sessionmaker, tmp_path, LANDING, SERVER, scenario="dev")
        assert dev["ASIDE_API_KEY"] != out["ASIDE_API_KEY"]
        assert "ASIDE_DEMO_SCENARIO=dev" in (tmp_path / "demo.env").read_text()
        async with rt.sessionmaker() as s:
            assert (await s.execute(select(Publisher).where(Publisher.name == "Demo Chat"))).scalar_one()
        assert pub.name == "Rasoi AI"
        with pytest.raises(ValueError):
            await seed_demo(rt.sessionmaker, tmp_path, LANDING, SERVER, scenario="nope")
    finally:
        await w.client.aclose()
        await rt.close()


@pytest.mark.skipif(
    not os.environ.get("ASIDE_TEST_FASTEMBED"), reason="set ASIDE_TEST_FASTEMBED=1 (downloads a model)"
)
async def test_cooking_demo_questions_with_the_embedding_model(tmp_path: Path) -> None:
    from aside_ads.matching import build_matcher

    matcher = build_matcher("embedding", cache_dir=os.environ.get("FASTEMBED_CACHE_PATH"))
    w, rt = await build_world(make_settings(tmp_path), matcher=matcher)
    try:
        out = await seed_demo(rt.sessionmaker, tmp_path, LANDING, SERVER, scenario="cooking")
        key = out["ASIDE_API_KEY"]
        assert (await _serve(w, key, RECIPE))["ad"]["advertiser"] == "Thaila Now"
        assert (await _serve(w, key, KADHAI))["ad"]["advertiser"] == "Lohasutra Cookware"
        history = await _serve(w, key, HISTORY)
        assert history["ad"] is None and history["reason"] == "below_relevance_threshold"
    finally:
        await w.client.aclose()
        await rt.close()


def _chat_demo():  # type: ignore[no-untyped-def]
    spec = importlib.util.spec_from_file_location("aside_chat_demo_test", REPO / "examples/chat-demo/app.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_chat_demo_cooking_page_and_written_answers() -> None:
    chat = _chat_demo()
    page = chat.render_page("cooking")
    assert '"title": "Rasoi AI"' in page and RECIPE in page and "/*ASIDE_DEMO_CONFIG*/" not in page
    assert "@keyframes aside-in" in page  # the sponsored card animates in

    recipe = chat.canned_answer(RECIPE, "cooking")
    assert "Ingredients" in recipe and "400 g paneer" in recipe
    assert "season" in chat.canned_answer(KADHAI, "cooking")
    history = chat.canned_answer(HISTORY, "cooking")
    assert "Denis Papin" in history and "1679" in history
    # Unknown prompts, and the dev scenario, fall back to the existing responder.
    assert chat.canned_answer("Is it safe to eat curd at night?", "cooking").startswith("(Demo responder)")
    assert chat.canned_answer(KADHAI, "dev").startswith("(Demo responder)")
