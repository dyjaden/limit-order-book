"""Replay a LOBSTER sample day through the book.

    python scripts/replay_lobster.py --level 1
    python scripts/replay_lobster.py --level 10

Seeds the book from the reference's first row, applies every message
after it, and reports everything: per-type counts, dark-liquidity
operations (pre-window and below-K ids the file never introduced),
anomalies with context, and a throughput number. The messages/sec here
becomes the Python baseline the C++ twin is later measured against.

This script does not judge correctness; that is the validator's job
(--validate arrives with the next step). It proves the day REPLAYS:
every message handled, nothing unexplained.
"""
from __future__ import annotations

import argparse
import time
from pathlib import Path

from lob.book import Book, Side
from lob.lobster import read_messages, read_orderbook
from lob.replay import LobsterReplayer, run_validated

TICKER = "AAPL"
DATE = "2012-06-21"
SPAN = "34200000_57600000"


def paths(data: Path, level: int) -> tuple[Path, Path]:
    stem = f"{TICKER}_{DATE}_{SPAN}"
    return (data / f"{stem}_message_{level}.csv",
            data / f"{stem}_orderbook_{level}.csv")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data/lobster")
    ap.add_argument("--level", type=int, default=10, choices=(1, 5, 10))
    ap.add_argument("--validate", action="store_true",
                    help="grade the book against the reference after "
                         "every message")
    args = ap.parse_args()

    msg_path, book_path = paths(Path(args.data), args.level)
    print(f"REPLAY {TICKER} {DATE} level {args.level}"
          f"{' -- VALIDATED' if args.validate else ''}")
    msgs = read_messages(msg_path)
    ref = read_orderbook(book_path, args.level)
    print(f"  {len(msgs):,} messages, {len(ref):,} reference rows")

    book = Book()
    rep = LobsterReplayer(book, levels=args.level)
    rep.seed(ref[0])
    t0 = time.perf_counter()
    if args.validate:
        v = run_validated(rep, msgs, ref, args.level)
        stats = rep.stats
    else:
        v = None
        stats = rep.run(msgs, start_index=1)
    elapsed = time.perf_counter() - t0

    print(f"  seeded {stats.seeded_orders} synthetic level-orders from "
          f"the reference's first row")
    print(f"  applied {stats.applied:,} of {len(msgs) - 1:,} messages:")
    for name, n in sorted(stats.by_type.items(), key=lambda kv: -kv[1]):
        print(f"    {name:<12} {n:>9,}")
    print(f"  dark-liquidity ops (unknown ids, applied at their price): "
          f"{stats.dark_ops:,}")
    print(f"  unresolved: {stats.unresolved:,} ops, "
          f"{stats.unresolved_shares:,} shares (counted, not invented)")
    print(f"  ghost evictions (proven stale by a later message): "
          f"{stats.ghost_evictions:,} orders, {stats.ghost_shares:,} shares")
    print(f"  anomalies: {len(stats.anomalies):,}")
    for i, m, err in stats.anomalies[:5]:
        print(f"    msg {i}: {m.kind.name} id {m.order_id} @ {m.price} "
              f"x{m.size}: {err}")
    bid = book.best_bid()
    ask = book.best_ask()
    ref_close = ref[-1]
    ref_bid = ref_close.bids[0][0] if ref_close.bids else None
    ref_ask = ref_close.asks[0][0] if ref_close.asks else None
    print(f"  book at close: bid {bid} ask {ask} "
          f"({len(book):,} live orders)")
    print(f"  reference close:  bid {ref_bid} ask {ref_ask}   "
          f"(top-of-book {'MATCHES' if (bid, ask) == (ref_bid, ref_ask) else 'DIFFERS'})")
    print(f"  {elapsed:.1f}s, ~{(len(msgs) - 1) / elapsed:,.0f} msg/s "
          f"(the Python baseline)")

    if v is not None:
        print(f"\nVALIDATION -- graded against the reference after every "
              f"message")
        print(f"  matched {v.matched:,} of {v.total:,} rows "
              f"({v.match_rate:.4%})")
        print(f"  divergence streaks: {v.streaks:,}   longest: "
              f"{v.longest_streak:,} rows   first at msg "
              f"{v.first_divergence}")
        print(f"  exact-match rate by compared depth:")
        for j, n in sorted(v.matched_at_depth.items()):
            bar = "#" * int(50 * n / v.total)
            print(f"    top-{j:<2} {n / v.total:>9.4%}  {bar}")
        print(f"  by hour (rows / top-1 exact / all-{args.level} exact):")
        for h, (n, full, top1) in sorted(v.hourly.items()):
            print(f"    {h:02d}:00  {n:>7,}   top-1 {top1 / n:>8.2%}   "
                  f"full {full / n:>8.2%}")
        if v.divergence_classes:
            total_div = sum(v.divergence_classes.values())
            print(f"  every divergence classified against the file's "
                  f"information limit ({total_div:,} rows):")
            for name, n in sorted(v.divergence_classes.items(),
                                  key=lambda kv: -kv[1]):
                print(f"    {name:<14} {n:>9,}  ({n / total_div:.1%})")
        if v.first_bad_depth:
            print(f"  divergences first appearing at depth: "
                  + "  ".join(f"J{j}:{n:,}" for j, n in
                              sorted(v.first_bad_depth.items())))
        if stats.delete_size_disagreements:
            print(f"  WARNING known-delete size disagreements: "
                  f"{stats.delete_size_disagreements:,}")
        else:
            print(f"  known-delete size disagreements: 0 "
                  f"(per-order handling is consistent)")
        for m in v.first_mismatches:
            print("  " + m.render().replace("\n", "\n  "))

    if stats.anomalies:
        raise SystemExit("anomalies present; do not quote this replay "
                         "until each has a name")


if __name__ == "__main__":
    main()
