"""API key auth, the admin JSON API and the server-rendered dashboard."""

from __future__ import annotations

from aside_ads.auth import create_api_key
from aside_ads.models import ApiKey, utcnow

DB_CONTEXT = "my postgres queries are slow, should I add an index?"


async def test_serve_requires_publisher_key(world) -> None:  # type: ignore[no-untyped-def]
    body = {"context": "hi", "placement": "default"}
    assert (await world.client.post("/v1/serve", json=body)).status_code == 401
    r = await world.client.post("/v1/serve", json=body, headers={"Authorization": "Bearer aside_pub_wrong"})
    assert r.status_code == 401
    assert (await world.client.post("/v1/serve", json=body, headers=world.admin_headers)).status_code == 403
    r = await world.client.post("/v1/serve", json=body, headers={"X-API-Key": world.pub_key})
    assert r.status_code == 200
    r = await world.client.post("/v1/serve", json={**body, "placement": "nope"}, headers=world.pub_headers)
    assert r.status_code == 404


async def test_revoked_key_is_rejected(world) -> None:  # type: ignore[no-untyped-def]
    async with world.rt.sessionmaker() as s:
        key, plaintext = await create_api_key(s, "publisher", publisher_id=world.publisher_id)
        await s.commit()
    h = {"Authorization": f"Bearer {plaintext}"}
    assert (await world.client.post("/v1/serve", json={"context": "x"}, headers=h)).status_code == 200
    async with world.rt.sessionmaker() as s:
        (await s.get(ApiKey, key.id)).revoked_at = utcnow()
        await s.commit()
    assert (await world.client.post("/v1/serve", json={"context": "x"}, headers=h)).status_code == 401


async def test_admin_api_requires_admin(world) -> None:  # type: ignore[no-untyped-def]
    assert (await world.client.get("/v1/admin/campaigns")).status_code == 401
    assert (await world.client.get("/v1/admin/campaigns", headers=world.pub_headers)).status_code == 403
    assert (await world.client.get("/v1/admin/campaigns", headers=world.admin_headers)).status_code == 200


async def test_admin_api_campaign_lifecycle(world) -> None:  # type: ignore[no-untyped-def]
    h = world.admin_headers
    adv = (
        await world.client.post(
            "/v1/admin/advertisers", json={"name": "Acme", "website": "https://acme.example"}, headers=h
        )
    ).json()
    r = await world.client.post(
        "/v1/admin/campaigns",
        json={
            "advertiser_id": adv["id"],
            "name": "Acme DB",
            "bid_type": "cpc",
            "bid": "0.75",
            "daily_budget": "20",
            "targeting_text": "slow postgres queries\ndatabase indexing",
            "keywords_exclude": "mysql, oracle",
            "schedule": {"days": [0, 1, 2, 3, 4, 5, 6]},
        },
        headers=h,
    )
    assert r.status_code == 201, r.text
    camp = r.json()
    assert (
        camp["bid"] == 0.75
        and camp["daily_budget"] == 20.0
        and camp["keywords_exclude"] == ["mysql", "oracle"]
    )
    r = await world.client.post(
        f"/v1/admin/campaigns/{camp['id']}/creatives",
        json={"headline": "Fix slow queries", "body": "Index advisor", "url": "https://acme.example/db"},
        headers=h,
    )
    assert r.status_code == 201
    out = await world.serve(DB_CONTEXT)
    assert out["ad"]["headline"] == "Fix slow queries"
    r = await world.client.patch(
        f"/v1/admin/campaigns/{camp['id']}", json={"status": "paused", "clear_daily_budget": True}, headers=h
    )
    assert r.json()["status"] == "paused" and r.json()["daily_budget"] is None
    assert (await world.serve(DB_CONTEXT))["ad"] is None
    bad = await world.client.post(
        f"/v1/admin/campaigns/{camp['id']}/creatives",
        json={"headline": "x", "url": "javascript:alert(1)"},
        headers=h,
    )
    assert bad.status_code == 422
    stats = await world.client.get("/v1/admin/stats", headers=h)
    assert stats.status_code == 200


