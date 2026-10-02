"""Day 4, Step 4: the known-answer checks, on fiction and on paper.

The level-1 versus level-10 identity is tested the way the Week 3
self-filter experiment made possible: the same synthetic day is
filtered to K = 1 and K = 10 by our own emission rule, both views are
written through the real CSV writers and read back through the real
loaders, and the touch sums must be the same integers. The error
budget's arithmetic and the MIDAS reader are pinned on hand-built
inputs.
"""
import pytest

from filter_experiment import filter_day, write_files
from lob.checks import (TOUCH_SUMS, Budget, Comparison, depth_decomposition,
                        error_budget, levels_agree, midas_comparison,
                        norm_date, ours_on_midas_window, read_midas,
                        replayed_rows, touch_sums)
from lob.lobster import EventType, LobsterMessage, ReferenceRow, read_messages, read_orderbook
from lob.micro import NS, OPEN_NS, BinStats, time_weighted
from make_fake_itch import build_fake_day


def _views(tmp_path, n=3_000, seed=7):
    _, truth = build_fake_day(n, seed)
    out = {}
    for k in (1, 10):
        view = filter_day(truth, k)
        mpath, bpath = write_files(view, tmp_path / f"k{k}")
        out[k] = (read_messages(mpath), read_orderbook(bpath, k))
    close = truth[-1].time_ns + NS
    return out, close


def test_level_1_and_level_10_views_of_one_day_agree_to_the_integer(tmp_path):
    views, close = _views(tmp_path)
    (m1, r1), (m10, r10) = views[1], views[10]
    assert len(m1) < len(m10)                       # the filter bit
    a, b, same = levels_agree(m1, r1, m10, r10, close_ns=close)
    assert same and a == b
    assert a["two_sided_ns"] > 0 and a["trades"] > 0
    assert set(a) == set(TOUCH_SUMS)
    # and the identity is not vacuous: a wrong file breaks it
    bad = r10[:1] + [ReferenceRow(asks=r.asks, bids=r.bids[1:] or r.bids)
                     for r in r10[1:]]
    _, _, same_bad = levels_agree(m1, r1, m10, bad, close_ns=close)
    assert not same_bad


def test_touch_sums_read_the_day_off_the_bins():
    b = BinStats(0, OPEN_NS, 300 * NS, spread_x_ns=7, two_sided_ns=3, trades=2,
                 trade_shares=9, mid2_x_ns=11, one_cent_ns=1, touch_x_ns=5)
    assert touch_sums([b]) == {"two_sided_ns": 3, "spread_x_ns": 7,
                               "mid2_x_ns": 11, "one_cent_ns": 1,
                               "touch_x_ns": 5, "trades": 2, "trade_shares": 9}


def msg(t_s, kind, oid, size, price, direction):
    return LobsterMessage(time_ns=OPEN_NS + int(t_s * NS), kind=kind,
                          order_id=oid, size=size, price=price,
                          direction=direction)


def test_replayed_rows_start_from_the_seed_and_track_the_book():
    msgs = [msg(0, EventType.ADD, 1, 100, 10_000, 1),
            msg(1, EventType.ADD, 2, 50, 10_100, -1),
            msg(2, EventType.DELETE, 1, 100, 10_000, 1)]
    ref = [ReferenceRow(asks=(), bids=((10_000, 100),)),
           ReferenceRow(asks=((10_100, 50),), bids=((10_000, 100),)),
           ReferenceRow(asks=((10_100, 50),), bids=())]
    rows, stats = replayed_rows(msgs, ref, levels=10)
    assert rows[0] == ref[0]                         # the seed IS row 0
    assert rows[1] == ref[1]
    # the delete names id 1, which the seed holds as a synthetic order:
    # the dark rule removes it at the message's price, so the bid side
    # is empty again, exactly like the reference
    assert rows[2] == ref[2]
    assert stats.dark_ops == 1 and not stats.anomalies


def test_error_budget_signs_and_the_decomposition_by_hand():
    """Reference holds 100 at the touch for 4 s; our replay holds 150
    for the first 1 s (surplus 50) and 80 for the last 3 s (deficit
    20): net depth = (50*1 - 20*3)/4 = -2.5 shares, i.e. our touch depth
    is 97.5 against 100, so 'higher' is FLIPPED, while surplus and
    deficit come out 12.5 and 15 time-weighted."""
    msgs = [msg(0, EventType.ADD, 1, 100, 10_000, 1),
            msg(1, EventType.ADD, 2, 100, 10_000, 1)]
    close = OPEN_NS + 4 * NS
    ref = [ReferenceRow(asks=((10_100, 50),), bids=((10_000, 50),))] * 2
    ours = [ReferenceRow(asks=((10_100, 75),), bids=((10_000, 75),)),
            ReferenceRow(asks=((10_100, 40),), bids=((10_000, 40),))]
    budget = {b.name: b for b in error_budget(msgs, ref, ours, close_ns=close)}
    assert budget["touch depth, shares"].reference == 100.0
    assert budget["touch depth, shares"].ours == 97.5
    assert budget["touch depth, shares"].relative == pytest.approx(-0.025)
    assert not budget["touch depth, shares"].sign_ok
    assert budget["spread, cents"].sign_ok                  # equal spreads
    d = {x.name: x for x in depth_decomposition(msgs, ref, ours, close_ns=close)}
    assert d["touch depth"].surplus == 12.5 and d["touch depth"].deficit == 15.0
    assert d["touch depth"].net == -2.5
    assert d["depth-10"].reference == 100.0
    b = Budget("x", 10.0, 12.0, "higher")
    assert b.sign_ok and b.relative == pytest.approx(0.2)
    assert Budget("x", 0.0, 1.0, "none").relative is None


