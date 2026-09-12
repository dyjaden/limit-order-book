# Raw ITCH: the prediction's verdict and the throughput baseline, 11 September 2026

Week 3 rebuilt the book from Nasdaq's own wire format, TotalView-ITCH
5.0, and put the Week 2 pre-commitment in `lobster_validation.md` on
trial. This file records the verdict, what the level filter measurably
costs now that we own a full-depth ground truth, and the Python
throughput numbers that Week 9's C++ twin will be measured against.

Reproduce, from a clean clone (`data/` is gitignored, so the fiction is
generated first):

```
python scripts/make_fake_itch.py                 # writes data/_fake/fake_itch5.bin
python scripts/replay_itch.py                    # part one: counters must be zero
python scripts/filter_experiment.py --level 3    # part two; also --level 1, 5, 10
python scripts/replay_itch.py --bench --write results/itch_baseline.md
```

## The prediction, restated in the form the data allows

Week 2 wrote down, before Week 3 started: if the LOBSTER divergences are
the filter's information limit and not a defect in the replayer, then
replaying the same day from unfiltered raw ITCH must validate at
effectively 100% with the dark-liquidity, eviction and witness machinery
never firing; otherwise the fault is ours and no data story excuses it.

One amendment, made before any result was seen: Nasdaq's free sample
directory does not carry 2012-06-21 (free ITCH 5.0 days start in 2019),
so the same-day comparison is not available. The prediction transfers to
two operational tests, the second of which is stronger than the original
because its ground truth is ours by construction:

1. On unfiltered data, from an empty book, the four counters that stand
   in for the repair machinery (unknown ids, crossing adds,
   front-of-queue violations, anomalies) must all be zero.
2. When we filter our own full-depth day to K levels and feed the
   UNCHANGED Week 2 machinery, the four divergence classes must
   reappear, every divergent row classified, zero unexplained, and the
   machinery's dark-op count must be predictable from the events the
   filter hid.

## Part one: unfiltered replay from an empty book

The synthetic day: Week 1's tape generator emitted as valid ITCH 5.0
bytes (system-event brackets, a directory entry, then A/F, X, D, E and U
messages with correct framing and big-endian fields), 3.7 MB, 120,013
messages, 120,006 of them touching the book. The parser round-trips it
field for field against the writer's own record, and every decoded type
is also pinned by a hand-built byte fixture.

Result: unknown-id operations 0, crossing adds 0, front-of-queue breaks
0, other anomalies 0. The close book is uncrossed, every resting
quantity positive, level totals reconcile with their member orders.
The front-of-queue assertion is the new instrument here: every one of
the 16,963 executions named the order at the front of its price level,
which Week 2 could never demand because seeded composition was fiction.

The real day is not yet run. Free ITCH 5.0 days at emi.nasdaq.com are
2 to 6 GB compressed and the run is on the machine that holds the file
(`python scripts/replay_itch.py --file <day>.NASDAQ_ITCH50.gz --symbol
AAPL`, streamed without decompressing to disk). Its printed report goes
here verbatim when it lands; until then part one stands on synthetic
data only, and this paragraph is the place that says so.

## Part two: the self-filter experiment

`scripts/filter_experiment.py` replays the synthetic day full-depth,
becomes the filter (a primitive event is published if and only if the
top-K snapshot changes, each U split into the delete-plus-add pair
LOBSTER publishes, each half judged on its own), writes LOBSTER-format
message and orderbook files, and records every event it swallowed. The
unchanged Week 2 machinery then replays those files through the real
loaders and is graded row by row. Same tape (seed 7, 100,000 messages,
129,083 primitive events), four filter depths:

| K | emitted | hidden adds | hidden partials | hidden deletes | phantoms made | evicted | at close | dark ops predicted / seen | touch exact | exact to K |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 25,107 (19.5%) | 49,238 | 10,611 | 44,127 | 461 | 246 | 215 | 6,577 / 6,577 | 8.7% | 8.7% |
| 3 | 48,373 (37.5%) | 36,969 | 8,412 | 35,329 | 2,184 | 689 | 1,495 | 2,854 / 2,854 | 62.1% | 0.2% |
| 5 | 75,908 (58.8%) | 24,316 | 5,587 | 23,272 | 2,258 | 178 | 2,080 | 2,789 / 2,789 | 88.0% | 0.2% |
| 10 | 128,671 (99.7%) | 28 | 79 | 305 | 294 | 2 | 292 | 17 / 17 | 100.0% | 0.6% |

