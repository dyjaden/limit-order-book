"""Fabricate a valid TotalView-ITCH 5.0 binary day from the synthetic tape.

    python scripts/make_fake_itch.py                 # ~100k-message day
    python scripts/make_fake_itch.py --n 20000

The fiction-first rule at the binary level: before the parser ever meets
a real multi-gigabyte file, it must round-trip a day WE wrote, where the
expected parse is known by construction because the writer records it
message by message as it emits bytes. If our own writer and reader
disagree, no real file will save us.

The fiction reuses Week 1's synthetic tape (market-shaped adds, cancels,
replaces, executes) and translates it to the wire:

- tape add      -> 'A' (every so often 'F', for attribution coverage)
- tape cancel   -> 'D', or an 'X' partial then 'D' remainder, so the
                   reduce path is exercised on binary data too
- tape replace  -> 'U' with a NEW order reference, exactly as Nasdaq
                   retires the old id; the writer keeps the translation
                   map the way a real session would force a parser to
- tape execute  -> one 'E' per filled resting order (the tape's
                   aggregate demand is resolved to per-order fills by
                   running a real Book alongside, as the exchange would)

System-event brackets ('O','S','Q' ... 'M','E','C') and a stock
directory entry frame the day the way real files are framed.
"""
from __future__ import annotations

import argparse
import io
import struct
from pathlib import Path
from random import Random

from lob.book import Book, Side
from lob.itch import ItchMessage, describe_itch
from make_fake_tape import make_tape

STOCK = "FAKE"
LOCATE = 7
OPEN_NS = 34_200_000_000_000          # 09:30


def _side_char(side: Side) -> bytes:
    return b"B" if side is Side.BID else b"S"


class ItchWriter:
    """Emits framed ITCH 5.0 bytes and records the expected parse."""

    def __init__(self, stock: str = STOCK, locate: int = LOCATE) -> None:
        self.stock = stock
        self.locate = locate
        self.buf = io.BytesIO()
        self.expected: list[ItchMessage] = []

    def _emit(self, body: bytes) -> None:
        self.buf.write(struct.pack(">H", len(body)))
        self.buf.write(body)

    def _hdr(self, kind: bytes, ts: int) -> bytes:
        return (kind + struct.pack(">HH", self.locate, 0)
                + ts.to_bytes(6, "big"))

    def system(self, ts: int, code: str) -> None:
        self._emit(self._hdr(b"S", ts) + code.encode())
        self.expected.append(ItchMessage("S", ts, self.locate,
                                         event_code=code))

    def directory(self, ts: int) -> None:
        stock8 = self.stock.ljust(8).encode()
        body = (self._hdr(b"R", ts) + stock8 + b"Q" + b"N"
                + struct.pack(">I", 100) + b"N" + b"C" + b"  "
                + b"P" + b"N" + b"N" + b"1" + b"N"
                + struct.pack(">I", 0) + b"N")
        self._emit(body)
        self.expected.append(ItchMessage("R", ts, self.locate,
                                         stock=self.stock))

    def add(self, ts: int, ref: int, side: Side, shares: int,
            price: int, attributed: bool = False) -> None:
        kind = b"F" if attributed else b"A"
        body = (self._hdr(kind, ts)
                + struct.pack(">Q", ref) + _side_char(side)
                + struct.pack(">I", shares)
                + self.stock.ljust(8).encode()
                + struct.pack(">I", price))
        if attributed:
            body += b"MPID"
        self._emit(body)
        self.expected.append(ItchMessage(
            kind.decode(), ts, self.locate, order_ref=ref,
            side=_side_char(side).decode(), shares=shares, price=price,
            stock=self.stock))

    def execute(self, ts: int, ref: int, shares: int) -> None:
        body = (self._hdr(b"E", ts) + struct.pack(">QI", ref, shares)
                + struct.pack(">Q", 0))
        self._emit(body)
        self.expected.append(ItchMessage("E", ts, self.locate,
                                         order_ref=ref, shares=shares))

    def cancel_partial(self, ts: int, ref: int, shares: int) -> None:
        body = self._hdr(b"X", ts) + struct.pack(">QI", ref, shares)
        self._emit(body)
        self.expected.append(ItchMessage("X", ts, self.locate,
                                         order_ref=ref, shares=shares))

    def delete(self, ts: int, ref: int) -> None:
        body = self._hdr(b"D", ts) + struct.pack(">Q", ref)
        self._emit(body)
        self.expected.append(ItchMessage("D", ts, self.locate,
                                         order_ref=ref))

    def replace(self, ts: int, orig: int, new: int, shares: int,
                price: int) -> None:
        body = (self._hdr(b"U", ts)
                + struct.pack(">QQII", orig, new, shares, price))
        self._emit(body)
        self.expected.append(ItchMessage("U", ts, self.locate,
                                         order_ref=orig,
                                         new_order_ref=new,
                                         shares=shares, price=price))


