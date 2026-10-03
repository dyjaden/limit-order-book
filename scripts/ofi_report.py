"""Order flow imbalance, event by event and on two clocks.

    python scripts/ofi_report.py --ticker AAPL                        # events, level 10
    python scripts/ofi_report.py --ticker AAPL --level 1              # the identity check
    python scripts/ofi_report.py --ticker AAPL --clock calendar --bucket 10
    python scripts/ofi_report.py --ticker AAPL --clock event --bucket 50
    python scripts/ofi_report.py --ticker AAPL --figure               # from the two CSVs

Three modes, deliberately separate, the Week 4 pattern. The event mode
reads a LOBSTER message/orderbook pair through the Day 2 loaders, takes
the best quotes after every message as the observation sequence,
computes Cont, Kukanov and Stoikov's e_n between consecutive two-sided
observations (``lob.ofi``), prints the day summary and writes
results/ofi_events_{ticker}_L{level}.csv with every event: the times
and twice-mids of both observations and e_n in shares. The clock mode
aggregates the same events into results/ofi_{ticker}_{clock}_{bucket}.csv,
one row per bucket with the integer sums and the derived cents and
basis points, and prints the distribution that is the point of the
clock: updates per bin on the calendar clock, bucket durations on the
event clock. The figure mode reads the ten-second and fifty-update CSVs
and draws results/figures/ofi_clocks_{ticker}.png; matplotlib is
imported only there.

Run at level 1 and at level 10, the nonzero events must be the same
sequence and the daily sum the same integer: a message that touches
only deeper levels contributes exactly zero, so the level filter cannot
change the answer, and the level-10 file simply carries more zero rows.
The bucket CSVs ship (they are aggregates); the per-event file does not
yet: at one row per message it is a transformed copy of the licensed
data, and whether any of it ships is Step 3's decision, taken in the
open.

Week 5 rows A and B of the plan.
"""
from __future__ import annotations

import argparse
import csv
import math
import sys
from pathlib import Path

from intraday_report import DATE, SERIES, paths
from lob.lobster import read_messages, read_orderbook
from lob.micro import CLOSE_NS, NS, OPEN_NS, hms
from lob.ofi import (BUCKET_COLUMNS, Touch, aggregate_calendar,
                     aggregate_events, day_summary, events, touches)

CALENDAR_DEFAULT = 10            # seconds per bin, the paper's interval
EVENT_DEFAULT = 50               # best-quote updates per bucket


def events_path(out: Path, ticker: str, level: int) -> Path:
    return out / f"ofi_events_{ticker}_L{level}.csv"


def bucket_path(out: Path, ticker: str, clock: str, bucket: int) -> Path:
    return out / f"ofi_{ticker}_{clock}_{bucket}.csv"


def load(args):
    msg_path, book_path = paths(Path(args.data), args.ticker, args.level)
    for p in (msg_path, book_path):
        if not p.exists():
            raise SystemExit(f"no such file: {p}")
    msgs = read_messages(msg_path)
    ref = read_orderbook(book_path, args.level)
    skipped: list[Touch] = []
    evs = list(events(touches(msgs, ref), skipped))
    return msgs, evs, skipped


# -------------------------------------------------------------- events
def write_events(evs, path: Path) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["time_prev_ns", "time_ns", "e", "mid2_prev", "mid2"])
        for ev in evs:
            w.writerow([ev.time_prev_ns, ev.time_ns, ev.e, ev.mid2_prev, ev.mid2])
            n += 1
    return n


def fmt_mid(mid2) -> str:
    return "n/a" if mid2 is None else f"{mid2 / 20_000:,.4f}"


