# limit-order-book

Limit order book reconstruction and short-horizon prediction, built to be
verified. A market is a state machine and the feed is its event log; this
project folds exchange messages into the book state they imply — exactly,
reproducibly, and with every eventual claim carrying its window and its
trial count.

Second project in a sequence: the
[event-driven-backtester](https://github.com/dyjaden/event-driven-backtester)
measured why daily-bar backtests lie (survivorship: 5.48 pp/yr, measured).
This one goes one level down, to how prices actually get made — and its
Week 7 audits the backtester's own spread assumption against measured TAQ
data, so the two repos check each other.

## Status

Day 1 (2 September 2026). Scaffold, CI, and the plan. The book core,
invariants, and synthetic message tape land this week; LOBSTER reference
validation is Week 2. Results will appear above this line as they are
measured, not before.

## Design commitments, stated before the code exists

- **Prices are integer ticks.** Float money is how backtests lie by
  fractions of a cent; the tick size is display metadata, never arithmetic.
- **This is a reconstruction book, not a matching engine.** It applies an
  exchange's message log; it does not itself match orders. A crossing add
  is refused loudly, because a real feed never delivers one — the exchange
  would have matched it. Feed corruption becomes noise you hear.
- **Replace loses queue position by construction** (cancel plus re-add),
  because price-time priority says it must, and a rule the structure
  enforces cannot be forgotten by an implementation.
- **Fiction before data.** A synthetic message tape exercises everything
  before any real file is parsed, so when a real replay disagrees with a
  reference, the book is not the suspect.
- **The trial registry exists before the first prediction is attempted.**

## Reproduce

```bash
git clone https://github.com/dyjaden/limit-order-book.git
cd limit-order-book
python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
python -m pytest -q
```

CI runs exactly this on every push — the stranger's path, automated.
`data/` is gitignored by design: LOBSTER, ITCH, and TAQ data carry
redistribution restrictions and never ship; the synthetic tape exists so
the machinery is verifiable without any of them.

## Limitations

Kept deliberately explicit from the first commit. A claim without this
section should not be believed.

- **Nothing has run against real data yet.** Until the Week 2 validation —
  matching LOBSTER's own published book levels row by row — every test is
  against synthetic messages and hand-computed answers.
- **Python only, by design, until Week 8.** Correctness is the current
  product; the C++ twin and latency percentiles are supporting work and
  the plan's first cut line.
- **One venue's semantics.** The book implements Nasdaq-style price-time
  priority; other venues (pro-rata, hidden liquidity, auctions) are out of
  scope and will be named, not silently assumed away.

## License

MIT.
