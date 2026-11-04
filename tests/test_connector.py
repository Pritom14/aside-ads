"""Agent connector: delivery counts, reports, the MCP tools, and the OAuth approval flow."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import secrets
from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from sqlalchemy import select

from aside_ads import reports
from aside_ads.app import create_app
from aside_ads.auth import create_api_key
from aside_ads.models import Advertiser, ApiKey, AuditEntry, BudgetMiss, Campaign, ServeOutcome
from tests.conftest import World, build_world, make_settings

BASE = "http://localhost:8000"
DB_TEXT = "slow postgres queries\ndatabase indexing"
DB_CONTEXT = "my postgres queries are slow, should I add an index to the database?"
CALLBACK = "https://claude.ai/api/mcp/auth_callback"


class Agent:
    """An MCP client talking JSON-RPC over the streamable HTTP endpoint."""

    def __init__(self, client: httpx.AsyncClient, token: str | None) -> None:
        self.client = client
        self.token = token
        self._id = 0

    async def rpc(self, method: str, params: dict[str, Any] | None = None) -> httpx.Response:
        self._id += 1
        headers = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        body: dict[str, Any] = {"jsonrpc": "2.0", "id": self._id, "method": method}
        if params is not None:
            body["params"] = params
        return await self.client.post("/mcp", headers=headers, json=body)

    async def call(self, tool: str, **arguments: Any) -> dict[str, Any]:
        r = await self.rpc("tools/call", {"name": tool, "arguments": arguments})
        assert r.status_code == 200, r.text
        return r.json()["result"]

    async def ok(self, tool: str, **arguments: Any) -> Any:
        result = await self.call(tool, **arguments)
        assert not result["isError"], result
        if "structuredContent" in result and result["structuredContent"] is not None:
            sc = result["structuredContent"]
            return sc.get("result", sc) if set(sc) == {"result"} else sc
        return result["content"][0]["text"]

    async def error(self, tool: str, **arguments: Any) -> str:
        result = await self.call(tool, **arguments)
        assert result["isError"], result
        return result["content"][0]["text"]


@pytest.fixture
async def aw(tmp_path: Any) -> AsyncIterator[tuple[World, httpx.AsyncClient]]:
    w, rt = await build_world(make_settings(tmp_path, public_url=BASE))
    app = create_app(runtime=rt)
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE)
    ready, stop = asyncio.Event(), asyncio.Event()

    async def run_connector() -> None:  # enter and exit the lifespan in one task
        async with app.state.connector.lifespan():
            ready.set()
            await stop.wait()

    task = asyncio.create_task(run_connector())
    await ready.wait()
    try:
        yield w, client
    finally:
        stop.set()
        await task
        await client.aclose()
        await w.client.aclose()
        await rt.close()


async def _advertiser_key(w: World, campaign_id: str) -> str:
    async with w.rt.sessionmaker() as s:
        c = await s.get(Campaign, campaign_id)
        _, key = await create_api_key(s, "advertiser", "adv", advertiser_id=c.advertiser_id)
        await s.commit()
    return key


async def _outcomes(w: World) -> dict[str, int]:
    async with w.rt.sessionmaker() as s:
        rows = (await s.execute(select(ServeOutcome))).scalars().all()
    return {r.outcome: r.requests for r in rows}


# -- delivery counts --------------------------------------------------------------------------------


async def test_serve_outcomes_and_budget_misses_are_counted(world: World) -> None:
    cid = await world.campaign("DB", DB_TEXT, bid="0.10", daily_budget=Decimal("0.0003"))
    served = [await world.serve(DB_CONTEXT) for _ in range(5)]
    assert [bool(x["ad"]) for x in served] == [True, True, True, False, False]
    assert served[-1]["reason"] == "budget_exhausted"
    await world.serve("what is a good name for a cat?")  # nothing relevant
    await world.serve(DB_CONTEXT, sensitive=True)
    assert await _outcomes(world) == {
        "filled": 3,
        "budget_exhausted": 2,
        "below_relevance_threshold": 1,
        "sensitive_context": 1,
    }
    async with world.rt.sessionmaker() as s:
        miss = await s.get(BudgetMiss, (cid, world.rt.day()))
    assert miss is not None and miss.requests == 2


async def test_budget_miss_counted_when_another_campaign_fills(world: World) -> None:
    capped = await world.campaign("DB capped", DB_TEXT, bid="0.20", daily_budget=Decimal("0.0002"))
    await world.campaign("DB open", DB_TEXT, bid="0.10")
    first, second = await world.serve(DB_CONTEXT), await world.serve(DB_CONTEXT)
    assert first["ad"]["campaign_id"] == capped
    assert second["ad"] and second["ad"]["campaign_id"] != capped
    async with world.rt.sessionmaker() as s:
        miss = await s.get(BudgetMiss, (capped, world.rt.day()))
    assert miss is not None and miss.requests == 1


# -- reports ----------------------------------------------------------------------------------------


async def test_overview_says_when_the_budget_ran_out(world: World) -> None:
    rt = world.rt
    noon = datetime.now(timezone.utc).replace(hour=12, minute=0, second=0, microsecond=0)
    rt.clock = lambda: noon
    cid = await world.campaign("DB", DB_TEXT, bid="0.10", daily_budget=Decimal("0.0002"))
    for minute in range(4):
        rt.clock = lambda m=minute: noon + timedelta(minutes=10 * m)
        out = await world.serve(DB_CONTEXT)
        if out["ad"]:
            assert (await world.impression(out["ad"])).status_code in (200, 204)
    rep = await reports.overview(rt, noon.date().isoformat())
    row = next(r for r in rep["campaigns"] if r["id"] == cid)
    assert row["impressions"] == 2
    assert row["budget_ran_out_at"] == "12:10"
    assert row["missed_for_budget"] == 2
    assert any("ran out at 12:10" in a for a in rep["needs_attention"])
    assert rep["placements"][0]["fill_rate_pct"] == 50.0


async def test_advertiser_report_is_markdown_with_counts_only(world: World) -> None:
    cid = await world.campaign("DB", DB_TEXT, bid="0.10", daily_budget=Decimal("0.0001"))
    ad = (await world.serve(DB_CONTEXT))["ad"]
    await world.impression(ad)
    await world.serve(DB_CONTEXT)
    async with world.rt.sessionmaker() as s:
        adv_id = (await s.get(Campaign, cid)).advertiser_id
    md = await reports.advertiser_report(world.rt, adv_id, 7)
    assert md.startswith("## DB Inc: ads from")
    assert "| DB | 1 | 0 | 0.0% | 0 |" in md
    assert "matched 1 more conversations" in md
    assert DB_CONTEXT not in md


# -- MCP tools --------------------------------------------------------------------------------------


async def test_mcp_needs_an_admin_or_advertiser_key(aw: tuple[World, httpx.AsyncClient]) -> None:
    w, client = aw
    r = await Agent(client, None).rpc("tools/list")
    assert r.status_code == 401
    assert "oauth-protected-resource/mcp" in r.headers["www-authenticate"]
    assert (await Agent(client, w.pub_key).rpc("tools/list")).status_code == 401
    assert (await Agent(client, "aside_admin_wrong").rpc("tools/list")).status_code == 401
    r = await Agent(client, w.admin_key).rpc("tools/list")
    names = {t["name"] for t in r.json()["result"]["tools"]}
    assert names == {
        "ads_overview",
        "campaign_report",
        "delivery_report",
        "advertiser_report",
        "recent_changes",
        "update_campaign_settings",
        "sponsor_requests",
        "review_sponsor_request",
    }
    tools = {t["name"]: t for t in r.json()["result"]["tools"]}
    assert tools["ads_overview"]["annotations"]["readOnlyHint"] is True
    assert tools["sponsor_requests"]["annotations"]["readOnlyHint"] is True
    assert tools["update_campaign_settings"]["annotations"]["destructiveHint"] is True
    assert tools["review_sponsor_request"]["annotations"]["destructiveHint"] is True


async def test_admin_overview_and_delivery_through_mcp(aw: tuple[World, httpx.AsyncClient]) -> None:
    w, client = aw
    await w.campaign("DB", DB_TEXT, bid="0.10", daily_budget=Decimal("0.0001"))
    await w.serve(DB_CONTEXT)
    await w.serve(DB_CONTEXT)
    agent = Agent(client, w.admin_key)
    ov = await agent.ok("ads_overview", day="today")
    assert ov["campaigns"][0]["missed_for_budget"] == 1
    dr = await agent.ok("delivery_report", days=1)
    reasons = {x["reason"]: x for x in dr["placements"][0]["no_ad"]}
    assert reasons["budget_exhausted"]["requests"] == 1 and "budget" in reasons["budget_exhausted"]["meaning"]
    rep = await agent.ok("campaign_report", campaign="db")
    assert rep["campaign"] == "DB" and rep["total"]["missed_for_budget"] == 1
    assert "matches more than one" not in (await agent.error("campaign_report", campaign="nothing"))


async def test_advertiser_key_sees_only_its_campaigns_and_cannot_change(
    aw: tuple[World, httpx.AsyncClient],
) -> None:
    w, client = aw
    mine = await w.campaign("Pans", "nonstick pans\ncast iron")
    await w.campaign("Knives", "chef knives")
    agent = Agent(client, await _advertiser_key(w, mine))
    ov = await agent.ok("ads_overview")
    assert [c["campaign"] for c in ov["campaigns"]] == ["Pans"] and ov["placements"] is None
    assert "No campaign matches" in await agent.error("campaign_report", campaign="Knives")
    assert "admin key" in await agent.error("delivery_report")
    assert "admin key" in await agent.error("update_campaign_settings", campaign="Pans", status="paused")
    md = await agent.ok("advertiser_report")
    assert "Pans" in md and "Knives" not in md


async def test_update_campaign_changes_budget_and_logs_it(aw: tuple[World, httpx.AsyncClient]) -> None:
    w, client = aw
    cid = await w.campaign("Pans", "nonstick pans", daily_budget=Decimal("10"))
    agent = Agent(client, w.admin_key)
    out = await agent.ok("update_campaign_settings", campaign="pans", daily_budget=25, status="paused")
    assert out["changed"] == {
        "daily_budget": {"from": 10.0, "to": 25.0},
        "status": {"from": "active", "to": "paused"},
    }
    async with w.rt.sessionmaker() as s:
        c = await s.get(Campaign, cid)
        entry = (await s.execute(select(AuditEntry))).scalar_one()
    assert c.daily_budget_micros == 25_000_000 and c.status == "paused"
    assert entry.via == "agent" and entry.target_name == "Pans" and entry.after["daily_budget"] == 25.0
    changes = await agent.ok("recent_changes")
    assert changes[0]["after"] == {"status": "paused", "daily_budget": 25.0}
    assert "greater than 0" in await agent.error("update_campaign_settings", campaign="Pans", bid=0)
    assert "Nothing to change" in await agent.error("update_campaign_settings", campaign="Pans")


async def test_revoked_key_stops_working(aw: tuple[World, httpx.AsyncClient]) -> None:
    w, client = aw
    async with w.rt.sessionmaker() as s:
        key, plaintext = await create_api_key(s, "admin", "agent")
        await s.commit()
    agent = Agent(client, plaintext)
    await agent.ok("ads_overview")
    async with w.rt.sessionmaker() as s:
        (await s.get(ApiKey, key.id)).revoked_at = datetime.now(timezone.utc)
        await s.commit()
    assert (await agent.rpc("tools/list")).status_code == 401


# -- OAuth ------------------------------------------------------------------------------------------


async def _register(client: httpx.AsyncClient) -> str:
    r = await client.post(
        "/register",
        json={
            "redirect_uris": [CALLBACK],
            "client_name": "Claude",
            "token_endpoint_auth_method": "none",
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
        },
    )
    assert r.status_code == 201, r.text
    return r.json()["client_id"]


async def _authorize(client: httpx.AsyncClient, client_id: str, verifier: str, scope: str) -> str:
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    r = await client.get(
        "/authorize",
        params={
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": CALLBACK,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": "st8",
            "scope": scope,
            "resource": f"{BASE}/mcp",
        },
    )
    assert r.status_code == 302, r.text
    loc = urlparse(r.headers["location"])
    assert loc.path == "/connect"
    return parse_qs(loc.query)["request"][0]


async def _token(client: httpx.AsyncClient, **form: str) -> dict[str, Any]:
    r = await client.post("/token", data=form)
    assert r.status_code == 200, r.text
    return r.json()


async def test_oauth_connect_with_an_admin_key(aw: tuple[World, httpx.AsyncClient]) -> None:
    w, client = aw
    await w.campaign("Pans", "nonstick pans")
    client_id = await _register(client)
    verifier = secrets.token_urlsafe(48)
    request_id = await _authorize(client, client_id, verifier, "aside.read aside.write")

    page = await client.get("/connect", params={"request": request_id})
    assert page.status_code == 200 and "Connect Claude to Aside" in page.text
    bad = await client.post("/connect", data={"request": request_id, "key": w.pub_key, "action": "approve"})
    assert bad.status_code == 401 and "not a valid admin or advertiser key" in bad.text

    ok = await client.post("/connect", data={"request": request_id, "key": w.admin_key, "action": "approve"})
    assert ok.status_code == 303
    back = urlparse(ok.headers["location"])
    assert f"{back.scheme}://{back.netloc}{back.path}" == CALLBACK
    q = parse_qs(back.query)
    assert q["state"] == ["st8"]
    # The pending request is used up.
    assert (await client.get("/connect", params={"request": request_id})).status_code == 400

    tok = await _token(
        client,
        grant_type="authorization_code",
        code=q["code"][0],
        redirect_uri=CALLBACK,
        client_id=client_id,
        code_verifier=verifier,
        resource=f"{BASE}/mcp",
    )
    assert set(tok["scope"].split()) == {"aside.read", "aside.write"}
    agent = Agent(client, tok["access_token"])
    assert (await agent.ok("ads_overview"))["campaigns"][0]["campaign"] == "Pans"
    await agent.ok("update_campaign_settings", campaign="Pans", status="paused")

    # Codes work once; refresh tokens rotate.
    again = await client.post(
        "/token",
        data={
            "grant_type": "authorization_code",
            "code": q["code"][0],
            "redirect_uri": CALLBACK,
            "client_id": client_id,
            "code_verifier": verifier,
        },
    )
    assert again.status_code == 400
    fresh = await _token(
        client, grant_type="refresh_token", refresh_token=tok["refresh_token"], client_id=client_id
    )
    assert fresh["access_token"] != tok["access_token"]
    reuse = await client.post(
        "/token",
        data={"grant_type": "refresh_token", "refresh_token": tok["refresh_token"], "client_id": client_id},
    )
    assert reuse.status_code == 400

    # Revoking the admin key cuts the connection off.
    async with w.rt.sessionmaker() as s:
        key = (await s.execute(select(ApiKey).where(ApiKey.role == "admin"))).scalars().first()
        key.revoked_at = datetime.now(timezone.utc)
        await s.commit()
    assert (await Agent(client, fresh["access_token"]).rpc("tools/list")).status_code == 401


async def test_oauth_advertiser_key_gets_read_only(aw: tuple[World, httpx.AsyncClient]) -> None:
    w, client = aw
    cid = await w.campaign("Pans", "nonstick pans")
    adv_key = await _advertiser_key(w, cid)
    client_id = await _register(client)
    verifier = secrets.token_urlsafe(48)
    request_id = await _authorize(client, client_id, verifier, "aside.read aside.write")
    ok = await client.post("/connect", data={"request": request_id, "key": adv_key, "action": "approve"})
    code = parse_qs(urlparse(ok.headers["location"]).query)["code"][0]
    tok = await _token(
        client,
        grant_type="authorization_code",
        code=code,
        redirect_uri=CALLBACK,
        client_id=client_id,
        code_verifier=verifier,
    )
    assert tok["scope"] == "aside.read"
    agent = Agent(client, tok["access_token"])
    assert "admin key" in await agent.error("update_campaign_settings", campaign="Pans", status="paused")


async def test_oauth_cancel_sends_access_denied(aw: tuple[World, httpx.AsyncClient]) -> None:
    _, client = aw
    client_id = await _register(client)
    request_id = await _authorize(client, client_id, secrets.token_urlsafe(48), "aside.read")
    r = await client.post("/connect", data={"request": request_id, "action": "deny"})
    q = parse_qs(urlparse(r.headers["location"]).query)
    assert r.status_code == 303 and q["error"] == ["access_denied"] and q["state"] == ["st8"]


async def test_tokens_are_stored_hashed(aw: tuple[World, httpx.AsyncClient]) -> None:
    w, client = aw
    client_id = await _register(client)
    verifier = secrets.token_urlsafe(48)
    request_id = await _authorize(client, client_id, verifier, "aside.read")
    ok = await client.post("/connect", data={"request": request_id, "key": w.admin_key, "action": "approve"})
    code = parse_qs(urlparse(ok.headers["location"]).query)["code"][0]
    tok = await _token(
        client,
        grant_type="authorization_code",
        code=code,
        redirect_uri=CALLBACK,
        client_id=client_id,
        code_verifier=verifier,
    )
    from aside_ads.models import OAuthGrant

    async with w.rt.sessionmaker() as s:
        dump = json.dumps([g.__dict__ for g in (await s.execute(select(OAuthGrant))).scalars()], default=str)
    for secret in (tok["access_token"], tok["refresh_token"], code, w.admin_key):
        assert secret not in dump


async def test_connector_can_be_turned_off(tmp_path: Any) -> None:
    w, rt = await build_world(make_settings(tmp_path, connector=False))
    try:
        app = create_app(runtime=rt)
        assert app.state.connector is None
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE) as c:
            assert (await c.post("/mcp", json={})).status_code == 404
    finally:
        await w.client.aclose()
        await rt.close()


async def test_advertiser_names_resolve(aw: tuple[World, httpx.AsyncClient]) -> None:
    w, client = aw
    await w.campaign("Pans", "nonstick pans")
    async with w.rt.sessionmaker() as s:
        assert (await s.execute(select(Advertiser))).scalars().first().name == "Pans Inc"
    md = await Agent(client, w.admin_key).ok("advertiser_report", advertiser="pans inc")
    assert md.startswith("## Pans Inc")
