"""Spreads from consolidated quotes and trades: quoted, effective, realized.

Week 7's bridge to the other repo. The backtester charges a transaction
cost in basis points per side; this module measures what a taker paid,
from TAQ's consolidated quotes and trades, so the assumption can be
audited against the market it stands in for. Everything is in basis
points of the prevailing mid, because the names span $5 to $600 and
cents do not compare across them.

Definitions, the standard ones, written down so the numbers have a
denominator:

- The **prevailing quote** for a trade at time t is the last NBBO with a
  time strictly before t (a quote with the same timestamp is ambiguous
  and is not used), two-sided, with a positive spread.
- The **mid** M is (bid + ask) / 2; the **quoted spread** is
  (ask - bid) / M, time-weighted over the session the way Day 4 weighs
  book states: each quote holds until the next, the last until the
  close, time outside the window dropped, one-sided or crossed quotes
  counted and excluded.
- The **trade direction** q is Lee and Ready's: +1 above the prevailing
  mid, -1 below it, and at the mid the tick test (up from the previous
  trade's price is a buy, down a sell, unchanged takes the sign of the
  last nonzero change before it); 0 when nothing decides, and the
  unsigned share is reported beside every spread.
- The **effective spread** of a signed trade is 2 q (P - M) / M, twice
  what the taker paid against the mid; the **realized spread** is
  2 q (P - M_{t+h}) / M with M_{t+h} the prevailing mid h later (five
  minutes by convention), what the liquidity provider kept; the
  **price impact** is their difference, 2 q (M_{t+h} - M) / M.
- Per name the spreads are averaged two ways, simply and weighted by
  dollar volume, because a few large trades at a wide spread are a
  different fact from many small ones.

Prices arrive as floats from TAQ and are used as floats here; this is
the one module in the project where the integer-tick law does not hold,
because the data is already decimal dollars and nothing is accumulated
across a day that a float would corrupt at the fourth decimal.
"""
from __future__ import annotations

import bisect
from dataclasses import dataclass
from typing import Iterable

NS = 10**9
BPS = 10_000
FIVE_MIN_NS = 300 * NS


@dataclass(frozen=True)
class Quote:
    time_ns: int
    bid: float
    ask: float

    @property
    def two_sided(self) -> bool:
        return self.bid > 0 and self.ask > 0 and self.ask > self.bid

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2


@dataclass(frozen=True)
class Trade:
    time_ns: int
    price: float
    size: int


class QuoteBook:
    """Quotes in time order, with the prevailing-quote lookup done once
    per call through a prebuilt time index."""

    def __init__(self, quotes: Iterable[Quote]):
        self.quotes = sorted(quotes, key=lambda q: q.time_ns)
        self.times = [q.time_ns for q in self.quotes]

    def at(self, time_ns: int) -> Quote | None:
        i = bisect.bisect_left(self.times, time_ns)
        return self.quotes[i - 1] if i > 0 else None

    def mid_at(self, time_ns: int) -> float | None:
        q = self.at(time_ns)
        return q.mid if q is not None and q.two_sided else None


def quoted_spread_bps(quotes: Iterable[Quote], open_ns: int, close_ns: int) -> dict:
    """Time-weighted quoted spread over [open, close) in basis points of
    the time-weighted mid (the ratio of two time integrals, as in Day
    4), plus the time two-sided and the time excluded."""
    qs = sorted(quotes, key=lambda q: q.time_ns)
    spread_x = mid_x = 0.0
    two_sided_ns = excluded_ns = 0
    for i, q in enumerate(qs):
        start = max(q.time_ns, open_ns)
        end = min(qs[i + 1].time_ns if i + 1 < len(qs) else close_ns, close_ns)
        if end <= start:
            continue
        hold = end - start
        if q.two_sided:
            two_sided_ns += hold
            spread_x += (q.ask - q.bid) * hold
            mid_x += q.mid * hold
        else:
            excluded_ns += hold
    bps = BPS * spread_x / mid_x if mid_x else None
    cents = 100 * spread_x / two_sided_ns if two_sided_ns else None
    return {"quoted_bps": bps, "quoted_cents": cents, "two_sided_ns": two_sided_ns,
            "excluded_ns": excluded_ns,
            "mid": (mid_x / two_sided_ns) if two_sided_ns else None}


