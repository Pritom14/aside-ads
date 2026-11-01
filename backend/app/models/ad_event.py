from __future__ import annotations

import uuid

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.models.base import Base, UUIDPrimaryKeyMixin


class AdEvent(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "ad_events"

    campaign_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("campaigns.id"), nullable=False, index=True)
    ad_type: Mapped[str] = mapped_column(String(30), nullable=False)  # business|influencer
    ad_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)

    event_type: Mapped[str] = mapped_column(String(30), nullable=False)  # impression|click
    occurred_at = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), index=True)
    meta: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

