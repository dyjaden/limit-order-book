"""Where AAPL sits, and what changes on the names that sit elsewhere.

    python scripts/liquidity_spectrum.py --part midas       # the day's universe
    python scripts/liquidity_spectrum.py --part compare     # every name with results
    python scripts/liquidity_spectrum.py --part compare --figure

``--part midas`` places the five LOBSTER sample names on the SEC's
MIDAS spectrum for the day (``lob.spectrum.place``): each metric's value
and percentile among every security in the file, beside the SEC's own
deciles, plus the universe's decile cut points for lit trades and
cancel-to-trade. It writes results/liquidity_spectrum.csv and splices
its table into results/liquidity_spectrum.md between
``<!-- spectrum:begin -->`` and ``<!-- spectrum:end -->``.

``--part compare`` reads the Week 4 to 6 results of every name that has
them (``lob.spectrum.summarize``) and puts them side by side: spread,
depth, updates, the contemporaneous fit, the predictive hit rates with
their bands, the five-second cost verdict. It writes
results/liquidity_compare.csv and splices between ``<!-- compare:begin -->``
and ``<!-- compare:end -->``. A name whose LOBSTER files are on disk
but whose results are not gets the run list printed, in order, each
step a registry-logging run where Days 5 and 6 were, to be run once.
With one name the table has one row and says the second column is
waiting on data/lobster/.

``--figure`` draws results/figures/liquidity_spectrum.png when two or
more names have rows: the predictive hit rate and the consumed factor
against the four axes of the spectrum. No regression runs here and no
registry row is added; this script reads what earlier steps logged.

Week 7 row A of the plan.
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

from intraday_report import DATE, SERIES, paths
from lob.spectrum import (DECILES, LABELS, METRICS, SAMPLE_NAMES, NameSummary,
                          Position, deciles, missing_results, place,
                          read_midas_day, summarize)

SPECTRUM = ("<!-- spectrum:begin -->", "<!-- spectrum:end -->")
COMPARE = ("<!-- compare:begin -->", "<!-- compare:end -->")
RATES = ("cancel_to_trade", "trade_to_order_volume", "hidden_rate", "odd_lot_rate")
RUN_LIST = (
    "python scripts/intraday_report.py --ticker {t}",
    "python scripts/lifecycle_report.py --ticker {t}",
    "python scripts/depth_profile.py --ticker {t}",
    "python scripts/microstructure_checks.py --ticker {t} --write results/microstructure.md",
    "python scripts/ofi_report.py --ticker {t}",
    "python scripts/ofi_report.py --ticker {t} --clock calendar --bucket 10",
    "python scripts/ofi_report.py --ticker {t} --clock event --bucket 50",
    "python scripts/ofi_report.py --ticker {t} --figure",
    "python scripts/ofi_first_look.py --ticker {t} --figure          # 19 registry rows",
    "python scripts/ofi_prediction.py --ticker {t} --part decay      # 24 rows",
    "python scripts/ofi_prediction.py --ticker {t} --part regimes    # 10 rows",
    "python scripts/ofi_prediction.py --ticker {t} --part cost       # 24 rows",
    "python scripts/ofi_prediction.py --ticker {t} --part writeup --figure",
    "python scripts/liquidity_spectrum.py --part compare --figure",
)


def fmt(x, nd=2, plus=False) -> str:
    if x is None:
        return "n/a"
    return f"{x:+,.{nd}f}" if plus else f"{x:,.{nd}f}"


def pct(x, nd=1) -> str:
    return "n/a" if x is None else f"{100 * x:.{nd}f}%"


def value_str(metric: str, v) -> str:
    if v is None:
        return "n/a"
    if metric in ("LitTrades", "Cancels"):
        return f"{v:,.0f}"
    if metric == "LitVol":
        return f"{v / 1e6:,.1f}M"
    if metric == "cancel_to_trade":
        return f"{v:.1f}"
    return pct(v)


# -------------------------------------------------------------- splice
def splice(path: Path, markers: tuple[str, str], block: str, skeleton: str) -> None:
    begin, end = markers
    body = f"{begin}\n{block}\n{end}"
    if path.exists():
        text = path.read_text(encoding="utf-8")
        if begin in text and end in text:
            head, rest = text.split(begin, 1)
            _, tail = rest.split(end, 1)
            text = head + body + tail
        else:
            text = text.rstrip("\n") + "\n\n" + body + "\n"
    else:
        text = skeleton.format(block=body)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


SKELETON = """# The liquidity spectrum

Where the one name sits among every security that traded the same day,
and what the Week 4 to 6 numbers look like on the names that sit
elsewhere.

