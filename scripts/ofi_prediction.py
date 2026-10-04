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
                         Pair, Verdict, cost_verdict, evaluate, leave_out, pairs,
                         regimes, split)
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


# ------------------------------------------------------------ regimes
REGIME_COLUMNS = ("regime", "kind", "label", "horizon_s", "predictor", "n_fit", "n_test",
                  "beta", "t_stat", "r2_oos", "hits", "both_nonzero", "excluded",
                  "hit_rate", "z", "band", "verdict", "edge_cents", "n_signals",
                  "trial_id")


def verdict_word(f: Forecast) -> str:
    """Where the regime's hit rate sits against the band a coin would
    produce with this many calls: above it, inside it, or below it."""
    if f.hit_rate is None:
        return "no call"
    if f.hit_rate - 0.5 > f.band:
        return "above"
    if 0.5 - f.hit_rate > f.band:
        return "below"
    return "inside"


def regime_rows(day: Day, registry: Path, key_s: int = KEY_S,
                split_ns: int = SPLIT_NS, script: str = SCRIPT) -> list[dict]:
    """Every regime fit, each logged the moment it exists. Nothing here
    prints."""
    ps = day.pairs_at(key_s)
    rows = []
    for rg in regimes(ps, split_ns):
        fit, test = leave_out(ps, rg)
        split_name = f"all-but-{rg.kind}/{rg.kind}"
        for predictor in PREDICTORS:
            f = evaluate(fit, test, predictor)
            tid = log_trial(registry, script=script,
                            question=QUESTIONS[predictor] + f" ({rg.name} regime, fitted outside it)",
                            ticker=day.ticker, date=DATE, clock="calendar", bucket=key_s,
                            horizon=key_s, window=rg.label if rg.kind == "window" else rg.name,
                            split=split_name, n_obs=f.n_test, metric="hit_rate",
                            value=f.hit_rate, status="predictive",
                            note=note_for(f) + f"; verdict={verdict_word(f)}; "
                                 f"regime={rg.label}; level {day.level}")
            rows.append({"regime": rg.name, "kind": rg.kind, "label": rg.label,
                         "horizon_s": key_s, "predictor": predictor, "n_fit": f.n_fit,
                         "n_test": f.n_test, "beta": slope(f), "t_stat": f.t_stat,
                         "r2_oos": f.r2_oos, "hits": f.hits, "both_nonzero": f.both_nonzero,
                         "excluded": f.excluded, "hit_rate": f.hit_rate, "z": f.z,
                         "band": f.band, "verdict": verdict_word(f),
                         "edge_cents": f.edge_cents, "n_signals": f.n_signals,
                         "trial_id": tid})
    return rows


def print_regimes(rows: list[dict], ticker: str) -> None:
    ids = [r["trial_id"] for r in rows]
    print(f"REGIMES {ticker} {DATE}: {hlabel(rows[0]['horizon_s'])} horizon, each regime "
          f"fitted on its complement and tested inside; registry rows #{min(ids)} to #{max(ids)}")
    print(f"  {'regime':<9} {'pred':<5} {'n_fit':>6} {'n_test':>6} {'slope':>9} {'t':>6} "
          f"{'R2 oos':>8} {'hit rate':>9} {'band':>7} {'z':>6} {'verdict':<8} {'edge c':>8} trial")
    for r in rows:
        print(f"  {r['regime']:<9} {r['predictor']:<5} {r['n_fit']:>6,} {r['n_test']:>6,} "
              f"{fmt(r['beta'], 4, True):>9} {fmt(r['t_stat'], 1):>6} {fmt(r['r2_oos'], 4, True):>8} "
              f"{pct(r['hit_rate']):>9} {('+/-' + pct(r['band'])) if r['band'] is not None else 'n/a':>7} "
              f"{fmt(r['z'], 1, True):>6} {r['verdict']:<8} {fmt(r['edge_cents'], 3, True):>8} "
              f"#{r['trial_id']}")
    labels = {r["regime"]: r["label"] for r in rows}
    for name, label in labels.items():
        print(f"    {name}: {label}")


