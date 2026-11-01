"""Privacy guarantee: conversation text (and anything derived from it) is never written to storage."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import select

from aside_ads.models import Base, Campaign

MARKER = "zqxjvorbitalpelican"  # unique, so any occurrence in storage must come from a conversation


async def test_conversation_text_is_never_stored(world, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    cid = await world.campaign("DB", "slow postgres queries\ndatabase indexing", bid_type="cpc", bid="1")
    contexts = [
        f"my postgres queries are slow {MARKER} should I add an index?",
        [
            {"role": "user", "content": f"hello {MARKER} I run postgres"},
            {"role": "assistant", "content": "Sure, what is slow?"},
            {"role": "user", "content": f"slow queries and indexing {MARKER}"},
        ],
        f"I want to kill myself {MARKER}",  # sensitive path
        f"tell me a poem about {MARKER}",  # no-match path
    ]
    raw_user_id = f"user-{MARKER}@example.com"
    served = []
    for ctx in contexts:
        out = await world.serve(ctx, user_id=raw_user_id, geo={"country": "IN", "region": "KA"})
        if out["ad"]:
            served.append(out)
    assert served, "expected at least one ad so that events are written"
    for out in served:
        await world.impression(out["ad"])
        await world.click(out["ad"])
    await world.client.post(
        "/v1/conversions", json={"click_id": served[0]["request_id"]}, headers=world.admin_headers
    )

    # 1. Every text column of every table.
    async with world.rt.sessionmaker() as s:
        for table in Base.metadata.sorted_tables:
            for row in (await s.execute(select(*table.columns))).mappings():
                for col in table.columns:
                    value = row[col.name]
                    assert MARKER not in str(value), f"conversation text leaked into {table.name}.{col.name}"
        # Events keep a keyed hash, never the raw user id.
        c = (await s.execute(select(Campaign).where(Campaign.id == cid))).scalar_one()
        assert c.clicks_total >= 1

    # 2. The raw database files, including the WAL.
    await world.rt.engine.dispose()
    for f in tmp_path.glob("test.db*"):
        data = f.read_bytes()
        assert MARKER.encode() not in data, f"marker found in {f.name}"
        assert b"user-" + MARKER.encode() not in data
        assert b"add an index" not in data
