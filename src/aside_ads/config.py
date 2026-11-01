"""Runtime settings, read from environment variables (prefix ``ASIDE_``) or a ``.env`` file."""

from __future__ import annotations

import os
import secrets
from decimal import Decimal
from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_SENSITIVE_CATEGORIES = (
    "self_harm,medical_emergency,serious_illness,minors,abuse_violence,grief,addiction,financial_distress"
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="ASIDE_", env_file=".env", extra="ignore", populate_by_name=True
    )

    # Storage -----------------------------------------------------------------
    data_dir: Path = Path(".aside")
    database_url: str | None = Field(
        default=None, validation_alias=AliasChoices("ASIDE_DATABASE_URL", "DATABASE_URL", "database_url")
    )
    redis_url: str | None = Field(
        default=None, validation_alias=AliasChoices("ASIDE_REDIS_URL", "REDIS_URL", "redis_url")
    )
    auto_migrate: bool = True

    # Security ----------------------------------------------------------------
    # HMAC key for tracking tokens, user-id hashing and dashboard sessions.
    # If unset, a random key is generated once and stored in <data_dir>/secret.key.
    secret_key: str | None = None
    # Public base URL used in impression/click links. Defaults to the request's base URL.
    public_url: str | None = None
    trust_proxy_headers: bool = False

    # Matching ----------------------------------------------------------------
    matcher: Literal["auto", "embedding", "bm25"] = "auto"
    embedding_model: str = "BAAI/bge-small-en-v1.5"
    # Minimum relevance (0..1) to serve an ad. None uses the matcher's calibrated default
    # (0.65 for bge-small cosine similarity, 0.5 for the BM25 fallback).
    relevance_threshold: float | None = None
    sensitive_categories: str = DEFAULT_SENSITIVE_CATEGORIES
    sensitive_terms_file: Path | None = None
    sensitive_threshold: float = 0.70
    topic_threshold: float = 0.68
    max_context_chars: int = 4000

    # Auction, budgets, pacing ----------------------------------------------------
    currency: str = "USD"
    floor_cpm: Decimal = Decimal("0.50")
    default_ctr: float = 0.01
    pacing_buffer: float = 1 / 24
    timezone: str = "UTC"

    # Tracking ----------------------------------------------------------------
    impression_token_ttl: int = 3600
    click_token_ttl: int = 86400
    conversion_window_days: int = 30
    rate_limit_per_minute: int = 120
    click_id_param: str = "aside_click_id"

    @property
    def resolved_database_url(self) -> str:
        if self.database_url:
            url = self.database_url
            # Accept plain driver names and pick the async driver.
            if url.startswith("postgres://"):
                url = "postgresql+asyncpg://" + url[len("postgres://") :]
            elif url.startswith("postgresql://"):
                url = "postgresql+asyncpg://" + url[len("postgresql://") :]
            elif url.startswith("sqlite:///"):
                url = "sqlite+aiosqlite:///" + url[len("sqlite:///") :]
            return url
        return f"sqlite+aiosqlite:///{self.data_dir / 'aside.db'}"

    @property
    def is_sqlite(self) -> bool:
        return self.resolved_database_url.startswith("sqlite")

    @property
    def sensitive_category_list(self) -> list[str]:
        return [c.strip() for c in self.sensitive_categories.split(",") if c.strip()]

    @property
    def floor_cpm_micros(self) -> int:
        return int(self.floor_cpm * 1_000_000)

    def ensure_data_dir(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)

    def resolved_secret(self) -> bytes:
        """Return the HMAC secret, creating ``<data_dir>/secret.key`` on first use."""
        if self.secret_key:
            return self.secret_key.encode()
        self.ensure_data_dir()
        path = self.data_dir / "secret.key"
        if not path.exists():
            try:
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError:
                pass  # another process created it first
            else:
                with os.fdopen(fd, "w") as fh:
                    fh.write(secrets.token_urlsafe(48))
        return path.read_text().strip().encode()
