"""Day 7, Step 1: the spectrum's arithmetic by hand, and the readers.

``percentile`` and ``deciles`` on small universes with ties; ``place`` on
a hand-built MIDAS file with the README's ratios, an excluded date and
an absent ticker; ``summarize`` on a temporary results folder with
hand-built CSVs and a hand-built registry, returning the right numbers
and refusing when a file is missing; the splice that leaves prose
alone; the figure on two invented names.
"""
import csv

import pytest

from lob.spectrum import (METRICS, NameSummary, contemporaneous, deciles, derived,
                          missing_results, percentile, place, read_midas_day, summarize)
import liquidity_spectrum as ls

HEADER = ("Date,Security,Ticker,McapRank,TurnRank,VolatilityRank,PriceRank,"
          "LitVol('000),OrderVol('000),Hidden,TradesForHidden,HiddenVol('000),"
          "TradeVolForHidden('000),Cancels,LitTrades,OddLots,TradesForOddLots,"
          "OddLotVol('000),TradeVolForOddLots('000)")


def test_percentile_and_deciles_by_hand():
    u = [1, 2, 2, 3, 10, None]
    assert percentile(u, 2) == 3 / 5                       # ties count as at or below
    assert percentile(u, 10) == 1.0 and percentile(u, 0.5) == 0.0
    assert percentile(u, None) is None and percentile([None], 1) is None
    cuts = deciles(range(1, 101))
    assert len(cuts) == 9 and all(abs(c - 10 * k) <= 1 for k, c in enumerate(cuts, start=1))
    assert cuts == sorted(cuts) and deciles([]) == []
    assert deciles([5, None, 1, 3]) == [1, 1, 3, 3, 3, 3, 3, 5, 5]      # nearest rank, Nones out


def test_place_puts_names_on_a_hand_built_universe(tmp_path):
    rows = ["20120621,Stock,AAA,10,9,1,10,100,1000,10,100,1,10,1000,100,50,100,1,10",   # c2t 10, odd 50%
            "20120621,Stock,BBB,9,8,2,5,200,1000,5,100,1,10,500,50,10,100,1,10",       # c2t 10, odd 10%
            "20120621,Stock,CCC,8,7,3,3,50,500,20,100,1,10,2000,40,80,100,1,10",       # c2t 50, odd 80%
            "20120621,ETF,DDD,7,6,4,2,10,100,0,0,0,0,10,10,0,0,0,0",                   # no hidden or odd-lot trades
            "20120620,Stock,AAA,10,9,1,10,1,1,1,1,1,1,1,1,1,1,1,1"]                    # another day
    (tmp_path / "q2.csv").write_text(HEADER + "\n" + "\n".join(rows) + "\n")
    universe = read_midas_day(tmp_path, "2012-06-21")
    assert set(universe) == {"AAA", "BBB", "CCC", "DDD"}
    m = universe["AAA"]["_metrics"]
    assert m["LitTrades"] == 100 and m["LitVol"] == 100_000 and m["Cancels"] == 1000
    assert m["cancel_to_trade"] == 10.0 and m["odd_lot_rate"] == 0.5
    assert m["hidden_rate"] == 0.1 and m["trade_to_order_volume"] == pytest.approx(0.1)
    assert universe["DDD"]["_metrics"]["hidden_rate"] is None            # no denominator
    pos = {p.ticker: p for p in place(universe, ["aaa", "CCC", "ZZZ"])}
    assert pos["AAA"].present and pos["AAA"].security == "Stock"
    assert pos["AAA"].ranks == {"McapRank": "10", "TurnRank": "9", "VolatilityRank": "1", "PriceRank": "10"}
    assert pos["AAA"].percentiles["LitTrades"] == 1.0                   # 100 is the most
    assert pos["AAA"].percentiles["cancel_to_trade"] == 3 / 4           # 1, 10, 10 at or below 10; 50 above
    assert pos["CCC"].percentiles["odd_lot_rate"] == 1.0                # Nones left out
    assert pos["AAA"].universe == 4
    assert not pos["ZZZ"].present and pos["ZZZ"].values == {}
    assert set(pos["AAA"].values) == set(METRICS)
    assert derived({"Cancels": 5, "LitTrades": 0})["cancel_to_trade"] is None