def sign_trades(trades: list[Trade], book: QuoteBook) -> list[int]:
    """Lee-Ready: +1 above the prevailing mid, -1 below, the tick test
    at the mid (the last nonzero price change among earlier trades), 0
    when there is no prevailing two-sided quote or no earlier change."""
    signs = []
    last_tick = 0                      # the last nonzero change before this trade
    prev_price = None
    for t in trades:
        mid = book.mid_at(t.time_ns)
        if mid is None:
            s = 0
        elif t.price > mid:
            s = 1
        elif t.price < mid:
            s = -1
        elif prev_price is None:
            s = 0
        elif t.price > prev_price:      # the tick test: an uptick
            s = 1
        elif t.price < prev_price:
            s = -1
        else:                           # a zero tick: the last change decides
            s = last_tick
        signs.append(s)
        if prev_price is not None and t.price != prev_price:
            last_tick = 1 if t.price > prev_price else -1
        prev_price = t.price
    return signs


@dataclass(frozen=True)
class TradeSpread:
    time_ns: int
    price: float
    size: int
    sign: int
    mid: float
    effective_bps: float
    realized_bps: float | None       # None when no mid is available h later
    impact_bps: float | None


def trade_spreads(trades: list[Trade], book: QuoteBook, signs: list[int],
                  horizon_ns: int = FIVE_MIN_NS,
                  close_ns: int | None = None) -> list[TradeSpread]:
    """Effective, realized and impact per signed trade. The realized
    spread needs a two-sided mid ``horizon_ns`` later and inside the
    session (``close_ns``); without one it is None, and the caller
    reports how many that was."""
    out = []
    for t, s in zip(trades, signs):
        if s == 0:
            continue
        mid = book.mid_at(t.time_ns)
        if mid is None:
            continue
        eff = 2 * s * (t.price - mid) / mid * BPS
        later = t.time_ns + horizon_ns
        mid_h = None
        if close_ns is None or later < close_ns:
            mid_h = book.mid_at(later)
        real = imp = None
        if mid_h is not None:
            real = 2 * s * (t.price - mid_h) / mid * BPS
            imp = 2 * s * (mid_h - mid) / mid * BPS
        out.append(TradeSpread(t.time_ns, t.price, t.size, s, mid, eff, real, imp))
    return out


def _mean(xs) -> float | None:
    xs = list(xs)
    return sum(xs) / len(xs) if xs else None


def _wmean(pairs) -> float | None:
    pairs = list(pairs)
    w = sum(p[1] for p in pairs)
    return sum(x * wt for x, wt in pairs) / w if w else None


def summarize_name(trades: list[Trade], quotes: Iterable[Quote], open_ns: int,
                   close_ns: int, horizon_ns: int = FIVE_MIN_NS) -> dict:
    """One name's day: the quoted spread, the effective, realized and
    impact spreads (simple and dollar-weighted means), the unsigned
    share, the share without a realized spread, counts and dollar
    volume. Trades outside [open, close) are dropped first."""
    book = QuoteBook(quotes)
    inside = [t for t in sorted(trades, key=lambda t: t.time_ns)
              if open_ns <= t.time_ns < close_ns]
    signs = sign_trades(inside, book)
    spreads = trade_spreads(inside, book, signs, horizon_ns, close_ns)
    quoted = quoted_spread_bps(book.quotes, open_ns, close_ns)
    dollars = [(s, s.price * s.size) for s in spreads]
    with_real = [(s, d) for s, d in dollars if s.realized_bps is not None]
    n = len(inside)
    out = dict(quoted)
    out.update({
        "trades": n,
        "signed": len(spreads),
        "unsigned_share": (n - len(spreads)) / n if n else None,
        "no_realized_share": (len(spreads) - len(with_real)) / len(spreads) if spreads else None,
        "dollar_volume": sum(t.price * t.size for t in inside),
        "shares": sum(t.size for t in inside),
        "effective_bps": _mean(s.effective_bps for s in spreads),
        "effective_bps_dw": _wmean((s.effective_bps, d) for s, d in dollars),
        "realized_bps": _mean(s.realized_bps for s, _ in with_real),
        "realized_bps_dw": _wmean((s.realized_bps, d) for s, d in with_real),
        "impact_bps": _mean(s.impact_bps for s, _ in with_real),
        "impact_bps_dw": _wmean((s.impact_bps, d) for s, d in with_real),
        "buy_share": (sum(1 for s in spreads if s.sign > 0) / len(spreads)) if spreads else None,
    })
    return out
