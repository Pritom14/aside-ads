"""Daily delivery counts: how serve requests ended per placement, and matches lost to budget.

Only counts are stored, keyed by day, publisher, placement and outcome (``filled`` or the no-ad
reason), and by campaign for budget misses. Nothing about the conversation is recorded. A failure
here is logged and never fails the serve request.
"""

from __future__ import annotations

import logging

from aside_ads.db import insert_for
from aside_ads.models import BudgetMiss, ServeOutcome
from aside_ads.runtime import Runtime

log = logging.getLogger("aside.delivery")

# What each no-ad reason means, and what usually fixes it. Shared by reports and the agent connector.
OUTCOMES: dict[str, str] = {
    "filled": "an ad was shown",
    "sensitive_context": "the conversation touched a sensitive topic, so no ad by design",
    "no_eligible_campaigns": (
        "no active campaign could run here (placement, schedule, geo, keyword or category rules)"
    ),
    "below_relevance_threshold": (
        "campaigns could run but none fit the conversation closely enough; "
        "new campaigns or targeting lines for these topics would fill them"
    ),
    "budget_exhausted": "a campaign matched but its budget was spent or pacing held it back",
    "frequency_capped": "the user had already seen the matching ads as often as their caps allow",
    "below_floor": "matching bids were below the floor price",
}


async def record_delivery(
    rt: Runtime, publisher_id: str, placement_key: str, outcome: str | None, missed: list[str]
) -> None:
    now = rt.now()
    day = rt.day(now)
    try:
        async with rt.sessionmaker() as s:
            ins = insert_for(s, ServeOutcome).values(
                day=day,
                publisher_id=publisher_id,
                placement_key=placement_key,
                outcome=(outcome or "no_ad")[:32],
                requests=1,
            )
            await s.execute(
                ins.on_conflict_do_update(
                    index_elements=["day", "publisher_id", "placement_key", "outcome"],
                    set_={"requests": ServeOutcome.requests + 1},
                )
            )
            for campaign_id in dict.fromkeys(missed):
                miss = insert_for(s, BudgetMiss).values(
                    campaign_id=campaign_id, day=day, requests=1, first_at=now
                )
                await s.execute(
                    miss.on_conflict_do_update(
                        index_elements=["campaign_id", "day"],
                        set_={"requests": BudgetMiss.requests + 1},
                    )
                )
            await s.commit()
    except Exception:  # counting must never break serving
        log.exception("could not record delivery counts")
