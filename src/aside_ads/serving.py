"""Ad selection: sensitive guard -> hard filters -> relevance -> auction -> frequency cap -> reservation.

Privacy: the conversation text is wrapped in a ``ContextAnalysis`` that exists
only for the duration of this call. Nothing derived from it (text, tokens,
embedding, detected categories) is written to the database, logs or counters.
Only the outcome (which ad, at what price) leaves this module.
"""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass
from urllib.parse import urlparse

import anyio
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from aside_ads.auction import Candidate, clearing_price, impression_cost, predicted_ctr, rank
from aside_ads.matching import AdDoc, ContextAnalysis
from aside_ads.models import Campaign, Creative, Placement, new_id
from aside_ads.pacing import budget_allows, day_fraction, pacing_allows
from aside_ads.reservations import hold_for, outstanding_by_campaign, purge_expired, reserve
from aside_ads.runtime import Runtime
from aside_ads.schemas import ServeRequest, ServeResponse, SponsoredAd
from aside_ads.targeting import (
    categories_allow,
    geo_allows,
    keywords_allow,
    placement_allows,
    schedule_allows,
)

log = logging.getLogger("aside.serve")


class UnknownPlacement(Exception):
    pass


@dataclass
class _Scored:
    campaign: Campaign
    relevance: float


def _ad_doc(c: Campaign, creatives: list[Creative]) -> AdDoc:
    lines = [ln.strip() for ln in (c.targeting_text or "").splitlines() if ln.strip()]
    if c.keywords_include:
        lines.append(", ".join(c.keywords_include))
    lines.extend(f"{cr.headline}. {cr.body}".strip() for cr in creatives)
    return AdDoc(key=c.id, lines=lines)


def _unit_cost(c: Campaign) -> int:
    return impression_cost(c.bid_micros) if c.bid_type == "cpm" else c.bid_micros


def _fc_key(campaign_id: str, user_hash: str) -> str:
    return f"fc:{campaign_id}:{user_hash}"


