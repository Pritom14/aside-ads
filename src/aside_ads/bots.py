"""Basic invalid-traffic filtering: user-agent heuristics and a per-IP rate limit.

This catches crawlers, link-preview fetchers and scripted traffic. It is not a
full fraud system; see the README roadmap.
"""

from __future__ import annotations

import re

from aside_ads.counters import Counters

_BOT_UA = re.compile(
    r"bot\b|bot/|crawl|spider|slurp|scrap|curl/|wget/|python-requests|python-urllib|python-httpx|"
    r"aiohttp|go-http-client|okhttp|java/|libwww|httpclient|headless|phantomjs|selenium|puppeteer|"
    r"playwright|lighthouse|facebookexternalhit|whatsapp|slackbot|telegrambot|discordbot|"
    r"linkpreview|preview|monitor|uptime|pingdom|validator|feedfetcher|^mozilla/4\.0$",
    re.IGNORECASE,
)


def is_bot_user_agent(ua: str | None) -> bool:
    if not ua or len(ua) < 10:
        return True
    return bool(_BOT_UA.search(ua))


class RateLimiter:
    def __init__(self, counters: Counters, per_minute: int) -> None:
        self.counters = counters
        self.per_minute = per_minute

    async def allow(self, ip: str | None) -> bool:
        if self.per_minute <= 0:
            return True
        return await self.counters.incr(f"rl:{ip or 'unknown'}", 60) <= self.per_minute