def part_regimes(args, day: Day, registry: Path, out: Path) -> list[dict]:
    rows = regime_rows(day, registry, key_s=KEY_S, split_ns=SPLIT_NS)
    # everything is logged; now, and only now, the numbers may be seen
    print_regimes(rows, day.ticker)
    target = out / f"ofi_regimes_{day.ticker}.csv"
    write_csv(rows, REGIME_COLUMNS, target)
    print(f"\n  wrote {target} ({len(rows)} rows)")
    return rows


# --------------------------------------------------------------- cost
COST_COLUMNS = ("horizon_s", "subset", "latency", "threshold", "n_signals", "hits",
                "both_nonzero", "hit_rate", "gross_cents", "half_spread_cents",
                "net_one_leg", "net_two_legs", "beat_half_spread", "consumed",
                "beta_trial_id", "trial_id")
SUBSETS = ("all", "top-decile")
LATENCIES = (0, 1)


def decay_betas(decay_rows: list[dict]) -> dict[float, tuple[float, str, bool]]:
    """Per horizon: the primary-split OFI slope (cents per share), the
    trial id it was logged under, and whether its hit rate cleared the
    band; read from the decay CSV so the verdict never refits."""
    out = {}
    for r in decay_rows:
        if r["direction"] == "primary" and r["predictor"] == "ofi" and r["beta"]:
            out[float(r["horizon_s"])] = (float(r["beta"]) / KSH, r["trial_id"],
                                          str(r["clears_band"]) == "True")
    return out


def cost_rows(day: Day, registry: Path, betas: dict, horizons=HORIZONS_S,
              split_ns: int = SPLIT_NS, script: str = SCRIPT) -> list[dict]:
    """Every verdict, each logged the moment it exists. Nothing here
    prints."""
    rows = []
    for h in horizons:
        if float(h) not in betas:
            continue
        beta, beta_trial, _ = betas[float(h)]
        fit, test = split(day.pairs_at(h), split_ns)
        for subset in SUBSETS:
            for latency in LATENCIES:
                v = cost_verdict(fit, test, beta, subset, latency)
                tid = log_trial(
                    registry, script=script,
                    question=(f"cost: gross edge per signal of trading sign(beta x) from trial "
                              f"#{beta_trial}, {subset} signals, latency {latency} bucket(s), "
                              f"against the half-spread at entry"),
                    ticker=day.ticker, date=DATE, clock="calendar", bucket=h, horizon=h,
                    window="12:45-16:00", split="first-half/second-half", n_obs=v.n_signals,
                    metric="gross_edge_cents", value=v.gross_cents, status="predictive",
                    note=(f"half_spread={fmt(v.half_spread_cents, 4)} cents; "
                          f"net_one_leg={fmt(v.net_one_leg, 4)}; net_two_legs={fmt(v.net_two_legs, 4)}; "
                          f"beat_half_spread={fmt(v.beat_half_spread, 4)}; "
                          f"consumed={fmt(v.consumed, 2)}x; hit_rate={fmt(v.hit_rate, 4)} over "
                          f"{v.both_nonzero}; threshold={fmt(v.threshold, 0)} shares; level {day.level}"))
                rows.append({"horizon_s": h, "subset": subset, "latency": latency,
                             "threshold": v.threshold, "n_signals": v.n_signals,
                             "hits": v.hits, "both_nonzero": v.both_nonzero,
                             "hit_rate": v.hit_rate, "gross_cents": v.gross_cents,
                             "half_spread_cents": v.half_spread_cents,
                             "net_one_leg": v.net_one_leg, "net_two_legs": v.net_two_legs,
                             "beat_half_spread": v.beat_half_spread, "consumed": v.consumed,
                             "beta_trial_id": beta_trial, "trial_id": tid})
    return rows


