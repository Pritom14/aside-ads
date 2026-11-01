from __future__ import annotations

from pydantic import BaseModel, Field


class InfluencerCreate(BaseModel):
    handle: str = Field(min_length=2, max_length=100)
    category: str = Field(default="general", max_length=100)
    followers: int = Field(default=0, ge=0)
    engagement_rate: float = Field(default=0, ge=0)


class InfluencerUpdate(BaseModel):
    category: str | None = Field(default=None, max_length=100)
    followers: int | None = Field(default=None, ge=0)
    engagement_rate: float | None = Field(default=None, ge=0)
    verified: bool | None = None


class InfluencerOut(BaseModel):
    id: str
    handle: str
    category: str
    verified: bool
    followers: int
    engagement_rate: float

