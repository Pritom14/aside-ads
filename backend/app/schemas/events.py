from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class EventCreate(BaseModel):
    campaign_id: str
    ad_type: str = Field(pattern="^(business|influencer)$")
    ad_id: str
    event_type: str = Field(pattern="^(impression|click)$")
    meta: dict = Field(default_factory=dict)


class EventOut(BaseModel):
    id: str
    campaign_id: str
    ad_type: str
    ad_id: str
    event_type: str
    occurred_at: datetime

