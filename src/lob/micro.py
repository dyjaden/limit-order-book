"""Descriptive microstructure, time-weighted, from LOBSTER's own book states.

Week 4's instrument. Three weeks built and verified a book; this module
looks at one, and the first decision is WHICH one. The reference
orderbook file is LOBSTER's exact reconstruction of the top K levels,
and Day 2 proved that no replay of a level-filtered file can be exact.
So statistics of the book come from the reference rows, statistics of
orders come from the message file (the only place order identity
lives, see ``lob.lifecycle``), and our replayed book is measured
AGAINST the reference in the Week 4 checks to learn the error budget
it carries on data with no answer key.

The one technique: a book state is a duration, not an event. Row i of
the orderbook file is the book from message i until message i+1, and
the last row holds until the close. Averaging over messages weights the
open, where thousands of messages arrive per second, hundreds of times
more than a quiet minute at noon; the honest average weights each state
by how long it lasted. So every book statistic here is

    sum(value x holding time) / sum(holding time)

accumulated in INTEGER nanoseconds and integer ticks, with the division
done exactly once at the end. Only messages and shares are counted per
event, because they are events. Time-weighted basis points are defined
as the time-weighted spread over the time-weighted mid (a ratio of two
exact integers), not the average of per-state ratios, so no float ever
enters an accumulator.

Same-timestamp rows hold for zero nanoseconds and fall out of every
average by arithmetic. Type-7 (halt) rows duplicate the preceding book
state, per LOBSTER's ReadMe, and need no special case either. A
one-sided or empty book has no spread; its time is counted and
excluded, never averaged as ``9999999999 - bid``.
"""
from __future__ import annotations

from dataclasses import dataclass, fields
from typing import Iterable, Iterator

from lob.lobster import EventType

NS = 10**9
OPEN_NS = 34_200 * NS            # 09:30:00, the first bin starts here
CLOSE_NS = 57_600 * NS           # 16:00:00, the last state holds to here
DEFAULT_BIN_NS = 300 * NS        # five minutes: 78 bins in the session
TICKS_PER_DOLLAR = 10_000        # the project's price unit: $0.0001
INCREMENT = 100                  # the exchange's minimum price increment,
                                 # $0.01, in those units. A "tick" in this
                                 # code base is the unit; the increment is
                                 # what a one-cent spread is measured in


@dataclass(frozen=True)
class State:
    """One book state and how long it lasted."""
    start_ns: int
    hold_ns: int
    asks: tuple[tuple[int, int], ...]     # (price, size), best first
    bids: tuple[tuple[int, int], ...]


def states(messages, reference, close_ns: int = CLOSE_NS) -> Iterator[State]:
    """Pair message i with reference row i and the time until message
    i+1. The final row holds until the close. Holding times can never
    be negative: a file whose clock runs backwards is a wrong file, and
    the clamp keeps it from poisoning a sum silently (it is counted by
    the caller through ``hold_ns == 0``)."""
    n = len(messages)
    if len(reference) != n:
        raise ValueError(f"{n} messages but {len(reference)} reference "
                         f"rows; the files must align 1:1")
    for i in range(n):
        start = messages[i].time_ns
        end = messages[i + 1].time_ns if i + 1 < n else close_ns
        yield State(start, max(0, end - start),
                    reference[i].asks, reference[i].bids)


