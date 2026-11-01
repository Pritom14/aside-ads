from __future__ import annotations

import math
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.business import Business
from app.models.business_ad import BusinessAd
from app.models.campaign import Campaign
from app.models.campaign_targeting import CampaignTargeting
from app.models.influencer_ad import InfluencerAd
from app.schemas.serve import ServeRequest, ServeResponse, ServedAd

router = APIRouter()


def _tokenize(text: str) -> set[str]:
    return {t.strip().lower() for t in text.split() if t.strip()}


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * r * math.atan2(math.sqrt(a), math.sqrt(1 - a))


@router.post("/serve", response_model=ServeResponse)
async def serve_ads(payload: ServeRequest, db: AsyncSession = Depends(get_db)) -> ServeResponse:
    now = datetime.now(timezone.utc)
    query_tokens = _tokenize(payload.query)

    # Load active campaigns with targeting
    campaigns = (
        await db.execute(
            select(Campaign, CampaignTargeting)
            .join(CampaignTargeting, CampaignTargeting.campaign_id == Campaign.id, isouter=True)
            .where(Campaign.status == "active")
        )
    ).all()

    served: list[tuple[int, datetime, str, ServedAd]] = []

    for campaign, targeting in campaigns:
        # date window check
        if campaign.start_at and campaign.start_at > now:
            continue
        if campaign.end_at and campaign.end_at < now:
            continue

        keywords = (targeting.keywords if targeting else []) or []
        kw_tokens = {k.lower() for k in keywords}
        match_score = len(query_tokens.intersection(kw_tokens)) if kw_tokens else 0

        # Location filter only for business campaigns when we have coordinates
        if campaign.campaign_type == "business" and payload.lat is not None and payload.lng is not None:
            business = (
                await db.execute(select(Business).where(Business.id == campaign.business_id))
            ).scalar_one_or_none()
            if not business or business.lat is None or business.lng is None:
                continue
            radius_km = int(targeting.radius_km) if targeting else 0
            if radius_km > 0:
                dist = _haversine_km(payload.lat, payload.lng, float(business.lat), float(business.lng))
                if dist > radius_km:
                    continue

        # Gather ads for this campaign
        if campaign.campaign_type == "business":
            ads = (await db.execute(select(BusinessAd).where(BusinessAd.campaign_id == campaign.id))).scalars().all()
            for ad in ads:
                served.append(
                    (
                        match_score,
                        ad.created_at,
                        str(ad.id),
                        ServedAd(
                            ad_type="business",
                            ad_id=str(ad.id),
                            campaign_id=str(campaign.id),
                            headline=ad.headline,
                            body=ad.body,
                            cta_text=ad.cta_text,
                            cta_url=ad.cta_url,
                        ),
                    )
                )
        else:
            ads = (await db.execute(select(InfluencerAd).where(InfluencerAd.campaign_id == campaign.id))).scalars().all()
            for ad in ads:
                served.append(
                    (
                        match_score,
                        ad.created_at,
                        str(ad.id),
                        ServedAd(
                            ad_type="influencer",
                            ad_id=str(ad.id),
                            campaign_id=str(campaign.id),
                            headline=ad.headline,
                            body=ad.body,
                            cta_text=ad.cta_text,
                            cta_url=ad.cta_url,
                        ),
                    )
                )

    served.sort(key=lambda x: (x[0], x[1], x[2]), reverse=True)
    return ServeResponse(ads=[x[3] for x in served[: payload.limit]])

