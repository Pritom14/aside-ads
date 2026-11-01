"""Request/response models for the public and admin APIs."""

from __future__ import annotations

from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

MICROS = Decimal(1_000_000)


def to_micros(value: Decimal | float | str | None) -> int | None:
    if value is None or value == "":
        return None
    return int((Decimal(str(value)) * MICROS).to_integral_value(rounding=ROUND_HALF_UP))


def from_micros(value: int | None) -> float | None:
    return None if value is None else float(Decimal(value) / MICROS)


def _http_url(v: str | None) -> str | None:
    if v is None or v == "":
        return None
    p = urlparse(v)
    if p.scheme not in ("http", "https") or not p.netloc:
        raise ValueError("must be an absolute http(s) URL")
    return v


# --------------------------------------------------------------------------- serving


class ChatMessage(BaseModel):
    role: str = "user"
    content: str = Field(max_length=20_000)


class Geo(BaseModel):
    """Coarse location, only if your app already knows it. Never required."""

    country: str | None = Field(default=None, min_length=2, max_length=2, description="ISO 3166-1 alpha-2")
    region: str | None = Field(default=None, max_length=64)
    lat: float | None = Field(default=None, ge=-90, le=90)
    lng: float | None = Field(default=None, ge=-180, le=180)


class ServeRequest(BaseModel):
    """Conversation context is used in memory to pick an ad and then discarded. It is never stored."""

    context: str | list[ChatMessage] = Field(
        description="Recent conversation: a string, or a list of {role, content} messages (last 8 are used)."
    )
    placement: str = Field(default="default", max_length=64)
    user_id: str | None = Field(
        default=None, max_length=256, description="Opaque end-user or session id; hashed before any use."
    )
    geo: Geo | None = None
    categories: list[str] = Field(
        default_factory=list, max_length=20, description="Topic hints from your app."
    )
    sensitive: bool = Field(default=False, description="Set true to force no ad for this request.")

    @field_validator("context")
    @classmethod
    def _limit(cls, v: str | list[ChatMessage]) -> str | list[ChatMessage]:
        if isinstance(v, str) and len(v) > 50_000:
            raise ValueError("context too long (max 50000 characters)")
        if isinstance(v, list) and len(v) > 100:
            raise ValueError("too many messages (max 100)")
        return v

    def context_text(self) -> str:
        if isinstance(self.context, str):
            return self.context
        return "\n".join(m.content for m in self.context[-8:])


class SponsoredAd(BaseModel):
    id: str
    campaign_id: str
    label: Literal["Sponsored"] = "Sponsored"
    advertiser: str
    headline: str
    body: str
    cta: str
    url: str = Field(description="Signed click URL; redirects to the advertiser.")
    display_url: str
    image_url: str | None = None
    impression_url: str = Field(description="Fetch (GET or POST) when the card is shown.")
    relevance: float


class ServeResponse(BaseModel):
    request_id: str
    ad: SponsoredAd | None
    reason: str | None = None


class ImpressionRequest(BaseModel):
    token: str = Field(max_length=4096)


class TrackResponse(BaseModel):
    status: str
    billed: bool = False


class ConversionRequest(BaseModel):
    click_id: str = Field(max_length=64)
    idempotency_key: str | None = Field(default=None, max_length=128)
    value: Decimal | None = Field(default=None, ge=0)


class ConversionResponse(BaseModel):
    status: str  # recorded | duplicate
    click_id: str
    campaign_id: str


# --------------------------------------------------------------------------- admin


class AdvertiserIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    website: str | None = None

    @field_validator("website")
    @classmethod
    def _website(cls, v: str | None) -> str | None:
        return _http_url(v)


class AdvertiserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    name: str
    website: str | None
    created_at: datetime


class PublisherIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    domain: str | None = None


class PlacementIn(BaseModel):
    key: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9][a-z0-9_-]*$")
    name: str = ""
    floor_cpm: Decimal | None = Field(default=None, ge=0)


class PlacementOut(BaseModel):
    id: str
    key: str
    name: str
    floor_cpm: float | None


class PublisherOut(BaseModel):
    id: str
    name: str
    domain: str | None
    placements: list[PlacementOut]


class Schedule(BaseModel):
    days: list[int] | None = Field(default=None, description="0=Monday ... 6=Sunday")
    start_hour: int | None = Field(default=None, ge=0, le=23)
    end_hour: int | None = Field(default=None, ge=1, le=24)
    timezone: str | None = None

    @field_validator("days")
    @classmethod
    def _days(cls, v: list[int] | None) -> list[int] | None:
        if v is not None and any(d < 0 or d > 6 for d in v):
            raise ValueError("days must be 0..6")
        return v

    @field_validator("timezone")
    @classmethod
    def _tz(cls, v: str | None) -> str | None:
        if v:
            from zoneinfo import ZoneInfo

            ZoneInfo(v)
        return v


def _clean_list(v: Any, *, upper: bool = False) -> list[str]:
    if v is None:
        return []
    if isinstance(v, str):
        v = [p for chunk in v.splitlines() for p in chunk.split(",")]
    out = []
    for item in v:
        s = str(item).strip()
        if s:
            out.append(s.upper() if upper else s.lower())
    return list(dict.fromkeys(out))


