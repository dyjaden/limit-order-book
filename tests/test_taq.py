"""Day 7, Step 2: the spread arithmetic by hand, no network anywhere.

Lee-Ready on a hand-built sequence (above, below, at the mid with an
uptick, a downtick and a zero tick, an undecidable first trade, a trade
before any quote); effective, realized and impact spreads on three
trades and two quotes; the time-weighted quoted spread on quotes that
straddle the open, with a crossed quote excluded; the per-name summary
with its unsigned and no-realized shares.
"""
import pytest

from lob.taq import (BPS, NS, Quote, QuoteBook, Trade, quoted_spread_bps,
                     sign_trades, summarize_name, trade_spreads)

T0 = 34_200 * NS                     # 09:30:00


def Q(t_s, bid, ask) -> Quote:
    return Quote(T0 + int(t_s * NS), bid, ask)


def Tr(t_s, price, size=100) -> Trade:
    return Trade(T0 + int(t_s * NS), price, size)


def test_prevailing_quote_is_strictly_before_the_trade():
    book = QuoteBook([Q(10, 100.00, 100.10), Q(20, 100.02, 100.12), Q(5, 99.9, 100.0)])
    assert [q.time_ns for q in book.quotes] == [T0 + 5 * NS, T0 + 10 * NS, T0 + 20 * NS]
    assert book.at(T0 + 4 * NS) is None                   # before the first quote
    assert book.at(T0 + 10 * NS).bid == 99.9              # same timestamp: the earlier one
    assert book.at(T0 + 10 * NS + 1).bid == 100.00
    assert book.mid_at(T0 + 25 * NS) == pytest.approx(100.07)
    crossed = QuoteBook([Q(0, 100.10, 100.00)])
    assert crossed.mid_at(T0 + NS) is None                # not two-sided


def test_lee_ready_by_hand():
    book = QuoteBook([Q(0, 100.00, 100.10)])              # mid 100.05 throughout
    trades = [Tr(-1, 100.05),      # before any quote: unsigned
              Tr(1, 100.05),       # at the mid, first trade with a quote, no earlier price change: unsigned
              Tr(2, 100.10),       # above the mid: buy
              Tr(3, 100.00),       # below the mid: sell
              Tr(4, 100.05),       # at the mid, up from 100.00: buy (tick test)
              Tr(5, 100.05),       # at the mid, unchanged: the last change was up: buy
              Tr(6, 100.09),       # above: buy
              Tr(7, 100.05),       # at the mid, down from 100.09: sell
              Tr(8, 100.05)]       # zero tick after a downtick: sell
    assert sign_trades(trades, book) == [0, 0, 1, -1, 1, 1, 1, -1, -1]


def test_effective_realized_and_impact_by_hand():
    quotes = [Q(0, 100.00, 100.10),                      # mid 100.05
              Q(200, 100.20, 100.30)]                    # mid 100.25 from 09:33:20
    book = QuoteBook(quotes)
    trades = [Tr(10, 100.10, 200),    # buy at the ask: effective 2*(0.05)/100.05
              Tr(20, 100.00, 100),    # sell at the bid
              Tr(400, 100.25, 50)]    # at the new mid, up from 100.00: a buy by the tick test; no mid 5 min later inside the session
    signs = sign_trades(trades, book)
    assert signs == [1, -1, 1]
    close = T0 + 600 * NS
    out = trade_spreads(trades, book, signs, horizon_ns=300 * NS, close_ns=close)
    assert len(out) == 3
    buy, sell, late = out
    assert buy.effective_bps == pytest.approx(2 * 0.05 / 100.05 * BPS)
    # five minutes after the buy the mid is 100.25: the buyer's counterparty lost
    assert buy.realized_bps == pytest.approx(2 * (100.10 - 100.25) / 100.05 * BPS)
    assert buy.impact_bps == pytest.approx(2 * (100.25 - 100.05) / 100.05 * BPS)
    assert buy.effective_bps == pytest.approx(buy.realized_bps + buy.impact_bps)
    assert sell.effective_bps == pytest.approx(2 * 0.05 / 100.05 * BPS)
    assert sell.realized_bps == pytest.approx(2 * -1 * (100.00 - 100.25) / 100.05 * BPS)
    assert late.realized_bps is None and late.impact_bps is None    # 09:36:40 + 5 min is past the close
    assert late.effective_bps == pytest.approx(0.0)


def test_quoted_spread_is_time_weighted_and_straddles_the_open():
    quotes = [Q(-100, 99.00, 101.00),      # 2.00 wide, holds from before the open to 09:30:10
              Q(10, 100.00, 100.10),       # 0.10 wide for 20 s
              Q(30, 100.20, 100.10),       # crossed: excluded for 10 s
              Q(40, 100.00, 100.20)]       # 0.20 wide until the close
    close = T0 + 100 * NS
    q = quoted_spread_bps(quotes, T0, close)
    assert q["two_sided_ns"] == 90 * NS and q["excluded_ns"] == 10 * NS
    spread_x = 2.00 * 10 + 0.10 * 20 + 0.20 * 60
    mid_x = 100.0 * 10 + 100.05 * 20 + 100.10 * 60
    assert q["quoted_cents"] == pytest.approx(100 * spread_x / 90)
    assert q["quoted_bps"] == pytest.approx(BPS * spread_x / mid_x)
    assert q["mid"] == pytest.approx(mid_x / 90)
    empty = quoted_spread_bps([], T0, close)
    assert empty["quoted_bps"] is None and empty["two_sided_ns"] == 0


