"""Nasdaq TotalView-ITCH 5.0: the real wire format, parsed exactly.

A sample file is messages back to back, each preceded by a 2-byte
big-endian length. No network headers survive in the file form.
Everything multi-byte is big-endian; timestamps are 48-bit nanoseconds
since midnight (6 bytes, padded to 8 for unpacking); prices are integers
in units of $0.0001, which is this project's tick law arriving from a
third independent source with zero conversion.

Only the book-relevant types are decoded (the table below). Everything
else is SKIPPED BY LENGTH and counted: the framing tells us how long a
message is whether or not we understand it, and skipping precisely what
you do not understand is what the length prefix is for. Guessing is how
parsers corrupt books silently.

| type | meaning                          | book effect (Week 3 replayer) |
|------|----------------------------------|-------------------------------|
| A/F  | add order (anon / attributed)    | add                           |
| X    | cancel PART of an order          | reduce (keeps queue position) |
| D    | delete an order                  | cancel                        |
| E    | execute against a visible order  | shares off THAT order         |
| C    | execute at a different price     | same as E for the book        |
| U    | replace: old id OUT, NEW id in   | cancel + add, id translated   |
| P    | trade against hidden liquidity   | none; counted                 |
| Q/B  | cross trade / broken trade       | none; counted                 |
| S    | system event                     | bracket marker; counted       |
| R    | stock directory                  | the locate -> symbol map      |
| H    | stock trading action (halt etc.) | none; counted                 |

The U message is the one that bites: Nasdaq RETIRES the old order
reference and issues a new one. A parser that loses that translation
turns every later X/D/E on the new id into a phantom "unknown," which on
unfiltered data means a bug, not dark liquidity.
"""
from __future__ import annotations

import struct
from collections import Counter
from dataclasses import dataclass
from typing import BinaryIO, Iterator

TICKS_PER_DOLLAR = 10_000

# body sizes per message type, from the 5.0 specification (type byte
# included). Used to sanity-check framing for the types we decode.
_EXPECTED_LEN = {
    b"S": 12, b"R": 39, b"A": 36, b"F": 40, b"E": 31, b"C": 36,
    b"X": 23, b"D": 19, b"U": 35, b"P": 44, b"Q": 40, b"B": 19, b"H": 25,
}


@dataclass(frozen=True)
class ItchMessage:
    """One decoded message. Fields not carried by a given type are None;
    the `kind` says which ones are meaningful. Deliberately one flat
    type rather than a class per message: the replayer dispatches on
    `kind`, and a flat record keeps that dispatch legible."""
    kind: str                    # 'A', 'F', 'X', 'D', 'E', 'C', 'U', ...
    time_ns: int                 # nanoseconds since midnight
    stock_locate: int
    order_ref: int | None = None
    new_order_ref: int | None = None      # 'U' only
    side: str | None = None               # 'B' or 'S', add messages only
    shares: int | None = None
    price: int | None = None              # integer ticks of $0.0001
    stock: str | None = None              # 'A'/'F'/'R'/'P'
    event_code: str | None = None         # 'S' only


def _ts(body: bytes, off: int) -> int:
    """48-bit big-endian nanoseconds at `off`."""
    return int.from_bytes(body[off:off + 6], "big")


def _stock(body: bytes, off: int) -> str:
    return body[off:off + 8].decode("ascii").rstrip(" ")


