"""Day 4, Step 1: time-weighted book statistics, every expected value
worked out on paper first.

The point of the first test is to make time-weighting and
event-weighting give visibly different answers on the same three
states, so nobody can later confuse the two. The rest pin the
mechanics: bin splitting, one-sided time, zero-duration rows, the
basis-point definition, depth sums, event counts, and the windows.
"""
import pytest

from lob.lobster import EventType, LobsterMessage, ReferenceRow
from lob.micro import (CLOSE_NS, INCREMENT, NS, OPEN_NS, BinStats,
                       day_summary, states, time_weighted, window)


def msg(t_s: float, kind=EventType.ADD, size=100, price=10_000, oid=1,
        direction=1) -> LobsterMessage:
    return LobsterMessage(time_ns=OPEN_NS + int(round(t_s * NS)),
                          kind=kind, order_id=oid, size=size, price=price,
                          direction=direction)


def row(bid=None, ask=None, bids=None, asks=None) -> ReferenceRow:
    """A reference row from a best bid/ask (price, size) pair, or full
    ladders best first."""
    if bids is None:
        bids = () if bid is None else (bid,)
    if asks is None:
        asks = () if ask is None else (ask,)
    return ReferenceRow(asks=tuple(asks), bids=tuple(bids))


def test_time_weighting_differs_from_event_weighting():
    """States held for 1 s, 3 s, 6 s with spreads 2, 4, 1 (in price
    units): time-weighted (2*1 + 4*3 + 1*6) / 10 = 2.0 exactly; the
    event-weighted mean of the same numbers is 7/3."""
    msgs = [msg(0), msg(1), msg(4)]
    ref = [row((9_998, 10), (10_000, 10)),         # spread 2
           row((9_996, 10), (10_000, 10)),         # spread 4
           row((9_999, 10), (10_000, 10))]         # spread 1
    bins = time_weighted(msgs, ref, width_ns=10 * NS, open_ns=OPEN_NS,
                         close_ns=OPEN_NS + 10 * NS)
    (b,) = bins
    assert b.two_sided_ns == 10 * NS and b.hold_ns == 10 * NS
    assert b.spread_x_ns == (2 * 1 + 4 * 3 + 1 * 6) * NS
    assert b.tw_spread_ticks == 2.0
    assert sum((2, 4, 1)) / 3 == pytest.approx(7 / 3) != b.tw_spread_ticks
    assert b.messages == 3 and b.adds == 3 and b.add_shares == 300


def test_a_state_straddling_a_bin_edge_is_split():
    """Width 4 s: a state from t=2 lasting 8 s gives 2 s to bin 0,
    4 s to bin 1 and 2 s to bin 2."""
    msgs = [msg(2), msg(10)]
    ref = [row((9_990, 5), (10_000, 5)), row((9_990, 5), (10_000, 5))]
    bins = time_weighted(msgs, ref, width_ns=4 * NS, open_ns=OPEN_NS,
                         close_ns=OPEN_NS + 12 * NS)
    assert [b.two_sided_ns for b in bins] == [2 * NS, 4 * NS, 2 * NS + 2 * NS]
    # the second state (t=10 to the 12 s close) also lands in bin 2
    assert bins[2].messages == 1 and bins[0].messages == 1


def test_one_sided_and_empty_time_is_counted_never_averaged():
    msgs = [msg(0), msg(3), msg(5)]
    ref = [row(bid=(9_990, 5)),                    # bids only, 3 s
           row(),                                  # empty, 2 s
           row((9_990, 5), (10_000, 5))]           # two-sided, 5 s
    (b,) = time_weighted(msgs, ref, width_ns=10 * NS, open_ns=OPEN_NS,
                         close_ns=OPEN_NS + 10 * NS)
    assert (b.one_sided_ns, b.empty_ns, b.two_sided_ns) == (3 * NS, 2 * NS,
                                                            5 * NS)
    assert b.spread_x_ns == 10 * 5 * NS            # only the two-sided part
    assert b.tw_spread_ticks == 10.0
    assert b.one_sided_share == 0.5


