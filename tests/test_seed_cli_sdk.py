"""seed-demo, the CLI, and the Python SDK against a real HTTP server."""

from __future__ import annotations

import asyncio
import socket
import sys
import threading
import time
from pathlib import Path

import pytest
import uvicorn
from sqlalchemy import func, select

from aside_ads.app import create_app
from aside_ads.cli import main
from aside_ads.models import Campaign
from aside_ads.seed import DEMO_ADS, seed_demo
from tests.conftest import build_world, make_settings

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "sdks" / "python" / "src"))
from aside_ads_client import AsideClient, AsyncAsideClient  # noqa: E402

DEMO_EXPECTATIONS = {
    "my postgres queries got really slow after the table grew, how do I find the problem?": "Slowlog Lens",
    "how do I speed up my github actions builds, they take 20 minutes": "Rivetline CI",
    "what's the best way to fine-tune llama on my own data": "Kilnworks GPU Cloud",
    "how do I file GST returns as a freelancer": "Bahi Books",
    "planning a trip to japan next month, what should I pack": "Wayfarer Shield",
    "give me a recipe for sourdough bread": "Kettle & Crumb",
    "I want to learn spanish before my trip": "Lingo Lantern",
    "can I export this chat to markdown?": "Demo Chat (house ads)",
}


async def test_seed_demo_is_idempotent_and_serves_expected_brands(tmp_path: Path) -> None:
    w, rt = await build_world(make_settings(tmp_path))
    try:
        first = await seed_demo(
            rt.sessionmaker, tmp_path, "http://127.0.0.1:8001/landing", "http://127.0.0.1:8000"
        )
        second = await seed_demo(
            rt.sessionmaker, tmp_path, "http://127.0.0.1:8001/landing", "http://127.0.0.1:8000"
        )
        assert first["ASIDE_API_KEY"] == second["ASIDE_API_KEY"]  # keys reused, not rotated
        async with rt.sessionmaker() as s:
            assert (await s.execute(select(func.count()).select_from(Campaign))).scalar_one() == len(DEMO_ADS)
        env = (tmp_path / "demo.env").read_text()
        assert "ASIDE_API_KEY=aside_pub_" in env and "ASIDE_PLACEMENT=chat-sidebar" in env
        headers = {"Authorization": f"Bearer {first['ASIDE_API_KEY']}"}
        for context, brand in DEMO_EXPECTATIONS.items():
            r = await w.client.post(
                "/v1/serve", json={"context": context, "placement": "chat-sidebar"}, headers=headers
            )
            ad = r.json()["ad"]
            assert ad and ad["advertiser"] == brand, (context, r.json())
        for context in ("what is the capital of australia", "I want to kill myself"):
            r = await w.client.post(
                "/v1/serve", json={"context": context, "placement": "chat-sidebar"}, headers=headers
            )
            assert r.json()["ad"] is None
    finally:
        await w.client.aclose()
        await rt.close()


def test_cli_create_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    monkeypatch.setenv("ASIDE_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("ASIDE_DATABASE_URL", raising=False)
    assert main(["create-key", "--role", "publisher", "--publisher", "CLI App"]) == 0
    key = capsys.readouterr().out.strip().splitlines()[-1]
    assert key.startswith("aside_pub_")
    assert (tmp_path / "aside.db").exists()
    assert main(["create-key", "--role", "admin"]) == 0
    assert capsys.readouterr().out.strip().startswith("aside_admin_")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_python_sdk_against_live_server(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)

    async def seed() -> dict[str, str]:
        from aside_ads.runtime import Runtime

        rt = await Runtime.create(settings)
        try:
            return await seed_demo(rt.sessionmaker, tmp_path, "http://127.0.0.1:8001/landing", "x")
        finally:
            await rt.close()

    keys = asyncio.run(seed())
    port = _free_port()
    server = uvicorn.Server(
        uvicorn.Config(create_app(settings), host="127.0.0.1", port=port, log_level="warning")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        for _ in range(100):
            if server.started:
                break
            time.sleep(0.05)
        client = AsideClient(f"http://127.0.0.1:{port}", keys["ASIDE_API_KEY"], timeout=5)
        ad = client.get_sponsored(
            [{"role": "user", "content": "give me a recipe for sourdough bread"}],
            user_id="session-1",
            placement="chat-sidebar",
        )
        assert ad is not None and ad.label == "Sponsored" and ad.advertiser == "Kettle & Crumb"
        assert client.track_impression(ad) is True
        assert client.get_sponsored("I'm 13 years old", placement="chat-sidebar") is None
        assert client.last_reason == "sensitive_context"
        # Fail-open: wrong key or dead server returns None instead of raising.
        assert AsideClient(f"http://127.0.0.1:{port}", "aside_pub_bad").get_sponsored("bread") is None
        assert AsideClient("http://127.0.0.1:9", "k", timeout=0.2).get_sponsored("bread") is None
        async_client = AsyncAsideClient(f"http://127.0.0.1:{port}", keys["ASIDE_API_KEY"], timeout=5)
        ad2 = asyncio.run(
            async_client.get_sponsored(
                "planning a trip to japan, what should I pack", placement="chat-sidebar"
            )
        )
        assert ad2 is not None and ad2.advertiser == "Wayfarer Shield"
    finally:
        server.should_exit = True
        thread.join(timeout=10)
