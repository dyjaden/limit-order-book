"""The intraday U: spread and depth through the day, time-weighted.

    python scripts/intraday_report.py --ticker AAPL        # CSV + summary
    python scripts/intraday_report.py --ticker MSFT
    python scripts/intraday_report.py --figure             # from the CSVs

Two modes, deliberately separate. The compute mode reads a LOBSTER
message/orderbook pair through the Day 2 loaders, bins the session
(five minutes by default), and writes results/intraday_{ticker}_5min.csv
with every integer accumulator AND every derived ratio, so anyone can
re-derive the floats from the sums. It imports no plotting library. The
figure mode reads whatever intraday CSVs exist and draws
results/figures/intraday_spread_depth.png: spread in basis points on
top (in cents, MSFT would be a flat line at 1), touch depth in shares
on a log scale below, one line per name, both panels on the same clock.

Everything here is Week 4 row A of the plan; the numbers it prints are
graded against the expectations written in the Day 4 guide BEFORE this
script first ran, and the write-up keeps that list whether or not it
was right.
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

from lob.lobster import read_messages, read_orderbook
from lob.micro import (ACCUMULATORS, NS, RATIOS, BinStats, day_summary,
                       hms, time_weighted)

DATE = "2012-06-21"
SPAN = "34200000_57600000"

# categorical slots in fixed order, never cycled: the name decides the
# colour, so a filter that drops a name never repaints the survivors
SERIES = {"AAPL": "#2a78d6", "MSFT": "#eb6834", "INTC": "#1baf7a",
          "AMZN": "#eda100", "GOOG": "#e87ba4"}


def paths(data: Path, ticker: str, level: int) -> tuple[Path, Path]:
    stem = f"{ticker}_{DATE}_{SPAN}"
    return (data / f"{stem}_message_{level}.csv",
            data / f"{stem}_orderbook_{level}.csv")


def csv_path(out: Path, ticker: str, width_s: int) -> Path:
    label = f"{width_s // 60}min" if width_s % 60 == 0 else f"{width_s}s"
    return out / f"intraday_{ticker}_{label}.csv"


# ------------------------------------------------------------- compute
def write_bins(bins: list[BinStats], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    cols = ["bin", "start_s", "start_hms"] + list(ACCUMULATORS) + list(RATIOS)
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(cols)
        for b in bins:
            row = [b.index, b.start_ns // NS, hms(b.start_ns)]
            row += [getattr(b, a) for a in ACCUMULATORS]
            row += ["" if getattr(b, r) is None else f"{getattr(b, r):.6f}"
                    for r in RATIOS]
            w.writerow(row)


def fmt(x, digits=1) -> str:
    return "n/a" if x is None else f"{x:,.{digits}f}"


def print_summary(ticker: str, level: int, n_msgs: int,
                  windows: dict[str, BinStats], width_s: int) -> None:
    d = windows["day"]
    print(f"INTRADAY {ticker} {DATE} level {level}, {n_msgs:,} messages, "
          f"{width_s}-second bins, time-weighted")
    print(f"  session time accounted: {d.hold_ns / NS:,.1f}s of "
          f"{(d.width_ns) / NS:,.0f}s; two-sided {d.two_sided_ns / NS:,.1f}s, "
          f"one-sided or empty {(d.one_sided_ns + d.empty_ns) / NS:,.3f}s")
    print(f"  events: {d.messages:,} messages, {d.adds:,} adds "
          f"({d.add_shares:,} sh), {d.cancels:,} cancels ({d.cancel_shares:,} sh), "
          f"{d.trades:,} visible executions ({d.trade_shares:,} sh), "
          f"{d.hidden:,} hidden ({d.hidden_shares:,} sh)")
    print(f"  cancel-to-trade by messages, whole day: "
          f"{d.cancels / max(1, d.trades + d.hidden):,.1f}")
    print()
    print(f"  {'window':<20} {'spread c':>10} {'spread bp':>10} "
          f"{'1-cent %':>9} {'touch sh':>10} {'depth10 sh':>11} "
          f"{'mid $':>9} {'trades':>8} {'volume':>10}")
    for key in ("day", "open5", "midday", "close5"):
        w = windows[key]
        one = w.one_cent_share
        print(f"  {w.label:<20} {fmt(w.tw_spread_cents, 2):>10} "
              f"{fmt(w.tw_spread_bps, 2):>10} "
              f"{('n/a' if one is None else f'{100 * one:.1f}'):>9} "
              f"{fmt(w.tw_touch_depth, 0):>10} {fmt(w.tw_depth10, 0):>11} "
              f"{fmt(None if w.tw_mid_ticks is None else w.tw_mid_ticks / 10_000, 2):>9} "
              f"{w.trades:>8,} {w.trade_shares:>10,}")


def compute(args) -> None:
    msg_path, book_path = paths(Path(args.data), args.ticker, args.level)
    for p in (msg_path, book_path):
        if not p.exists():
            raise SystemExit(f"no such file: {p}")
    width_ns = args.bin * NS
    msgs = read_messages(msg_path)
    ref = read_orderbook(book_path, args.level)
    bins = time_weighted(msgs, ref, width_ns)
    out = csv_path(Path(args.out), args.ticker, args.bin)
    write_bins(bins, out)
    windows = day_summary(bins)
    print_summary(args.ticker, args.level, len(msgs), windows, args.bin)
    print(f"\n  wrote {out} ({len(bins)} bins)")


# -------------------------------------------------------------- figure
def read_bins_csv(path: Path) -> list[dict]:
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def figure(args) -> None:
    import matplotlib
    matplotlib.use("Agg")                     # headless: CI and shells alike
    import matplotlib.pyplot as plt
    from matplotlib.ticker import FuncFormatter, LogLocator, NullFormatter

    out = Path(args.out)
    found = []
    for ticker in args.tickers:
        p = csv_path(out, ticker, args.bin)
        if p.exists():
            found.append((ticker, read_bins_csv(p)))
        else:
            print(f"  (no {p}; run --ticker {ticker} first)", file=sys.stderr)
    if not found:
        raise SystemExit("nothing to draw")

    fig, (ax_sp, ax_dp) = plt.subplots(2, 1, figsize=(10, 6.4), sharex=True,
                                       gridspec_kw={"hspace": 0.12})
    for ticker, rows in found:
        colour = SERIES.get(ticker, "#52514e")
        x = [(int(r["start_s"]) + args.bin / 2) / 3600 for r in rows]
        bps = [float(r["tw_spread_bps"]) if r["tw_spread_bps"] else None
               for r in rows]
        depth = [float(r["tw_touch_depth"]) if r["tw_touch_depth"] else None
                 for r in rows]
        ax_sp.plot(x, bps, color=colour, linewidth=2, label=ticker)
        ax_dp.plot(x, depth, color=colour, linewidth=2, label=ticker)
        # a selective direct label at the last bin, not a number on every point
        for ax, ys in ((ax_sp, bps), (ax_dp, depth)):
            last = next((v for v in reversed(ys) if v is not None), None)
            if last is not None:
                ax.annotate(ticker, (x[-1], last), xytext=(6, 0),
                            textcoords="offset points", color="#52514e",
                            fontsize=9, va="center")

    ax_sp.set_ylabel("quoted spread, bps of mid\n(time-weighted, per bin)")
    ax_sp.set_ylim(bottom=0)
    ax_dp.set_ylabel("depth at the touch, shares\n(bid + ask, time-weighted)")
    ax_dp.set_yscale("log")
    # labels at 1, 2, 5 per decade, in plain shares: a log axis whose
    # ticks read as numbers, not exponents
    ax_dp.yaxis.set_major_locator(LogLocator(base=10, subs=(1.0, 2.0, 5.0)))
    ax_dp.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:,.0f}"))
    ax_dp.yaxis.set_minor_formatter(NullFormatter())
    ax_dp.set_xlabel("time of day (ET), 2012-06-21, Nasdaq, LOBSTER level 10")
    ax_dp.set_xlim(9.5, 16.0)
    ax_dp.xaxis.set_major_formatter(FuncFormatter(
        lambda h, _: f"{int(h):02d}:{int(round((h % 1) * 60)):02d}"))
    ax_dp.set_xticks([9.5, 10, 11, 12, 13, 14, 15, 16])
    for ax in (ax_sp, ax_dp):
        ax.grid(True, color="#e6e5e0", linewidth=0.8)
        ax.set_axisbelow(True)
        for side in ("top", "right"):
            ax.spines[side].set_visible(False)
        ax.spines["left"].set_color("#c3c2b7")
        ax.spines["bottom"].set_color("#c3c2b7")
        ax.tick_params(colors="#52514e")
    if len(found) > 1:
        ax_sp.legend(frameon=False, loc="upper right")
    width_label = f"{args.bin // 60}-minute" if args.bin % 60 == 0 else f"{args.bin}-second"
    fig.suptitle(f"Spread and touch depth through the day, {width_label} bins",
                 x=0.125, ha="left", fontsize=12, color="#0b0b0b")
    fig.subplots_adjust(top=0.92, bottom=0.09, left=0.11, right=0.95)
    target = out / "figures" / "intraday_spread_depth.png"
    target.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(target, dpi=150, facecolor="#fcfcfb")
    print(f"  drew {target} ({', '.join(t for t, _ in found)})")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ticker", default="AAPL")
    ap.add_argument("--level", type=int, default=10, choices=(1, 5, 10))
    ap.add_argument("--data", default="data/lobster")
    ap.add_argument("--out", default="results")
    ap.add_argument("--bin", type=int, default=300, help="bin width, seconds")
    ap.add_argument("--figure", action="store_true",
                    help="draw the figure from the CSVs already written")
    ap.add_argument("--tickers", nargs="+", default=["AAPL", "MSFT"],
                    help="names to draw (those whose CSV exists)")
    args = ap.parse_args()
    if args.figure:
        figure(args)
    else:
        compute(args)


if __name__ == "__main__":
    main()
