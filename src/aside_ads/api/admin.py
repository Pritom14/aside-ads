"""Admin JSON API (requires an admin key). The dashboard offers the same operations as HTML forms."""

from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from aside_ads.api.deps import get_runtime, require_roles
from aside_ads.auth import create_api_key
from aside_ads.models import Advertiser, ApiKey, utcnow
from aside_ads.runtime import Runtime
from aside_ads.schemas import (
    AdvertiserIn,
    AdvertiserOut,
    CampaignIn,
    CampaignOut,
    CampaignPatch,
    CreativeIn,
    CreativeOut,
    CreativePatch,
    KeyIn,
    KeyOut,
    PlacementIn,
    PublisherIn,
    PublisherOut,
    StatRow,
    to_micros,
)
from aside_ads.services import (
    NotFound,
    add_creative,
    add_placement,
    campaign_out,
    create_campaign,
    create_publisher,
    daily_stats,
    list_campaigns,
    list_publishers,
    load_campaign,
    load_publisher,
    publisher_out,
    update_campaign,
    update_creative,
)

router = APIRouter(prefix="/v1/admin", tags=["admin"], dependencies=[Depends(require_roles("admin"))])


def _404(exc: NotFound) -> HTTPException:
    return HTTPException(404, str(exc))


@router.get("/advertisers", response_model=list[AdvertiserOut])
async def advertisers(rt: Runtime = Depends(get_runtime)) -> list[Advertiser]:
    async with rt.sessionmaker() as s:
        return list((await s.execute(select(Advertiser).order_by(Advertiser.name))).scalars().all())


@router.post("/advertisers", response_model=AdvertiserOut, status_code=201)
async def new_advertiser(body: AdvertiserIn, rt: Runtime = Depends(get_runtime)) -> Advertiser:
    async with rt.sessionmaker() as s:
        adv = Advertiser(name=body.name, website=body.website)
        s.add(adv)
        try:
            await s.commit()
        except IntegrityError as exc:
            raise HTTPException(409, "An advertiser with that name exists") from exc
        return adv


@router.get("/campaigns", response_model=list[CampaignOut])
async def campaigns(rt: Runtime = Depends(get_runtime)) -> list[CampaignOut]:
    async with rt.sessionmaker() as s:
        return [campaign_out(c, rt.day()) for c in await list_campaigns(s)]


@router.post("/campaigns", response_model=CampaignOut, status_code=201)
async def new_campaign(body: CampaignIn, rt: Runtime = Depends(get_runtime)) -> CampaignOut:
    async with rt.sessionmaker() as s:
        try:
            c = await create_campaign(s, body)
            await s.commit()
            return campaign_out(await load_campaign(s, c.id), rt.day())
        except NotFound as exc:
            raise _404(exc) from exc


@router.get("/campaigns/{campaign_id}", response_model=CampaignOut)
async def get_campaign(campaign_id: str, rt: Runtime = Depends(get_runtime)) -> CampaignOut:
    async with rt.sessionmaker() as s:
        try:
            return campaign_out(await load_campaign(s, campaign_id), rt.day())
        except NotFound as exc:
            raise _404(exc) from exc


@router.patch("/campaigns/{campaign_id}", response_model=CampaignOut)
async def patch_campaign(
    campaign_id: str, body: CampaignPatch, rt: Runtime = Depends(get_runtime)
) -> CampaignOut:
    async with rt.sessionmaker() as s:
        try:
            await update_campaign(s, campaign_id, body)
            await s.commit()
            return campaign_out(await load_campaign(s, campaign_id), rt.day())
        except NotFound as exc:
            raise _404(exc) from exc


@router.post("/campaigns/{campaign_id}/creatives", response_model=CreativeOut, status_code=201)
async def new_creative(campaign_id: str, body: CreativeIn, rt: Runtime = Depends(get_runtime)) -> CreativeOut:
    async with rt.sessionmaker() as s:
        try:
            cr = await add_creative(s, campaign_id, body)
            await s.commit()
            return CreativeOut.model_validate(cr)
        except NotFound as exc:
            raise _404(exc) from exc


