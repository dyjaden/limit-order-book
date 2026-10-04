"""The honest evaluation: does last-interval OFI predict the next interval?

    python scripts/ofi_prediction.py --ticker AAPL --part decay
    python scripts/ofi_prediction.py --ticker AAPL --part regimes
    python scripts/ofi_prediction.py --ticker AAPL --part cost
    python scripts/ofi_prediction.py --ticker AAPL --part writeup --figure

The question was frozen in the Day 6 guide before any of this ran, and
``lob.predict`` is that specification as code. Each part of this script
runs its own trials, logs every one in results/trials.csv BEFORE any
number is formatted or printed (``lob.registry``), and writes one CSV
with every number and its trial id:

    decay    six horizons, two predictors (OFI, and the last mid change
             as the baseline), two split directions: results/ofi_decay_{ticker}.csv
    regimes  open, close, midday, high-vol, quiet at the key horizon,
             fitted on the complement of each: results/ofi_regimes_{ticker}.csv
    cost     the gross edge per signal beside the half-spread at the
             time, all signals and the strongest decile, latency 0 and 1:
             results/ofi_cost_{ticker}.csv
    writeup  reads the three CSVs, runs no regression, splices the
             tables and the graded expectations into results/ofi_prediction.md
             and (with --figure) draws results/figures/ofi_prediction.png

The parts are separate so that a later step never re-logs an earlier
step's trials; a re-run of a part appends again, which is the rule.
The write-up and the figure read the CSVs, so a figure tweak never adds
a row. The split is within the day (fit before 12:45, test after)
because there is one day, and every page that quotes a number says so.

Week 6 rows A to D of the plan.
"""
from __future__ import annotations

import argparse
import csv
import sys
from dataclasses import dataclass, field
from pathlib import Path

from intraday_report import DATE, SERIES, paths
from lob.lobster import read_messages, read_orderbook
from lob.micro import NS, hms, time_weighted
from lob.ofi import aggregate_calendar, events, touches
from lob.predict import (HORIZONS_S, KEY_S, PREDICTORS, SPLIT_NS, Forecast,
                         Pair, evaluate, pairs, split)
from lob.registry import REGISTRY, log_trial

SCRIPT = "ofi_prediction.py"
KSH = 1_000
QUESTIONS = {
    "ofi": "predictive: next-bucket mid change (cents) on this bucket's OFI "
           "(shares), through the origin",
    "last": "predictive baseline: next-bucket mid change (cents) on this bucket's "
            "mid change (cents), through the origin",
}
UNITS = {"ofi": "cents/kshare", "last": "cents/cent"}
DIRECTIONS = (("primary", "first-half/second-half", False),
              ("reversed", "second-half/first-half", True))


# ------------------------------------------------------------- inputs
@dataclass
class Day:
    ticker: str
    level: int
    msgs: list
    ref: list
    evs: list
    _cache: dict = field(default_factory=dict)

    def pairs_at(self, h_s: int) -> list[Pair]:
        """Pairs at horizon h: buckets from Day 5's aggregator, spreads
        from Day 4's time-weighted bins on the same edges."""
        if h_s not in self._cache:
            width = int(round(h_s * NS))
            buckets = aggregate_calendar(self.evs, width)
            bins = time_weighted(self.msgs, self.ref, width)
            if len(bins) != len(buckets):
                raise ValueError("bucket and bin counts differ")
            spreads = [b.tw_spread_cents for b in bins]
            self._cache[h_s] = pairs(buckets, spreads)
        return self._cache[h_s]


def load(args) -> Day:
    msg_path, book_path = paths(Path(args.data), args.ticker, args.level)
    for p in (msg_path, book_path):
        if not p.exists():
            raise SystemExit(f"no such file: {p}")
    msgs = read_messages(msg_path)
    ref = read_orderbook(book_path, args.level)
    evs = list(events(touches(msgs, ref)))
    return Day(args.ticker, args.level, msgs, ref, evs)


def slope(f: Forecast) -> float | None:
    """The fitted slope in the units the tables quote."""
    if f.beta is None:
        return None
    return f.beta * KSH if f.predictor == "ofi" else f.beta


def fmt(x, nd=3, plus=False) -> str:
    if x is None:
        return "n/a"
    return f"{x:+.{nd}f}" if plus else f"{x:.{nd}f}"


def pct(x, nd=1) -> str:
    return "n/a" if x is None else f"{100 * x:.{nd}f}%"


def note_for(f: Forecast) -> str:
    return (f"beta={fmt(slope(f), 5, True)} {UNITS[f.predictor]}; t={fmt(f.t_stat, 2)}; "
            f"r2_oos={fmt(f.r2_oos, 5)}; z={fmt(f.z, 2)}; band=+/-{fmt(f.band, 4)}; "
            f"edge={fmt(f.edge_cents, 4)} cents over {f.n_signals} signals; "
            f"n_fit={f.n_fit}; n_test={f.n_test}; both_nonzero={f.both_nonzero}; "
            f"excluded={f.excluded}")


