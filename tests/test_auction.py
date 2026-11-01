"""Ranking by bid x relevance and generalized second-price clearing."""

from __future__ import annotations

from sqlalchemy import select

from aside_ads.auction import Candidate, clearing_price, impression_cost, predicted_ctr, rank
from aside_ads.models import Campaign

FLOOR = 100_000  # 0.10 CPM in micros


def test_rank_uses_bid_times_relevance() -> None:
    rich_but_vague = Candidate("a", "cpm", 10_000_000, relevance=0.3)  # score 3.0M
    cheap_but_spot_on = Candidate("b", "cpm", 4_000_000, relevance=0.9)  # score 3.6M
    assert [c.campaign_id for c in rank([rich_but_vague, cheap_but_spot_on], FLOOR)] == ["b", "a"]


def test_second_price_is_runner_up_score_over_winner_quality() -> None:
    w = Candidate("w", "cpm", 4_000_000, relevance=0.9)
    r = Candidate("r", "cpm", 10_000_000, relevance=0.3)
    ranked = rank([w, r], FLOOR)
    placed = clearing_price(ranked, 0, FLOOR)
    # runner score 3.0M / winner relevance 0.9 = 3.333M CPM, below the 4M bid
    assert placed.price_micros == 3_333_334
    assert placed.price_micros <= w.bid_micros


def test_single_bidder_pays_floor() -> None:
    ranked = rank([Candidate("w", "cpm", 4_000_000, relevance=0.8)], FLOOR)
    assert clearing_price(ranked, 0, FLOOR).price_micros == FLOOR


def test_bids_below_floor_are_excluded() -> None:
    assert rank([Candidate("w", "cpm", 50_000, relevance=1.0)], FLOOR) == []


def test_cpc_converted_through_predicted_ctr() -> None:
    cpc = Candidate("c", "cpc", 2_000_000, relevance=1.0, ctr=0.02)  # eCPM = 2.0 * 0.02 * 1000 = $40
    cpm = Candidate("m", "cpm", 20_000_000, relevance=1.0)  # $20
    ranked = rank([cpc, cpm], FLOOR)
    assert ranked[0].campaign_id == "c"
    placed = clearing_price(ranked, 0, FLOOR)
    # Needs eCPM $20 -> CPC = 20 / (0.02 * 1000) = $1.00
    assert placed.price_micros == 1_000_000


def test_price_never_exceeds_bid_when_floor_is_high() -> None:
    ranked = rank([Candidate("w", "cpm", 300_000, relevance=1.0)], 200_000)
    assert clearing_price(ranked, 0, 200_000).price_micros <= 300_000


def test_ctr_smoothing_and_impression_cost() -> None:
    assert predicted_ctr(0, 0, 0.01) == 0.01
    assert 0.01 < predicted_ctr(1000, 100, 0.01) < 0.1
    assert impression_cost(5_000_000) == 5_000  # $5 CPM = $0.005 per impression
    assert impression_cost(1_001) == 2  # rounds up


async def test_auction_end_to_end_bills_second_price(world) -> None:  # type: ignore[no-untyped-def]
    text = "slow postgres queries\ndatabase indexing"
    high = await world.campaign("High", text, bid="8")
    await world.campaign("Low", text, bid="3")
    out = await world.serve("my postgres queries are slow, should I add an index?")
    assert out["ad"]["campaign_id"] == high
    r = await world.impression(out["ad"])
    assert r.headers["x-aside-status"] == "counted"
    async with world.rt.sessionmaker() as s:
        c = (await s.execute(select(Campaign).where(Campaign.id == high))).scalar_one()
    # Equal relevance, so the winner pays the runner-up's $3 CPM: 3000 micros per impression, not 8000.
    assert c.spend_total_micros == 3_000
