"""Day 1, Step 3: the book's invariants, computed by hand.

Every expected value below was worked out on paper before the code ran --
the house practice: a test that asserts the code does what the code does
is not a test. The one that matters most is the crossing invariant; it is
this project's version of the backtester's no-look-ahead rule, the
structural property everything downstream silently assumes.

This suite also earned its keep before any data existed: writing the
refused-replace test exposed that the first implementation restored a
refused amend by RE-ADDING it, silently costing the original order its
place in line. The fix (validate before canceling) is documented in
Book.replace.
"""
import pytest

from lob import Book, Side


def small_market() -> Book:
    """Bids 9998(100), 9997(200); asks 10001(150), 10002(300).
    Spread 3 ticks. Drawn on paper first; every test reads from it."""
    b = Book()
    b.add(1, Side.BID, 9998, 100)
    b.add(2, Side.BID, 9997, 200)
    b.add(3, Side.ASK, 10001, 150)
    b.add(4, Side.ASK, 10002, 300)
    return b


# ------------------------------------------------------------------ edges
def test_an_empty_book_says_none_never_a_fake_zero():
    b = Book()
    assert b.best_bid() is None
    assert b.best_ask() is None
    assert b.spread() is None
    b.add(1, Side.BID, 100, 10)
    assert b.spread() is None          # one-sided market has no spread
    assert b.best_bid() == 100


def test_adds_build_both_sides_and_the_spread():
    b = small_market()
    assert (b.best_bid(), b.best_ask(), b.spread()) == (9998, 10001, 3)
    assert b.depth(Side.BID, 2) == [(9998, 100), (9997, 200)]
    assert b.depth(Side.ASK, 2) == [(10001, 150), (10002, 300)]
    assert len(b) == 4


# ------------------------------------------- THE invariant: never crossed
def test_a_crossing_add_is_refused_loudly():
    """A real feed never delivers a crossing add -- the exchange would
    have matched it. Seeing one is corruption, and corruption is loud."""
    b = small_market()
    with pytest.raises(ValueError, match="crosses"):
        b.add(9, Side.BID, 10001, 50)      # bid at the ask
    with pytest.raises(ValueError, match="crosses"):
        b.add(9, Side.ASK, 9998, 50)       # ask at the bid
    with pytest.raises(ValueError, match="crosses"):
        b.add(9, Side.BID, 10002, 50)      # bid through the ask
    # the refusals changed nothing
    assert (b.best_bid(), b.best_ask()) == (9998, 10001)
    assert len(b) == 4


def test_the_book_never_crosses_through_a_legal_sequence():
    """After every legal operation, bid < ask. Checked step by step
    through a sequence that tightens the spread from both sides."""
    b = small_market()
    steps = [
        lambda: b.add(5, Side.BID, 9999, 60),      # bid improves to 9999
        lambda: b.add(6, Side.ASK, 10000, 40),     # ask improves to 10000
        lambda: b.cancel(5),                       # best bid back to 9998
        lambda: b.execute(Side.ASK, 40),           # ask 10000 emptied
        lambda: b.replace(1, 9999, 100),           # bid improves again
    ]
    for step in steps:
        step()
        assert b.best_bid() < b.best_ask()
    assert (b.best_bid(), b.best_ask()) == (9999, 10001)


# ------------------------------------------------------ time priority
def test_fifo_within_a_level_survives_a_middle_cancel():
    """Three orders at one price -- A(100), B(50), C(70). Cancel the
    middle one. An execute of 120 must fill A completely (100) and then
    C partially (20): first in, first filled, and B's departure did not
    reshuffle anyone."""
    b = Book()
    b.add(1, Side.BID, 9998, 100)      # A
    b.add(2, Side.BID, 9998, 50)       # B
    b.add(3, Side.BID, 9998, 70)       # C
    b.cancel(2)
    fills = b.execute(Side.BID, 120)
    assert [(f.order_id, f.qty) for f in fills] == [(1, 100), (3, 20)]
    assert b.order(3).qty == 50        # C keeps the remainder


def test_replace_loses_queue_position():
    """The rule beginners get wrong. A(80) then B(30) rest at one price;
    A is replaced at the SAME price with a new size. A now stands behind
    B, so an execute of 30 fills B -- the order that never moved."""
    b = Book()
    b.add(1, Side.BID, 9998, 80)       # A
    b.add(2, Side.BID, 9998, 30)       # B
    b.replace(1, 9998, 60)             # A rejoins at the back
    fills = b.execute(Side.BID, 30)
    assert [(f.order_id, f.qty) for f in fills] == [(2, 30)]
    assert b.order(1).qty == 60        # A untouched, but now first in line


