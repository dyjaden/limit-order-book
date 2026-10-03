"""Day 5, Step 2: the two clocks, every bucket worked out on paper first.

Eight hand-built events at chosen times aggregate to known ten-second
bins (one of them empty, one holding a zero event) and to known
three-update buckets (with a dropped remainder and no gap between
consecutive buckets). The case that matters most is the one where a
bucket's price change, close minus open, differs from the sum of its
events' changes: the aggregator must difference the ends. Then the
loop-closer again: the level-1 and level-10 views of one synthetic day
give the same bucket series on both clocks.
"""
import pytest

from filter_experiment import filter_day, write_files
from lob.lobster import read_messages, read_orderbook
from lob.micro import NS, OPEN_NS
from lob.ofi import (BUCKET_COLUMNS, Bucket, Event, aggregate_calendar,
                     aggregate_events, events, touches)
from make_fake_itch import build_fake_day


def ev(t_prev_s, t_s, e, mid2_prev, mid2) -> Event:
    return Event(OPEN_NS + int(round(t_prev_s * NS)), OPEN_NS + int(round(t_s * NS)),
                 e, mid2_prev, mid2)


# eight events inside the first minute, one more after it
EIGHT = [ev(0.5, 2, +100, 20_000, 20_000),      # bin 0
         ev(2, 7, -30, 20_000, 19_900),          # bin 0
         ev(7, 12, 0, 19_900, 19_900),           # bin 1: a hidden execution
         ev(12, 15, +50, 19_900, 20_100),        # bin 1
         ev(15, 31, -80, 20_100, 20_000),        # bin 3 (bin 2 is empty)
         ev(31, 33, +10, 20_000, 20_000),        # bin 3
         ev(33, 48, -5, 20_000, 19_950),         # bin 4
         ev(48, 59, +200, 19_950, 20_200)]       # bin 5
LATE = ev(59, 61, +1_000, 20_200, 20_300)        # outside the window
CLOSE = OPEN_NS + 60 * NS


def sums(b: Bucket) -> tuple:
    return tuple(getattr(b, c) for c in BUCKET_COLUMNS)


def test_calendar_bins_by_hand_including_the_empty_one():
    bins = aggregate_calendar(EIGHT + [LATE], 10 * NS, OPEN_NS, CLOSE)
    assert len(bins) == 6
    assert [b.start_ns for b in bins] == [OPEN_NS + 10 * i * NS for i in range(6)]
    assert all(b.duration_ns == 10 * NS for b in bins)
    # (n_events, n_updates, ofi, abs_flow, mid2_open, mid2_close)
    assert sums(bins[0]) == (2, 2, +70, 130, 20_000, 19_900)
    assert sums(bins[1]) == (2, 1, +50, 50, 19_900, 20_100)
    assert sums(bins[2]) == (0, 0, 0, 0, 20_100, 20_100)      # empty, not missing
    assert sums(bins[3]) == (2, 2, -70, 90, 20_100, 20_000)
    assert sums(bins[4]) == (1, 1, -5, 5, 20_000, 19_950)
    assert sums(bins[5]) == (1, 1, +200, 200, 19_950, 20_200)
    assert [b.dmid_cents for b in bins] == [-0.5, 1.0, 0.0, -0.5, -0.25, 1.25]
    # the bins tile the window: total flow and total price change agree
    # with the events inside it, and the late event was left out
    assert sum(b.n_events for b in bins) == 8
    assert sum(b.ofi for b in bins) == sum(e.e for e in EIGHT)
    assert sum(b.dmid_cents for b in bins) == (20_200 - 20_000) / 200
    assert bins[1].dmid_bps == pytest.approx(10_000 * 200 / 19_900)


def test_the_bins_open_is_the_mid_prevailing_at_the_edge_not_a_sum_of_changes():
    """A gapped stream: the fifth event claims a different mid before it
    than the fourth left behind. The bin's open is what prevailed at the
    edge (the last observation before it), and its change is close minus
    open, which is not the sum of the per-event changes inside it."""
    gapped = list(EIGHT)
    gapped[4] = ev(15, 31, -80, 20_000, 20_000)              # mid2_prev lies
    bins = aggregate_calendar(gapped, 10 * NS, OPEN_NS, CLOSE)
    b = bins[3]
    assert b.mid2_open == 20_100 and b.mid2_close == 20_000
    assert b.dmid_cents == -0.5
    assert sum(e.dmid_cents for e in gapped[4:6]) == 0.0      # the sum would say nothing moved


def test_bins_before_the_first_event_hold_the_first_observed_mid():
    late_start = [ev(25, 27, +40, 20_000, 20_200)]
    bins = aggregate_calendar(late_start, 10 * NS, OPEN_NS, CLOSE)
    assert sums(bins[0]) == (0, 0, 0, 0, 20_000, 20_000)
    assert sums(bins[1]) == (0, 0, 0, 0, 20_000, 20_000)
    assert sums(bins[2]) == (1, 1, +40, 40, 20_000, 20_200)
    assert sums(bins[5]) == (0, 0, 0, 0, 20_200, 20_200)
    # and an event before the open moves the running mid without being counted
    early = [ev(-2, -1, +500, 19_000, 19_500)] + late_start
    bins = aggregate_calendar(early, 10 * NS, OPEN_NS, CLOSE)
    assert sums(bins[0]) == (0, 0, 0, 0, 19_500, 19_500)
    assert sum(b.n_events for b in bins) == 1