def print_summary(ticker: str, level: int, n_rows: int, s: dict,
                  skipped: list[Touch]) -> None:
    one_sided = sum(1 for t in skipped if (t.bid is None) != (t.ask is None))
    empty = len(skipped) - one_sided
    print(f"OFI {ticker} {DATE} level {level}: {n_rows:,} observations, "
          f"{s['events']:,} events, {len(skipped):,} skipped "
          f"({one_sided:,} one-sided, {empty:,} empty)")
    if skipped:
        times = ", ".join(hms(t.time_ns) for t in skipped[:5])
        print(f"  skipped at: {times}{', ...' if len(skipped) > 5 else ''}")
    if not s["events"]:
        print("  no events")
        return
    print(f"  net flow, sum e_n:      {s['sum']:>14,} shares   "
          f"(buy {s['buy_shares']:,}, sell {s['sell_shares']:,})")
    print(f"  gross flow, sum |e_n|:  {s['abs_sum']:>14,} shares   "
          f"net over gross {100 * s['net_share']:.2f}%")
    print(f"  sign balance:           {s['positive']:>14,} positive, "
          f"{s['negative']:,} negative, {s['zero_events']:,} zero "
          f"({100 * s['zero_share']:.1f}%)")
    print(f"  largest |e_n|:          {s['largest_e']:>+14,} shares at "
          f"{hms(s['largest_time_ns'])} (a sanity row, not a statistic)")
    print(f"  mid, first to last obs: {fmt_mid(s['first_mid2'])} to "
          f"{fmt_mid(s['last_mid2'])} dollars, "
          f"{s['mid_change_cents']:+,.2f} cents, {s['mid_change_bps']:+,.1f} bps")


def compute_events(args) -> None:
    msgs, evs, skipped = load(args)
    s = day_summary(evs, len(skipped))
    print_summary(args.ticker, args.level, len(msgs), s, skipped)
    target = events_path(Path(args.out), args.ticker, args.level)
    n = write_events(evs, target)
    span = f"{hms(msgs[0].time_ns)} to {hms(msgs[-1].time_ns)}" if msgs else "empty"
    print(f"\n  wrote {target} ({n:,} rows, {span}; not whitelisted, see "
          f"the docstring)")


