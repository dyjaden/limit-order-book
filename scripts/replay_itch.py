"""Replay a TotalView-ITCH 5.0 day full-depth, from an empty book.

    python scripts/replay_itch.py                          # synthetic day
    python scripts/replay_itch.py --file 12302019.NASDAQ_ITCH50.gz \
        --symbol AAPL                                      # a real day

Part one of the Day 2 pre-commitment's day in court: on unfiltered data
the repair machinery must never fire. The replayer starts from an EMPTY
book (an ITCH file begins before the open, so empty is the truth, not
an assumption), applies every message, and keeps four counters --
unknown ids, crossing adds, front-of-queue violations, anomalies --
that the prediction requires to be ZERO. The exit code enforces it.

Real files arrive gzipped at 2-6 GB and are streamed compressed: the
gzip module decompresses in memory as bytes are read, and the expanded
file is never written anywhere.

One book holds one symbol. For a real (multi-symbol) day, --symbol is
how the parser's directory filter narrows the stream; replaying a
many-symbol stream into one book is refused before it can cross.
"""
from __future__ import annotations

import argparse
import gzip
import io
import sys
import time
from pathlib import Path

from lob.book import Side
from lob.itch import ItchParser
from lob.itch_replay import BOOK_KINDS, ItchReplayer


def open_stream(path: Path) -> io.BufferedReader:
    """The day's bytes, streamed; .gz decompressed on the fly."""
    if path.suffix == ".gz":
        return io.BufferedReader(gzip.open(path, "rb"), 1 << 20)
    return open(path, "rb", buffering=1 << 20)


def guarded(messages, progress_every: int):
    """Refuse to mix symbols into one book; narrate long runs."""
    locate_seen = None
    n = 0
    t0 = time.perf_counter()
    for m in messages:
        if m.kind in BOOK_KINDS:
            if locate_seen is None:
                locate_seen = m.stock_locate
            elif m.stock_locate != locate_seen:
                raise SystemExit(
                    f"this file carries more than one symbol (locates "
                    f"{locate_seen} and {m.stock_locate} both touch the "
                    f"book). One book holds one symbol: pass --symbol.")
        n += 1
        if progress_every and n % progress_every == 0:
            dt = time.perf_counter() - t0
            print(f"  ... {n:,} messages, {n / dt:,.0f}/s",
                  file=sys.stderr, flush=True)
        yield m


def hms(ns: int) -> str:
    s = ns // 1_000_000_000
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


def close_book_report(book) -> list[str]:
    lines = []
    bid, ask = book.best_bid(), book.best_ask()
    if bid is not None and ask is not None:
        lines.append(f"  close: bid {bid} (${bid / 10_000:,.4f})  "
                     f"ask {ask} (${ask / 10_000:,.4f})  "
                     f"spread {book.spread()} ticks")
    else:
        lines.append(f"  close: one-sided or empty (bid {bid}, ask {ask})")
    for side in (Side.BID, Side.ASK):
        d = ", ".join(f"{p}x{q}" for p, q in book.depth(side, 5))
        lines.append(f"  {side.value:>4} top 5: {d or '(empty)'}")
    lines.append(f"  live orders at close: {len(book):,}")
    return lines


def verify_close(book) -> list[tuple[str, bool]]:
    """Invariants at the close, via the public API only: verify, don't
    trust the structure that was supposed to guarantee them."""
    bid, ask = book.best_bid(), book.best_ask()
    uncrossed = bid is None or ask is None or bid < ask
    positive = True
    reconciled = True
    for side in (Side.BID, Side.ASK):
        prices = book.prices(side)
        totals = dict(book.depth(side, len(prices))) if prices else {}
        for p in prices:
            member_sum = 0
            for oid in book.orders_at(side, p):
                q = book.order(oid).qty
                if q <= 0:
                    positive = False
                member_sum += q
            if member_sum != totals.get(p):
                reconciled = False
    return [("book uncrossed at close", uncrossed),
            ("every resting quantity positive", positive),
            ("level totals reconcile with member orders", reconciled)]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default="data/_fake/fake_itch5.bin")
    ap.add_argument("--symbol", default=None,
                    help="replay this stock only (real, multi-symbol days)")
    ap.add_argument("--progress-every", type=int, default=10_000_000,
                    help="stderr heartbeat; 0 silences it")
    args = ap.parse_args()

    path = Path(args.file)
    if not path.exists():
        raise SystemExit(f"no such file: {path} (the synthetic day comes "
                         f"from scripts/make_fake_itch.py)")
    symbols = {args.symbol} if args.symbol else None

    parser = ItchParser(symbols)
    replayer = ItchReplayer()
    t0 = time.perf_counter()
    with open_stream(path) as stream:
        stats = replayer.run(
            guarded(parser.parse(stream), args.progress_every))
    dt = time.perf_counter() - t0

    print(f"ITCH 5.0 full-depth replay -- {path.name}"
          + (f", symbol {args.symbol}" if args.symbol else ""))
    print(f"  {parser.messages_read:,} messages read in {dt:,.1f}s "
          f"({parser.messages_read / dt:,.0f}/s end-to-end), "
          f"{stats.applied:,} applied to the book")
    print(f"  types: " + "  ".join(
        f"{k}:{v:,}" for k, v in sorted(stats.counts.items())))
    if parser.unknown:
        print(f"  unknown types skipped by length: "
              + "  ".join(f"{k}:{v:,}" for k, v in
                          sorted(parser.unknown.items())))
    if symbols:
        print(f"  skipped by symbol filter: {parser.skipped_by_filter:,}")
    if stats.hourly:
        print(f"  applied by hour: " + "  ".join(
            f"{h:02d}h:{n:,}" for h, n in sorted(stats.hourly.items())))
    print(f"  max live orders: {stats.max_live:,}; "
          f"full fills: {stats.full_fills:,}")
    print()
    for line in close_book_report(replayer.book):
        print(line)
    print()
    checks = verify_close(replayer.book)
    for name, ok in checks:
        print(f"  [{'ok' if ok else 'FAIL'}] {name}")
    print()
    print(f"  the counters the prediction stakes itself on:")
    print(f"    unknown-id operations : {stats.unknown_refs:>8,}   "
          f"(dark liquidity cannot exist in a full log)")
    print(f"    crossing adds         : {stats.crossing_adds:>8,}   "
          f"(the exchange matches before it publishes)")
    print(f"    front-of-queue breaks : {stats.front_violations:>8,}   "
          f"(price-time priority, finally assertable)")
    print(f"    other anomalies       : {stats.anomalies_total:>8,}   "
          f"(refused or impossible operations)")
    for a in stats.anomalies:
        print(f"      {a.render()}")
    if stats.anomalies_total > len(stats.anomalies):
        print(f"      ... and "
              f"{stats.anomalies_total - len(stats.anomalies):,} more")
    print()
    invariants_ok = all(ok for _, ok in checks)
    if stats.clean and invariants_ok:
        print("  VERDICT: all counters zero from an empty book. On this "
              "file the Day 2 repair")
        print("  machinery had nothing to do, which is exactly what "
              "results/lobster_validation.md")
        print("  predicted for unfiltered data.")
    else:
        print("  VERDICT: FALSIFIED on this file. The pre-commitment "
              "allows no information-limit")
        print("  story here: the fault is in our parser, our replayer, "
              "or our book. Hunt it.")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