def _write(path, header, rows):
    with open(path, "w", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(header)
        w.writerows(rows)


def _results(tmp_path, ticker="XYZ"):
    _write(tmp_path / f"intraday_{ticker}_5min.csv",
           ["bin", "two_sided_ns", "spread_x_ns", "mid2_x_ns", "one_cent_ns", "touch_x_ns"],
           [[0, 100, 100 * 1500, 100 * 1_000_000, 10, 100 * 300],       # 15 cents, 300 shares
            [1, 300, 300 * 500, 300 * 1_000_000, 300, 300 * 100]])      # 5 cents, 100 shares
    _write(tmp_path / f"ofi_{ticker}_calendar_10.csv", ["bucket", "n_updates"],
           [[0, 10], [1, 20], [2, 60]])
    _write(tmp_path / f"ofi_decay_{ticker}.csv",
           ["horizon_s", "direction", "predictor", "hit_rate", "band", "r2_oos"],
           [[1, "primary", "ofi", 0.55, 0.02, 0.006], [5, "primary", "ofi", 0.53, 0.03, 0.004],
            [5, "reversed", "ofi", 0.40, 0.03, -0.1], [5, "primary", "last", 0.5, 0.03, 0.0]])
    _write(tmp_path / f"ofi_cost_{ticker}.csv",
           ["horizon_s", "subset", "latency", "gross_cents", "half_spread_cents", "consumed"],
           [[5, "all", 0, 0.5, 6.0, 12.0], [5, "all", 1, 0.1, 6.0, 60.0], [5, "top-decile", 0, 1.0, 5.0, 5.0]])
    _write(tmp_path / "trials.csv",
           ["trial_id", "script", "ticker", "clock", "bucket", "window", "metric", "value", "note"],
           [[1, "ofi_first_look.py", ticker, "calendar", "10", "09:30-16:00", "r2", 0.30,
             "beta0=3.5 cents/kshare; r2_0=0.3"],
            [2, "ofi_first_look.py", ticker, "calendar", "10", "09:30-10:00", "beta0_cents_per_kshare", 2.0, ""],
            [3, "ofi_first_look.py", ticker, "calendar", "10", "09:30-16:00", "r2", 0.41,
             "beta0=4.09 cents/kshare; r2_0=0.41"],                      # a re-run: the last wins
            [4, "ofi_prediction.py", ticker, "calendar", "10", "09:30-16:00", "hit_rate", 0.5, ""]])


def test_summarize_reads_the_week_4_to_6_results_and_names_what_is_missing(tmp_path):
    _results(tmp_path)
    s = summarize(tmp_path, "XYZ")
    assert isinstance(s, NameSummary)
    # time-weighted over both bins: (1500*100 + 500*300) / (400 * 100 units per cent)
    assert s.spread_cents == pytest.approx(300_000 / 40_000)            # 7.5 cents
    assert s.spread_bps == pytest.approx(2e4 * 300_000 / (400 * 1_000_000))
    assert s.one_cent_share == pytest.approx(310 / 400)
    assert s.touch_depth == pytest.approx((300 * 100 + 100 * 300) / 400)  # 150
    assert s.updates_per_10s == 30.0
    assert (s.r2_contemporaneous_10s, s.beta_contemporaneous) == (0.41, 4.09)
    assert (s.hit_1s, s.band_1s, s.hit_5s, s.band_5s, s.r2_oos_5s) == (0.55, 0.02, 0.53, 0.03, 0.004)
    assert (s.gross_5s, s.half_spread_5s, s.consumed_5s) == (0.5, 6.0, 12.0)
    assert missing_results(tmp_path, "XYZ") == []
    (tmp_path / "ofi_cost_XYZ.csv").unlink()
    assert summarize(tmp_path, "XYZ") is None
    assert [p.name for p in missing_results(tmp_path, "XYZ")] == ["ofi_cost_XYZ.csv"]
    assert contemporaneous([], "XYZ") == (None, None)


def test_splice_creates_a_skeleton_and_then_leaves_the_prose_alone(tmp_path):
    page = tmp_path / "page.md"
    ls.splice(page, ls.SPECTRUM, "first", ls.SKELETON)
    text = page.read_text()
    assert text.startswith("# The liquidity spectrum") and "first" in text
    page.write_text("# mine\n\nabove\n\n<!-- spectrum:begin -->\nold\n<!-- spectrum:end -->\n\nbelow\n")
    ls.splice(page, ls.SPECTRUM, "new", ls.SKELETON)
    text = page.read_text()
    assert text.startswith("# mine") and "above" in text and "below" in text
    assert "new" in text and "old" not in text
    ls.splice(page, ls.COMPARE, "table", ls.SKELETON)        # a second block is appended
    assert "<!-- compare:begin -->\ntable\n<!-- compare:end -->" in page.read_text()
    assert "new" in page.read_text()


def test_compare_part_lists_the_run_list_for_a_name_with_files_and_no_results(tmp_path, capsys):
    import types
    _results(tmp_path, "AAA")
    data = tmp_path / "lobster"
    data.mkdir()
    (data / "BBB_2012-06-21_34200000_57600000_message_10.csv").write_text("")
    args = types.SimpleNamespace(data=str(data), compare=None, figure=False)
    summaries = ls.part_compare(args, tmp_path)
    out = capsys.readouterr().out
    assert [s.ticker for s in summaries] == ["AAA"]
    assert "BBB has LOBSTER files and no results" in out
    assert "ofi_prediction.py --ticker BBB --part decay" in out
    assert "one name so far" in out
    assert (tmp_path / "liquidity_compare.csv").exists()
    assert "AAA" in (tmp_path / "liquidity_spectrum.md").read_text()


def test_the_figure_draws_with_two_names(tmp_path):
    pytest.importorskip("matplotlib")
    a = NameSummary("AAA", 15.0, 2.6, 0.001, 308, 46, 0.41, 4.1, 0.557, 0.016, 0.543, 0.023,
                    0.0068, 0.59, 6.5, 11.0)
    b = NameSummary("BBB", 1.0, 3.3, 0.95, 5000, 120, 0.6, 0.2, 0.52, 0.02, 0.51, 0.03,
                    0.001, 0.05, 0.5, 10.0)
    ls.draw([a, b], tmp_path / "f.png")
    assert (tmp_path / "f.png").stat().st_size > 10_000
