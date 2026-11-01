"""Generalized second-price (GSP) auction with quality = contextual relevance.

Every candidate is converted to an effective CPM (eCPM, in micros per 1000
impressions):

* CPM bid: eCPM = bid
* CPC bid: eCPM = bid x predicted CTR x 1000

Rank score = eCPM x relevance. The winner pays the smallest eCPM that would
still have beaten the runner-up's score (runner_score / winner_relevance),
never less than the floor and never more than its own bid. For CPC winners the
price is converted back to a per-click price.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any


@dataclass
class Candidate:
    campaign_id: str
    bid_type: str  # "cpc" | "cpm"
    bid_micros: int
    relevance: float
    ctr: float = 0.01
    payload: Any = None

    @property
    def ecpm(self) -> float:
        if self.bid_type == "cpm":
            return float(self.bid_micros)
        return self.bid_micros * self.ctr * 1000.0

    @property
    def score(self) -> float:
        return self.ecpm * self.relevance


@dataclass
class Placed:
    candidate: Candidate
    price_micros: int  # CPM price (per 1000 impressions) or CPC price (per click)
    ecpm_price: float


def predicted_ctr(impressions: int, clicks: int, prior_ctr: float, prior_weight: int = 200) -> float:
    """Smoothed CTR: behaves like ``prior_weight`` impressions at ``prior_ctr`` before data arrives."""
    return (clicks + prior_ctr * prior_weight) / (impressions + prior_weight)


def rank(candidates: list[Candidate], floor_cpm_micros: int) -> list[Candidate]:
    eligible = [c for c in candidates if c.ecpm >= floor_cpm_micros and c.relevance > 0]
    return sorted(eligible, key=lambda c: (-c.score, -c.ecpm, c.campaign_id))


def clearing_price(ranked: list[Candidate], index: int, floor_cpm_micros: int) -> Placed:
    """Price for ``ranked[index]`` against the next candidate in the ranking."""
    winner = ranked[index]
    runner = ranked[index + 1] if index + 1 < len(ranked) else None
    needed = runner.score / winner.relevance if runner is not None else 0.0
    ecpm_price = min(max(needed, float(floor_cpm_micros)), winner.ecpm)
    if winner.bid_type == "cpm":
        price = math.ceil(ecpm_price - 1e-9)
    else:
        price = math.ceil(ecpm_price / (winner.ctr * 1000.0) - 1e-9)
    price = max(0, min(price, winner.bid_micros))
    return Placed(winner, price, ecpm_price)


def impression_cost(cpm_price_micros: int) -> int:
    """Cost of one impression at a CPM price, rounded up to a whole micro."""
    return -(-cpm_price_micros // 1000)
