"""Signed tokens, click redirects, dedupe and invalid-traffic filtering."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from urllib.parse import parse_qs, urlparse

import pytest
from sqlalchemy import func, select

from aside_ads.auth import create_api_key
from aside_ads.models import Event
from aside_ads.services import add_placement, create_publisher
from aside_ads.tokens import Signer, TokenExpired, TokenInvalid
from tests.conftest import BROWSER_UA, build_world, make_settings

DB_TEXT = "slow postgres queries\ndatabase indexing"
DB_CONTEXT = "my postgres queries are slow, should I add an index?"


async def _count(world, kind: str) -> int:  # type: ignore[no-untyped-def]
    async with world.rt.sessionmaker() as s:
        return (await s.execute(select(func.count()).where(Event.kind == kind))).scalar_one()


def _token(url: str) -> str:
    return parse_qs(urlparse(url).query)["t"][0]


def test_signer_roundtrip_tamper_expiry_and_kind() -> None:
    s = Signer(b"k")
    tok = s.sign("imp", {"r": "abc", "pr": 5}, ttl_seconds=60, now=1000)
    assert s.verify(tok, "imp", now=1001)["r"] == "abc"
    with pytest.raises(TokenExpired):
        s.verify(tok, "imp", now=1061)
    with pytest.raises(TokenInvalid):
        s.verify(tok, "clk", now=1001)
    body, sig = tok.split(".")
    with pytest.raises(TokenInvalid):
        s.verify(body[:-2] + ("A" if body[-2] != "A" else "B") + body[-1] + "." + sig, "imp", now=1001)
    with pytest.raises(TokenInvalid):
        Signer(b"other").verify(tok, "imp", now=1001)


async def test_tampered_tokens_are_rejected(world) -> None:  # type: ignore[no-untyped-def]
    await world.campaign("DB", DB_TEXT)
    ad = (await world.serve(DB_CONTEXT))["ad"]
    imp, clk = _token(ad["impression_url"]), _token(ad["url"])
    # Swap the signature of the click token onto the impression payload.
    forged = imp.split(".")[0] + "." + clk.split(".")[1]
    r = await world.client.get(f"/t/i?t={forged}", headers={"User-Agent": BROWSER_UA})
    assert r.status_code == 400 and r.headers["x-aside-status"] == "invalid"
    r = await world.client.get(f"/t/c?t={clk[:-3]}xyz", headers={"User-Agent": BROWSER_UA})
    assert r.status_code == 400
    # An impression token cannot be replayed as a click.
    r = await world.client.get(f"/t/c?t={imp}", headers={"User-Agent": BROWSER_UA})
    assert r.status_code == 400
    assert await _count(world, "impression") == 0 and await _count(world, "click") == 0


async def test_expired_tokens_are_not_counted(world) -> None:  # type: ignore[no-untyped-def]
    await world.campaign("DB", DB_TEXT)
    ad = (await world.serve(DB_CONTEXT))["ad"]
    real_now = world.rt.now()
    world.rt.clock = lambda: real_now + timedelta(days=2)
    r = await world.impression(ad)
    assert r.status_code == 410 and r.headers["x-aside-status"] == "expired"
    r = await world.click(ad)
    assert r.status_code == 302  # still take the user to the page
    assert r.headers["x-aside-status"] == "expired"
    assert await _count(world, "impression") == 0 and await _count(world, "click") == 0


async def test_click_redirects_with_click_id_and_dedupes(world) -> None:  # type: ignore[no-untyped-def]
    await world.campaign("DB", DB_TEXT, url="https://dbtool.example/pricing?utm_source=chat")
    out = await world.serve(DB_CONTEXT)
    r = await world.click(out["ad"])
    assert r.status_code == 302
    loc = urlparse(r.headers["location"])
    assert loc.netloc == "dbtool.example" and loc.path == "/pricing"
    q = parse_qs(loc.query)
    assert q["utm_source"] == ["chat"] and q["aside_click_id"] == [out["request_id"]]
    assert r.headers["x-aside-status"] == "counted"
    again = await world.click(out["ad"])
    assert again.status_code == 302 and again.headers["x-aside-status"] == "duplicate"
    rs = await asyncio.gather(*(world.click(out["ad"]) for _ in range(10)))
    assert all(x.headers["x-aside-status"] == "duplicate" for x in rs)
    assert await _count(world, "click") == 1


async def test_bots_are_redirected_but_not_counted(world) -> None:  # type: ignore[no-untyped-def]
    await world.campaign("DB", DB_TEXT)
    ad = (await world.serve(DB_CONTEXT))["ad"]
    for ua in ["", "curl/8.4.0", "Mozilla/5.0 (compatible; Googlebot/2.1)", "Slackbot-LinkExpanding 1.0"]:
        r = await world.click(ad, ua=ua)
        assert r.status_code == 302 and r.headers["x-aside-status"] == "bot"
        r = await world.impression(ad, ua=ua)
        assert r.headers["x-aside-status"] == "bot"
    assert await _count(world, "click") == 0 and await _count(world, "impression") == 0


async def test_rate_limit_per_ip(tmp_path) -> None:  # type: ignore[no-untyped-def]
    w, rt = await build_world(make_settings(tmp_path, rate_limit_per_minute=3))
    try:
        await w.campaign("DB", DB_TEXT)
        ads = [(await w.serve(DB_CONTEXT))["ad"] for _ in range(5)]
        statuses = [(await w.impression(ad)).headers["x-aside-status"] for ad in ads]
        assert statuses == ["counted", "counted", "counted", "rate_limited", "rate_limited"]
    finally:
        await w.client.aclose()
        await rt.close()


async def test_server_side_impression_requires_owning_publisher(world) -> None:  # type: ignore[no-untyped-def]
    await world.campaign("DB", DB_TEXT)
    ad = (await world.serve(DB_CONTEXT))["ad"]
    async with world.rt.sessionmaker() as s:
        other = await create_publisher(s, "Other App")
        await add_placement(s, other.id, "default")
        _, other_key = await create_api_key(s, "publisher", publisher_id=other.id)
        await s.commit()
    r = await world.client.post(
        "/v1/impressions",
        json={"token": ad["impression_url"]},
        headers={"Authorization": f"Bearer {other_key}"},
    )
    assert r.status_code == 403
    r = await world.client.post(
        "/v1/impressions", json={"token": ad["impression_url"]}, headers=world.pub_headers
    )
    assert r.status_code == 200 and r.json()["status"] == "counted"
    r = await world.client.post(
        "/v1/impressions", json={"token": ad["impression_url"]}, headers=world.pub_headers
    )
    assert r.json()["status"] == "duplicate"