def sentence(decay: list[dict], cost: list[dict]) -> str:
    """The deliverable sentence with X, Y and Z from the run: the
    horizon with the largest gross edge among those whose primary-split
    hit rate cleared the band, or the plain statement that none did."""
    cleared = {float(r["horizon_s"]): r for r in decay
               if r["direction"] == "primary" and r["predictor"] == "ofi"
               and str(r["clears_band"]) == "True"}
    if not cleared:
        return ("OFI does not predict the next interval's mid change at any horizon "
                "from one second to five minutes on this day: no hit rate clears the "
                "95% band around one half.")
    best = max((r for r in cost if r["subset"] == "all" and int(r["latency"]) == 0
                and float(r["horizon_s"]) in cleared and r["gross_cents"] is not None),
               key=lambda r: float(r["gross_cents"]))
    d = cleared[float(best["horizon_s"])]
    return (f"OFI predicts the next interval's mid change at a horizon of "
            f"{hlabel(best['horizon_s'])} with a hit rate of {pct(float(d['hit_rate']))} "
            f"(plus or minus {pct(float(d['band']))}), and the edge is consumed by half the "
            f"spread {float(best['consumed']):.0f} times over: {float(best['gross_cents']):.2f} "
            f"cents gross per signal against a half-spread of "
            f"{float(best['half_spread_cents']):.1f} cents.")


def print_cost(rows: list[dict], ticker: str, line: str) -> None:
    ids = [r["trial_id"] for r in rows]
    print(f"COST VERDICT {ticker} {DATE}: test half only, entered at the end of the signal's "
          f"bucket (latency 0) or one bucket later (latency 1); registry rows #{min(ids)} to #{max(ids)}")
    print(f"  {'horizon':<8} {'subset':<10} {'lat':>3} {'signals':>7} {'hit rate':>9} "
          f"{'gross c':>8} {'half spr':>9} {'net 1 leg':>10} {'net 2 legs':>11} "
          f"{'beat %':>7} {'consumed':>9} trial")
    for r in rows:
        print(f"  {hlabel(r['horizon_s']):<8} {r['subset']:<10} {r['latency']:>3} "
              f"{r['n_signals']:>7,} {pct(r['hit_rate']):>9} {fmt(r['gross_cents'], 3, True):>8} "
              f"{fmt(r['half_spread_cents'], 2):>9} {fmt(r['net_one_leg'], 3, True):>10} "
              f"{fmt(r['net_two_legs'], 3, True):>11} {pct(r['beat_half_spread'], 0):>7} "
              f"{(fmt(r['consumed'], 1) + 'x') if r['consumed'] is not None else 'n/a':>9} "
              f"#{r['trial_id']}")
    print("\n  gross and nets in cents per signal; consumed = half-spread over gross edge")
    print(f"\n  {line}")


def part_cost(args, day: Day, registry: Path, out: Path) -> list[dict]:
    decay_path = out / f"ofi_decay_{day.ticker}.csv"
    if not decay_path.exists():
        raise SystemExit(f"no {decay_path}; run --part decay first")
    decay = read_csv(decay_path)
    rows = cost_rows(day, registry, decay_betas(decay), horizons=HORIZONS_S,
                     split_ns=SPLIT_NS)
    # everything is logged; now, and only now, the numbers may be seen
    print_cost(rows, day.ticker, sentence(decay, rows))
    target = out / f"ofi_cost_{day.ticker}.csv"
    write_csv(rows, COST_COLUMNS, target)
    print(f"\n  wrote {target} ({len(rows)} rows)")
    return rows


# ------------------------------------------------------------ writeup
BEGIN, END = "<!-- prediction:begin -->", "<!-- prediction:end -->"
SMALL = 100                      # signals; fewer is noise and is marked as such


def fnum(r: dict, key: str) -> float | None:
    v = r.get(key)
    return None if v in (None, "") else float(v)


def rows_of(decay, direction="primary", predictor="ofi") -> dict[float, dict]:
    return {float(r["horizon_s"]): r for r in decay
            if r["direction"] == direction and r["predictor"] == predictor}


