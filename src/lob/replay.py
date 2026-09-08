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
    delete_size_disagreements: int = 0   # known DELETE, size != our resting
    anomalies: list = field(default_factory=list)   # (index, msg, error)

    def count(self, kind: EventType) -> None:
        self.by_type[kind.name] = self.by_type.get(kind.name, 0) + 1


class LobsterReplayer:
    """Folds LOBSTER messages into a book, tracking everything it does."""

    def __init__(self, book: Book, levels: int = 10) -> None:
        self.book = book
        self.levels = levels
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

    def _trim_by_witness(self, side: Side, price: int) -> None:
        """The file's own inclusion contract, used as evidence: every
        message in a K-level file concerns a price inside the true book's
        top K, so a message at `price` certifies that at most K-1 true
        levels sit better than it on that side. If our book holds more,
        the surplus is provably stale; the worst of the better levels
        (nearest the witness price) are evicted, since the touch is
        re-certified constantly and mid-book staleness is where filtered
        removals accumulate. Message-stream truth only."""
        prices = self.book.prices(side)          # best first
        if side is Side.BID:
            better = [p for p in prices if p > price]
        else:
            better = [p for p in prices if p < price]
        excess = len(better) - (self.levels - 1)
        if excess <= 0:
            return
        # choose victims on evidence: synthetic seed liquidity is the
        # likeliest stale, so it goes first (nearest the witness price);
        # real levels only if the count still demands it
        synth = [p for p in reversed(better)
                 if (side, p) in self._synth_at]
        real = [p for p in reversed(better)
                if (side, p) not in self._synth_at]
        for p in (synth + real)[:excess]:
            self._evict(side, p)

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

        if kind in (EventType.EXEC_HIDDEN, EventType.CROSS, EventType.HALT):
            return                       # no visible-book effect; counted

        self._trim_by_witness(side, msg.price)

        if kind is EventType.ADD:
            self._evict_crossed_by_add(side, msg.price)
            self.book.add(msg.order_id, side, msg.price, msg.size)
            return

        if kind is EventType.EXEC:
            self._evict_better_than_exec(side, msg.price)

        # REDUCE / DELETE / EXEC all remove msg.size shares from a
        # specific resting order (DELETE removes what remains of it)
        if self._known(msg.order_id):
            resting = self.book.order(msg.order_id).qty
            if kind is EventType.DELETE:
                if msg.size != resting:
                    self.stats.delete_size_disagreements += 1
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


# ---------------------------------------------------------- the validator
@dataclass(frozen=True)
class Mismatch:
    """One divergence from the reference, with everything a debugging
    session needs on one screen: which message, what it was, and the two
    books side by side."""
    index: int
    time_ns: int
    msg: LobsterMessage
    ours_asks: tuple
    ours_bids: tuple
    ref_asks: tuple
    ref_bids: tuple

    def render(self) -> str:
        s = self.time_ns // 1_000_000_000
        hms = f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"
        m = self.msg
        lines = [f"msg {self.index} at {hms}: {m.kind.name} "
                 f"id {m.order_id} @ {m.price} x{m.size} dir {m.direction}"]
        for name, ours, ref in (("asks", self.ours_asks, self.ref_asks),
                                ("bids", self.ours_bids, self.ref_bids)):
            if ours != ref:
                lines.append(f"  {name} ours: {list(ours)[:4]}")
                lines.append(f"  {name} ref : {list(ref)[:4]}")
        return "\n".join(lines)


@dataclass
class ValidationResult:
    total: int = 0
    matched: int = 0
    first_mismatches: list = field(default_factory=list)
    streaks: int = 0            # runs of consecutive mismatching rows
    longest_streak: int = 0
    first_divergence: int | None = None
    matched_at_depth: dict = field(default_factory=dict)   # J -> rows exact to J
    first_bad_depth: dict = field(default_factory=dict)    # J -> mismatches first seen at J
    divergence_classes: dict = field(default_factory=dict) # class -> rows
    hourly: dict = field(default_factory=dict)  # hour -> [rows, full, top1]

    @property
    def match_rate(self) -> float:
        return self.matched / self.total if self.total else 0.0


