"""The self-filter experiment: part two of the Day 2 verdict.

    python scripts/filter_experiment.py                  # 100k-message day
    python scripts/filter_experiment.py --level 5 --n 20000

Day 2 ended with a taxonomy -- STALE, STALE_SIZE, BACKFILL,
BACKFILL_SIZE -- and a causal story: every residual divergence is the
level filter hiding below-band events, not a defect in the replay. On
LOBSTER's files that story was consistent with everything and provable
from nothing, because the ground truth belonged to LOBSTER.

Now we own the ground truth. This script replays a full-depth synthetic
ITCH day through a real Book, and BECOMES the filter: it emits exactly
the LOBSTER-style level-K view of that day -- the message subset that
changes the top K levels (each 'U' split into the delete+add pair
LOBSTER publishes, each half filtered independently), and the K-level
orderbook row after every emitted message, dummy-padded like the real
files. Because we do the hiding ourselves, we know every event the
filter swallowed: which adds were born below the band, which deletes
died there silently, which partial cancels drifted a visible order's
size, and therefore

- exactly which emitted operations name an id the filtered file never
  introduced -- the Day 2 dark-op rule's caseload, PREDICTED before the
  machinery runs;
- exactly which orders become phantoms: add emitted, death silent, so
  a faithful replayer must carry them until evidence kills them;
- exactly which emitted deletes will disagree about size.

Then the UNCHANGED Day 2 machinery (seed from row 0, dark rule,
crossing/execution evictions, witness trim) replays our filtered files
through the real CSV loaders, and the validator grades it against our
reference rows. The bar:

- the machinery's dark-op count must be predicted_never_added plus an
  eviction-induced remainder, never less than the prediction;
- every divergent row must land in the four classes, zero unexplained;
- the machinery must report zero anomalies -- the filter explains
  everything, or the pre-commitment falls;
- and the filter must actually have hidden something, or this
  experiment proved nothing and says so loudly.

This is 'two independent paths agree' in its strongest form: the
full-depth path and the filtered path run over the identical underlying
reality, and Day 2's plausible story either becomes a measured
mechanism here, or dies here.
"""
from __future__ import annotations

import argparse
import statistics
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from lob.book import Book, Side
from lob.itch import ItchMessage
from lob.lobster import (ASK_DUMMY_PRICE, BID_DUMMY_PRICE, EventType,
                         read_messages, read_orderbook)
from lob.replay import LobsterReplayer, classify_divergence, run_validated
from make_fake_itch import build_fake_day

_SIDE = {"B": Side.BID, "S": Side.ASK}
_DIR = {Side.BID: 1, Side.ASK: -1}


@dataclass(frozen=True)
class Silent:
    """One event the filter swallowed, kept for causal lookups."""
    etype: int
    order_ref: int
    side: Side
    price: int
    size: int
    time_ns: int


@dataclass(frozen=True)
class Phantom:
    """Add emitted, death silent: the replay must carry this order
    until the machinery's evidence rules kill it, or forever."""
    order_ref: int
    side: Side
    price: int
    death_ns: int
    shares: int


@dataclass
class FilterOutcome:
    k: int
    message_rows: list = field(default_factory=list)   # 6-column CSV rows
    book_rows: list = field(default_factory=list)      # 4k-column CSV rows
    events: int = 0                    # ground-truth primitive events
    silent: Counter = field(default_factory=Counter)   # etype -> hidden
    silents: list = field(default_factory=list)        # Silent records
    silent_adds: int = 0               # orders born below the band
    born_and_died_below: int = 0       # never visible at all: benign
    phantoms: list = field(default_factory=list)       # Phantom records
    drift_shares: int = 0              # silent partials on visible orders
    predicted_dark: int = 0            # emitted ops on never-introduced ids
    predicted_delete_disagreements: int = 0
    last_ns: int = 0

    @property
    def emitted(self) -> int:
        return len(self.message_rows)


def _time_str(ns: int) -> str:
    return f"{ns // 1_000_000_000}.{ns % 1_000_000_000:09d}"


def _top_k(book: Book, k: int):
    return (book.top_levels(Side.ASK, k), book.top_levels(Side.BID, k))


