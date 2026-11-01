"""Process-wide runtime: DB engine, matcher, classifiers, counters, signer, clock."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from aside_ads.bots import RateLimiter
from aside_ads.classify import CategoryClassifier, build_sensitive_guard, build_topic_classifier
from aside_ads.config import Settings
from aside_ads.counters import Counters, build_counters
from aside_ads.db import create_all, make_engine, make_sessionmaker
from aside_ads.matching import Matcher, build_matcher
from aside_ads.pacing import local_day
from aside_ads.tokens import Signer

log = logging.getLogger("aside")


class Runtime:
    def __init__(
        self,
        settings: Settings,
        engine: AsyncEngine,
        sessionmaker: async_sessionmaker[AsyncSession],
        matcher: Matcher,
        counters: Counters,
    ) -> None:
        self.settings = settings
        self.engine = engine
        self.sessionmaker = sessionmaker
        self.matcher = matcher
        self.counters = counters
        self.signer = Signer(settings.resolved_secret())
        self.tz = ZoneInfo(settings.timezone)
        self.guard: CategoryClassifier = build_sensitive_guard(
            settings.sensitive_category_list,
            matcher,
            settings.sensitive_threshold,
            settings.sensitive_terms_file,
        )
        self.topics: CategoryClassifier = build_topic_classifier(matcher, settings.topic_threshold)
        self.rate_limiter = RateLimiter(counters, settings.rate_limit_per_minute)
        self.clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)
        self.last_reservation_purge = 0.0  # monotonic time of the last expired-reservation purge

    @property
    def relevance_threshold(self) -> float:
        t = self.settings.relevance_threshold
        return self.matcher.default_threshold if t is None else t

    def now(self) -> datetime:
        return self.clock()

    def day(self, when: datetime | None = None) -> str:
        return local_day(when or self.now(), self.tz)

    @classmethod
    async def create(cls, settings: Settings, matcher: Matcher | None = None) -> Runtime:
        settings.ensure_data_dir()
        engine = make_engine(settings.resolved_database_url)
        if settings.is_sqlite:
            await create_all(engine)
        if matcher is None:
            matcher = build_matcher(
                settings.matcher, settings.embedding_model, str(settings.data_dir / "models")
            )
        log.info("matcher=%s relevance_threshold=%s", matcher.name, settings.relevance_threshold)
        rt = cls(settings, engine, make_sessionmaker(engine), matcher, build_counters(settings.redis_url))
        return rt

    async def close(self) -> None:
        await self.counters.close()
        await self.engine.dispose()
