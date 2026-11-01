from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Async driver for app runtime (SQLAlchemy async engine)
    DATABASE_URL: str = "postgresql+asyncpg://postgres:postgres@127.0.0.1:5433/ads_ai"

    JWT_SECRET_KEY: str = "dev-secret-change-me"
    JWT_ALGORITHM: str = "HS256"
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24  # 24h

    @property
    def alembic_database_url(self) -> str:
        """
        Alembic uses a sync SQLAlchemy engine. Convert async URLs to sync URLs.
        """
        url = self.DATABASE_URL
        return url.replace("+asyncpg", "")


settings = Settings()

