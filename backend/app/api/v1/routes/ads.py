from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.database import get_db
from app.models.ad_event import AdEvent
from app.models.business import Business
from app.models.business_ad import BusinessAd
from app.models.campaign import Campaign
from app.models.influencer import Influencer
from app.models.influencer_ad import InfluencerAd
from app.models.user import User
from app.schemas.ads import AdCreate, AdOut, TopAdsOut

router = APIRouter()


async def _assert_campaign_owned(db: AsyncSession, current_user: User, campaign: Campaign) -> None:
    if campaign.campaign_type == "business":
        business = (
            await db.execute(select(Business).where(Business.id == campaign.business_id))
        ).scalar_one_or_none()
        if not business or business.owner_user_id != current_user.id:
            raise HTTPException(status_code=403, detail="Forbidden")
    else:
        influencer = (
            await db.execute(select(Influencer).where(Influencer.id == campaign.influencer_id))
        ).scalar_one_or_none()
        if not influencer or influencer.owner_user_id != current_user.id:
            raise HTTPException(status_code=403, detail="Forbidden")


@router.post("/campaigns/{campaign_id}/ads", response_model=AdOut)
async def create_ad(
    campaign_id: str,
    payload: AdCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> AdOut:
    try:
        cid = uuid.UUID(campaign_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid campaign_id")

    campaign = (await db.execute(select(Campaign).where(Campaign.id == cid))).scalar_one_or_none()
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    await _assert_campaign_owned(db, current_user, campaign)

    if campaign.campaign_type == "business":
        ad = BusinessAd(
            campaign_id=campaign.id,
            headline=payload.headline,
            body=payload.body,
            cta_text=payload.cta_text,
            cta_url=payload.cta_url,
        )
        db.add(ad)
        await db.commit()
        await db.refresh(ad)
        return AdOut(
            id=str(ad.id),
            campaign_id=str(ad.campaign_id),
            ad_type="business",
            headline=ad.headline,
            body=ad.body,
            cta_text=ad.cta_text,
            cta_url=ad.cta_url,
        )

    ad = InfluencerAd(
        campaign_id=campaign.id,
        headline=payload.headline,
        body=payload.body,
        cta_text=payload.cta_text,
        cta_url=payload.cta_url,
    )
    db.add(ad)
    await db.commit()
    await db.refresh(ad)
    return AdOut(
        id=str(ad.id),
        campaign_id=str(ad.campaign_id),
        ad_type="influencer",
        headline=ad.headline,
        body=ad.body,
        cta_text=ad.cta_text,
        cta_url=ad.cta_url,
    )


@router.get("/campaigns/{campaign_id}/ads", response_model=list[AdOut])
async def list_ads(
    campaign_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[AdOut]:
    try:
        cid = uuid.UUID(campaign_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid campaign_id")

    campaign = (await db.execute(select(Campaign).where(Campaign.id == cid))).scalar_one_or_none()
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    await _assert_campaign_owned(db, current_user, campaign)

    if campaign.campaign_type == "business":
        ads = (await db.execute(select(BusinessAd).where(BusinessAd.campaign_id == campaign.id))).scalars().all()
        return [
            AdOut(
                id=str(a.id),
                campaign_id=str(a.campaign_id),
                ad_type="business",
                headline=a.headline,
                body=a.body,
                cta_text=a.cta_text,
                cta_url=a.cta_url,
            )
            for a in ads
        ]

    ads = (await db.execute(select(InfluencerAd).where(InfluencerAd.campaign_id == campaign.id))).scalars().all()
    return [
        AdOut(
            id=str(a.id),
            campaign_id=str(a.campaign_id),
            ad_type="influencer",
            headline=a.headline,
            body=a.body,
            cta_text=a.cta_text,
            cta_url=a.cta_url,
        )
        for a in ads
    ]


@router.get("/ads/top", response_model=list[TopAdsOut])
async def top_ads(
    metric: str = "clicks",
    window_days: int = 7,
    limit: int = 20,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[TopAdsOut]:
    if metric not in {"clicks", "impressions"}:
        raise HTTPException(status_code=400, detail="metric must be clicks or impressions")
    if window_days < 1 or window_days > 365:
        raise HTTPException(status_code=400, detail="window_days out of range")
    if limit < 1 or limit > 200:
        raise HTTPException(status_code=400, detail="limit out of range")

    since = datetime.now(timezone.utc) - timedelta(days=window_days)

    # Scope to user's owned campaigns (businesses + influencer)
    business_ids = (
        await db.execute(select(Business.id).where(Business.owner_user_id == current_user.id))
    ).scalars().all()
    influencer = (
        await db.execute(select(Influencer).where(Influencer.owner_user_id == current_user.id))
    ).scalar_one_or_none()

    clauses = []
    if business_ids:
        clauses.append(Campaign.business_id.in_(business_ids))
    if influencer:
        clauses.append(Campaign.influencer_id == influencer.id)
    if not clauses:
        return []

    owned_campaign_ids = (await db.execute(select(Campaign.id).where(or_(*clauses)))).scalars().all()
    if not owned_campaign_ids:
        return []

    impressions = func.sum(case((AdEvent.event_type == "impression", 1), else_=0)).label("impressions")
    clicks = func.sum(case((AdEvent.event_type == "click", 1), else_=0)).label("clicks")

    q = (
        select(AdEvent.ad_type, AdEvent.ad_id, AdEvent.campaign_id, impressions, clicks)
        .where(AdEvent.occurred_at >= since, AdEvent.campaign_id.in_(owned_campaign_ids))
        .group_by(AdEvent.ad_type, AdEvent.ad_id, AdEvent.campaign_id)
    )
    q = q.order_by((clicks if metric == "clicks" else impressions).desc()).limit(limit)

    rows = (await db.execute(q)).all()
    return [
        TopAdsOut(
            ad_type=r[0],
            ad_id=str(r[1]),
            campaign_id=str(r[2]),
            impressions=int(r[3] or 0),
            clicks=int(r[4] or 0),
        )
        for r in rows
    ]

