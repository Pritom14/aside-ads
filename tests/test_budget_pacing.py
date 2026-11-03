"""Budgets, reservations, atomic spend accounting under concurrency, pacing and frequency caps.

These tests run on SQLite by default and on PostgreSQL when ASIDE_TEST_DATABASE_URL is set.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import func, select

from aside_ads.models import BudgetReservation, Campaign, Event
from aside_ads.pacing import pacing_allows
from aside_ads.reservations import hold_for, purge_expired
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


async def _held(world) -> tuple[int, int]:  # type: ignore[no-untyped-def]
    """(rows, micros) of unexpired reservations."""
    now = world.rt.now()
    async with world.rt.sessionmaker() as s:
        rows, total = (
            await s.execute(
                select(func.count(), func.coalesce(func.sum(BudgetReservation.cost_micros), 0)).where(
                    BudgetReservation.expires_at > now
                )
            )
        ).one()
        return int(rows), int(total)


def _frozen_clock(world):  # type: ignore[no-untyped-def]
    clock = {"now": world.rt.now()}
    world.rt.clock = lambda: clock["now"]
    return clock


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
    assert results.count("counted") == 10  # reservations: no unbilled ads were served
    # Exhausted: stop serving.
    after = await world.serve(DB_CONTEXT)
    assert after["ad"] is None and after["reason"] == "budget_exhausted"


async def test_concurrent_serve_burst_delivers_only_what_the_budget_pays_for(world) -> None:  # type: ignore[no-untyped-def]
    # 60 concurrent serves against a budget of 10 impressions (100 micros each). Before
    # reservations all 60 were served and only 10 billed.
    cid = await world.campaign("DB", DB_TEXT, bid="0.10", daily_budget=Decimal("0.001"))
    outs = await asyncio.gather(*(world.serve(DB_CONTEXT) for _ in range(60)))
    ads = [o["ad"] for o in outs if o["ad"]]
    assert len(ads) == 10
    assert {o["reason"] for o in outs if not o["ad"]} <= {"budget_exhausted", "no_eligible_campaigns"}
    assert await _held(world) == (10, 1_000)
    rs = await asyncio.gather(*(world.impression(ad) for ad in ads))
    assert [r.headers["x-aside-status"] for r in rs] == ["counted"] * 10
    assert await _billed(world, "impression") == 10  # every served ad was billed
    assert (await _campaign(world, cid)).spend_total_micros == 1_000
    assert await _held(world) == (0, 0)  # each reservation converted into spend


async def test_expired_reservations_release_budget(world) -> None:  # type: ignore[no-untyped-def]
    clock = _frozen_clock(world)
    await world.campaign("DB", DB_TEXT, bid="0.10", daily_budget=Decimal("0.001"))
    ads = [(await world.serve(DB_CONTEXT))["ad"] for _ in range(10)]
    assert all(ads)
    blocked = await world.serve(DB_CONTEXT)
    assert blocked["ad"] is None and blocked["reason"] == "budget_exhausted"
    clock["now"] += timedelta(seconds=world.rt.settings.reservation_ttl - 1)
    assert (await world.serve(DB_CONTEXT))["ad"] is None  # still held
    clock["now"] += timedelta(seconds=2)
    assert (await world.serve(DB_CONTEXT))["ad"] is not None  # released by expiry
    await purge_expired(world.rt, force=True)
    async with world.rt.sessionmaker() as s:
        assert (await s.execute(select(func.count()).select_from(BudgetReservation))).scalar_one() == 1


async def test_late_impression_is_dropped_when_budget_is_held_by_others(world) -> None:  # type: ignore[no-untyped-def]
    clock = _frozen_clock(world)
    cid = await world.campaign("DB", DB_TEXT, bid="0.10", daily_budget=Decimal("0.001"))
    stale = [(await world.serve(DB_CONTEXT))["ad"] for _ in range(10)]
    clock["now"] += timedelta(seconds=world.rt.settings.reservation_ttl + 1)
    fresh = [(await world.serve(DB_CONTEXT))["ad"] for _ in range(10)]
    assert all(stale) and all(fresh)
    # Impressions for the expired reservations arrive late: recorded, but not billed,
    # because the whole budget is now held for the fresh ads.
    late = [
        (
            await world.client.post(
                "/v1/impressions", json={"token": ad["impression_url"]}, headers=world.pub_headers
            )
        ).json()
        for ad in stale
    ]
    assert late == [{"status": "counted", "billed": False}] * 10
    on_time = [
        (
            await world.client.post(
                "/v1/impressions", json={"token": ad["impression_url"]}, headers=world.pub_headers
            )
        ).json()
        for ad in fresh
    ]
    assert on_time == [{"status": "counted", "billed": True}] * 10
    # Replays never bill twice.
    again = await world.client.post(
        "/v1/impressions", json={"token": stale[0]["impression_url"]}, headers=world.pub_headers
    )
    assert again.json() == {"status": "duplicate", "billed": False}
    c = await _campaign(world, cid)
    assert c.spend_total_micros == 1_000 and c.impressions_total == 20
    assert await _billed(world, "impression") == 10


async def test_late_impression_is_billed_when_budget_allows(world) -> None:  # type: ignore[no-untyped-def]
    clock = _frozen_clock(world)
    cid = await world.campaign("DB", DB_TEXT, bid="0.10", daily_budget=Decimal("0.001"))
    ad = (await world.serve(DB_CONTEXT))["ad"]
    clock["now"] += timedelta(seconds=world.rt.settings.reservation_ttl + 1)
    r = await world.impression(ad)
    assert r.headers["x-aside-status"] == "counted"
    assert await _billed(world, "impression") == 1
    assert (await _campaign(world, cid)).spend_total_micros == 100
    assert await _held(world) == (0, 0)


async def test_cpc_exposure_bounds_serving_and_free_clicks(tmp_path) -> None:  # type: ignore[no-untyped-def]
    # Expected CTR 25%: each CPC serve holds 0.25 x 10_000 = 2_500 micros. Budget 50_000:
    # a serve needs existing holds + one full click (10_000) to fit, so 17 serves at most.
    w, rt = await build_world(make_settings(tmp_path, cpc_expected_ctr=0.25))
    clock = _frozen_clock(w)
    try:
        cid = await w.campaign("DB", DB_TEXT, bid_type="cpc", bid="0.01", total_budget=Decimal("0.05"))
        outs = await asyncio.gather(*(w.serve(DB_CONTEXT) for _ in range(30)))
        ads = [o["ad"] for o in outs if o["ad"]]
        assert len(ads) == 17
        assert await _held(w) == (17, 42_500)
        # Three clicks convert their holds into full-price spend.
        for ad in ads[:3]:
            assert (await w.click(ad)).headers["x-aside-status"] == "counted"
        assert (await _campaign(w, cid)).spend_total_micros == 30_000
        assert await _held(w) == (14, 35_000)
        assert (await w.serve(DB_CONTEXT))["ad"] is None  # 30_000 + 35_000 + 10_000 > 50_000
        # Exposure expires, freeing room to serve again.
        clock["now"] += timedelta(seconds=rt.settings.cpc_exposure_ttl + 1)
        assert (await w.serve(DB_CONTEXT))["ad"] is not None
        # Clicks beyond the estimate are bounded: the budget is never exceeded.
        await asyncio.gather(*(w.click(ad) for ad in ads[3:]))
        c = await _campaign(w, cid)
        assert c.spend_total_micros == 50_000 and c.clicks_total == 17
        assert await _billed(w, "click") == 5
    finally:
        await w.client.aclose()
        await rt.close()


def test_hold_sizes(settings_factory) -> None:  # type: ignore[no-untyped-def]
    class _Rt:
        settings = settings_factory()

    rt = _Rt()
    assert hold_for(rt, "cpm", 2_000_000, 2_000) == (2_000, 120)  # type: ignore[arg-type]
    assert hold_for(rt, "cpc", 10_000, 10_000) == (500, 900)  # type: ignore[arg-type]
    assert hold_for(rt, "cpc", 1, 1) == (1, 900)  # type: ignore[arg-type]
    rt.settings = settings_factory(reservation_ttl=0, cpc_expected_ctr=0.0)
    assert hold_for(rt, "cpm", 2_000_000, 2_000) is None  # type: ignore[arg-type]
    assert hold_for(rt, "cpc", 10_000, 10_000) is None  # type: ignore[arg-type]


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
