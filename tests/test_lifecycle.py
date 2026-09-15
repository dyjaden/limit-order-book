"""Day 4, Step 2: order lifecycles on a hand-built day.

Twelve messages, a hand-built reference, every expected value worked
out on paper: distances at submission, the two end kinds and their
shares, the censored order, the dark op, the delete-size disagreement,
the three cancel-to-trade definitions, and the Kaplan-Meier curve at
chosen times, computed by hand from the product-limit formula.
"""
import pytest

from lob.book import Side
from lob.lifecycle import (Life, distance_bucket, kaplan_meier, km_median,
                           lifecycles, lot, summarize, survival_at)
from lob.lobster import EventType, LobsterMessage, ReferenceRow
from lob.micro import NS, OPEN_NS

E = EventType


def msg(t_s, kind, oid, size, price, direction) -> LobsterMessage:
    return LobsterMessage(time_ns=OPEN_NS + int(round(t_s * NS)), kind=kind,
                          order_id=oid, size=size, price=price,
                          direction=direction)


def row(bids=(), asks=()) -> ReferenceRow:
    return ReferenceRow(asks=tuple(asks), bids=tuple(bids))


# a hand-built day. Prices in units of $0.0001; 100 units is one cent.
#   t     type  id   size  price      dir   what
DAY = [
    msg(0.0, E.ADD, 1, 100, 10_000, 1),      # bid 100 @ 100.00 (first row: distance unknown)
    msg(0.1, E.ADD, 2, 200, 10_100, -1),     # ask 200 @ 101.00, joins nothing (no prior ask): unknown
    msg(0.2, E.ADD, 3, 300, 10_000, 1),      # bid joins the best: distance 0
    msg(0.3, E.ADD, 4, 50, 10_050, 1),       # bid 50 units better than the best: under a cent, distance 0
    msg(0.4, E.ADD, 5, 100, 9_800, 1),       # bid 2 cents behind the best (10,050 - 9,800 = 250 units = 2 cents)
    msg(1.0, E.REDUCE, 3, 100, 10_000, 1),   # id 3 partial cancel: 300 -> 200
    msg(2.0, E.EXEC, 1, 40, 10_000, 1),      # id 1 first fill: 100 -> 60
    msg(2.5, E.EXEC, 1, 60, 10_000, 1),      # id 1 second fill: done, executed at t=2.5
    msg(3.0, E.DELETE, 3, 150, 10_000, 1),   # id 3 delete says 150, we hold 200: disagreement, ends cancelled
    msg(3.5, E.DELETE, 99, 10, 9_900, 1),    # id 99 never added: dark op
    msg(4.0, E.EXEC_HIDDEN, 0, 30, 10_050, -1),  # hidden execution, id 0
    msg(5.0, E.DELETE, 2, 200, 10_100, -1),  # id 2 deleted in full at t=5
    # ids 4 and 5 are never seen again: censored
]
# reference rows: the state AFTER each message (only the bests matter
# for distance; row i-1 is consulted for message i)
REF = [
    row(bids=[(10_000, 100)]),
    row(bids=[(10_000, 100)], asks=[(10_100, 200)]),
    row(bids=[(10_000, 400)], asks=[(10_100, 200)]),
    row(bids=[(10_050, 50), (10_000, 400)], asks=[(10_100, 200)]),
    row(bids=[(10_050, 50), (10_000, 400), (9_800, 100)], asks=[(10_100, 200)]),
    row(bids=[(10_050, 50), (10_000, 300), (9_800, 100)], asks=[(10_100, 200)]),
    row(bids=[(10_050, 50), (10_000, 260), (9_800, 100)], asks=[(10_100, 200)]),
    row(bids=[(10_050, 50), (10_000, 200), (9_800, 100)], asks=[(10_100, 200)]),
    row(bids=[(10_050, 50), (9_800, 100)], asks=[(10_100, 200)]),
    row(bids=[(10_050, 50), (9_800, 100)], asks=[(10_100, 200)]),
    row(bids=[(10_050, 50), (9_800, 100)], asks=[(10_100, 200)]),
    row(bids=[(10_050, 50), (9_800, 100)]),
]


def test_distances_at_submission_use_the_row_before_the_add():
    res = lifecycles(DAY, REF)
    by = {l.order_id: l for l in res.lives}
    assert by[1].distance_cents is None            # no row before message 0
    assert by[2].distance_cents is None            # no ask side to measure from
    assert by[3].distance_cents == 0               # joined the best bid
    assert by[4].distance_cents == 0               # 50 units better: under a cent
    assert by[5].distance_cents == 2               # 250 units behind: 2 cents
    assert by[3].side is Side.BID and by[2].side is Side.ASK
    assert distance_bucket(-1) == "improve" and distance_bucket(7) == "6_to_10"
    assert distance_bucket(11) == "over_10" and distance_bucket(None) == "unknown"


