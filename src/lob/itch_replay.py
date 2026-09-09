"""Full-depth ITCH replay: an empty book, every message, and no mercy.

This is Day 2's machinery turned inside out. The LOBSTER replayer began
with a seed (the filter hid history) and carried repair rules (the
filter created blind spots). An ITCH file needs neither: it begins
before the market opens, when the book is GENUINELY empty, and carries
every add at every depth from the first message. Start from nothing,
apply everything, and the book is exact at every instant by
construction.

So this replayer has no seed parameter, no dark-liquidity rule, no
evictions, no witness trimming. In their place stand four counters that
must all be ZERO on unfiltered data, or the Day 2 pre-commitment in
results/lobster_validation.md is falsified and the bug is ours:

- unknown_refs    -- an operation named an id this file never gave us.
                     On LOBSTER that was dark liquidity; here it is a
                     parser or replayer bug, full stop.
- crossing_adds   -- an add (or the add half of a U) priced through the
                     opposite best. The exchange matches before it
                     publishes; a cross here is corruption, ours.
- front_violations -- an 'E' executed an order that was NOT at the front
                     of its price level. Price-time priority says the
                     front goes first; Day 2 could never assert this
                     (seeded composition was fiction), and now we can.
- anomalies       -- everything else refused or impossible: an 'X' that
                     would zero an order (a full removal is 'D' in
                     feed-land), shares exceeding what rests, a 'U'
                     naming a new id that already exists.

Violations are recorded and the replay CONTINUES -- message truth is
still applied where it names a real order -- because a bug hunt needs
the whole pattern, not the first stack trace. The script exits nonzero
if any counter is nonzero; nothing is quietly relaxed.

The 'U' translation happens here and nowhere else: Nasdaq retires the
old reference and issues a new one, so the replayer cancels the old id
and adds the NEW id at the new price and size -- at the back of its
queue, which is exactly what U semantics cost the order that amended.
Validation happens BEFORE the cancel (the backtester's Week 1 lesson,
pinned by test): a refused replace must leave the original exactly
where it stood, place in line included.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Iterable

from lob.book import Book, Side
from lob.itch import ItchMessage

_SIDE = {"B": Side.BID, "S": Side.ASK}

# message kinds that touch the book; everything else is a counted no-op
BOOK_KINDS = frozenset("AFXDECU")

_ANOMALY_KEEP = 30       # full renders kept; the total is always counted


@dataclass(frozen=True)
class ItchAnomaly:
    """One impossible message, with enough context to hunt the bug."""
    seq: int                  # ordinal among book-touching messages
    msg: ItchMessage
    reason: str

    def render(self) -> str:
        m = self.msg
        return (f"#{self.seq} {m.kind} ref={m.order_ref}"
                + (f"->{m.new_order_ref}" if m.new_order_ref else "")
                + (f" shares={m.shares}" if m.shares is not None else "")
                + (f" px={m.price}" if m.price is not None else "")
                + f" @ {m.time_ns}ns: {self.reason}")


@dataclass
class ItchReplayStats:
    counts: Counter = field(default_factory=Counter)   # every kind seen
    applied: int = 0            # book-touching messages applied
    unknown_refs: int = 0
    crossing_adds: int = 0
    front_violations: int = 0
    anomalies_total: int = 0
    anomalies: list[ItchAnomaly] = field(default_factory=list)
    full_fills: int = 0         # executions that emptied an order
    max_live: int = 0
    hourly: Counter = field(default_factory=Counter)   # applied per hour

    @property
    def clean(self) -> bool:
        """The prediction's bar: every counter zero, no exceptions."""
        return (self.unknown_refs == 0 and self.crossing_adds == 0
                and self.front_violations == 0 and self.anomalies_total == 0)


