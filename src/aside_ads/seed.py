"""``aside seed-demo``: a demo publisher, keys, and sample campaigns from fictional brands.

Two scenarios ship with Aside:

* ``dev`` (the default): "Demo Chat", a general assistant with developer-tool advertisers, in USD.
* ``cooking``: "Rasoi AI", a free Indian cooking assistant, with kitchen and grocery advertisers
  bidding in INR.
"""

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
    campaign: str | None = None  # defaults to "<advertiser> demo"


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


# The cooking scenario. All brands are fictional (each name was checked against existing Indian
# companies); amounts are in INR. Targeting describes what each advertiser wants to sit next to:
# buying intent or a problem their product solves, not every mention of a word.
COOKING_ADS: list[DemoAd] = [
    DemoAd(
        "Thaila Now",
        "thaila-now",
        "cpc",
        "6",
        "1500",
        "ordering ingredients for a recipe like paneer butter masala, biryani or dal makhani\n"
        "groceries delivered fast: paneer, butter, cream, tomatoes, onions and fresh vegetables\n"
        "cooking dinner for my family or guests tonight",
        "Cooking tonight? Get the ingredients in 10 minutes",
        "Paneer, cream, tomatoes and fresh coriander, delivered from a store near you. No minimum order.",
        "Order ingredients",
        campaign="Thaila Now: recipe ingredients",
    ),
    DemoAd(
        "Lohasutra Cookware",
        "lohasutra",
        "cpc",
        "8",
        "1000",
        "my kadhai, tawa or pan keeps sticking or rusting\n"
        "seasoning and caring for cast iron cookware\n"
        "buying a new cast iron kadhai, tawa or non-stick pan",
        "Pre-seasoned cast-iron kadhais",
        "Ready to cook from day one and better with every use. Each one comes with a two-minute care guide.",
        "Shop kadhais",
        campaign="Lohasutra: cast-iron kadhais",
    ),
    DemoAd(
        "Jeera Lane",
        "jeera-lane",
        "cpm",
        "55",
        "600",
        "making garam masala, chai masala or biryani masala at home\n"
        "buying spices and spice blends for my kitchen\n"
        "keeping whole spices fresh in a masala dabba",
        "Masalas ground every week, not every year",
        "Small-batch garam masala, chai masala and biryani masala, packed within days of grinding.",
        "Explore masalas",
        campaign="Jeera Lane: fresh masalas",
    ),
    DemoAd(
        "Chakkiwell",
        "chakkiwell",
        "cpc",
        "10",
        "2000",
        "choosing a mixer grinder for idli and dosa batter or chutneys\n"
        "wet grinder or mixer grinder for an Indian kitchen\n"
        "pressure cooker whistle, gasket or safety valve problems",
        "Smooth idli batter in one go",
        "A 750 W mixer grinder with a wet-grinding jar. Pressure cookers and spare gaskets too.",
        "See mixer grinders",
        campaign="Chakkiwell: mixer grinders",
    ),
    DemoAd(
        "Tadka Talim",
        "tadka-talim",
        "cpm",
        "30",
        "500",
        "learning to cook Indian food as a beginner\n"
        "cooking classes and step-by-step video lessons\n"
        "improving my cooking skills, making soft rotis or a perfect biryani",
        "Learn the dishes you grew up eating",
        "Short video lessons from home cooks: soft rotis, dal, biryani and more. First week free.",
        "Start a free class",
        campaign="Tadka Talim: video cooking classes",
    ),
]


@dataclass(frozen=True)
class DemoScenario:
    publisher: str
    currency: str
    ads: list[DemoAd]


SCENARIOS: dict[str, DemoScenario] = {
    "dev": DemoScenario(DEMO_PUBLISHER, "USD", DEMO_ADS),
    "cooking": DemoScenario("Rasoi AI", "INR", COOKING_ADS),
}


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
    scenario: str = "dev",
) -> dict[str, str]:
    """Create the demo publisher, campaigns and keys (idempotent) and write ``demo.env``.

    ``scenario`` picks the publisher and advertisers (see ``SCENARIOS``). Bids and budgets
    are in the scenario's currency; ``demo.env`` records it as ``ASIDE_CURRENCY`` so the
    server can be started with the matching setting.

    With ``include_admin_key=False`` no admin key is created or written to disk
    (the hosted demo issues its own and prints it to the logs only).
    """
    if scenario not in SCENARIOS:
        raise ValueError(f"unknown demo scenario {scenario!r}; choose from {', '.join(SCENARIOS)}")
    sc = SCENARIOS[scenario]
    landing_url = landing_url.rstrip("/")
    env_path = data_dir / "demo.env"
    existing = _read_env(env_path)
    created: list[str] = []
    async with sessionmaker() as s:
        pub = (await s.execute(select(Publisher).where(Publisher.name == sc.publisher))).scalar_one_or_none()
        if pub is None:
            pub = await create_publisher(s, sc.publisher, "localhost")
        for key in (DEMO_PLACEMENT, "default"):
            exists = (
                await s.execute(
                    select(Placement).where(Placement.publisher_id == pub.id, Placement.key == key)
                )
            ).scalar_one_or_none()
            if exists is None:
                await add_placement(s, pub.id, key, key.replace("-", " ").title())

        for ad in sc.ads:
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
                    name=ad.campaign or f"{ad.advertiser} demo",
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
            found = await lookup_key(s, current) if current else None
            # Reuse a key only if it still fits: a publisher key from another scenario's
            # publisher would serve that publisher's placements.
            fits = found is not None and found.role == role
            if fits and (role != "publisher" or found.publisher_id == pub.id):
                keys[env_name] = current
                continue
            _, plaintext = await create_api_key(
                s, role, "demo", publisher_id=pub.id if role == "publisher" else None
            )
            keys[env_name] = plaintext
        await s.commit()

    values = {
        "ASIDE_URL": server_url.rstrip("/"),
        **keys,
        "ASIDE_PLACEMENT": DEMO_PLACEMENT,
        "ASIDE_DEMO_SCENARIO": scenario,
        "ASIDE_CURRENCY": sc.currency,
    }
    data_dir.mkdir(parents=True, exist_ok=True)
    env_path.write_text(
        "# Written by `aside seed-demo`. Demo credentials for examples/chat-demo. Do not use in production.\n"
        + "".join(f"{k}={v}\n" for k, v in values.items())
    )
    env_path.chmod(0o600)
    values["created"] = ", ".join(created) if created else "(campaigns already existed)"
    values["env_file"] = str(env_path)
    return values
