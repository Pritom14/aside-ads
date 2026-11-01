"""Publisher-facing API (serve, server-side impressions) and advertiser conversion postbacks."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response

from aside_ads.api.deps import base_url, get_runtime, require_roles
from aside_ads.models import ApiKey
from aside_ads.runtime import Runtime
from aside_ads.schemas import (
    ConversionRequest,
    ConversionResponse,
    ImpressionRequest,
    ServeRequest,
    ServeResponse,
    TrackResponse,
    to_micros,
)
from aside_ads.serving import UnknownPlacement, serve
from aside_ads.tokens import TokenExpired, TokenInvalid
from aside_ads.tracking import ConversionError, record_conversion, record_event

router = APIRouter(prefix="/v1", tags=["public"])


@router.post("/serve", response_model=ServeResponse)
async def serve_ad(
    req: ServeRequest,
    request: Request,
    response: Response,
    key: ApiKey = Depends(require_roles("publisher")),
    rt: Runtime = Depends(get_runtime),
) -> ServeResponse:
    """Pick at most one sponsored ad for a conversation.

    The context is used in memory only and never stored. Render the returned ad
    beside the model's answer, labeled "Sponsored", and fetch ``impression_url``
    when it is shown.
    """
    response.headers["Cache-Control"] = "no-store"
    assert key.publisher_id is not None
    try:
        return await serve(rt, key.publisher_id, req, base_url(request, rt))
    except UnknownPlacement as exc:
        raise HTTPException(404, f"Unknown placement '{exc}' for this publisher") from exc


@router.post("/impressions", response_model=TrackResponse)
async def server_side_impression(
    body: ImpressionRequest,
    key: ApiKey = Depends(require_roles("publisher")),
    rt: Runtime = Depends(get_runtime),
) -> TrackResponse:
    """Record an impression from your backend (authenticated, so no bot heuristics apply)."""
    token = body.token
    if "t=" in token:  # accept the full impression_url too
        token = token.split("t=", 1)[1].split("&", 1)[0]
    try:
        claims = rt.signer.verify(token, "imp", rt.now().timestamp())
    except TokenExpired as exc:
        raise HTTPException(410, "Impression token expired") from exc
    except TokenInvalid as exc:
        raise HTTPException(400, "Invalid impression token") from exc
    if claims.get("p") != key.publisher_id:
        raise HTTPException(403, "Token was issued to another publisher")
    out = await record_event(rt, "impression", claims)
    return TrackResponse(status=out.status, billed=out.billed)


async def _conversion(
    rt: Runtime, key: ApiKey, click_id: str, idem: str | None, value: Decimal | None, response: Response
) -> ConversionResponse:
    try:
        out = await record_conversion(
            rt,
            click_id=click_id,
            idempotency_key=idem,
            value_micros=to_micros(value),
            advertiser_scope=key.advertiser_id if key.role == "advertiser" else None,
        )
    except ConversionError as exc:
        raise HTTPException(exc.status_code, exc.detail) from exc
    response.status_code = 201 if out.status == "recorded" else 200
    return ConversionResponse(status=out.status, click_id=click_id, campaign_id=out.campaign_id)


@router.post("/conversions", response_model=ConversionResponse, status_code=201)
async def post_conversion(
    body: ConversionRequest,
    response: Response,
    idempotency_key: str | None = Header(default=None, max_length=128),
    key: ApiKey = Depends(require_roles("advertiser", "admin")),
    rt: Runtime = Depends(get_runtime),
) -> ConversionResponse:
    """Conversion postback keyed by click id. Repeats with the same idempotency key count once.

    The idempotency key comes from the body or the ``Idempotency-Key`` header and
    defaults to the click id (one conversion per click).
    """
    return await _conversion(
        rt, key, body.click_id, body.idempotency_key or idempotency_key, body.value, response
    )


@router.get("/conversions", response_model=ConversionResponse, status_code=201)
async def get_conversion(
    response: Response,
    click_id: str,
    idempotency_key: str | None = None,
    value: str | None = None,
    key: ApiKey = Depends(require_roles("advertiser", "admin", allow_query_key=True)),
    rt: Runtime = Depends(get_runtime),
) -> ConversionResponse:
    """Pixel-style postback: ``GET /v1/conversions?click_id=...&key=...``."""
    try:
        dec = Decimal(value) if value else None
    except InvalidOperation as exc:
        raise HTTPException(422, "value must be a number") from exc
    if dec is not None and dec < 0:
        raise HTTPException(422, "value must be >= 0")
    return await _conversion(rt, key, click_id[:64], idempotency_key, dec, response)
