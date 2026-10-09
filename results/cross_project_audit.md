# Two repos, checking each other

Week 7 row D of the plan. The
[event-driven-backtester](https://github.com/dyjaden/event-driven-backtester)
measures why daily-bar backtests lie and charges a transaction cost it
could not measure; this repo measures how prices get made and, since
Week 7, what the cost actually was. This page puts the two sets of
numbers on one sheet: the backtester's assumptions that this repo can
now check, this repo's habits that the backtester's lessons shaped, and
the two sentences each project was built to be able to say. Every
number here is quoted from a page that already carries its window and
its trial count; nothing is computed on this page and no registry row
is added by it.

## The backtester's numbers this repo can now check

| The backtester says | Where | The tape says (2012-06-21, 51 S&P 500 names) | Where |
|---|---|---|---|
| The spread cost is "reported across 1-5 bp rather than at a single value"; the headline runs use 1 bp; the prose calls the parameter a half-spread | README lines 515-516; `results/momentum_baseline.md` | Half effective spread, one way: median 1.70 bps, quartiles 1.32 to 2.22; top dollar-volume decile 1.13, bottom 2.73; 49 of 51 names at or above 1 bp | `spread_audit.md` #78 to #81 |
| `HalfSpreadSlippage(spread_bps=1.0)` takes the full quoted spread and fills at the mid plus or minus half of it | `src/backtester/costs.py`, pinned by `test_half_spread_is_exactly_half_the_full_spread` | So the runs labelled 1 bp charged 0.5 bp per side: every name costs more, 3.4 times at the median, 5.5 times in the bottom decile | `spread_audit.md` #82 to #85 |
| The top of the range, 5 bp | README line 516 | Covers 50 of the 51 names (ANR, 5.79 bps, is above it); read as a half-spread it is 1.8 times heavy in the bottom decile; read the code's way it is 2.5 bp per side, under the bottom decile's median of 2.73 | `spread_audit.md` #86 to #89 |
| "Ten basis points of assumed half-spread cost 0.02 Sharpe" at about seven times a year turnover; net Sharpe 0.58 at 1 bp and 0.57 at 5 bp | README lines 268-272; `results/robustness.md` | Charging the median name's 1.70 bps per side needs a parameter of 3.4, which by that table is under 0.01 Sharpe: the label is off by more than the result | `spread_audit.md`, "What the numbers say" |
| The universe is the S&P 500 as CRSP records it, point in time, keyed on PERMNO | README, "What survivorship bias was worth" | The same membership table (`crsp_a_indexes.dsp500list_v2`) picked the 50 names, five per dollar-volume decile, so the audit is on the backtester's own universe and not a stand-in | `taq_spreads.md` |
| The cost model charges the taker the half-spread and nothing to the provider | `costs.py` | The provider kept almost nothing: the realized spread is negative for 23 of 51 names, median 0.16 bps against a median effective spread of 3.4, median price impact 3.7 bps | `taq_spreads.md` |

The audit's one sentence, written for the backtester's README and
committed there with it:

> Measured on the consolidated tape for 51 S&P 500 names on 2012-06-21
> (five per dollar-volume decile plus AAPL; limit-order-book,
> results/spread_audit.md), the median half effective spread, the
> one-way cost of crossing, was 1.7 bps: a 1 bp half-spread is right
> for the top dollar-volume decile (median 1.1 bps), 1.7x light for the
> median name and 2.7x light in the bottom decile, and the top of the
> 1-5 bp range covers 50 of the 51 names (1.8x heavy in the bottom
> decile).
>
> HalfSpreadSlippage halves spread_bps, so the runs labelled 1 bp
> charged 0.5 bp one way, which is 3.4x light for the median name and
> 5.5x light in the bottom decile.

What the backtester cannot see from daily bars, this repo measured from
the book. Its cost model is a taker's model: cross the spread, pay half
of it, and the provider is nobody. The tape says the provider on these
books was paid about 1.7 bps one way and gave it all back within five
minutes, which is adverse selection measured rather than assumed, and
it is the same fact as Week 6's: the flow that fits the price so well
inside an interval (R-squared 0.41) is the flow that costs whoever is
resting at the touch. A strategy that turns over seven times a year
pays the taker's side of that on every trade and nothing else; the
backtester's conclusion
that costs are decided by turnover rather than by the cost model is the
daily-bar view of the same arithmetic, and it survived its audit.

## This repo's numbers the backtester's lessons shaped

| The backtester's habit | Its number | This repo's version | Its number |
|---|---|---|---|
| A trial registry that is also the cache, so a deflated Sharpe divides by every look taken | 47 registered trials; best cell 0.75 raw, 0.96 deflated | `results/trials.csv` from the first analysis run, append-only, a number logged before it is printed; Week 10 divides by it | 89 rows: 19 first look, 58 prediction, 12 audit |
| `data/` gitignored by design, because CRSP is licensed and never ships; a fabricated panel reproduces every code path without it | "No WRDS subscription, no market data, about half an hour end to end" | `data/` gitignored (LOBSTER, ITCH, TAQ); results whitelisted file by file; the per-event OFI series regenerated rather than shipped; a synthetic tape exercises everything | 146 tests, every one of them on fiction or hand-built fixtures |
| The expected number written before the data arrives ("hundreds, not zero" delistings caught a WHERE clause deleting every one) | 5.48 pp/yr, nearly shipped without its delistings | "Expected, written before the run" on every results page, graded afterwards, failures kept | Day 6: four of nine held; Day 7: the bottom-decile, Lee-Ready and cancel-to-trade lines failed and stayed, and two of the audit's four lines under each reading |
| A Limitations section: "A backtest without this section should not be believed" | twelve bullets | "A claim without this section should not be believed" | eleven bullets, two added this week |
| "Why these numbers changed": a published figure revised when the input data was found wrong, with the invariant that caught it | capacity wall $2.70bn to $3.27bn | "Rows are not shares": a predicted sign kept in the write-up beside the decomposition that explains why it was wrong | depth low by 7% at the touch, 18% over ten levels |
| Report across a range rather than at a point when a parameter is an assumption | spread 1-5 bp, impact Y 0.3 to 1.5 | Audit both readings of the parameter rather than pick one | 1 bp and 0.5 bp, twelve rows |

## The two sentences

> **The backtester** (README, "What survivorship bias was worth"): The
> gap is 5.48 percentage points a year, and it is two errors measured
> jointly, not one. And, one section later: The strategy lost to the
> basket it picks from, and that is the finding.

> **This repo** (`results/ofi_prediction.md`): OFI predicts the next
> interval's mid change at a horizon of 5 seconds with a hit rate of
> 54.3% (plus or minus 2.3 points), and the edge is consumed by half
> the spread 11 times over, 0.59 cents gross per signal against a
> half-spread of 6.5 cents.

Each is the honest answer the project was built to be able to give,
and each is a cost of a kind. The backtester's number is what a
backtest gives away by choosing its universe with information it did
not have; this repo's is what a signal gives away to the spread. Both
carry their window (2015 to 2025, point in time; 2012-06-21, one name,
a within-day split) and their trial count (47; 77 at the time, 89 now).
The two now share a measurement. The half-spread that consumes Week 6's
edge eleven times over is 6.5 cents on a $583 stock, 1.1 bps one way
from Nasdaq's quotes in the afternoon; the consolidated tape puts
AAPL's quoted half-spread at 5.8 cents (0.99 bps) and what takers
actually paid at 0.71 bps, the tightest name in the backtester's
universe and still above the 0.5 bp its code charged. The thing that
beat the signal is the thing the backtester charges for, and the two
repos now agree on its size because they measured it from opposite
ends: one from the book that quoted it, one from the tape that paid
it.

## What changes, and what does not

- **In the backtester:** its README now states what `HalfSpreadSlippage`
  charges and cites this audit, which is a commit in that repo made
  with this one. Its results do not move: the correction is under 0.01
  Sharpe at its turnover, and whether the parameter should be renamed
  or the fill should stop halving it is a decision for that repo, with
  this page as the citation either way.
- **In this repo:** the Week 6 sentence does not change; Week 7 added
  a universe (fifty names where there had been one) and a second
  instrument (the consolidated tape beside the reconstructed book), and
  the second LOBSTER name is still owed. `results/liquidity_spectrum.md`
  says where AAPL sits (the top 2% of 4,939 securities by lit trades;
  every LOBSTER sample name in the top 5%), which is why the long tail
  had to be measured in spreads from TAQ rather than in order flow from
  LOBSTER.
- **The bridge crosses a decade and one day.** The backtester's results
  are 2015 to 2025; the spreads are 2012-06-21's, round lots only, five
  names per decile. The audit says where the assumption was right on
  the day the tape was pulled, not across the backtester's window, and
  a second day is a rerun of `scripts/taq_spreads.py` with another
  `--date` and of `scripts/spread_audit.py` after it.
