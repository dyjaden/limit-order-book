"""Synthetic message tape: fiction that obeys the physics.

    python scripts/make_fake_tape.py                # 100k messages
    python scripts/make_fake_tape.py --n 500000 --seed 11

The make_fake_crsp lesson, transplanted whole from the backtester: the
NUMBERS are fiction, the SHAPES are the contract, and the machinery earns
trust against fiction before it touches a real feed. When Week 2's
LOBSTER replay disagrees with the reference, the suspect list is the
parser and the sequencing -- not the book, because the book will already
have survived everything this file can throw at it.

The tape is market-shaped on purpose: adds cluster near the touch,
cancellations dominate (most orders die unexecuted; US equity
order-to-trade ratios run well past ten to one), executes consume the
front of the best queue, and the population is held in a band so the book
neither starves nor bloats. The generator only ever emits LEGAL
operations -- fiction has to obey the physics too, or every downstream
test inherits its confusion.

Replay is the other half: fold the finished tape into a FRESH book and
assert the invariants -- uncrossed, non-negative depth, id-count
consistency -- at every single step. The messages/sec it prints is a
throwaway curiosity; Week 3 records the honest baseline.
"""
from __future__ import annotations

import argparse
import time
from dataclasses import dataclass
from random import Random

from lob import Book, Side

ANCHOR = 10_000          # ticks; fiction needs a place to stand
MAX_LIVE = 2_000         # population band: above this, cancels dominate
MIN_LIVE = 50            # below this, adds dominate


@dataclass(frozen=True)
class Message:
    """One tape entry. kind: add | cancel | replace | execute.
    For executes, `side` names the side being consumed and `qty` the
    total demand; order_id and price are unused (-1)."""
    kind: str
    order_id: int
    side: Side
    price: int
    qty: int


@dataclass
class ReplayStats:
    messages: int = 0
    adds: int = 0
    cancels: int = 0
    replaces: int = 0
    executes: int = 0
    fills: int = 0
    full_fills: int = 0
    max_live: int = 0
    seconds: float = 0.0

    @property
    def msgs_per_sec(self) -> float:
        return self.messages / self.seconds if self.seconds > 0 else 0.0


def make_tape(n_messages: int = 100_000, seed: int = 7) -> list[Message]:
    """A legal, market-shaped message sequence, deterministic per seed.

    Drives a real Book while generating, so every choice is drawn from
    actual live state -- a cancel always names a live order, an execute
    never exceeds visible liquidity, an add never crosses. The tape is
    then INDEPENDENTLY replayed by callers against a fresh book; this
    function's book is scaffolding and is thrown away.
    """
    rng = Random(seed)
    book = Book()
    tape: list[Message] = []
    live: dict[int, int] = {}        # order_id -> remaining qty (shadow)
    next_id = 1

    def touch_prices(side: Side) -> tuple[int, int]:
        """A legal, near-the-touch price range for a new order."""
        bid, ask = book.best_bid(), book.best_ask()
        if side is Side.BID:
            hi = (ask - 1) if ask is not None else ANCHOR
            lo = hi - 8
        else:
            lo = (bid + 1) if bid is not None else ANCHOR + 1
            hi = lo + 8
        return max(1, lo), max(1, hi)

    def do_add() -> None:
        nonlocal next_id
        side = rng.choice((Side.BID, Side.ASK))
        lo, hi = touch_prices(side)
        price = rng.randint(lo, hi)
        qty = rng.randint(1, 500)
        book.add(next_id, side, price, qty)
        live[next_id] = qty
        tape.append(Message("add", next_id, side, price, qty))
        next_id += 1

    def do_cancel() -> None:
        oid = rng.choice(list(live))
        order = book.order(oid)
        book.cancel(oid)
        del live[oid]
        tape.append(Message("cancel", oid, order.side, order.price, 0))

    def do_replace() -> None:
        oid = rng.choice(list(live))
        order = book.order(oid)
        lo, hi = touch_prices(order.side)
        # the order's own price is always legal too (pure size change)
        price = rng.choice([order.price, rng.randint(lo, hi)])
        qty = rng.randint(1, 500)
        book.replace(oid, price, qty)
        live[oid] = qty
        tape.append(Message("replace", oid, order.side, price, qty))

    def do_execute() -> None:
        sides = [s for s in (Side.BID, Side.ASK)
                 if book.depth(s, 1)]
        if not sides:
            return do_add()
        side = rng.choice(sides)
        # demand up to roughly the best few orders' worth, never more
        # than the side holds
        available = sum(q for _, q in book.depth(side, 3))
        qty = rng.randint(1, max(1, min(available, 600)))
        for fill in book.execute(side, qty):
            live[fill.order_id] -= fill.qty
            if live[fill.order_id] == 0:
                del live[fill.order_id]
        tape.append(Message("execute", -1, side, -1, qty))

    # market-like mix, bent by the population band so the book neither
    # starves nor bloats: cancels dominate trades ~4:1, adds keep pace
    for _ in range(n_messages):
        n_live = len(live)
        if n_live < MIN_LIVE:
            do_add()
            continue
        r = rng.random()
        if n_live > MAX_LIVE:
            r = 0.55 + 0.45 * r          # push into cancel territory
        if r < 0.52:
            do_add()
        elif r < 0.85:
            do_cancel()
        elif r < 0.93:
            do_replace()
        else:
            do_execute()

    return tape