async def test_admin_api_keys_and_publishers(world) -> None:  # type: ignore[no-untyped-def]
    h = world.admin_headers
    pub = (await world.client.post("/v1/admin/publishers", json={"name": "Second App"}, headers=h)).json()
    assert [p["key"] for p in pub["placements"]] == ["default"]
    r = await world.client.post(
        f"/v1/admin/publishers/{pub['id']}/placements", json={"key": "sidebar", "floor_cpm": "2"}, headers=h
    )
    assert {p["key"] for p in r.json()["placements"]} == {"default", "sidebar"}
    r = await world.client.post(
        "/v1/admin/keys", json={"role": "publisher", "publisher_id": pub["id"]}, headers=h
    )
    assert r.status_code == 201 and r.json()["key"].startswith("aside_pub_")
    listed = (await world.client.get("/v1/admin/keys", headers=h)).json()
    assert all(k["key"] is None for k in listed)  # plaintext never returned again


async def test_dashboard_login_and_pages(world) -> None:  # type: ignore[no-untyped-def]
    cid = await world.campaign("Dashboard DB", "slow postgres queries\ndatabase indexing")
    out = await world.serve(DB_CONTEXT)
    await world.impression(out["ad"])
    r = await world.client.get("/dashboard")
    assert r.status_code == 303 and r.headers["location"] == "/dashboard/login"
    r = await world.client.post("/dashboard/login", data={"key": world.pub_key})
    assert r.status_code == 401
    r = await world.client.post("/dashboard/login", data={"key": world.admin_key})
    assert r.status_code == 303
    world.client.cookies.set("aside_session", r.cookies["aside_session"], path="/dashboard")
    for path in (
        "/dashboard",
        "/dashboard?days=30",
        f"/dashboard/campaigns/{cid}",
        "/dashboard/advertisers",
        "/dashboard/publishers",
        "/dashboard/campaigns/new",
    ):
        page = await world.client.get(path)
        assert page.status_code == 200, path
    assert "Dashboard DB" in (await world.client.get("/dashboard")).text


async def test_dashboard_forms_create_and_edit_campaign(world) -> None:  # type: ignore[no-untyped-def]
    r = await world.client.post("/dashboard/login", data={"key": world.admin_key})
    world.client.cookies.set("aside_session", r.cookies["aside_session"], path="/dashboard")
    r = await world.client.post(
        "/dashboard/advertisers", data={"name": "Formy", "website": "https://formy.example"}
    )
    assert r.status_code == 303
    adv_id = (await world.client.get("/v1/admin/advertisers", headers=world.admin_headers)).json()[0]["id"]
    form = {
        "advertiser_id": adv_id,
        "name": "Formy DB",
        "status": "active",
        "bid_type": "cpm",
        "bid": "4",
        "daily_budget": "10",
        "total_budget": "",
        "pacing": "even",
        "freq_cap": "",
        "freq_cap_window_hours": "24",
        "targeting_text": "slow postgres queries\ndatabase indexing",
        "keywords_include": "",
        "countries": "",
        "cr_headline": "Formy finds slow queries",
        "cr_body": "Try it",
        "cr_cta": "Try",
        "cr_url": "https://formy.example",
        "sched_days": ["0", "1", "2", "3", "4", "5", "6"],
    }
    r = await world.client.post("/dashboard/campaigns/new", data=form)
    assert r.status_code == 303, r.text
    campaign_url = r.headers["location"]
    assert (await world.serve(DB_CONTEXT))["ad"]["headline"] == "Formy finds slow queries"
    r = await world.client.post(campaign_url, data={**form, "bid": "-1"})
    assert r.status_code == 422
    r = await world.client.post(campaign_url, data={**form, "status": "paused"})
    assert r.status_code == 303
    assert (await world.serve(DB_CONTEXT))["ad"] is None
    r = await world.client.post("/dashboard/keys", data={"role": "admin", "name": "ops"})
    assert "aside_admin_" in r.text
