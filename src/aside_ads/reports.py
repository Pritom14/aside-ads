"""Plain-language reports over Aside's counts, shared by the agent connector and anything else.

Every number here comes from aggregate tables (daily stats, delivery counts, billed events). No
conversation text exists anywhere to report on. Amounts are in the server's currency, as floats.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from aside_ads.auction import impression_cost
from aside_ads.delivery import OUTCOMES
from aside_ads.models import (
    AuditEntry,
    BudgetMiss,
    Campaign,
    DailyStat,
    Event,
    ServeOutcome,
    SponsorRequest,
    TopicCount,
)
from aside_ads.runtime import Runtime
from aside_ads.schemas import from_micros

LOW_CTR = 0.003  # below 0.3% on enough impressions, the creative is probably not working
LOW_CTR_MIN_IMPRESSIONS = 200


def _money(micros: int | None) -> float:
    return round(from_micros(micros or 0) or 0.0, 2)


def _pct(part: float, whole: float) -> float:
    return round(100 * part / whole, 1) if whole else 0.0


def resolve_day(rt: Runtime, day: str | None) -> str:
    """'today', 'yesterday' or YYYY-MM-DD, in the server's timezone."""
    value = (day or "today").strip().lower()
    if value == "today":
        return rt.day()
    if value == "yesterday":
        return rt.day(rt.now() - timedelta(days=1))
    return date.fromisoformat(value).isoformat()


def day_range(rt: Runtime, days: int) -> tuple[str, str]:
    days = max(1, min(int(days), 366))
    now = rt.now()
    return rt.day(now - timedelta(days=days - 1)), rt.day(now)


def _unit_cost(c: Campaign) -> int:
    return impression_cost(c.bid_micros) if c.bid_type == "cpm" else c.bid_micros


async def _ran_out_at(s: AsyncSession, rt: Runtime, c: Campaign, day: str) -> str | None:
    """Local time (HH:MM) at which the campaign's billed spend that day reached its daily budget."""
    if not c.daily_budget_micros:
        return None
    start = datetime.combine(date.fromisoformat(day), time.min, tzinfo=rt.tz).astimezone(timezone.utc)
    rows = (
        await s.execute(
            select(Event.created_at, Event.cost_micros)
            .where(
                Event.campaign_id == c.id,
                Event.billed.is_(True),
                Event.created_at >= start,
                Event.created_at < start + timedelta(days=1),
            )
            .order_by(Event.created_at)
        )
    ).all()
    limit = c.daily_budget_micros - _unit_cost(c)
    spent = 0
    for at, cost in rows:
        spent += cost or 0
        if spent > limit:
            at = at if at.tzinfo else at.replace(tzinfo=timezone.utc)
            return at.astimezone(rt.tz).strftime("%H:%M")
    return None


async def _campaigns(s: AsyncSession, advertiser_id: str | None = None) -> list[Campaign]:
    q = (
        select(Campaign)
        .where(Campaign.status != "archived")
        .options(selectinload(Campaign.creatives), selectinload(Campaign.advertiser))
        .order_by(Campaign.created_at)
    )
    if advertiser_id:
        q = q.where(Campaign.advertiser_id == advertiser_id)
    return list((await s.execute(q)).scalars().all())


async def _misses(s: AsyncSession, day_from: str, day_to: str) -> dict[str, int]:
    q = (
        select(BudgetMiss.campaign_id, func.sum(BudgetMiss.requests))
        .where(BudgetMiss.day >= day_from, BudgetMiss.day <= day_to)
        .group_by(BudgetMiss.campaign_id)
    )
    return {cid: int(n or 0) for cid, n in (await s.execute(q)).all()}


def _local_hhmm(rt: Runtime, at: datetime | None) -> str | None:
    if at is None:
        return None
    at = at if at.tzinfo else at.replace(tzinfo=timezone.utc)
    return at.astimezone(rt.tz).strftime("%H:%M")