def _book_row(book: Book, k: int) -> list[int]:
    asks, bids = _top_k(book, k)
    row: list[int] = []
    for i in range(k):
        ap, asz = asks[i] if i < len(asks) else (ASK_DUMMY_PRICE, 0)
        bp, bsz = bids[i] if i < len(bids) else (BID_DUMMY_PRICE, 0)
        row += [ap, asz, bp, bsz]
    return row


def filter_day(messages, k: int) -> FilterOutcome:
    """Replay full depth; emit the level-K view; account for the rest.

    The emission rule is LOBSTER's own, implemented literally instead of
    through case analysis: a primitive event is published iff the top-K
    snapshot it produces differs from the one before it. Everything else
    is the filter's silence, and every silence is recorded.
    """
    book = Book()
    out = FilterOutcome(k)
    visible: set[int] = set()      # ids the FILTERED file introduced
    drifted: set[int] = set()      # visible ids with silent partials
    seed_swallowed: set[int] = set()   # row-0 add: consumed by the seed

    for m in messages:
        if m.kind not in ("A", "F", "X", "D", "E", "U"):
            continue                       # S/R: no LOBSTER analogue
        # decompose into LOBSTER primitives: (etype, oid, size, price,
        # side, mutate); 'U' becomes the delete+add pair it publishes as
        if m.kind in ("A", "F"):
            side = _SIDE[m.side]
            prims = [(1, m.order_ref, m.shares, m.price, side,
                      lambda mm=m, s=side: book.add(mm.order_ref, s,
                                                    mm.price, mm.shares))]
        elif m.kind == "X":
            o = book.order(m.order_ref)
            prims = [(2, m.order_ref, m.shares, o.price, o.side,
                      lambda mm=m: book.reduce(mm.order_ref, mm.shares))]
        elif m.kind == "D":
            o = book.order(m.order_ref)
            prims = [(3, m.order_ref, o.qty, o.price, o.side,
                      lambda mm=m: book.cancel(mm.order_ref))]
        elif m.kind == "E":
            o = book.order(m.order_ref)
            full = m.shares == o.qty
            prims = [(4, m.order_ref, m.shares, o.price, o.side,
                      (lambda mm=m: book.cancel(mm.order_ref)) if full
                      else (lambda mm=m: book.reduce(mm.order_ref,
                                                     mm.shares)))]
        else:                              # U: delete old, add new
            o = book.order(m.order_ref)
            prims = [(3, m.order_ref, o.qty, o.price, o.side,
                      lambda mm=m: book.cancel(mm.order_ref)),
                     (1, m.new_order_ref, m.shares, m.price, o.side,
                      lambda mm=m, s=o.side: book.add(mm.new_order_ref, s,
                                                      mm.price, mm.shares))]

        for etype, oid, size, price, side, mutate in prims:
            out.events += 1
            before = _top_k(book, k)
            mutate()
            out.last_ns = m.time_ns
            if _top_k(book, k) != before:              # EMITTED
                if etype == 1:
                    if out.message_rows:
                        visible.add(oid)
                    else:                  # message 0: the seed eats it
                        seed_swallowed.add(oid)
                else:
                    if oid not in visible:
                        out.predicted_dark += 1
                    elif etype == 3 and oid in drifted:
                        out.predicted_delete_disagreements += 1
                out.message_rows.append(
                    [_time_str(m.time_ns), etype, oid, size, price,
                     _DIR[side]])
                out.book_rows.append(_book_row(book, k))
            else:                                      # SILENT
                out.silent[etype] += 1
                out.silents.append(Silent(etype, oid, side, price, size,
                                          m.time_ns))
                if etype == 1:
                    out.silent_adds += 1
                elif etype == 4:
                    raise AssertionError(
                        "an execution changed nothing? executions hit "
                        "the best level; the filter cannot hide one")
                elif etype == 2 and oid in visible:
                    drifted.add(oid)
                    out.drift_shares += size
                elif etype == 3:
                    if oid in visible:
                        out.phantoms.append(Phantom(oid, side, price,
                                                    m.time_ns, size))
                    elif oid not in seed_swallowed:
                        out.born_and_died_below += 1
    return out


