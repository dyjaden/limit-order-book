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
from lob.replay import LobsterReplayer

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
    args = ap.parse_args()

    msg_path, book_path = paths(Path(args.data), args.level)
    print(f"REPLAY {TICKER} {DATE} level {args.level}")
    msgs = read_messages(msg_path)
    ref = read_orderbook(book_path, args.level)
    print(f"  {len(msgs):,} messages, {len(ref):,} reference rows")

    book = Book()
    rep = LobsterReplayer(book)
    rep.seed(ref[0])
    t0 = time.perf_counter()
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

    if stats.anomalies:
        raise SystemExit("anomalies present; do not quote this replay "
                         "until each has a name")


if __name__ == "__main__":
    main()
