"""The liquidity spectrum: where a name sits, and what changes down it.

Week 7's instrument. Six weeks of numbers are one name on one day, and
AAPL in June 2012 is a particular kind of name: a $580 stock on a penny
grid, a 15-cent spread, 150 shares a side. Two questions follow. Where
does that name sit among every security that traded that day, and how
do the Week 4 to 6 numbers change on a name that sits somewhere else?

The first question is answered from a file already on disk. The SEC's
MIDAS data (Day 4) has one row per security per day for the quarter:
lit volume, lit trades, cancels, hidden and odd-lot counts, and four
decile ranks the SEC assigns (market cap, turnover, volatility, price;
10 the highest). ``place`` puts a list of tickers on that universe:
each metric's value, its percentile among every security on the day,
and the SEC's deciles beside it. The metrics are the README's own
definitions, the ones Day 4 matched before comparing: cancel-to-trade
is cancels over LIT trades, hidden and odd-lot rates are over the trades
of the exchanges that report them, trade-to-order volume is lit volume
over add volume. Volumes are shares; the file carries no price, so a
dollar axis is the SEC's price decile and nothing finer.

The second question is answered by the same scripts, run on the second
name, and ``summarize`` reads what they wrote: the time-weighted spread
and depth from the Day 4 bins, best-quote updates per bin from the Day
5 buckets, the contemporaneous fit from the trial registry (the only
place the first look's numbers live), and the predictive hit rates,
R-squared and the cost verdict from the Day 6 CSVs. Nothing here runs a
regression or adds a registry row: this module reads results, it does
not make them.
"""
from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from pathlib import Path

from lob.checks import read_midas
from lob.micro import INCREMENT, TICKS_PER_DOLLAR

SAMPLE_NAMES = ("AAPL", "MSFT", "INTC", "AMZN", "GOOG")   # LOBSTER's sample set
DECILES = ("McapRank", "TurnRank", "VolatilityRank", "PriceRank")
METRICS = ("LitTrades", "LitVol", "Cancels", "cancel_to_trade",
           "trade_to_order_volume", "hidden_rate", "odd_lot_rate")
LABELS = {"LitTrades": "lit trades", "LitVol": "lit volume, shares",
          "Cancels": "cancels", "cancel_to_trade": "cancel-to-trade",
          "trade_to_order_volume": "trade-to-order volume",
          "hidden_rate": "hidden rate", "odd_lot_rate": "odd-lot rate"}


# --------------------------------------------------------------- MIDAS
def derived(rec: dict) -> dict:
    """The README's metrics from a MIDAS record, None where the
    denominator is missing or zero."""
    def ratio(num, den):
        a, b = rec.get(num), rec.get(den)
        return None if a is None or not b else a / b
    return {"LitTrades": rec.get("LitTrades"),
            "LitVol": rec.get("LitVol('000)"),
            "Cancels": rec.get("Cancels"),
            "cancel_to_trade": ratio("Cancels", "LitTrades"),
            "trade_to_order_volume": ratio("LitVol('000)", "OrderVol('000)"),
            "hidden_rate": ratio("Hidden", "TradesForHidden"),
            "odd_lot_rate": ratio("OddLots", "TradesForOddLots")}


def read_midas_day(folder: Path, date: str = "2012-06-21") -> dict[str, dict]:
    """Every security on the date, keyed by ticker, each record carrying
    the file's columns and the derived metrics under ``_metrics``."""
    universe = read_midas(Path(folder), None, date)
    for rec in universe.values():
        rec["_metrics"] = derived(rec)
    return universe


def percentile(values, x) -> float | None:
    """The share of the universe at or below x, Nones left out."""
    vals = [v for v in values if v is not None]
    if x is None or not vals:
        return None
    return sum(1 for v in vals if v <= x) / len(vals)


def deciles(values) -> list[float]:
    """The nine cut points between the universe's deciles, nearest rank."""
    vals = sorted(v for v in values if v is not None)
    if not vals:
        return []
    return [vals[min(len(vals) - 1, int(q / 10 * (len(vals) - 1) + 0.5))]
            for q in range(1, 10)]


@dataclass(frozen=True)
class Position:
    """One ticker on the day's spectrum."""
    ticker: str
    present: bool
    security: str | None
    ranks: dict                    # the SEC's four deciles, as the file gives them
    values: dict                   # metric -> value
    percentiles: dict              # metric -> share of the universe at or below
    universe: int


