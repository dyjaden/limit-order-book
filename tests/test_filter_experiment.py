"""Day 3, Step 3: the filter we control, and the accounting it enables.

The hand-built scenarios walk the emission rule through every branch the
synthetic day cannot force -- including a drifted order dying IN band,
which the fake tape's back-to-back X-then-D pairs structurally never
produce -- and the end-to-end test pins the full pipeline: the four Day 2
divergence classes from our own ground truth, zero unexplained, and the
machinery's dark-op caseload predicted exactly by the filter's silences.
"""
from pathlib import Path

from lob.book import Book
from lob.itch import ItchMessage
from lob.lobster import read_messages, read_orderbook
from lob.replay import LobsterReplayer, run_validated
from filter_experiment import filter_day, run_experiment, write_files

T0 = 34_200_000_000_000


def im(kind, ref=None, *, new=None, side=None, shares=None, price=None,
       t=T0):
    return ItchMessage(kind, t, 7, order_ref=ref, new_order_ref=new,
                       side=side, shares=shares, price=price)


def add(ref, side, price, shares):
    return im("A", ref, side=side, shares=shares, price=price)


def machinery(out, tmp_path, k):
    """The unchanged Day 2 pipeline over the filtered files on disk."""
    mpath, bpath = write_files(out, tmp_path)
    messages = read_messages(mpath)
    reference = read_orderbook(bpath, k)
    replayer = LobsterReplayer(Book(), levels=k)
    replayer.seed(reference[0])
    result = run_validated(replayer, messages, reference, k)
    return replayer.stats, result


def test_the_emission_rule_and_the_dark_and_drift_predictions(tmp_path):
    """K=1: adds below the best are silent, ops that surface below-band
    history are emitted against ids the file never introduced, and a
    visible order that drifts below the band and dies back inside it
    produces exactly one predicted delete-size disagreement."""
    day = [
        add(1, "B", 10_000, 100),    # emitted; row 0, eaten by the seed
        add(2, "S", 10_010, 70),     # emitted
        add(3, "B", 9_990, 50),      # SILENT: below the K=1 band
        im("D", 1),                  # emitted; id 1 unknown -> dark
        im("X", 3, shares=10),       # 9990 is best now: emitted, dark
        add(4, "B", 10_000, 80),     # emitted, visible
        add(5, "B", 10_001, 60),     # emitted; 4 drops below the band
        im("X", 4, shares=20),       # SILENT: drift on a visible order
        im("D", 5),                  # emitted; 4 back in band, size stale
        im("D", 4),                  # emitted: drifted order dies IN band
    ]
    out = filter_day(day, k=1)
    assert out.emitted == 8
    assert dict(out.silent) == {1: 1, 2: 1}
    assert out.silent_adds == 1 and out.drift_shares == 20
    assert out.predicted_dark == 2
    assert out.predicted_delete_disagreements == 1
    assert not out.phantoms
    assert out.message_rows[0] == ["34200.000000000", 1, 1, 100, 10_000, 1]
    assert out.book_rows[0] == [9_999_999_999, 0, 10_000, 100]
    assert out.message_rows[-1][1:] == [3, 4, 60, 10_000, 1]  # true size
    assert out.book_rows[-1] == [10_010, 70, 9_990, 40]

    stats, result = machinery(out, tmp_path, k=1)
    assert stats.dark_ops == out.predicted_dark == 2
    assert stats.delete_size_disagreements == 1     # 80 resting, D says 60
    assert not stats.anomalies
    assert {"BACKFILL", "STALE_SIZE"} <= set(result.divergence_classes)


def test_u_halves_are_filtered_independently():
    """A replace is LOBSTER's delete+add pair, each half judged on its
    own: wholly below the band it vanishes; from below the band to the
    best it publishes only the add half."""
    day = [
        add(1, "B", 10_000, 100),
        add(2, "B", 9_990, 50),                      # silent, dark-born
        add(3, "S", 10_010, 70),
        im("U", 2, new=4, shares=60, price=9_985),   # below -> below
        im("U", 4, new=5, shares=30, price=10_005),  # below -> BEST
        im("U", 1, new=6, shares=40, price=10_006),  # (now) below -> BEST
    ]
    out = filter_day(day, k=1)
    assert [r[1] for r in out.message_rows] == [1, 1, 1, 1]   # no type 3!
    assert [r[2] for r in out.message_rows] == [1, 3, 5, 6]
    assert out.silent_adds == 2          # id 2's add, and U's half to 9985
    assert out.born_and_died_below == 2  # ids 2 and 4, never seen at all
    assert not out.phantoms              # nothing visible died silently


def test_the_experiment_closes_the_loop_end_to_end(tmp_path):
    """The CI-sized full pipeline: same day the script defaults to, in
    miniature. Every gate must hold, the dark-op prediction must be
    exact on this deterministic day, and all four Day 2 classes must
    reappear from ground truth we own."""
    report = run_experiment(4_000, seed=7, k=2, outdir=tmp_path,
                            quiet=True)
    assert report["ok"], report["checks"]
    assert report["unexplained"] == 0
    assert report["dark_ops"] == report["predicted_dark"] > 0
    assert report["divergent"] > 0
    assert report["phantoms"] > 0
    assert set(report["classes"]) == {"STALE", "STALE_SIZE",
                                      "BACKFILL", "BACKFILL_SIZE"}
    assert 0 < report["emitted"] < report["events"]