async def overview(rt: Runtime, day: str | None = None, advertiser_id: str | None = None) -> dict[str, Any]:
    """One day across campaigns, with the things that need attention spelled out."""
    day = resolve_day(rt, day)
    async with rt.sessionmaker() as s:
        campaigns = await _campaigns(s, advertiser_id)
        ids = [c.id for c in campaigns]
        stats = {
            r.campaign_id: r
            for r in (
                await s.execute(select(DailyStat).where(DailyStat.day == day, DailyStat.campaign_id.in_(ids)))
            )
            .scalars()
            .all()
        }
        misses = await _misses(s, day, day)
        first_miss = {
            m.campaign_id: m.first_at
            for m in (await s.execute(select(BudgetMiss).where(BudgetMiss.day == day))).scalars().all()
        }
        day_requests = (
            await s.execute(select(func.sum(ServeOutcome.requests)).where(ServeOutcome.day == day))
        ).scalar() or 0
        rows, attention = [], []
        if advertiser_id is None:
            waiting = (
                await s.execute(
                    select(func.count()).select_from(SponsorRequest).where(SponsorRequest.status == "pending")
                )
            ).scalar_one()
            if waiting:
                attention.append(
                    f"{waiting} sponsorship request{'s' if waiting != 1 else ''} from your sponsor page "
                    "waiting for review (see sponsor_requests)"
                )
        for c in campaigns:
            st = stats.get(c.id)
            imps, clicks = (st.impressions, st.clicks) if st else (0, 0)
            spend = st.spend_micros if st else 0
            missed = misses.get(c.id, 0)
            notes: list[str] = []
            ran_out = None
            if c.daily_budget_micros and spend > c.daily_budget_micros - _unit_cost(c):
                ran_out = await _ran_out_at(s, rt, c, day)
            held_from = _local_hhmm(rt, first_miss.get(c.id))
            lost = f"{missed:,} more conversations matched it but got no ad from it"
            if ran_out:
                notes.append(
                    f"daily budget ran out at {ran_out}" + (f"; after that {lost}" if missed else "")
                )
            elif missed:
                notes.append(f"from {held_from} its budget or pacing held it back; {lost}")
            if c.status == "active" and not any(cr.status == "active" for cr in c.creatives):
                notes.append("has no active creatives, so it cannot serve")
            if c.total_budget_micros and c.spend_total_micros >= 0.9 * c.total_budget_micros:
                notes.append(f"lifetime budget {_pct(c.spend_total_micros, c.total_budget_micros)}% spent")
            if c.end_at is not None:
                end = c.end_at if c.end_at.tzinfo else c.end_at.replace(tzinfo=timezone.utc)
                if rt.now() <= end <= rt.now() + timedelta(days=3):
                    notes.append(f"ends {end.astimezone(rt.tz):%d %b %H:%M}")
            if c.status == "active" and imps == 0 and day_requests:
                notes.append(
                    "showed no ads: it lost every auction it matched, or its targeting fits nothing people "
                    "asked"
                )
            if imps >= LOW_CTR_MIN_IMPRESSIONS and clicks / imps < LOW_CTR:
                notes.append(f"CTR {_pct(clicks, imps)}% on {imps} impressions: the ad may need new copy")
            rows.append(
                {
                    "id": c.id,
                    "campaign": c.name,
                    "advertiser": c.advertiser.name,
                    "status": c.status,
                    "bid": f"{_money(c.bid_micros)} {c.bid_type.upper()}",
                    "daily_budget": _money(c.daily_budget_micros) if c.daily_budget_micros else None,
                    "spend": _money(spend),
                    "budget_used_pct": _pct(spend, c.daily_budget_micros) if c.daily_budget_micros else None,
                    "budget_ran_out_at": ran_out,
                    "held_back_by_budget_from": held_from,
                    "impressions": imps,
                    "clicks": clicks,
                    "ctr_pct": _pct(clicks, imps),
                    "conversions": st.conversions if st else 0,
                    "missed_for_budget": missed,
                    "notes": notes,
                }
            )
            attention.extend(f"{c.name}: {n}" for n in notes)
        delivery = None if advertiser_id else await _delivery(s, day, day)
    totals = {
        k: sum(r[k] for r in rows) for k in ("impressions", "clicks", "conversions", "missed_for_budget")
    }
    totals["spend"] = round(sum(r["spend"] for r in rows), 2)
    totals["ctr_pct"] = _pct(totals["clicks"], totals["impressions"])
    if delivery:
        for p in delivery:
            if p["requests"] and p["fill_rate_pct"] < 50:
                top = p["no_ad"][0] if p["no_ad"] else None
                why = f"; most common reason: {top['reason']} ({top['meaning']})" if top else ""
                attention.append(
                    f"placement {p['placement']}: ads shown on {p['fill_rate_pct']}% of {p['requests']} "
                    f"requests{why}"
                )
    return {
        "day": day,
        "timezone": rt.settings.timezone,
        "currency": rt.settings.currency,
        "totals": totals,
        "needs_attention": attention,
        "campaigns": rows,
        "placements": delivery,
    }


