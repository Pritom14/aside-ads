from __future__ import annotations

from pydantic import BaseModel, Field


class BusinessCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    category: str = Field(min_length=1, max_length=100)
    phone: str | None = Field(default=None, max_length=30)
    website: str | None = Field(default=None, max_length=255)
    address: str | None = Field(default=None, max_length=255)
    lat: float | None = None
    lng: float | None = None


class BusinessUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=200)
    category: str | None = Field(default=None, max_length=100)
    phone: str | None = Field(default=None, max_length=30)
    website: str | None = Field(default=None, max_length=255)
    address: str | None = Field(default=None, max_length=255)
    lat: float | None = None
    lng: float | None = None


class BusinessOut(BaseModel):
    id: str
    name: str
    category: str
    phone: str | None
    website: str | None
    address: str | None
    lat: float | None
    lng: float | None

