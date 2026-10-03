"""Order flow imbalance at the touch, exactly as Cont, Kukanov and
Stoikov define it (The Price Impact of Order Book Events, Journal of
Financial Econometrics, 2014).

Week 5's instrument, built on the touch states Week 4 made trustworthy.
Between two consecutive best-quote observations, with best bid price and
size (P^b, q^b) and best ask (P^a, q^a), the event's contribution is

    e_n = 1[P^b_n >= P^b_{n-1}] q^b_n  -  1[P^b_n <= P^b_{n-1}] q^b_{n-1}
        - 1[P^a_n <= P^a_{n-1}] q^a_n  +  1[P^a_n >= P^a_{n-1}] q^a_{n-1}

in shares, and OFI over an interval is the sum of e_n inside it. Read
case by case it is bookkeeping of buy pressure net of sell pressure at
the best quotes. Positive: the best bid grows, a better bid appears (its
whole queue counts, whatever the old queue held), the best ask shrinks
because it was eaten or cancelled, the best ask level empties (its whole
old queue counts). Negative: each mirror image. A message that touches
only deeper levels leaves both best quotes unchanged and contributes
exactly zero, which is why the level-1 and level-10 files of one day
must give the same OFI to the share; ``tests/test_ofi.py`` pins that on
a day we filtered ourselves, the way Day 4's check 1 did.

Units, carried over from ``lob.micro``: prices are integer price units
($0.0001), sizes and e_n are shares, and ``mid2`` is bid plus ask, twice
the mid, kept as an integer so no float enters an accumulator. A change
of ``mid2_n - mid2_{n-1}`` is half that many price units, so that many
divided by 200 in cents; the division happens once, for display.

Observations. Row i of a LOBSTER orderbook file is the book after
message i, so it is the observation at message i's time, and every row
is one observation. Consecutive rows carrying the same timestamp are
consecutive observations too: e_n is not additive across intermediate
states (a bid that steps up twice counts two whole new queues, not one),
so collapsing same-time rows would change the number, and the finest
grain the file offers is the one both level files share. A one-sided or
empty row is not an observation of the best quotes at all: there is no
best quote on the empty side, an OFI across it is undefined, and the row
is skipped and counted, so the next event spans from the last two-sided
observation to the next. Nothing is invented for the missing side.

Two clocks. The events are aggregated two ways and the difference is
the point. Calendar time (fixed bins from 09:30) is the clock a trading
question is asked in and the one the paper used; a bin at the open
holds hundreds of updates and a bin at noon may hold none, and the empty
bin is still an observation (zero flow, zero price change), never a
missing one. Event time (a bucket every N best-quote updates) is the
clock the book runs on: every bucket holds the same number of updates
and their durations vary by orders of magnitude across the day. On
either clock a bucket's price change is its closing mid minus its
opening mid, and its flow is an integer sum; the two series carry the
same total flow, because both tile the same events.

The first look. ``ols`` fits a bucket's price change on its flow
through the origin (the paper's specification) and with an intercept,
and ``depth_scaling`` repeats the origin fit per window beside the
window's time-weighted touch depth, then fits log beta on log depth
across the windows. Both are descriptive: the flow and the price change
come from the same interval, and the caller logs every fit in the trial
registry before it prints one (``scripts/ofi_first_look.py``).
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable, Iterator

from lob.micro import CLOSE_NS, INCREMENT, OPEN_NS

BPS = 10_000                     # basis points in one unit of relative change


@dataclass(frozen=True)
class Touch:
    """One observation of the best quotes. An absent side is None on
    both of its fields; such a touch is counted, never used."""
    time_ns: int
    bid: int | None
    bid_size: int | None
    ask: int | None
    ask_size: int | None

    @property
    def two_sided(self) -> bool:
        return self.bid is not None and self.ask is not None

    @property
    def mid2(self) -> int:
        """Twice the mid, bid + ask, in integer price units."""
        if not self.two_sided:
            raise ValueError("a one-sided book has no mid")
        return self.bid + self.ask


@dataclass(frozen=True)
class Event:
    """e_n between two consecutive two-sided observations: the time and
    twice-mid of both, so every row of the events file is a complete
    transition on its own, and the flow in shares between them."""
    time_prev_ns: int       # the earlier observation's time
    time_ns: int            # the later observation's time
    e: int                  # shares, signed
    mid2_prev: int
    mid2: int

    @property
    def dmid_cents(self) -> float:
        return (self.mid2 - self.mid2_prev) / (2 * INCREMENT)


def touches(messages, reference) -> Iterator[Touch]:
    """Row i of the orderbook file, paired with message i's time. The
    files must align 1:1, as in ``lob.micro.states``."""
    n = len(messages)
    if len(reference) != n:
        raise ValueError(f"{n} messages but {len(reference)} reference "
                         f"rows; the files must align 1:1")
    for m, r in zip(messages, reference):
        bid, bid_size = r.bids[0] if r.bids else (None, None)
        ask, ask_size = r.asks[0] if r.asks else (None, None)
        yield Touch(m.time_ns, bid, bid_size, ask, ask_size)


def ofi_event(prev: Touch, cur: Touch) -> int:
    """The four-term formula, exactly, in shares. Both observations must
    be two-sided; an OFI across an empty side is undefined and the
    caller skips the observation instead of inventing a side."""
    if not (prev.two_sided and cur.two_sided):
        raise ValueError("OFI across a one-sided book is undefined; skip "
                         "the observation, do not invent a side")
    e = 0
    if cur.bid >= prev.bid:          # bid held or improved: the new queue
        e += cur.bid_size
    if cur.bid <= prev.bid:          # bid held or fell: the old queue
        e -= prev.bid_size
    if cur.ask <= prev.ask:          # ask held or improved: the new queue
        e -= cur.ask_size
    if cur.ask >= prev.ask:          # ask held or rose: the old queue
        e += prev.ask_size
    return e


def events(touches: Iterable[Touch],
           skipped: list[Touch] | None = None) -> Iterator[Event]:
    """One Event per consecutive pair of two-sided observations. A
    one-sided or empty touch is appended to ``skipped`` (when a list is
    given) and used as neither end of an event, so the chain telescopes:
    every event's ``time_prev_ns`` and ``mid2_prev`` are the previous
    event's ``time_ns`` and ``mid2``."""
    prev: Touch | None = None
    for t in touches:
        if not t.two_sided:
            if skipped is not None:
                skipped.append(t)
            continue
        if prev is not None:
            yield Event(prev.time_ns, t.time_ns, ofi_event(prev, t),
                        prev.mid2, t.mid2)
        prev = t


