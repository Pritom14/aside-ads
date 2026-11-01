"""Budgets, atomic spend accounting under concurrency, pacing and frequency caps."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import func, select

from aside_ads.models import Campaign, Event
from aside_ads.pacing import pacing_allows
from tests.conftest import build_world, make_settings

DB_TEXT = "slow postgres queries\ndatabase indexing"
DB_CONTEXT = "my postgres queries are slow, should I add an index?"


async def _campaign(world, cid: str) -> Campaign:  # type: ignore[no-untyped-def]
    async with world.rt.sessionmaker() as s:
        return (await s.execute(select(Campaign).where(Campaign.id == cid))).scalar_one()


async def _billed(world, kind: str) -> int:  # type: ignore[no-untyped-def]
    async with world.rt.sessionmaker() as s:
        return (
            await s.execute(select(func.count()).where(Event.kind == kind, Event.billed.is_(True)))
        ).scalar_one()


async def test_no_overspend_under_concurrent_serves_and_impressions(world) -> None:  # type: ignore[no-untyped-def]
    # Bid = floor, so each impression costs exactly 0.10 CPM = 100 micros. Budget fits 10 impressions.
    cid = await world.campaign("DB", DB_TEXT, bid="0.10", daily_budget=Decimal("0.001"))

    async def serve_and_view() -> str:
        out = await world.serve(DB_CONTEXT)
        if out["ad"] is None:
            return "no_ad"
        r = await world.impression(out["ad"])
        return r.headers["x-aside-status"]

    results = await asyncio.gather(*(serve_and_view() for _ in range(40)))
    c = await _campaign(world, cid)
    assert c.spend_total_micros == 1_000  # exactly the budget, never more
    assert c.spend_today_micros == 1_000
    assert await _billed(world, "impression") == 10
    assert results.count("counted") >= 10
    # Exhausted: stop serving.
    after = await world.serve(DB_CONTEXT)
    assert after["ad"] is None and after["reason"] == "no_eligible_campaigns"


async def test_lifetime_budget_caps_concurrent_clicks(world) -> None:  # type: ignore[no-untyped-def]
    # CPC bid at the floor: each click costs 0.01. Lifetime budget fits 5 clicks.
    cid = await world.campaign("DB", DB_TEXT, bid_type="cpc", bid="0.01", total_budget=Decimal("0.05"))
    ads = [(await world.serve(DB_CONTEXT))["ad"] for _ in range(20)]
    assert all(ads)
    responses = await asyncio.gather(*(world.click(ad) for ad in ads))
    assert all(r.status_code == 302 for r in responses)  # users always reach the advertiser
    c = await _campaign(world, cid)
    assert c.spend_total_micros == 50_000
    assert await _billed(world, "click") == 5
    assert (await world.serve(DB_CONTEXT))["ad"] is None


async def test_concurrent_duplicate_impressions_bill_once(world) -> None:  # type: ignore[no-untyped-def]
    cid = await world.campaign("DB", DB_TEXT, bid="0.10")
    ad = (await world.serve(DB_CONTEXT))["ad"]
    rs = await asyncio.gather(*(world.impression(ad) for _ in range(15)))
    statuses = [r.headers["x-aside-status"] for r in rs]
    assert statuses.count("counted") == 1 and statuses.count("duplicate") == 14
    c = await _campaign(world, cid)
    assert c.impressions_total == 1 and c.spend_total_micros == 100


def test_pacing_curve() -> None:
    budget = 1_000_000
    assert pacing_allows(200_000, budget, 0.25, 0.05, "even")
    assert not pacing_allows(310_000, budget, 0.25, 0.05, "even")
    assert pacing_allows(990_000, budget, 0.99, 0.05, "even")
    assert pacing_allows(900_000, budget, 0.10, 0.05, "asap")
    assert pacing_allows(10**9, None, 0.0, 0.0, "even")


async def test_even_pacing_throttles_then_resumes(tmp_path) -> None:  # type: ignore[no-untyped-def]
    w, rt = await build_world(make_settings(tmp_path, pacing_buffer=0.0))
    clock = {"now": datetime(2026, 3, 2, 6, 0, tzinfo=timezone.utc)}  # 25% through the UTC day
    rt.clock = lambda: clock["now"]
    try:
        # 100 micros per impression, daily budget 1000: 250 allowed by 06:00.
        cid = await w.campaign("DB", DB_TEXT, bid="0.10", daily_budget=Decimal("0.001"))
        served = 0
        for _ in range(10):
            out = await w.serve(DB_CONTEXT)
            if out["ad"] is None:
                break
            served += 1
            await w.impression(out["ad"])
        assert served == 3  # 0, 100, 200 spent before each serve; at 300 > 250 pacing holds back
        assert (await _campaign(w, cid)).spend_today_micros == 300
        clock["now"] = clock["now"].replace(hour=18)
        assert (await w.serve(DB_CONTEXT))["ad"] is not None
        # Next day: daily spend resets even though the row still holds yesterday's figure.
        clock["now"] = clock["now"] + timedelta(days=1)
        assert (await w.serve(DB_CONTEXT))["ad"] is not None
    finally:
        await w.client.aclose()
        await rt.close()


async def test_frequency_cap_per_user(world) -> None:  # type: ignore[no-untyped-def]
    await world.campaign("DB", DB_TEXT, freq_cap=2)
    seen = [await world.serve(DB_CONTEXT, user_id="user-1") for _ in range(3)]
    assert [s["ad"] is not None for s in seen] == [True, True, False]
    assert seen[2]["reason"] == "frequency_capped"
    assert (await world.serve(DB_CONTEXT, user_id="user-2"))["ad"] is not None


async def test_frequency_cap_strict_under_concurrency(world) -> None:  # type: ignore[no-untyped-def]
    await world.campaign("DB", DB_TEXT, freq_cap=2)
    outs = await asyncio.gather(*(world.serve(DB_CONTEXT, user_id="same-user") for _ in range(12)))
    assert sum(1 for o in outs if o["ad"]) == 2


async def test_frequency_capped_campaign_yields_to_next_bidder(world) -> None:  # type: ignore[no-untyped-def]
    first = await world.campaign("First", DB_TEXT, bid="9", freq_cap=1)
    second = await world.campaign("Second", DB_TEXT, bid="2")
    a = await world.serve(DB_CONTEXT, user_id="u")
    b = await world.serve(DB_CONTEXT, user_id="u")
    assert a["ad"]["campaign_id"] == first
    assert b["ad"]["campaign_id"] == second
