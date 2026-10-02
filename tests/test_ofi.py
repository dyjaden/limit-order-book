"""Day 5, Step 1: the OFI sign convention, pinned before any data touches it.

One test per row of the Day 5 guide's table. The four indicator terms
are easy to transcribe with one inequality the wrong way, and the result
still "works": R-squared is unchanged and beta flips sign, so a flipped
beta at the first look would read as a finding. These cases make the
convention a fact of the code, not of the author's memory. Then the
mechanics (a deeper level changes nothing, a one-sided row is skipped and
counted, the mid chain telescopes), and the loop-closer from Week 3:
the self-filter emits level-1 and level-10 views of one synthetic day,
and the daily OFI from both must be the same integer.
"""
import pytest

from filter_experiment import filter_day, write_files
from lob.lobster import EventType, LobsterMessage, ReferenceRow, read_messages, read_orderbook
from lob.micro import NS, OPEN_NS
from lob.ofi import Event, Touch, day_summary, events, ofi_event, touches
from make_fake_itch import build_fake_day


def T(bid, bid_size, ask, ask_size, t_s=0.0) -> Touch:
    return Touch(OPEN_NS + int(round(t_s * NS)), bid, bid_size, ask, ask_size)


BASE = T(10_000, 300, 10_100, 150)          # bid 300 @ 1.0000, ask 150 @ 1.0100


# ------------------------------------------------ the eight hand cases
def test_bid_size_grows_at_an_unchanged_price_is_plus_the_shares_added():
    assert ofi_event(BASE, T(10_000, 400, 10_100, 150)) == +100


def test_bid_size_shrinks_at_an_unchanged_price_is_minus_the_shares_removed():
    """A cancel or a sell market order at the best bid: 300 to 240."""
    assert ofi_event(BASE, T(10_000, 240, 10_100, 150)) == -60


def test_a_new_better_bid_counts_its_whole_queue_whatever_the_old_bid_held():
    assert ofi_event(BASE, T(10_050, 250, 10_100, 150)) == +250
    big_old = T(10_000, 9_999, 10_100, 150)
    assert ofi_event(big_old, T(10_050, 250, 10_100, 150)) == +250


def test_the_best_bid_level_emptying_is_minus_the_whole_old_queue():
    """The best bid falls to the next level, whatever that level holds."""
    assert ofi_event(BASE, T(9_900, 1_000, 10_100, 150)) == -300


def test_ask_size_grows_at_an_unchanged_price_is_minus_the_shares_added():
    assert ofi_event(BASE, T(10_000, 300, 10_100, 250)) == -100


def test_a_buy_trade_that_eats_the_best_ask_is_plus_the_shares_taken():
    assert ofi_event(BASE, T(10_000, 300, 10_100, 90)) == +60


def test_a_new_better_ask_is_minus_its_whole_queue():
    assert ofi_event(BASE, T(10_000, 300, 10_050, 250)) == -250
    big_old = T(10_000, 300, 10_100, 9_999)
    assert ofi_event(big_old, T(10_000, 300, 10_050, 250)) == -250


def test_the_best_ask_level_emptying_is_plus_the_whole_old_queue():
    assert ofi_event(BASE, T(10_000, 300, 10_200, 7)) == +150


# ------------------------------------------------------- the mechanics
def test_both_sides_moving_at_once_add_their_terms():
    """No single message does this, but the formula is a sum of a bid
    term and an ask term and must behave like one: +50 on the bid, +20
    for the ask shrinking."""
    assert ofi_event(BASE, T(10_000, 350, 10_100, 130)) == +70


def test_nothing_changing_at_the_touch_is_exactly_zero():
    assert ofi_event(BASE, T(10_000, 300, 10_100, 150, t_s=5)) == 0


def msg(t_s: float, kind=EventType.ADD, oid=1, size=100, price=10_000,
        direction=1) -> LobsterMessage:
    return LobsterMessage(time_ns=OPEN_NS + int(round(t_s * NS)), kind=kind,
                          order_id=oid, size=size, price=price,
                          direction=direction)


def test_a_message_that_touches_only_level_three_contributes_zero():
    """Three reference rows: the third adds 500 shares two levels behind
    the best bid and changes nothing at the touch."""
    msgs = [msg(0), msg(1), msg(2, oid=2, size=500, price=9_800)]
    asks = ((10_100, 150), (10_200, 40))
    ref = [ReferenceRow(asks=asks, bids=((10_000, 300), (9_900, 20))),
           ReferenceRow(asks=asks, bids=((10_000, 300), (9_900, 20))),
           ReferenceRow(asks=asks, bids=((10_000, 300), (9_900, 20), (9_800, 500)))]
    evs = list(events(touches(msgs, ref)))
    assert [ev.e for ev in evs] == [0, 0]
    assert evs[1].time_ns == msgs[2].time_ns          # the later observation's time
    assert evs[1].mid2 == evs[1].mid2_prev == 20_100


