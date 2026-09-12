"""Day 3, Step 4: the throughput baseline's machinery, not its numbers.

The numbers belong to the machine that measured them and live in
results/itch_baseline.md. What a test can pin is that the three stages
count the same messages, that the replay they time is the clean one,
that the parser's limit really bounds what every stage sees, and that
writing the block into the results file touches nothing but the block.
"""
import io

from lob.itch import ItchParser
from lob.itch_replay import BOOK_KINDS
from make_fake_itch import build_fake_day
from replay_itch import (BENCH_BEGIN, BENCH_END, render_bench, run_bench,
                         splice_block)


def test_parse_limit_bounds_messages_read_not_messages_kept():
    data, expected = build_fake_day(2_000, seed=7)
    p = ItchParser()
    got = list(p.parse(io.BytesIO(data), limit=100))
    assert p.messages_read == 100 and len(got) == 100
    # with a filter that keeps nothing, the limit still counts what was
    # READ: the feed handler's rate is over the stream, not the survivors
    p = ItchParser(symbols={"NOPE"})
    got = list(p.parse(io.BytesIO(data), limit=100))
    assert p.messages_read == 100
    assert [m.kind for m in got] == ["S", "S", "S"]     # system events only
    p = ItchParser()
    assert len(list(p.parse(io.BytesIO(data), limit=None))) == len(expected)


def test_bench_stages_count_the_same_stream_and_the_replay_is_clean(tmp_path):
    data, expected = build_fake_day(1_500, seed=3)
    path = tmp_path / "day.bin"
    path.write_bytes(data)
    b = run_bench(path, None, None, repeats=2, say=lambda *a: None)
    stages = b["stages"]
    assert list(stages) == ["parse only", "replay only (pre-parsed)",
                            "end to end"]
    for r in stages.values():
        assert r["messages"] == len(expected)
        assert r["seconds_median"] > 0 and r["rate_median"] > 0
        assert r["rate_min"] <= r["rate_median"] <= r["rate_max"]
    assert b["applied"] == sum(m.kind in BOOK_KINDS for m in expected)
    assert b["clean"] and b["counters"] == {
        "unknown_refs": 0, "crossing_adds": 0, "front_violations": 0,
        "anomalies": 0}
    assert b["repeats"] == 2 and b["file"] == "day.bin"
    assert b["machine"] and "Python" in b["machine"]

    # the limit is honoured by every stage identically
    b = run_bench(path, None, 400, repeats=1, say=lambda *a: None)
    assert {r["messages"] for r in b["stages"].values()} == {400}


def test_render_names_the_machine_and_never_hides_a_broken_replay():
    base = {
        "file": "x.bin", "bytes": 1_000_000, "symbol": "AAPL",
        "limit": 5_000_000, "repeats": 3, "machine": "TestCPU; TestOS 1 "
        "(x86_64); Python 3.12.0", "date": "2026-09-11", "applied": 9,
        "counters": {"unknown_refs": 0, "crossing_adds": 0,
                     "front_violations": 0, "anomalies": 0},
        "clean": True,
        "stages": {"parse only": {"messages": 5_000_000,
                                  "seconds_median": 10.0,
                                  "rate_median": 500_000.0,
                                  "rate_min": 490_000.0,
                                  "rate_max": 510_000.0}},
    }
    text = render_bench(base)
    assert text.startswith(BENCH_BEGIN) and text.endswith(BENCH_END)
    assert "TestCPU" in text and "symbol AAPL" in text
    assert "first 5,000,000 messages" in text
    assert ("| parse only | 5,000,000 | 10.00 | 500,000 | 490.0k to 510.0k |"
            in text)
    assert "(clean)" in text
    broken = dict(base, clean=False,
                  counters=dict(base["counters"], front_violations=3))
    assert "NOT CLEAN" in render_bench(broken)


def test_splice_replaces_only_the_block_and_appends_when_absent():
    prose = ("# title\n\nprose before\n\n" + BENCH_BEGIN + "\nold numbers\n"
             + BENCH_END + "\n\nprose after\n")
    block = BENCH_BEGIN + "\nnew numbers\n" + BENCH_END
    once = splice_block(prose, block)
    assert once == ("# title\n\nprose before\n\n" + block + "\n\nprose after\n")
    assert splice_block(once, block) == once            # idempotent
    assert "old numbers" not in once
    # no markers: the block is appended, the prose untouched
    assert splice_block("just prose\n", block) == "just prose\n\n" + block + "\n"
    assert splice_block("no newline", block) == "no newline\n\n" + block + "\n"
    assert splice_block("", block) == block + "\n"
