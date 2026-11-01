"""Conversion postbacks: keyed by click id, idempotent, scoped to the advertiser."""

from __future__ import annotations

import asyncio
from datetime import timedelta

from sqlalchemy import func, select

from aside_ads.auth import create_api_key
from aside_ads.models import Advertiser, Campaign, Conversion, DailyStat

DB_TEXT = "slow postgres queries\ndatabase indexing"
DB_CONTEXT = "my postgres queries are slow, should I add an index?"


async def _clicked(world) -> tuple[str, str]:  # type: ignore[no-untyped-def]
    cid = await world.campaign("DB", DB_TEXT, bid_type="cpc", bid="1")
    out = await world.serve(DB_CONTEXT)
    r = await world.click(out["ad"])
    assert r.headers["x-aside-status"] == "counted"
    return cid, out["request_id"]


async def _adv_key(world, campaign_id: str) -> str:  # type: ignore[no-untyped-def]
    async with world.rt.sessionmaker() as s:
        c = await s.get(Campaign, campaign_id)
        _, key = await create_api_key(s, "advertiser", advertiser_id=c.advertiser_id)
        await s.commit()
    return key


async def _conversions(world) -> int:  # type: ignore[no-untyped-def]
    async with world.rt.sessionmaker() as s:
        return (await s.execute(select(func.count()).select_from(Conversion))).scalar_one()


async def test_duplicate_postback_counted_once(world) -> None:  # type: ignore[no-untyped-def]
    cid, click_id = await _clicked(world)
    h = {"Authorization": f"Bearer {await _adv_key(world, cid)}"}
    first = await world.client.post(
        "/v1/conversions", json={"click_id": click_id, "value": "49.00"}, headers=h
    )
    second = await world.client.post(
        "/v1/conversions", json={"click_id": click_id, "value": "49.00"}, headers=h
    )
    assert (first.status_code, first.json()["status"]) == (201, "recorded")
    assert (second.status_code, second.json()["status"]) == (200, "duplicate")
    assert await _conversions(world) == 1
    async with world.rt.sessionmaker() as s:
        stat = (await s.execute(select(DailyStat).where(DailyStat.campaign_id == cid))).scalar_one()
    assert stat.conversions == 1 and stat.conversion_value_micros == 49_000_000


async def test_concurrent_duplicate_postbacks_counted_once(world) -> None:  # type: ignore[no-untyped-def]
    cid, click_id = await _clicked(world)
    h = {"Authorization": f"Bearer {await _adv_key(world, cid)}", "Idempotency-Key": "order-1001"}
    rs = await asyncio.gather(
        *(world.client.post("/v1/conversions", json={"click_id": click_id}, headers=h) for _ in range(25))
    )
    statuses = sorted(r.json()["status"] for r in rs)
    assert statuses.count("recorded") == 1 and statuses.count("duplicate") == 24
    assert await _conversions(world) == 1


async def test_distinct_idempotency_keys_count_separately(world) -> None:  # type: ignore[no-untyped-def]
    cid, click_id = await _clicked(world)
    key = await _adv_key(world, cid)
    for idem in ("signup", "purchase", "purchase"):
        await world.client.post(
            "/v1/conversions",
            json={"click_id": click_id, "idempotency_key": idem},
            headers={"Authorization": f"Bearer {key}"},
        )
    assert await _conversions(world) == 2


async def test_pixel_style_get_postback(world) -> None:  # type: ignore[no-untyped-def]
    cid, click_id = await _clicked(world)
    key = await _adv_key(world, cid)
    r = await world.client.get(f"/v1/conversions?click_id={click_id}&key={key}&value=10")
    assert r.status_code == 201
    r = await world.client.get(f"/v1/conversions?click_id={click_id}&key={key}&value=10")
    assert r.status_code == 200 and r.json()["status"] == "duplicate"


async def test_postback_validation(world) -> None:  # type: ignore[no-untyped-def]
    cid, click_id = await _clicked(world)
    good = {"Authorization": f"Bearer {await _adv_key(world, cid)}"}
    async with world.rt.sessionmaker() as s:
        other = Advertiser(name="Someone else")
        s.add(other)
        await s.flush()
        _, other_key = await create_api_key(s, "advertiser", advertiser_id=other.id)
        await s.commit()
    r = await world.client.post("/v1/conversions", json={"click_id": "nope"}, headers=good)
    assert r.status_code == 404
    r = await world.client.post(
        "/v1/conversions", json={"click_id": click_id}, headers={"Authorization": f"Bearer {other_key}"}
    )
    assert r.status_code == 403
    r = await world.client.post("/v1/conversions", json={"click_id": click_id}, headers=world.pub_headers)
    assert r.status_code == 403
    r = await world.client.post("/v1/conversions", json={"click_id": click_id})
    assert r.status_code == 401
    real_now = world.rt.now()
    world.rt.clock = lambda: real_now + timedelta(days=31)
    r = await world.client.post("/v1/conversions", json={"click_id": click_id}, headers=good)
    assert r.status_code == 410
    assert await _conversions(world) == 0
