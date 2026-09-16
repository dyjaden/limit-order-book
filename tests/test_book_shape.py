"""Day 4, Step 3: book shape by occupied level and by cent, by hand.

Three states with hand-chosen ladders: a gap at level 2 on the bid
side, a side with only two levels, and unequal holding times, so every
time-weighted size, gap, existence share and band coverage below was
computed on paper before the code ran.
"""
import pytest

from lob.lobster import ReferenceRow
from lob.micro import INCREMENT, NS, State, book_shape

C = INCREMENT          # one cent, in price units


def st(hold_s, bids, asks) -> State:
    return State(0, int(hold_s * NS), tuple(asks), tuple(bids))


# state A, 1 s: bids at 0c (100 sh), 3c (200 sh); asks at 0c (50), 1c (60), 2c (70)
# state B, 3 s: bids at 0c (300 sh), 1c (400 sh), 2c (500 sh); asks at 0c (80)
# state C, 6 s: one-sided (no asks): skipped entirely
STATES = [
    st(1, [(10_000, 100), (10_000 - 3 * C, 200)],
       [(10_100, 50), (10_100 + C, 60), (10_100 + 2 * C, 70)]),
    st(3, [(10_000, 300), (10_000 - C, 400), (10_000 - 2 * C, 500)],
       [(10_100, 80)]),
    st(6, [(10_000, 1)], []),
]


def test_level_profile_sizes_gaps_and_existence_by_hand():
    lv, _ = book_shape(STATES, levels=10, max_cents=5)
    bid, ask = lv["bid"], lv["ask"]
    assert bid.two_sided_ns == 4 * NS                 # 1 s + 3 s; C skipped
    # bid level 1: (100*1 + 300*3) / 4 = 250 shares, gap 0, exists 100%
    assert bid.tw_size(1) == 250.0 and bid.tw_gap_cents(1) == 0.0
    assert bid.exists_share(1) == 1.0
    # bid level 2: (200*1 + 400*3) / 4 = 350; gap (3*1 + 1*3)/4 = 1.5 cents
    assert bid.tw_size(2) == 350.0 and bid.tw_gap_cents(2) == 1.5
    # bid level 3 exists only in B (3 of 4 s): 500 shares at 2 cents
    assert bid.tw_size(3) == 500.0 and bid.tw_gap_cents(3) == 2.0
    assert bid.exists_share(3) == 0.75
    assert bid.tw_size(4) is None and bid.exists_share(4) == 0.0
    # ask side: level 1 (50*1 + 80*3)/4 = 72.5; levels 2 and 3 only in A
    assert ask.tw_size(1) == 72.5
    assert ask.tw_size(2) == 60.0 and ask.tw_gap_cents(2) == 1.0
    assert ask.exists_share(2) == 0.25 and ask.exists_share(3) == 0.25
    # adjacent gaps on the bid side: A has one gap of 3c (1 s), B has two
    # gaps of 1c (3 s each): mean (3*1 + 1*3 + 1*3)/(1 + 3 + 3) = 9/7 cents,
    # one-cent share 6/7
    assert bid.tw_adjacent_gap_cents == pytest.approx(9 / 7)
    assert bid.one_cent_gap_share == pytest.approx(6 / 7)
    # ask side: A has two 1c gaps (1 s each), B has none
    assert ask.tw_adjacent_gap_cents == 1.0 and ask.one_cent_gap_share == 1.0


def test_cent_profile_zeros_inside_the_band_and_unknown_beyond_it():
    _, ct = book_shape(STATES, levels=10, max_cents=5)
    bid, ask = ct["bid"], ct["ask"]
    # bid band: A reaches 3c, B reaches 2c: coverage 4/4 for cents 0..2,
    # 1/4 for cent 3, 0 beyond
    assert [bid.coverage(d) for d in range(6)] == [1.0, 1.0, 1.0, 0.25, 0.0, 0.0]
    # bid sizes conditional on coverage:
    #   0c (100*1 + 300*3)/4 = 250;  1c: A empty (known 0), B 400: (0*1+400*3)/4 = 300
    #   2c: A empty, B 500: 375;  3c: only A covers it, 200 there: 200
    assert [bid.tw_size(d) for d in range(4)] == [250.0, 300.0, 375.0, 200.0]
    assert bid.tw_size(4) is None                      # never inside the band
    # ask band: A reaches 2c, B reaches 0c: coverage 1 at 0c, 1/4 at 1c and 2c
    assert [ask.coverage(d) for d in range(4)] == [1.0, 0.25, 0.25, 0.0]
    assert ask.tw_size(0) == 72.5 and ask.tw_size(1) == 60.0 and ask.tw_size(2) == 70.0


def test_max_cents_caps_the_profile_and_levels_caps_the_ranks():
    lv, ct = book_shape(STATES, levels=2, max_cents=1)
    assert lv["bid"].tw_size(2) == 350.0 and len(lv["bid"].size_x_ns) == 3
    assert len(ct["bid"].size_x_ns) == 2
    # with levels=2 the band is what the two profiled ranks reach: 3c in
    # A and 1c in B, so cent 1 is covered all the time; cents beyond
    # max_cents are simply not tracked
    assert ct["bid"].coverage(1) == 1.0


def test_zero_hold_and_one_sided_states_contribute_nothing():
    lv, ct = book_shape([st(0, [(10_000, 9)], [(10_100, 9)]),
                         st(5, [(10_000, 9)], [])], levels=3, max_cents=2)
    assert lv["bid"].two_sided_ns == 0 and lv["bid"].tw_size(1) is None
    assert ct["ask"].coverage(0) is None


def test_states_from_reference_rows_feed_the_profiles():
    rows = [ReferenceRow(asks=((10_100, 5),), bids=((10_000, 7),))]
    lv, _ = book_shape([State(0, NS, rows[0].asks, rows[0].bids)])
    assert lv["bid"].tw_size(1) == 7.0 and lv["ask"].tw_size(1) == 5.0
