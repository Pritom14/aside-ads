from __future__ import annotations

from collections.abc import Awaitable, Callable

from fastapi import Depends, HTTPException, Request, status

from aside_ads.auth import lookup_key
from aside_ads.models import ApiKey
from aside_ads.runtime import Runtime


def get_runtime(request: Request) -> Runtime:
    return request.app.state.runtime


def _key_from_headers(request: Request) -> str | None:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return request.headers.get("x-api-key")


async def _resolve(rt: Runtime, plaintext: str | None) -> ApiKey:
    if not plaintext:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing API key", {"WWW-Authenticate": "Bearer"})
    async with rt.sessionmaker() as s:
        key = await lookup_key(s, plaintext)
    if key is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid API key", {"WWW-Authenticate": "Bearer"})
    return key


def require_roles(*roles: str, allow_query_key: bool = False) -> Callable[..., Awaitable[ApiKey]]:
    async def dep(request: Request, rt: Runtime = Depends(get_runtime)) -> ApiKey:
        plaintext = _key_from_headers(request)
        if plaintext is None and allow_query_key:
            plaintext = request.query_params.get("key")
        key = await _resolve(rt, plaintext)
        if key.role not in roles:
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"This endpoint needs a {' or '.join(roles)} key")
        return key

    return dep


def base_url(request: Request, rt: Runtime) -> str:
    return (rt.settings.public_url or str(request.base_url)).rstrip("/")


def client_ip(request: Request, rt: Runtime) -> str | None:
    if rt.settings.trust_proxy_headers:
        fwd = request.headers.get("x-forwarded-for")
        if fwd:
            return fwd.split(",")[0].strip()
    return request.client.host if request.client else None
