"""Impression/click recording, atomic budget charging and conversion postbacks.

Integrity rules:

* Each (kind, request_id) is recorded at most once: a unique constraint plus
  ``INSERT ... ON CONFLICT DO NOTHING`` makes duplicates and concurrent retries no-ops.
* Charging is a single conditional ``UPDATE`` on the campaign row that only
  succeeds if the cost, plus the other outstanding CPM reservations, still
  fits in both the daily and lifetime budgets. The transaction locks the campaign
  row before it reads the reservations (see ``aside_ads.reservations``). Spend can
  therefore never exceed a budget, no matter how many requests race, and an event
  whose reservation expired cannot take budget already held for other served ads.
* Conversions are unique on (click_id, idempotency_key).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from sqlalchemy import case, or_, select, update

from aside_ads.auction import impression_cost
from aside_ads.db import insert_for
from aside_ads.models import Campaign, Conversion, DailyStat, Event
from aside_ads.reservations import outstanding, take
from aside_ads.runtime import Runtime
from aside_ads.targeting import as_utc


@dataclass
class TrackOutcome:
    status: str  # counted | duplicate
    billed: bool = False
    cost_micros: int = 0


def charge_statement(campaign_id: str, cost: int, day: str, reserved: int = 0) -> Any:
    """Add ``cost`` to spend only if spend + cost + ``reserved`` fits both budgets."""
    today_spend = case((Campaign.spend_day == day, Campaign.spend_today_micros), else_=0)
    need = cost + reserved
    return (
        update(Campaign)
        .where(
            Campaign.id == campaign_id,
            or_(
                Campaign.total_budget_micros.is_(None),
                Campaign.spend_total_micros + need <= Campaign.total_budget_micros,
            ),
            or_(Campaign.daily_budget_micros.is_(None), today_spend + need <= Campaign.daily_budget_micros),
        )
        .values(
            spend_total_micros=Campaign.spend_total_micros + cost,
            spend_today_micros=today_spend + cost,
            spend_day=day,
        )
        .execution_options(synchronize_session=False)
    )


def _daily_upsert(session: Any, campaign_id: str, day: str, **increments: int) -> Any:
    ins = insert_for(session, DailyStat).values(campaign_id=campaign_id, day=day, **increments)
    return ins.on_conflict_do_update(
        index_elements=["campaign_id", "day"],
        set_={k: getattr(DailyStat, k) + getattr(ins.excluded, k) for k in increments},
    )


async def record_event(rt: Runtime, kind: str, claims: dict[str, Any]) -> TrackOutcome:
    """Record an impression or click from verified token claims, charging the campaign if billable."""
    assert kind in ("impression", "click")
    now = rt.now()
    day = rt.day(now)
    campaign_id = claims["c"]
    price = int(claims.get("pr", 0))
    cost = 0
    if kind == "impression" and claims.get("bt") == "cpm":
        cost = impression_cost(price)
    elif kind == "click" and claims.get("bt") == "cpc":
        cost = price

    async with rt.sessionmaker() as s, s.begin():
        # The first statement is a write, so on SQLite the transaction takes the write lock up front.
        ins = (
            insert_for(s, Event)
            .values(
                kind=kind,
                request_id=claims["r"],
                campaign_id=campaign_id,
                creative_id=claims["a"],
                publisher_id=claims.get("p"),
                placement_key=claims.get("pl"),
                user_hash=claims.get("u"),
                country=claims.get("g"),
                region=claims.get("rg"),
                created_at=now,
                cost_micros=0,
                billed=False,
            )
            .on_conflict_do_nothing(index_elements=["kind", "request_id"])
        )
        if (await s.execute(ins)).rowcount == 0:
            return TrackOutcome("duplicate")

        # Writing the campaign row next takes its lock (PostgreSQL), so the reservation
        # total read below is complete: every reserve() for this campaign locks it first.
        counter = Campaign.impressions_total if kind == "impression" else Campaign.clicks_total
        await s.execute(
            update(Campaign)
            .where(Campaign.id == campaign_id)
            .values({counter: counter + 1})
            .execution_options(synchronize_session=False)
        )
        billed = False
        if cost > 0:
            # Convert this ad's reservation (if any) into spend. A live CPM reservation already
            # made room for the cost; an expired one did not, so a late event is billed only if
            # it still fits next to the other outstanding CPM reservations. CPC holds are
            # estimates and never block a real click.
            await take(s, claims["r"])
            others = await outstanding(s, campaign_id, now, kind="cpm")
            billed = (await s.execute(charge_statement(campaign_id, cost, day, others))).rowcount == 1
            if billed:
                await s.execute(
                    update(Event)
                    .where(Event.kind == kind, Event.request_id == claims["r"])
                    .values(billed=True, cost_micros=cost)
                    .execution_options(synchronize_session=False)
                )
        incs = {"impressions" if kind == "impression" else "clicks": 1, "spend_micros": cost if billed else 0}
        await s.execute(_daily_upsert(s, campaign_id, day, **incs))
    return TrackOutcome("counted", billed, cost if billed else 0)


class ConversionError(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


@dataclass
class ConversionOutcome:
    status: str  # recorded | duplicate
    campaign_id: str


async def record_conversion(
    rt: Runtime,
    *,
    click_id: str,
    idempotency_key: str | None,
    value_micros: int | None,
    advertiser_scope: str | None,
) -> ConversionOutcome:
    """Record a conversion for a click. ``advertiser_scope`` restricts it to that advertiser's clicks."""
    now = rt.now()
    idem = (idempotency_key or click_id)[:128]
    async with rt.sessionmaker() as s:
        click = (
            await s.execute(select(Event).where(Event.kind == "click", Event.request_id == click_id))
        ).scalar_one_or_none()
        if click is None:
            raise ConversionError(404, "unknown click_id")
        campaign = await s.get(Campaign, click.campaign_id)
        if campaign is None:
            raise ConversionError(404, "campaign no longer exists")
        if advertiser_scope is not None and campaign.advertiser_id != advertiser_scope:
            raise ConversionError(403, "click belongs to another advertiser")
        clicked_at = as_utc(click.created_at)
        if clicked_at and now - clicked_at > timedelta(days=rt.settings.conversion_window_days):
            raise ConversionError(410, "conversion window has passed")
        await s.commit()  # end the read before starting the write transaction

        async with s.begin():
            ins = (
                insert_for(s, Conversion)
                .values(
                    click_id=click_id,
                    idempotency_key=idem,
                    campaign_id=campaign.id,
                    creative_id=click.creative_id,
                    advertiser_id=campaign.advertiser_id,
                    value_micros=value_micros,
                    created_at=now,
                )
                .on_conflict_do_nothing(index_elements=["click_id", "idempotency_key"])
            )
            if (await s.execute(ins)).rowcount == 0:
                return ConversionOutcome("duplicate", campaign.id)
            await s.execute(
                _daily_upsert(
                    s, campaign.id, rt.day(now), conversions=1, conversion_value_micros=value_micros or 0
                )
            )
    return ConversionOutcome("recorded", campaign.id)
