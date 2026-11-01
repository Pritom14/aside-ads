"""Budget reservations: hold a served ad's cost until it is billed.

Budgets are charged when the billable event arrives (impression for CPM, click
for CPC), but ads are chosen earlier, at serve time. Without a hold, a burst of
concurrent serves all see the same remaining budget and the server delivers more
ads than the budget pays for. Reservations close that gap:

* **CPM.** When a CPM ad wins, its impression cost (at the clearing price) is
  reserved atomically before the ad is returned. The impression converts the
  reservation into spend. If no impression arrives within ``ASIDE_RESERVATION_TTL``
  seconds (default 120) the reservation expires and stops counting. A burst can
  therefore never serve more CPM ads than the budget can bill.
* **CPC.** Clicks are rare and can arrive long after the serve, so holding a full
  click price per serve would starve delivery. Instead each serve holds an
  expected cost, ``price x ASIDE_CPC_EXPECTED_CTR`` (default 5%), for
  ``ASIDE_CPC_EXPOSURE_TTL`` seconds (default 900). Serving stops while spend plus
  outstanding exposure plus one full click would exceed the budget. The click
  converts its hold into the full price. This bounds, but does not eliminate,
  free clicks: if the real CTR of the outstanding serves is above the estimate,
  or a click arrives after its hold expired and the budget is gone, that click is
  not billed. Raising the expected CTR or the TTL lowers free clicks and delivers
  less near the end of a budget.

Serve-time budget and pacing checks count every outstanding (unexpired)
reservation as if it were spend. Charging is stricter about what it protects:
an impression or click is billed only if its cost fits next to the other
outstanding CPM reservations, which are firm (each is exactly one impression
already on screen). CPC holds are estimates, so they never stop a real click
from being billed. A late impression (its reservation already expired) is
therefore billed if the budget still allows after counting everyone else's
CPM reservations, and dropped (recorded, not billed) otherwise. An event is
never billed twice: events are unique per request id, and the reservation row
is deleted when it converts.

Concurrency: every change that must see a consistent total (reserve, charge)
first takes a write lock on the campaign row, then reads the outstanding total.
On SQLite the first write takes the database write lock; on PostgreSQL the row
lock serializes writers per campaign and each later statement in READ COMMITTED
sees everything the previous lock holder committed.
"""

from __future__ import annotations

import math
import time
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from aside_ads.models import BudgetReservation, Campaign
from aside_ads.pacing import budget_allows, day_fraction, pacing_allows
from aside_ads.runtime import Runtime

PURGE_INTERVAL_S = 60.0


async def lock_campaign(s: AsyncSession, campaign_id: str) -> None:
    """Take the write lock for ``campaign_id`` as the first statement of a transaction."""
    if s.bind.dialect.name == "postgresql":
        await s.execute(select(Campaign.id).where(Campaign.id == campaign_id).with_for_update())
    else:
        # SQLite: a write as the first statement takes the database write lock up front,
        # so the reads that follow cannot go stale before this transaction writes.
        await s.execute(
            update(Campaign)
            .where(Campaign.id == campaign_id)
            .values(spend_total_micros=Campaign.spend_total_micros, updated_at=Campaign.updated_at)
            .execution_options(synchronize_session=False)
        )


async def outstanding(s: AsyncSession, campaign_id: str, now: datetime, kind: str | None = None) -> int:
    """Total unexpired reservations for one campaign (optionally one kind), in micros."""
    query = select(func.coalesce(func.sum(BudgetReservation.cost_micros), 0)).where(
        BudgetReservation.campaign_id == campaign_id, BudgetReservation.expires_at > now
    )
    if kind is not None:
        query = query.where(BudgetReservation.kind == kind)
    return int(await s.scalar(query) or 0)


async def outstanding_by_campaign(s: AsyncSession, now: datetime) -> dict[str, int]:
    """Unexpired reservations per campaign (a read for filtering, not a guarantee)."""
    rows = await s.execute(
        select(BudgetReservation.campaign_id, func.sum(BudgetReservation.cost_micros))
        .where(BudgetReservation.expires_at > now)
        .group_by(BudgetReservation.campaign_id)
    )
    return {cid: int(total or 0) for cid, total in rows.all()}


def hold_for(rt: Runtime, bid_type: str, price_micros: int, unit_cost: int) -> tuple[int, int] | None:
    """(cost to hold, ttl seconds) for a served ad, or None if this kind of hold is off."""
    st = rt.settings
    if bid_type == "cpm":
        ttl = min(st.reservation_ttl, st.impression_token_ttl)
        return (unit_cost, ttl) if ttl > 0 else None
    ttl = min(st.cpc_exposure_ttl, st.click_token_ttl)
    if ttl <= 0 or st.cpc_expected_ctr <= 0:
        return None
    return max(1, math.ceil(price_micros * st.cpc_expected_ctr - 1e-9)), ttl


async def reserve(
    rt: Runtime,
    campaign: Campaign,
    request_id: str,
    *,
    hold_micros: int,
    full_unit_micros: int,
    ttl_seconds: int,
) -> bool:
    """Atomically reserve ``hold_micros`` for one served ad. False if budget or pacing say no.

    The check requires that one full billable unit (``full_unit_micros``: the
    impression cost for CPM, the click price for CPC) still fits on top of current
    spend and every outstanding reservation.
    """
    now = rt.now()
    day = rt.day(now)
    async with rt.sessionmaker() as s, s.begin():
        await lock_campaign(s, campaign.id)
        row = (
            await s.execute(
                select(
                    Campaign.status,
                    Campaign.spend_total_micros,
                    Campaign.spend_today_micros,
                    Campaign.spend_day,
                    Campaign.daily_budget_micros,
                    Campaign.total_budget_micros,
                    Campaign.pacing,
                ).where(Campaign.id == campaign.id)
            )
        ).one_or_none()
        if row is None or row.status != "active":
            return False
        held = await outstanding(s, campaign.id, now)
        spend_today = (row.spend_today_micros if row.spend_day == day else 0) + held
        if not budget_allows(
            unit_cost_micros=max(hold_micros, full_unit_micros),
            spend_today_micros=spend_today,
            daily_budget_micros=row.daily_budget_micros,
            spend_total_micros=row.spend_total_micros + held,
            total_budget_micros=row.total_budget_micros,
        ):
            return False
        if not pacing_allows(
            spend_today,
            row.daily_budget_micros,
            day_fraction(now, rt.tz),
            rt.settings.pacing_buffer,
            row.pacing,
        ):
            return False
        s.add(
            BudgetReservation(
                request_id=request_id,
                campaign_id=campaign.id,
                kind=campaign.bid_type,
                cost_micros=hold_micros,
                expires_at=now + timedelta(seconds=ttl_seconds),
            )
        )
    return True


async def take(s: AsyncSession, request_id: str) -> Any:
    """Delete this request's reservation (expired or not) inside a charging transaction."""
    return await s.execute(delete(BudgetReservation).where(BudgetReservation.request_id == request_id))


async def purge_expired(rt: Runtime, *, force: bool = False) -> None:
    """Delete expired reservations, at most once a minute unless ``force``.

    Expired rows already stop counting, so this only keeps the table small.
    """
    mono = time.monotonic()
    if not force and mono - rt.last_reservation_purge < PURGE_INTERVAL_S:
        return
    rt.last_reservation_purge = mono
    async with rt.sessionmaker() as s, s.begin():
        await s.execute(delete(BudgetReservation).where(BudgetReservation.expires_at <= rt.now()))
