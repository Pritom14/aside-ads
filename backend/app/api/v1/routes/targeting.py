from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.database import get_db
from app.models.business import Business
from app.models.campaign import Campaign
from app.models.campaign_targeting import CampaignTargeting
from app.models.influencer import Influencer
from app.models.user import User
from app.schemas.targeting import TargetingOut, TargetingUpsert

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


@router.get("/campaigns/{campaign_id}/targeting", response_model=TargetingOut)
async def get_targeting(
    campaign_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> TargetingOut:
    try:
        cid = uuid.UUID(campaign_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid campaign_id")

    campaign = (await db.execute(select(Campaign).where(Campaign.id == cid))).scalar_one_or_none()
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    await _assert_campaign_owned(db, current_user, campaign)

    targeting = (
        await db.execute(select(CampaignTargeting).where(CampaignTargeting.campaign_id == campaign.id))
    ).scalar_one_or_none()
    if not targeting:
        raise HTTPException(status_code=404, detail="Targeting not found")

    return TargetingOut(
        id=str(targeting.id),
        campaign_id=str(targeting.campaign_id),
        radius_km=targeting.radius_km,
        keywords=targeting.keywords,
        rules=targeting.rules,
    )


@router.put("/campaigns/{campaign_id}/targeting", response_model=TargetingOut)
async def upsert_targeting(
    campaign_id: str,
    payload: TargetingUpsert,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> TargetingOut:
    try:
        cid = uuid.UUID(campaign_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid campaign_id")

    campaign = (await db.execute(select(Campaign).where(Campaign.id == cid))).scalar_one_or_none()
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    await _assert_campaign_owned(db, current_user, campaign)

    targeting = (
        await db.execute(select(CampaignTargeting).where(CampaignTargeting.campaign_id == campaign.id))
    ).scalar_one_or_none()

    if not targeting:
        targeting = CampaignTargeting(
            campaign_id=campaign.id,
            radius_km=payload.radius_km,
            keywords=payload.keywords,
            rules=payload.rules,
        )
        db.add(targeting)
    else:
        targeting.radius_km = payload.radius_km
        targeting.keywords = payload.keywords
        targeting.rules = payload.rules

    await db.commit()
    await db.refresh(targeting)
    return TargetingOut(
        id=str(targeting.id),
        campaign_id=str(targeting.campaign_id),
        radius_km=targeting.radius_km,
        keywords=targeting.keywords,
        rules=targeting.rules,
    )

