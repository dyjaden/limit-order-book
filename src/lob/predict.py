"""The predictive question, defined before it runs.

Week 6's instrument. Day 5 measured the contemporaneous relation (the
mid change over an interval against the order flow imbalance inside the
same interval: R-squared 0.41 at ten seconds, hit rate 86%), and said
plainly that none of it was a forecast. This module asks the question a
trader cares about: does the imbalance over the LAST interval say
anything about the mid change over the NEXT one? Every choice below was
frozen in the Day 6 guide before any number existed, and every fit the
functions here make is logged in the trial registry by the caller
before it is printed (``scripts/ofi_prediction.py``).

The specification, as code:

- A horizon h is a calendar bucket width (1, 5, 10, 30, 60, 300
  seconds, ``HORIZONS_S``). The predictor at horizon h is the OFI over
  bucket k, the target is the mid change over bucket k + 1; the two
  windows share an edge and nothing else (``pairs``).
- The split is within the day, at 12:45 (``SPLIT_NS``), because there
  is one day. A pair is in the fit set when its target ends at or
  before the split and in the test set when it is forecast at or after
  it; the reversed direction swaps the two (``split``).
- The model is through the origin, beta = sum xy / sum x squared on the
  fit set, so no first-half drift rides along as a forecast. The
  baselines are zero (the benchmark of every out-of-sample R-squared)
  and the last mid change, fitted the same way (``evaluate`` with
  ``predictor`` 'ofi' or 'last').
- The metrics are out of sample: the direction hit rate over pairs
  where predictor and target are both nonzero, with its binomial z and
  the 95% band around one half; the R-squared against the zero
  forecast, negative when the model is worse than nothing; and the
  gross edge, the mean realized move in the forecast direction per
  signal, in cents, before any cost.
- Regimes (``regimes``, ``leave_out``) are sets of forecast times known
  at the forecast: three windows of the day and two deciles of trailing
  realized variance with thresholds from the first half. The fit is on
  the complement, the test inside.
- The cost verdict (``cost_verdict``) puts the gross edge beside the
  half-spread prevailing in the signal's own bucket, for every signal
  and for the strongest decile of |OFI| (threshold from the fit set),
  at zero latency and with one interval of delay.

Units: OFI in shares, mid changes in cents from the integer twice-mid,
spreads in cents; slopes in cents per share here and quoted in cents
per 1,000 shares by the script.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable, Iterable

from lob.micro import CLOSE_NS, OPEN_NS
from lob.ofi import Bucket

HORIZONS_S = (1, 5, 10, 30, 60, 300)
SPLIT_NS = OPEN_NS + (CLOSE_NS - OPEN_NS) // 2        # 12:45:00
KEY_S = 10                                            # the regimes' horizon
VAR_WINDOW = 6                                        # trailing buckets of realized variance
Z95 = 1.96
PREDICTORS = ("ofi", "last")


# ------------------------------------------------------------- pairs
@dataclass(frozen=True)
class Pair:
    """One forecast opportunity: what was known at ``time_ns`` (the end
    of bucket k) and what happened over bucket k + 1."""
    time_ns: int            # the forecast time
    target_end_ns: int      # the end of bucket k + 1
    x: int                  # OFI over bucket k, shares
    last: float             # mid change over bucket k, cents
    y: float                # mid change over bucket k + 1, cents
    half_spread: float | None     # time-weighted half-spread of bucket k, cents
    var: float | None             # trailing realized variance, cents squared


def pairs(buckets: list[Bucket], spreads: list | None = None,
          var_window: int = VAR_WINDOW) -> list[Pair]:
    """Consecutive calendar buckets into forecast opportunities. The
    predictor's bucket must end exactly where the target's begins, and
    a bucket with no mid yet (before the day's first observation) makes
    no pair. ``spreads`` is the time-weighted spread per bucket in
    cents, index-aligned (``None`` where the book was never two-sided);
    the trailing variance is the sum of squared mid changes over the
    ``var_window`` buckets ending at the forecast bucket."""
    if spreads is not None and len(spreads) != len(buckets):
        raise ValueError("spreads must be index-aligned with the buckets")
    out: list[Pair] = []
    for k in range(len(buckets) - 1):
        b, nxt = buckets[k], buckets[k + 1]
        if b.end_ns != nxt.start_ns:
            raise ValueError(f"buckets {k} and {k + 1} do not share an edge")
        if b.dmid_cents is None or nxt.dmid_cents is None:
            continue
        hs = None
        if spreads is not None and spreads[k] is not None:
            hs = spreads[k] / 2
        var = None
        if k >= var_window - 1:
            window = buckets[k - var_window + 1:k + 1]
            if all(w.dmid_cents is not None for w in window):
                var = sum(w.dmid_cents ** 2 for w in window)
        out.append(Pair(b.end_ns, nxt.end_ns, b.ofi, b.dmid_cents, nxt.dmid_cents, hs, var))
    return out


def split(pairs: Iterable[Pair], split_ns: int = SPLIT_NS,
          reverse: bool = False) -> tuple[list[Pair], list[Pair]]:
    """Fit on one half, test on the other. A pair is in the first half
    when its target ends at or before the split, in the second when it
    is forecast at or after it; a pair straddling the split is in
    neither. ``reverse`` fits on the second half and tests on the first."""
    pairs = list(pairs)
    first = [p for p in pairs if p.target_end_ns <= split_ns]
    second = [p for p in pairs if p.time_ns >= split_ns]
    return (second, first) if reverse else (first, second)


# ---------------------------------------------------------- evaluation
@dataclass(frozen=True)
class Forecast:
    """One out-of-sample evaluation of one predictor at one horizon."""
    predictor: str
    n_fit: int
    n_test: int
    beta: float | None          # cents per unit of the predictor
    t_stat: float | None        # classical, on the fit set
    r2_oos: float | None        # against the zero forecast, on the test set
    hits: int
    both_nonzero: int
    excluded: int
    edge_cents: float | None    # mean sign(beta x) * y over signals
    n_signals: int

    @property
    def hit_rate(self) -> float | None:
        return self.hits / self.both_nonzero if self.both_nonzero else None

    @property
    def z(self) -> float | None:
        n = self.both_nonzero
        return (self.hits - n / 2) / math.sqrt(n / 4) if n else None

    @property
    def band(self) -> float | None:
        """Half-width of the 95% band around one half: the hit rate a
        coin would produce this often."""
        n = self.both_nonzero
        return Z95 * math.sqrt(0.25 / n) if n else None

    @property
    def clears_band(self) -> bool | None:
        if self.hit_rate is None:
            return None
        return self.hit_rate - 0.5 > self.band


def regressor(p: Pair, predictor: str) -> float:
    if predictor == "ofi":
        return p.x
    if predictor == "last":
        return p.last
    raise ValueError(f"unknown predictor {predictor!r}")


def fit_origin(xs, ys) -> tuple[float | None, float | None]:
    """Slope through the origin and its classical t-statistic, which
    assumes homoskedastic, serially independent errors and is reported
    as the overstatement it is."""
    xs, ys = list(xs), list(ys)
    n = len(xs)
    sxx = sum(x * x for x in xs)
    if n < 2 or not sxx:
        return None, None
    beta = sum(x * y for x, y in zip(xs, ys)) / sxx
    resid = sum((y - beta * x) ** 2 for x, y in zip(xs, ys))
    se2 = resid / (n - 1) / sxx
    return beta, (beta / math.sqrt(se2) if se2 > 0 else None)


def evaluate(fit: Iterable[Pair], test: Iterable[Pair], predictor: str) -> Forecast:
    """Fit on one set, measure on the other, and only there."""
    fit, test = list(fit), list(test)
    beta, t = fit_origin([regressor(p, predictor) for p in fit], [p.y for p in fit])
    if beta is None:
        return Forecast(predictor, len(fit), len(test), None, None, None, 0, 0,
                        len(test), None, 0)
    sse = sum((p.y - beta * regressor(p, predictor)) ** 2 for p in test)
    sst0 = sum(p.y * p.y for p in test)
    r2 = (1 - sse / sst0) if sst0 else None
    hits = both = 0
    edge_sum = 0.0
    n_signals = 0
    for p in test:
        x = regressor(p, predictor)
        if not x or not beta:
            continue
        direction = 1 if (beta * x) > 0 else -1
        n_signals += 1
        edge_sum += direction * p.y
        if p.y:
            both += 1
            if (direction > 0) == (p.y > 0):
                hits += 1
    edge = edge_sum / n_signals if n_signals else None
    return Forecast(predictor, len(fit), len(test), beta, t, r2, hits, both,
                    len(test) - both, edge, n_signals)


# ------------------------------------------------------------- regimes
WINDOWS_S = (("open", 34_200, 36_000),        # 09:30 to 10:00
             ("midday", 39_600, 50_400),      # 11:00 to 14:00
             ("close", 55_800, 57_600))       # 15:30 to 16:00
DECILE = 0.1


@dataclass(frozen=True)
class Regime:
    """A set of forecast opportunities defined by what was known at the
    forecast: a window of the day the forecast bucket lies inside, or a
    decile of trailing realized variance."""
    name: str
    kind: str                      # 'window' | 'decile'
    label: str
    member: Callable[[Pair], bool]


def _nearest_rank(xs, q: float) -> float:
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(q * (len(xs) - 1) + 0.5))]


def regimes(pairs: Iterable[Pair], split_ns: int = SPLIT_NS,
            windows=WINDOWS_S, decile: float = DECILE) -> list[Regime]:
    """The three windows by forecast bucket (wholly inside the window)
    and the two variance deciles, whose thresholds come from the pairs
    of the first half only, so no test-half information sets a cut."""
    out = []
    for name, start_s, end_s in windows:
        start_ns, end_ns = start_s * 10**9, end_s * 10**9

        def inside(p: Pair, s=start_ns, e=end_ns) -> bool:
            width = p.target_end_ns - p.time_ns
            return p.time_ns - width >= s and p.time_ns <= e
        out.append(Regime(name, "window", f"{start_s // 3600:02d}:{start_s % 3600 // 60:02d}-"
                                          f"{end_s // 3600:02d}:{end_s % 3600 // 60:02d}", inside))
    first = [p.var for p in pairs if p.var is not None and p.target_end_ns <= split_ns]
    if first:
        hi, lo = _nearest_rank(first, 1 - decile), _nearest_rank(first, decile)
        out.append(Regime("high-vol", "decile", f"trailing variance >= {hi:.4g} (top decile, first-half cut)",
                          lambda p, hi=hi: p.var is not None and p.var >= hi))
        out.append(Regime("quiet", "decile", f"trailing variance <= {lo:.4g} (bottom decile, first-half cut)",
                          lambda p, lo=lo: p.var is not None and p.var <= lo))
    return out


def leave_out(pairs: Iterable[Pair], regime: Regime) -> tuple[list[Pair], list[Pair]]:
    """Fit on everything outside the regime, test inside it."""
    pairs = list(pairs)
    return ([p for p in pairs if not regime.member(p)],
            [p for p in pairs if regime.member(p)])
