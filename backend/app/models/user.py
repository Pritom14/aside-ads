from __future__ import annotations

from sqlalchemy import Boolean, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class User(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(320), unique=True, index=True, nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(50), nullable=False, default="business")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    businesses = relationship("Business", back_populates="owner", cascade="all,delete-orphan")
    influencer = relationship("Influencer", back_populates="owner", uselist=False, cascade="all,delete-orphan")