class CampaignBase(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    status: Literal["active", "paused", "archived"] | None = None
    bid_type: Literal["cpc", "cpm"] | None = None
    bid: Decimal | None = Field(default=None, gt=0, description="CPC: per click. CPM: per 1000 impressions.")
    daily_budget: Decimal | None = Field(default=None, ge=0)
    total_budget: Decimal | None = Field(default=None, ge=0)
    pacing: Literal["even", "asap"] | None = None
    freq_cap: int | None = Field(default=None, ge=1)
    freq_cap_window_hours: float | None = Field(default=None, gt=0)
    targeting_text: str | None = Field(default=None, max_length=5000)
    min_relevance: float | None = Field(default=None, ge=0, le=1)
    keywords_include: list[str] | None = None
    keywords_exclude: list[str] | None = None
    categories_include: list[str] | None = None
    categories_exclude: list[str] | None = None
    placements: list[str] | None = None
    countries: list[str] | None = None
    regions: list[str] | None = None
    geo_lat: float | None = Field(default=None, ge=-90, le=90)
    geo_lng: float | None = Field(default=None, ge=-180, le=180)
    geo_radius_km: float | None = Field(default=None, gt=0)
    start_at: datetime | None = None
    end_at: datetime | None = None
    schedule: Schedule | None = None

    @field_validator(
        "keywords_include",
        "keywords_exclude",
        "categories_include",
        "categories_exclude",
        "placements",
        "regions",
        mode="before",
    )
    @classmethod
    def _lists(cls, v: Any) -> list[str] | None:
        return None if v is None else _clean_list(v)

    @field_validator("countries", mode="before")
    @classmethod
    def _countries(cls, v: Any) -> list[str] | None:
        return None if v is None else _clean_list(v, upper=True)


class CampaignIn(CampaignBase):
    advertiser_id: str
    name: str = Field(min_length=1, max_length=200)
    bid_type: Literal["cpc", "cpm"]
    bid: Decimal = Field(gt=0)
    status: Literal["active", "paused", "archived"] = "active"
    pacing: Literal["even", "asap"] = "even"

    @model_validator(mode="after")
    def _check(self) -> CampaignIn:
        if self.start_at and self.end_at and self.end_at <= self.start_at:
            raise ValueError("end_at must be after start_at")
        return self


class CampaignPatch(CampaignBase):
    """All fields optional. Send ``null`` budgets with ``clear_*`` flags to remove them."""

    clear_daily_budget: bool = False
    clear_total_budget: bool = False


class CreativeIn(BaseModel):
    headline: str = Field(min_length=1, max_length=90)
    body: str = Field(default="", max_length=300)
    cta: str = Field(default="Learn more", min_length=1, max_length=40)
    url: str
    image_url: str | None = None
    status: Literal["active", "paused"] = "active"

    @field_validator("url")
    @classmethod
    def _url(cls, v: str) -> str:
        out = _http_url(v)
        if not out:
            raise ValueError("url is required")
        return out

    @field_validator("image_url")
    @classmethod
    def _image(cls, v: str | None) -> str | None:
        return _http_url(v)


class CreativePatch(BaseModel):
    headline: str | None = Field(default=None, min_length=1, max_length=90)
    body: str | None = Field(default=None, max_length=300)
    cta: str | None = Field(default=None, min_length=1, max_length=40)
    url: str | None = None
    image_url: str | None = None
    status: Literal["active", "paused"] | None = None

    @field_validator("url", "image_url")
    @classmethod
    def _urls(cls, v: str | None) -> str | None:
        return _http_url(v)


class CreativeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    campaign_id: str
    headline: str
    body: str
    cta: str
    url: str
    image_url: str | None
    status: str


class CampaignOut(BaseModel):
    id: str
    advertiser_id: str
    name: str
    status: str
    bid_type: str
    bid: float
    daily_budget: float | None
    total_budget: float | None
    pacing: str
    freq_cap: int | None
    freq_cap_window_hours: float
    targeting_text: str
    min_relevance: float | None
    keywords_include: list[str]
    keywords_exclude: list[str]
    categories_include: list[str]
    categories_exclude: list[str]
    placements: list[str]
    countries: list[str]
    regions: list[str]
    geo_lat: float | None
    geo_lng: float | None
    geo_radius_km: float | None
    start_at: datetime | None
    end_at: datetime | None
    schedule: dict[str, Any] | None
    spend_total: float
    spend_today: float
    creatives: list[CreativeOut]


class KeyIn(BaseModel):
    role: Literal["admin", "publisher", "advertiser"]
    name: str = ""
    publisher_id: str | None = None
    advertiser_id: str | None = None


class KeyOut(BaseModel):
    id: str
    role: str
    name: str
    prefix: str
    publisher_id: str | None
    advertiser_id: str | None
    created_at: datetime
    revoked_at: datetime | None
    key: str | None = Field(default=None, description="Plaintext, returned once at creation.")


class StatRow(BaseModel):
    campaign_id: str
    day: str
    impressions: int
    clicks: int
    ctr: float
    conversions: int
    spend: float
    conversion_value: float
