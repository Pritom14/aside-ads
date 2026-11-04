"""Fill a demo Aside database with simulated traffic, to try the agent connector without real users.

Seeds the cooking demo (the "Rasoi AI" app and five advertisers, amounts in INR), then replays a day
of chat traffic for yesterday and today up to now (``--days`` for more history): serves,
impressions, clicks and a few conversions, through the real API in-process. One advertiser spends
its daily budget early, some questions have no relevant campaign, and a few are sensitive, so the
reports have something to say.

    python examples/agent-demo/simulate.py --data-dir .aside-agent-demo
    export ASIDE_DATA_DIR=.aside-agent-demo ASIDE_TIMEZONE=Asia/Kolkata ASIDE_CURRENCY=INR
    ASIDE_FLOOR_CPM=20 aside serve

Every number it produces is simulated. Do not run it against a real database.
"""

from __future__ import annotations

import argparse
import asyncio
import random
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import select

from aside_ads.app import create_app
from aside_ads.config import Settings
from aside_ads.models import Campaign
from aside_ads.runtime import Runtime
from aside_ads.seed import DEMO_PLACEMENT, seed_demo

FLOOR_CPM = 20  # INR per 1000 impressions; start the server with ASIDE_FLOOR_CPM=20 too
UA = "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148"

# What people ask a cooking assistant, grouped by the advertiser that fits (None: no advertiser does).
QUESTIONS: list[tuple[str | None, float, list[str]]] = [
    (
        "Lohasutra",
        0.30,
        [
            "my iron kadhai keeps rusting after I wash it, what am I doing wrong?",
            "eggs keep sticking to my tawa even with oil, how do I fix it?",
            "how do I season a new cast iron kadhai before first use?",
            "which is better for dosa, a cast iron tawa or a non-stick pan?",
            "my non-stick pan is scratched and food sticks now, should I buy a new one?",
            "is cast iron cookware safe for cooking tomato curries?",
        ],
    ),
    (
        "Thaila Now",
        0.20,
        [
            "I want to cook paneer butter masala for 6 guests tonight, what do I need?",
            "what ingredients do I need for chicken biryani for dinner tonight?",
            "cooking dal makhani for my family tonight, give me a shopping list",
            "guests are coming at 8, quick dinner menu with ingredients please",
        ],
    ),
    (
        "Jeera Lane",
        0.10,
        [
            "how do I make garam masala at home from whole spices?",
            "my chai masala tastes flat, which spices should I buy fresh?",
            "how long do ground spices stay fresh in a masala dabba?",
        ],
    ),
    (
        "Chakkiwell",
        0.08,
        [
            "my mixer grinder can't make smooth idli batter, which one should I get?",
            "pressure cooker whistle is leaking steam, is it the gasket?",
            "wet grinder or mixer grinder for dosa batter at home?",
        ],
    ),
    (
        "Tadka Talim",
        0.07,
        [
            "I am a total beginner, how do I learn to cook basic Indian food?",
            "my rotis come out hard every time, how can I improve?",
            "are there step by step classes to learn biryani properly?",
        ],
    ),
    (
        None,
        0.22,
        [
            "how do I bake an eggless chocolate cake in a pressure cooker?",
            "what can I use instead of curd in a marinade?",
            "how many calories are in a plate of rajma chawal?",
            "what is the history of the samosa?",
            "convert 2 cups of flour to grams",
            "what wine goes with butter chicken?",
            "how do I make a sourdough starter in a humid city?",
            "is it ok to refreeze thawed chicken?",
        ],
    ),
    (
        None,
        0.03,
        [
            "I think I have food poisoning, vomiting since last night, what should I do?",
            "my child ate peanuts and her lips are swelling",
        ],
    ),
]
CTR = {"Lohasutra": 0.07, "Thaila Now": 0.05, "Jeera Lane": 0.03, "Chakkiwell": 0.04, "Tadka Talim": 0.025}
CONVERT = {"Thaila Now": (0.25, 450, 900), "Lohasutra": (0.08, 2499, 2499), "Chakkiwell": (0.05, 3999, 3999)}
# Share of a day's chats per local hour: light mornings, busy lunch and dinner.
HOURS = {
    7: 2,
    8: 4,
    9: 5,
    10: 6,
    11: 9,
    12: 11,
    13: 10,
    14: 6,
    15: 5,
    16: 6,
    17: 8,
    18: 10,
    19: 11,
    20: 10,
    21: 7,
    22: 4,
}


