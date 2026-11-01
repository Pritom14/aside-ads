"""``aside seed-demo``: a demo publisher, keys, and sample campaigns from fictional brands."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aside_ads.auth import create_api_key, lookup_key
from aside_ads.models import Advertiser, Placement, Publisher
from aside_ads.schemas import CampaignIn, CreativeIn
from aside_ads.services import add_creative, add_placement, create_campaign, create_publisher

DEMO_PUBLISHER = "Demo Chat"
DEMO_PLACEMENT = "chat-sidebar"


@dataclass
class DemoAd:
    advertiser: str
    slug: str
    bid_type: Literal["cpc", "cpm"]
    bid: str
    daily_budget: str
    targeting: str
    headline: str
    body: str
    cta: str
    freq_cap: int | None = None
    categories_exclude: tuple[str, ...] = ()


# All brands are fictional. Landing pages are served by examples/chat-demo.
DEMO_ADS: list[DemoAd] = [
    DemoAd(
        "Slowlog Lens",
        "slowlog-lens",
        "cpc",
        "1.50",
        "50",
        "slow postgres or SQL database queries\ndatabase indexing and query performance\n"
        "finding bottlenecks in a production database",
        "Find the slow query before your users do",
        "Slowlog Lens reads your Postgres query plans and points at the missing index. "
        "Free for one database.",
        "Try it free",
    ),
    DemoAd(
        "Rivetline CI",
        "rivetline-ci",
        "cpm",
        "12",
        "40",
        "slow CI builds and GitHub Actions pipelines\ncaching dependencies and running tests in parallel\n"
        "continuous integration and deployment",
        "Builds in minutes, not coffee breaks",
        "Rivetline CI caches dependencies and splits your test suite across runners automatically.",
        "Speed up my builds",
    ),
    DemoAd(
        "Kilnworks GPU Cloud",
        "kilnworks",
        "cpc",
        "2.00",
        "60",
        "fine-tuning a large language model on custom data\nrenting GPUs for model training or inference\n"
        "running open-source LLMs like Llama or Mistral",
        "H100s by the hour for fine-tuning",
        "Spin up a GPU box with your framework preinstalled. Per-second billing, no commitments.",
        "See GPU pricing",
    ),
    DemoAd(
        "Bahi Books",
        "bahi-books",
        "cpm",
        "6",
        "25",
        "GST returns and invoicing for freelancers and small businesses in India\n"
        "bookkeeping and accounting software\nfiling taxes as a freelancer",
        "GST invoices that reconcile themselves",
        "Bahi Books matches your invoices to GSTR-2B and drafts the return. Made for freelancers.",
        "Start free trial",
    ),
    DemoAd(
        "Wayfarer Shield",
        "wayfarer-shield",
        "cpm",
        "5",
        "30",
        "planning an international trip or vacation abroad\n"
        "travel insurance, flight delays and lost baggage\n"
        "what to pack for a trip",
        "Trip cover in two minutes",
        "Medical cover abroad, delayed flights and lost bags. "
        "Buy before you fly, cancel any time before departure.",
        "Get a quote",
        freq_cap=3,
    ),
    DemoAd(
        "Lingo Lantern",
        "lingo-lantern",
        "cpc",
        "0.80",
        "20",
        "learning a new language like Spanish, Hindi or Japanese\npracticing speaking a foreign language\n"
        "translating phrases into another language",
        "Ten minutes a day to real conversations",
        "Short daily lessons and speaking practice with instant feedback. Spanish, Hindi, Japanese and more.",
        "Start learning",
    ),
    DemoAd(
        "Kettle & Crumb",
        "kettle-and-crumb",
        "cpm",
        "4",
        "15",
        "baking bread, sourdough, cakes and recipes\nbaking supplies, flour and yeast",
        "Sourdough starter kits, shipped fresh",
        "A live starter, a proofing basket and a lame. Everything you need for your first loaf.",
        "Shop starter kits",
    ),
    DemoAd(
        "Demo Chat (house ads)",
        "demo-chat-pro",
        "cpm",
        "0.60",
        "5",
        "exporting or saving a chat conversation\nlonger context and more messages per day\n"
        "sharing a conversation with my team",
        "Demo Chat Pro: export any chat to Markdown",
        "Unlimited history, longer context and one-click export. A house ad for this app.",
        "Upgrade",
    ),
]


def _read_env(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    out = {}
    for line in path.read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
    return out


async def seed_demo(
    sessionmaker: async_sessionmaker[AsyncSession],
    data_dir: Path,
    landing_url: str,
    server_url: str,
    *,
    include_admin_key: bool = True,
) -> dict[str, str]:
    """Create the demo publisher, campaigns and keys (idempotent) and write ``demo.env``.

    With ``include_admin_key=False`` no admin key is created or written to disk
    (the hosted demo issues its own and prints it to the logs only).
    """
    landing_url = landing_url.rstrip("/")
    env_path = data_dir / "demo.env"
    existing = _read_env(env_path)
    created: list[str] = []
    async with sessionmaker() as s:
        pub = (
            await s.execute(select(Publisher).where(Publisher.name == DEMO_PUBLISHER))
        ).scalar_one_or_none()
        if pub is None:
            pub = await create_publisher(s, DEMO_PUBLISHER, "localhost")
        for key in (DEMO_PLACEMENT, "default"):
            exists = (
                await s.execute(
                    select(Placement).where(Placement.publisher_id == pub.id, Placement.key == key)
                )
            ).scalar_one_or_none()
            if exists is None:
                await add_placement(s, pub.id, key, key.replace("-", " ").title())

        for ad in DEMO_ADS:
            adv = (
                await s.execute(select(Advertiser).where(Advertiser.name == ad.advertiser))
            ).scalar_one_or_none()
            if adv is not None:
                continue
            adv = Advertiser(name=ad.advertiser, website=f"{landing_url}/{ad.slug}")
            s.add(adv)
            await s.flush()
            campaign = await create_campaign(
                s,
                CampaignIn(
                    advertiser_id=adv.id,
                    name=f"{ad.advertiser} demo",
                    bid_type=ad.bid_type,
                    bid=Decimal(ad.bid),
                    daily_budget=Decimal(ad.daily_budget),
                    total_budget=Decimal(ad.daily_budget) * 30,
                    targeting_text=ad.targeting,
                    freq_cap=ad.freq_cap,
                    categories_exclude=list(ad.categories_exclude),
                ),
            )
            await add_creative(
                s,
                campaign.id,
                CreativeIn(headline=ad.headline, body=ad.body, cta=ad.cta, url=f"{landing_url}/{ad.slug}"),
            )
            created.append(ad.advertiser)

        keys = {}
        roles = [("ASIDE_API_KEY", "publisher")]
        if include_admin_key:
            roles.insert(0, ("ASIDE_ADMIN_KEY", "admin"))
        for env_name, role in roles:
            current = existing.get(env_name)
            if current and await lookup_key(s, current) is not None:
                keys[env_name] = current
                continue
            _, plaintext = await create_api_key(
                s, role, "demo", publisher_id=pub.id if role == "publisher" else None
            )
            keys[env_name] = plaintext
        await s.commit()

    values = {"ASIDE_URL": server_url.rstrip("/"), **keys, "ASIDE_PLACEMENT": DEMO_PLACEMENT}
    data_dir.mkdir(parents=True, exist_ok=True)
    env_path.write_text(
        "# Written by `aside seed-demo`. Demo credentials for examples/chat-demo. Do not use in production.\n"
        + "".join(f"{k}={v}\n" for k, v in values.items())
    )
    env_path.chmod(0o600)
    values["created"] = ", ".join(created) if created else "(campaigns already existed)"
    values["env_file"] = str(env_path)
    return values
