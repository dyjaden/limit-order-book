"""Day 3, Step 1: the ITCH parser against hand-built bytes and its own
fiction.

The hand-built fixtures are the 5.0 spec transcribed into struct.pack,
one message type at a time, with known values chosen so that a wrong
offset or a wrong endianness cannot produce the expected answer by luck.
The round-trip test then closes the loop: a full synthetic day written
by our own writer must parse back field for field, because if writer
and reader disagree, no real file will save us.
"""
import io
import struct

import pytest

from lob.itch import ItchParser, describe_itch
from make_fake_itch import build_fake_day

TS = 34_200_123_456_789          # 09:30:00.123456789
LOCATE = 7


def hdr(kind: bytes, locate: int = LOCATE, ts: int = TS) -> bytes:
    return kind + struct.pack(">HH", locate, 0) + ts.to_bytes(6, "big")


def frame(*bodies: bytes) -> io.BytesIO:
    out = b"".join(struct.pack(">H", len(b)) + b for b in bodies)
    return io.BytesIO(out)


def add_body(ref=42, side=b"B", shares=100, price=5_853_300,
             stock=b"AAPL    ", locate=LOCATE) -> bytes:
    return (hdr(b"A", locate) + struct.pack(">Q", ref) + side
            + struct.pack(">I", shares) + stock
            + struct.pack(">I", price))


def parse_all(stream, symbols=None):
    p = ItchParser(symbols)
    return list(p.parse(stream)), p


def test_a_hand_built_add_decodes_field_for_field():
    msgs, _ = parse_all(frame(add_body()))
    (m,) = msgs
    assert m.kind == "A"
    assert m.time_ns == TS                       # 48-bit big-endian, exact
    assert m.stock_locate == LOCATE
    assert m.order_ref == 42
    assert m.side == "B"
    assert m.shares == 100
    assert m.price == 5_853_300                  # ticks: $585.33
    assert m.price / 10_000 == 585.33
    assert m.stock == "AAPL"                     # padding stripped


def test_every_decoded_type_round_trips_from_hand_bytes():
    bodies = [
        hdr(b"S") + b"Q",
        hdr(b"R") + b"AAPL    " + b"Q" + b"N" + struct.pack(">I", 100)
            + b"N" + b"C" + b"  " + b"P" + b"N" + b"N" + b"1" + b"N"
            + struct.pack(">I", 0) + b"N",
        add_body(),
        add_body(ref=43)[:36].replace(b"A", b"F", 1) + b"MPID",
        hdr(b"E") + struct.pack(">QI", 42, 30) + struct.pack(">Q", 900),
        hdr(b"C") + struct.pack(">QI", 42, 5) + struct.pack(">Q", 901)
            + b"Y" + struct.pack(">I", 5_853_200),
        hdr(b"X") + struct.pack(">QI", 43, 25),
        hdr(b"U") + struct.pack(">QQII", 43, 44, 60, 5_853_400),
        hdr(b"D") + struct.pack(">Q", 44),
        hdr(b"P") + struct.pack(">Q", 0) + b"S" + struct.pack(">I", 10)
            + b"AAPL    " + struct.pack(">I", 5_853_250)
            + struct.pack(">Q", 902),
    ]
    msgs, parser = parse_all(frame(*bodies))
    assert [m.kind for m in msgs] == ["S", "R", "A", "F", "E", "C", "X",
                                      "U", "D", "P"]
    S, R, A, F, E, C, X, U, D, P = msgs
    assert S.event_code == "Q"
    assert R.stock == "AAPL"
    assert F.order_ref == 43 and F.side == "B"   # attribution ignored
    assert E.order_ref == 42 and E.shares == 30
    assert C.order_ref == 42 and C.shares == 5 and C.price == 5_853_200
    assert X.order_ref == 43 and X.shares == 25
    assert (U.order_ref, U.new_order_ref) == (43, 44)   # old OUT, new IN
    assert U.shares == 60 and U.price == 5_853_400
    assert D.order_ref == 44
    assert P.shares == 10 and P.price == 5_853_250
    assert not parser.unknown


def test_unknown_types_are_skipped_by_length_and_counted():
    weird = b"Z" + b"\x00" * 17                  # any length works
    msgs, parser = parse_all(frame(
        hdr(b"D") + struct.pack(">Q", 1), weird,
        hdr(b"D") + struct.pack(">Q", 2)))
    assert [m.order_ref for m in msgs] == [1, 2]
    assert parser.unknown == {"Z": 1}


def test_truncation_and_wrong_lengths_are_loud():
    good = add_body()
    with pytest.raises(ValueError, match="truncated message"):
        parse_all(io.BytesIO(struct.pack(">H", len(good)) + good[:-4]))
    with pytest.raises(ValueError, match="spec says"):
        parse_all(frame(hdr(b"D") + struct.pack(">I", 1)))   # 15, not 19


def test_symbol_filter_keeps_wanted_plus_system_events():
    r_fake = (hdr(b"R", locate=1) + b"FAKE    " + b"Q" + b"N"
              + struct.pack(">I", 100) + b"N" + b"C" + b"  " + b"P"
              + b"N" + b"N" + b"1" + b"N" + struct.pack(">I", 0) + b"N")
    r_othr = r_fake.replace(b"FAKE    ", b"OTHR    ")
    r_othr = b"R" + struct.pack(">HH", 2, 0) + TS.to_bytes(6, "big") \
        + r_othr[11:]
    msgs, parser = parse_all(frame(
        hdr(b"S") + b"Q", r_fake, r_othr,
        add_body(ref=1, stock=b"FAKE    ", locate=1),
        add_body(ref=2, stock=b"OTHR    ", locate=2)),
        symbols={"FAKE"})
    assert [(m.kind, m.stock_locate) for m in msgs] == [
        ("S", LOCATE), ("R", 1), ("A", 1)]
    assert parser.skipped_by_filter == 2         # OTHR's R and its add


def test_the_fake_day_round_trips_field_for_field():
    """Writer and reader must agree on every field of every message, or
    nothing downstream is trustworthy. Also proves the fiction exercises
    the paths that matter: partial cancels, replaces, per-order fills."""
    data, expected = build_fake_day(2_000, seed=7)
    parser = ItchParser()
    got = list(parser.parse(io.BytesIO(data)))
    assert got == expected
    kinds = {m.kind for m in got}
    assert {"A", "F", "X", "D", "E", "U", "S", "R"} <= kinds
    assert not parser.unknown

    a, b = build_fake_day(2_000, seed=7)[0], data
    assert a == b                                # deterministic per seed