def test_a_refused_replace_leaves_the_original_in_place():
    """A refused amend must not move the original -- including its place
    in line. A(80) then B(30); replacing A to a crossing price raises;
    an execute must still fill A FIRST, proving A never left the front.
    (This is the test that caught the re-add rollback bug.)"""
    b = Book()
    b.add(1, Side.BID, 9998, 80)       # A, front of the queue
    b.add(2, Side.BID, 9998, 30)       # B, behind A
    b.add(3, Side.ASK, 10001, 50)
    with pytest.raises(ValueError, match="would cross"):
        b.replace(1, 10001, 80)        # bid to the ask price: refused
    with pytest.raises(ValueError, match="positive"):
        b.replace(1, 9998, 0)          # zero quantity: refused
    fills = b.execute(Side.BID, 50)
    assert [(f.order_id, f.qty) for f in fills] == [(1, 50)]   # A still first


# ----------------------------------------------------------- conservation
def test_depth_conserves_through_execution():
    """Add 300 at a level, execute 120: exactly 180 remains and the fills
    sum to exactly 120. Shares neither appear nor vanish."""
    b = Book()
    b.add(1, Side.ASK, 10001, 100)
    b.add(2, Side.ASK, 10001, 200)
    fills = b.execute(Side.ASK, 120)
    assert sum(f.qty for f in fills) == 120
    assert b.depth(Side.ASK, 1) == [(10001, 180)]


def test_execute_walks_levels_best_first():
    """Demand spanning two levels: fills show the best price first, FIFO
    within each level, at the RESTING orders' prices. Hand-computed:
    150 at 10001 (order 3), then 50 of order 4 at 10002."""
    b = small_market()
    fills = b.execute(Side.ASK, 200)
    assert [(f.order_id, f.price, f.qty) for f in fills] == [
        (3, 10001, 150), (4, 10002, 50)]
    assert b.best_ask() == 10002       # emptied level deleted, best moved
    assert b.depth(Side.ASK, 1) == [(10002, 250)]


def test_execute_beyond_visible_liquidity_is_refused_and_changes_nothing():
    """An aggregate execute that exceeds what the book holds is feed
    corruption or caller error; a silent partial would launder it. The
    refusal must also leave the book EXACTLY as it was."""
    b = small_market()
    before = (b.depth(Side.ASK, 2), len(b))
    with pytest.raises(ValueError, match="exceeds available"):
        b.execute(Side.ASK, 451)       # 150 + 300 = 450 available
    assert (b.depth(Side.ASK, 2), len(b)) == before
    b.execute(Side.ASK, 450)           # exactly everything is legal
    assert b.best_ask() is None


# ---------------------------------------------------------- loud failures
def test_loud_failures_for_hostile_input():
    b = small_market()
    with pytest.raises(ValueError, match="duplicate"):
        b.add(1, Side.ASK, 10005, 10)          # id reuse
    with pytest.raises(KeyError, match="unknown"):
        b.cancel(999)                          # cancel of a ghost
    with pytest.raises(KeyError, match="unknown"):
        b.replace(999, 10005, 10)              # replace of a ghost
    with pytest.raises(ValueError, match="positive"):
        b.add(9, Side.ASK, 10005, 0)           # zero quantity
    with pytest.raises(ValueError, match="positive"):
        b.execute(Side.BID, 0)                 # zero execute
    with pytest.raises(KeyError, match="unknown"):
        b.cancel(1) and b.cancel(1)            # cancel of the cancelled


def test_level_cleanup_moves_the_best_price():
    """Emptying the best level -- by cancel or by fill -- must promote the
    next one; a stale empty level would freeze the top of book."""
    b = small_market()
    b.cancel(1)                                # sole order at 9998 leaves
    assert b.best_bid() == 9997
    b.execute(Side.ASK, 150)                   # exactly empties 10001
    assert b.best_ask() == 10002


def test_the_id_index_stays_consistent():
    """len(book) tracks live orders; a fully filled order is gone from
    the index exactly like a cancelled one."""
    b = small_market()
    assert len(b) == 4
    b.execute(Side.ASK, 150)                   # order 3 fully filled
    assert len(b) == 3
    with pytest.raises(KeyError):
        b.order(3)
    b.cancel(4)
    assert len(b) == 2
