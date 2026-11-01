from __future__ import annotations

from pydantic import BaseModel, Field


class AdCreate(BaseModel):
    headline: str = Field(min_length=1, max_length=120)
    body: str = Field(min_length=1, max_length=5000)
    cta_text: str | None = Field(default=None, max_length=50)
    cta_url: str | None = Field(default=None, max_length=255)


class AdOut(BaseModel):
    id: str
    campaign_id: str
    ad_type: str  # business|influencer
    headline: str
    body: str
    cta_text: str | None
    cta_url: str | None


class TopAdsOut(BaseModel):
    ad_type: str
    ad_id: str
    campaign_id: str
    impressions: int
    clicks: int
