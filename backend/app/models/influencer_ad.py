from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class InfluencerAd(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "influencer_ads"

    campaign_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("campaigns.id"), nullable=False, index=True)

    headline: Mapped[str] = mapped_column(String(120), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    cta_text: Mapped[str | None] = mapped_column(String(50), nullable=True)
    cta_url: Mapped[str | None] = mapped_column(String(255), nullable=True)

    campaign = relationship("Campaign", back_populates="influencer_ads")