def build_fake_day(n_messages: int = 100_000,
                   seed: int = 7) -> tuple[bytes, list[ItchMessage]]:
    """A framed binary day plus its expected parse, from the Week 1 tape.

    Runs a real Book alongside to resolve the tape's aggregate executes
    into per-order fills, and keeps the tape-id to ITCH-reference map
    that 'U' messages make necessary.
    """
    rng = Random(seed + 1)
    tape = make_tape(n_messages, seed)
    w = ItchWriter()
    book = Book()
    ref_of: dict[int, int] = {}          # tape id -> current ITCH ref
    next_ref = 1

    w.system(25_200_000_000_000, "O")    # start of messages
    w.system(28_800_000_000_000, "S")    # start of system hours
    w.directory(28_900_000_000_000)
    w.system(OPEN_NS, "Q")               # start of market hours

    ts = OPEN_NS
    for m in tape:
        ts += rng.randint(1_000, 5_000_000)      # 1us .. 5ms
        if m.kind == "add":
            ref_of[m.order_id] = next_ref
            book.add(m.order_id, m.side, m.price, m.qty)
            w.add(ts, next_ref, m.side, m.qty, m.price,
                  attributed=(next_ref % 97 == 0))
            next_ref += 1
        elif m.kind == "cancel":
            order = book.order(m.order_id)
            ref = ref_of.pop(m.order_id)
            if order.qty > 1 and rng.random() < 0.3:
                part = rng.randint(1, order.qty - 1)
                book.reduce(m.order_id, part)
                w.cancel_partial(ts, ref, part)
                ts += rng.randint(1_000, 50_000)
            book.cancel(m.order_id)
            w.delete(ts, ref)
        elif m.kind == "replace":
            old = ref_of[m.order_id]
            ref_of[m.order_id] = next_ref
            book.replace(m.order_id, m.price, m.qty)
            w.replace(ts, old, next_ref, m.qty, m.price)
            next_ref += 1
        elif m.kind == "execute":
            for fill in book.execute(m.side, m.qty):
                w.execute(ts, ref_of[fill.order_id], fill.qty)
                try:
                    book.order(fill.order_id)
                except KeyError:                  # fully filled
                    del ref_of[fill.order_id]
                ts += rng.randint(100, 5_000)
        else:
            raise ValueError(f"unknown tape kind {m.kind!r}")

    w.system(ts + 1_000_000, "M")        # end of market hours
    w.system(ts + 2_000_000, "E")        # end of system hours
    w.system(ts + 3_000_000, "C")        # end of messages
    return w.buf.getvalue(), w.expected


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=100_000)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--out", default="data/_fake/fake_itch5.bin")
    args = ap.parse_args()

    data, expected = build_fake_day(args.n, args.seed)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(data)
    print(f"SYNTHETIC ITCH 5.0 -- FICTION, for rehearsal only")
    print(f"  wrote {out} ({len(data):,} bytes, "
          f"{len(expected):,} messages expected)")
    import io as _io
    info = describe_itch(_io.BytesIO(data))
    print(f"  parser re-read: {info['messages_read']:,} messages, "
          f"decoded {sum(info['decoded'].values()):,}, "
          f"unknown {sum(info['unknown_skipped'].values())}")
    print(f"  types: " + "  ".join(f"{k}:{v:,}" for k, v in
                                   sorted(info["decoded"].items())))
    print(f"  span {info['first']} to {info['last']}")


if __name__ == "__main__":
    main()