{block}
"""


# --------------------------------------------------------------- midas
def midas_rows(positions: list[Position]) -> list[dict]:
    rows = []
    for p in positions:
        row = {"ticker": p.ticker, "present": p.present, "security": p.security,
               "universe": p.universe}
        for d in DECILES:
            row[d] = p.ranks.get(d)
        for m in METRICS:
            row[m] = p.values.get(m)
            row[m + "_pct"] = p.percentiles.get(m)
        rows.append(row)
    return rows


def render_midas(positions: list[Position], universe: dict, date: str) -> str:
    n = positions[0].universe if positions else 0
    L = [f"Generated by `scripts/liquidity_spectrum.py --part midas` from the SEC's MIDAS file "
         f"for {date}: {n:,} securities on the day. Values are the README's definitions "
         f"(volumes in shares); the percentile is the share of the universe at or below the "
         f"value; the SEC's four deciles (10 the highest) are quoted as the file gives them.", "",
         "| name | security | mcap | turnover | volatility | price | "
         + " | ".join(f"{LABELS[m]} (pct)" for m in METRICS) + " |",
         "|---|---|---:|---:|---:|---:|" + "---:|" * len(METRICS)]
    for p in positions:
        if not p.present:
            L.append(f"| {p.ticker} | absent from the file | | | | |" + " |" * len(METRICS))
            continue
        cells = " | ".join(f"{value_str(m, p.values[m])} ({pct(p.percentiles[m], 0)})"
                           for m in METRICS)
        L.append(f"| {p.ticker} | {p.security} | {p.ranks['McapRank']} | {p.ranks['TurnRank']} | "
                 f"{p.ranks['VolatilityRank']} | {p.ranks['PriceRank']} | {cells} |")
    L += ["", "Universe decile cut points (10th to 90th percentile):", ""]
    for m in ("LitTrades", "cancel_to_trade", "odd_lot_rate", "hidden_rate"):
        cuts = deciles(rec["_metrics"][m] for rec in universe.values())
        L.append(f"- {LABELS[m]}: " + ", ".join(value_str(m, c) for c in cuts))
    return "\n".join(L)


def print_midas(positions: list[Position], universe: dict, date: str) -> None:
    n = positions[0].universe if positions else 0
    print(f"MIDAS SPECTRUM {date}: {n:,} securities; the LOBSTER sample names placed on it")
    print(f"  {'name':<6} {'sec':<6} {'mcap':>4} {'turn':>4} {'vol':>4} {'px':>4}  "
          + "  ".join(f"{LABELS[m][:18]:>24}" for m in METRICS))
    for p in positions:
        if not p.present:
            print(f"  {p.ticker:<6} absent from the file")
            continue
        cells = "  ".join(f"{value_str(m, p.values[m]) + ' (' + pct(p.percentiles[m], 0) + ')':>24}"
                          for m in METRICS)
        print(f"  {p.ticker:<6} {str(p.security)[:6]:<6} {p.ranks['McapRank']:>4} "
              f"{p.ranks['TurnRank']:>4} {p.ranks['VolatilityRank']:>4} {p.ranks['PriceRank']:>4}  {cells}")
    print("\n  universe decile cut points (10th to 90th):")
    for m in ("LitTrades", "cancel_to_trade", "odd_lot_rate", "hidden_rate"):
        cuts = deciles(rec["_metrics"][m] for rec in universe.values())
        print(f"    {LABELS[m]:<22} " + ", ".join(value_str(m, c) for c in cuts))


def part_midas(args, out: Path) -> None:
    universe = read_midas_day(Path(args.midas), args.date)
    if not universe:
        raise SystemExit(f"no MIDAS rows for {args.date} under {args.midas}")
    positions = place(universe, args.names)
    print_midas(positions, universe, args.date)
    rows = midas_rows(positions)
    target = out / "liquidity_spectrum.csv"
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]), lineterminator="\n")
        w.writeheader()
        for r in rows:
            w.writerow({k: ("" if v is None else v) for k, v in r.items()})
    splice(out / "liquidity_spectrum.md", SPECTRUM, render_midas(positions, universe, args.date),
           SKELETON)
    print(f"\n  wrote {target} and spliced the table into {out / 'liquidity_spectrum.md'}")


# ------------------------------------------------------------- compare
COMPARE_COLUMNS = ("ticker", "spread_cents", "spread_bps", "one_cent_share", "touch_depth",
                   "updates_per_10s", "r2_contemporaneous_10s", "beta_contemporaneous",
                   "hit_1s", "band_1s", "hit_5s", "band_5s", "r2_oos_5s", "gross_5s",
                   "half_spread_5s", "consumed_5s")


def lobster_tickers(data: Path) -> list[str]:
    return sorted({p.name.split("_")[0] for p in Path(data).glob("*_message_10.csv")})


def result_tickers(out: Path) -> list[str]:
    return sorted({p.name[len("ofi_decay_"):-4] for p in Path(out).glob("ofi_decay_*.csv")})


def render_compare(summaries: list[NameSummary], waiting: list[str]) -> str:
    L = [f"Generated by `scripts/liquidity_spectrum.py --part compare` from each name's Week 4 "
         f"to 6 results; no regression is run here. Spread and depth are time-weighted over the "
         f"session from the five-minute bins; updates are best-quote updates per ten-second bin; "
         f"the contemporaneous fit is the first look's ten-second row in the registry; the "
         f"predictive numbers are the primary split's; the cost row is five seconds, all signals, "
         f"latency 0.", "",
         "| name | spread (c) | spread (bps) | one-cent share | touch depth (sh) | updates / 10 s | "
         "R2 contemp. 10 s | beta (c per 1,000 sh) | hit 1 s | hit 5 s | R2 oos 5 s | gross 5 s (c) | "
         "half-spread (c) | consumed |",
         "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for s in summaries:
        L.append(f"| {s.ticker} | {fmt(s.spread_cents)} | {fmt(s.spread_bps)} | "
                 f"{pct(s.one_cent_share)} | {fmt(s.touch_depth, 0)} | {fmt(s.updates_per_10s, 1)} | "
                 f"{fmt(s.r2_contemporaneous_10s)} | {fmt(s.beta_contemporaneous)} | "
                 f"{pct(s.hit_1s)} (+/-{pct(s.band_1s)}) | {pct(s.hit_5s)} (+/-{pct(s.band_5s)}) | "
                 f"{fmt(None if s.r2_oos_5s is None else 100 * s.r2_oos_5s, 2, True)}% | "
                 f"{fmt(s.gross_5s, 2, True)} | {fmt(s.half_spread_5s)} | "
                 f"{(fmt(s.consumed_5s, 0) + 'x') if s.consumed_5s is not None else 'n/a'} |")
    if len(summaries) < 2:
        L += ["", f"One name so far. The second column is waiting on LOBSTER files in `data/lobster/`"
                  + (f" (names with files but no results yet: {', '.join(waiting)})" if waiting else "")
                  + "; the run list is in the script's docstring and is printed by `--part compare` "
                    "when a name has files and no results."]
    return "\n".join(L)


def print_compare(summaries: list[NameSummary], waiting: list[str], absent: dict) -> None:
    print("LIQUIDITY COMPARE: every name whose Week 4 to 6 results exist")
    print(f"  {'name':<6} {'spread c':>9} {'bps':>6} {'1c %':>6} {'touch':>6} {'upd/10s':>8} "
          f"{'R2 c10':>7} {'beta':>6} {'hit 1s':>14} {'hit 5s':>14} {'R2oos5':>7} "
          f"{'gross5':>7} {'hspr':>6} {'consumed':>9}")
    for s in summaries:
        print(f"  {s.ticker:<6} {fmt(s.spread_cents):>9} {fmt(s.spread_bps):>6} "
              f"{pct(s.one_cent_share):>6} {fmt(s.touch_depth, 0):>6} {fmt(s.updates_per_10s, 1):>8} "
              f"{fmt(s.r2_contemporaneous_10s):>7} {fmt(s.beta_contemporaneous):>6} "
              f"{pct(s.hit_1s) + ' +/-' + pct(s.band_1s):>14} {pct(s.hit_5s) + ' +/-' + pct(s.band_5s):>14} "
              f"{fmt(None if s.r2_oos_5s is None else 100 * s.r2_oos_5s, 2, True) + '%':>7} "
              f"{fmt(s.gross_5s, 2, True):>7} {fmt(s.half_spread_5s):>6} "
              f"{(fmt(s.consumed_5s, 0) + 'x') if s.consumed_5s is not None else 'n/a':>9}")
    for t, missing in absent.items():
        print(f"  {t}: results incomplete, missing " + ", ".join(str(m) for m in missing))
    if waiting:
        for t in waiting:
            print(f"\n  {t} has LOBSTER files and no results. The run list, once, in order:")
            for line in RUN_LIST:
                print("    " + line.format(t=t))
    if len(summaries) < 2:
        print("\n  one name so far; the second column is waiting on data/lobster/")


def part_compare(args, out: Path) -> list[NameSummary]:
    with_files = lobster_tickers(Path(args.data))
    with_results = result_tickers(out)
    tickers = sorted(set(with_files) | set(with_results) | set(args.compare or []))
    summaries, absent, waiting = [], {}, []
    for t in tickers:
        s = summarize(out, t)
        if s is not None:
            summaries.append(s)
        elif t in with_files:
            waiting.append(t)
        else:
            absent[t] = missing_results(out, t)
    print_compare(summaries, waiting, absent)
    target = out / "liquidity_compare.csv"
    with open(target, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(COMPARE_COLUMNS), lineterminator="\n")
        w.writeheader()
        for s in summaries:
            w.writerow({c: ("" if getattr(s, c) is None else getattr(s, c)) for c in COMPARE_COLUMNS})
    splice(out / "liquidity_spectrum.md", COMPARE, render_compare(summaries, waiting), SKELETON)
    print(f"\n  wrote {target} and spliced the table into {out / 'liquidity_spectrum.md'}")
    if args.figure:
        if len(summaries) >= 2:
            draw(summaries, out / "figures" / "liquidity_spectrum.png")
        else:
            print("  (no figure with one name)")
    return summaries


# -------------------------------------------------------------- figure
def draw(summaries: list[NameSummary], target: Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    ink, muted, grid, spine = "#0b0b0b", "#52514e", "#e6e5e0", "#c3c2b7"
    axes_spec = (("spread_bps", "quoted spread, bps"), ("touch_depth", "touch depth, shares"),
                 ("updates_per_10s", "best-quote updates per 10 s"),
                 ("one_cent_share", "share of time at a one-cent spread"))
    fig, grid_axes = plt.subplots(2, 4, figsize=(13, 6.4), gridspec_kw={"hspace": 0.4, "wspace": 0.35})
    for col, (attr, label) in enumerate(axes_spec):
        for row, (yattr, ylabel) in enumerate((("hit_5s", "5 s hit rate, %"),
                                               ("consumed_5s", "half-spread over gross edge"))):
            ax = grid_axes[row][col]
            for s in summaries:
                x, y = getattr(s, attr), getattr(s, yattr)
                if x is None or y is None:
                    continue
                if yattr == "hit_5s":
                    y = 100 * y
                    band = 100 * (s.band_5s or 0)
                    ax.errorbar([x], [y], yerr=[band], fmt="o", color=SERIES.get(s.ticker, muted),
                                markersize=8, capsize=4, linewidth=1.2)
                else:
                    ax.scatter([x], [y], s=64, color=SERIES.get(s.ticker, muted), linewidths=0)
                ax.annotate(s.ticker, (x, y), xytext=(7, 0), textcoords="offset points",
                            fontsize=9, color=muted, va="center")
            if yattr == "hit_5s":
                ax.axhline(50, color=spine, linewidth=1)
            else:
                ax.axhline(1, color=spine, linewidth=1)
                ax.set_yscale("log")
            if attr in ("touch_depth", "updates_per_10s"):
                ax.set_xscale("log")
            ax.set_xlabel(label, color=muted)
            if col == 0:
                ax.set_ylabel(ylabel, color=muted)
            ax.grid(True, color=grid, linewidth=0.8)
            ax.set_axisbelow(True)
            for side in ("top", "right"):
                ax.spines[side].set_visible(False)
            ax.spines["left"].set_color(spine)
            ax.spines["bottom"].set_color(spine)
            ax.tick_params(colors=muted)
    fig.suptitle(f"The same question down the spectrum, {DATE}: five-second direction and the "
                 f"cost verdict against four axes of liquidity", x=0.06, ha="left", fontsize=12, color=ink)
    fig.subplots_adjust(top=0.9, bottom=0.1, left=0.06, right=0.98)
    target.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(target, dpi=150, facecolor="#fcfcfb")
    print(f"  drew {target}")


# ---------------------------------------------------------------- main
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--part", choices=("midas", "compare"), required=True)
    ap.add_argument("--midas", default="data/midas")
    ap.add_argument("--data", default="data/lobster")
    ap.add_argument("--out", default="results")
    ap.add_argument("--date", default=DATE)
    ap.add_argument("--names", nargs="+", default=list(SAMPLE_NAMES),
                    help="tickers to place on the MIDAS spectrum")
    ap.add_argument("--compare", nargs="+", default=None,
                    help="extra tickers to look for results of")
    ap.add_argument("--figure", action="store_true")
    args = ap.parse_args()
    out = Path(args.out)
    if args.part == "midas":
        part_midas(args, out)
    else:
        part_compare(args, out)


if __name__ == "__main__":
    main()
