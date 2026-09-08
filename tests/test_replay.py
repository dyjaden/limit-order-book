"""Day 2, Step 3: the replayer and validator, pinned on handmade cases.

Every mechanism the real day exercised gets a miniature here: the seed,
the dark-liquidity rule, ghost eviction, the witness trim, and the
divergence taxonomy. The real-file numbers live in the validation
write-up; these tests keep the machinery honest when it changes.
"""
from lob.book import Book, Side
from lob.lobster import EventType, LobsterMessage, ReferenceRow
from lob.replay import (LobsterReplayer, classify_divergence, side_of,
                        validate_row)


def msg(kind, order_id, size, price, direction, t=34200_000000000):
    return LobsterMessage(time_ns=t, kind=kind, order_id=order_id,
                          size=size, price=price, direction=direction)


ROW0 = ReferenceRow(asks=((10010, 100), (10020, 200)),
                    bids=((9990, 150), (9980, 50)))


def test_seed_reproduces_the_reference_row_exactly():
    book = Book()
    rep = LobsterReplayer(book, levels=2)
    rep.seed(ROW0)
    ok, _, _ = validate_row(book, ROW0, levels=2)
    assert ok
    assert rep.stats.seeded_orders == 4
    assert len(book) == 4


def test_dark_removal_hits_the_synthetic_at_that_price():
    """A DELETE for an id we never saw is pre-window liquidity leaving:
    it comes out of the seeded synthetic at the message's own price, and
    what is not there to take is counted, never invented."""
    book = Book()
    rep = LobsterReplayer(book, levels=2)
    rep.seed(ROW0)
    rep.apply(1, msg(EventType.DELETE, 777, 60, 9990, +1))   # unknown id
    assert book.top_levels(Side.BID, 1) == ((9990, 90),)     # 150 - 60
    assert rep.stats.dark_ops == 1 and rep.stats.unresolved == 0

    rep.apply(2, msg(EventType.DELETE, 778, 40, 9955, +1))   # nothing there
    assert rep.stats.unresolved == 1
    assert rep.stats.unresolved_shares == 40
    assert not rep.stats.anomalies


def test_a_crossing_add_evicts_the_ghost_it_proves_dead():
    """The exchange accepted the add, so whatever it crosses cannot still
    exist. The stale level dies, the add lands, the book stays legal."""
    book = Book()
    rep = LobsterReplayer(book, levels=2)
    rep.seed(ROW0)
    rep.apply(1, msg(EventType.ADD, 500, 30, 10015, +1))     # bid through ask
    assert rep.stats.ghost_evictions == 1                    # ask 10010 died
    assert book.best_bid() == 10015
    assert book.best_ask() == 10020
    assert not rep.stats.anomalies


def test_the_witness_trim_enforces_the_files_inclusion_contract():
    """A message at price P in a K-level file certifies at most K-1 true
    levels better than P. levels=2 here, so a witness at the fourth-best
    bid forces two of the three better levels out."""
    book = Book()
    rep = LobsterReplayer(book, levels=2)
    for i, p in enumerate((10000, 9990, 9980, 9970)):
        book.add(100 + i, Side.BID, p, 10)
    rep.apply(1, msg(EventType.DELETE, 103, 10, 9970, +1))   # witness at 9970
    assert rep.stats.ghost_evictions == 2
    assert book.prices(Side.BID) == (10000,)                 # best survives
    assert not rep.stats.anomalies


def test_exec_of_a_known_order_reduces_in_place_and_proves_the_touch():
    book = Book()
    rep = LobsterReplayer(book, levels=2)
    rep.seed(ROW0)
    book.add(600, Side.ASK, 10005, 80)                       # new best ask
    rep.apply(1, msg(EventType.EXEC, 600, 30, 10005, -1))
    assert book.order(600).qty == 50                         # partial, in place
    rep.apply(2, msg(EventType.EXEC, 600, 50, 10005, -1))    # full fill
    assert book.best_ask() == 10010
    assert not rep.stats.anomalies


def test_classify_divergence_names_all_four_faces():
    bid = Side.BID
    ours = ((100, 10), (98, 5))
    assert classify_divergence(ours, ((100, 10), (98, 5)), bid) is None
    assert classify_divergence(ours, ((100, 10),), bid) == (2, "STALE")
    assert classify_divergence(((100, 10),), ours, bid) == (2, "BACKFILL")
    assert classify_divergence(ours, ((100, 25), (98, 5)), bid) \
        == (1, "BACKFILL_SIZE")
    assert classify_divergence(ours, ((100, 4), (98, 5)), bid) \
        == (1, "STALE_SIZE")
    assert classify_divergence(ours, ((99, 10), (98, 5)), bid) \
        == (1, "STALE")               # our extra better level
    assert classify_divergence(((99, 10),), ((100, 3),), bid) \
        == (1, "BACKFILL")            # reference holds the better level


def test_direction_mapping_is_loud():
    assert side_of(1) is Side.BID and side_of(-1) is Side.ASK
    try:
        side_of(0)
        assert False
    except ValueError:
        pass
