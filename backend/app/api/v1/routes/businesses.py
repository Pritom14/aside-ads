from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.database import get_db
from app.models.business import Business
from app.models.user import User
from app.schemas.businesses import BusinessCreate, BusinessOut, BusinessUpdate

router = APIRouter()


@router.post("", response_model=BusinessOut)
async def create_business(
    payload: BusinessCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> BusinessOut:
    business = Business(
        owner_user_id=current_user.id,
        name=payload.name,
        category=payload.category,
        phone=payload.phone,
        website=payload.website,
        address=payload.address,
        lat=payload.lat,
        lng=payload.lng,
    )
    db.add(business)
    await db.commit()
    await db.refresh(business)
    return BusinessOut(
        id=str(business.id),
        name=business.name,
        category=business.category,
        phone=business.phone,
        website=business.website,
        address=business.address,
        lat=business.lat,
        lng=business.lng,
    )


@router.get("", response_model=list[BusinessOut])
async def list_businesses(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[BusinessOut]:
    rows = (await db.execute(select(Business).where(Business.owner_user_id == current_user.id))).scalars().all()
    return [
        BusinessOut(
            id=str(b.id),
            name=b.name,
            category=b.category,
            phone=b.phone,
            website=b.website,
            address=b.address,
            lat=b.lat,
            lng=b.lng,
        )
        for b in rows
    ]


@router.patch("/{business_id}", response_model=BusinessOut)
async def update_business(
    business_id: str,
    payload: BusinessUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> BusinessOut:
    try:
        bid = uuid.UUID(business_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid business_id")

    business = (
        await db.execute(select(Business).where(Business.id == bid, Business.owner_user_id == current_user.id))
    ).scalar_one_or_none()
    if not business:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Business not found")

    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(business, field, value)

    await db.commit()
    await db.refresh(business)
    return BusinessOut(
        id=str(business.id),
        name=business.name,
        category=business.category,
        phone=business.phone,
        website=business.website,
        address=business.address,
        lat=business.lat,
        lng=business.lng,
    )

