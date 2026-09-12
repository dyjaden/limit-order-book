# limit-order-book

Limit order book reconstruction and short-horizon prediction, built to be
verified. A market is a state machine and the feed is its event log. This
project folds exchange messages into the book state they imply, exactly
and reproducibly, and every eventual claim will carry its window and its
trial count.

It is the second project in a sequence. The
[event-driven-backtester](https://github.com/dyjaden/event-driven-backtester)
measured why daily-bar backtests lie (survivorship bias: 5.48 pp/yr,
measured). This one goes a level down, to how prices actually get made.
Later on, its Week 7 audits the backtester's own spread assumption
against measured TAQ data, so the two repos end up checking each other.

## Results (measured, not claimed)

One real day so far: AAPL, 2012-06-21, LOBSTER sample data, replayed
message by message and graded against LOBSTER's own published book
states after every single one of 400,390 messages. Zero anomalies,
zero per-order inconsistencies, 68.4% of rows exact at the touch, and
**every divergent row machine-classified into one of four named classes
of the file's own information limit, with zero rows unexplained**. The
headline finding: a level-filtered feed file is provably not a
self-contained event log, and we traced a named order through the exact
mechanism (added in view, deleted below the visible band, resurfacing
as a phantom). Full write-up, tables, and the falsifiable prediction
for the raw-ITCH replay: [`results/lobster_validation.md`](results/lobster_validation.md).

That prediction then went on trial, in two parts. The book was rebuilt
from Nasdaq's binary TotalView-ITCH 5.0 format, and on unfiltered data
the repair machinery must never fire: a full-depth synthetic day
(120,013 messages written by our own ITCH writer and round-tripped
field for field) replays from an empty book with every counter at zero,
including a front-of-queue assertion on all 16,963 executions. Then we
became the filter: our own full-depth ground truth cut to K levels and
fed to the unchanged Week 2 machinery reproduces **all four divergence
classes with zero rows unexplained, and the machinery's dark-liquidity
caseload is predicted exactly from the events the filter hid** (K=3:
2,854 predicted, 2,854 observed; the same at K=1, 5 and 10). The real
ITCH day has not run yet, so the prediction stands on synthetic data
only. Numbers, the filter's measured cost, and the Python throughput
baseline: [`results/itch_baseline.md`](results/itch_baseline.md).

## Status

Week 1 (2 September 2026): the book core, its invariant suite, and a
synthetic message tape, all in Python. The suite caught a real bug
before any data existed: a refused amend was silently sending the
original order to the back of its queue (details in the `Book.replace`
docstring). Week 2 (9 September): LOBSTER replay and the row-by-row
reference validation above, which added `reduce`, the seed, the
dark-liquidity rule, ghost eviction, and the witness rule. Week 3 (9 to
11 September): the ITCH 5.0 parser with hand-built byte fixtures and a
binary fiction writer, the full-depth replayer from an empty book, the
self-filter experiment, and the throughput baseline (parse, replay and
end to end timed separately, medians with spread, machine named).
**55 tests passing**, CI green on every push. Next is Week 4:
descriptive microstructure (spread and depth through the day, queue
lifetimes, cancel-to-trade ratios), each checked against published
sample statistics before anything downstream trusts it.

## Design commitments, stated before the results exist

- **Prices are integer ticks.** Float money is how backtests lie by
  fractions of a cent. The tick size is display metadata, never
  arithmetic.
- **This is a reconstruction book, not a matching engine.** It applies an
  exchange's message log; it does not match orders itself. A crossing add
  is refused loudly, because a real feed never delivers one (the exchange
  would have matched it first). Feed corruption becomes noise you hear.
- **Replace loses queue position by construction** (cancel plus re-add),
  because price-time priority says it must, and a rule the structure
  enforces cannot be forgotten by an implementation.
- **Fiction before data.** A synthetic message tape exercises everything
  before any real file is parsed. When a real replay disagrees with a
  reference, the book should not be the suspect.
- **The trial registry exists before the first prediction is attempted.**

## Reproduce

```bash
git clone https://github.com/dyjaden/limit-order-book.git
cd limit-order-book
python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
python -m pytest -q                  # 55 passed
python scripts/make_fake_tape.py     # 100k synthetic messages, invariants
                                     # checked at every step
python scripts/make_fake_itch.py     # the same fiction as ITCH 5.0 bytes,
                                     # written to data/_fake/fake_itch5.bin
python scripts/replay_itch.py        # full-depth replay from an empty book;
                                     # exits nonzero unless every counter is 0
python scripts/filter_experiment.py  # the self-filter experiment (~10 s)
python scripts/replay_itch.py --bench --write results/itch_baseline.md
```

The two LOBSTER scripts (`scripts/replay_lobster.py --level 10
--validate`) need the free AAPL 2012-06-21 sample files in
`data/lobster/`; `results/lobster_validation.md` names them.

CI runs the install-and-test path on every push, so the stranger's route
stays proven. `data/` is gitignored by design: LOBSTER, ITCH, and TAQ
data carry redistribution restrictions and never ship. The synthetic tape
exists so the machinery is verifiable without any of them.

## Limitations

Kept explicit from the first commit. A claim without this section should
not be believed.

- **Real data so far is one ticker on one day**: AAPL, 2012-06-21,
  from LOBSTER's free sample files. Every real-data number in this
  README carries that window. The reference validation is a validation
  of level totals; queue composition inside seeded levels is synthetic
  and is not validated.
- **The ITCH path has met fixtures and fiction, not yet a real day.**
  The parser is pinned by hand-built bytes for every decoded type and
  round-trips a synthetic day written by our own writer; the real
  TotalView-ITCH day (a 2 to 6 GB download) is still to run, and until
  it does the Week 2 prediction is confirmed on synthetic data only.
- **Throughput is one machine, one Python, whole-run rates.** The
  baseline names its machine and reports medians with spread; it quotes
  no latency percentiles, which wait for Week 9's harness.
- **Python only, by design, for now.** Correctness is the current
  product. The C++ twin and latency percentiles are planned as supporting
  work, and they are the first thing cut if time runs short.
- **One venue's semantics.** The book implements Nasdaq-style price-time
  priority. Other venues (pro-rata, hidden liquidity, auctions) are out
  of scope and will be named as such, not silently assumed away.

## License

MIT.
