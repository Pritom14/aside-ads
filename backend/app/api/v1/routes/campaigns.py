from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.database import get_db
from app.models.business import Business
from app.models.campaign import Campaign
from app.models.influencer import Influencer
from app.models.user import User
from app.schemas.campaigns import CampaignCreate, CampaignOut, CampaignStatusUpdate, CampaignUpdate

router = APIRouter()


def _campaign_out(c: Campaign) -> CampaignOut:
    return CampaignOut(
        id=str(c.id),
        campaign_type=c.campaign_type,
        business_id=str(c.business_id) if c.business_id else None,
        influencer_id=str(c.influencer_id) if c.influencer_id else None,
        name=c.name,
        status=c.status,
        daily_budget=float(c.daily_budget),
    )


@router.post("", response_model=CampaignOut)
async def create_campaign(
    payload: CampaignCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> CampaignOut:
    if payload.campaign_type == "business":
        if not payload.business_id:
            raise HTTPException(status_code=400, detail="business_id is required for business campaigns")
        try:
            bid = uuid.UUID(payload.business_id)
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid business_id")

        business = (
            await db.execute(select(Business).where(Business.id == bid, Business.owner_user_id == current_user.id))
        ).scalar_one_or_none()
        if not business:
            raise HTTPException(status_code=404, detail="Business not found")

        campaign = Campaign(
            campaign_type="business",
            business_id=business.id,
            influencer_id=None,
            name=payload.name,
            status=payload.status,
            daily_budget=payload.daily_budget,
            start_at=payload.start_at,
            end_at=payload.end_at,
        )
    else:
        influencer = (
            await db.execute(select(Influencer).where(Influencer.owner_user_id == current_user.id))
        ).scalar_one_or_none()
        if not influencer:
            raise HTTPException(status_code=400, detail="Create influencer profile first")

        campaign = Campaign(
            campaign_type="influencer",
            business_id=None,
            influencer_id=influencer.id,
            name=payload.name,
            status=payload.status,
            daily_budget=payload.daily_budget,
            start_at=payload.start_at,
            end_at=payload.end_at,
        )

    db.add(campaign)
    await db.commit()
    await db.refresh(campaign)
    return _campaign_out(campaign)


@router.get("", response_model=list[CampaignOut])
async def list_campaigns(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[CampaignOut]:
    # Business campaigns owned via businesses
    business_ids = (
        await db.execute(select(Business.id).where(Business.owner_user_id == current_user.id))
    ).scalars().all()

    influencer = (
        await db.execute(select(Influencer).where(Influencer.owner_user_id == current_user.id))
    ).scalar_one_or_none()

    q = select(Campaign)
    clauses = []
    if business_ids:
        clauses.append(Campaign.business_id.in_(business_ids))
    if influencer:
        clauses.append(Campaign.influencer_id == influencer.id)

    if not clauses:
        return []

    rows = (await db.execute(q.where(or_(*clauses)))).scalars().all()
    return [_campaign_out(c) for c in rows]


@router.patch("/{campaign_id}", response_model=CampaignOut)
async def update_campaign(
    campaign_id: str,
    payload: CampaignUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> CampaignOut:
    try:
        cid = uuid.UUID(campaign_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid campaign_id")

    # Load campaign, then verify ownership
    campaign = (await db.execute(select(Campaign).where(Campaign.id == cid))).scalar_one_or_none()
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")

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

    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(campaign, field, value)

    await db.commit()
    await db.refresh(campaign)
    return _campaign_out(campaign)


@router.post("/{campaign_id}/status", response_model=CampaignOut)
async def set_campaign_status(
    campaign_id: str,
    payload: CampaignStatusUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> CampaignOut:
    updated = await update_campaign(campaign_id, CampaignUpdate(), db, current_user)  # ownership check reuse
    campaign = (await db.execute(select(Campaign).where(Campaign.id == uuid.UUID(updated.id)))).scalar_one()
    campaign.status = payload.status
    await db.commit()
    await db.refresh(campaign)
    return _campaign_out(campaign)