def test_a_last_bin_cut_at_the_close_and_an_empty_stream():
    bins = aggregate_calendar(EIGHT, 25 * NS, OPEN_NS, CLOSE)
    assert len(bins) == 3 and bins[2].duration_ns == 10 * NS
    assert sums(bins[2]) == (1, 1, +200, 200, 19_950, 20_200)
    empty = aggregate_calendar([], 10 * NS, OPEN_NS, CLOSE)
    assert len(empty) == 6
    assert empty[0].mid2_open is None and empty[0].dmid_cents is None
    assert empty[0].dmid_bps is None
    with pytest.raises(ValueError):
        aggregate_calendar(EIGHT, 0, OPEN_NS, CLOSE)


def test_event_buckets_by_hand_with_the_remainder_dropped():
    buckets = aggregate_events(EIGHT + [LATE], 3)
    assert len(buckets) == 2                     # 8 updates: two full buckets, two dropped
    a, b = buckets
    # events 1 to 4: three updates and one zero event
    assert sums(a) == (4, 3, +120, 180, 20_000, 20_100)
    assert a.start_ns == OPEN_NS + int(0.5 * NS)              # the first observation
    assert a.end_ns == OPEN_NS + 15 * NS
    # events 5 to 7
    assert sums(b) == (3, 3, -75, 95, 20_100, 19_950)
    assert b.start_ns == a.end_ns                             # no gap between buckets
    assert b.end_ns == OPEN_NS + 48 * NS and b.duration_ns == 33 * NS
    assert b.dmid_cents == -0.75
    assert sum(x.n_events for x in buckets) == 7              # two events dropped
    assert [x.index for x in buckets] == [0, 1]
    with pytest.raises(ValueError):
        aggregate_events(EIGHT, 0)


def test_zero_events_after_a_closing_update_open_the_next_bucket_at_that_update():
    stream = [ev(0, 1, +10, 20_000, 20_000), ev(1, 2, 0, 20_000, 20_000),
              ev(2, 3, 0, 20_000, 20_000), ev(3, 4, -10, 20_000, 19_900)]
    buckets = aggregate_events(stream, 1)
    assert [sums(b) for b in buckets] == [(1, 1, +10, 10, 20_000, 20_000),
                                          (3, 1, -10, 10, 20_000, 19_900)]
    assert buckets[1].start_ns == buckets[0].end_ns == OPEN_NS + 1 * NS
    assert buckets[1].duration_ns == 3 * NS


def test_events_out_of_order_are_refused_on_both_clocks():
    bad = [EIGHT[1], EIGHT[0]]
    with pytest.raises(ValueError):
        aggregate_calendar(bad, 10 * NS, OPEN_NS, CLOSE)
    with pytest.raises(ValueError):
        aggregate_events(bad, 1)


# -------------------------------------------------------- the loop-closer
def _event_streams(tmp_path, n=3_000, seed=7):
    _, truth = build_fake_day(n, seed)
    out = {}
    for k in (1, 10):
        view = filter_day(truth, k)
        mpath, bpath = write_files(view, tmp_path / f"k{k}")
        msgs, ref = read_messages(mpath), read_orderbook(bpath, k)
        out[k] = list(events(touches(msgs, ref)))
    return out


def test_level_1_and_level_10_views_give_the_same_bucket_series_on_both_clocks(tmp_path):
    streams = _event_streams(tmp_path)
    e1, e10 = streams[1], streams[10]
    assert len(e10) > len(e1)
    close = max(e10[-1].time_ns, e1[-1].time_ns) + NS
    c1 = aggregate_calendar(e1, NS, OPEN_NS, close)
    c10 = aggregate_calendar(e10, NS, OPEN_NS, close)
    assert len(c1) == len(c10) > 3
    flow = lambda bs: [(b.n_updates, b.ofi, b.abs_flow, b.mid2_open, b.mid2_close) for b in bs]
    assert flow(c1) == flow(c10)
    assert sum(b.n_events for b in c10) > sum(b.n_events for b in c1)   # only the zero rows differ
    v1 = aggregate_events(e1, 20)
    v10 = aggregate_events(e10, 20)
    assert len(v1) == len(v10) >= 20
    assert flow(v1) == flow(v10)
    assert [(b.start_ns, b.end_ns) for b in v1] == [(b.start_ns, b.end_ns) for b in v10]
    assert all(b.n_updates == 20 for b in v10)
    assert all(v10[i + 1].start_ns == v10[i].end_ns for i in range(len(v10) - 1))