def test_same_timestamp_rows_hold_for_zero_time():
    msgs = [msg(0), msg(0), msg(0), msg(4)]
    ref = [row((9_000, 1), (10_000, 1)),           # spread 1000, 0 s
           row((9_500, 1), (10_000, 1)),           # spread 500, 0 s
           row((9_998, 1), (10_000, 1)),           # spread 2, 4 s
           row((9_999, 1), (10_000, 1))]           # spread 1, 6 s
    holds = [s.hold_ns for s in states(msgs, ref, OPEN_NS + 10 * NS)]
    assert holds == [0, 0, 4 * NS, 6 * NS]
    (b,) = time_weighted(msgs, ref, width_ns=10 * NS, open_ns=OPEN_NS,
                         close_ns=OPEN_NS + 10 * NS)
    assert b.tw_spread_ticks == (2 * 4 + 1 * 6) / 10
    assert b.messages == 4                         # events still count


def test_basis_points_and_the_one_cent_share():
    """Bid 9,999 and ask 10,001 price units: spread 2, mid 10,000, so
    2 bps. A one-cent spread is INCREMENT units wide, not 1."""
    msgs = [msg(0), msg(5)]
    ref = [row((9_999, 1), (10_001, 1)),
           row((10_000 - INCREMENT, 1), (10_000, 1))]
    (b,) = time_weighted(msgs, ref, width_ns=10 * NS, open_ns=OPEN_NS,
                         close_ns=OPEN_NS + 10 * NS)
    assert b.one_cent_ns == 5 * NS and b.one_cent_share == 0.5
    # tw spread = (2*5 + 100*5)/10 = 51; tw mid = (10000*5 + 9950*5)/10
    assert b.tw_spread_ticks == 51.0
    assert b.tw_mid_ticks == 9_975.0
    assert b.tw_spread_bps == pytest.approx(1e4 * 51 / 9_975)
    assert b.tw_spread_cents == 0.51


def test_depth_sums_over_the_levels_that_exist():
    asks = [(10_000 + i, 10 * (i + 1)) for i in range(12)]   # 12 levels
    bids = [(9_999 - i, 5) for i in range(3)]                # 3 levels
    msgs = [msg(0)]
    (b,) = time_weighted(msgs, [row(bids=bids, asks=asks)], width_ns=10 * NS,
                         open_ns=OPEN_NS, close_ns=OPEN_NS + 10 * NS)
    assert b.tw_touch_depth == 10 + 5
    assert b.tw_depth5 == sum(10 * (i + 1) for i in range(5)) + 15
    assert b.tw_depth10 == sum(10 * (i + 1) for i in range(10)) + 15


def test_events_are_counted_in_their_own_bin_by_kind():
    msgs = [msg(0, EventType.ADD, 100), msg(1, EventType.REDUCE, 30),
            msg(2, EventType.DELETE, 70), msg(5, EventType.EXEC, 40),
            msg(6, EventType.EXEC_HIDDEN, 25), msg(7, EventType.HALT, 0)]
    ref = [row((9_990, 5), (10_000, 5))] * 6
    bins = time_weighted(msgs, ref, width_ns=5 * NS, open_ns=OPEN_NS,
                         close_ns=OPEN_NS + 10 * NS)
    a, c = bins
    assert (a.messages, a.adds, a.add_shares, a.cancels, a.cancel_shares) == (
        3, 1, 100, 2, 100)
    assert (c.messages, c.trades, c.trade_shares, c.hidden,
            c.hidden_shares) == (3, 1, 40, 1, 25)


def test_windows_sum_whole_bins_only_and_the_day_is_the_total():
    msgs = [msg(0)]
    ref = [row((9_990, 5), (10_000, 5))]
    bins = time_weighted(msgs, ref)                # the real session geometry
    assert len(bins) == 78
    w = day_summary(bins)
    assert w["day"].two_sided_ns == CLOSE_NS - OPEN_NS
    assert w["open5"].two_sided_ns == 300 * NS
    assert w["midday"].two_sided_ns == 3 * 3600 * NS
    assert w["close5"].two_sided_ns == 300 * NS
    assert w["day"].messages == 1 and w["midday"].messages == 0
    # a window that does not align with bin edges takes only whole bins
    part = window(bins, OPEN_NS + 100 * NS, OPEN_NS + 700 * NS)
    assert part.two_sided_ns == 300 * NS           # bin 1 only
    assert isinstance(part, BinStats) and part.label == ""


def test_misaligned_files_are_refused():
    with pytest.raises(ValueError, match="align"):
        list(states([msg(0), msg(1)], [row((1, 1), (2, 1))]))
