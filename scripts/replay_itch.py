"""Replay a TotalView-ITCH 5.0 day full-depth, from an empty book.

    python scripts/replay_itch.py                          # synthetic day
    python scripts/replay_itch.py --file 12302019.NASDAQ_ITCH50.gz \
        --symbol AAPL                                      # a real day
    python scripts/replay_itch.py --bench --write results/itch_baseline.md

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

--bench is Week 3's throughput baseline, the number Week 9's C++ twin
gets compared against. Three stages are timed SEPARATELY so the
comparison cannot blur them: parse only (bytes to messages, the
symbol filter's work included), replay only (pre-parsed messages
through the book, timed in chunks so a real day never has to sit in
memory whole), and end to end (the path this script normally runs).
Each stage is run --repeats times and the MEDIAN is reported with its
spread. Whole-run throughput is all that is claimed; per-operation
latency percentiles are Week 9's discipline and are deliberately not
quoted here. --write splices the result into a marked block of the
results file so the numbers on the page are the numbers the machine
produced, with the machine named.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import gzip
import io
import itertools
import platform
import statistics
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from lob.book import Side
from lob.itch import ItchParser
from lob.itch_replay import BOOK_KINDS, ItchReplayer, ItchReplayStats

BENCH_BEGIN = "<!-- bench:begin -->"
BENCH_END = "<!-- bench:end -->"
_CHUNK = 250_000            # replay-only buffer; bounds memory on real days


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


# ------------------------------------------------------------ the baseline
def cpu_name() -> str:
    """The processor's marketing name, best effort per platform;
    platform.processor() alone says 'Intel64 Family 6' on Windows and
    nothing at all on many Linuxes, which names no machine."""
    try:
        if sys.platform == "win32":
            import winreg
            key = winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"HARDWARE\DESCRIPTION\System\CentralProcessor\0")
            return winreg.QueryValueEx(key, "ProcessorNameString")[0].strip()
        if sys.platform == "darwin":
            return subprocess.check_output(
                ["sysctl", "-n", "machdep.cpu.brand_string"],
                text=True).strip()
        with open("/proc/cpuinfo") as f:
            for line in f:
                if line.lower().startswith("model name"):
                    return line.split(":", 1)[1].strip()
    except Exception:                        # noqa: BLE001 -- best effort
        pass
    return platform.processor() or platform.machine() or "unknown CPU"


def machine_stanza() -> str:
    return (f"{cpu_name()}; {platform.system()} {platform.release()} "
            f"({platform.machine()}); Python {platform.python_version()}")


def _single_symbol(buf: list) -> None:
    """The guarded() rule, applied to a chunk OUTSIDE the timed region:
    one book holds one symbol, benchmark or not."""
    locates = {m.stock_locate for m in buf if m.kind in BOOK_KINDS}
    if len(locates) > 1:
        raise SystemExit(f"this file carries more than one symbol (locates "
                         f"{sorted(locates)[:2]} both touch the book). "
                         f"One book holds one symbol: pass --symbol.")


@dataclass
class StageRun:
    """One timed pass of one stage: the message count the rate is over,
    the seconds it took, and (for stages that replay) the counters."""
    messages: int
    seconds: float
    stats: ItchReplayStats | None = None

    @property
    def rate(self) -> float:
        return self.messages / self.seconds if self.seconds > 0 else 0.0


def bench_parse_only(path: Path, symbols, limit) -> StageRun:
    """Bytes to messages, nothing applied. The count is every framed
    message the stream carried, filtered ones included: that is the rate
    a feed handler has to sustain, and the symbol filter is part of the
    parser's work."""
    parser = ItchParser(symbols)
    t0 = time.perf_counter()
    with open_stream(path) as stream:
        for _ in parser.parse(stream, limit):
            pass
    return StageRun(parser.messages_read, time.perf_counter() - t0)


def bench_replay_only(path: Path, symbols, limit) -> StageRun:
    """Pre-parsed messages through the book. Parsing happens in chunks
    with the clock STOPPED, so only the book's work is on the meter and
    a multi-gigabyte day never has to be materialised in memory. The
    count is the messages fed to the replayer (post-filter)."""
    parser = ItchParser(symbols)
    replayer = ItchReplayer()
    fed, secs = 0, 0.0
    with open_stream(path) as stream:
        it = parser.parse(stream, limit)
        while True:
            buf = list(itertools.islice(it, _CHUNK))
            if not buf:
                break
            _single_symbol(buf)
            t0 = time.perf_counter()
            replayer.run(buf)
            secs += time.perf_counter() - t0
            fed += len(buf)
    return StageRun(fed, secs, replayer.stats)


def bench_end_to_end(path: Path, symbols, limit) -> StageRun:
    """The script's own path, parse and guard and replay in one stream;
    the count is every message read, as for parse-only."""
    parser = ItchParser(symbols)
    replayer = ItchReplayer()
    t0 = time.perf_counter()
    with open_stream(path) as stream:
        stats = replayer.run(guarded(parser.parse(stream, limit), 0))
    return StageRun(parser.messages_read, time.perf_counter() - t0, stats)


STAGES = (("parse only", bench_parse_only),
          ("replay only (pre-parsed)", bench_replay_only),
          ("end to end", bench_end_to_end))