class ItchReplayer:
    """Folds a decoded ITCH stream into a Book, from empty, keeping score.

    Deliberately takes no seed and no repair options: their absence IS
    the experiment. Feed one symbol's stream (the parser's filter, or a
    single-symbol file); mixing symbols into one book is nonsense the
    caller must not commit.
    """

    def __init__(self) -> None:
        self.book = Book()
        self.stats = ItchReplayStats()

    # ---------------------------------------------------------- recording
    def _flag(self, m: ItchMessage, reason: str, counter: str) -> None:
        s = self.stats
        setattr(s, counter, getattr(s, counter) + 1)
        if counter == "anomalies_total" and len(s.anomalies) < _ANOMALY_KEEP:
            s.anomalies.append(ItchAnomaly(s.applied, m, reason))

    # ------------------------------------------------------------ applying
    def _take_shares(self, m: ItchMessage, assert_front: bool) -> None:
        """Shares off THE order an execution names -- 'E' and 'C' both
        land here; only 'E' carries the front-of-queue assertion."""
        try:
            order = self.book.order(m.order_ref)
        except KeyError:
            self._flag(m, "execution against an id not in the book",
                       "unknown_refs")
            return
        if assert_front:
            front = self.book.orders_at(order.side, order.price)[0]
            if front != m.order_ref:
                self._flag(m, f"executed order is not at the front of "
                              f"{order.price} (front is {front})",
                           "front_violations")
        if m.shares > order.qty:
            self._flag(m, f"execution of {m.shares} exceeds the {order.qty} "
                          f"resting", "anomalies_total")
            return
        if m.shares == order.qty:
            self.book.cancel(m.order_ref)
            self.stats.full_fills += 1
        else:
            self.book.reduce(m.order_ref, m.shares)

    def apply(self, m: ItchMessage) -> None:
        s = self.stats
        s.counts[m.kind] += 1
        if m.kind not in BOOK_KINDS:
            return                       # P/Q/B/S/R/H: counted, never applied

        s.applied += 1
        s.hourly[m.time_ns // 3_600_000_000_000] += 1

        if m.kind in ("A", "F"):
            try:
                self.book.add(m.order_ref, _SIDE[m.side], m.price, m.shares)
            except ValueError as e:
                counter = ("crossing_adds" if "crosses" in str(e)
                           else "anomalies_total")
                self._flag(m, str(e), counter)

        elif m.kind == "X":              # partial cancel: position KEPT
            try:
                order = self.book.order(m.order_ref)
            except KeyError:
                self._flag(m, "partial cancel of an id not in the book",
                           "unknown_refs")
            else:
                if m.shares >= order.qty:
                    self._flag(m, f"partial cancel of {m.shares} would zero "
                                  f"the {order.qty} resting; a full removal "
                                  f"is 'D' in feed-land", "anomalies_total")
                else:
                    self.book.reduce(m.order_ref, m.shares)

        elif m.kind == "D":
            try:
                self.book.cancel(m.order_ref)
            except KeyError:
                self._flag(m, "delete of an id not in the book",
                           "unknown_refs")

        elif m.kind == "E":
            self._take_shares(m, assert_front=True)

        elif m.kind == "C":              # price-improved execution: same
            self._take_shares(m, assert_front=False)   # book effect as E

        elif m.kind == "U":
            # validate BEFORE the cancel: a refused replace leaves the
            # original exactly where it stood, place in line included.
            try:
                old = self.book.order(m.order_ref)
            except KeyError:
                self._flag(m, "replace of an id not in the book",
                           "unknown_refs")
                return
            try:
                self.book.order(m.new_order_ref)
            except KeyError:
                pass                     # good: the new id must be new
            else:
                self._flag(m, f"replace issues id {m.new_order_ref}, which "
                              f"already rests in the book", "anomalies_total")
                return
            if m.shares <= 0:
                self._flag(m, "replace to a non-positive size",
                           "anomalies_total")
                return
            opp = (self.book.best_ask() if old.side is Side.BID
                   else self.book.best_bid())
            if opp is not None and (
                    m.price >= opp if old.side is Side.BID else m.price <= opp):
                self._flag(m, f"replace to {m.price} would cross the "
                              f"opposite best {opp}", "crossing_adds")
                return
            # cancelling our own order cannot change the OPPOSITE best,
            # so the check above still holds after this cancel.
            self.book.cancel(m.order_ref)
            self.book.add(m.new_order_ref, old.side, m.price, m.shares)

        live = len(self.book)
        if live > s.max_live:
            s.max_live = live

    def run(self, messages: Iterable[ItchMessage]) -> ItchReplayStats:
        for m in messages:
            self.apply(m)
        return self.stats