# -------------------------------------------------------------- decay
DECAY_COLUMNS = ("horizon_s", "direction", "split", "predictor", "n_fit", "n_test",
                 "beta", "t_stat", "r2_oos", "hits", "both_nonzero", "excluded",
                 "hit_rate", "z", "band", "clears_band", "edge_cents", "n_signals",
                 "trial_id")


def decay_rows(day: Day, registry: Path, horizons=HORIZONS_S,
               split_ns: int = SPLIT_NS, script: str = SCRIPT) -> list[dict]:
    """Every fit of the decay curve, each logged the moment it exists.
    Nothing here prints."""
    rows = []
    for h in horizons:
        ps = day.pairs_at(h)
        for direction, split_name, reverse in DIRECTIONS:
            fit, test = split(ps, split_ns, reverse)
            for predictor in PREDICTORS:
                f = evaluate(fit, test, predictor)
                tid = log_trial(registry, script=script, question=QUESTIONS[predictor],
                                ticker=day.ticker, date=DATE, clock="calendar",
                                bucket=h, horizon=h, window="09:30-16:00",
                                split=split_name, n_obs=f.n_test, metric="hit_rate",
                                value=f.hit_rate, status="predictive",
                                note=note_for(f) + f"; level {day.level}")
                rows.append({"horizon_s": h, "direction": direction, "split": split_name,
                             "predictor": predictor, "n_fit": f.n_fit, "n_test": f.n_test,
                             "beta": slope(f), "t_stat": f.t_stat, "r2_oos": f.r2_oos,
                             "hits": f.hits, "both_nonzero": f.both_nonzero,
                             "excluded": f.excluded, "hit_rate": f.hit_rate, "z": f.z,
                             "band": f.band, "clears_band": f.clears_band,
                             "edge_cents": f.edge_cents, "n_signals": f.n_signals,
                             "trial_id": tid})
    return rows


def write_csv(rows: list[dict], columns, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(columns), lineterminator="\n")
        w.writeheader()
        for r in rows:
            w.writerow({c: ("" if r.get(c) is None else
                            (repr(r[c]) if isinstance(r[c], float) else r[c]))
                        for c in columns})


def read_csv(path: Path) -> list[dict]:
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def hlabel(h) -> str:
    h = float(h)
    return f"{h:g} s" if h < 60 else f"{h / 60:g} min"


def print_decay(rows: list[dict], ticker: str) -> None:
    ids = [r["trial_id"] for r in rows]
    print(f"HORIZON DECAY {ticker} {DATE}: out of sample, split at {hms(SPLIT_NS)}; "
          f"registry rows #{min(ids)} to #{max(ids)}")
    for direction, split_name, _ in DIRECTIONS:
        print(f"\n  {direction} split ({split_name}):")
        print(f"  {'horizon':<8} {'pred':<5} {'n_fit':>6} {'n_test':>6} {'slope':>9} "
              f"{'t':>6} {'R2 oos':>8} {'hit rate':>9} {'band':>7} {'z':>6} "
              f"{'edge c':>8} {'signals':>7} trial")
        for r in rows:
            if r["direction"] != direction:
                continue
            mark = "*" if r["clears_band"] else " "
            print(f"  {hlabel(r['horizon_s']):<8} {r['predictor']:<5} {r['n_fit']:>6,} "
                  f"{r['n_test']:>6,} {fmt(r['beta'], 4, True):>9} {fmt(r['t_stat'], 1):>6} "
                  f"{fmt(r['r2_oos'], 4, True):>8} {pct(r['hit_rate']):>8}{mark} "
                  f"{('+/-' + pct(r['band'])) if r['band'] is not None else 'n/a':>7} "
                  f"{fmt(r['z'], 1, True):>6} {fmt(r['edge_cents'], 3, True):>8} "
                  f"{r['n_signals']:>7,} #{r['trial_id']}")
    print("\n  slope in cents per 1,000 shares (ofi) or cents per cent (last); "
          "* = hit rate above the 95% band around one half")


def part_decay(args, day: Day, registry: Path, out: Path) -> list[dict]:
    rows = decay_rows(day, registry, horizons=HORIZONS_S, split_ns=SPLIT_NS)
    # everything is logged; now, and only now, the numbers may be seen
    print_decay(rows, day.ticker)
    target = out / f"ofi_decay_{day.ticker}.csv"
    write_csv(rows, DECAY_COLUMNS, target)
    print(f"\n  wrote {target} ({len(rows)} rows)")
    return rows


# --------------------------------------------------------------- main
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ticker", default="AAPL")
    ap.add_argument("--level", type=int, default=10, choices=(1, 5, 10))
    ap.add_argument("--data", default="data/lobster")
    ap.add_argument("--out", default="results")
    ap.add_argument("--registry", default=str(REGISTRY))
    ap.add_argument("--part", choices=("decay", "regimes", "cost", "writeup"),
                    required=True)
    ap.add_argument("--figure", action="store_true")
    args = ap.parse_args()
    out, registry = Path(args.out), Path(args.registry)
    if args.part == "decay":
        part_decay(args, load(args), registry, out)
    else:
        raise SystemExit(f"--part {args.part} is built in a later step")


if __name__ == "__main__":
    main()