def place(universe: dict[str, dict], tickers) -> list[Position]:
    columns = {m: [rec["_metrics"][m] for rec in universe.values()] for m in METRICS}
    out = []
    for t in tickers:
        rec = universe.get(t.upper())
        if rec is None:
            out.append(Position(t.upper(), False, None, {}, {}, {}, len(universe)))
            continue
        vals = rec["_metrics"]
        out.append(Position(t.upper(), True, rec.get("Security"),
                            {d: rec.get(d) for d in DECILES}, dict(vals),
                            {m: percentile(columns[m], vals[m]) for m in METRICS},
                            len(universe)))
    return out


# ------------------------------------------------------- the second column
@dataclass(frozen=True)
class NameSummary:
    """One name's Week 4 to 6 numbers, read from what the scripts wrote."""
    ticker: str
    spread_cents: float | None
    spread_bps: float | None
    one_cent_share: float | None
    touch_depth: float | None            # bid plus ask, shares, time-weighted
    updates_per_10s: float | None        # mean best-quote updates per ten-second bin
    r2_contemporaneous_10s: float | None
    beta_contemporaneous: float | None   # cents per 1,000 shares
    hit_1s: float | None
    band_1s: float | None
    hit_5s: float | None
    band_5s: float | None
    r2_oos_5s: float | None
    gross_5s: float | None
    half_spread_5s: float | None
    consumed_5s: float | None


RESULT_FILES = ("intraday_{t}_5min.csv", "ofi_{t}_calendar_10.csv", "ofi_decay_{t}.csv",
                "ofi_cost_{t}.csv", "trials.csv")


def missing_results(results: Path, ticker: str) -> list[Path]:
    """The files ``summarize`` needs and does not find."""
    return [Path(results) / f.format(t=ticker) for f in RESULT_FILES
            if not (Path(results) / f.format(t=ticker)).exists()]


def _rows(path: Path) -> list[dict]:
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def _f(row: dict, key: str) -> float | None:
    v = row.get(key)
    return None if v in (None, "") else float(v)


def _mean(xs) -> float | None:
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def contemporaneous(trials: list[dict], ticker: str) -> tuple[float | None, float | None]:
    """The first look's ten-second R-squared and origin slope for the
    ticker, from the registry: the last row the first-look script logged
    for the whole-session calendar ten-second fit."""
    rows = [r for r in trials if r.get("script") == "ofi_first_look.py"
            and r.get("ticker") == ticker and r.get("clock") == "calendar"
            and r.get("bucket") in ("10", "10.0") and r.get("metric") == "r2"
            and r.get("window") == "09:30-16:00"]
    if not rows:
        return None, None
    r = rows[-1]
    r2 = _f(r, "value")
    m = re.search(r"beta0=([-+0-9.eE]+)", r.get("note", ""))
    return r2, (float(m.group(1)) if m else None)


def summarize(results: Path, ticker: str) -> NameSummary | None:
    """None when any input is missing; ``missing_results`` says which."""
    results = Path(results)
    if missing_results(results, ticker):
        return None
    bins = _rows(results / f"intraday_{ticker}_5min.csv")
    two_sided = sum(_f(b, "two_sided_ns") or 0 for b in bins)
    spread_x = sum(_f(b, "spread_x_ns") or 0 for b in bins)
    mid2_x = sum(_f(b, "mid2_x_ns") or 0 for b in bins)
    one_cent = sum(_f(b, "one_cent_ns") or 0 for b in bins)
    touch_x = sum(_f(b, "touch_x_ns") or 0 for b in bins)
    spread_cents = spread_x / (INCREMENT * two_sided) if two_sided else None
    spread_bps = 2 * TICKS_PER_DOLLAR * spread_x / mid2_x if mid2_x else None
    one_cent_share = one_cent / two_sided if two_sided else None
    touch = touch_x / two_sided if two_sided else None
    cal = _rows(results / f"ofi_{ticker}_calendar_10.csv")
    updates = _mean(_f(b, "n_updates") for b in cal)
    r2c, beta = contemporaneous(_rows(results / "trials.csv"), ticker)
    decay = {(r["predictor"], float(r["horizon_s"])): r
             for r in _rows(results / f"ofi_decay_{ticker}.csv") if r["direction"] == "primary"}
    cost = {(r["subset"], int(r["latency"]), float(r["horizon_s"])): r
            for r in _rows(results / f"ofi_cost_{ticker}.csv")}
    d1, d5 = decay.get(("ofi", 1.0), {}), decay.get(("ofi", 5.0), {})
    c5 = cost.get(("all", 0, 5.0), {})
    return NameSummary(ticker, spread_cents, spread_bps, one_cent_share, touch, updates,
                       r2c, beta, _f(d1, "hit_rate"), _f(d1, "band"), _f(d5, "hit_rate"),
                       _f(d5, "band"), _f(d5, "r2_oos"), _f(c5, "gross_cents"),
                       _f(c5, "half_spread_cents"), _f(c5, "consumed"))
