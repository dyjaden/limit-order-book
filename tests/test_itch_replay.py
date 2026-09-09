"""Day 3, Step 2: the full-depth replayer, from an empty book.

Every rule the replayer enforces is pinned here on hand-built message
sequences with exactly known outcomes, and then the whole synthetic day
must come back with every counter at zero -- the loop-closer, because
the writer resolved its fills front-first through a real Book and the
replayer independently demands front-of-queue on every 'E'.
"""
import pytest

from lob.book import Side
from lob.itch import ItchMessage
from lob.itch_replay import ItchReplayer
from make_fake_itch import build_fake_day

T = 34_200_000_000_000


def msg(kind, ref=None, *, new=None, side=None, shares=None, price=None,
        t=T, code=None):
    return ItchMessage(kind, t, 7, order_ref=ref, new_order_ref=new,
                       side=side, shares=shares, price=price,
                       event_code=code)


def add(ref, side, price, shares, kind="A"):
    return msg(kind, ref, side=side, shares=shares, price=price)


def replay(*messages):
    r = ItchReplayer()
    r.run(messages)
    return r


def test_adds_build_an_uncrossed_book_from_empty():
    r = replay(add(1, "B", 9_990, 100), add(2, "S", 10_010, 50),
               add(3, "B", 9_980, 200, kind="F"))
    assert (r.book.best_bid(), r.book.best_ask()) == (9_990, 10_010)
    assert len(r.book) == 3
    assert r.stats.applied == 3 and r.stats.clean


def test_x_partial_cancel_keeps_queue_position():
    r = replay(add(1, "B", 9_990, 100), add(2, "B", 9_990, 80),
               msg("X", 1, shares=40))
    assert r.book.orders_at(Side.BID, 9_990) == (1, 2)   # 1 still front
    assert r.book.order(1).qty == 60
    # an X that would zero the order is not a delete in disguise
    r.apply(msg("X", 1, shares=60))
    assert r.stats.anomalies_total == 1
    assert r.book.order(1).qty == 60                     # untouched
    assert not r.stats.clean


def test_unknown_ids_are_counted_never_guessed():
    r = replay(add(1, "B", 9_990, 100),
               msg("D", 99), msg("X", 98, shares=10), msg("E", 97, shares=5),
               msg("U", 96, new=200, shares=10, price=9_980))
    assert r.stats.unknown_refs == 4
    assert len(r.book) == 1 and r.book.order(1).qty == 100
    r.apply(msg("D", 1))
    assert len(r.book) == 0 and r.stats.unknown_refs == 4


def test_e_takes_shares_off_the_front_and_full_fill_removes():
    r = replay(add(1, "B", 9_990, 100), add(2, "B", 9_990, 80),
               msg("E", 1, shares=30))
    assert r.book.order(1).qty == 70
    assert r.book.orders_at(Side.BID, 9_990) == (1, 2)
    r.apply(msg("E", 1, shares=70))                      # full fill
    assert r.book.orders_at(Side.BID, 9_990) == (2,)
    assert r.stats.full_fills == 1
    r.apply(msg("E", 2, shares=80))
    assert r.book.best_bid() is None
    assert r.stats.clean and r.stats.front_violations == 0


def test_e_off_the_front_is_flagged_but_message_truth_still_applies():
    r = replay(add(1, "B", 9_990, 100), add(2, "B", 9_990, 80),
               msg("E", 2, shares=30))                   # 2 is NOT front
    assert r.stats.front_violations == 1
    assert r.book.order(2).qty == 50                     # applied anyway
    assert r.book.order(1).qty == 100
    assert not r.stats.clean


def test_c_executes_like_e_but_carries_no_front_assertion():
    r = replay(add(1, "S", 10_010, 100), add(2, "S", 10_010, 60),
               msg("C", 2, shares=20, price=10_005))     # price-improved
    assert r.stats.front_violations == 0                 # C never asserts
    assert r.book.order(2).qty == 40
    assert r.stats.counts["C"] == 1 and r.stats.clean


def test_u_retires_the_old_id_and_the_new_one_joins_the_back():
    r = replay(add(1, "B", 9_990, 100), add(2, "B", 9_990, 80),
               msg("U", 1, new=3, shares=50, price=9_990))
    assert r.book.orders_at(Side.BID, 9_990) == (2, 3)   # position lost
    assert r.book.order(3).qty == 50
    with pytest.raises(KeyError):
        r.book.order(1)                                  # old id retired
    r.apply(msg("X", 1, shares=10))                      # old id is dead
    assert r.stats.unknown_refs == 1
    r.apply(msg("D", 3))                                 # new id lives
    assert r.book.orders_at(Side.BID, 9_990) == (2,)


def test_u_that_would_cross_is_refused_and_the_original_stands():
    r = replay(add(1, "B", 9_990, 100), add(2, "B", 9_990, 80),
               add(3, "S", 10_010, 50),
               msg("U", 1, new=4, shares=100, price=10_010))
    assert r.stats.crossing_adds == 1
    assert r.book.orders_at(Side.BID, 9_990) == (1, 2)   # 1 still FRONT
    assert r.book.order(1).qty == 100
    with pytest.raises(KeyError):
        r.book.order(4)                                  # new id never born
    # a U to an id already resting is impossible too
    r.apply(msg("U", 1, new=2, shares=10, price=9_980))
    assert r.stats.anomalies_total == 1
    assert r.book.order(1).qty == 100


def test_crossing_adds_are_refused_and_counted():
    r = replay(add(1, "S", 10_000, 50), add(2, "B", 10_000, 50))
    assert r.stats.crossing_adds == 1
    assert r.book.best_bid() is None and len(r.book) == 1


def test_noops_are_counted_and_never_touch_the_book():
    r = replay(msg("S", code="Q"), msg("R"), msg("P", 1, shares=10,
               price=9_990, side="B"), msg("Q"), msg("B"), msg("H"))
    assert len(r.book) == 0 and r.stats.applied == 0
    assert sum(r.stats.counts.values()) == 6
    assert r.stats.clean


def test_the_synthetic_day_replays_clean_end_to_end():
    """The loop-closer: the writer resolved fills front-first through a
    real Book; the replayer independently demands front-of-queue on
    every 'E' and translates every 'U'. All counters zero, or Step 2
    has no business asking a real file for the same verdict."""
    _, expected = build_fake_day(3_000, seed=11)
    r = ItchReplayer()
    stats = r.run(expected)
    assert stats.clean, (stats.unknown_refs, stats.crossing_adds,
                         stats.front_violations,
                         [a.render() for a in stats.anomalies])
    assert stats.counts["A"] > 0 and stats.counts["U"] > 0
    assert stats.counts["X"] > 0 and stats.counts["E"] > 0
    bid, ask = r.book.best_bid(), r.book.best_ask()
    assert bid is None or ask is None or bid < ask
    for side in (Side.BID, Side.ASK):
        for price in r.book.prices(side):
            ids = r.book.orders_at(side, price)
            total = dict(r.book.depth(side, 10_000))[price]
            assert total == sum(r.book.order(i).qty for i in ids) > 0
