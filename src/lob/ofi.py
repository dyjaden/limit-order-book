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
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Iterator

from lob.micro import INCREMENT

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
    """e_n between two consecutive two-sided observations, with the
    twice-mid before and after, so every row of the events file stands
    on its own and a bucket's price change can be checked against the
    sum of its events' changes."""
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
    given) and used as neither end of an event, so the chain of mid2
    values telescopes: every event's ``mid2_prev`` is the previous
    event's ``mid2``."""
    prev: Touch | None = None
    for t in touches:
        if not t.two_sided:
            if skipped is not None:
                skipped.append(t)
            continue
        if prev is not None:
            yield Event(t.time_ns, ofi_event(prev, t), prev.mid2, t.mid2)
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
