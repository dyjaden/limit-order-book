"""Replay LOBSTER messages into a Book, seeded from the reference.

A feed is an interval cut out of an infinite log. Orders resting at 9:30
were placed before the file begins, and orders resting below level K only
enter the file once they matter to the top K, so the day's messages will
reduce, delete, and execute order ids the file never introduced. This
module handles both facts explicitly instead of treating them as errors:

- ``seed_book`` initialises the book from the FIRST reference row, one
  synthetic order per occupied level, ids drawn from a reserved negative
  range. Level totals are exact; the composition within a seeded level is
  admittedly fiction and is documented as such wherever it matters.
- Operations on unknown ids are operations on that pre-window or below-K
  liquidity. They are applied against the synthetic order at the
  message's own price (LOBSTER messages carry the price, which is what
  makes this possible), so level totals stay truthful. What cannot be
  resolved is counted, never swallowed.

Executions of known orders are applied as in-place removals (reduce, or
cancel when fully filled). For book STATE that is exactly what an
execution is; the book's own ``execute`` walks the front of the best
queue, which is the right primitive for aggregate demand but not for
replaying a feed that names its targets. No front-of-queue assertion is
made here: seeded levels put synthetic liquidity ahead of real orders
whose true queue position at 9:30 is unknowable, so front-ness against
our book is not evidence of anything.

The book stays loud; the replayer is the feed-facing layer, and its job
includes surviving reality and REPORTING it. A message the book refuses
is recorded as an anomaly with its full context and the replay continues;
the validator is the instrument that decides whether anomalies mattered.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from lob.book import Book, Side
from lob.lobster import EventType, LobsterMessage, ReferenceRow

SYNTH_BASE = -1_000_000     # synthetic ids live at SYNTH_BASE - k


def side_of(direction: int) -> Side:
    """LOBSTER direction: +1 is a buy (bid side), -1 a sell (ask side)."""
    if direction == 1:
        return Side.BID
    if direction == -1:
        return Side.ASK
    raise ValueError(f"direction must be +1 or -1, got {direction}")


@dataclass
class ReplayStats:
    applied: int = 0
    by_type: dict = field(default_factory=dict)
    seeded_orders: int = 0
    dark_ops: int = 0           # unknown-id ops resolved against synthetics
    unresolved: int = 0         # dark ops with nothing at that price
    unresolved_shares: int = 0
    ghost_evictions: int = 0    # stale orders a message PROVED dead
    ghost_shares: int = 0
    anomalies: list = field(default_factory=list)   # (index, msg, error)

    def count(self, kind: EventType) -> None:
        self.by_type[kind.name] = self.by_type.get(kind.name, 0) + 1


class LobsterReplayer:
    """Folds LOBSTER messages into a book, tracking everything it does."""

    def __init__(self, book: Book) -> None:
        self.book = book
        self.stats = ReplayStats()
        self._synth_at: dict[tuple[Side, int], int] = {}
        self._next_synth = SYNTH_BASE

    # ------------------------------------------------------------- seeding
    def seed(self, first_row: ReferenceRow) -> None:
        """The book state after message 0, taken from the reference's own
        first row: one synthetic order per occupied level. Apply messages
        from index 1 onward; message 0's effect is inside the seed."""
        for side, levels in ((Side.ASK, first_row.asks),
                             (Side.BID, first_row.bids)):
            for price, size in levels:
                self._new_synth(side, price, size)
        self.stats.seeded_orders = len(self._synth_at)

    def _new_synth(self, side: Side, price: int, size: int) -> None:
        self._next_synth -= 1
        self.book.add(self._next_synth, side, price, size)
        self._synth_at[(side, price)] = self._next_synth

    # ------------------------------------------------------------ applying
    def apply(self, index: int, msg: LobsterMessage) -> None:
        try:
            self._apply(msg)
            self.stats.applied += 1
            self.stats.count(msg.kind)
        except (ValueError, KeyError) as e:
            self.stats.anomalies.append((index, msg, str(e)))

    def _known(self, order_id: int) -> bool:
        try:
            self.book.order(order_id)
            return True
        except KeyError:
            return False

    def _evict(self, side: Side, price: int) -> None:
        """Remove every order at one level as proven-stale: liquidity that
        left silently below the visible band (a level-filtered file never
        reports those departures) and has now been contradicted by a
        legal message. Message-stream truth only; the reference is never
        consulted."""
        for oid in self.book.orders_at(side, price):
            self.stats.ghost_evictions += 1
            self.stats.ghost_shares += self.book.order(oid).qty
            self.book.cancel(oid)
            if self._synth_at.get((side, price)) == oid:
                del self._synth_at[(side, price)]

    def _evict_crossed_by_add(self, add_side: Side, price: int) -> None:
        """A real feed never delivers a crossing add, so an add that
        crosses OUR book proves the crossed liquidity is a ghost."""
        opposite = Side.ASK if add_side is Side.BID else Side.BID
        for p in self.book.prices(opposite):
            crossed = (p <= price) if opposite is Side.ASK else (p >= price)
            if crossed:
                self._evict(opposite, p)

    def _evict_better_than_exec(self, side: Side, price: int) -> None:
        """Executions consume the best price first, so an execution at
        `price` proves nothing better survives on that side."""
        for p in self.book.prices(side):
            better = (p > price) if side is Side.BID else (p < price)
            if better:
                self._evict(side, p)

    def _apply(self, msg: LobsterMessage) -> None:
        side = side_of(msg.direction)
        kind = msg.kind

        if kind is EventType.ADD:
            self._evict_crossed_by_add(side, msg.price)
            self.book.add(msg.order_id, side, msg.price, msg.size)
            return

        if kind in (EventType.EXEC_HIDDEN, EventType.CROSS, EventType.HALT):
            return                       # no visible-book effect; counted

        if kind is EventType.EXEC:
            self._evict_better_than_exec(side, msg.price)

        # REDUCE / DELETE / EXEC all remove msg.size shares from a
        # specific resting order (DELETE removes what remains of it)
        if self._known(msg.order_id):
            resting = self.book.order(msg.order_id).qty
            if kind is EventType.DELETE:
                self.book.cancel(msg.order_id)
            elif msg.size < resting:
                self.book.reduce(msg.order_id, msg.size)
            elif msg.size == resting:
                self.book.cancel(msg.order_id)
            else:
                raise ValueError(f"{kind.name} of {msg.size} exceeds "
                                 f"resting {resting} on order "
                                 f"{msg.order_id}")
            return

        self._dark_remove(side, msg.price, msg.size)

    def _dark_remove(self, side: Side, price: int, size: int) -> None:
        """An operation on liquidity we never saw added: pre-window or
        below-K. Take it out of the synthetic order at that price. What
        is not there to take is counted, never invented."""
        self.stats.dark_ops += 1
        synth_id = self._synth_at.get((side, price))
        if synth_id is None or not self._known(synth_id):
            self.stats.unresolved += 1
            self.stats.unresolved_shares += size
            return
        resting = self.book.order(synth_id).qty
        if size < resting:
            self.book.reduce(synth_id, size)
        else:
            self.book.cancel(synth_id)
            del self._synth_at[(side, price)]
            if size > resting:
                self.stats.unresolved += 1
                self.stats.unresolved_shares += size - resting

    # ------------------------------------------------------------- driving
    def run(self, messages: list[LobsterMessage],
            start_index: int = 1) -> ReplayStats:
        """Apply messages from `start_index` (the seed already contains
        message 0's effect)."""
        for i in range(start_index, len(messages)):
            self.apply(i, messages[i])
        return self.stats