# -------------------------------------------------------------- clocks
def write_buckets(buckets, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    cols = ["bucket", "start_ns", "start_hms", "end_ns", "duration_ns",
            *BUCKET_COLUMNS, "dmid_cents", "dmid_bps"]
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(cols)
        for b in buckets:
            row = [b.index, b.start_ns, hms(b.start_ns), b.end_ns, b.duration_ns]
            row += [("" if getattr(b, c) is None else getattr(b, c)) for c in BUCKET_COLUMNS]
            row += ["" if b.dmid_cents is None else f"{b.dmid_cents:.4f}",
                    "" if b.dmid_bps is None else f"{b.dmid_bps:.6f}"]
            w.writerow(row)


def quantiles(xs, qs=(0.0, 0.1, 0.5, 0.9, 1.0)):
    xs = sorted(xs)
    if not xs:
        return [None] * len(qs)
    return [xs[min(len(xs) - 1, int(q * (len(xs) - 1) + 0.5))] for q in qs]


def fmt_s(ns) -> str:
    """A duration in nanoseconds, printed in the unit it deserves."""
    if ns is None:
        return "n/a"
    if ns < 1_000_000:
        return f"{ns / 1_000:,.0f} us"
    if ns < NS:
        return f"{ns / 1_000_000:,.1f} ms"
    return f"{ns / NS:,.2f} s"


def compute_clock(args) -> None:
    msgs, evs, skipped = load(args)
    s = day_summary(evs, len(skipped))
    out = Path(args.out)
    if args.clock == "calendar":
        width_ns = args.bucket * NS
        buckets = aggregate_calendar(evs, width_ns)
        inside = sum(b.n_events for b in buckets)
        upd = [b.n_updates for b in buckets]
        q = quantiles(upd)
        empty = sum(1 for u in upd if u == 0)
        print(f"OFI {args.ticker} {DATE} level {args.level}, calendar clock, "
              f"{args.bucket}-second bins: {len(buckets):,} bins, "
              f"{inside:,} of {len(evs):,} events inside "
              f"[{hms(OPEN_NS)}, {hms(CLOSE_NS)})")
        print(f"  best-quote updates per bin: min {q[0]:,}, p10 {q[1]:,}, "
              f"median {q[2]:,}, p90 {q[3]:,}, max {q[4]:,}; "
              f"{empty:,} bins with no update ({100 * empty / len(buckets):.1f}%)")
        busiest = max(buckets, key=lambda b: b.n_updates)
        print(f"  busiest bin {hms(busiest.start_ns)}: {busiest.n_updates:,} updates, "
              f"OFI {busiest.ofi:+,} shares, mid {busiest.dmid_cents:+.2f} cents")
    else:
        buckets = aggregate_events(evs, args.bucket)
        inside = sum(b.n_events for b in buckets)
        dropped_updates = (s["events"] - s["zero_events"]) - args.bucket * len(buckets)
        durs = [b.duration_ns for b in buckets]
        q = quantiles(durs)
        print(f"OFI {args.ticker} {DATE} level {args.level}, event clock, "
              f"{args.bucket}-update buckets: {len(buckets):,} buckets, "
              f"{inside:,} of {len(evs):,} events inside; the remainder "
              f"({len(evs) - inside:,} events, {dropped_updates:,} updates) dropped")
        span = math.log10(q[4] / q[0]) if q[0] else float("inf")
        print(f"  bucket duration: min {fmt_s(q[0])}, p10 {fmt_s(q[1])}, "
              f"median {fmt_s(q[2])}, p90 {fmt_s(q[3])}, max {fmt_s(q[4])}; "
              f"{span:.1f} orders of magnitude")
        covered = sum(durs)
        print(f"  the buckets tile {covered / NS:,.1f} s of the "
              f"{(evs[-1].time_ns - evs[0].time_prev_ns) / NS:,.1f} s between the "
              f"first and last observation")
    total_ofi = sum(b.ofi for b in buckets)
    total_dmid = sum(b.dmid_cents for b in buckets if b.dmid_cents is not None)
    print(f"  over the buckets: OFI {total_ofi:+,} shares (day {s['sum']:+,}), "
          f"mid {total_dmid:+,.2f} cents (first to last observation "
          f"{s['mid_change_cents']:+,.2f})")
    # no hit rate, correlation or slope is printed here: the registry that
    # must log such a number before it is read does not exist until Step 3
    target = bucket_path(out, args.ticker, args.clock, args.bucket)
    write_buckets(buckets, target)
    print(f"\n  wrote {target} ({len(buckets):,} rows)")


# -------------------------------------------------------------- figure
def read_buckets_csv(path: Path) -> list[dict]:
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def figure(args) -> None:
    import matplotlib
    matplotlib.use("Agg")                     # headless: CI and shells alike
    import matplotlib.pyplot as plt
    from matplotlib.ticker import FuncFormatter, LogLocator, NullFormatter

    out = Path(args.out)
    cal_path = bucket_path(out, args.ticker, "calendar", CALENDAR_DEFAULT)
    ev_path = bucket_path(out, args.ticker, "event", EVENT_DEFAULT)
    for p in (cal_path, ev_path):
        if not p.exists():
            raise SystemExit(f"no {p}; run the clock mode first")
    cal = read_buckets_csv(cal_path)
    evb = read_buckets_csv(ev_path)
    colour = SERIES.get(args.ticker, "#52514e")
    ink, muted, grid = "#0b0b0b", "#52514e", "#e6e5e0"

    fig, (ax_n, ax_ofi, ax_dur) = plt.subplots(
        3, 1, figsize=(10, 8.4), sharex=True, gridspec_kw={"hspace": 0.14})

    # calendar clock: updates per bin, then the flow per bin
    # 2,340 bins across about 1,300 pixels is under a pixel a bin, so the
    # series is drawn as a stepped area from the baseline, not as bars that
    # would alias in and out
    x = [int(r["start_ns"]) / NS / 3600 for r in cal]
    x_step = x + [int(cal[-1]["end_ns"]) / NS / 3600]
    upd = [int(r["n_updates"]) for r in cal]
    ofi = [int(r["ofi"]) for r in cal]
    ax_n.fill_between(x_step, 0, upd + upd[-1:], step="post", color=colour,
                      linewidth=0)
    ax_n.set_ylabel(f"best-quote updates\nper {CALENDAR_DEFAULT}-second bin")
    ax_n.set_ylim(bottom=0)
    ax_ofi.fill_between(x_step, 0, ofi + ofi[-1:], step="post", color=colour,
                        linewidth=0)
    ax_ofi.axhline(0, color="#c3c2b7", linewidth=1)
    ax_ofi.set_ylabel(f"OFI, shares\nper {CALENDAR_DEFAULT}-second bin")
    lim = max(abs(v) for v in ofi) * 1.05
    ax_ofi.set_ylim(-lim, lim)
    ax_ofi.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:+,.0f}" if v else "0"))

    # event clock: the duration of each bucket, at the time it closed
    xe = [int(r["end_ns"]) / NS / 3600 for r in evb]
    dur = [int(r["duration_ns"]) / NS for r in evb]
    ax_dur.scatter(xe, dur, s=6, color=colour, linewidths=0, alpha=0.8)
    ax_dur.set_yscale("log")
    ax_dur.yaxis.set_major_locator(LogLocator(base=10, subs=(1.0,)))
    ax_dur.yaxis.set_major_formatter(FuncFormatter(
        lambda v, _: f"{v:g} s" if v >= 1 else f"{1000 * v:g} ms"))
    ax_dur.yaxis.set_minor_formatter(NullFormatter())
    ax_dur.set_ylabel(f"seconds per {EVENT_DEFAULT}-update bucket\n(log scale)")
    ax_dur.set_xlabel(f"time of day (ET), {DATE}, Nasdaq, LOBSTER level 10")
    ax_dur.set_xlim(9.5, 16.0)
    ax_dur.set_xticks([9.5, 10, 11, 12, 13, 14, 15, 16])
    ax_dur.xaxis.set_major_formatter(FuncFormatter(
        lambda h, _: f"{int(h):02d}:{int(round((h % 1) * 60)):02d}"))
    for ax in (ax_n, ax_ofi, ax_dur):
        ax.grid(True, color=grid, linewidth=0.8)
        ax.set_axisbelow(True)
        for side in ("top", "right"):
            ax.spines[side].set_visible(False)
        ax.spines["left"].set_color("#c3c2b7")
        ax.spines["bottom"].set_color("#c3c2b7")
        ax.tick_params(colors=muted)
        ax.yaxis.label.set_color(muted)
    ax_dur.xaxis.label.set_color(muted)
    # one selective label per panel: the extreme, named
    i = max(range(len(upd)), key=upd.__getitem__)
    ax_n.annotate(f"{upd[i]:,} updates", (x[i] + CALENDAR_DEFAULT / 7200, upd[i]), xytext=(8, -2),
                  textcoords="offset points", color=muted, fontsize=9, va="top")
    j = max(range(len(dur)), key=dur.__getitem__)
    ax_dur.annotate(f"{dur[j]:,.0f} s", (xe[j], dur[j]), xytext=(8, 0),
                    textcoords="offset points", color=muted, fontsize=9, va="center")
    k = min(range(len(dur)), key=dur.__getitem__)
    ax_dur.annotate(f"{1000 * dur[k]:,.0f} ms", (xe[k], dur[k]), xytext=(8, 0),
                    textcoords="offset points", color=muted, fontsize=9, va="center")
    fig.suptitle(f"{args.ticker}: the same day on two clocks", x=0.125, ha="left",
                 fontsize=12, color=ink)
    fig.text(0.125, 0.935,
             f"calendar bins hold from {min(upd):,} to {max(upd):,} updates; "
             f"{EVENT_DEFAULT}-update buckets last from {1000 * min(dur):,.0f} ms "
             f"to {max(dur):,.0f} s",
             fontsize=9.5, color=muted)
    fig.subplots_adjust(top=0.91, bottom=0.07, left=0.11, right=0.95)
    target = out / "figures" / f"ofi_clocks_{args.ticker}.png"
    target.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(target, dpi=150, facecolor="#fcfcfb")
    print(f"  drew {target}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ticker", default="AAPL")
    ap.add_argument("--level", type=int, default=10, choices=(1, 5, 10))
    ap.add_argument("--data", default="data/lobster")
    ap.add_argument("--out", default="results")
    ap.add_argument("--clock", choices=("calendar", "event"),
                    help="aggregate: fixed seconds, or a bucket every N updates")
    ap.add_argument("--bucket", type=int,
                    help=f"bin width in seconds (calendar, default {CALENDAR_DEFAULT}) "
                         f"or updates per bucket (event, default {EVENT_DEFAULT})")
    ap.add_argument("--figure", action="store_true",
                    help="draw the two-clock figure from the CSVs already written")
    args = ap.parse_args()
    if args.figure:
        figure(args)
    elif args.clock:
        if args.bucket is None:
            args.bucket = CALENDAR_DEFAULT if args.clock == "calendar" else EVENT_DEFAULT
        if args.bucket <= 0:
            raise SystemExit("--bucket must be positive")
        compute_clock(args)
    else:
        compute_events(args)


if __name__ == "__main__":
    main()
