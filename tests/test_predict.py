"""Day 6, Step 1: the predictive question's arithmetic, by hand.

A six-bucket series pairs into five forecast opportunities; the split
puts the boundary pairs where the guide says; ``evaluate`` recovers a
known slope, a known out-of-sample R-squared against zero (including a
negative one), a hit rate with the right exclusions, the binomial z and
band, and the gross edge; and the script's decay part logs every fit
before it prints one.
"""
import math

import pytest

from filter_experiment import filter_day, write_files
from lob.lobster import read_messages, read_orderbook
from lob.micro import NS, OPEN_NS
from lob.ofi import Bucket, events, touches
from lob.predict import (SPLIT_NS, Forecast, Pair, evaluate, fit_origin, pairs,
                         split)
from lob.registry import count, read
from make_fake_itch import build_fake_day
import ofi_prediction as op


def B(i, start_s, width_s, ofi, dmid_cents, mid2_open=1_000_000) -> Bucket:
    start = OPEN_NS + int(start_s * NS)
    return Bucket(i, start, start + width_s * NS, n_events=1, n_updates=1 if ofi else 0,
                  ofi=ofi, abs_flow=abs(ofi), mid2_open=mid2_open,
                  mid2_close=mid2_open + int(round(200 * dmid_cents)))


SIX = [B(0, 0, 10, +100, 1.0), B(1, 10, 10, -200, -0.5), B(2, 20, 10, 0, 0.0),
       B(3, 30, 10, +300, 2.0), B(4, 40, 10, -50, -1.0), B(5, 50, 10, +80, 0.5)]
SPREADS = [10.0, 12.0, None, 14.0, 16.0, 18.0]


def test_pairs_pair_each_bucket_with_the_next_and_carry_the_spread():
    ps = pairs(SIX, SPREADS, var_window=2)
    assert len(ps) == 5
    p0 = ps[0]
    assert p0.time_ns == SIX[0].end_ns and p0.target_end_ns == SIX[1].end_ns
    assert (p0.x, p0.last, p0.y, p0.half_spread) == (100, 1.0, -0.5, 5.0)
    assert p0.var is None                                 # needs two buckets
    assert ps[1].var == 1.0 ** 2 + 0.5 ** 2                # buckets 0 and 1
    assert ps[2].half_spread is None                      # never two-sided
    assert ps[4] == Pair(SIX[4].end_ns, SIX[5].end_ns, -50, -1.0, 0.5, 8.0,
                         2.0 ** 2 + 1.0 ** 2)
    assert all(p.half_spread is None for p in pairs(SIX))      # no spreads given
    with pytest.raises(ValueError):
        pairs(SIX, SPREADS[:3])
    gap = SIX[:2] + [B(2, 25, 10, 0, 0.0)]
    with pytest.raises(ValueError):
        pairs(gap)
    # a bucket before the day's first observation has no mid and makes no pair
    blind = [Bucket(0, OPEN_NS, OPEN_NS + 10 * NS)] + SIX[1:]
    assert len(pairs(blind)) == 4


def test_split_places_the_boundary_pairs_as_the_guide_says():
    ps = pairs(SIX)
    at = SIX[2].end_ns                                    # 09:30:30
    fit, test = split(ps, at)
    # a pair whose target ends exactly at the split is in the fit set
    assert ps[1] in fit and ps[1].target_end_ns == at
    # a pair forecast exactly at the split time is in the test set
    assert ps[2] in test and ps[2].time_ns == at
    assert [p.time_ns for p in fit] == [ps[0].time_ns, ps[1].time_ns]
    assert [p.time_ns for p in test] == [p.time_ns for p in ps[2:]]
    rfit, rtest = split(ps, at, reverse=True)
    assert (rfit, rtest) == (test, fit)
    # a pair straddling the split belongs to neither
    mid = SIX[2].start_ns + 5 * NS
    fit2, test2 = split(ps, mid)
    assert ps[1] not in fit2 and ps[1] not in test2
    assert SPLIT_NS == OPEN_NS + 11_700 * NS               # 12:45:00


def P(x, y, last=0.0, hs=10.0, t=0):
    return Pair(OPEN_NS + t * NS, OPEN_NS + (t + 10) * NS, x, last, y, hs, None)


