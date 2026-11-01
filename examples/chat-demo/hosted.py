"""Hosted demo: the Aside ad server and the chat demo in one process, on one public port.

This is what the ``demo`` Docker target runs (Fly.io, Hugging Face Spaces). It is a
public playground, not a production setup.

* One port serves the chat page and ``/api/chat`` (``app.py`` next to this file),
  plus the ad server's tracking (``/t/*``) and API (``/v1/*``) routes. The
  dashboard, ``/v1/admin`` and the server's API docs answer 404 to the public;
  set ``ASIDE_DEMO_DASHBOARD=1`` to expose them (they still need the admin key).
* On boot it seeds the fictional demo campaigns if they are missing and issues a
  fresh random admin key, revoking older ones. The key is printed to the logs and
  stored nowhere else in plain text.
* ``/api/chat`` is rate limited per client IP and globally.
* Database growth is capped: impressions, clicks, conversions and spend are reset
  every ``ASIDE_DEMO_RESET_HOURS`` (default 24) and whenever the events table passes
  ``ASIDE_DEMO_MAX_EVENTS`` (default 50000).
* Conversation text is never stored, by the ad server or by the chat app.

Run locally (no Docker):

    pip install -e ".[embeddings]" ./sdks/python
    PORT=8080 python examples/chat-demo/hosted.py     # http://127.0.0.1:8080

Settings (environment): PORT, HOST, ASIDE_PUBLIC_URL (default: derived from
FLY_APP_NAME or SPACE_HOST, else http://localhost:PORT), ASIDE_DATA_DIR,
ASIDE_DEMO_CHAT_PER_MINUTE (20), ASIDE_DEMO_CHAT_GLOBAL_PER_MINUTE (600),
ASIDE_DEMO_MAX_EVENTS, ASIDE_DEMO_RESET_HOURS, ASIDE_DEMO_DASHBOARD, and the LLM_*
variables of the chat demo.
"""

from __future__ import annotations

import asyncio
import contextlib
import importlib.util
import json
import logging
import os
import sys
import tempfile
import time
from collections.abc import AsyncIterator, Awaitable, Callable, MutableMapping
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
log = logging.getLogger("aside.demo")

Scope = MutableMapping[str, Any]
ASGIApp = Callable[[Scope, Any, Any], Awaitable[None]]

PUBLIC_PREFIXES = ("/t/", "/v1/", "/healthz")
ADMIN_PREFIXES = ("/v1/admin", "/dashboard")
LOOPBACK = {"127.0.0.1", "::1", "localhost"}


def _flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


def _public_url(port: int) -> str:
    if os.environ.get("ASIDE_PUBLIC_URL"):
        return os.environ["ASIDE_PUBLIC_URL"].rstrip("/")
    if os.environ.get("FLY_APP_NAME"):
        return f"https://{os.environ['FLY_APP_NAME']}.fly.dev"
    if os.environ.get("SPACE_HOST"):  # set by Hugging Face Spaces
        return f"https://{os.environ['SPACE_HOST']}"
    return f"http://localhost:{port}"


def _send_json(status: int, body: dict[str, Any]) -> Callable[[Any], Awaitable[None]]:
    payload = json.dumps(body).encode()

    async def send_it(send: Any) -> None:
        headers = [(b"content-type", b"application/json"), (b"cache-control", b"no-store")]
        if status == 429:
            headers.append((b"retry-after", b"60"))
        await send({"type": "http.response.start", "status": status, "headers": headers})
        await send({"type": "http.response.body", "body": payload})

    return send_it


