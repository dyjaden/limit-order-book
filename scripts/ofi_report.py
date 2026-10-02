"""Order flow imbalance, event by event, from the touch.

    python scripts/ofi_report.py --ticker AAPL              # level 10
    python scripts/ofi_report.py --ticker AAPL --level 1    # the identity check

Reads a LOBSTER message/orderbook pair through the Day 2 loaders, takes
the best quotes after every message as the observation sequence, and
computes Cont, Kukanov and Stoikov's e_n between consecutive two-sided
observations (``lob.ofi``). Prints the day summary and writes
results/ofi_events_{ticker}_L{level}.csv with every event: the time in
integer nanoseconds, e_n in shares, and the twice-mid before and after,
so the Step 2 aggregations can be re-derived from the file alone. It
draws nothing and imports no plotting library.

Run at level 1 and at level 10, the nonzero events must be the same
sequence and the daily sum the same integer: a message that touches
only deeper levels contributes exactly zero, so the level filter cannot
change the answer, and the level-10 file simply carries more zero rows.
The summary prints the numbers that comparison needs. The events file
is written under results/ but is NOT whitelisted in .gitignore yet: at
one row per message it is a transformed copy of the licensed data, and
whether any of it ships is Step 3's decision, taken in the open.

Week 5 row A of the plan.
"""
from __future__ import annotations

import argparse
import csv
from pathlib import Path

from intraday_report import DATE, paths
from lob.lobster import read_messages, read_orderbook
from lob.micro import NS, hms
from lob.ofi import Touch, day_summary, events, touches


def events_path(out: Path, ticker: str, level: int) -> Path:
    return out / f"ofi_events_{ticker}_L{level}.csv"


def write_events(evs, path: Path) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["time_ns", "e", "mid2_prev", "mid2"])
        for ev in evs:
            w.writerow([ev.time_ns, ev.e, ev.mid2_prev, ev.mid2])
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


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ticker", default="AAPL")
    ap.add_argument("--level", type=int, default=10, choices=(1, 5, 10))
    ap.add_argument("--data", default="data/lobster")
    ap.add_argument("--out", default="results")
    args = ap.parse_args()

    msg_path, book_path = paths(Path(args.data), args.ticker, args.level)
    for p in (msg_path, book_path):
        if not p.exists():
            raise SystemExit(f"no such file: {p}")
    msgs = read_messages(msg_path)
    ref = read_orderbook(book_path, args.level)
    skipped: list[Touch] = []
    evs = list(events(touches(msgs, ref), skipped))
    s = day_summary(evs, len(skipped))
    print_summary(args.ticker, args.level, len(msgs), s, skipped)
    target = events_path(Path(args.out), args.ticker, args.level)
    n = write_events(evs, target)
    span = f"{hms(msgs[0].time_ns)} to {hms(msgs[-1].time_ns)}" if msgs else "empty"
    print(f"\n  wrote {target} ({n:,} rows, {span}; not whitelisted, see "
          f"the docstring)")


if __name__ == "__main__":
    main()