def test_fit_origin_and_evaluate_by_hand():
    beta, t = fit_origin([1, 2, 3], [2, 4, 6])
    assert beta == 2.0 and t is None                      # a perfect fit has no error
    beta, t = fit_origin([1, 2, 3], [1, 3, 4])            # sum xy 19, sum xx 14
    assert beta == pytest.approx(19 / 14)
    assert t == pytest.approx(beta / math.sqrt((sum((y - beta * x) ** 2 for x, y in
                                                     zip([1, 2, 3], [1, 3, 4])) / 2) / 14))
    assert fit_origin([0, 0], [1, 2]) == (None, None)
    assert fit_origin([1], [1]) == (None, None)
    # fit: y = 0.01 x exactly, so beta = 0.01 cents per share
    fit = [P(100, 1.0), P(-200, -2.0), P(50, 0.5)]
    # test: four pairs with a signal, one without; y pulled around
    test = [P(100, 2.0), P(-100, -0.5), P(200, -1.0), P(0, 3.0), P(50, 0.0)]
    f = evaluate(fit, test, "ofi")
    assert f.beta == pytest.approx(0.01) and f.n_fit == 3 and f.n_test == 5
    # forecasts 1.0, -1.0, 2.0, 0.0, 0.5 against 2.0, -0.5, -1.0, 3.0, 0.0
    sse = 1.0 + 0.25 + 9.0 + 9.0 + 0.25
    sst0 = 4.0 + 0.25 + 1.0 + 9.0 + 0.0
    assert f.r2_oos == pytest.approx(1 - sse / sst0)       # negative: worse than zero
    assert f.r2_oos < 0
    assert (f.hits, f.both_nonzero, f.excluded) == (2, 3, 2)   # x = 0 or y = 0 excluded
    assert f.hit_rate == pytest.approx(2 / 3)
    assert f.z == pytest.approx((2 - 1.5) / math.sqrt(0.75))
    assert f.band == pytest.approx(1.96 * math.sqrt(0.25 / 3))
    assert f.clears_band is False
    # edge over the four signals: +2.0, +0.5 (short, y -0.5), -1.0, 0.0
    assert f.n_signals == 4 and f.edge_cents == pytest.approx((2.0 + 0.5 - 1.0 + 0.0) / 4)
    # the last-change baseline uses the previous mid change as its regressor
    fit_l = [P(0, -1.0, last=1.0), P(0, 2.0, last=-2.0)]  # reversal: gamma = -1
    test_l = [P(0, -0.5, last=1.0), P(0, 1.0, last=-1.0), P(0, 1.0, last=0.0)]
    g = evaluate(fit_l, test_l, "last")
    assert g.beta == pytest.approx(-1.0)
    assert (g.hits, g.both_nonzero, g.excluded) == (2, 2, 1) and g.hit_rate == 1.0
    assert g.edge_cents == pytest.approx((0.5 + 1.0) / 2)
    with pytest.raises(ValueError):
        evaluate(fit, test, "moon")


def test_evaluate_says_none_without_a_signal_or_a_fit():
    flat = [P(0, 1.0), P(0, -1.0)]
    f = evaluate(flat, flat, "ofi")
    assert f.beta is None and f.hit_rate is None and f.edge_cents is None
    assert f.excluded == 2 and f.clears_band is None
    f2 = evaluate([P(100, 1.0), P(200, 2.0)], [P(0, 0.0)], "ofi")
    assert f2.r2_oos is None and f2.n_signals == 0 and f2.edge_cents is None
    assert Forecast("ofi", 0, 0, None, None, None, 0, 0, 0, None, 0).z is None


# ------------------------------------------------------- the discipline
def _fake_day(tmp_path, n=3_000, seed=7, k=10):
    _, truth = build_fake_day(n, seed)
    view = filter_day(truth, k)
    mpath, bpath = write_files(view, tmp_path / f"k{k}")
    return read_messages(mpath), read_orderbook(bpath, k)


def test_every_decay_fit_is_logged_before_anything_prints(tmp_path, monkeypatch, capsys):
    msgs, ref = _fake_day(tmp_path)
    evs = list(events(touches(msgs, ref)))
    day = op.Day("FAKE", 10, msgs, ref, evs)
    registry = tmp_path / "trials.csv"
    seen = {}
    real_print, real_write = op.print_decay, op.write_csv

    def spy_print(rows, ticker):
        seen["at_print"] = count(registry)
        return real_print(rows, ticker)

    def spy_write(rows, columns, path):
        seen["at_write"] = count(registry)
        return real_write(rows, columns, path)
    monkeypatch.setattr(op, "print_decay", spy_print)
    monkeypatch.setattr(op, "write_csv", spy_write)
    # the synthetic day is about 7.5 s long: sub-second horizons, split at 4 s
    monkeypatch.setattr(op, "HORIZONS_S", (0.25, 0.5, 1.0))
    monkeypatch.setattr(op, "SPLIT_NS", OPEN_NS + 4 * NS)
    rows = op.part_decay(None, day, registry, tmp_path)
    expected = 3 * 2 * 2
    assert seen["at_print"] == expected and seen["at_write"] == expected
    assert len(rows) == expected and len(read(registry)) == expected
    assert {r["status"] for r in read(registry)} == {"predictive"}
    assert {r["split"] for r in read(registry)} == {"first-half/second-half",
                                                     "second-half/first-half"}
    assert all(r["trial_id"] for r in rows)
    out = capsys.readouterr().out
    assert "HORIZON DECAY" in out and "#1" in out
    csv_rows = op.read_csv(tmp_path / "ofi_decay_FAKE.csv")
    assert len(csv_rows) == expected and csv_rows[0]["predictor"] == "ofi"

