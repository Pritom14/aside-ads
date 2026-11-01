"""Database models.

Privacy rule for this module: no table stores conversation text or any
embedding derived from it. Events keep ids, a keyed hash of the end-user id,
timestamps and coarse geo (country/region) only when the publisher sends it.
``tests/test_privacy.py`` scans every table to enforce this.

Money is stored as integer micros (1 currency unit = 1_000_000 micros) so that
spend accounting is exact and can be updated atomically in SQL.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def new_id() -> str:
    return uuid.uuid4().hex


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSON, list[str]: JSON}


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class Publisher(TimestampMixin, Base):
    """An AI app that shows ads (usually you)."""

    __tablename__ = "publishers"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    domain: Mapped[str | None] = mapped_column(String(255))

    placements: Mapped[list[Placement]] = relationship(
        back_populates="publisher", cascade="all, delete-orphan", order_by="Placement.key"
    )


class Placement(TimestampMixin, Base):
    """A slot in the publisher's UI where a sponsored card can appear (e.g. ``chat-sidebar``)."""

    __tablename__ = "placements"
    __table_args__ = (UniqueConstraint("publisher_id", "key", name="uq_placement_publisher_key"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    publisher_id: Mapped[str] = mapped_column(ForeignKey("publishers.id", ondelete="CASCADE"), index=True)
    key: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(200), default="")
    floor_cpm_micros: Mapped[int | None] = mapped_column(BigInteger)

    publisher: Mapped[Publisher] = relationship(back_populates="placements")


class Advertiser(TimestampMixin, Base):
    __tablename__ = "advertisers"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    website: Mapped[str | None] = mapped_column(String(2048))

    campaigns: Mapped[list[Campaign]] = relationship(
        back_populates="advertiser", cascade="all, delete-orphan"
    )


class ApiKey(Base):
    """API keys are stored as SHA-256 hashes; the plaintext is shown once at creation."""

    __tablename__ = "api_keys"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(200), default="")
    role: Mapped[str] = mapped_column(String(20))  # admin | publisher | advertiser
    prefix: Mapped[str] = mapped_column(String(24))
    key_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    publisher_id: Mapped[str | None] = mapped_column(ForeignKey("publishers.id", ondelete="CASCADE"))
    advertiser_id: Mapped[str | None] = mapped_column(ForeignKey("advertisers.id", ondelete="CASCADE"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Campaign(TimestampMixin, Base):
    __tablename__ = "campaigns"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    advertiser_id: Mapped[str] = mapped_column(ForeignKey("advertisers.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(20), default="active", index=True)  # active|paused|archived

    # Bidding: cpc = price per click, cpm = price per 1000 impressions. Both in micros.
    bid_type: Mapped[str] = mapped_column(String(3), default="cpm")
    bid_micros: Mapped[int] = mapped_column(BigInteger)
    daily_budget_micros: Mapped[int | None] = mapped_column(BigInteger)
    total_budget_micros: Mapped[int | None] = mapped_column(BigInteger)
    pacing: Mapped[str] = mapped_column(String(10), default="even")  # even | asap

    # Frequency cap per hashed user id: at most freq_cap serves per window.
    freq_cap: Mapped[int | None] = mapped_column(Integer)
    freq_cap_window_s: Mapped[int] = mapped_column(Integer, default=86400)

    # Contextual targeting. targeting_text: one description per line of the conversations
    # this ad fits ("slow postgres queries"). Keywords and categories are hard filters.
    targeting_text: Mapped[str] = mapped_column(Text, default="")
    min_relevance: Mapped[float | None] = mapped_column(Float)
    keywords_include: Mapped[list[str]] = mapped_column(default=list)
    keywords_exclude: Mapped[list[str]] = mapped_column(default=list)
    categories_include: Mapped[list[str]] = mapped_column(default=list)
    categories_exclude: Mapped[list[str]] = mapped_column(default=list)
    placements: Mapped[list[str]] = mapped_column(default=list)  # placement keys; empty = all

    # Optional geo targeting (requires the publisher to pass coarse geo).
    countries: Mapped[list[str]] = mapped_column(default=list)
    regions: Mapped[list[str]] = mapped_column(default=list)
    geo_lat: Mapped[float | None] = mapped_column(Float)
    geo_lng: Mapped[float | None] = mapped_column(Float)
    geo_radius_km: Mapped[float | None] = mapped_column(Float)

    # Optional schedule: start/end plus {"days": [0..6], "start_hour": 9, "end_hour": 18, "timezone": "..."}
    start_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    end_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    schedule: Mapped[dict[str, Any] | None] = mapped_column(JSON)

    # Running totals, updated atomically by aside_ads.tracking.
    spend_total_micros: Mapped[int] = mapped_column(BigInteger, default=0)
    spend_today_micros: Mapped[int] = mapped_column(BigInteger, default=0)
    spend_day: Mapped[str | None] = mapped_column(String(10))
    impressions_total: Mapped[int] = mapped_column(BigInteger, default=0)
    clicks_total: Mapped[int] = mapped_column(BigInteger, default=0)

    advertiser: Mapped[Advertiser] = relationship(back_populates="campaigns")
    creatives: Mapped[list[Creative]] = relationship(
        back_populates="campaign", cascade="all, delete-orphan", order_by="Creative.created_at"
    )


class Creative(TimestampMixin, Base):
    __tablename__ = "creatives"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    campaign_id: Mapped[str] = mapped_column(ForeignKey("campaigns.id", ondelete="CASCADE"), index=True)
    headline: Mapped[str] = mapped_column(String(90))
    body: Mapped[str] = mapped_column(String(300), default="")
    cta: Mapped[str] = mapped_column(String(40), default="Learn more")
    url: Mapped[str] = mapped_column(String(2048))
    image_url: Mapped[str | None] = mapped_column(String(2048))
    status: Mapped[str] = mapped_column(String(20), default="active")  # active | paused

    campaign: Mapped[Campaign] = relationship(back_populates="creatives")


class Event(Base):
    """An impression or click. One row per (kind, request_id): the unique constraint is the dedupe."""

    __tablename__ = "events"
    __table_args__ = (UniqueConstraint("kind", "request_id", name="uq_event_kind_request"),)

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    kind: Mapped[str] = mapped_column(String(12))  # impression | click
    request_id: Mapped[str] = mapped_column(String(32))
    campaign_id: Mapped[str] = mapped_column(String(32), index=True)
    creative_id: Mapped[str] = mapped_column(String(32))
    publisher_id: Mapped[str | None] = mapped_column(String(32))
    placement_key: Mapped[str | None] = mapped_column(String(64))
    cost_micros: Mapped[int] = mapped_column(BigInteger, default=0)
    billed: Mapped[bool] = mapped_column(Boolean, default=False)
    user_hash: Mapped[str | None] = mapped_column(String(32))
    country: Mapped[str | None] = mapped_column(String(2))
    region: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class BudgetReservation(Base):
    """Budget held for a served ad until it is billed or the hold expires.

    One row per served ad (keyed by request id) for campaigns with a budget. CPM
    rows hold the impression cost; CPC rows hold the expected click cost (bid x
    ``ASIDE_CPC_EXPECTED_CTR``). Expired rows no longer count and are purged
    lazily. See ``aside_ads.reservations``.
    """

    __tablename__ = "budget_reservations"
    __table_args__ = (Index("ix_budget_reservations_campaign_expires", "campaign_id", "expires_at"),)

    request_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    campaign_id: Mapped[str] = mapped_column(String(32))
    kind: Mapped[str] = mapped_column(String(3))  # cpm | cpc
    cost_micros: Mapped[int] = mapped_column(BigInteger)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class Conversion(Base):
    """A conversion postback. Unique on (click_id, idempotency_key): duplicates are counted once."""

    __tablename__ = "conversions"
    __table_args__ = (UniqueConstraint("click_id", "idempotency_key", name="uq_conversion_click_idem"),)

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    click_id: Mapped[str] = mapped_column(String(32))
    idempotency_key: Mapped[str] = mapped_column(String(128))
    campaign_id: Mapped[str] = mapped_column(String(32), index=True)
    creative_id: Mapped[str] = mapped_column(String(32))
    advertiser_id: Mapped[str] = mapped_column(String(32))
    value_micros: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class DailyStat(Base):
    """Per-campaign, per-day rollup for reporting (day is in the server's configured timezone)."""

    __tablename__ = "daily_stats"

    campaign_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    day: Mapped[str] = mapped_column(String(10), primary_key=True)
    impressions: Mapped[int] = mapped_column(BigInteger, default=0)
    clicks: Mapped[int] = mapped_column(BigInteger, default=0)
    conversions: Mapped[int] = mapped_column(BigInteger, default=0)
    spend_micros: Mapped[int] = mapped_column(BigInteger, default=0)
    conversion_value_micros: Mapped[int] = mapped_column(BigInteger, default=0)
