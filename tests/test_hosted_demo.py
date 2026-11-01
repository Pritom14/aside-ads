"""The hosted demo's public surface: client IP, chat rate limit and the admin gate."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import httpx

from aside_ads.counters import MemoryCounters

_spec = importlib.util.spec_from_file_location(
    "aside_hosted_demo", Path(__file__).resolve().parents[1] / "examples" / "chat-demo" / "hosted.py"
)
assert _spec and _spec.loader
hosted = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = hosted
_spec.loader.exec_module(hosted)


async def _echo(scope: Any, receive: Any, send: Any) -> None:
    body = f"{scope['path']} {scope['client'][0]}".encode()
    await send({"type": "http.response.start", "status": 200, "headers": [(b"content-type", b"text/plain")]})
    await send({"type": "http.response.body", "body": body})


def _client(app: Any, peer: str = "198.51.100.7") -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=(peer, 4000)), base_url="http://d")


async def test_client_ip_prefers_fly_header_then_forwarded_for() -> None:
    async with _client(hosted.ClientIP(_echo)) as c:
        assert (await c.get("/x")).text == "/x 198.51.100.7"
        r = await c.get("/x", headers={"X-Forwarded-For": "203.0.113.1, 10.0.0.1"})
        assert r.text == "/x 203.0.113.1"
        r = await c.get("/x", headers={"Fly-Client-IP": "203.0.113.9", "X-Forwarded-For": "1.2.3.4"})
        assert r.text == "/x 203.0.113.9"
    async with _client(hosted.ClientIP(_echo), peer="127.0.0.1") as c:
        # Loopback callers (the chat backend calling /v1/serve) keep their address.
        assert (await c.get("/x", headers={"X-Forwarded-For": "203.0.113.1"})).text == "/x 127.0.0.1"


async def test_chat_rate_limit_per_ip_and_global() -> None:
    app = hosted.ClientIP(hosted.ChatRateLimit(_echo, MemoryCounters(), per_ip=2, global_limit=3))
    async with _client(app) as c:
        codes = [
            (await c.post("/api/chat", headers={"Fly-Client-IP": "1.1.1.1"})).status_code for _ in range(3)
        ]
        assert codes == [200, 200, 429]
        r = await c.post("/api/chat", headers={"Fly-Client-IP": "2.2.2.2"})
        assert r.status_code == 429 and r.json()["ad_reason"] == "rate_limited"  # global limit reached
        assert (await c.get("/t/i?t=x")).status_code == 200  # only the chat endpoint is limited


async def test_gate_hides_admin_routes_from_the_public() -> None:
    gate = hosted.AsideGate(expose_admin=False)
    gate.app = _echo
    app = hosted.ClientIP(gate)
    async with _client(app) as c:
        for path in ("/t/i", "/t/c", "/v1/serve", "/healthz"):
            assert (await c.get(path)).status_code == 200, path
        for path in ("/dashboard", "/dashboard/login", "/v1/admin/campaigns", "/docs", "/anything"):
            assert (await c.get(path)).status_code == 404, path
        # A forged X-Forwarded-For does not make a remote client look local.
        r = await c.get("/dashboard", headers={"X-Forwarded-For": "127.0.0.1"})
        assert r.status_code == 404
    async with _client(app, peer="127.0.0.1") as c:
        assert (await c.get("/dashboard/login")).status_code == 200  # e.g. `fly ssh console`
    gate.expose_admin = True
    async with _client(app) as c:
        assert (await c.get("/v1/admin/campaigns")).status_code == 200