def replay(tape: list[Message], book: Book | None = None,
           check_every: int = 1) -> ReplayStats:
    """Fold the tape into a fresh book, asserting the invariants as it
    goes: the book never crosses, no level holds non-positive quantity,
    and the id count reconciles exactly with adds minus departures.
    `check_every=1` checks after every message -- slow and thorough, the
    right setting for fiction; real replays can widen it.
    """
    book = book if book is not None else Book()
    stats = ReplayStats()
    expected_live = len(book)
    t0 = time.perf_counter()

    for i, msg in enumerate(tape):
        if msg.kind == "add":
            book.add(msg.order_id, msg.side, msg.price, msg.qty)
            stats.adds += 1
            expected_live += 1
        elif msg.kind == "cancel":
            book.cancel(msg.order_id)
            stats.cancels += 1
            expected_live -= 1
        elif msg.kind == "replace":
            book.replace(msg.order_id, msg.price, msg.qty)
            stats.replaces += 1
        elif msg.kind == "execute":
            fills = book.execute(msg.side, msg.qty)
            stats.executes += 1
            stats.fills += len(fills)
            died = 0
            for f in fills:                    # a full fill leaves the index
                try:
                    book.order(f.order_id)
                except KeyError:
                    died += 1
            stats.full_fills += died
            expected_live -= died
        else:
            raise ValueError(f"unknown message kind {msg.kind!r}")

        stats.messages += 1
        stats.max_live = max(stats.max_live, len(book))

        if i % check_every == 0:
            bid, ask = book.best_bid(), book.best_ask()
            assert bid is None or ask is None or bid < ask, (
                f"CROSSED at message {i}: bid {bid} >= ask {ask}")
            assert len(book) == expected_live, (
                f"id drift at message {i}: book {len(book)}, "
                f"expected {expected_live}")
            for side in (Side.BID, Side.ASK):
                for price, qty in book.depth(side, 3):
                    assert qty > 0, (f"non-positive depth {qty} at "
                                     f"{price} on {side.value}")

    stats.seconds = time.perf_counter() - t0
    return stats


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=100_000)
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()

    print(f"SYNTHETIC TAPE -- {args.n:,} messages, seed {args.seed} "
          f"(FICTION, for rehearsal only)")
    tape = make_tape(args.n, args.seed)
    stats = replay(tape, check_every=1)

    n = stats.messages
    print(f"  adds {stats.adds:,} ({stats.adds / n:.0%})   "
          f"cancels {stats.cancels:,} ({stats.cancels / n:.0%})   "
          f"replaces {stats.replaces:,} ({stats.replaces / n:.0%})   "
          f"executes {stats.executes:,} ({stats.executes / n:.0%})")
    print(f"  fills {stats.fills:,} ({stats.full_fills:,} full)   "
          f"order:trade ratio "
          f"{(stats.adds + stats.replaces) / max(1, stats.executes):.1f}:1   "
          f"peak live orders {stats.max_live:,}")
    print(f"  replayed with invariants checked at EVERY step: "
          f"{stats.seconds:.1f}s  (~{stats.msgs_per_sec:,.0f} msg/s -- "
          f"a curiosity, not the baseline; Week 3 measures honestly)")
    print("  ALL INVARIANTS HELD")


if __name__ == "__main__":
    main()