def test_midas_reader_matches_ticker_and_any_date_format(tmp_path):
    assert norm_date("20120621") == "2012-06-21"
    assert norm_date("2012-06-21") == "2012-06-21"
    assert norm_date("6/21/2012") == "2012-06-21"
    assert norm_date("21 June 2012") is None
    header = ("Date,Security,Ticker,McapRank,TurnRank,VolatilityRank,PriceRank,"
              "LitVol('000),OrderVol('000),Hidden,TradesForHidden,HiddenVol('000),"
              "TradeVolForHidden('000),Cancels,LitTrades,OddLots,TradesForOddLots,"
              "OddLotVol('000),TradeVolForOddLots('000)")
    (tmp_path / "q2_2012_all.csv").write_text(
        header + "\n"
        "20120620,Stock,AAPL,10,9,1,10,1,1,1,1,1,1,1,1,1,1,1,1\n"
        "20120621,Stock,AAPL,10,9,1,10,6539.038,158826.338,24849,93449.0,"
        "2224.872,8529.976,933448,69970,42856,94819,1555.391,8763.91\n"
        "6/21/2012,Stock,MSFT,10,6,2,8,28640.606,609751.373,8716,112285.0,"
        "2204.88,30528.639,1128656,104275,9096,112991,413.398,30757.356\n"
        "2012-06-21,Stock,GOOG,10,8,1,10,1,1,1,1,1,1,1,1,1,1,1,1\n")
    recs = read_midas(tmp_path, {"AAPL", "MSFT"})
    assert set(recs) == {"AAPL", "MSFT"}
    a = recs["AAPL"]
    assert a["Cancels"] == 933_448 and a["LitTrades"] == 69_970
    assert a["LitVol('000)"] == pytest.approx(6_539_038)       # thousands to shares
    assert a["OrderVol('000)"] == pytest.approx(158_826_338)
    assert recs["MSFT"]["TradesForOddLots"] == 112_991
    assert a["_file"] == "q2_2012_all.csv" and a["Security"] == "Stock"
    ours = {"cancel_to_trade": 7.0, "hidden_rate": 0.32, "odd_lot_rate": 0.5,
            "trade_to_order_volume": 0.10, "lit_volume": 1_800_000.0,
            "lit_trades": 23_000.0, "cancels": 170_000.0}
    comps = {c.statistic.split(" (")[0]: c for c in midas_comparison(ours, a)}
    c2t = comps["cancel-to-trade"]
    assert c2t.midas == pytest.approx(933_448 / 69_970)        # 13.34
    assert c2t.as_expected is True                              # ours lower
    assert comps["hidden rate"].midas == pytest.approx(24_849 / 93_449)
    assert comps["hidden rate"].as_expected is True
    assert comps["odd-lot rate"].midas == pytest.approx(42_856 / 94_819)
    t2o = comps["trade-to-order volume"]
    assert t2o.midas == pytest.approx(6_539_038 / 158_826_338)
    assert t2o.as_expected is True                              # ours higher
    vol = comps["lit volume, shares"]
    assert vol.ratio == pytest.approx(1_800_000 / 6_539_038) and vol.as_expected is True
    assert Comparison("x", None, 1.0, "close", "").as_expected is None


def test_ours_on_the_midas_window_drops_the_first_five_minutes():
    early = BinStats(0, OPEN_NS, 300 * NS, cancels=100, trades=10, hidden=0,
                     odd_lot_trades=5, trade_shares=1_000, add_shares=10_000)
    late = BinStats(1, OPEN_NS + 300 * NS, 300 * NS, cancels=40, trades=8,
                    hidden=2, odd_lot_trades=5, trade_shares=800,
                    hidden_shares=200, add_shares=5_000)
    o = ours_on_midas_window([early, late])
    assert o["cancels"] == 40 and o["lit_trades"] == 8
    assert o["cancel_to_trade"] == 5.0                       # cancels / lit trades
    assert o["hidden_rate"] == 0.2 and o["odd_lot_rate"] == 0.5   # over all trades
    assert o["lit_volume"] == 800
    assert o["trade_to_order_volume"] == pytest.approx(800 / 5_000)


def test_time_weighted_sums_are_what_check_1_compares():
    msgs = [msg(0, EventType.ADD, 1, 100, 10_000, 1)]
    ref = [ReferenceRow(asks=((10_100, 5),), bids=((10_000, 7),))]
    s = touch_sums(time_weighted(msgs, ref))
    assert s["touch_x_ns"] == 12 * s["two_sided_ns"]
    assert s["spread_x_ns"] == 100 * s["two_sided_ns"]