class ClientIP:
    """Put the real client IP in ``scope["client"]`` (Fly-Client-IP, then X-Forwarded-For).

    The original peer stays in ``scope["demo.peer"]`` for the loopback check. A client
    can forge X-Forwarded-For on hosts that only append to it, so the per-IP limit is
    backed by a global one.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Any, send: Any) -> None:
        if scope["type"] == "http":
            peer = scope.get("client") or ("", 0)
            scope["demo.peer"] = peer[0]
            headers = dict(scope.get("headers") or [])
            ip = headers.get(b"fly-client-ip", b"").decode().strip()
            if not ip and peer[0] not in LOOPBACK:
                ip = headers.get(b"x-forwarded-for", b"").decode().split(",")[0].strip()
            if ip:
                scope["client"] = (ip[:64], peer[1])
        await self.app(scope, receive, send)


class ChatRateLimit:
    def __init__(self, app: ASGIApp, counters: Any, per_ip: int, global_limit: int) -> None:
        self.app, self.counters, self.per_ip, self.global_limit = app, counters, per_ip, global_limit

    async def __call__(self, scope: Scope, receive: Any, send: Any) -> None:
        if scope["type"] == "http" and scope["path"] == "/api/chat":
            ip = (scope.get("client") or ("unknown", 0))[0]
            over_ip = self.per_ip > 0 and await self.counters.incr(f"chat:{ip}", 60) > self.per_ip
            over_all = self.global_limit > 0 and await self.counters.incr("chat:*", 60) > self.global_limit
            if over_ip or over_all:
                await _send_json(
                    429,
                    {
                        "answer": "This public demo is busy or you are sending messages quickly. "
                        "Try again in a minute, or run Aside yourself: it is open source.",
                        "sponsored": None,
                        "ad_reason": "rate_limited",
                    },
                )(send)
                return
        await self.app(scope, receive, send)


class AsideGate:
    """Mounted at ``/``: forwards public ad-server routes to the Aside app, 404 for the rest."""

    def __init__(self, expose_admin: bool) -> None:
        self.app: ASGIApp | None = None
        self.expose_admin = expose_admin

    async def __call__(self, scope: Scope, receive: Any, send: Any) -> None:
        path = scope.get("path", "")  # mounted at "/", so this is the full request path
        if scope["type"] == "http" and self.app is not None:
            admin = path.startswith(ADMIN_PREFIXES)
            allowed = path.startswith(PUBLIC_PREFIXES) and not admin
            if admin and (self.expose_admin or scope.get("demo.peer") in LOOPBACK):
                allowed = True
            if allowed:
                await self.app(scope, receive, send)
                return
        await _send_json(404, {"detail": "Not Found"})(send)


async def _reset_activity(sessionmaker: Any) -> None:
    from sqlalchemy import delete, update

    from aside_ads.models import BudgetReservation, Campaign, Conversion, DailyStat, Event

    async with sessionmaker() as s, s.begin():
        for model in (Event, Conversion, DailyStat, BudgetReservation):
            await s.execute(delete(model))
        await s.execute(
            update(Campaign).values(
                spend_total_micros=0,
                spend_today_micros=0,
                spend_day=None,
                impressions_total=0,
                clicks_total=0,
            )
        )


async def _events(sessionmaker: Any) -> int:
    from sqlalchemy import func, select

    from aside_ads.models import Event

    async with sessionmaker() as s:
        return int((await s.execute(select(func.count()).select_from(Event))).scalar_one())


async def _prepare(settings: Any, public_url: str, internal_url: str) -> str:
    """Seed if needed, rotate the admin key, return the publisher key. Runs before the app starts."""
    from sqlalchemy import update

    from aside_ads.auth import create_api_key
    from aside_ads.db import create_all, make_engine, make_sessionmaker
    from aside_ads.models import ApiKey, utcnow
    from aside_ads.seed import seed_demo

    settings.ensure_data_dir()
    engine = make_engine(settings.resolved_database_url)
    try:
        await create_all(engine)
        sm = make_sessionmaker(engine)
        out = await seed_demo(
            sm, settings.data_dir, f"{public_url}/landing", internal_url, include_admin_key=False
        )
        async with sm() as s:
            await s.execute(
                update(ApiKey)
                .where(ApiKey.role == "admin", ApiKey.revoked_at.is_(None))
                .values(revoked_at=utcnow())
            )
            _, admin_key = await create_api_key(s, "admin", "hosted demo")
            await s.commit()
    finally:
        await engine.dispose()
    log.warning("Seeded campaigns: %s", out["created"])
    log.warning("Admin key for this boot (shown only here): %s", admin_key)
    return out["ASIDE_API_KEY"]


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    for noisy in ("httpx", "httpcore", "huggingface_hub"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    port = int(os.environ.get("PORT", "8080"))
    host = os.environ.get("HOST", "127.0.0.1")
    public_url = _public_url(port)
    internal_url = f"http://127.0.0.1:{port}"
    os.environ["ASIDE_PUBLIC_URL"] = public_url
    os.environ.setdefault("ASIDE_DATA_DIR", str(Path(tempfile.gettempdir()) / "aside-demo"))
    for var in ("DATABASE_URL", "ASIDE_DATABASE_URL", "REDIS_URL", "ASIDE_REDIS_URL"):
        os.environ.pop(var, None)  # the demo always uses its own SQLite file and in-process counters

    from aside_ads.config import Settings

    settings = Settings()
    publisher_key = asyncio.run(_prepare(settings, public_url, internal_url))

    # The chat app reads its configuration at import time.
    os.environ.update(
        {"ASIDE_URL": internal_url, "ASIDE_API_KEY": publisher_key, "ASIDE_PLACEMENT": "chat-sidebar"}
    )
    spec = importlib.util.spec_from_file_location("aside_chat_demo", HERE / "app.py")
    assert spec and spec.loader
    chat = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = chat  # pydantic resolves the request models through sys.modules
    spec.loader.exec_module(chat)

    import uvicorn

    from aside_ads.app import create_app
    from aside_ads.counters import MemoryCounters
    from aside_ads.runtime import Runtime

    gate = AsideGate(expose_admin=_flag("ASIDE_DEMO_DASHBOARD"))
    max_events = int(os.environ.get("ASIDE_DEMO_MAX_EVENTS", "50000"))
    reset_every = float(os.environ.get("ASIDE_DEMO_RESET_HOURS", "24")) * 3600

    async def housekeeping(rt: Runtime) -> None:
        last_reset = time.monotonic()
        while True:
            await asyncio.sleep(300)
            try:
                events = await _events(rt.sessionmaker)
                if events > max_events or time.monotonic() - last_reset >= reset_every:
                    await _reset_activity(rt.sessionmaker)
                    last_reset = time.monotonic()
                    log.warning("Demo activity reset (%d events).", events)
            except Exception:  # keep the demo up; try again next round
                log.exception("Demo housekeeping failed")

    @contextlib.asynccontextmanager
    async def lifespan(_: Any) -> AsyncIterator[None]:
        rt = await Runtime.create(settings)
        gate.app = create_app(runtime=rt)
        task = asyncio.create_task(housekeeping(rt))
        log.warning("Aside demo on %s (matcher: %s)", public_url, rt.matcher.name)
        try:
            yield
        finally:
            task.cancel()
            await rt.close()

    chat.app.router.lifespan_context = lifespan
    chat.app.mount("/", gate)
    app = ClientIP(
        ChatRateLimit(
            chat.app,
            MemoryCounters(),
            per_ip=int(os.environ.get("ASIDE_DEMO_CHAT_PER_MINUTE", "20")),
            global_limit=int(os.environ.get("ASIDE_DEMO_CHAT_GLOBAL_PER_MINUTE", "600")),
        )
    )
    uvicorn.run(app, host=host, port=port, access_log=False, proxy_headers=False)


if __name__ == "__main__":
    sys.exit(main())