@router.patch("/creatives/{creative_id}", response_model=CreativeOut)
async def patch_creative(
    creative_id: str, body: CreativePatch, rt: Runtime = Depends(get_runtime)
) -> CreativeOut:
    async with rt.sessionmaker() as s:
        try:
            cr = await update_creative(s, creative_id, body)
            await s.commit()
            return CreativeOut.model_validate(cr)
        except NotFound as exc:
            raise _404(exc) from exc


@router.get("/publishers", response_model=list[PublisherOut])
async def publishers(rt: Runtime = Depends(get_runtime)) -> list[PublisherOut]:
    async with rt.sessionmaker() as s:
        return [publisher_out(p) for p in await list_publishers(s)]


@router.post("/publishers", response_model=PublisherOut, status_code=201)
async def new_publisher(body: PublisherIn, rt: Runtime = Depends(get_runtime)) -> PublisherOut:
    async with rt.sessionmaker() as s:
        try:
            p = await create_publisher(s, body.name, body.domain)
            await add_placement(s, p.id, "default", "Default")
            await s.commit()
        except IntegrityError as exc:
            raise HTTPException(409, "A publisher with that name exists") from exc
        return publisher_out(await load_publisher(s, p.id))


@router.post("/publishers/{publisher_id}/placements", response_model=PublisherOut, status_code=201)
async def new_placement(
    publisher_id: str, body: PlacementIn, rt: Runtime = Depends(get_runtime)
) -> PublisherOut:
    async with rt.sessionmaker() as s:
        try:
            await add_placement(s, publisher_id, body.key, body.name, to_micros(body.floor_cpm))
            await s.commit()
        except NotFound as exc:
            raise _404(exc) from exc
        except IntegrityError as exc:
            raise HTTPException(409, "Placement key already exists for this publisher") from exc
        return publisher_out(await load_publisher(s, publisher_id))


def _key_out(k: ApiKey, plaintext: str | None = None) -> KeyOut:
    return KeyOut(
        id=k.id,
        role=k.role,
        name=k.name,
        prefix=k.prefix,
        publisher_id=k.publisher_id,
        advertiser_id=k.advertiser_id,
        created_at=k.created_at,
        revoked_at=k.revoked_at,
        key=plaintext,
    )


@router.get("/keys", response_model=list[KeyOut])
async def keys(rt: Runtime = Depends(get_runtime)) -> list[KeyOut]:
    async with rt.sessionmaker() as s:
        return [_key_out(k) for k in (await s.execute(select(ApiKey).order_by(ApiKey.created_at))).scalars()]


@router.post("/keys", response_model=KeyOut, status_code=201)
async def new_key(body: KeyIn, rt: Runtime = Depends(get_runtime)) -> KeyOut:
    async with rt.sessionmaker() as s:
        try:
            k, plaintext = await create_api_key(
                s, body.role, body.name, body.publisher_id, body.advertiser_id
            )
            await s.commit()
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        except IntegrityError as exc:
            raise HTTPException(404, "Unknown publisher_id or advertiser_id") from exc
        return _key_out(k, plaintext)


@router.post("/keys/{key_id}/revoke", response_model=KeyOut)
async def revoke_key(key_id: str, rt: Runtime = Depends(get_runtime)) -> KeyOut:
    async with rt.sessionmaker() as s:
        k = await s.get(ApiKey, key_id)
        if k is None:
            raise HTTPException(404, "Key not found")
        k.revoked_at = k.revoked_at or utcnow()
        await s.commit()
        return _key_out(k)


@router.get("/stats", response_model=list[StatRow])
async def stats(
    campaign_id: str | None = None,
    days: int = 30,
    rt: Runtime = Depends(get_runtime),
) -> list[StatRow]:
    """Daily impressions, clicks, CTR, spend and conversions per campaign."""
    now = rt.now()
    day_from = rt.day(now - timedelta(days=max(days, 1) - 1))
    async with rt.sessionmaker() as s:
        return await daily_stats(s, day_from, rt.day(now), campaign_id)