async def _delivery(s: AsyncSession, day_from: str, day_to: str) -> list[dict[str, Any]]:
    q = (
        select(
            ServeOutcome.publisher_id,
            ServeOutcome.placement_key,
            ServeOutcome.outcome,
            func.sum(ServeOutcome.requests),
        )
        .where(ServeOutcome.day >= day_from, ServeOutcome.day <= day_to)
        .group_by(ServeOutcome.publisher_id, ServeOutcome.placement_key, ServeOutcome.outcome)
    )
    by_placement: dict[tuple[str, str], dict[str, int]] = {}
    for pub, key, outcome, n in (await s.execute(q)).all():
        by_placement.setdefault((pub, key), {})[outcome] = int(n or 0)
    out = []
    for (_, key), counts in sorted(by_placement.items(), key=lambda kv: -sum(kv[1].values())):
        total = sum(counts.values())
        filled = counts.get("filled", 0)
        no_ad = sorted(((r, n) for r, n in counts.items() if r != "filled"), key=lambda rn: -rn[1])
        out.append(
            {
                "placement": key,
                "requests": total,
                "filled": filled,
                "fill_rate_pct": _pct(filled, total),
                "no_ad": [
                    {"reason": r, "requests": n, "share_pct": _pct(n, total), "meaning": OUTCOMES.get(r, r)}
                    for r, n in no_ad
                ],
            }
        )
    return out


async def _topics(s: AsyncSession, day_from: str, day_to: str) -> list[dict[str, Any]]:
    q = (
        select(TopicCount.topic, func.sum(TopicCount.requests), func.sum(TopicCount.filled))
        .where(TopicCount.day >= day_from, TopicCount.day <= day_to)
        .group_by(TopicCount.topic)
    )
    out = []
    for topic, requests, filled in (await s.execute(q)).all():
        requests, filled = int(requests or 0), int(filled or 0)
        out.append(
            {
                "topic": topic,
                "conversations": requests,
                "with_ad": filled,
                "without_ad": requests - filled,
                "fill_rate_pct": _pct(filled, requests),
            }
        )
    return sorted(out, key=lambda x: -x["without_ad"])


async def delivery(rt: Runtime, days: int = 1) -> dict[str, Any]:
    """Fill rate per placement over the last ``days`` days, with why requests got no ad, and the
    topics of conversations that got no ad (the sponsors worth finding)."""
    day_from, day_to = day_range(rt, days)
    async with rt.sessionmaker() as s:
        placements = await _delivery(s, day_from, day_to)
        topics = await _topics(s, day_from, day_to)
    return {
        "from": day_from,
        "to": day_to,
        "placements": placements,
        "topics": topics,
        "topics_note": (
            "Conversations per topic category (a conversation can have several; sensitive ones are not "
            "counted). Topics with many conversations without an ad are where a new sponsor would fill."
        ),
    }