@dataclass
class BinStats:
    """Integer accumulators for one time bin. Every ratio is derived
    from these, so the CSV can carry the sums and anyone can re-derive
    the floats."""
    index: int
    start_ns: int
    width_ns: int
    label: str = ""
    # time, in nanoseconds
    hold_ns: int = 0             # every state's time inside the bin
    two_sided_ns: int = 0        # both sides occupied
    one_sided_ns: int = 0        # exactly one side occupied
    empty_ns: int = 0            # neither
    # value x time, two-sided states only
    spread_x_ns: int = 0         # spread in ticks x ns
    mid2_x_ns: int = 0           # (bid + ask) in ticks x ns; twice the mid
    one_cent_ns: int = 0         # time the spread was exactly INCREMENT
    touch_x_ns: int = 0          # (best bid size + best ask size) x ns
    depth5_x_ns: int = 0         # shares in the best 5 levels, both sides
    depth10_x_ns: int = 0        # shares in the best 10 levels, both sides
    # events, counted at their own timestamp
    messages: int = 0
    adds: int = 0
    add_shares: int = 0
    cancels: int = 0             # partial (2) and full (3)
    cancel_shares: int = 0
    trades: int = 0              # visible executions (4)
    trade_shares: int = 0
    hidden: int = 0              # hidden executions (5)
    hidden_shares: int = 0

    # ------------------------------------------------------------ ratios
    @property
    def end_ns(self) -> int:
        return self.start_ns + self.width_ns

    @property
    def tw_spread_ticks(self) -> float | None:
        return _ratio(self.spread_x_ns, self.two_sided_ns)

    @property
    def tw_mid_ticks(self) -> float | None:
        return _ratio(self.mid2_x_ns, 2 * self.two_sided_ns)

    @property
    def tw_spread_bps(self) -> float | None:
        """Time-weighted spread over time-weighted mid, in basis points:
        1e4 * (spread_x_ns / T) / (mid2_x_ns / 2T) = 2e4 * spread / mid2."""
        return _ratio(2 * TICKS_PER_DOLLAR * self.spread_x_ns, self.mid2_x_ns)

    @property
    def tw_spread_cents(self) -> float | None:
        return _ratio(self.spread_x_ns, INCREMENT * self.two_sided_ns)

    @property
    def one_cent_share(self) -> float | None:
        """Share of two-sided time at the minimum increment: the
        large-tick signature."""
        return _ratio(self.one_cent_ns, self.two_sided_ns)

    @property
    def tw_touch_depth(self) -> float | None:
        return _ratio(self.touch_x_ns, self.two_sided_ns)

    @property
    def tw_depth5(self) -> float | None:
        return _ratio(self.depth5_x_ns, self.two_sided_ns)

    @property
    def tw_depth10(self) -> float | None:
        return _ratio(self.depth10_x_ns, self.two_sided_ns)

    @property
    def one_sided_share(self) -> float | None:
        return _ratio(self.one_sided_ns + self.empty_ns, self.hold_ns)

    def add_into(self, other: "BinStats") -> None:
        """Fold this bin's accumulators into another (for windows and
        the day). Index and geometry belong to the receiver."""
        for f in fields(self):
            if f.name in _GEOMETRY:
                continue
            setattr(other, f.name, getattr(other, f.name) + getattr(self, f.name))


_GEOMETRY = ("index", "start_ns", "width_ns", "label")
ACCUMULATORS = tuple(f.name for f in fields(BinStats)
                     if f.name not in _GEOMETRY)
RATIOS = ("tw_spread_ticks", "tw_spread_cents", "tw_spread_bps",
          "tw_mid_ticks", "one_cent_share", "tw_touch_depth", "tw_depth5",
          "tw_depth10", "one_sided_share")


def _ratio(num: int, den: int) -> float | None:
    return num / den if den else None


def _side_depth(levels, k: int) -> int:
    return sum(size for _, size in levels[:k])


def _weigh(b: BinStats, s: State, ns: int) -> None:
    """Add `ns` nanoseconds of state `s` to bin `b`."""
    b.hold_ns += ns
    if not s.asks or not s.bids:
        if s.asks or s.bids:
            b.one_sided_ns += ns
        else:
            b.empty_ns += ns
        return
    (ask, ask_sz), (bid, bid_sz) = s.asks[0], s.bids[0]
    spread = ask - bid
    b.two_sided_ns += ns
    b.spread_x_ns += spread * ns
    b.mid2_x_ns += (ask + bid) * ns
    if spread == INCREMENT:
        b.one_cent_ns += ns
    b.touch_x_ns += (ask_sz + bid_sz) * ns
    b.depth5_x_ns += (_side_depth(s.asks, 5) + _side_depth(s.bids, 5)) * ns
    b.depth10_x_ns += (_side_depth(s.asks, 10) + _side_depth(s.bids, 10)) * ns