class ItchParser:
    """Streams a file of length-framed ITCH 5.0 messages.

    With `symbols` given, only messages for those stocks pass through
    (plus system events, which are market-wide, and the directory
    entries that define the filter itself). The locate map is built
    from 'R' messages as they arrive, which is why the directory must
    precede the day's flow -- in real files it does.
    """

    def __init__(self, symbols: set[str] | None = None) -> None:
        self.symbols = symbols
        self.locates: dict[int, str] = {}
        self.wanted_locates: set[int] = set()
        self.unknown: Counter = Counter()
        self.skipped_by_filter = 0
        self.messages_read = 0

    # ------------------------------------------------------------ decode
    def _decode(self, body: bytes) -> ItchMessage | None:
        kind = body[0:1]
        expected = _EXPECTED_LEN.get(kind)
        if expected is not None and len(body) != expected:
            raise ValueError(f"ITCH {kind!r} message is {len(body)} bytes, "
                             f"spec says {expected}")
        locate = struct.unpack(">H", body[1:3])[0]
        t = _ts(body, 5)

        if kind == b"S":
            return ItchMessage("S", t, locate,
                               event_code=body[11:12].decode("ascii"))
        if kind == b"R":
            stock = _stock(body, 11)
            self.locates[locate] = stock
            if self.symbols is not None and stock in self.symbols:
                self.wanted_locates.add(locate)
            return ItchMessage("R", t, locate, stock=stock)
        if kind in (b"A", b"F"):
            ref, side, shares = struct.unpack(">QcI", body[11:24])
            stock = _stock(body, 24)
            price = struct.unpack(">I", body[32:36])[0]
            return ItchMessage(kind.decode(), t, locate, order_ref=ref,
                               side=side.decode(), shares=shares,
                               price=price, stock=stock)
        if kind == b"E":
            ref, shares = struct.unpack(">QI", body[11:23])
            return ItchMessage("E", t, locate, order_ref=ref, shares=shares)
        if kind == b"C":
            ref, shares = struct.unpack(">QI", body[11:23])
            price = struct.unpack(">I", body[32:36])[0]
            return ItchMessage("C", t, locate, order_ref=ref,
                               shares=shares, price=price)
        if kind == b"X":
            ref, shares = struct.unpack(">QI", body[11:23])
            return ItchMessage("X", t, locate, order_ref=ref, shares=shares)
        if kind == b"D":
            ref = struct.unpack(">Q", body[11:19])[0]
            return ItchMessage("D", t, locate, order_ref=ref)
        if kind == b"U":
            orig, new, shares, price = struct.unpack(">QQII", body[11:35])
            return ItchMessage("U", t, locate, order_ref=orig,
                               new_order_ref=new, shares=shares,
                               price=price)
        if kind == b"P":
            ref, side, shares = struct.unpack(">QcI", body[11:24])
            stock = _stock(body, 24)
            price = struct.unpack(">I", body[32:36])[0]
            return ItchMessage("P", t, locate, order_ref=ref,
                               side=side.decode(), shares=shares,
                               price=price, stock=stock)
        if kind in (b"Q", b"B", b"H"):
            return ItchMessage(kind.decode(), t, locate)

        self.unknown[kind.decode("ascii", "replace")] += 1
        return None                       # skipped by length, counted

    # ------------------------------------------------------------ stream
    def parse(self, stream: BinaryIO) -> Iterator[ItchMessage]:
        """Yield decoded messages from a length-framed stream. Truncated
        framing is loud: a file that ends mid-message is a wrong file."""
        while True:
            head = stream.read(2)
            if not head:
                return
            if len(head) != 2:
                raise ValueError("truncated length prefix at end of stream")
            (length,) = struct.unpack(">H", head)
            body = stream.read(length)
            if len(body) != length:
                raise ValueError(f"truncated message: wanted {length} "
                                 f"bytes, got {len(body)}")
            self.messages_read += 1
            msg = self._decode(body)
            if msg is None:
                continue
            if self.symbols is not None:
                keep = (msg.kind == "S"
                        or (msg.kind == "R"
                            and msg.stock in self.symbols)
                        or msg.stock_locate in self.wanted_locates)
                if not keep:
                    self.skipped_by_filter += 1
                    continue
            yield msg


def describe_itch(stream: BinaryIO,
                  symbols: set[str] | None = None) -> dict:
    """Row A's log, as numbers: counts per type, unknown types, the time
    span, and the symbols seen (or kept, when filtering)."""
    parser = ItchParser(symbols)
    counts: Counter = Counter()
    first = last = None
    stocks: set[str] = set()
    for m in parser.parse(stream):
        counts[m.kind] += 1
        if m.kind not in ("S", "R"):
            first = m.time_ns if first is None else first
            last = m.time_ns
        if m.stock:
            stocks.add(m.stock)

    def hms(ns):
        if ns is None:
            return None
        s = ns // 1_000_000_000
        return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"

    return {
        "messages_read": parser.messages_read,
        "decoded": dict(counts),
        "unknown_skipped": dict(parser.unknown),
        "skipped_by_filter": parser.skipped_by_filter,
        "first": hms(first), "last": hms(last),
        "symbols_seen": sorted(stocks),
    }