def test_summarize_name_counts_what_it_could_not_sign_or_realize():
    quotes = [Q(0, 100.00, 100.10), Q(200, 100.20, 100.30)]
    trades = [Tr(-5, 100.05, 100),        # before the open: dropped
              Tr(1, 100.05, 100),         # unsigned (no earlier price change)
              Tr(10, 100.10, 200),        # buy
              Tr(20, 100.00, 100),        # sell
              Tr(400, 100.25, 50)]        # buy by the tick test, no realized
    s = summarize_name(trades, quotes, T0, T0 + 600 * NS)
    assert s["trades"] == 4 and s["signed"] == 3
    assert s["unsigned_share"] == pytest.approx(1 / 4)
    assert s["no_realized_share"] == pytest.approx(1 / 3)
    assert s["shares"] == 450 and s["dollar_volume"] == pytest.approx(100.05 * 100 + 100.10 * 200 + 100.00 * 100 + 100.25 * 50)
    eff = 2 * 0.05 / 100.05 * BPS
    assert s["effective_bps"] == pytest.approx((eff + eff + 0.0) / 3)
    assert s["effective_bps_dw"] == pytest.approx((eff * 100.10 * 200 + eff * 100.00 * 100 + 0.0 * 100.25 * 50)
                                                  / (100.10 * 200 + 100.00 * 100 + 100.25 * 50))
    assert s["buy_share"] == pytest.approx(2 / 3)
    assert s["quoted_bps"] is not None and s["two_sided_ns"] == 600 * NS
    none = summarize_name([], quotes, T0, T0 + 600 * NS)
    assert none["trades"] == 0 and none["effective_bps"] is None and none["unsigned_share"] is None


# ------------------------------------------------------------ the script
def test_script_helpers_choose_time_and_measure_from_cache(tmp_path):
    import gzip
    import types
    import taq_spreads as ts
    import datetime as dt
    assert ts.time_to_ns("09:30:00.004241") == 34_200 * NS + 4_241_000
    assert ts.time_to_ns(dt.time(16, 0, 0, 500)) == 57_600 * NS + 500_000
    # deciles over 23 names with volume, five per decile where the decile has five
    universe = [{"permno": i, "ticker": f"T{i:02d}", "close": 10.0, "volume": float(i),
                 "dollar_volume": 10.0 * i} for i in range(1, 24)]
    universe.append({"permno": 99, "ticker": "NOVOL", "close": None, "volume": None, "dollar_volume": None})
    chosen = ts.choose(universe, per_decile=2)
    assert {u["decile"] for u in universe if u["dollar_volume"]} == set(range(1, 11))
    assert universe[-1]["decile"] is None and not universe[-1]["chosen"]
    assert all(u["chosen"] for u in chosen) and 10 <= len(chosen) <= 20
    assert max(u["decile"] for u in chosen) == 10 and min(u["decile"] for u in chosen) == 1
    upath = tmp_path / "universe.csv"
    ts.write_universe(universe, upath)
    back = ts.read_universe(upath)
    assert back[0]["ticker"] == "T01" and back[0]["dollar_volume"] == 10.0 and back[-1]["decile"] is None
    # a cached name, measured through the module
    cache = tmp_path / "taq"
    date = "2012-06-21"
    with gzip.open(ts.nbbo_path(cache, date, "XYZ"), "wt", newline="") as f:
        f.write("time_ns,bid,ask\n")
        f.write(f"{T0},100.00,100.10\n{T0 + 200 * NS},100.20,100.30\n")
    with gzip.open(ts.trades_path(cache, date, "XYZ"), "wt", newline="") as f:
        f.write("time_ns,price,size\n")
        f.write(f"{T0 + 10 * NS},100.10,200\n{T0 + 20 * NS},100.00,100\n{T0 + 400 * NS},100.25,50\n")
    assert not ts.cached(cache, date, "XYZ")                       # no marker: an interrupted pull
    ts.done_path(cache, date, "XYZ").write_text("2 nbbo rows, 3 trades\n")
    assert ts.cached(cache, date, "XYZ")
    assert ts.missing(None) and ts.missing(float("nan")) and not ts.missing("A") and not ts.missing(0)
    row = ts.measure(cache, date, "XYZ", {"permno": 1, "decile": 7}, 300 * NS)
    assert row["ticker"] == "XYZ" and row["decile"] == 7 and row["trades"] == 3 and row["signed"] == 3
    assert row["effective_bps"] == pytest.approx((2 * 0.05 / 100.05 * BPS * 2 + 0.0) / 3)
    assert row["quoted_cents"] == pytest.approx(100 * (0.10 * 200 + 0.10 * 23_200) / 23_400)
    assert row["two_sided_share"] == pytest.approx(1.0)
    # the cache-only path: universe on disk, one name cached, outputs written
    args = types.SimpleNamespace(date=date, horizon=300.0)
    rows = ts.compute(args, cache, tmp_path / "out", {"XYZ": {"permno": 1, "decile": 7}, "ABC": {}})
    assert [r["ticker"] for r in rows] == ["XYZ"]                 # ABC is not cached, skipped
    assert (tmp_path / "out" / f"taq_spreads_{date}.csv").exists()
    page = (tmp_path / "out" / "taq_spreads.md").read_text()
    assert "<!-- taq:begin -->" in page and "| XYZ | 7 |" in page
    dec = ts.by_decile(rows)
    assert dec == [{"decile": 7, "names": 1, "median_dollar_volume": row["dollar_volume"],
                    "median_quoted_bps": row["quoted_bps"],
                    "median_half_effective_bps": row["effective_bps"] / 2,
                    "median_realized_bps": row["realized_bps"], "median_impact_bps": row["impact_bps"]}]