def test_ends_shares_and_the_accounting_counters():
    res = lifecycles(DAY, REF)
    by = {l.order_id: l for l in res.lives}
    assert by[1].end == "executed" and by[1].executed == 100
    assert by[1].fills == 2 and by[1].ended_ns == DAY[7].time_ns
    assert by[1].first_fill_ns == DAY[6].time_ns
    assert by[1].lifetime_ns == int(2.5 * NS)
    assert by[3].end == "cancelled" and by[3].cancelled == 300
    assert by[3].lifetime_ns == int(2.8 * NS)     # added at 0.2, deleted at 3.0
    assert by[2].end == "cancelled" and by[2].lifetime_ns == int(4.9 * NS)
    assert by[4].end == "censored" and by[5].end == "censored"
    assert by[4].lifetime_ns is None and by[4].censor_ns == 0
    assert res.size_disagreements == 1             # delete said 150, held 200
    assert dict(res.dark_ops) == {"DELETE": 1} and res.dark_shares == 10
    assert res.hidden_execs == 1 and res.readded == 0
    assert res.by_type["ADD"] == 5 and res.by_type["EXEC"] == 2


def test_the_three_cancel_to_trade_definitions_computed_on_paper():
    stats = {(s.section, s.name): s.value for s in summarize(lifecycles(DAY, REF))}
    # by messages: cancels = 1 REDUCE + 3 DELETE (the dark one counts: it
    # is a message) = 4; trades = 2 EXEC + 1 hidden = 3
    assert stats["cancel_to_trade", "by_messages_full_day"] == pytest.approx(4 / 3)
    # the day is all inside 9:35? no: it starts at 9:30:00, so the MIDAS
    # window (from 9:35) holds none of it
    assert stats["cancel_to_trade", "by_messages_midas_window"] is None
    # by orders: 2 cancelled (ids 2, 3) / 1 executed (id 1)
    assert stats["cancel_to_trade", "by_orders"] == 2.0
    # by shares: cancelled 300 + 200 = 500 over executed 100
    assert stats["cancel_to_trade", "by_shares"] == 5.0
    assert stats["cancel_to_trade", "hidden_share_of_executions"] == pytest.approx(1 / 3)
    # executions 40, 60 (odd) and the hidden 30 (odd): 3 of 3
    assert stats["cancel_to_trade", "odd_lot_share_of_executions"] == 1.0
    assert stats["ends", "orders_added"] == 5
    assert stats["ends", "share_censored"] == pytest.approx(2 / 5)
    assert stats["ends", "shares_unresolved"] == 150          # ids 4 and 5
    assert stats["fill_by_distance", "at_best_orders"] == 2  # ids 3 and 4
    assert stats["fill_by_distance", "at_best_fill_rate_shares"] == 0.0
    assert stats["fill_by_distance", "unknown_fill_rate_orders"] == 0.5  # id 1 of ids 1, 2
    assert stats["sizes", "adds_round_lot_share"] == pytest.approx(4 / 5)
    assert lot(50) == "odd" and lot(100) == "round" and lot(150) == "mixed"


def test_kaplan_meier_by_hand():
    """Events at 1, 2, 2, 4; censored at 3 and 5. At risk: 6 at t=1 (one
    dies: S = 5/6); 5 at t=2 (two die: S = 5/6 * 3/5 = 1/2); the censored
    order at 3 leaves; 2 at t=4 (one dies: S = 1/2 * 1/2 = 1/4); the
    order censored at 5 never dies. The median is 2."""
    curve = kaplan_meier([1, 2, 2, 4], [3, 5])
    assert curve == [(1, pytest.approx(5 / 6)), (2, pytest.approx(0.5)),
                     (4, pytest.approx(0.25))]
    assert survival_at(curve, 0) == 1.0
    assert survival_at(curve, 1) == pytest.approx(5 / 6)
    assert survival_at(curve, 3) == pytest.approx(0.5)
    assert survival_at(curve, 100) == pytest.approx(0.25)
    assert km_median(curve) == 2
    # a tie between a death and a censoring at the same time: the censored
    # order is still at risk for that death
    assert kaplan_meier([2], [2]) == [(2, 0.5)]
    # nothing ever gets to one half: no median
    assert km_median(kaplan_meier([1], [2, 3, 4])) is None


def test_the_day_survival_curve_matches_the_hand_computation():
    """Observed lifetimes 2.5 s (id 1), 2.8 s (id 3), 4.9 s (id 2);
    censored at 0 s (ids 4 and 5, never seen after their add). With the
    censorings at 0, both leave the at-risk set before the first death,
    so S steps 2/3, 1/3, 0 at the three deaths."""
    res = lifecycles(DAY, REF)
    observed = sorted(l.lifetime_ns for l in res.lives if l.lifetime_ns is not None)
    censored = [l.censor_ns for l in res.lives if l.lifetime_ns is None]
    assert censored == [0, 0]
    curve = kaplan_meier(observed, censored)
    assert [round(s, 6) for _, s in curve] == [round(2 / 3, 6), round(1 / 3, 6), 0.0]
    stats = {(s.section, s.name): s.value for s in summarize(res)}
    assert stats["lifetime_km", "censored_share"] == pytest.approx(0.4)
    assert stats["lifetime_km", "median_ms"] == 2_800.0       # id 3's death
    assert stats["lifetime_km", "S_at_1s"] == 1.0
    assert stats["lifetime_km", "S_at_10s"] == 0.0
    assert stats["lifetime_naive", "observed_p50_ms"] == 2_800.0
    assert stats["lifetime_naive", "share_under_1s"] == 0.0


def test_misaligned_files_are_refused():
    with pytest.raises(ValueError, match="align"):
        lifecycles(DAY, REF[:-1])


def test_life_remaining_is_derived_never_stored():
    life = Life(7, Side.BID, 10_000, 0, 100, 0)
    life.executed, life.cancelled = 30, 20
    assert life.remaining == 50 and life.lifetime_ns is None
