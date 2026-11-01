"""Budget pacing.

Even pacing spreads a daily budget across the day: by a given time of day a
campaign may have spent at most ``daily_budget x (fraction_of_day_elapsed +
buffer)``. Ahead of that line it sits out auctions until time catches up. The
hard guarantee against overspend lives in ``tracking.charge``; pacing only
shapes delivery.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo


def local_day(now_utc: datetime, tz: ZoneInfo) -> str:
    return now_utc.astimezone(tz).strftime("%Y-%m-%d")


def day_fraction(now_utc: datetime, tz: ZoneInfo) -> float:
    local = now_utc.astimezone(tz)
    seconds = local.hour * 3600 + local.minute * 60 + local.second + local.microsecond / 1e6
    return seconds / 86400.0


def pacing_allows(
    spend_today_micros: int,
    daily_budget_micros: int | None,
    fraction_of_day: float,
    buffer: float,
    mode: str = "even",
) -> bool:
    if daily_budget_micros is None or mode != "even":
        return True
    allowance = daily_budget_micros * min(1.0, fraction_of_day + buffer)
    return spend_today_micros < allowance


def budget_allows(
    *,
    unit_cost_micros: int,
    spend_today_micros: int,
    daily_budget_micros: int | None,
    spend_total_micros: int,
    total_budget_micros: int | None,
) -> bool:
    """True if at least one more billable unit at full bid fits in both budgets."""
    unit = max(unit_cost_micros, 1)
    if daily_budget_micros is not None and spend_today_micros + unit > daily_budget_micros:
        return False
    return not (total_budget_micros is not None and spend_total_micros + unit > total_budget_micros)
