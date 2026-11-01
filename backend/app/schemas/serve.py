from __future__ import annotations

from pydantic import BaseModel, Field


class ServeRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    lat: float | None = None
    lng: float | None = None
    limit: int = Field(default=5, ge=1, le=50)


class ServedAd(BaseModel):
    ad_type: str
    ad_id: str
    campaign_id: str
    headline: str
    body: str
    cta_text: str | None
    cta_url: str | None


class ServeResponse(BaseModel):
    ads: list[ServedAd]