def day_summary(events: Iterable[Event], skipped: int = 0) -> dict:
    """Counts and integer sums over the event stream: how many events,
    how many were zero, the sign balance, the net and gross flow in
    shares, the largest single event with its time (a sanity row, not a
    statistic), and the mid at both ends. Ratios are derived once, at
    the end, and are None when undefined."""
    n = zeros = pos = neg = 0
    total = gross = buy = sell = 0
    largest: Event | None = None
    first_mid2 = last_mid2 = None
    for ev in events:
        n += 1
        if first_mid2 is None:
            first_mid2 = ev.mid2_prev
        last_mid2 = ev.mid2
        e = ev.e
        total += e
        gross += abs(e)
        if e == 0:
            zeros += 1
        elif e > 0:
            pos += 1
            buy += e
        else:
            neg += 1
            sell -= e
        if largest is None or abs(e) > abs(largest.e):
            largest = ev
    return {
        "events": n,
        "zero_events": zeros,
        "zero_share": zeros / n if n else None,
        "positive": pos,
        "negative": neg,
        "sum": total,                       # net flow, shares
        "abs_sum": gross,                   # gross flow, shares
        "buy_shares": buy,                  # sum of positive e_n
        "sell_shares": sell,                # sum of |e_n| over negative e_n
        "net_share": abs(total) / gross if gross else None,
        "largest_e": None if largest is None else largest.e,
        "largest_time_ns": None if largest is None else largest.time_ns,
        "first_mid2": first_mid2,
        "last_mid2": last_mid2,
        "mid_change_cents": (None if first_mid2 is None else
                             (last_mid2 - first_mid2) / (2 * INCREMENT)),
        "mid_change_bps": (None if not first_mid2 else
                           BPS * (last_mid2 - first_mid2) / first_mid2),
        "skipped": skipped,
    }