def grade(decay: list[dict], regimes_: list[dict], cost: list[dict]) -> list[tuple]:
    """The Day 6 Step 0 list, line by line, from the CSVs. True held,
    False failed, None could not be checked."""
    out = []
    ofi, last = rows_of(decay), rows_of(decay, predictor="last")
    rofi = rows_of(decay, "reversed")
    hs = sorted(ofi)
    hit = {h: fnum(ofi[h], "hit_rate") for h in hs}
    clears = {h: str(ofi[h]["clears_band"]) == "True" for h in hs}
    # 1. hit rate in [50, 56] everywhere, highest at 1 or 5 s, inside the band by 60 s
    peak = max(hs, key=lambda h: hit[h] or 0)
    first_inside = next((h for h in hs if not clears[h]), None)
    ok1 = (all(hit[h] is not None and 0.50 <= hit[h] <= 0.56 for h in hs) and peak <= 5
           and first_inside is not None and first_inside <= 60
           and all(not clears[h] for h in hs if h >= first_inside))
    out.append((ok1, "OFI hit rate between 50% and 56% at every horizon, highest at 1 to 5 s, "
                     "inside the band by one minute: "
                     + ", ".join(f"{hlabel(h)} {pct(hit[h])}{'*' if clears[h] else ''}" for h in hs)
                     + f"; first inside the band at {hlabel(first_inside) if first_inside else 'none'}"))
    # 2. out-of-sample R-squared
    r2 = {h: fnum(ofi[h], "r2_oos") for h in hs}
    ok2 = (all(r2[h] is not None for h in hs) and r2[hs[0]] < 0.02
           and all(r2[h] < 0.005 for h in hs if h > 10) and r2[hs[-1]] < 0.01)
    out.append((ok2, "out-of-sample R-squared under 2% at 1 s, under 0.5% beyond 10 s, about zero "
                     "or negative at 5 min: "
                     + ", ".join(f"{hlabel(h)} {100 * r2[h]:+.2f}%" if r2[h] is not None
                                 else f"{hlabel(h)} n/a" for h in hs)))
    # 3. beta positive at 1 to 30 s and under 0.4 cents per 1,000 shares
    beta = {h: fnum(ofi[h], "beta") for h in hs}
    ok3 = all(beta[h] is not None and beta[h] > 0 for h in hs if h <= 30) and \
        all(beta[h] is not None and abs(beta[h]) < 0.4 for h in hs)
    out.append((ok3, "predictive beta positive at 1 to 30 s and under 0.4 cents per 1,000 shares "
                     "(a tenth of the contemporaneous 4.1): "
                     + ", ".join(f"{hlabel(h)} {beta[h]:+.3f}" if beta[h] is not None
                                 else f"{hlabel(h)} n/a" for h in hs)))
    # 4. the last-change baseline reverses at 1 to 10 s and is within two points
    gam = {h: fnum(last[h], "beta") for h in hs}
    lhit = {h: fnum(last[h], "hit_rate") for h in hs}
    short = [h for h in hs if h <= 10]
    ok4 = all(gam[h] is not None and gam[h] < 0 for h in short) and \
        all(hit[h] is not None and lhit[h] is not None and abs(hit[h] - lhit[h]) <= 0.02 for h in hs)
    out.append((ok4, "last-change baseline reverses (gamma < 0) at 1 to 10 s and its hit rate is "
                     "within two points of OFI's: gamma "
                     + ", ".join(f"{hlabel(h)} {gam[h]:+.4f}" if gam[h] is not None
                                 else f"{hlabel(h)} n/a" for h in short)
                     + "; gaps " + ", ".join(f"{hlabel(h)} {100 * (hit[h] - lhit[h]):+.1f}"
                                            if hit[h] is not None and lhit[h] is not None
                                            else f"{hlabel(h)} n/a" for h in hs)
                     + " points"))
    # 5. the reversed split agrees in sign and within three points
    rhit = {h: fnum(rofi[h], "hit_rate") for h in hs}
    rbeta = {h: fnum(rofi[h], "beta") for h in hs}
    ok5 = all(beta[h] is not None and rbeta[h] is not None and (beta[h] > 0) == (rbeta[h] > 0)
              for h in hs) and \
        all(hit[h] is not None and rhit[h] is not None and abs(hit[h] - rhit[h]) <= 0.03 for h in hs)
    out.append((ok5, "reversed split: same sign of beta and hit rates within three points: "
                     + ", ".join(f"{hlabel(h)} {pct(hit[h])} vs {pct(rhit[h])}" for h in hs)))
    # 6. regimes
    rg = {r["regime"]: r for r in regimes_ if r["predictor"] == "ofi"}
    if {"open", "midday", "close", "high-vol", "quiet"} <= set(rg):
        w = {k: fnum(rg[k], "hit_rate") for k in ("open", "midday", "close")}
        windows_ok = (None not in w.values() and w["open"] == min(w.values())
                      and w["close"] == max(w.values()))
        hv = {k: fnum(rg["high-vol"], k) for k in ("beta", "r2_oos", "hit_rate")}
        qt = {k: fnum(rg["quiet"], k) for k in ("beta", "r2_oos", "hit_rate")}
        deciles_ok = (None not in hv.values() and None not in qt.values()
                      and hv["beta"] > qt["beta"] and hv["r2_oos"] < qt["r2_oos"])
        out.append((windows_ok, "regimes, windows: the open has the lowest hit rate of the three and "
                                "the close the highest: "
                                + ", ".join(f"{k} {pct(w[k])} ({rg[k]['verdict']})" for k in w)))
        out.append((deciles_ok, "regimes, deciles: high-vol has a larger beta and a lower R-squared than "
                                f"quiet: beta {fmt(hv['beta'], 3, True)} vs {fmt(qt['beta'], 3, True)}, "
                                f"R-squared {fmt(None if hv['r2_oos'] is None else 100 * hv['r2_oos'], 2, True)}% vs "
                                f"{fmt(None if qt['r2_oos'] is None else 100 * qt['r2_oos'], 2, True)}%; hit rates "
                                f"{pct(hv['hit_rate'])} ({rg['high-vol']['verdict']}) and "
                                f"{pct(qt['hit_rate'])} ({rg['quiet']['verdict']})"))
    else:
        out.append((None, "regimes: not run"))
    # 7. the cost
    c_all = {float(r["horizon_s"]): r for r in cost if r["subset"] == "all" and int(r["latency"]) == 0}
    c_top = {float(r["horizon_s"]): r for r in cost if r["subset"] == "top-decile" and int(r["latency"]) == 0}
    c_lag = {float(r["horizon_s"]): r for r in cost if r["subset"] == "all" and int(r["latency"]) == 1}
    if c_all:
        gross = {h: fnum(c_all[h], "gross_cents") for h in hs}
        top = {h: fnum(c_top[h], "gross_cents") for h in hs}
        hsp = {h: fnum(c_all[h], "half_spread_cents") for h in hs}
        cons = {h: fnum(c_all[h], "consumed") for h in hs}
        lag_cons = {h: fnum(c_lag[h], "consumed") for h in hs}
        net1 = {h: fnum(c_all[h], "net_one_leg") for h in hs}
        ok7 = (all(gross[h] is not None and gross[h] < 0.5 for h in hs)
               and all(top[h] is not None and top[h] < 2 for h in hs)
               and all(hsp[h] is not None and 5 <= hsp[h] <= 9 for h in hs)
               and all((cons[h] is None or cons[h] >= 10) and (lag_cons[h] is None or lag_cons[h] >= 10) for h in hs)
               and all(net1[h] is not None and net1[h] < 0 for h in hs))
        out.append((ok7, "cost: gross edge under 0.5 cents per signal at every horizon and under 2 cents "
                         "for the strongest decile, half-spread about 7 cents, consumed at least ten times "
                         "over with and without latency, net after one leg negative everywhere: gross "
                         + ", ".join(f"{hlabel(h)} {fmt(gross[h], 2, True)}" for h in hs)
                         + "; top decile " + ", ".join(f"{hlabel(h)} {fmt(top[h], 2, True)}" for h in hs)
                         + "; half-spread " + ", ".join(fmt(hsp[h], 1) for h in hs)
                         + "; consumed " + ", ".join(f"{hlabel(h)} {('%.0fx' % cons[h]) if cons[h] else 'n/a'}" for h in hs)
                         + f" (signals per horizon {', '.join(c_all[h]['n_signals'] for h in hs)})"))
    else:
        out.append((None, "cost: not run"))
    # 8. the sentence's shape
    line = sentence(decay, cost) if cost else ""
    best = None
    for r in cost:
        if r["subset"] == "all" and int(r["latency"]) == 0 and clears.get(float(r["horizon_s"])):
            if best is None or float(r["gross_cents"]) > float(best["gross_cents"]):
                best = r
    ok8 = (best is not None and float(best["horizon_s"]) < 60
           and hit.get(float(best["horizon_s"])) is not None
           and 0.50 <= hit[float(best["horizon_s"])] <= 0.56
           and fnum(best, "consumed") is not None and fnum(best, "consumed") >= 10)
    out.append((ok8, "the sentence has the literature's shape (a horizon of seconds, a hit rate in the "
                     "low fifties, consumed many times over): " + (line or "no cost rows")))
    return out


