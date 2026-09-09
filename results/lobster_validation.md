# Reconstruction against LOBSTER's reference, 9 September 2026

The claim this project's credibility hangs on, graded row by row: replay
LOBSTER's message file for AAPL on 2012-06-21 through our book and
compare, after every single message, against the book states LOBSTER
itself publishes. The book evolves from messages only; the reference is
consulted for the seed and for grading, never for evolution.

Reproduce: `python scripts/replay_lobster.py --level 10 --validate`
(about half a minute) and `--level 1 --validate`.

## The headline, stated carefully

The planned claim was "our book matches the reference on every row." The
data refused, and the refusal is the finding: **a level-filtered LOBSTER
file is provably not a self-contained event log**, so a perfect state
match is not achievable by any replayer, ours or anyone's. What is
achievable, and what we claim, is this:

> All 400,390 level-10 rows replayed with zero anomalies and zero
> per-order inconsistencies; 68.4% of rows match the reference exactly
> at the touch; and **every one of the 399,571 divergent rows is
> machine-classified into one of four named classes, each a face of the
> file's own information limit. Zero rows are unexplained.**

## Setup

| | level 10 file | level 1 file |
|---|---|---|
| messages | 400,391 | 118,497 |
| reference rows | 400,391 (aligns 1:1) | 118,497 |
| seed | 20 synthetic level-orders from reference row 0 | 2 |
| anomalies | 0 | 0 |
| ghost evictions | 12,014 orders | 20,268 |
| dark-liquidity ops | 11,351 | 14,980 |
| known-delete size disagreements | 0 | 4 |
| replay speed (Python) | ~13,700 msg/s validated, ~93,000 raw | ~43,000 msg/s |

Prices are the file's own integers (ticks of $0.0001); time is integer
nanoseconds parsed from the string. AAPL traded $577 to $588 that day,
pre-split, which dates the sample correctly.

## Exact-match profile

Level-10 file, share of rows whose top-J levels match the reference
exactly on both sides:

| depth J | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 |
|---|---|---|---|---|---|---|---|---|---|---|
| exact | 68.4% | 49.5% | 34.2% | 21.9% | 12.5% | 6.5% | 3.0% | 1.3% | 0.6% | 0.2% |

By hour, the touch match is 83.5% in the first hour and flat around
61-70% after, and the closing top-of-book matches the reference to the
tick on both sides. The level-1 file, whose visible band is a single
level, manages only 29.3% at its own touch with identical code. That
contrast is the point: the accuracy ceiling belongs to the file's
filter, not to the replayer.

## The finding: silent departures across the band boundary

A K-level file contains only messages that affect the top K levels. An
order can be added in view, sink below the band as the ladder rallies,
be cancelled there (no message, it is out of range), and leave a phantom
in any replay when the band recedes. We traced one all the way down:

- Order 135845355, ADD 100 shares at 5848400, message 160247, in the
  visible band at the time.
- The bid ladder rallies; from message 160294 the price oscillates below
  the band (the reference's tenth-best bid is better than 5848400).
- During a below-band spell the order is deleted. No message exists,
  by the format's own definition.
- At message 160300 the band recedes: the reference shows 5848400 as
  absent, our book still holds it, and 282 messages later it briefly
  becomes our phantom best bid.

This mechanism regenerates all day as the ladder breathes across the
rank-K boundary, which is why the hourly match rate is flat rather than
decaying: it is not the seed wearing off, it is the filter working.

The same mechanism explains the only per-order oddity found: two
level-10 orders whose ADD said 200 shares and whose final DELETE said
100. The missing 100 was reduced while the order sat below the band.
After the witness rule (below) both cases resolve before their deletes
arrive, and the disagreement count is zero.

## The repairs, in the order the data forced them

All four use message-stream truth only. None consults the reference.

1. **The dark-liquidity rule.** Operations on ids the file never
   introduced are operations on pre-window or below-band liquidity;
   they apply against the synthetic order at the message's own price.
   What is not there to take is counted, never invented.
2. **Crossing-add eviction.** The exchange accepted the add, so
   whatever it crosses in our book cannot still exist. First run of the
   level-1 file before this rule: 27,419 crossing anomalies and a book
   of 10,058 ghosts. After: zero anomalies.
3. **Execution eviction.** An execution at price P proves nothing
   better survives on that side.
4. **The witness rule.** The file's own inclusion contract as evidence:
   every message concerns a price inside the true top K, so a message
   at P certifies at most K-1 true levels better than P. Our surplus is
   provably stale and is trimmed. Touch accuracy: 60.4% to 68.4%
   (level 10) and 11.0% to 29.3% (level 1); delete-size disagreements
   on level 10: 2 to 0.

## The divergence taxonomy

Every divergent row is classified at its first divergent rank:

| class | meaning | level 10 | level 1 |
|---|---|---|---|
| STALE | we hold a level whose removal was filtered | 46.6% | 30.9% |
| STALE_SIZE | shared price, our size larger | 25.0% | 26.2% |
| BACKFILL | reference reveals a level we never saw arrive | 14.2% | 25.0% |
| BACKFILL_SIZE | shared price, reference size larger | 14.2% | 17.9% |
| unexplained | anything else | **0** | **0** |

## The falsifiable prediction, written down before Week 3

If these divergences really are the filter's information limit and not
a defect in the replayer, then replaying the SAME day from raw
Nasdaq TotalView-ITCH, which is unfiltered and carries every add and
every delete at every depth, must validate at effectively 100% with the
dark-liquidity, eviction, and witness machinery never firing. That is
Week 3's experiment, and this paragraph is the pre-commitment: if ITCH
replay does not reach effectively-perfect state match, the fault is in
our book or parser, and no information-limit story will be allowed to
excuse it.

## Limitations

One ticker, one day, one venue's sample files. The seed's composition
within a level is synthetic (totals exact, queue order fiction), so
queue-position statistics are not validated here, only level state.
Victim choice inside the witness trim is a heuristic (nearest the
witness price, synthetics first); wrong choices convert one divergence
class into another but cannot create unexplained rows. The taxonomy
names classes of the file's information limit; it cannot prove any
individual divergent share innocent, only that its shape matches the
limit and nothing else in the replay does.