def run_bench(path: Path, symbols, limit: int | None, repeats: int,
              say=print) -> dict:
    """Every stage `repeats` times; medians reported, spread kept. The
    counters from the last end-to-end pass ride along, because a fast
    replay of a broken book would be a number about nothing."""
    results = {}
    last: StageRun | None = None
    for name, stage in STAGES:
        runs: list[StageRun] = []
        for i in range(repeats):
            run = stage(path, symbols, limit)
            runs.append(run)
            say(f"  {name:<26} run {i + 1}/{repeats}: {run.messages:,} "
                f"messages in {run.seconds:,.2f}s  ({run.rate:,.0f}/s)")
        results[name] = {
            "messages": runs[-1].messages,
            "seconds_median": statistics.median(r.seconds for r in runs),
            "rate_median": statistics.median(r.rate for r in runs),
            "rate_min": min(r.rate for r in runs),
            "rate_max": max(r.rate for r in runs),
        }
        last = runs[-1]
    stats = last.stats
    return {
        "file": path.name,
        "bytes": path.stat().st_size,
        "symbol": next(iter(symbols)) if symbols else None,
        "limit": limit,
        "repeats": repeats,
        "machine": machine_stanza(),
        "date": _dt.date.today().isoformat(),
        "applied": stats.applied,
        "counters": {
            "unknown_refs": stats.unknown_refs,
            "crossing_adds": stats.crossing_adds,
            "front_violations": stats.front_violations,
            "anomalies": stats.anomalies_total,
        },
        "clean": stats.clean,
        "stages": results,
    }


def render_bench(b: dict) -> str:
    """The block the results file carries between the bench markers."""
    def k(x: float) -> str:
        return f"{x / 1000:,.1f}k"

    scope = ("the whole file" if not b["limit"]
             else f"the first {b['limit']:,} messages")
    sym = f", symbol {b['symbol']}" if b["symbol"] else ""
    lines = [
        BENCH_BEGIN,
        f"Measured {b['date']} on {b['machine']}.",
        f"File `{b['file']}` ({b['bytes'] / 1e6:,.1f} MB{sym}), {scope}; "
        f"median of {b['repeats']} runs per stage, spread in the last "
        f"column. Whole-run throughput only; no latency percentiles "
        f"(Week 9).",
        "",
        "| stage | messages | seconds (median) | msg/s (median) | spread |",
        "|---|---|---|---|---|",
    ]
    for name, r in b["stages"].items():
        lines.append(f"| {name} | {r['messages']:,} | "
                     f"{r['seconds_median']:,.2f} | "
                     f"{r['rate_median']:,.0f} | "
                     f"{k(r['rate_min'])} to {k(r['rate_max'])} |")
    c = b["counters"]
    lines += [
        "",
        f"Book-touching messages applied: {b['applied']:,}. Counters after "
        f"the benchmark replay: unknown ids {c['unknown_refs']}, crossing "
        f"adds {c['crossing_adds']}, front-of-queue breaks "
        f"{c['front_violations']}, anomalies {c['anomalies']}"
        + (" (clean)." if b["clean"] else
           " (NOT CLEAN: this number is about a broken replay)."),
        BENCH_END,
    ]
    return "\n".join(lines)


def splice_block(text: str, block: str) -> str:
    """Replace whatever sits between the bench markers with `block`
    (markers included), or append the block when the file has none.
    The prose around the markers is never touched."""
    start, end = text.find(BENCH_BEGIN), text.find(BENCH_END)
    if start == -1 or end == -1 or end < start:
        if not text:
            return block + "\n"
        sep = "" if text.endswith("\n") else "\n"
        return text + sep + "\n" + block + "\n"
    end += len(BENCH_END)
    return text[:start] + block + text[end:]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default="data/_fake/fake_itch5.bin")
    ap.add_argument("--symbol", default=None,
                    help="replay this stock only (real, multi-symbol days)")
    ap.add_argument("--progress-every", type=int, default=10_000_000,
                    help="stderr heartbeat; 0 silences it")
    ap.add_argument("--bench", action="store_true",
                    help="time parse-only, replay-only and end-to-end "
                         "separately; median of --repeats runs")
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--limit", type=int, default=0,
                    help="bench only the first N messages of the file "
                         "(0 = all); a real day is ~300M messages")
    ap.add_argument("--write", default=None, metavar="PATH",
                    help="splice the bench block into this markdown file "
                         "between the bench markers")
    args = ap.parse_args()

    path = Path(args.file)
    if not path.exists():
        raise SystemExit(f"no such file: {path} (the synthetic day comes "
                         f"from scripts/make_fake_itch.py)")
    symbols = {args.symbol} if args.symbol else None

    if args.bench:
        print(f"ITCH 5.0 throughput baseline -- {path.name}"
              + (f", symbol {args.symbol}" if args.symbol else "")
              + (f", first {args.limit:,} messages" if args.limit else ""))
        b = run_bench(path, symbols, args.limit or None, args.repeats)
        block = render_bench(b)
        print()
        print(block)
        if args.write:
            target = Path(args.write)
            old = target.read_text(encoding="utf-8") if target.exists() else ""
            target.write_text(splice_block(old, block), encoding="utf-8")
            print(f"\n  written into {target} between the bench markers")
        if not b["clean"]:
            raise SystemExit(1)
        return

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