def render_block(decay, regimes_, cost, ticker: str) -> str:
    ids = sorted(int(r["trial_id"]) for rows in (decay, regimes_, cost) for r in rows)
    L = [BEGIN,
         f"Generated by `scripts/{SCRIPT} --part writeup` on {ticker}, {DATE}, from the three CSVs; "
         f"registry rows #{ids[0]} to #{ids[-1]} of `results/trials.csv`. Every number below was a "
         f"registry row before it was a number on this page; this part runs no regression.", ""]
    L += ["### The decay curve, out of sample", "",
          "Fit on one half of the day through the origin, tested on the other. Slope in cents per "
          "1,000 shares for OFI and cents per cent for the last-change baseline; R-squared against the "
          "zero forecast; hit rate over test pairs where predictor and target are both nonzero, with the "
          "95% band around one half for that many calls (an asterisk marks a hit rate above it); the "
          "gross edge is the mean realized move in the forecast direction per signal, before costs.", ""]
    for direction, split_name, _ in DIRECTIONS:
        L += [f"**{direction.capitalize()} split ({split_name})**", "",
              "| horizon | predictor | n fit | n test | slope | t | R2 oos | hit rate | band | z | gross edge (c) | signals | trial |",
              "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|"]
        for r in decay:
            if r["direction"] != direction:
                continue
            star = "*" if str(r["clears_band"]) == "True" else ""
            L.append(f"| {hlabel(r['horizon_s'])} | {r['predictor']} | {int(r['n_fit']):,} | "
                     f"{int(r['n_test']):,} | {fmt(fnum(r, 'beta'), 4, True)} | {fmt(fnum(r, 't_stat'), 1)} | "
                     f"{fmt(fnum(r, 'r2_oos'), 4, True)} | {pct(fnum(r, 'hit_rate'))}{star} | "
                     f"+/-{pct(fnum(r, 'band'))} | {fmt(fnum(r, 'z'), 1, True)} | "
                     f"{fmt(fnum(r, 'edge_cents'), 3, True)} | {int(r['n_signals']):,} | #{r['trial_id']} |")
        L.append("")
    if regimes_:
        L += [f"### Regimes at {hlabel(regimes_[0]['horizon_s'])}, each fitted on its complement", "",
              "| regime | definition | predictor | n fit | n test | slope | R2 oos | hit rate | band | z | verdict | gross edge (c) | trial |",
              "|---|---|---|---:|---:|---:|---:|---:|---:|---:|---|---:|---|"]
        for r in regimes_:
            L.append(f"| {r['regime']} | {r['label']} | {r['predictor']} | {int(r['n_fit']):,} | "
                     f"{int(r['n_test']):,} | {fmt(fnum(r, 'beta'), 4, True)} | {fmt(fnum(r, 'r2_oos'), 4, True)} | "
                     f"{pct(fnum(r, 'hit_rate'))} | +/-{pct(fnum(r, 'band'))} | {fmt(fnum(r, 'z'), 1, True)} | "
                     f"{r['verdict']} | {fmt(fnum(r, 'edge_cents'), 3, True)} | #{r['trial_id']} |")
        L.append("")
    if cost:
        L += ["### The cost verdict, test half", "",
              "Every signal in the test half traded in the forecast direction, entered at the end of its "
              "bucket (latency 0) or one bucket later (latency 1), held one bucket. Gross edge and nets in "
              "cents per signal; the half-spread is the time-weighted one of the bucket the trade is "
              "entered in; consumed is the half-spread over the gross edge; beat is the share of signals "
              f"whose own move exceeded their own half-spread. Rows with fewer than {SMALL} signals are "
              "marked (n) and are noise.", "",
              "| horizon | signals | latency | n | hit rate | gross (c) | half-spread (c) | net, one leg | net, two legs | beat | consumed | beta from | trial |",
              "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|"]
        for r in cost:
            n = int(r["n_signals"])
            mark = " (n)" if n < SMALL else ""
            cons = fnum(r, "consumed")
            L.append(f"| {hlabel(r['horizon_s'])} | {r['subset']} | {r['latency']} | {n:,}{mark} | "
                     f"{pct(fnum(r, 'hit_rate'))} | {fmt(fnum(r, 'gross_cents'), 2, True)} | "
                     f"{fmt(fnum(r, 'half_spread_cents'), 2)} | {fmt(fnum(r, 'net_one_leg'), 2, True)} | "
                     f"{fmt(fnum(r, 'net_two_legs'), 2, True)} | {pct(fnum(r, 'beat_half_spread'), 0)} | "
                     f"{(f'{cons:.0f}x' if cons is not None else 'n/a')} | #{r['beta_trial_id']} | #{r['trial_id']} |")
        L += ["", "**The sentence, with its numbers:** " + sentence(decay, cost), ""]
    L += ["### Expected, written before the run, graded", ""]
    for ok, text in grade(decay, regimes_, cost):
        L.append(f"- **{ {True: 'held', False: 'FAILED', None: 'n/a'}[ok] }**: {text}")
    L.append(END)
    return "\n".join(L)