# ------------------------------------------------------------ two clocks
@dataclass
class Bucket:
    """OFI over one interval of either clock, as integer sums plus the
    twice-mid at both ends.

    Calendar clock: ``start_ns`` and ``end_ns`` are the bin's edges, and
    a bin with no observation inside still exists, with zero flow and
    ``mid2_open == mid2_close`` (the mid did not move), so the series is
    complete and a regression's n is honest. Event clock: a bucket is
    every ``n`` best-quote updates, and it runs from the observation
    whose mid is its open (the previous bucket's last update, or the
    day's first observation) to its own last update, so consecutive
    buckets tile the day with no gaps. On either clock the price change
    is the close minus the open, never a sum of per-event changes."""
    index: int
    start_ns: int
    end_ns: int
    n_events: int = 0            # observations inside, zero events included
    n_updates: int = 0           # best-quote updates inside: nonzero e_n
    ofi: int = 0                 # sum of e_n, shares
    abs_flow: int = 0            # sum of |e_n|, shares
    mid2_open: int | None = None
    mid2_close: int | None = None

    @property
    def duration_ns(self) -> int:
        return self.end_ns - self.start_ns

    @property
    def dmid_cents(self) -> float | None:
        if self.mid2_open is None or self.mid2_close is None:
            return None
        return (self.mid2_close - self.mid2_open) / (2 * INCREMENT)

    @property
    def dmid_bps(self) -> float | None:
        """Relative mid change in basis points: 1e4 * (mid_close -
        mid_open) / mid_open, which is the same ratio of the twice-mids."""
        if not self.mid2_open or self.mid2_close is None:
            return None
        return BPS * (self.mid2_close - self.mid2_open) / self.mid2_open

    def _take(self, ev: Event) -> None:
        self.n_events += 1
        if ev.e:
            self.n_updates += 1
        self.ofi += ev.e
        self.abs_flow += abs(ev.e)


BUCKET_COLUMNS = ("n_events", "n_updates", "ofi", "abs_flow",
                  "mid2_open", "mid2_close")


def _in_order(events: Iterable[Event]) -> Iterator[Event]:
    """Events must arrive in time order, like the rows they came from;
    a stream that runs backwards is a wrong stream, not a quirk."""
    last = None
    for ev in events:
        if last is not None and ev.time_ns < last:
            raise ValueError(f"events out of order: {ev.time_ns} after {last}")
        last = ev.time_ns
        yield ev


