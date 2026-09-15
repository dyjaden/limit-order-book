"""Order lifecycles: how long orders live, how they die, how often they trade.

    python scripts/lifecycle_report.py --ticker AAPL       # CSVs + summary
    python scripts/lifecycle_report.py --ticker MSFT
    python scripts/lifecycle_report.py --figure            # from the CSVs

Compute mode folds the message file into one life per visible order
(see lob.lifecycle) and writes two files: results/lifecycle_{ticker}.csv,
every statistic in long form with its DEFINITION in the next column so
no number can travel without it, and results/survival_{ticker}.csv, the
Kaplan-Meier survival curve sampled on a log-spaced grid beside the
naive observed-deaths curve. Figure mode draws
results/figures/lifetimes.png from the survival CSVs: S(t) against
time since submission on a log axis, one line per name, the censored
fraction in the legend because a survival curve that hides its
censoring is a lie of omission.

Week 4 row B of the plan. No plotting import in compute mode.
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

from lob.lifecycle import (SURVIVAL_GRID_NS, kaplan_meier, lifecycles,
                           summarize, survival_at)
from lob.lobster import read_messages, read_orderbook

DATE = "2012-06-21"
SPAN = "34200000_57600000"
SERIES = {"AAPL": "#2a78d6", "MSFT": "#eb6834", "INTC": "#1baf7a",
          "AMZN": "#eda100", "GOOG": "#e87ba4"}

# 60 points per decade from 0.1 ms to the session length, for the curve
_GRID = [int(10 ** (5 + i / 20)) for i in range(0, 20 * 6 + 1)]   # 1e5 .. 1e11 ns
_GRID = [t for t in _GRID if t <= 23_400 * 10**9] + [23_400 * 10**9]


def paths(data: Path, ticker: str, level: int) -> tuple[Path, Path]:
    stem = f"{ticker}_{DATE}_{SPAN}"
    return (data / f"{stem}_message_{level}.csv",
            data / f"{stem}_orderbook_{level}.csv")


def fmt(v, digits=3) -> str:
    if v is None:
        return "n/a"
    if isinstance(v, float):
        return f"{v:,.{digits}f}"
    return f"{v:,}"


def compute(args) -> None:
    msg_path, book_path = paths(Path(args.data), args.ticker, args.level)
    for p in (msg_path, book_path):
        if not p.exists():
            raise SystemExit(f"no such file: {p}")
    msgs = read_messages(msg_path)
    ref = read_orderbook(book_path, args.level)
    res = lifecycles(msgs, ref)
    stats = summarize(res)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    # ---- the long-form statistics file
    spath = out / f"lifecycle_{args.ticker}.csv"
    with open(spath, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["section", "statistic", "value", "definition"])
        for s in stats:
            v = "" if s.value is None else (
                f"{s.value:.6f}" if isinstance(s.value, float) else s.value)
            w.writerow([s.section, s.name, v, s.definition])

    # ---- the survival curve, sampled
    observed = sorted(l.lifetime_ns for l in res.lives
                      if l.lifetime_ns is not None)
    censored = [l.censor_ns for l in res.lives if l.lifetime_ns is None]
    curve = kaplan_meier(observed, censored)
    n_obs = len(observed)
    cpath = out / f"survival_{args.ticker}.csv"
    with open(cpath, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["t_ns", "t_s", "S_km", "S_naive", "n_observed",
                    "n_censored"])
        j = 0
        for t in _GRID:
            while j < n_obs and observed[j] <= t:
                j += 1
            naive = (n_obs - j) / n_obs if n_obs else ""
            w.writerow([t, f"{t / 1e9:.6f}", f"{survival_at(curve, t):.6f}",
                        "" if naive == "" else f"{naive:.6f}", n_obs,
                        len(censored)])

    # ---- the summary on screen
    by = {(s.section, s.name): s.value for s in stats}
    print(f"LIFECYCLES {args.ticker} {DATE} level {args.level}, "
          f"{len(msgs):,} messages")
    print(f"  orders added {by['ends', 'orders_added']:,}: executed "
          f"{by['ends', 'orders_executed']:,} "
          f"({100 * by['ends', 'share_executed']:.1f}%), cancelled "
          f"{by['ends', 'orders_cancelled']:,} "
          f"({100 * by['ends', 'share_cancelled']:.1f}%), censored "
          f"{by['ends', 'orders_censored']:,} "
          f"({100 * by['ends', 'share_censored']:.1f}%)")
    print(f"  shares added {by['ends', 'shares_added']:,}: executed "
          f"{by['ends', 'shares_executed']:,}, cancelled "
          f"{by['ends', 'shares_cancelled']:,}, unresolved "
          f"{by['ends', 'shares_unresolved']:,}")
    print(f"  dark ops {by['accounting', 'dark_ops']:,} "
          f"({by['accounting', 'dark_shares']:,} sh), hidden executions "
          f"{by['accounting', 'hidden_execs']:,}, size disagreements "
          f"{by['accounting', 'size_disagreements']:,}, re-added ids "
          f"{by['accounting', 'readded_ids']:,}")
    print()
    print(f"  lifetimes, observed deaths only (ms):  "
          f"cancelled p10/p50/p90 {fmt(by['lifetime_naive', 'cancelled_p10_ms'], 1)} / "
          f"{fmt(by['lifetime_naive', 'cancelled_p50_ms'], 1)} / "
          f"{fmt(by['lifetime_naive', 'cancelled_p90_ms'], 1)};  executed "
          f"{fmt(by['lifetime_naive', 'executed_p10_ms'], 1)} / "
          f"{fmt(by['lifetime_naive', 'executed_p50_ms'], 1)} / "
          f"{fmt(by['lifetime_naive', 'executed_p90_ms'], 1)}")
    print(f"  under 100 ms {100 * by['lifetime_naive', 'share_under_100ms']:.1f}%, "
          f"under 1 s {100 * by['lifetime_naive', 'share_under_1s']:.1f}%, "
          f"over 1 min {100 * by['lifetime_naive', 'share_over_1min']:.1f}% "
          f"of observed deaths")
    med = by["lifetime_km", "median_ms"]
    print(f"  Kaplan-Meier: censored {100 * by['lifetime_km', 'censored_share']:.1f}%, "
          f"median {fmt(med, 1)} ms; S(t) at "
          + "  ".join(f"{_tlabel(t)}:{by['lifetime_km', f'S_at_{_tlabel(t)}']:.3f}"
                      for t in SURVIVAL_GRID_NS))
    print()
    print(f"  {'distance at add':<16} {'orders':>9} {'% adds':>7} "
          f"{'fill % sh':>10} {'fill % ord':>11}")
    for b in ("improve", "at_best", "1", "2_to_5", "6_to_10", "over_10",
              "unknown"):
        o = by["fill_by_distance", f"{b}_orders"]
        if not o:
            continue
        print(f"  {b:<16} {o:>9,} "
              f"{100 * by['fill_by_distance', f'{b}_share_of_adds']:>6.1f}% "
              f"{100 * (by['fill_by_distance', f'{b}_fill_rate_shares'] or 0):>9.1f}% "
              f"{100 * (by['fill_by_distance', f'{b}_fill_rate_orders'] or 0):>10.1f}%")
    print()
    print(f"  sizes: round lots {100 * by['sizes', 'adds_round_lot_share']:.1f}% "
          f"of adds, odd lots {100 * by['sizes', 'adds_odd_lot_share']:.1f}%, "
          f"median add {by['sizes', 'add_size_p50']:,} sh; odd-lot executions "
          f"{100 * by['cancel_to_trade', 'odd_lot_share_of_executions']:.1f}%, "
          f"hidden executions {100 * by['cancel_to_trade', 'hidden_share_of_executions']:.1f}%")
    print(f"  cancel-to-trade: by messages {fmt(by['cancel_to_trade', 'by_messages_full_day'], 2)} "
          f"(full day), {fmt(by['cancel_to_trade', 'by_messages_midas_window'], 2)} "
          f"(9:35 to 16:00, the MIDAS window); by orders "
          f"{fmt(by['cancel_to_trade', 'by_orders'], 2)}; by shares "
          f"{fmt(by['cancel_to_trade', 'by_shares'], 2)}")
    print(f"\n  wrote {spath} ({len(stats)} statistics) and {cpath} "
          f"({len(_GRID)} grid points)")


def _tlabel(ns: int) -> str:
    if ns < 10**9:
        return f"{ns // 10**6}ms"
    if ns < 60 * 10**9:
        return f"{ns // 10**9}s"
    if ns < 3600 * 10**9:
        return f"{ns // (60 * 10**9)}min"
    return f"{ns // (3600 * 10**9)}h"


# -------------------------------------------------------------- figure
def figure(args) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.ticker import FuncFormatter

    out = Path(args.out)
    found = []
    for ticker in args.tickers:
        p = out / f"survival_{ticker}.csv"
        if p.exists():
            with open(p, newline="") as f:
                found.append((ticker, list(csv.DictReader(f))))
        else:
            print(f"  (no {p}; run --ticker {ticker} first)", file=sys.stderr)
    if not found:
        raise SystemExit("nothing to draw")

    fig, ax = plt.subplots(figsize=(10, 5.2))
    for ticker, rows in found:
        colour = SERIES.get(ticker, "#52514e")
        t = [float(r["t_s"]) for r in rows]
        s = [float(r["S_km"]) for r in rows]
        n_obs, n_cen = int(rows[0]["n_observed"]), int(rows[0]["n_censored"])
        cen = n_cen / (n_obs + n_cen)
        ax.plot(t, s, color=colour, linewidth=2,
                label=f"{ticker}: {n_obs + n_cen:,} orders, "
                      f"{100 * cen:.1f}% censored")
        # the exact Kaplan-Meier median from the statistics file, marked
        # once at S = 1/2 (the sampled curve would round it to the grid)
        med = _km_median_seconds(out / f"lifecycle_{ticker}.csv")
        if med is not None:
            ax.plot([med], [0.5], marker="o", markersize=8, color=colour,
                    markeredgecolor="#fcfcfb", markeredgewidth=2)
            ax.annotate(f"median {_fmt_seconds(med)}", (med, 0.5),
                        xytext=(8, 8), textcoords="offset points",
                        color="#52514e", fontsize=9)
    ax.set_xscale("log")
    ax.set_xlim(1e-4, 23_400)
    ax.set_ylim(0, 1)
    ax.set_xlabel("time since submission (log scale), 2012-06-21, Nasdaq, "
                  "LOBSTER level 10")
    ax.set_ylabel("share of orders still resting, S(t)\n(Kaplan-Meier)")
    ax.xaxis.set_major_formatter(FuncFormatter(lambda v, _: _fmt_seconds(v)))
    ax.set_xticks([1e-3, 1e-2, 1e-1, 1, 10, 60, 600, 3600, 23_400])
    ax.grid(True, color="#e6e5e0", linewidth=0.8)
    ax.set_axisbelow(True)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    ax.spines["left"].set_color("#c3c2b7")
    ax.spines["bottom"].set_color("#c3c2b7")
    ax.tick_params(colors="#52514e")
    ax.legend(frameon=False, loc="upper right")
    fig.suptitle("Order survival: how long a visible limit order rests "
                 "before it is cancelled or filled", x=0.125, ha="left",
                 fontsize=12, color="#0b0b0b")
    fig.subplots_adjust(top=0.9, bottom=0.13, left=0.1, right=0.97)
    target = out / "figures" / "lifetimes.png"
    target.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(target, dpi=150, facecolor="#fcfcfb")
    print(f"  drew {target} ({', '.join(t for t, _ in found)})")


def _km_median_seconds(stats_csv: Path) -> float | None:
    if not stats_csv.exists():
        return None
    with open(stats_csv, newline="") as f:
        for r in csv.DictReader(f):
            if r["section"] == "lifetime_km" and r["statistic"] == "median_ms":
                return float(r["value"]) / 1000 if r["value"] else None
    return None


def _fmt_seconds(v: float) -> str:
    if v < 1:
        return f"{v * 1000:g} ms"
    if v < 60:
        return f"{v:g} s"
    if v < 3600:
        return f"{v / 60:g} min"
    return f"{v / 3600:g} h"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ticker", default="AAPL")
    ap.add_argument("--level", type=int, default=10, choices=(1, 5, 10))
    ap.add_argument("--data", default="data/lobster")
    ap.add_argument("--out", default="results")
    ap.add_argument("--figure", action="store_true")
    ap.add_argument("--tickers", nargs="+", default=["AAPL", "MSFT"])
    args = ap.parse_args()
    if args.figure:
        figure(args)
    else:
        compute(args)


if __name__ == "__main__":
    main()