def classify_divergence(ours: tuple, refs: tuple, side: Side) -> tuple[int, str] | None:
    """Name the first divergence between our side and the reference side.

    Every class is a face of the same information limit -- a level-filtered
    file reports nothing that happens below the band:

    - BACKFILL: the reference shows a level our book has never heard of
      (rested below the band at the seed, or arrived below it, silently).
    - BACKFILL_SIZE: a shared price where the reference holds MORE than we
      do; the extra shares arrived below the band.
    - STALE: we carry a level the reference says is gone; its removal was
      filtered while it sat below the band (seed synthetics and real
      buried orders alike).
    - STALE_SIZE: a shared price where we hold MORE; the difference left
      below the band.

    Returns (rank, class) for the first divergent rank, or None if the
    side matches. By construction every divergence lands in one of these
    four; per-order correctness is guarded separately (anomalies, the
    delete-size counter, and the invariant suite)."""
    n = max(len(ours), len(refs))
    for i in range(n):
        o = ours[i] if i < len(ours) else None
        r = refs[i] if i < len(refs) else None
        if o == r:
            continue
        if o is None:
            return i + 1, "BACKFILL"
        if r is None:
            return i + 1, "STALE"
        (op, osz), (rp, rsz) = o, r
        if op == rp:
            return i + 1, ("BACKFILL_SIZE" if rsz > osz else "STALE_SIZE")
        ours_better = (op > rp) if side is Side.BID else (op < rp)
        return i + 1, ("STALE" if ours_better else "BACKFILL")
    return None


def validate_row(book: Book, ref, levels: int) -> tuple[bool, tuple, tuple]:
    """Exact integer comparison of our top-K against one reference row.
    Returns (matched, ours_asks, ours_bids); the reference caps at K
    occupied levels and so does our view, so equality means every visible
    price and every visible size agrees."""
    ours_asks = book.top_levels(Side.ASK, levels)
    ours_bids = book.top_levels(Side.BID, levels)
    return (ours_asks == ref.asks and ours_bids == ref.bids,
            ours_asks, ours_bids)


def run_validated(replayer: LobsterReplayer, messages: list[LobsterMessage],
                  reference: list, levels: int,
                  max_reports: int = 8) -> ValidationResult:
    """Apply every message and grade the book against the reference row
    it should produce, message by message. The reference is consulted for
    GRADING only; the book evolves from messages alone."""
    result = ValidationResult()
    result.matched_at_depth = {j: 0 for j in range(1, levels + 1)}
    in_streak = False
    streak_len = 0
    for i in range(1, len(messages)):
        replayer.apply(i, messages[i])
        ok, ours_a, ours_b = validate_row(replayer.book, reference[i],
                                          levels)
        ref_a, ref_b = reference[i].asks, reference[i].bids
        hour = messages[i].time_ns // 3_600_000_000_000
        bucket = result.hourly.setdefault(hour, [0, 0, 0])
        bucket[0] += 1
        if ok:
            bucket[1] += 1
        if (ours_a[:1] == ref_a[:1]) and (ours_b[:1] == ref_b[:1]):
            bucket[2] += 1
        first_bad = None
        for j in range(1, levels + 1):
            if (ours_a[:j] == ref_a[:j]) and (ours_b[:j] == ref_b[:j]):
                result.matched_at_depth[j] += 1
            elif first_bad is None:
                first_bad = j
        if first_bad is not None:
            result.first_bad_depth[first_bad] = \
                result.first_bad_depth.get(first_bad, 0) + 1
            ca = classify_divergence(ours_a, ref_a, Side.ASK)
            cb = classify_divergence(ours_b, ref_b, Side.BID)
            shallowest = min((c for c in (ca, cb) if c is not None),
                             default=None)
            if shallowest is not None:
                name = shallowest[1]
                result.divergence_classes[name] = \
                    result.divergence_classes.get(name, 0) + 1
        result.total += 1
        if ok:
            result.matched += 1
            if in_streak:
                result.longest_streak = max(result.longest_streak,
                                            streak_len)
                in_streak, streak_len = False, 0
        else:
            if not in_streak:
                result.streaks += 1
                in_streak = True
            streak_len += 1
            if result.first_divergence is None:
                result.first_divergence = i
            if len(result.first_mismatches) < max_reports:
                result.first_mismatches.append(Mismatch(
                    index=i, time_ns=messages[i].time_ns, msg=messages[i],
                    ours_asks=ours_a, ours_bids=ours_b,
                    ref_asks=reference[i].asks, ref_bids=reference[i].bids))
    if in_streak:
        result.longest_streak = max(result.longest_streak, streak_len)
    return result
