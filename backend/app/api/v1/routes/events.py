from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.ad_event import AdEvent
from app.models.campaign import Campaign
from app.schemas.events import EventCreate, EventOut

router = APIRouter()


@router.post("", response_model=EventOut)
async def create_event(payload: EventCreate, db: AsyncSession = Depends(get_db)) -> EventOut:
    try:
        cid = uuid.UUID(payload.campaign_id)
        aid = uuid.UUID(payload.ad_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid campaign_id/ad_id")

    campaign = (await db.execute(select(Campaign).where(Campaign.id == cid))).scalar_one_or_none()
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")

    evt = AdEvent(
        campaign_id=cid,
        ad_type=payload.ad_type,
        ad_id=aid,
        event_type=payload.event_type,
        meta=payload.meta or {},
    )
    db.add(evt)
    await db.commit()
    await db.refresh(evt)
    return EventOut(
        id=str(evt.id),
        campaign_id=str(evt.campaign_id),
        ad_type=evt.ad_type,
        ad_id=str(evt.ad_id),
        event_type=evt.event_type,
        occurred_at=evt.occurred_at,
    )