def aggregate_calendar(events: Iterable[Event], width_ns: int,
                       open_ns: int = OPEN_NS,
                       close_ns: int = CLOSE_NS) -> list[Bucket]:
    """Fixed bins of ``width_ns`` from ``open_ns``; the last bin ends at
    ``close_ns``. Each bin's open is the mid prevailing at its start,
    which is the last observation before it (the day's first observation
    for the bins before any event, so the mid is taken to hold from the
    open until it is first seen); its close is the last observation
    inside it, or the open again when there is none. Events outside
    [open, close) move the running mid but are not counted, so a caller
    can see how many fell outside by comparing counts."""
    if width_ns <= 0:
        raise ValueError("width_ns must be positive")
    n_bins = -(-(close_ns - open_ns) // width_ns)                 # ceiling
    bins = [Bucket(i, open_ns + i * width_ns,
                   min(open_ns + (i + 1) * width_ns, close_ns))
            for i in range(n_bins)]
    stream = _in_order(events)
    ev = next(stream, None)
    mid2 = ev.mid2_prev if ev is not None else None
    while ev is not None and ev.time_ns < open_ns:
        mid2 = ev.mid2
        ev = next(stream, None)
    for b in bins:
        b.mid2_open = mid2
        while ev is not None and ev.time_ns < b.end_ns:
            b._take(ev)
            mid2 = ev.mid2
            ev = next(stream, None)
        b.mid2_close = mid2
    return bins


def aggregate_events(events: Iterable[Event], n_per_bucket: int) -> list[Bucket]:
    """A bucket every ``n_per_bucket`` best-quote updates (nonzero e_n),
    in order. Zero events (hidden executions, messages below the touch)
    are counted in whichever bucket is open when they arrive and move
    nothing. The trailing partial bucket is dropped; the caller sees how
    much by comparing counts, and says so."""
    if n_per_bucket <= 0:
        raise ValueError("n_per_bucket must be positive")
    out: list[Bucket] = []
    cur: Bucket | None = None
    for ev in _in_order(events):
        if cur is None:
            cur = Bucket(len(out), ev.time_prev_ns, ev.time_ns,
                         mid2_open=ev.mid2_prev)
        cur._take(ev)
        cur.end_ns = ev.time_ns
        cur.mid2_close = ev.mid2
        if cur.n_updates == n_per_bucket:
            out.append(cur)
            cur = None
    return out


# --------------------------------------------------------- the first look
@dataclass(frozen=True)
class Fit:
    """One contemporaneous regression of price change on flow, both
    ways the question is asked. ``beta0`` is the slope through the
    origin (the paper's specification, sum xy over sum x squared) and
    ``beta``/``alpha`` the slope and intercept of the ordinary fit.
    Both R-squareds are one minus the residual sum over the CENTERED
    total sum, so they are comparable and the origin fit's can never
    exceed the intercept fit's. The hit rate is the share of
    observations where flow and price change have the same sign, over
    those where both are nonzero; ``excluded`` counts the rest."""
    n: int
    beta0: float | None
    r2_0: float | None
    beta: float | None
    alpha: float | None
    r2: float | None
    hits: int
    both_nonzero: int
    excluded: int

    @property
    def hit_rate(self) -> float | None:
        return self.hits / self.both_nonzero if self.both_nonzero else None


def ols(xs, ys) -> Fit:
    """Pure Python, two passes, no library. Slopes are None when the
    flow never varies; R-squared is None when the price never does."""
    xs, ys = list(xs), list(ys)
    n = len(xs)
    if n != len(ys):
        raise ValueError("xs and ys differ in length")
    hits = both = 0
    for x, y in zip(xs, ys):
        if x and y:
            both += 1
            if (x > 0) == (y > 0):
                hits += 1
    if n == 0:
        return Fit(0, None, None, None, None, None, 0, 0, 0)
    sx = sum(xs); sy = sum(ys)
    sxx = sum(x * x for x in xs); sxy = sum(x * y for x, y in zip(xs, ys))
    my = sy / n
    sst = sum((y - my) ** 2 for y in ys)
    beta0 = sxy / sxx if sxx else None
    r2_0 = None
    if beta0 is not None and sst:
        r2_0 = 1 - sum((y - beta0 * x) ** 2 for x, y in zip(xs, ys)) / sst
    mx = sx / n
    sxx_c = sxx - n * mx * mx
    beta = alpha = r2 = None
    if sxx_c > 0:
        beta = (sxy - n * mx * my) / sxx_c
        alpha = my - beta * mx
        if sst:
            r2 = 1 - sum((y - alpha - beta * x) ** 2 for x, y in zip(xs, ys)) / sst
    return Fit(n, beta0, r2_0, beta, alpha, r2, hits, both, n - both)


@dataclass(frozen=True)
class DepthRow:
    """One window of the depth scaling: the origin slope and its
    R-squared over the window's buckets, beside the window's mean touch
    depth per side (time-weighted, from ``lob.micro.time_weighted``)."""
    index: int
    start_ns: int
    end_ns: int
    n: int
    beta0: float | None
    r2_0: float | None
    depth: float | None


@dataclass(frozen=True)
class Scaling:
    """log beta0 against log depth across the windows: ``slope`` is the
    elasticity the paper puts near minus one. Windows without a positive
    slope or a depth are left out and counted."""
    rows: tuple
    n_used: int
    n_skipped: int
    slope: float | None
    intercept: float | None
    r2: float | None


def depth_scaling(buckets: Iterable[Bucket], depth_by_window,
                  window_ns: int = 1800 * 10**9,
                  open_ns: int = OPEN_NS) -> Scaling:
    """Per window of ``window_ns`` from ``open_ns``: the regression of
    each bucket's price change (cents) on its flow (shares) through the
    origin, over the buckets starting inside the window, beside the
    window's depth from ``depth_by_window[index]``; then the log-log
    slope across windows."""
    groups: dict[int, list[Bucket]] = {}
    for b in buckets:
        groups.setdefault((b.start_ns - open_ns) // window_ns, []).append(b)
    rows = []
    for i in range(len(depth_by_window)):
        bs = groups.get(i, [])
        fit = ols([b.ofi for b in bs], [b.dmid_cents for b in bs])
        rows.append(DepthRow(i, open_ns + i * window_ns, open_ns + (i + 1) * window_ns,
                             fit.n, fit.beta0, fit.r2_0, depth_by_window[i]))
    pts = [(math.log(r.depth), math.log(r.beta0)) for r in rows
           if r.beta0 is not None and r.beta0 > 0 and r.depth]
    skipped = len(rows) - len(pts)
    if len(pts) < 3:
        return Scaling(tuple(rows), len(pts), skipped, None, None, None)
    fit = ols([p[0] for p in pts], [p[1] for p in pts])
    return Scaling(tuple(rows), len(pts), skipped, fit.beta, fit.alpha, fit.r2)
