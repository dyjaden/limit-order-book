# TAQ spreads: what a taker paid

Quoted, effective and realized spreads from the consolidated tape for
the backtester's universe on one day, in basis points of the mid. Week
7 row B of the plan, and the bridge to the
[event-driven-backtester](https://github.com/dyjaden/event-driven-backtester):
its transaction-cost assumption is in basis points per side, and this
page measures the market it stands in for.

## What is measured, and how

The universe is the S&P 500 on the day as CRSP records it (the
backtester's own membership table), each member with its closing price
and share volume that day, sorted into dollar-volume deciles; a fixed
number of names is taken from each decile, evenly spaced inside it, so
the sample spans the index from its thinnest tenth to its heaviest. For
each name the day's NBBO and trades come from WRDS TAQ (`taqm_2012`),
regular hours only, correction code 00, the usual sale conditions
dropped, cached once under `data/taq/` (gitignored; TAQ is licensed).

Three spreads per name, all in basis points of the prevailing mid
(`src/lob/taq.py`, pinned by hand in `tests/test_taq.py`): the quoted
spread, time-weighted over the session the way Day 4 weighs book
states; the effective spread, 2 q (P − M) / M per trade with q from Lee
and Ready's rule (above the mid a buy, below a sell, at the mid the
tick test), averaged simply and by dollar volume; and the realized
spread, 2 q (P − M five minutes later) / M, with the price impact as
their difference. Half the effective spread is the one-way cost of
crossing, the number the backtester's assumption is measured against in
Step 3. The 2012 consolidated tape excludes odd lots, which LOBSTER
includes and which were more than half of AAPL's executions that day,
so these are the round-lot taker's spreads. Nothing on this page is a
regression and no registry row is added here.

<!-- taq:begin -->
The pull has not run yet. The connection to WRDS waits for the account's
two-factor approval, which only the account's owner can give, so the
measurement runs the moment that approval is at hand:
`python scripts/taq_spreads.py --date 2012-06-21 --sp500 --per-decile 5 --username <wrds user>`.
<!-- taq:end -->

## Expected, written before the run

The lines below were written in the Day 7 guide before the pull first
ran, and stay here whether or not they were right.

- Across the backtester's universe on one day in 2012: median half
  effective spread 1.5 to 3 bps; about 1 bp in the top dollar-volume
  decile; 5 to 10 bps in the bottom decile.
- The realized spread is below the effective spread in every decile,
  so the price impact is positive everywhere: the provider keeps less
  than the taker pays.
- AAPL's NBBO quoted spread is 20 to 40% narrower than the 15.13 cents
  Day 4 measured on Nasdaq alone, because the consolidated best bid and
  offer is drawn from every venue.
- Lee and Ready's rule leaves under 5% of trades unsigned.
- The quoted spread in cents is a single cent for the large-tick names
  and tens of cents for the highest-priced ones; in basis points the
  ordering reverses, and the basis-point ranking is the one that
  follows dollar volume.

## Limitations

- **One day.** Spreads move with volatility; 2012-06-21 is an ordinary
  Thursday and nothing more, and a second day is a rerun with a
  different `--date`.
- **A sample of the index, not the index.** Five names per decile span
  the distribution; they do not average it. The decile medians are
  medians of five.
- **Round lots only.** The 2012 tape excluded odd lots until December
  2013, and on a $600 stock most trades were odd lots.
- **Lee and Ready is a rule, not a record.** The tape does not say who
  initiated a trade; the rule guesses from the mid and the tick, and
  its unsigned share is reported beside every number.
- **The pull is the owner's.** The WRDS connection requires the
  account's two-factor approval, so the script runs on the account
  owner's machine with the owner present; the computation from the
  cached pulls needs neither.
