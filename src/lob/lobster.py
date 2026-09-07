"""LOBSTER sample-file loader: messages and the reference orderbook.

LOBSTER publishes pairs of CSVs reconstructed from Nasdaq ITCH. The
message file is the event log; the orderbook file is the book state after
every event, to K levels. The second file is the answer key the replay
gets graded against, row for row.

Format facts this loader encodes, from LOBSTER's own data-structure page:

- Message columns, exactly six: time (seconds after midnight, fractional
  part down to nanoseconds), event type, order id, size, price,
  direction. Direction +1 is a buy order, -1 a sell order.
- Prices are dollars times 10,000: integer ticks of $0.0001. They arrive
  as integers and stay integers; the book's tick law fits this data with
  no conversion at all.
- Time is parsed from the STRING into integer nanoseconds after midnight.
  Never through a float: nanosecond timestamps read as float seconds
  collide and reorder, and an event log that reorders is not a log.
- Orderbook columns come in groups of four per level: ask price, ask
  size, bid price, bid size, best level first. An unoccupied level
  carries dummy values (a huge positive price on the ask side, a huge
  negative one on the bid side, size zero). Dummies decode to ABSENT,
  never to a price; a fake best ask of 9999999999 is exactly the kind of
  plausible-looking poison this project exists to refuse.

Event types, and the two distinctions that matter: type 2 reduces an
order in place and KEEPS its queue position (that is why it exists);
type 3 deletes it. Type 4 executes against a visible resting order;
type 5 executes hidden liquidity and does not change the visible book.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from enum import IntEnum
from pathlib import Path

TICKS_PER_DOLLAR = 10_000
ASK_DUMMY_PRICE = 9_999_999_999
BID_DUMMY_PRICE = -9_999_999_999


class EventType(IntEnum):
    ADD = 1              # new limit order
    REDUCE = 2           # partial cancel: size down, queue position kept
    DELETE = 3           # full cancel
    EXEC = 4             # execution of a visible order
    EXEC_HIDDEN = 5      # execution of hidden liquidity: visible no-op
    CROSS = 6            # auction cross trade: visible no-op
    HALT = 7             # trading halt: visible no-op


@dataclass(frozen=True)
class LobsterMessage:
    """One event: exact time, what happened, to whom, how much, where."""
    time_ns: int         # integer nanoseconds after midnight
    kind: EventType
    order_id: int
    size: int
    price: int           # integer ticks of $0.0001
    direction: int       # +1 buy side, -1 sell side


@dataclass(frozen=True)
class ReferenceRow:
    """The answer key after one message: (price, size) per occupied
    level, best first. Empty levels are simply absent, never dummies."""
    asks: tuple[tuple[int, int], ...]
    bids: tuple[tuple[int, int], ...]


def _time_to_ns(text: str) -> int:
    """'34200.004241176' -> 34200004241176000. String in, integer out;
    the fractional part is right-padded to nine digits."""
    sec, _, frac = text.strip().partition(".")
    frac = (frac + "000000000")[:9]
    return int(sec) * 1_000_000_000 + int(frac)


def read_messages(path: str | Path) -> list[LobsterMessage]:
    """The full message file, typed and validated. Loud on a malformed
    row: a feed file with the wrong shape is a wrong file, not a warning.
    """
    out: list[LobsterMessage] = []
    with open(path, newline="") as f:
        for i, row in enumerate(csv.reader(f)):
            if not row:
                continue
            if len(row) != 6:
                raise ValueError(f"{path}: row {i} has {len(row)} columns, "
                                 f"expected 6")
            kind = EventType(int(row[1]))
            direction = int(row[5])
            if direction not in (-1, 1):
                raise ValueError(f"{path}: row {i} direction {direction} "
                                 f"is not +1/-1")
            out.append(LobsterMessage(
                time_ns=_time_to_ns(row[0]),
                kind=kind,
                order_id=int(row[2]),
                size=int(row[3]),
                price=int(row[4]),
                direction=direction,
            ))
    return out


def read_orderbook(path: str | Path, levels: int) -> list[ReferenceRow]:
    """The reference book states, dummy levels decoded to absent."""
    out: list[ReferenceRow] = []
    want = 4 * levels
    with open(path, newline="") as f:
        for i, row in enumerate(csv.reader(f)):
            if not row:
                continue
            if len(row) != want:
                raise ValueError(f"{path}: row {i} has {len(row)} columns, "
                                 f"expected {want} for {levels} levels")
            asks: list[tuple[int, int]] = []
            bids: list[tuple[int, int]] = []
            for lv in range(levels):
                ap, asz = int(row[4 * lv]), int(row[4 * lv + 1])
                bp, bsz = int(row[4 * lv + 2]), int(row[4 * lv + 3])
                if asz > 0 and ap != ASK_DUMMY_PRICE:
                    asks.append((ap, asz))
                if bsz > 0 and bp != BID_DUMMY_PRICE:
                    bids.append((bp, bsz))
            out.append(ReferenceRow(asks=tuple(asks), bids=tuple(bids)))
    return out


def describe(message_path: str | Path,
             orderbook_path: str | Path | None = None,
             levels: int | None = None) -> dict:
    """The schema note as numbers: counts, type histogram, time span,
    price range, distinct ids. Printed by the caller, kept honest here."""
    msgs = read_messages(message_path)
    hist = {k.name: 0 for k in EventType}
    ids = set()
    lo_px, hi_px = None, None
    for m in msgs:
        hist[m.kind.name] += 1
        ids.add(m.order_id)
        if m.kind is not EventType.HALT:      # halts carry sentinel prices
            lo_px = m.price if lo_px is None else min(lo_px, m.price)
            hi_px = m.price if hi_px is None else max(hi_px, m.price)

    def hms(ns: int) -> str:
        s = ns // 1_000_000_000
        return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"

    info = {
        "messages": len(msgs),
        "types": hist,
        "first": hms(msgs[0].time_ns) if msgs else None,
        "last": hms(msgs[-1].time_ns) if msgs else None,
        "distinct_order_ids": len(ids),
        "price_range_ticks": (lo_px, hi_px),
        "price_range_dollars": (None if lo_px is None else
                                (lo_px / TICKS_PER_DOLLAR,
                                 hi_px / TICKS_PER_DOLLAR)),
    }
    if orderbook_path is not None and levels is not None:
        ref = read_orderbook(orderbook_path, levels)
        info["reference_rows"] = len(ref)
        info["rows_match_messages"] = (len(ref) == len(msgs))
    return info