At every depth: zero unexplained rows, zero machinery anomalies, zero
delete-size disagreements, and the dark-op count predicted exactly from
the filter's silences, with zero eviction-induced remainder (no evicted
order was ever named again by the filtered file). The classes seen were
all four at K = 1, 3 and 5; at K = 10 only STALE and STALE_SIZE, because
a ten-level view of a book that rarely holds ten levels a side hides
almost nothing that later resurfaces.

Two things the table shows that the LOBSTER day could only suggest.
First, touch accuracy climbs with K (8.7%, 62.1%, 88.0%, 100%) with the
code held fixed, which is the Week 2 contrast (29.3% on the level-1
file, 68.4% on the level-10 file) reproduced under control: the
accuracy ceiling belongs to the filter. Second, the phantom mechanism is
real and measurable: at K = 3, 2,184 orders were added in view and died
below the band with no message, the machinery's evidence rules evicted
689 of them, and 1,495 were still in the replayed book at the close
(317,794 shares of nothing, median age 1.5 minutes on a day that lasts
about four).

## What the filter costs

For the fiction, at K = 3: 62.5% of all primitive events happen below
the band and are never published, including 35,329 of the 45,040
deletes (78%) and 8,412 of the 10,828 partial cancels (78%). A level-3
replayer that trusts the file as a self-contained log would carry 1,495
dead orders at the close. At K = 1 the filter hides 98% of removals; at
K = 10, 0.7%. The real cost on AAPL is a different number and needs the
real day; what transfers is the mechanism and the sign.

## Throughput baseline (Python)

Three stages, timed separately so the Week 9 comparison cannot blur
them: parse only (bytes to messages, the symbol filter's work
included), replay only (pre-parsed messages through the book, parsing
done in chunks with the clock stopped), and end to end (the path
`scripts/replay_itch.py` normally runs). The block below is written by
`--bench --write`; the machine is named because a throughput number
without one is a rumour.

<!-- bench:begin -->
Measured 2026-09-11 on Intel(R) Core(TM) Ultra 7 255H; Windows 11 (AMD64); Python 3.12.10.
File `fake_itch5.bin` (3.7 MB), the whole file; median of 3 runs per stage, spread in the last column. Whole-run throughput only; no latency percentiles (Week 9).

| stage | messages | seconds (median) | msg/s (median) | spread |
|---|---|---|---|---|
| parse only | 120,013 | 0.42 | 286,618 | 272.1k to 301.3k |
| replay only (pre-parsed) | 120,013 | 1.10 | 108,811 | 107.2k to 111.6k |
| end to end | 120,013 | 1.76 | 68,214 | 67.7k to 69.3k |

Book-touching messages applied: 120,006. Counters after the benchmark replay: unknown ids 0, crossing adds 0, front-of-queue breaks 0, anomalies 0 (clean).
<!-- bench:end -->

What is deliberately NOT here: per-operation latency percentiles. Week 9
measures p50 / p99 / p99.9 on both books with a harness built for it;
quoting sloppy ones now would only have to be retracted. The synthetic
day's message mix (about 39% adds, 30% deletes, 14% executions, 9%
partial cancels, 8% replaces) is roughly market-shaped but it is not
AAPL's; the real day's numbers replace nothing above, they sit beside it.

## Verdict

On every unfiltered stream the machinery has seen, its counters stayed
at zero. On filtered streams whose ground truth we own, the Week 2
taxonomy reproduced with nothing unexplained and the dark-op caseload
predicted exactly, at four depths. The prediction stands, on synthetic
data; its real-data half is open until the ITCH day runs, and the
pre-commitment applies to that run unchanged: if the counters are not
zero there, the fault is ours.

## Limitations

The synthetic day is about four minutes of simulated time on a random
walk held inside an eight-tick band, with no hidden liquidity, no
halts, no crosses, and no price-improved executions; the writer never
emits C, P, Q, B or H messages, so those types are verified by
hand-built fixtures only. Part one's real-data test has not run. The
throughput block is one machine, one Python version, whole-run rates
only, and says so on its first line. Ground truth for part two is the
fiction's, so the filter-cost percentages describe the mechanism, not
any real ticker.
