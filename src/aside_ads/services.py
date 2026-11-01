"""Admin operations shared by the JSON admin API, the dashboard and the CLI."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from aside_ads.models import Advertiser, Campaign, Creative, DailyStat, Placement, Publisher
from aside_ads.schemas import (
    CampaignIn,
    CampaignOut,
    CampaignPatch,
    CreativeIn,
    CreativeOut,
    CreativePatch,
    PlacementOut,
    PublisherOut,
    StatRow,
    from_micros,
    to_micros,
)
from aside_ads.targeting import as_utc


class NotFound(Exception):
    pass


async def get_or_404(session: AsyncSession, model: type, id_: str):  # type: ignore[no-untyped-def]
    obj = await session.get(model, id_)
    if obj is None:
        raise NotFound(f"{model.__name__} {id_} not found")
    return obj


async def find_by_name_or_id(session: AsyncSession, model: type, value: str):  # type: ignore[no-untyped-def]
    obj = await session.get(model, value)
    if obj is None:
        obj = (await session.execute(select(model).where(model.name == value))).scalar_one_or_none()
    return obj


def _apply_campaign_fields(c: Campaign, data: CampaignIn | CampaignPatch) -> None:
    fields = data.model_dump(exclude_unset=True)
    simple = [
        "name",
        "status",
        "bid_type",
        "pacing",
        "freq_cap",
        "targeting_text",
        "min_relevance",
        "keywords_include",
        "keywords_exclude",
        "categories_include",
        "categories_exclude",
        "placements",
        "countries",
        "regions",
        "geo_lat",
        "geo_lng",
        "geo_radius_km",
        "start_at",
        "end_at",
    ]
    for f in simple:
        if f in fields:
            value = fields[f]
            if value is None and f in ("name", "status", "bid_type", "pacing"):
                continue
            if f in ("targeting_text",) and value is None:
                value = ""
            if f.endswith(("_include", "_exclude")) or f in ("placements", "countries", "regions"):
                value = value or []
            setattr(c, f, value)
    if "bid" in fields and fields["bid"] is not None:
        c.bid_micros = to_micros(fields["bid"])  # type: ignore[assignment]
    if "daily_budget" in fields:
        c.daily_budget_micros = to_micros(fields["daily_budget"])
    if "total_budget" in fields:
        c.total_budget_micros = to_micros(fields["total_budget"])
    if "freq_cap_window_hours" in fields and fields["freq_cap_window_hours"]:
        c.freq_cap_window_s = int(float(fields["freq_cap_window_hours"]) * 3600)
    if "schedule" in fields:
        sched = data.schedule.model_dump(exclude_none=True) if data.schedule else None
        c.schedule = sched or None
    if isinstance(data, CampaignPatch):
        if data.clear_daily_budget:
            c.daily_budget_micros = None
        if data.clear_total_budget:
            c.total_budget_micros = None


async def create_campaign(session: AsyncSession, data: CampaignIn) -> Campaign:
    await get_or_404(session, Advertiser, data.advertiser_id)
    c = Campaign(advertiser_id=data.advertiser_id, bid_micros=to_micros(data.bid))
    _apply_campaign_fields(c, data)
    session.add(c)
    await session.flush()
    return c


async def update_campaign(session: AsyncSession, campaign_id: str, data: CampaignPatch) -> Campaign:
    c = await get_or_404(session, Campaign, campaign_id)
    _apply_campaign_fields(c, data)
    await session.flush()
    return c


async def add_creative(session: AsyncSession, campaign_id: str, data: CreativeIn) -> Creative:
    await get_or_404(session, Campaign, campaign_id)
    cr = Creative(campaign_id=campaign_id, **data.model_dump())
    session.add(cr)
    await session.flush()
    return cr


async def update_creative(session: AsyncSession, creative_id: str, data: CreativePatch) -> Creative:
    cr = await get_or_404(session, Creative, creative_id)
    for k, v in data.model_dump(exclude_unset=True).items():
        if v is None and k != "image_url":
            continue  # only image_url can be cleared
        setattr(cr, k, v)
    await session.flush()
    return cr


async def load_campaign(session: AsyncSession, campaign_id: str) -> Campaign:
    c = (
        await session.execute(
            select(Campaign)
            .where(Campaign.id == campaign_id)
            .options(selectinload(Campaign.creatives), selectinload(Campaign.advertiser))
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if c is None:
        raise NotFound(f"Campaign {campaign_id} not found")
    return c


async def list_campaigns(session: AsyncSession) -> list[Campaign]:
    return list(
        (
            await session.execute(
                select(Campaign)
                .options(selectinload(Campaign.creatives), selectinload(Campaign.advertiser))
                .order_by(Campaign.created_at)
            )
        )
        .scalars()
        .all()
    )


def campaign_out(c: Campaign, today: str) -> CampaignOut:
    return CampaignOut(
        id=c.id,
        advertiser_id=c.advertiser_id,
        name=c.name,
        status=c.status,
        bid_type=c.bid_type,
        bid=from_micros(c.bid_micros) or 0.0,
        daily_budget=from_micros(c.daily_budget_micros),
        total_budget=from_micros(c.total_budget_micros),
        pacing=c.pacing,
        freq_cap=c.freq_cap,
        freq_cap_window_hours=c.freq_cap_window_s / 3600,
        targeting_text=c.targeting_text,
        min_relevance=c.min_relevance,
        keywords_include=c.keywords_include,
        keywords_exclude=c.keywords_exclude,
        categories_include=c.categories_include,
        categories_exclude=c.categories_exclude,
        placements=c.placements,
        countries=c.countries,
        regions=c.regions,
        geo_lat=c.geo_lat,
        geo_lng=c.geo_lng,
        geo_radius_km=c.geo_radius_km,
        start_at=as_utc(c.start_at),
        end_at=as_utc(c.end_at),
        schedule=c.schedule,
        spend_total=from_micros(c.spend_total_micros) or 0.0,
        spend_today=from_micros(c.spend_today_micros if c.spend_day == today else 0) or 0.0,
        creatives=[CreativeOut.model_validate(cr) for cr in c.creatives],
    )


async def create_publisher(session: AsyncSession, name: str, domain: str | None = None) -> Publisher:
    p = Publisher(name=name, domain=domain)
    session.add(p)
    await session.flush()
    return p


async def add_placement(
    session: AsyncSession, publisher_id: str, key: str, name: str = "", floor_cpm_micros: int | None = None
) -> Placement:
    await get_or_404(session, Publisher, publisher_id)
    pl = Placement(publisher_id=publisher_id, key=key, name=name or key, floor_cpm_micros=floor_cpm_micros)
    session.add(pl)
    await session.flush()
    return pl


async def list_publishers(session: AsyncSession) -> list[Publisher]:
    return list(
        (
            await session.execute(
                select(Publisher).options(selectinload(Publisher.placements)).order_by(Publisher.name)
            )
        )
        .scalars()
        .all()
    )


async def load_publisher(session: AsyncSession, publisher_id: str) -> Publisher:
    p = (
        await session.execute(
            select(Publisher)
            .where(Publisher.id == publisher_id)
            .options(selectinload(Publisher.placements))
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if p is None:
        raise NotFound(f"Publisher {publisher_id} not found")
    return p


def publisher_out(p: Publisher) -> PublisherOut:
    return PublisherOut(
        id=p.id,
        name=p.name,
        domain=p.domain,
        placements=[
            PlacementOut(id=pl.id, key=pl.key, name=pl.name, floor_cpm=from_micros(pl.floor_cpm_micros))
            for pl in p.placements
        ],
    )


async def daily_stats(
    session: AsyncSession, day_from: str, day_to: str, campaign_id: str | None = None
) -> list[StatRow]:
    q = select(DailyStat).where(DailyStat.day >= day_from, DailyStat.day <= day_to)
    if campaign_id:
        q = q.where(DailyStat.campaign_id == campaign_id)
    rows = (await session.execute(q.order_by(DailyStat.day.desc(), DailyStat.campaign_id))).scalars().all()
    return [
        StatRow(
            campaign_id=r.campaign_id,
            day=r.day,
            impressions=r.impressions,
            clicks=r.clicks,
            ctr=(r.clicks / r.impressions) if r.impressions else 0.0,
            conversions=r.conversions,
            spend=from_micros(r.spend_micros) or 0.0,
            conversion_value=from_micros(r.conversion_value_micros) or 0.0,
        )
        for r in rows
    ]


async def totals_by_campaign(session: AsyncSession, day_from: str, day_to: str) -> dict[str, StatRow]:
    q = (
        select(
            DailyStat.campaign_id,
            func.sum(DailyStat.impressions),
            func.sum(DailyStat.clicks),
            func.sum(DailyStat.conversions),
            func.sum(DailyStat.spend_micros),
            func.sum(DailyStat.conversion_value_micros),
        )
        .where(DailyStat.day >= day_from, DailyStat.day <= day_to)
        .group_by(DailyStat.campaign_id)
    )
    out: dict[str, StatRow] = {}
    for cid, imps, clicks, convs, spend, value in (await session.execute(q)).all():
        imps, clicks = int(imps or 0), int(clicks or 0)
        out[cid] = StatRow(
            campaign_id=cid,
            day=f"{day_from}..{day_to}",
            impressions=imps,
            clicks=clicks,
            ctr=(clicks / imps) if imps else 0.0,
            conversions=int(convs or 0),
            spend=from_micros(int(spend or 0)) or 0.0,
            conversion_value=from_micros(int(value or 0)) or 0.0,
        )
    return out
