from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import httpx
import pytest

from aside_ads.app import create_app
from aside_ads.auth import create_api_key
from aside_ads.config import Settings
from aside_ads.matching.bm25 import BM25Matcher
from aside_ads.models import Advertiser
from aside_ads.runtime import Runtime
from aside_ads.schemas import CampaignIn, CreativeIn
from aside_ads.services import add_creative, add_placement, create_campaign, create_publisher

BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0 Safari/537.36"
)


def make_settings(tmp_path: Any, **overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "data_dir": tmp_path,
        "database_url": f"sqlite+aiosqlite:///{tmp_path}/test.db",
        "redis_url": None,
        "secret_key": "test-secret-key",
        "matcher": "bm25",
        "rate_limit_per_minute": 1000,
        "floor_cpm": Decimal("0.10"),
        "pacing_buffer": 1.0,  # tests opt in to pacing explicitly
        "sensitive_terms_file": None,
    }
    base.update(overrides)
    return Settings(_env_file=None, **base)


@dataclass
class World:
    rt: Runtime
    client: httpx.AsyncClient
    publisher_id: str
    pub_key: str
    admin_key: str

    @property
    def pub_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.pub_key}"}

    @property
    def admin_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.admin_key}"}

    async def campaign(
        self,
        name: str,
        targeting: str,
        *,
        bid_type: str = "cpm",
        bid: str = "5",
        headline: str | None = None,
        url: str = "https://advertiser.example/landing?ref=chat",
        **fields: Any,
    ) -> str:
        async with self.rt.sessionmaker() as s:
            adv = Advertiser(name=f"{name} Inc")
            s.add(adv)
            await s.flush()
            c = await create_campaign(
                s,
                CampaignIn(
                    advertiser_id=adv.id,
                    name=name,
                    bid_type=bid_type,
                    bid=Decimal(bid),
                    targeting_text=targeting,
                    **fields,
                ),
            )
            await add_creative(
                s, c.id, CreativeIn(headline=headline or f"{name} headline", body=f"{name} body", url=url)
            )
            await s.commit()
            return c.id

    async def serve(self, context: Any, **extra: Any) -> dict[str, Any]:
        r = await self.client.post(
            "/v1/serve", json={"context": context, "placement": "default", **extra}, headers=self.pub_headers
        )
        assert r.status_code == 200, r.text
        return r.json()

    async def impression(self, ad: dict[str, Any], ua: str = BROWSER_UA) -> httpx.Response:
        return await self.client.get(_path(ad["impression_url"]), headers={"User-Agent": ua})

    async def click(self, ad: dict[str, Any], ua: str = BROWSER_UA) -> httpx.Response:
        return await self.client.get(_path(ad["url"]), headers={"User-Agent": ua})


def _path(url: str) -> str:
    return url.split("http://test", 1)[-1]


async def build_world(settings: Settings, matcher: Any = None) -> tuple[World, Any]:
    rt = await Runtime.create(settings, matcher=matcher or BM25Matcher())
    async with rt.sessionmaker() as s:
        pub = await create_publisher(s, "Test App")
        await add_placement(s, pub.id, "default")
        await add_placement(s, pub.id, "sidebar")
        _, pub_key = await create_api_key(s, "publisher", "t", publisher_id=pub.id)
        _, admin_key = await create_api_key(s, "admin", "t")
        await s.commit()
        publisher_id = pub.id
    app = create_app(runtime=rt)
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")
    return World(rt, client, publisher_id, pub_key, admin_key), rt


@pytest.fixture
async def world(tmp_path: Any) -> AsyncIterator[World]:
    w, rt = await build_world(make_settings(tmp_path))
    try:
        yield w
    finally:
        await w.client.aclose()
        await rt.close()


@pytest.fixture
def settings_factory(tmp_path: Any):  # type: ignore[no-untyped-def]
    def make(**overrides: Any) -> Settings:
        return make_settings(tmp_path, **overrides)

    return make