SKELETON = """# OFI prediction: the honest evaluation

Does the order flow imbalance over the last interval say anything about
the mid change over the next one? Fit on one half of the day, tested on
the other, against a baseline, and then against half the spread.

{block}
"""


def write_report(path: Path, block: str) -> None:
    path = Path(path)
    if path.exists():
        text = path.read_text(encoding="utf-8")
        if BEGIN in text and END in text:
            head, rest = text.split(BEGIN, 1)
            _, tail = rest.split(END, 1)
            text = head + block + tail
        else:
            text = text.rstrip("\n") + "\n\n" + block + "\n"
    else:
        text = SKELETON.format(block=block)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def draw(decay, cost, ticker: str, target: Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.ticker import FuncFormatter

    colour = SERIES.get(ticker, "#52514e")
    base, ink, muted, grid, spine = "#8a8984", "#0b0b0b", "#52514e", "#e6e5e0", "#c3c2b7"
    ofi, last = rows_of(decay), rows_of(decay, predictor="last")
    rofi = rows_of(decay, "reversed")
    hs = sorted(ofi)
    fig, (ax_h, ax_r, ax_c) = plt.subplots(3, 1, figsize=(9, 10), sharex=True,
                                           gridspec_kw={"hspace": 0.16})
    # hit rate with the coin's band
    band_lo = [50 - 100 * fnum(ofi[h], "band") for h in hs]
    band_hi = [50 + 100 * fnum(ofi[h], "band") for h in hs]
    ax_h.fill_between(hs, band_lo, band_hi, color="#eeede8", linewidth=0, label="a coin, 95% band")
    ax_h.axhline(50, color=spine, linewidth=1)
    ax_h.plot(hs, [100 * fnum(ofi[h], "hit_rate") for h in hs], color=colour, linewidth=2,
              marker="o", markersize=7, label="OFI, fit first half, test second")
    ax_h.plot(hs, [100 * fnum(rofi[h], "hit_rate") for h in hs], color=colour, linewidth=1.2,
              linestyle=(0, (3, 3)), marker="o", markersize=6, markerfacecolor="#fcfcfb",
              label="OFI, reversed split")
    ax_h.plot(hs, [100 * fnum(last[h], "hit_rate") for h in hs], color=base, linewidth=2,
              marker="s", markersize=6, label="last mid change, the baseline")
    ax_h.set_ylabel("out-of-sample direction hit rate, %")
    ax_h.legend(frameon=False, loc="upper right", fontsize=9)
    ax_h.set_title("direction: right more often than a coin at 1 and 5 seconds, then not",
                   loc="left", fontsize=10.5, color=muted)
    # R-squared
    ax_r.axhline(0, color=spine, linewidth=1)
    ax_r.plot(hs, [100 * fnum(ofi[h], "r2_oos") for h in hs], color=colour, linewidth=2,
              marker="o", markersize=7, label="OFI")
    ax_r.plot(hs, [100 * fnum(last[h], "r2_oos") for h in hs], color=base, linewidth=2,
              marker="s", markersize=6, label="last mid change")
    ax_r.set_ylabel("out-of-sample R-squared\nagainst the zero forecast, %")
    ax_r.set_title("magnitude: under one percent of the variance, everywhere",
                   loc="left", fontsize=10.5, color=muted)
    # the cost
    c_all = {float(r["horizon_s"]): r for r in cost if r["subset"] == "all" and int(r["latency"]) == 0}
    c_top = {float(r["horizon_s"]): r for r in cost if r["subset"] == "top-decile" and int(r["latency"]) == 0}
    ax_c.plot(hs, [fnum(c_all[h], "half_spread_cents") for h in hs], color=ink, linewidth=2,
              label="half-spread at the signal times")
    # horizons with fewer than SMALL signals are left off this panel: their
    # rows are in the table, marked (n), and are noise
    for series, lab, mk, fc in ((c_all, "gross edge, all signals", "o", colour),
                                (c_top, "gross edge, strongest decile of |OFI|", "D", "#fcfcfb")):
        xs = [h for h in hs if int(series[h]["n_signals"]) >= SMALL]
        ys = [fnum(series[h], "gross_cents") for h in xs]
        ax_c.plot(xs, ys, color=colour, linewidth=1.5 if fc == colour else 1.2, marker=mk,
                  markersize=7, markerfacecolor=fc, label=lab)
    ax_c.axhline(0, color=spine, linewidth=1)
    ax_c.set_ylim(-3, 9)
    ax_c.set_ylabel("cents per signal, test half")
    ax_c.set_xlabel(f"horizon: the last h seconds forecast the next h "
                    f"(points with under {SMALL} signals left off the cost panel)")
    ax_c.set_xscale("log")
    ax_c.set_xticks(hs)
    ax_c.xaxis.set_major_formatter(FuncFormatter(lambda v, _: hlabel(v)))
    ax_c.minorticks_off()
    ax_c.legend(frameon=False, loc="center right", fontsize=9)
    ax_c.set_title("cost: the edge against what one aggressive leg pays", loc="left",
                   fontsize=10.5, color=muted)
    for a in (ax_h, ax_r, ax_c):
        a.grid(True, color=grid, linewidth=0.8)
        a.set_axisbelow(True)
        for side in ("top", "right"):
            a.spines[side].set_visible(False)
        a.spines["left"].set_color(spine)
        a.spines["bottom"].set_color(spine)
        a.tick_params(colors=muted)
        a.yaxis.label.set_color(muted)
    ax_c.xaxis.label.set_color(muted)
    fig.suptitle(f"{ticker} {DATE}: does last-interval OFI predict the next interval?",
                 x=0.09, ha="left", fontsize=12, color=ink)
    fig.subplots_adjust(top=0.94, bottom=0.06, left=0.1, right=0.97)
    target.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(target, dpi=150, facecolor="#fcfcfb")
    print(f"  drew {target}")


def part_writeup(args, out: Path, ticker: str) -> None:
    paths_ = {k: out / f"ofi_{k}_{ticker}.csv" for k in ("decay", "regimes", "cost")}
    missing = [str(p) for p in paths_.values() if not p.exists()]
    if missing:
        raise SystemExit("missing: " + ", ".join(missing) + "; run the parts first")
    decay, regimes_, cost = (read_csv(paths_[k]) for k in ("decay", "regimes", "cost"))
    page = out / "ofi_prediction.md"
    write_report(page, render_block(decay, regimes_, cost, ticker))
    print(f"  spliced the generated block into {page} (no regression run, no registry row added)")
    print(f"  {sentence(decay, cost)}")
    for ok, text in grade(decay, regimes_, cost):
        print(f"    [{ {True: ' ok ', False: 'FAIL', None: ' n/a'}[ok] }] {text[:110]}{'...' if len(text) > 110 else ''}")
    if args.figure:
        try:
            draw(decay, cost, ticker, out / "figures" / "ofi_prediction.png")
        except ImportError:
            print("  (matplotlib is not installed; pip install -e '.[analysis]' to draw)", file=sys.stderr)


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
    elif args.part == "regimes":
        part_regimes(args, load(args), registry, out)
    elif args.part == "cost":
        part_cost(args, load(args), registry, out)
    else:
        part_writeup(args, out, args.ticker)


if __name__ == "__main__":
    main()