def _times(day: datetime, n: int, until: datetime | None, rng: random.Random) -> list[datetime]:
    total = sum(HOURS.values())
    out = []
    for hour, weight in HOURS.items():
        for _ in range(round(n * weight / total)):
            t = day.replace(hour=hour, minute=0, second=0) + timedelta(seconds=rng.uniform(0, 3599))
            if until is None or t < until:
                out.append(t)
    return sorted(out)


def _question(rng: random.Random) -> tuple[str | None, str]:
    group = rng.choices(QUESTIONS, weights=[w for _, w, _ in QUESTIONS])[0]
    return group[0], rng.choice(group[2])


async def simulate(data_dir: Path, per_day: int, seed: int, days: int = 2) -> dict[str, Any]:
    tz = ZoneInfo("Asia/Kolkata")
    settings = Settings(
        _env_file=None,
        data_dir=data_dir,
        timezone="Asia/Kolkata",
        currency="INR",
        floor_cpm=FLOOR_CPM,
        rate_limit_per_minute=1_000_000,
        public_url="http://127.0.0.1:8000",
        connector=False,
    )
    rt = await Runtime.create(settings)
    env = await seed_demo(
        rt.sessionmaker,
        data_dir,
        "http://127.0.0.1:8000/landing",
        "http://127.0.0.1:8000",
        scenario="cooking",
    )
    async with rt.sessionmaker() as s:
        # The demo's budget story: the cookware campaign spends its whole daily budget early.
        lohasutra = (
            await s.execute(select(Campaign).where(Campaign.name.startswith("Lohasutra")))
        ).scalar_one()
        lohasutra.daily_budget_micros = 90_000_000
        lohasutra.total_budget_micros = 30 * lohasutra.daily_budget_micros
        lohasutra.pacing = "asap"
        await s.commit()

    app = create_app(runtime=rt)
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8000")
    pub = {"Authorization": f"Bearer {env['ASIDE_API_KEY']}"}
    admin = {"Authorization": f"Bearer {env['ASIDE_ADMIN_KEY']}"}
    rng = random.Random(seed)
    users = [f"user-{i}" for i in range(int(per_day * 0.6))]
    now = datetime.now(tz)
    today = datetime.combine(now.date(), time.min, tzinfo=tz)
    counts = {"requests": 0, "ads": 0, "clicks": 0, "conversions": 0}
    past = [(today - timedelta(days=k), None) for k in range(max(days, 1) - 1, 0, -1)]
    for day, until in [*past, (today, now)]:
        for local in _times(day, per_day, until, rng):
            t = local.astimezone(timezone.utc)  # the server clock runs in UTC
            rt.clock = lambda t=t: t
            fits, question = _question(rng)
            r = await client.post(
                "/v1/serve",
                headers=pub,
                json={"context": question, "placement": DEMO_PLACEMENT, "user_id": rng.choice(users)},
            )
            counts["requests"] += 1
            ad = r.json().get("ad")
            if not ad:
                continue
            counts["ads"] += 1
            rt.clock = lambda t=t: t + timedelta(seconds=2)
            await client.get(
                ad["impression_url"].replace("http://127.0.0.1:8000", ""), headers={"User-Agent": UA}
            )
            name = next((n for n in CTR if ad["advertiser"].startswith(n)), None)
            if name is None or rng.random() > CTR[name]:
                continue
            rt.clock = lambda t=t: t + timedelta(seconds=25)
            click = await client.get(
                ad["url"].replace("http://127.0.0.1:8000", ""), headers={"User-Agent": UA}
            )
            counts["clicks"] += 1
            click_id = parse_qs(urlparse(click.headers.get("location", "")).query).get(
                "aside_click_id", [None]
            )[0]
            rate, low, high = CONVERT.get(name, (0, 0, 0))
            if click_id and rng.random() < rate:
                rt.clock = lambda t=t: t + timedelta(minutes=12)
                await client.post(
                    "/v1/conversions",
                    headers=admin,
                    json={"click_id": click_id, "value": rng.randint(low, high)},
                )
                counts["conversions"] += 1
    await client.aclose()
    await rt.close()
    return {**counts, "admin_key_file": env["env_file"]}


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--data-dir", type=Path, default=Path(".aside-agent-demo"))
    p.add_argument("--per-day", type=int, default=5000, help="chats per simulated day")
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--days", type=int, default=2, help="days to simulate, ending with today so far")
    args = p.parse_args()
    out = asyncio.run(simulate(args.data_dir, args.per_day, args.seed, args.days))
    print(
        f"Simulated {out['requests']:,} chats: {out['ads']:,} ads, {out['clicks']:,} clicks, "
        f"{out['conversions']:,} conversions. Keys are in {out['admin_key_file']}."
    )


if __name__ == "__main__":
    main()
