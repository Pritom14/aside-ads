from __future__ import annotations

import uuid

from sqlalchemy import Boolean, ForeignKey, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class Influencer(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "influencers"

    owner_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id"), unique=True, nullable=False, index=True
    )

    handle: Mapped[str] = mapped_column(String(100), nullable=False, unique=True, index=True)
    category: Mapped[str] = mapped_column(String(100), nullable=False, default="general")
    verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    followers: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    engagement_rate: Mapped[float] = mapped_column(Numeric(6, 3), nullable=False, default=0)

    owner = relationship("User", back_populates="influencer")
    campaigns = relationship("Campaign", back_populates="influencer", cascade="all,delete-orphan")

