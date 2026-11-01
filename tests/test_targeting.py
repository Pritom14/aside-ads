"""Hard targeting filters: keywords, categories, geo, schedule, placements."""

from __future__ import annotations

from datetime import datetime, timezone

from aside_ads.schemas import Schedule

DB_TEXT = "slow postgres queries\ndatabase indexing"
DB_CONTEXT = "my postgres queries are slow, should I add an index?"


async def test_keyword_include_and_exclude(world) -> None:  # type: ignore[no-untyped-def]
    await world.campaign("Inc", DB_TEXT, keywords_include=["index"], keywords_exclude=["mysql"])
    assert (await world.serve(DB_CONTEXT))["ad"] is not None
    assert (await world.serve("my postgres queries are slow"))["ad"] is None  # no "index"
    assert (await world.serve(DB_CONTEXT + " we are migrating from mysql"))["ad"] is None


async def test_category_include_and_exclude(world) -> None:  # type: ignore[no-untyped-def]
    dev = await world.campaign("Dev only", DB_TEXT, categories_include=["software_development"], bid="1")
    safe = await world.campaign("No politics", DB_TEXT, categories_exclude=["news_politics"], bid="9")
    assert (await world.serve(DB_CONTEXT))["ad"]["campaign_id"] == safe
    out = await world.serve(DB_CONTEXT + " for the election results site")
    assert out["ad"]["campaign_id"] == dev  # the brand-safety exclusion removed the higher bidder
    assert (await world.serve("slow postgres queries and database indexing for my election"))["ad"]


async def test_category_hint_from_publisher(world) -> None:  # type: ignore[no-untyped-def]
    cid = await world.campaign("Finance only", "bookkeeping and receipts", categories_include=["finance"])
    assert (await world.serve("help me sort my bookkeeping and receipts"))["ad"] is None
    out = await world.serve("help me sort my bookkeeping and receipts", categories=["finance"])
    assert out["ad"]["campaign_id"] == cid


async def test_geo_country_region_and_radius(world) -> None:  # type: ignore[no-untyped-def]
    await world.campaign("India", DB_TEXT, countries=["in"], regions=["KA"])
    assert (await world.serve(DB_CONTEXT))["ad"] is None  # no geo sent
    assert (await world.serve(DB_CONTEXT, geo={"country": "US"}))["ad"] is None
    assert (await world.serve(DB_CONTEXT, geo={"country": "IN", "region": "MH"}))["ad"] is None
    assert (await world.serve(DB_CONTEXT, geo={"country": "IN", "region": "ka"}))["ad"] is not None


async def test_geo_radius(world) -> None:  # type: ignore[no-untyped-def]
    await world.campaign("Bengaluru", DB_TEXT, geo_lat=12.9716, geo_lng=77.5946, geo_radius_km=50)
    near = {"lat": 13.0, "lng": 77.6}
    far = {"lat": 19.07, "lng": 72.87}  # Mumbai
    assert (await world.serve(DB_CONTEXT, geo=near))["ad"] is not None
    assert (await world.serve(DB_CONTEXT, geo=far))["ad"] is None


async def test_schedule_window(world) -> None:  # type: ignore[no-untyped-def]
    clock = {"now": datetime(2026, 3, 2, 10, 0, tzinfo=timezone.utc)}  # Monday 10:00 UTC
    world.rt.clock = lambda: clock["now"]
    await world.campaign(
        "Office hours",
        DB_TEXT,
        schedule=Schedule(days=[0, 1, 2, 3, 4], start_hour=9, end_hour=17, timezone="UTC"),
        start_at=datetime(2026, 3, 1, tzinfo=timezone.utc),
        end_at=datetime(2026, 4, 1, tzinfo=timezone.utc),
    )
    assert (await world.serve(DB_CONTEXT))["ad"] is not None
    clock["now"] = clock["now"].replace(hour=20)
    assert (await world.serve(DB_CONTEXT))["ad"] is None
    clock["now"] = datetime(2026, 3, 7, 10, 0, tzinfo=timezone.utc)  # Saturday
    assert (await world.serve(DB_CONTEXT))["ad"] is None
    clock["now"] = datetime(2026, 4, 6, 10, 0, tzinfo=timezone.utc)  # Monday, but after end_at
    assert (await world.serve(DB_CONTEXT))["ad"] is None


async def test_placement_restriction(world) -> None:  # type: ignore[no-untyped-def]
    await world.campaign("Sidebar only", DB_TEXT, placements=["sidebar"])
    assert (await world.serve(DB_CONTEXT))["ad"] is None
    assert (await world.serve(DB_CONTEXT, placement="sidebar"))["ad"] is not None
