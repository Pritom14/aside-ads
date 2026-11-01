from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.database import get_db
from app.models.influencer import Influencer
from app.models.user import User
from app.schemas.influencers import InfluencerCreate, InfluencerOut, InfluencerUpdate

router = APIRouter()


@router.post("", response_model=InfluencerOut)
async def create_influencer_profile(
    payload: InfluencerCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> InfluencerOut:
    existing = (await db.execute(select(Influencer).where(Influencer.owner_user_id == current_user.id))).scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Influencer profile already exists")

    handle_taken = (await db.execute(select(Influencer).where(Influencer.handle == payload.handle))).scalar_one_or_none()
    if handle_taken:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Handle already taken")

    inf = Influencer(
        owner_user_id=current_user.id,
        handle=payload.handle,
        category=payload.category,
        followers=payload.followers,
        engagement_rate=payload.engagement_rate,
        verified=False,
    )
    db.add(inf)
    await db.commit()
    await db.refresh(inf)
    return InfluencerOut(
        id=str(inf.id),
        handle=inf.handle,
        category=inf.category,
        verified=inf.verified,
        followers=inf.followers,
        engagement_rate=float(inf.engagement_rate),
    )


@router.get("/me", response_model=InfluencerOut)
async def get_my_influencer_profile(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> InfluencerOut:
    inf = (await db.execute(select(Influencer).where(Influencer.owner_user_id == current_user.id))).scalar_one_or_none()
    if not inf:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Influencer profile not found")
    return InfluencerOut(
        id=str(inf.id),
        handle=inf.handle,
        category=inf.category,
        verified=inf.verified,
        followers=inf.followers,
        engagement_rate=float(inf.engagement_rate),
    )


@router.patch("/me", response_model=InfluencerOut)
async def update_my_influencer_profile(
    payload: InfluencerUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> InfluencerOut:
    inf = (await db.execute(select(Influencer).where(Influencer.owner_user_id == current_user.id))).scalar_one_or_none()
    if not inf:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Influencer profile not found")

    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(inf, field, value)

    await db.commit()
    await db.refresh(inf)
    return InfluencerOut(
        id=str(inf.id),
        handle=inf.handle,
        category=inf.category,
        verified=inf.verified,
        followers=inf.followers,
        engagement_rate=float(inf.engagement_rate),
    )

