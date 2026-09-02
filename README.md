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

## Status

Week 1 done (2 September 2026): the book core, its invariant suite, and a
synthetic message tape, all in Python. **17 tests passing**, CI green on
every push. The suite already earned its keep by catching a real bug
before any data existed: a refused amend was silently sending the
original order to the back of its queue (details in the `Book.replace`
docstring). Next is Week 2: replay LOBSTER's free sample data and match
their published book levels row by row. Results will appear above this
line as they are measured, not before.

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
python -m pytest -q                  # 17 passed
python scripts/make_fake_tape.py     # 100k synthetic messages, invariants
                                     # checked at every step
```

CI runs the install-and-test path on every push, so the stranger's route
stays proven. `data/` is gitignored by design: LOBSTER, ITCH, and TAQ
data carry redistribution restrictions and never ship. The synthetic tape
exists so the machinery is verifiable without any of them.

## Limitations

Kept explicit from the first commit. A claim without this section should
not be believed.

- **Nothing has run against real data yet.** Until the Week 2 validation
  (matching LOBSTER's own published book levels row by row), every test
  is against synthetic messages and hand-computed answers.
- **Python only, by design, for now.** Correctness is the current
  product. The C++ twin and latency percentiles are planned as supporting
  work, and they are the first thing cut if time runs short.
- **One venue's semantics.** The book implements Nasdaq-style price-time
  priority. Other venues (pro-rata, hidden liquidity, auctions) are out
  of scope and will be named as such, not silently assumed away.

## License

MIT.