async def serve(rt: Runtime, publisher_id: str, req: ServeRequest, base_url: str) -> ServeResponse:
    request_id = new_id()
    now = rt.now()
    day = rt.day(now)
    settings = rt.settings

    async with rt.sessionmaker() as s:
        placement = (
            await s.execute(
                select(Placement).where(
                    Placement.publisher_id == publisher_id, Placement.key == req.placement
                )
            )
        ).scalar_one_or_none()
        if placement is None:
            raise UnknownPlacement(req.placement)
        campaigns = (
            (
                await s.execute(
                    select(Campaign)
                    .where(Campaign.status == "active")
                    .options(selectinload(Campaign.creatives), selectinload(Campaign.advertiser))
                )
            )
            .scalars()
            .all()
        )
        held = await outstanding_by_campaign(s, now)

    if req.sensitive:
        return ServeResponse(request_id=request_id, ad=None, reason="sensitive_context")

    geo = req.geo
    frac = day_fraction(now, rt.tz)

    def static_ok(c: Campaign) -> bool:
        # Outstanding reservations count as spend. This read only filters; reserve() below
        # re-checks under a lock before an ad is returned.
        reserved = held.get(c.id, 0)
        spend_today = (c.spend_today_micros if c.spend_day == day else 0) + reserved
        return (
            any(cr.status == "active" for cr in c.creatives)
            and placement_allows(c, placement.key)
            and schedule_allows(c, now, rt.tz)
            and geo_allows(
                c,
                geo.country if geo else None,
                geo.region if geo else None,
                geo.lat if geo else None,
                geo.lng if geo else None,
            )
            and budget_allows(
                unit_cost_micros=_unit_cost(c),
                spend_today_micros=spend_today,
                daily_budget_micros=c.daily_budget_micros,
                spend_total_micros=c.spend_total_micros + reserved,
                total_budget_micros=c.total_budget_micros,
            )
            and pacing_allows(spend_today, c.daily_budget_micros, frac, settings.pacing_buffer, c.pacing)
        )

    def analyze() -> tuple[str | None, list[_Scored]]:
        # CPU-bound (tokenizing, embedding), run in a worker thread.
        ctx = ContextAnalysis(req.context_text(), settings.max_context_chars)
        if rt.guard.classify(ctx, first_only=True):
            return "sensitive_context", []
        pool = [c for c in campaigns if static_ok(c) and keywords_allow(c, ctx)]
        if not pool:
            return "no_eligible_campaigns", []
        hinted = {x.strip().lower() for x in req.categories}
        if any(c.categories_include or c.categories_exclude for c in pool):
            topics = hinted | {h.category for h in rt.topics.classify(ctx)}
            pool = [c for c in pool if categories_allow(c, topics)]
            if not pool:
                return "no_eligible_campaigns", []
        active = {c.id: [cr for cr in c.creatives if cr.status == "active"] for c in pool}
        scores = rt.matcher.relevance(ctx, [_ad_doc(c, active[c.id]) for c in pool])
        scored = [
            _Scored(c, rel)
            for c, rel in zip(pool, scores, strict=True)
            if rel >= (c.min_relevance if c.min_relevance is not None else rt.relevance_threshold)
        ]
        return (None if scored else "below_relevance_threshold"), scored

    reason, scored = await anyio.to_thread.run_sync(analyze)
    if reason:
        return ServeResponse(request_id=request_id, ad=None, reason=reason)

    user_hash = rt.signer.hash_user(req.user_id) if req.user_id else None
    candidates: list[Candidate] = []
    for sc in scored:
        c = sc.campaign
        if user_hash and c.freq_cap and await rt.counters.get(_fc_key(c.id, user_hash)) >= c.freq_cap:
            continue
        ctr = predicted_ctr(c.impressions_total, c.clicks_total, settings.default_ctr)
        candidates.append(Candidate(c.id, c.bid_type, c.bid_micros, sc.relevance, ctr, payload=c))
    if not candidates:
        return ServeResponse(request_id=request_id, ad=None, reason="frequency_capped")

    floor = (
        placement.floor_cpm_micros if placement.floor_cpm_micros is not None else settings.floor_cpm_micros
    )
    ranked = rank(candidates, floor)
    if not ranked:
        return ServeResponse(request_id=request_id, ad=None, reason="below_floor")

    await purge_expired(rt)  # at most once a minute; expired rows already stop counting
    no_ad_reason = "frequency_capped"
    for i, cand in enumerate(ranked):
        c: Campaign = cand.payload
        # Increment-then-check makes the cap strict under concurrent requests.
        if (
            user_hash
            and c.freq_cap
            and await rt.counters.incr(_fc_key(c.id, user_hash), c.freq_cap_window_s) > c.freq_cap
        ):
            continue
        placed = clearing_price(ranked, i, floor)
        unit = impression_cost(placed.price_micros) if c.bid_type == "cpm" else placed.price_micros
        hold = hold_for(rt, c.bid_type, placed.price_micros, unit)
        if (
            hold is not None
            and (c.daily_budget_micros is not None or c.total_budget_micros is not None)
            and not await reserve(
                rt, c, request_id, hold_micros=hold[0], full_unit_micros=unit, ttl_seconds=hold[1]
            )
        ):
            no_ad_reason = "budget_exhausted"
            continue
        creative = random.choice([cr for cr in c.creatives if cr.status == "active"])
        claims = {
            "r": request_id,
            "c": c.id,
            "a": creative.id,
            "p": publisher_id,
            "pl": placement.key,
            "bt": c.bid_type,
            "pr": placed.price_micros,
        }
        if user_hash:
            claims["u"] = user_hash
        if geo and geo.country:
            claims["g"] = geo.country.upper()
        if geo and geo.region:
            claims["rg"] = geo.region[:64]
        imp = rt.signer.sign("imp", claims, settings.impression_token_ttl, now.timestamp())
        clk = rt.signer.sign("clk", claims, settings.click_token_ttl, now.timestamp())
        ad = SponsoredAd(
            id=creative.id,
            campaign_id=c.id,
            advertiser=c.advertiser.name,
            headline=creative.headline,
            body=creative.body,
            cta=creative.cta,
            url=f"{base_url}/t/c?t={clk}",
            display_url=urlparse(creative.url).netloc,
            image_url=creative.image_url,
            impression_url=f"{base_url}/t/i?t={imp}",
            relevance=round(cand.relevance, 4),
        )
        return ServeResponse(request_id=request_id, ad=ad)
    return ServeResponse(request_id=request_id, ad=None, reason=no_ad_reason)
