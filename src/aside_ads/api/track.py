"""Browser-facing tracking endpoints: impression pixel/beacon and click redirect."""

from __future__ import annotations

import base64
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from aside_ads.api.deps import client_ip, get_runtime
from aside_ads.bots import is_bot_user_agent
from aside_ads.models import Creative
from aside_ads.runtime import Runtime
from aside_ads.tokens import TokenExpired, TokenInvalid
from aside_ads.tracking import record_event

router = APIRouter(tags=["tracking"])

_GIF = base64.b64decode("R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7")
_NO_STORE = {"Cache-Control": "no-store, max-age=0"}


def _pixel(status: str, code: int = 200) -> Response:
    return Response(
        _GIF,
        status_code=code,
        media_type="image/gif",
        headers={**_NO_STORE, "X-Aside-Status": status, "Access-Control-Allow-Origin": "*"},
    )


async def _traffic_filter(request: Request, rt: Runtime) -> str | None:
    if is_bot_user_agent(request.headers.get("user-agent")):
        return "bot"
    if not await rt.rate_limiter.allow(client_ip(request, rt)):
        return "rate_limited"
    return None


@router.api_route("/t/i", methods=["GET", "POST"], include_in_schema=True)
async def impression(t: str, request: Request, rt: Runtime = Depends(get_runtime)) -> Response:
    """Impression pixel. Use as ``<img src>``, ``fetch`` or ``navigator.sendBeacon``."""
    try:
        claims = rt.signer.verify(t, "imp", rt.now().timestamp())
    except TokenExpired:
        return _pixel("expired", 410)
    except TokenInvalid:
        return _pixel("invalid", 400)
    blocked = await _traffic_filter(request, rt)
    if blocked:
        return _pixel(blocked)
    out = await record_event(rt, "impression", claims)
    return _pixel(out.status)


def _with_param(url: str, name: str, value: str) -> str:
    parts = urlparse(url)
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k != name]
    query.append((name, value))
    return urlunparse(parts._replace(query=urlencode(query)))


@router.get("/t/c")
async def click(t: str, request: Request, rt: Runtime = Depends(get_runtime)) -> Response:
    """Validate a signed click token, record the click once, and redirect to the advertiser.

    Expired but authentic tokens still redirect (the user should reach the page)
    but are not counted or billed. Tampered tokens are rejected.
    """
    try:
        claims = rt.signer.verify(t, "clk", rt.now().timestamp())
        status: str | None = None
    except TokenExpired as exc:
        claims, status = exc.payload, "expired"
    except TokenInvalid:
        return HTMLResponse(
            "<h1>Invalid link</h1><p>This sponsored link is invalid or was modified.</p>",
            status_code=400,
            headers={**_NO_STORE, "X-Aside-Status": "invalid"},
        )
    async with rt.sessionmaker() as s:
        creative = await s.get(Creative, claims.get("a"))
    if creative is None:
        return HTMLResponse("<h1>Ad not found</h1>", status_code=404, headers=_NO_STORE)
    if status is None:
        status = await _traffic_filter(request, rt)
    if status is None:
        status = (await record_event(rt, "click", claims)).status
    target = (
        _with_param(creative.url, rt.settings.click_id_param, claims["r"])
        if rt.settings.click_id_param
        else creative.url
    )
    return RedirectResponse(
        target,
        status_code=302,
        headers={**_NO_STORE, "X-Aside-Status": status, "Referrer-Policy": "origin"},
    )
