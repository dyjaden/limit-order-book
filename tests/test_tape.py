"""Day 1, Step 4: the fiction obeys the physics, and CI holds it to that.

A small tape, replayed with the invariants checked at every step, so the
generator can never quietly start emitting illegal sequences -- if it
crossed its own book or cancelled a ghost, every downstream test would
inherit the confusion. Determinism is tested too: a tape that changes
under a fixed seed cannot be used to reproduce a bug report.
"""
from lob import Book
from make_fake_tape import make_tape, replay


def test_the_tape_is_deterministic_per_seed():
    a = make_tape(2_000, seed=7)
    b = make_tape(2_000, seed=7)
    c = make_tape(2_000, seed=8)
    assert a == b                       # same seed, identical fiction
    assert a != c                       # different seed, different fiction


def test_a_tape_replays_with_invariants_held_at_every_step():
    tape = make_tape(5_000, seed=7)
    stats = replay(tape, Book(), check_every=1)   # asserts internally
    assert stats.messages == 5_000
    assert stats.adds + stats.cancels + stats.replaces + stats.executes \
        == 5_000


def test_the_tape_is_market_shaped():
    """Cancels dominate executes and the book never bloats -- the shape,
    not the numbers, is what Week 2's real data will recognise."""
    stats = replay(make_tape(5_000, seed=7), Book(), check_every=10)
    assert stats.cancels > stats.executes * 2     # changed minds dominate
    assert stats.executes > 0                     # but trades DO happen
    assert stats.fills >= stats.executes          # each consumes >= 1 order
    assert stats.max_live <= 2_100                # population band held
