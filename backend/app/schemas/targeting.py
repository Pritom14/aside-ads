from __future__ import annotations

from pydantic import BaseModel, Field


class TargetingUpsert(BaseModel):
    radius_km: int = Field(default=5, ge=0, le=500)
    keywords: list[str] = Field(default_factory=list)
    rules: dict = Field(default_factory=dict)


class TargetingOut(TargetingUpsert):
    id: str
    campaign_id: str

