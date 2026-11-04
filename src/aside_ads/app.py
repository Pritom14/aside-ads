"""FastAPI application factory."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from aside_ads import __version__
from aside_ads.api import admin, public, sponsor, track
from aside_ads.config import Settings
from aside_ads.dashboard import HERE as DASHBOARD_DIR
from aside_ads.dashboard import LoginRequired, protected
from aside_ads.dashboard import router as dashboard_router
from aside_ads.runtime import Runtime


def create_app(settings: Settings | None = None, runtime: Runtime | None = None) -> FastAPI:
    """Build the app. Pass ``runtime`` to reuse one (tests); otherwise it is created at startup."""
    settings = settings or (runtime.settings if runtime else Settings())
    connector = None
    if settings.connector:
        from aside_ads.connector import Connector

        connector = Connector(
            settings.public_url or f"http://127.0.0.1:{settings.port}", lambda: app.state.runtime
        )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        owned = runtime is None
        app.state.runtime = runtime or await Runtime.create(settings)
        try:
            async with AsyncExitStack() as stack:
                if connector is not None:
                    await stack.enter_async_context(connector.lifespan())
                yield
        finally:
            if owned:
                await app.state.runtime.close()

    app = FastAPI(
        title="Aside",
        version=__version__,
        description="Self-hosted ad server for AI chat apps. Conversations never leave your server.",
        lifespan=lifespan,
    )
    if runtime is not None:
        app.state.runtime = runtime

    app.include_router(public.router)
    app.include_router(track.router)
    app.include_router(admin.router)
    app.include_router(sponsor.router)
    app.include_router(dashboard_router)
    app.include_router(protected)
    app.mount("/dashboard/static", StaticFiles(directory=str(DASHBOARD_DIR / "static")), name="static")
    app.state.connector = connector
    if connector is not None:
        app.include_router(connector.router)

    @app.exception_handler(LoginRequired)
    async def _login(_: Request, __: LoginRequired) -> RedirectResponse:
        return RedirectResponse("/dashboard/login", status_code=303)

    @app.get("/healthz", include_in_schema=False)
    async def healthz(request: Request) -> dict[str, str]:
        rt: Runtime = request.app.state.runtime
        return {"status": "ok", "version": __version__, "matcher": rt.matcher.name}

    @app.get("/", include_in_schema=False)
    async def root() -> dict[str, str]:
        return {"name": "aside", "version": __version__, "docs": "/docs", "dashboard": "/dashboard"}

    if connector is not None:
        # Last, so it only sees what the routes above do not handle: /mcp and the OAuth endpoints.
        app.mount("/", connector.app)
    return app
