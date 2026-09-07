"""Day 2, Step 1: the loader, tested against a handwritten fixture.

Real LOBSTER files are gitignored (licensed), so the tests carry their
own dozen rows of LOBSTER-format CSV, written by hand from the published
format description. The fixture is the format contract in miniature: if
LOBSTER's description and this fixture disagree, one of them is wrong
and the real-file run in Step 1 will say which.
"""
import pytest

from lob.lobster import (EventType, LobsterMessage, describe,
                         read_messages, read_orderbook)

MESSAGE_FIXTURE = """\
34200.004241176,1,11885113,21,2238100,1
34200.025552,2,11885113,6,2238100,1
34200.201521,3,11885113,15,2238100,1
34201.5,1,21885200,100,2239500,-1
34202.000000001,4,21885200,40,2239500,-1
34203.75,5,0,60,2239000,-1
"""

ORDERBOOK_FIXTURE_L2 = """\
2239500,100,2238100,21,9999999999,0,-9999999999,0
2239500,60,2238100,21,2240000,55,2237000,10
"""


def _write(tmp_path, name, text):
    p = tmp_path / name
    p.write_text(text)
    return p


def test_messages_parse_typed_with_integer_ticks_and_ns(tmp_path):
    msgs = read_messages(_write(tmp_path, "msg.csv", MESSAGE_FIXTURE))
    assert len(msgs) == 6
    first = msgs[0]
    assert first == LobsterMessage(time_ns=34200_004241176,
                                   kind=EventType.ADD,
                                   order_id=11885113, size=21,
                                   price=2238100, direction=1)
    # nanosecond precision survives the string parse exactly
    assert msgs[4].time_ns == 34202_000000001
    # a bare ".5" fraction pads correctly
    assert msgs[3].time_ns == 34201_500000000
    # prices are the file's own integers: ticks of $0.0001, $223.81 here
    assert first.price / 10_000 == 223.81
    kinds = [m.kind for m in msgs]
    assert kinds == [EventType.ADD, EventType.REDUCE, EventType.DELETE,
                     EventType.ADD, EventType.EXEC, EventType.EXEC_HIDDEN]


def test_malformed_rows_are_loud(tmp_path):
    with pytest.raises(ValueError, match="columns"):
        read_messages(_write(tmp_path, "bad.csv", "1,2,3\n"))
    with pytest.raises(ValueError, match="direction"):
        read_messages(_write(tmp_path, "bad2.csv",
                             "34200.0,1,5,10,1000,0\n"))
    with pytest.raises(ValueError):
        read_messages(_write(tmp_path, "bad3.csv",
                             "34200.0,9,5,10,1000,1\n"))   # unknown type


def test_orderbook_decodes_dummy_levels_to_absent(tmp_path):
    rows = read_orderbook(_write(tmp_path, "book.csv", ORDERBOOK_FIXTURE_L2),
                          levels=2)
    assert len(rows) == 2
    # row 1: level 2 is dummy on both sides -> absent, not a fake price
    assert rows[0].asks == ((2239500, 100),)
    assert rows[0].bids == ((2238100, 21),)
    # row 2: both levels real, best first
    assert rows[1].asks == ((2239500, 60), (2240000, 55))
    assert rows[1].bids == ((2238100, 21), (2237000, 10))


def test_orderbook_refuses_the_wrong_width(tmp_path):
    p = _write(tmp_path, "book.csv", ORDERBOOK_FIXTURE_L2)
    with pytest.raises(ValueError, match="expected 12"):
        read_orderbook(p, levels=3)


def test_describe_reports_the_schema_note(tmp_path):
    m = _write(tmp_path, "msg.csv", MESSAGE_FIXTURE)
    b = _write(tmp_path, "book.csv", ORDERBOOK_FIXTURE_L2)
    info = describe(m, b, levels=2)
    assert info["messages"] == 6
    assert info["types"]["ADD"] == 2
    assert info["types"]["EXEC_HIDDEN"] == 1
    assert info["first"] == "09:30:00" and info["last"] == "09:30:03"
    assert info["distinct_order_ids"] == 3        # two real ids + the 0
    assert info["price_range_dollars"] == (223.81, 223.95)
    # the fixture's reference has fewer rows than messages, and describe
    # says so instead of hiding it
    assert info["reference_rows"] == 2
    assert info["rows_match_messages"] is False