def _count(b: BinStats, m) -> None:
    b.messages += 1
    k = m.kind
    if k is EventType.ADD:
        b.adds += 1
        b.add_shares += m.size
    elif k in (EventType.REDUCE, EventType.DELETE):
        b.cancels += 1
        b.cancel_shares += m.size
    elif k is EventType.EXEC:
        b.trades += 1
        b.trade_shares += m.size
    elif k is EventType.EXEC_HIDDEN:
        b.hidden += 1
        b.hidden_shares += m.size


def time_weighted(messages, reference, width_ns: int = DEFAULT_BIN_NS,
                  open_ns: int = OPEN_NS,
                  close_ns: int = CLOSE_NS) -> list[BinStats]:
    """The session in bins of `width_ns`, every book statistic weighted
    by holding time and every event counted at its own timestamp.

    A state that straddles a bin edge is SPLIT at the edge and its time
    apportioned; time outside [open, close) is dropped. Events outside
    the window are dropped too, so counts and time describe the same
    session."""
    n_bins = -(-(close_ns - open_ns) // width_ns)        # ceiling
    bins = [BinStats(i, open_ns + i * width_ns, width_ns)
            for i in range(n_bins)]
    for m, s in zip(messages, states(messages, reference, close_ns)):
        t = m.time_ns
        if open_ns <= t < close_ns:
            _count(bins[(t - open_ns) // width_ns], m)
        lo, hi = max(s.start_ns, open_ns), min(s.start_ns + s.hold_ns, close_ns)
        while lo < hi:
            b = bins[(lo - open_ns) // width_ns]
            cut = min(hi, b.end_ns)
            _weigh(b, s, cut - lo)
            lo = cut
    return bins


def window(bins: Iterable[BinStats], start_ns: int, end_ns: int,
           label: str = "") -> BinStats:
    """The accumulators summed over every bin lying wholly inside
    [start, end). Bins straddling the window's edges are left out, so
    the window is exact at the price of being bin-aligned."""
    out = BinStats(-1, start_ns, end_ns - start_ns, label)
    for b in bins:
        if b.start_ns >= start_ns and b.end_ns <= end_ns:
            b.add_into(out)
    return out


def day_summary(bins: list[BinStats], open_ns: int = OPEN_NS,
                close_ns: int = CLOSE_NS) -> dict[str, BinStats]:
    """The whole session plus the three windows the write-up names:
    the first five minutes, midday (11:00 to 14:00), the last five."""
    return {
        "day": window(bins, open_ns, close_ns, "whole session"),
        "open5": window(bins, open_ns, open_ns + 300 * NS,
                        "first five minutes"),
        "midday": window(bins, 39_600 * NS, 50_400 * NS, "11:00 to 14:00"),
        "close5": window(bins, close_ns - 300 * NS, close_ns,
                         "last five minutes"),
    }


def hms(ns: int) -> str:
    s = ns // NS
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


# --------------------------------------------------------- book shape
@dataclass
class LevelProfile:
    """Time-weighted shape of one side by OCCUPIED level rank j = 1..K.

    LOBSTER's levels are occupied prices, so level j's distance from the
    touch is a measurement, not j - 1 cents. Sizes are conditional on
    the level existing (``exists_ns[j]``); ``two_sided_ns`` lets a caller
    turn them into unconditional means (absent = 0 shares)."""
    side: str
    levels: int
    two_sided_ns: int = 0
    exists_ns: list[int] = None                 # per rank, time it existed
    size_x_ns: list[int] = None                 # shares x ns, per rank
    gap_x_ns: list[int] = None                  # price units from level 1 x ns
    adjacent_gaps_x_ns: int = 0                 # sum over j >= 2 of gap_j - gap_{j-1}, x ns
    adjacent_gaps_n_x_ns: int = 0               # number of adjacent gaps x ns
    one_cent_gaps_x_ns: int = 0                 # adjacent gaps == INCREMENT x ns

    def __post_init__(self):
        n = self.levels + 1
        self.exists_ns = [0] * n
        self.size_x_ns = [0] * n
        self.gap_x_ns = [0] * n

    def tw_size(self, j: int) -> float | None:
        return _ratio(self.size_x_ns[j], self.exists_ns[j])

    def tw_gap_cents(self, j: int) -> float | None:
        return _ratio(self.gap_x_ns[j], INCREMENT * self.exists_ns[j])

    def exists_share(self, j: int) -> float | None:
        return _ratio(self.exists_ns[j], self.two_sided_ns)

    @property
    def tw_adjacent_gap_cents(self) -> float | None:
        """Mean cents between consecutive occupied levels: 1.0 on a dense
        book, larger on a sparse one."""
        return _ratio(self.adjacent_gaps_x_ns, INCREMENT * self.adjacent_gaps_n_x_ns)

    @property
    def one_cent_gap_share(self) -> float | None:
        """Share of adjacent-level gaps that are exactly one cent."""
        return _ratio(self.one_cent_gaps_x_ns, self.adjacent_gaps_n_x_ns)


@dataclass
class CentProfile:
    """Time-weighted shares resting exactly d cents behind the best, for
    d = 0..max_cents, the regime-comparable picture in which a sparse
    book shows its holes.

    A level-K file only shows the band from the best to the K-th
    occupied level. Inside that band an unoccupied cent is a known
    zero; beyond it the depth is UNKNOWN, not zero. ``covered_ns[d]`` is
    the time the band reached cent d, so ``tw_size(d)`` is conditional on
    visibility and ``coverage(d)`` says how much of the day that was."""
    side: str
    max_cents: int
    two_sided_ns: int = 0
    covered_ns: list[int] = None
    size_x_ns: list[int] = None

    def __post_init__(self):
        self.covered_ns = [0] * (self.max_cents + 1)
        self.size_x_ns = [0] * (self.max_cents + 1)

    def tw_size(self, d: int) -> float | None:
        return _ratio(self.size_x_ns[d], self.covered_ns[d])

    def coverage(self, d: int) -> float | None:
        return _ratio(self.covered_ns[d], self.two_sided_ns)


def book_shape(states: Iterable[State], levels: int = 10,
               max_cents: int = 20) -> tuple[dict[str, LevelProfile],
                                             dict[str, CentProfile]]:
    """Both profiles for both sides in one pass over two-sided states.
    Sub-cent prices (none on a Reg NMS book above $1) would land on the
    cent they round down to."""
    lv = {"bid": LevelProfile("bid", levels), "ask": LevelProfile("ask", levels)}
    ct = {"bid": CentProfile("bid", max_cents), "ask": CentProfile("ask", max_cents)}
    for s in states:
        ns = s.hold_ns
        if not ns or not s.asks or not s.bids:
            continue
        for side, ladder in (("bid", s.bids), ("ask", s.asks)):
            L, C = lv[side], ct[side]
            L.two_sided_ns += ns
            C.two_sided_ns += ns
            best = ladder[0][0]
            prev_gap = 0
            last_gap_units = 0
            for j, (price, size) in enumerate(ladder[:levels], start=1):
                gap = (best - price) if side == "bid" else (price - best)
                L.exists_ns[j] += ns
                L.size_x_ns[j] += size * ns
                L.gap_x_ns[j] += gap * ns
                if j >= 2:
                    step = gap - prev_gap
                    L.adjacent_gaps_x_ns += step * ns
                    L.adjacent_gaps_n_x_ns += ns
                    if step == INCREMENT:
                        L.one_cent_gaps_x_ns += ns
                prev_gap = gap
                last_gap_units = gap
                d = gap // INCREMENT
                if d <= max_cents:
                    C.size_x_ns[d] += size * ns
            # the band covers every cent from 0 to the last level's cent:
            # record the reach once, and turn it into per-cent coverage
            # with one suffix sum at the end
            C.covered_ns[min(max_cents, last_gap_units // INCREMENT)] += ns
    for C in ct.values():
        acc = 0
        for d in range(C.max_cents, -1, -1):          # covered_ns[d] becomes
            acc += C.covered_ns[d]                    # the time the band
            C.covered_ns[d] = acc                     # reached cent d or beyond
    return lv, ct
