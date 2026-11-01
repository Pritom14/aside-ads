from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, Integer
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TEXT
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class CampaignTargeting(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "campaign_targeting"

    campaign_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("campaigns.id"), unique=True, nullable=False, index=True
    )

    radius_km: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    keywords: Mapped[list[str]] = mapped_column(ARRAY(TEXT), nullable=False, default=list)

    # Flexible bucket for MVP: time windows, language, etc.
    rules: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    campaign = relationship("Campaign", back_populates="targeting")

