"""Book shape: depth by occupied level and by cent from the touch.

    python scripts/depth_profile.py --ticker AAPL        # CSV + summary
    python scripts/depth_profile.py --ticker MSFT
    python scripts/depth_profile.py --figure             # from the CSVs

The day's argument in two pictures. By occupied level (LOBSTER's own
unit): the time-weighted shares at levels 1 to 10 each side, with the
mean distance of each level from the touch, because "level 5" is a
rank, not a distance. By cent from the best (the regime-comparable
unit): the shares resting exactly d cents behind the touch, with the
holes a sparse book leaves and the coverage of the level-10 band, which
is where the file stops knowing.

Compute mode writes results/depth_profile_{ticker}.csv in long form
(one row per side x kind x index, integer sums beside the ratios).
Figure mode draws results/figures/depth_by_level.png (one panel per
name, bids mirrored left, asks right, gap annotated under each rank)
and results/figures/depth_by_cent.png (both names on one axis, log
scale, dashed where the band covers the cent less than 90% of the day).

Week 4 row C of the plan. No plotting import in compute mode.
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

from lob.lobster import read_messages, read_orderbook
from lob.micro import NS, book_shape, states

DATE = "2012-06-21"
SPAN = "34200000_57600000"
SERIES = {"AAPL": "#2a78d6", "MSFT": "#eb6834", "INTC": "#1baf7a",
          "AMZN": "#eda100", "GOOG": "#e87ba4"}
COVERAGE_SOLID = 0.9          # below this the cent-profile line goes dashed


def paths(data: Path, ticker: str, level: int) -> tuple[Path, Path]:
    stem = f"{ticker}_{DATE}_{SPAN}"
    return (data / f"{stem}_message_{level}.csv",
            data / f"{stem}_orderbook_{level}.csv")


def f6(x) -> str:
    return "" if x is None else f"{x:.6f}"


def compute(args) -> None:
    msg_path, book_path = paths(Path(args.data), args.ticker, args.level)
    for p in (msg_path, book_path):
        if not p.exists():
            raise SystemExit(f"no such file: {p}")
    msgs = read_messages(msg_path)
    ref = read_orderbook(book_path, args.level)
    lv, ct = book_shape(states(msgs, ref), args.level, args.max_cents)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"depth_profile_{args.ticker}.csv"
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["side", "kind", "index", "tw_size", "tw_gap_cents",
                    "share", "size_x_ns", "ns", "two_sided_ns", "note"])
        for side in ("bid", "ask"):
            L = lv[side]
            for j in range(1, args.level + 1):
                w.writerow([side, "level", j, f6(L.tw_size(j)),
                            f6(L.tw_gap_cents(j)), f6(L.exists_share(j)),
                            L.size_x_ns[j], L.exists_ns[j], L.two_sided_ns,
                            "size and gap conditional on the level existing; "
                            "share = time it existed / two-sided time"])
            C = ct[side]
            for d in range(0, args.max_cents + 1):
                w.writerow([side, "cent", d, f6(C.tw_size(d)), d,
                            f6(C.coverage(d)), C.size_x_ns[d], C.covered_ns[d],
                            C.two_sided_ns,
                            "size conditional on the level-10 band reaching "
                            "this cent; share = coverage"])
            w.writerow([side, "summary", "adjacent_gap_cents",
                        f6(L.tw_adjacent_gap_cents), "", "",
                        L.adjacent_gaps_x_ns, L.adjacent_gaps_n_x_ns,
                        L.two_sided_ns, "mean cents between consecutive "
                        "occupied levels, time-weighted"])
            w.writerow([side, "summary", "one_cent_gap_share", "", "",
                        f6(L.one_cent_gap_share), L.one_cent_gaps_x_ns,
                        L.adjacent_gaps_n_x_ns, L.two_sided_ns,
                        "share of adjacent-level gaps equal to one cent"])

    # ---- on screen
    print(f"BOOK SHAPE {args.ticker} {DATE} level {args.level}, "
          f"{len(msgs):,} messages, time-weighted over "
          f"{lv['bid'].two_sided_ns / NS:,.0f}s of two-sided book")
    for side in ("bid", "ask"):
        L = lv[side]
        print(f"  {side} by occupied level (shares, cents from the touch, "
              f"share of time the level exists):")
        print("    " + "  ".join(
            f"L{j}:{L.tw_size(j):,.0f}sh@{L.tw_gap_cents(j):.1f}c"
            f"({100 * L.exists_share(j):.0f}%)"
            for j in range(1, args.level + 1)))
        print(f"    adjacent gap {L.tw_adjacent_gap_cents:.2f} cents on "
              f"average; {100 * L.one_cent_gap_share:.1f}% of gaps are one "
              f"cent; level {args.level} sits "
              f"{L.tw_gap_cents(args.level):.1f} cents from the touch")
    print("  by cent from the best (bid | ask shares, band coverage):")
    for d in range(0, args.max_cents + 1):
        b, a = ct["bid"], ct["ask"]
        print(f"    {d:>2}c  {b.tw_size(d) or 0:>8,.0f} | {a.tw_size(d) or 0:>8,.0f}"
              f"   covered {100 * min(b.coverage(d) or 0, a.coverage(d) or 0):5.1f}%")
    print(f"\n  wrote {path}")


# -------------------------------------------------------------- figure
def _load(path: Path) -> dict:
    rows = list(csv.DictReader(open(path, newline="")))
    prof = {"level": {"bid": {}, "ask": {}}, "cent": {"bid": {}, "ask": {}},
            "summary": {"bid": {}, "ask": {}}}
    for r in rows:
        if r["kind"] == "summary":
            prof["summary"][r["side"]][r["index"]] = (
                float(r["tw_size"]) if r["tw_size"] else float(r["share"]))
            continue
        prof[r["kind"]][r["side"]][int(r["index"])] = (
            float(r["tw_size"]) if r["tw_size"] else None,
            float(r["tw_gap_cents"]) if r["tw_gap_cents"] else None,
            float(r["share"]) if r["share"] else None)
    return prof


def _style(ax):
    ax.grid(True, color="#e6e5e0", linewidth=0.8)
    ax.set_axisbelow(True)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    ax.spines["left"].set_color("#c3c2b7")
    ax.spines["bottom"].set_color("#c3c2b7")
    ax.tick_params(colors="#52514e")


def figure(args) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.ticker import FuncFormatter, LogLocator, NullFormatter

    out = Path(args.out)
    found = []
    for ticker in args.tickers:
        p = out / f"depth_profile_{ticker}.csv"
        if p.exists():
            found.append((ticker, _load(p)))
        else:
            print(f"  (no {p}; run --ticker {ticker} first)", file=sys.stderr)
    if not found:
        raise SystemExit("nothing to draw")
    plain = FuncFormatter(lambda v, _: f"{abs(v):,.0f}")

    # ---- by occupied level: one panel per name, bids left, asks right
    fig, axes = plt.subplots(1, len(found), figsize=(5.2 * len(found) + 0.8, 5.6),
                             squeeze=False)
    for ax, (ticker, prof) in zip(axes[0], found):
        colour = SERIES.get(ticker, "#52514e")
        ranks = sorted(prof["level"]["bid"])
        bids = [-(prof["level"]["bid"][j][0] or 0) for j in ranks]
        asks = [prof["level"]["ask"][j][0] or 0 for j in ranks]
        ax.barh(ranks, bids, color=colour, height=0.72, alpha=0.55,
                edgecolor="#fcfcfb", linewidth=1)
        ax.barh(ranks, asks, color=colour, height=0.72,
                edgecolor="#fcfcfb", linewidth=1)
        span = max(-min(bids), max(asks)) or 1
        for j in ranks:
            gb = prof["level"]["bid"][j][1]
            ga = prof["level"]["ask"][j][1]
            ax.text(-span * 0.02, j, f"{gb:.1f}c" if gb is not None else "",
                    ha="right", va="center", fontsize=8, color="#52514e")
            ax.text(span * 0.02, j, f"{ga:.1f}c" if ga is not None else "",
                    ha="left", va="center", fontsize=8, color="#52514e")
        ax.set_xlim(-span * 1.15, span * 1.15)
        ax.set_yticks(ranks)
        ax.set_yticklabels([f"L{j}" for j in ranks])
        ax.invert_yaxis()
        ax.xaxis.set_major_formatter(plain)
        ax.axvline(0, color="#c3c2b7", linewidth=1)
        gap = prof["summary"]["bid"].get("adjacent_gap_cents")
        one = prof["summary"]["bid"].get("one_cent_gap_share")
        ax.set_title(f"{ticker}: bids left, asks right; a level's mean "
                     f"distance from the touch is written beside it\n"
                     f"(bid side: adjacent levels {gap:.2f}c apart on "
                     f"average, {100 * one:.0f}% of gaps one cent)",
                     fontsize=9, loc="left", color="#0b0b0b")
        ax.set_xlabel("time-weighted shares at the level (when it exists)")
        _style(ax)
    fig.suptitle("Depth by occupied level, 2012-06-21, Nasdaq, LOBSTER "
                 "level 10", x=0.06, ha="left", fontsize=12, color="#0b0b0b")
    fig.subplots_adjust(top=0.84, bottom=0.11, left=0.08, right=0.98,
                        wspace=0.25)
    t1 = out / "figures" / "depth_by_level.png"
    t1.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(t1, dpi=150, facecolor="#fcfcfb")

    # ---- by cent from the best: both names on one axis, log scale
    fig, ax = plt.subplots(figsize=(10, 5.2))
    for ticker, prof in found:
        colour = SERIES.get(ticker, "#52514e")
        cents = sorted(prof["cent"]["bid"])
        # the two sides averaged (each side's own curve is in the CSV)
        ys, cov = [], []
        for d in cents:
            b, a = prof["cent"]["bid"][d], prof["cent"]["ask"][d]
            vals = [v[0] for v in (b, a) if v[0] is not None]
            ys.append(sum(vals) / len(vals) if vals else None)
            cov.append(min(v[2] or 0 for v in (b, a)))
        solid = [d for d, c in zip(cents, cov) if c >= COVERAGE_SOLID]
        dashed = [d for d, c in zip(cents, cov) if c < COVERAGE_SOLID]
        # log scale: a known zero cannot be drawn, so it is marked at the
        # floor of the axis with an open marker
        floor = 1.0
        def y_of(d):
            v = ys[cents.index(d)]
            return None if v is None else max(v, floor)
        ax.plot(solid, [y_of(d) for d in solid], color=colour, linewidth=2,
                marker="o", markersize=5, label=ticker)
        if dashed:
            joint = ([solid[-1]] if solid else []) + dashed
            ax.plot(joint, [y_of(d) for d in joint], color=colour,
                    linewidth=2, linestyle=(0, (3, 3)), marker="o",
                    markersize=5, markerfacecolor="#fcfcfb")
        zeros = [d for d in cents if ys[cents.index(d)] is not None
                 and ys[cents.index(d)] < floor]
        if zeros:
            ax.plot(zeros, [floor] * len(zeros), linestyle="none", marker="v",
                    markersize=6, color=colour, alpha=0.6)
    ax.set_yscale("log")
    ax.yaxis.set_major_locator(LogLocator(base=10, subs=(1.0, 2.0, 5.0)))
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:,.0f}"))
    ax.yaxis.set_minor_formatter(NullFormatter())
    top = max(max(prof["cent"]["bid"]) for _, prof in found)
    ax.set_xlim(-0.5, top + 0.5)
    ax.set_xticks(range(0, top + 1, 1 if top <= 25 else 5))
    ax.set_xlabel("cents behind the same-side best (0 = at the touch)")
    ax.set_ylabel("time-weighted shares resting at that cent\n"
                  "(bid and ask averaged; log scale)")
    _style(ax)
    if len(found) > 1:
        ax.legend(frameon=False, loc="upper right")
    else:
        ax.annotate(found[0][0], (0.99, 0.95), xycoords="axes fraction",
                    ha="right", color="#52514e", fontsize=9)
    fig.suptitle("Depth by cent from the touch, 2012-06-21, Nasdaq, LOBSTER "
                 "level 10", x=0.1, ha="left", fontsize=12, color="#0b0b0b")
    fig.text(0.1, 0.02,
             "Dashed with open markers: the level-10 band reaches that cent "
             f"less than {int(100 * COVERAGE_SOLID)}% of the day, so the mean "
             "is over less time.\nTriangles at the floor: cents known to be "
             "empty.", fontsize=8.5, color="#52514e", va="bottom")
    fig.subplots_adjust(top=0.9, bottom=0.22, left=0.1, right=0.97)
    t2 = out / "figures" / "depth_by_cent.png"
    fig.savefig(t2, dpi=150, facecolor="#fcfcfb")
    print(f"  drew {t1} and {t2} ({', '.join(t for t, _ in found)})")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ticker", default="AAPL")
    ap.add_argument("--level", type=int, default=10, choices=(1, 5, 10))
    ap.add_argument("--max-cents", type=int, default=20)
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