def write_files(out: FilterOutcome, outdir: Path) -> tuple[Path, Path]:
    outdir.mkdir(parents=True, exist_ok=True)
    mpath = outdir / f"self_filtered_message_{out.k}.csv"
    bpath = outdir / f"self_filtered_orderbook_{out.k}.csv"
    with open(mpath, "w") as f:
        f.write("\n".join(",".join(map(str, r))
                          for r in out.message_rows) + "\n")
    with open(bpath, "w") as f:
        f.write("\n".join(",".join(map(str, r))
                          for r in out.book_rows) + "\n")
    return mpath, bpath


def trace_first(mismatch, out: FilterOutcome) -> list[str]:
    """Best-effort causal lookup: name the silent event behind the
    first divergence, from our own records of what the filter hid."""
    lines = []
    for label, ours, ref, side in (
            ("ask", mismatch.ours_asks, mismatch.ref_asks, Side.ASK),
            ("bid", mismatch.ours_bids, mismatch.ref_bids, Side.BID)):
        c = classify_divergence(ours, ref, side)
        if c is None:
            continue
        rank, klass = c
        if klass.startswith("STALE"):
            price = ours[rank - 1][0] if rank - 1 < len(ours) else None
            want = (2, 3)              # a hidden removal made us stale
        else:
            price = ref[rank - 1][0] if rank - 1 < len(ref) else None
            want = (1,)                # a hidden add backfilled the ref
        if price is None:
            continue
        hits = [s for s in out.silents
                if s.side is side and s.price == price
                and s.etype in want and s.time_ns <= mismatch.time_ns]
        if hits:
            s = hits[-1]
            lines.append(
                f"    {label} {klass} at rank {rank}, price {price}: "
                f"caused by silent type-{s.etype} of {s.size} on order "
                f"{s.order_ref} at {s.time_ns // 1_000_000_000}s -- "
                f"below the band, so the filter said nothing")
        else:
            lines.append(f"    {label} {klass} at rank {rank}, price "
                         f"{price}: no single silent event matches "
                         f"(compound history at this level)")
    return lines