async def campaign_report(rt: Runtime, campaign: Campaign, days: int = 7) -> dict[str, Any]:
    day_from, day_to = day_range(rt, days)
    async with rt.sessionmaker() as s:
        stats = (
            (
                await s.execute(
                    select(DailyStat)
                    .where(
                        DailyStat.campaign_id == campaign.id,
                        DailyStat.day >= day_from,
                        DailyStat.day <= day_to,
                    )
                    .order_by(DailyStat.day)
                )
            )
            .scalars()
            .all()
        )
        misses = {
            m.day: m.requests
            for m in (
                await s.execute(
                    select(BudgetMiss).where(
                        BudgetMiss.campaign_id == campaign.id,
                        BudgetMiss.day >= day_from,
                        BudgetMiss.day <= day_to,
                    )
                )
            )
            .scalars()
            .all()
        }
    by_day = {r.day: r for r in stats}
    daily = []
    for d in sorted(set(by_day) | set(misses)):  # a day with only misses still shows up
        r = by_day.get(d)
        imps, clicks = (r.impressions, r.clicks) if r else (0, 0)
        daily.append(
            {
                "day": d,
                "impressions": imps,
                "clicks": clicks,
                "ctr_pct": _pct(clicks, imps),
                "conversions": r.conversions if r else 0,
                "spend": _money(r.spend_micros if r else 0),
                "missed_for_budget": misses.get(d, 0),
            }
        )
    total = {
        k: sum(d[k] for d in daily) for k in ("impressions", "clicks", "conversions", "missed_for_budget")
    }
    total["spend"] = round(sum(d["spend"] for d in daily), 2)
    total["ctr_pct"] = _pct(total["clicks"], total["impressions"])
    return {
        "campaign": campaign.name,
        "id": campaign.id,
        "advertiser": campaign.advertiser.name,
        "status": campaign.status,
        "bid": f"{_money(campaign.bid_micros)} {campaign.bid_type.upper()}",
        "daily_budget": _money(campaign.daily_budget_micros) if campaign.daily_budget_micros else None,
        "total_budget": _money(campaign.total_budget_micros) if campaign.total_budget_micros else None,
        "spend_lifetime": _money(campaign.spend_total_micros),
        "currency": rt.settings.currency,
        "from": day_from,
        "to": day_to,
        "total": total,
        "daily": daily,
    }


async def advertiser_report(rt: Runtime, advertiser_id: str, days: int = 7) -> str:
    """A short Markdown report an advertiser can read as is."""
    day_from, day_to = day_range(rt, days)
    cur = rt.settings.currency
    async with rt.sessionmaker() as s:
        campaigns = await _campaigns(s, advertiser_id)
        if not campaigns:
            return "No campaigns for this advertiser."
        name = campaigns[0].advertiser.name
        q = (
            select(
                DailyStat.campaign_id,
                func.sum(DailyStat.impressions),
                func.sum(DailyStat.clicks),
                func.sum(DailyStat.conversions),
                func.sum(DailyStat.spend_micros),
            )
            .where(DailyStat.day >= day_from, DailyStat.day <= day_to)
            .where(DailyStat.campaign_id.in_([c.id for c in campaigns]))
            .group_by(DailyStat.campaign_id)
        )
        sums = {
            cid: (int(i or 0), int(k or 0), int(v or 0), int(m or 0))
            for cid, i, k, v, m in (await s.execute(q)).all()
        }
        misses = await _misses(s, day_from, day_to)
    lines = [
        f"## {name}: ads from {day_from} to {day_to}",
        "",
        f"| Campaign | Impressions | Clicks | CTR | Conversions | Spend ({cur}) |",
        "|---|---|---|---|---|---|",
    ]
    tot = [0, 0, 0, 0]
    extra = []
    for c in campaigns:
        i, k, v, m = sums.get(c.id, (0, 0, 0, 0))
        tot = [tot[0] + i, tot[1] + k, tot[2] + v, tot[3] + m]
        lines.append(f"| {c.name} | {i:,} | {k:,} | {_pct(k, i)}% | {v:,} | {_money(m):,.2f} |")
        if misses.get(c.id):
            extra.append(
                f"- {c.name} matched {misses[c.id]:,} more conversations it could not take because its "
                "budget was spent. A higher daily budget would have reached them."
            )
    imps, clicks, convs, spend = tot
    lines.append(
        f"| **Total** | {imps:,} | {clicks:,} | {_pct(clicks, imps)}% | {convs:,} | {_money(spend):,.2f} |"
    )
    if extra:
        lines += ["", *extra]
    lines += ["", "_Counts only: conversations are never stored or shared with advertisers._"]
    return "\n".join(lines)


async def recent_changes(rt: Runtime, limit: int = 20) -> list[dict[str, Any]]:
    async with rt.sessionmaker() as s:
        rows = (
            (
                await s.execute(
                    select(AuditEntry).order_by(AuditEntry.at.desc()).limit(max(1, min(limit, 200)))
                )
            )
            .scalars()
            .all()
        )
    out = []
    for r in rows:
        at = r.at if r.at.tzinfo else r.at.replace(tzinfo=timezone.utc)
        out.append(
            {
                "at": at.astimezone(rt.tz).strftime("%Y-%m-%d %H:%M"),
                "by": r.actor,
                "via": r.via,
                "action": r.action,
                "campaign": r.target_name,
                "before": r.before,
                "after": r.after,
            }
        )
    return out
