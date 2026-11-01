"""The Alembic migrations (used for PostgreSQL) must produce exactly the ORM schema."""

from __future__ import annotations

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy.ext.asyncio import create_async_engine

from aside_ads.config import Settings
from aside_ads.models import Base
from tests.conftest import TEST_DATABASE_URL

pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL, reason="set ASIDE_TEST_DATABASE_URL to check migrations on PostgreSQL"
)


async def test_migrations_match_models() -> None:
    url = Settings(_env_file=None, database_url=TEST_DATABASE_URL).resolved_database_url
    engine = create_async_engine(url)
    try:
        async with engine.connect() as conn:
            diff = await conn.run_sync(
                lambda sync_conn: compare_metadata(MigrationContext.configure(sync_conn), Base.metadata)
            )
    finally:
        await engine.dispose()
    assert diff == []