def run_experiment(n: int, seed: int, k: int, outdir: Path,
                   quiet: bool = False) -> dict:
    def say(*a):
        if not quiet:
            print(*a)

    _, truth = build_fake_day(n, seed)
    out = filter_day(truth, k)
    mpath, bpath = write_files(out, outdir)

    say(f"SELF-FILTER EXPERIMENT -- level {k}, {n:,}-message synthetic "
        f"day (seed {seed})")
    say(f"  ground truth: {out.events:,} primitive events; the filter "
        f"emitted {out.emitted:,} ({out.emitted / out.events:.1%})")
    say(f"  hidden below the band: "
        + "  ".join(f"type{t}:{c:,}" for t, c in sorted(out.silent.items())))
    say(f"    adds born dark: {out.silent_adds:,}   "
        f"born-and-died dark (benign): {out.born_and_died_below:,}")
    say(f"    phantoms made (add emitted, death silent): "
        f"{len(out.phantoms):,}")
    say(f"    size drift on visible orders: {out.drift_shares:,} shares "
        f"across silent partials")
    say(f"  therefore the machinery MUST report: dark ops >= "
        f"{out.predicted_dark:,}; delete-size disagreements <= "
        f"{out.predicted_delete_disagreements:,}")
    say()

    # ---- the UNCHANGED Day 2 machinery, through the real loaders ----
    messages = read_messages(mpath)
    reference = read_orderbook(bpath, k)
    assert len(messages) == len(reference)
    replayer = LobsterReplayer(Book(), levels=k)
    replayer.seed(reference[0])
    result = run_validated(replayer, messages, reference, k)
    stats = replayer.stats

    say(f"  Day 2 machinery on our filtered files "
        f"({len(messages):,} messages):")
    say(f"    dark ops {stats.dark_ops:,} (predicted "
        f"{out.predicted_dark:,} + {stats.dark_ops - out.predicted_dark:,} "
        f"eviction-induced)   ghost evictions {stats.ghost_evictions:,}")
    say(f"    delete-size disagreements {stats.delete_size_disagreements:,} "
        f"(predicted <= {out.predicted_delete_disagreements:,})   "
        f"anomalies {len(stats.anomalies)}")
    say(f"    rows exact to level {k}: {result.matched:,}/{result.total:,} "
        f"({result.match_rate:.1%}); touch-exact "
        f"{sum(v[2] for v in result.hourly.values()):,}")
    depth_bits = "  ".join(
        f"J{j}:{result.matched_at_depth[j] / result.total:.1%}"
        for j in sorted(result.matched_at_depth))
    say(f"    matched at depth: {depth_bits}")
    classified = sum(result.divergence_classes.values())
    divergent = sum(result.first_bad_depth.values())
    say(f"    divergence classes over {divergent:,} divergent rows "
        f"({classified:,} classified, {divergent - classified:,} "
        f"unexplained):")
    for name, cnt in sorted(result.divergence_classes.items(),
                            key=lambda kv: -kv[1]):
        say(f"      {name:<14} {cnt:,}")
    say()

    # ---- phantom census against the final replay book ----
    survivors = []
    for ph in out.phantoms:
        try:
            replayer.book.order(ph.order_ref)
        except KeyError:
            continue
        survivors.append(ph)
    evicted = len(out.phantoms) - len(survivors)
    say(f"  phantom census: {len(out.phantoms):,} made; the machinery's "
        f"evidence rules evicted {evicted:,}; {len(survivors):,} still "
        f"haunt the close")
    if survivors:
        ages_min = [(out.last_ns - ph.death_ns) / 60e9 for ph in survivors]
        say(f"    survivor age at close (min/median/max): "
            f"{min(ages_min):.1f} / {statistics.median(ages_min):.1f} / "
            f"{max(ages_min):.1f} minutes, "
            f"{sum(ph.shares for ph in survivors):,} shares of nothing")
    if result.first_mismatches:
        mm = result.first_mismatches[0]
        say(f"\n  first divergence, traced to its cause:")
        say(f"    {mm.render().splitlines()[0]}")
        for line in trace_first(mm, out):
            say(line)
    say()

    four = {"STALE", "STALE_SIZE", "BACKFILL", "BACKFILL_SIZE"}
    seen = set(result.divergence_classes)
    checks = {
        "filter actually hid something": sum(out.silent.values()) > 0,
        "divergences occurred (the filter bit)": divergent > 0,
        "every divergent row classified": classified == divergent,
        "only the four Day 2 classes appeared": seen <= four,
        "dark ops >= prediction": stats.dark_ops >= out.predicted_dark,
        "delete disagreements <= prediction":
            stats.delete_size_disagreements
            <= out.predicted_delete_disagreements,
        "machinery anomalies zero": len(stats.anomalies) == 0,
    }
    for name, ok in checks.items():
        say(f"  [{'ok' if ok else 'FAIL'}] {name}")
    say(f"  classes seen: {', '.join(sorted(seen)) or 'none'}"
        + ("" if seen == four else
           f"  (absent: {', '.join(sorted(four - seen))})"))
    say()
    if all(checks.values()):
        say("  VERDICT: the Day 2 taxonomy is a measured mechanism. The "
            "same machinery, fed a")
        say("  filter WE controlled, produced the same divergence "
            "classes with zero unexplained")
        say("  rows, and its dark-op caseload was predicted from the "
            "filter's own silences.")
    else:
        say("  VERDICT: FAILED. Either the filter, the machinery, or "
            "the Day 2 story is wrong,")
        say("  and now there is ground truth to find out which.")

    return {
        "checks": checks,
        "classes": dict(result.divergence_classes),
        "match_rate": result.match_rate,
        "divergent": divergent,
        "unexplained": divergent - classified,
        "dark_ops": stats.dark_ops,
        "predicted_dark": out.predicted_dark,
        "phantoms": len(out.phantoms),
        "phantom_survivors": len(survivors),
        "emitted": out.emitted,
        "events": out.events,
        "ok": all(checks.values()),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=100_000)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--level", type=int, default=3)
    ap.add_argument("--outdir", default="data/_fake/filtered")
    args = ap.parse_args()
    report = run_experiment(args.n, args.seed, args.level,
                            Path(args.outdir))
    if not report["ok"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
