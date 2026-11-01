"""Hard targeting filters applied before relevance scoring."""

from __future__ import annotations

import math
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from aside_ads.matching import ContextAnalysis
from aside_ads.models import Campaign
from aside_ads.text import any_phrase


def as_utc(dt: datetime | None) -> datetime | None:
    """SQLite returns naive datetimes; everything is stored in UTC."""
    if dt is None:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * r * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def schedule_allows(c: Campaign, now_utc: datetime, default_tz: ZoneInfo) -> bool:
    start, end = as_utc(c.start_at), as_utc(c.end_at)
    if start and now_utc < start:
        return False
    if end and now_utc >= end:
        return False
    sched = c.schedule or {}
    if not sched:
        return True
    tz = ZoneInfo(sched["timezone"]) if sched.get("timezone") else default_tz
    local = now_utc.astimezone(tz)
    days = sched.get("days")
    if days is not None and local.weekday() not in days:
        return False
    start_h, end_h = sched.get("start_hour"), sched.get("end_hour")
    if start_h is not None and end_h is not None:
        h = local.hour
        # Windows may wrap midnight, e.g. 22 -> 6.
        inside = start_h <= h < end_h if start_h <= end_h else (h >= start_h or h < end_h)
        if not inside:
            return False
    return True


def geo_allows(
    c: Campaign, country: str | None, region: str | None, lat: float | None, lng: float | None
) -> bool:
    if c.countries and (not country or country.upper() not in {x.upper() for x in c.countries}):
        return False
    if c.regions and (not region or region.lower() not in {x.lower() for x in c.regions}):
        return False
    if c.geo_radius_km and c.geo_lat is not None and c.geo_lng is not None:
        if lat is None or lng is None:
            return False
        if haversine_km(lat, lng, c.geo_lat, c.geo_lng) > c.geo_radius_km:
            return False
    return True


def keywords_allow(c: Campaign, ctx: ContextAnalysis) -> bool:
    if c.keywords_exclude and any_phrase(ctx.phrase_text, c.keywords_exclude):
        return False
    return not (c.keywords_include and not any_phrase(ctx.phrase_text, c.keywords_include))


def categories_allow(c: Campaign, topics: set[str]) -> bool:
    if c.categories_exclude and topics.intersection(c.categories_exclude):
        return False
    return not (c.categories_include and not topics.intersection(c.categories_include))


def placement_allows(c: Campaign, placement_key: str) -> bool:
    return not c.placements or placement_key in c.placements