def test_a_one_sided_observation_is_skipped_and_counted_never_invented():
    a = T(10_000, 300, 10_100, 150, 0)
    b = T(10_000, 300, None, None, 1)                # the ask side is empty
    c = T(10_000, 320, 10_200, 80, 2)
    skipped: list[Touch] = []
    evs = list(events([a, b, c], skipped))
    assert skipped == [b]
    assert len(evs) == 1 and evs[0].time_ns == c.time_ns
    # the one event spans a to c: bid grows by 20 (+20), the ask level
    # rose so the whole old ask queue counts (+150)
    assert evs[0].e == +170
    assert evs[0].mid2_prev == a.mid2 and evs[0].mid2 == c.mid2
    with pytest.raises(ValueError):
        ofi_event(a, b)
    with pytest.raises(ValueError):
        b.mid2
    # an empty row is skipped the same way, and a file of nothing but
    # one-sided rows has no events at all
    assert list(events([b, T(None, None, None, None, 3)])) == []


def test_touches_pair_row_i_with_message_i_and_refuse_misaligned_files():
    msgs = [msg(0), msg(1.5)]
    ref = [ReferenceRow(asks=((10_100, 150),), bids=((10_000, 300),)),
           ReferenceRow(asks=(), bids=((10_000, 300),))]
    ts = list(touches(msgs, ref))
    assert ts[0] == T(10_000, 300, 10_100, 150, 0)
    assert ts[1] == T(10_000, 300, None, None, 1.5)
    assert ts[0].two_sided and not ts[1].two_sided
    with pytest.raises(ValueError):
        list(touches(msgs, ref[:1]))


def test_the_mid_chain_telescopes_and_reads_in_cents():
    ts = [T(10_000, 300, 10_100, 150, 0), T(10_050, 250, 10_100, 150, 1),
          T(10_050, 250, 10_150, 10, 2)]
    evs = list(events(ts))
    assert [ev.mid2_prev for ev in evs] == [20_100, 20_150]
    assert [ev.mid2 for ev in evs] == [20_150, 20_200]
    assert [ev.e for ev in evs] == [+250, +150]
    assert evs[0].dmid_cents == 0.25                 # 50 units of mid2 = 25 units of mid
    assert evs[1].dmid_cents == 0.25
    assert Event(0, 0, 20_000, 19_000).dmid_cents == -5.0


def test_day_summary_counts_and_sums_by_hand():
    evs = [Event(1, +100, 20_000, 20_000), Event(2, 0, 20_000, 20_000),
           Event(3, -40, 20_000, 19_900), Event(4, +25, 19_900, 20_000),
           Event(5, 0, 20_000, 20_050)]
    s = day_summary(evs, skipped=3)
    assert s["events"] == 5 and s["zero_events"] == 2 and s["zero_share"] == 0.4
    assert s["positive"] == 2 and s["negative"] == 1
    assert s["sum"] == 85 and s["abs_sum"] == 165
    assert s["buy_shares"] == 125 and s["sell_shares"] == 40
    assert s["net_share"] == pytest.approx(85 / 165)
    assert s["largest_e"] == 100 and s["largest_time_ns"] == 1
    assert s["first_mid2"] == 20_000 and s["last_mid2"] == 20_050
    assert s["mid_change_cents"] == 0.25
    assert s["mid_change_bps"] == pytest.approx(10_000 * 50 / 20_000)
    assert s["skipped"] == 3
    empty = day_summary([])
    assert empty["events"] == 0 and empty["zero_share"] is None
    assert empty["net_share"] is None and empty["mid_change_cents"] is None


# -------------------------------------------------------- the loop-closer
def _views(tmp_path, n=3_000, seed=7):
    _, truth = build_fake_day(n, seed)
    out = {}
    for k in (1, 10):
        view = filter_day(truth, k)
        mpath, bpath = write_files(view, tmp_path / f"k{k}")
        out[k] = (read_messages(mpath), read_orderbook(bpath, k))
    return out


def test_level_1_and_level_10_views_of_one_day_give_the_same_ofi_to_the_share(tmp_path):
    views = _views(tmp_path)
    (m1, r1), (m10, r10) = views[1], views[10]
    assert len(m1) < len(m10)                        # the filter bit
    skipped1: list[Touch] = []
    skipped10: list[Touch] = []
    e1 = list(events(touches(m1, r1), skipped1))
    e10 = list(events(touches(m10, r10), skipped10))
    s1, s10 = day_summary(e1, len(skipped1)), day_summary(e10, len(skipped10))
    assert s1["abs_sum"] > 0                         # not vacuous
    assert s1["sum"] == s10["sum"] and s1["abs_sum"] == s10["abs_sum"]
    assert s1["buy_shares"] == s10["buy_shares"]
    assert s1["first_mid2"] == s10["first_mid2"] and s1["last_mid2"] == s10["last_mid2"]
    # the extra level-10 rows are messages below the touch: zero events,
    # and the nonzero events are the same sequence at the same times
    assert s10["zero_events"] > s1["zero_events"]
    nz = lambda evs: [(ev.time_ns, ev.e, ev.mid2) for ev in evs if ev.e]
    assert nz(e1) == nz(e10)
    # and a wrong file breaks it: one best-bid size off by one share, at
    # a row whose best bid price held, so the bid term must move by one
    bad = list(r10)
    i = next(j for j in range(1, len(bad))
             if bad[j].bids and bad[j].asks and bad[j - 1].bids and bad[j - 1].asks
             and bad[j].bids[0][0] == bad[j - 1].bids[0][0])
    (bp, bs), *rest = bad[i].bids
    bad[i] = ReferenceRow(asks=bad[i].asks, bids=((bp, bs + 1), *rest))
    assert nz(events(touches(m10, bad))) != nz(e10)
