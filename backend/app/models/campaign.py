from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class Campaign(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "campaigns"
    __table_args__ = (
        CheckConstraint(
            "(business_id IS NOT NULL AND influencer_id IS NULL) OR (business_id IS NULL AND influencer_id IS NOT NULL)",
            name="ck_campaign_owner_exactly_one",
        ),
    )

    campaign_type: Mapped[str] = mapped_column(String(30), nullable=False, default="business")
    business_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("businesses.id"), nullable=True, index=True)
    influencer_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("influencers.id"), nullable=True, index=True
    )

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="draft")  # draft|active|paused
    daily_budget: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False, default=0)
    start_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    end_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    business = relationship("Business", back_populates="campaigns")
    influencer = relationship("Influencer", back_populates="campaigns")

    business_ads = relationship("BusinessAd", back_populates="campaign", cascade="all,delete-orphan")
    influencer_ads = relationship("InfluencerAd", back_populates="campaign", cascade="all,delete-orphan")
    targeting = relationship(
        "CampaignTargeting", back_populates="campaign", uselist=False, cascade="all,delete-orphan"
    )

