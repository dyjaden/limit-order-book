"""Day 5, Step 4: the first look's arithmetic by hand, and its discipline.

``ols`` on three-point cases where both slopes, both R-squareds and the
hit rate are known; ``depth_scaling`` on windows built so that beta is
exactly one over depth, giving a log-log slope of exactly minus one with
the undefined windows counted out. Then the rule of the week, pinned on
the script itself: every regression is in the registry before the
write-up is written, so no unlogged number ever exists on a page.
"""
import pytest

from filter_experiment import filter_day, write_files
from lob.lobster import read_messages, read_orderbook
from lob.micro import NS, OPEN_NS, time_weighted
from lob.ofi import Bucket, Fit, depth_scaling, events, ols, touches
from lob.registry import count, read
from make_fake_itch import build_fake_day
import ofi_first_look as fl


def test_ols_through_the_origin_and_with_an_intercept_by_hand():
    f = ols([0, 1, 2], [1, 3, 5])
    assert (f.beta, f.alpha, f.r2) == (2.0, 1.0, 1.0)
    assert f.beta0 == pytest.approx(13 / 5)                   # sum xy / sum xx
    assert f.r2_0 == pytest.approx(1 - 1.2 / 8)               # centered total sum
    assert f.r2_0 < f.r2
    assert (f.hits, f.both_nonzero, f.excluded) == (2, 2, 1)  # x = 0 is excluded
    assert f.hit_rate == 1.0 and f.n == 3
    g = ols([1, 2, 3], [2, 4, 6])
    assert g == Fit(3, 2.0, 1.0, 2.0, 0.0, 1.0, 3, 3, 0)


def test_ols_hit_rate_counts_only_where_both_are_nonzero():
    f = ols([1, -1, 2, -2, 0], [1, 1, -2, -1, 3])
    assert (f.hits, f.both_nonzero, f.excluded) == (2, 4, 1)
    assert f.hit_rate == 0.5


def test_ols_says_none_where_the_answer_is_undefined():
    flat = ols([1, 2, 3], [4, 4, 4])
    assert flat.r2 is None and flat.r2_0 is None and flat.beta == 0.0
    assert flat.beta0 == pytest.approx(24 / 14)
    const = ols([5, 5, 5], [1, 2, 3])
    assert const.beta is None and const.alpha is None and const.r2 is None
    assert const.beta0 == pytest.approx(30 / 75)
    empty = ols([], [])
    assert empty.n == 0 and empty.beta0 is None and empty.hit_rate is None
    with pytest.raises(ValueError):
        ols([1, 2], [1])


def B(i, start_s, ofi, dmid_cents, width_s=1) -> Bucket:
    """A calendar bucket whose price change is given in cents: mid2 moves
    by 200 units per cent."""
    start = OPEN_NS + int(start_s * NS)
    return Bucket(i, start, start + width_s * NS, n_events=1, n_updates=1,
                  ofi=ofi, abs_flow=abs(ofi), mid2_open=1_000_000,
                  mid2_close=1_000_000 + int(round(200 * dmid_cents)))


def test_depth_scaling_recovers_a_slope_of_minus_one_and_counts_the_undefined():
    buckets = [B(0, 0, 100, 1.0), B(1, 1, 200, 2.0),          # window 0: beta 1/100
               B(2, 10, 100, 0.5), B(3, 11, 200, 1.0),        # window 1: beta 1/200
               B(4, 20, 400, 1.0),                            # window 2: beta 1/400
               B(5, 30, 100, 1.0)]                            # window 3: no depth
    depth = [100.0, 200.0, 400.0, None, 50.0]                 # window 4: no buckets
    s = depth_scaling(buckets, depth, window_ns=10 * NS)
    assert len(s.rows) == 5
    assert [r.n for r in s.rows] == [2, 2, 1, 1, 0]
    assert s.rows[0].beta0 == pytest.approx(0.01)
    assert s.rows[1].beta0 == pytest.approx(0.005)
    assert s.rows[2].beta0 == pytest.approx(0.0025) and s.rows[2].r2_0 is None
    assert s.rows[4].beta0 is None and s.rows[4].depth == 50.0
    assert (s.n_used, s.n_skipped) == (3, 2)
    assert s.slope == pytest.approx(-1.0) and s.intercept == pytest.approx(0.0)
    assert s.r2 == pytest.approx(1.0)
    assert s.rows[1].start_ns == OPEN_NS + 10 * NS and s.rows[1].end_ns == OPEN_NS + 20 * NS
    # fewer than three usable windows: no slope, and it says so
    thin = depth_scaling(buckets[:4], depth[:2], window_ns=10 * NS)
    assert thin.slope is None and (thin.n_used, thin.n_skipped) == (2, 0)


# ------------------------------------------------------- the discipline
def _fake_day(tmp_path, n=3_000, seed=7, k=10):
    _, truth = build_fake_day(n, seed)
    view = filter_day(truth, k)
    mpath, bpath = write_files(view, tmp_path / f"k{k}")
    return read_messages(mpath), read_orderbook(bpath, k)


def test_every_regression_is_in_the_registry_before_the_write_up_is_written(tmp_path, monkeypatch):
    msgs, ref = _fake_day(tmp_path)
    evs = list(events(touches(msgs, ref)))
    close = msgs[-1].time_ns + NS
    # the synthetic day is a few seconds long, so the clocks are scaled
    # to it: sub-second bins, small event buckets, one-second windows
    sizes = fl.Sizes(calendar_s=(0.1, 0.5, 1.0), event_n=(5, 20), key_s=0.5,
                     key_n=5, window_s=1.0)
    depth_bins = time_weighted(msgs, ref, int(sizes.window_s * NS), OPEN_NS, close)
    registry = tmp_path / "trials.csv"
    report = tmp_path / "ofi_first_look.md"
    seen = {}

    real_write, real_print = fl.write_report, fl.print_look

    def spy_write(path, *a, **kw):
        seen["count_at_write"] = count(registry)
        return real_write(path, *a, **kw)

    def spy_print(look):
        seen["count_at_print"] = count(registry)
        return real_print(look)
    monkeypatch.setattr(fl, "write_report", spy_write)
    monkeypatch.setattr(fl, "print_look", spy_print)

    look = fl.run(evs, depth_bins, "FAKE", 10, sizes, registry, report,
                  level1=lambda key_buckets: True, script="test",
                  open_ns=OPEN_NS, close_ns=close)
    expected = len(sizes.calendar_s) + len(sizes.event_n) + len(depth_bins) + 1
    assert seen["count_at_print"] == expected          # logged before printed
    assert seen["count_at_write"] == expected          # and before written
    rows = read(registry)
    assert len(rows) == expected
    assert {r["status"] for r in rows} == {"descriptive"}
    assert {r["horizon"] for r in rows} == {"0"}
    assert all(r["question"] and r["metric"] for r in rows)
    # every number on the page carries its trial id
    text = report.read_text()
    for r in rows:
        assert f"#{r['trial_id']}" in text
    assert "contemporaneous" in text.lower() and "not evidence" in text.lower()
    # the main fits are real numbers on this day, and the key fit is
    # the one the figure draws
    assert look.key.fit.n > 0 and look.key.fit.beta0 is not None
    assert len(look.fits) == len(sizes.calendar_s) + len(sizes.event_n)
    assert look.scaling.n_used + look.scaling.n_skipped == len(depth_bins)
