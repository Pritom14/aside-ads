from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class CampaignCreate(BaseModel):
    campaign_type: str = Field(default="business", pattern="^(business|influencer)$")
    name: str = Field(min_length=1, max_length=200)
    status: str = Field(default="draft", pattern="^(draft|active|paused)$")
    daily_budget: float = Field(default=0, ge=0)
    start_at: datetime | None = None
    end_at: datetime | None = None

    # For business campaigns
    business_id: str | None = None


class CampaignUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=200)
    daily_budget: float | None = Field(default=None, ge=0)
    start_at: datetime | None = None
    end_at: datetime | None = None


class CampaignStatusUpdate(BaseModel):
    status: str = Field(pattern="^(draft|active|paused)$")


class CampaignOut(BaseModel):
    id: str
    campaign_type: str
    business_id: str | None
    influencer_id: str | None
    name: str
    status: str
    daily_budget: float
