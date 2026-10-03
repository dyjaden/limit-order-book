"""The first look: contemporaneous and descriptive, logged before printed.

    python scripts/ofi_first_look.py --ticker AAPL
    python scripts/ofi_first_look.py --ticker AAPL --registry results/trials.csv \
        --write results/ofi_first_look.md --figure

The relation the literature calls strong: over an interval, the mid
change is linear in the order flow imbalance inside that same interval,
with a slope that scales as one over the depth at the touch (Cont,
Kukanov and Stoikov, 2014). This script reproduces that look on one
day, five ways (calendar bins of 1, 10 and 60 seconds; event buckets of
50 and 200 best-quote updates), and the depth scaling half hour by half
hour. It is a check that the machinery is right. It is NOT evidence of
predictability: the flow and the price move it is regressed on happened
together, and a trade that eats the ask and the uptick it causes are
the same event seen twice. The predictive question, flow at t against
the price at t + h, is Week 6's, and it goes into the registry before
it runs.

The discipline this script exists to demonstrate: every regression is
a row in results/trials.csv BEFORE its number is formatted or printed
(``lob.registry``). Nineteen rows per run on a full day: the five
clock-and-bucket fits, thirteen half-hour fits, and the log-log slope
across them. A re-run is a new set of trials and appends; the registry
is never rewritten, so the count Week 10 divides by only grows.

Outputs: the tables and the graded expectations spliced into
results/ofi_first_look.md between ``<!-- first-look:begin -->`` and
``<!-- first-look:end -->`` (prose outside the markers is untouched; a
missing file gets a skeleton), and results/figures/ofi_first_look.png:
the ten-second scatter with the origin fit, and the depth-scaling panel
beside it. Depth per window is the time-weighted touch depth per side
from Week 4's ``lob.micro.time_weighted``, the instrument already
tested for that, not a second accumulator here.

Week 5 row D of the plan.
"""
from __future__ import annotations

import argparse
import math
import sys
from dataclasses import dataclass, field
from pathlib import Path

from intraday_report import DATE, SERIES, paths
from lob.lobster import read_messages, read_orderbook
from lob.micro import CLOSE_NS, NS, OPEN_NS, hms, time_weighted
from lob.ofi import (Bucket, Fit, Scaling, Touch, aggregate_calendar,
                     aggregate_events, depth_scaling, events, ols, touches)
from lob.registry import REGISTRY, log_trial

SCRIPT = "ofi_first_look.py"
BEGIN, END = "<!-- first-look:begin -->", "<!-- first-look:end -->"
KSH = 1_000                       # beta is reported in cents per 1,000 shares


@dataclass(frozen=True)
class Sizes:
    """The clocks the look runs on. ``key_s``/``key_n`` name the two
    buckets the expectations and the figure refer to; ``window_s`` is
    the depth-scaling window."""
    calendar_s: tuple = (1, 10, 60)
    event_n: tuple = (50, 200)
    key_s: float = 10
    key_n: int = 50
    window_s: float = 1800


@dataclass
class Trial:
    clock: str
    bucket: float | int
    buckets: list
    fit: Fit
    trial_id: int

    @property
    def label(self) -> str:
        if self.clock == "calendar":
            s = self.bucket
            return f"{s:g} s" if s < 60 else f"{s / 60:g} min"
        return f"{self.bucket} updates"


@dataclass
class Look:
    ticker: str
    level: int
    sizes: Sizes
    fits: list = field(default_factory=list)          # Trial per clock/bucket
    key: Trial | None = None                           # calendar key_s
    key_event: Trial | None = None                     # event key_n
    scaling: Scaling | None = None
    window_trials: list = field(default_factory=list)  # trial id per window
    scaling_trial: int | None = None
    net_share: float | None = None
    level1_same: bool | None = None
    duration_span: float | None = None                 # log10(max / min) on the key event clock

    @property
    def trial_ids(self) -> list[int]:
        ids = [t.trial_id for t in self.fits] + list(self.window_trials)
        if self.scaling_trial is not None:
            ids.append(self.scaling_trial)
        return ids


def window_label(start_ns: int, end_ns: int) -> str:
    return f"{hms(start_ns)[:5]}-{hms(end_ns)[:5]}"


def _fit_note(fit: Fit, level: int) -> str:
    def f(x, nd=6):
        return "none" if x is None else f"{x:.{nd}g}"
    return (f"beta0={f(None if fit.beta0 is None else fit.beta0 * KSH)} cents/kshare; "
            f"r2_0={f(fit.r2_0)}; beta={f(None if fit.beta is None else fit.beta * KSH)}; "
            f"alpha={f(fit.alpha)} cents; hit_rate={f(fit.hit_rate)} over "
            f"{fit.both_nonzero} both-nonzero ({fit.excluded} excluded); level {level}")


def first_look(evs, depth_bins, ticker: str, level: int, sizes: Sizes = Sizes(),
               registry: Path = REGISTRY, open_ns: int = OPEN_NS,
               close_ns: int = CLOSE_NS, script: str = SCRIPT,
               level1_same: bool | None = None) -> Look:
    """Run every regression and log each one the moment it exists.
    Nothing here prints; the caller formats AFTER this returns, so no
    unlogged number is ever on screen."""
    look = Look(ticker, level, sizes, level1_same=level1_same)
    evs = list(evs)
    gross = sum(abs(e.e) for e in evs)
    look.net_share = abs(sum(e.e for e in evs)) / gross if gross else None
    common = dict(script=script, ticker=ticker, date=DATE, horizon=0,
                  window=window_label(open_ns, close_ns), split="none",
                  status="descriptive")
    question = ("contemporaneous: bucket mid change (cents) on bucket OFI "
                "(shares), through the origin and with an intercept")
    for s in sizes.calendar_s:
        bs = aggregate_calendar(evs, int(round(s * NS)), open_ns, close_ns)
        fit = ols([b.ofi for b in bs], [b.dmid_cents for b in bs])
        tid = log_trial(registry, question=question, clock="calendar", bucket=s,
                        n_obs=fit.n, metric="r2", value=fit.r2,
                        note=_fit_note(fit, level), **common)
        t = Trial("calendar", s, bs, fit, tid)
        look.fits.append(t)
        if s == sizes.key_s:
            look.key = t
    for n in sizes.event_n:
        bs = aggregate_events(evs, n)
        fit = ols([b.ofi for b in bs], [b.dmid_cents for b in bs])
        tid = log_trial(registry, question=question, clock="event", bucket=n,
                        n_obs=fit.n, metric="r2", value=fit.r2,
                        note=_fit_note(fit, level), **common)
        t = Trial("event", n, bs, fit, tid)
        look.fits.append(t)
        if n == sizes.key_n:
            look.key_event = t
            durs = [b.duration_ns for b in bs if b.duration_ns > 0]
            if durs:
                look.duration_span = math.log10(max(durs) / min(durs))
    if look.key is None:
        raise ValueError("key_s must be one of calendar_s")
    # depth scaling on the key calendar buckets, one window at a time
    window_ns = int(round(sizes.window_s * NS))
    depth = [None if b.tw_touch_depth is None else b.tw_touch_depth / 2
             for b in depth_bins]
    look.scaling = depth_scaling(look.key.buckets, depth, window_ns, open_ns)
    for r in look.scaling.rows:
        tid = log_trial(registry,
                        question="depth scaling: bucket mid change (cents) on bucket "
                                 "OFI (shares) through the origin, one window",
                        clock="calendar", bucket=sizes.key_s, n_obs=r.n,
                        metric="beta0_cents_per_kshare",
                        value=None if r.beta0 is None else r.beta0 * KSH,
                        note=(f"r2_0={'none' if r.r2_0 is None else f'{r.r2_0:.6g}'}; "
                              f"depth_per_side={'none' if r.depth is None else f'{r.depth:.6g}'} "
                              f"shares, time-weighted; level {level}"),
                        **{**common, "window": window_label(r.start_ns, r.end_ns)})
        look.window_trials.append(tid)
    sc = look.scaling
    look.scaling_trial = log_trial(
        registry, question="depth scaling: log beta0 on log depth per side across windows",
        clock="calendar", bucket=sizes.key_s, n_obs=sc.n_used, metric="elasticity",
        value=sc.slope,
        note=(f"intercept={'none' if sc.intercept is None else f'{sc.intercept:.6g}'}; "
              f"r2={'none' if sc.r2 is None else f'{sc.r2:.6g}'}; windows skipped={sc.n_skipped}; "
              f"window={sizes.window_s:g} s; level {level}"),
        **common)
    return look


# ----------------------------------------------------------- grading
def grade(look: Look) -> list[tuple[bool | None, str]]:
    """The Day 5 Step 0 list, line by line, with the numbers. True held,
    False failed, None could not be checked."""
    k, ke = look.key, look.key_event
    out = []
    ns = look.net_share
    out.append((None if ns is None else ns < 0.05,
                f"daily net flow under 5% of gross: {100 * ns:.2f}%" if ns is not None
                else "daily net flow under 5% of gross: no flow"))
    out.append((look.level1_same,
                "level-1 and level-10 files give the same daily OFI and the same "
                + f"{k.label} series: "
                + {True: "identical", False: "DIFFERENT", None: "not checked (level-1 file absent)"}[look.level1_same]))
    f = k.fit
    ok3 = (f.beta0 is not None and f.beta0 > 0 and f.r2 is not None
           and 0.2 <= f.r2 <= 0.7 and f.hit_rate is not None and f.hit_rate > 0.7)
    out.append((ok3, f"calendar {k.label}: beta > 0, R-squared in [0.2, 0.7], hit rate above 70%: "
                     f"beta0 {f.beta0 * KSH:+.3f} cents per 1,000 sh, R-squared {f.r2:.3f} "
                     f"(origin {f.r2_0:.3f}), hit rate {100 * f.hit_rate:.1f}% "
                     f"over {f.both_nonzero:,} buckets" if f.beta0 is not None and f.r2 is not None
                     and f.hit_rate is not None else f"calendar {k.label}: undefined"))
    if ke is not None and ke.fit.r2 is not None and f.r2 is not None:
        within = abs(ke.fit.r2 - f.r2) <= 0.1
        span_ok = look.duration_span is not None and look.duration_span > 2
        out.append((within and span_ok,
                    f"event {ke.label}: R-squared within 0.1 of calendar {k.label} and bucket "
                    f"durations spanning more than two orders of magnitude: R-squared "
                    f"{ke.fit.r2:.3f} against {f.r2:.3f} (difference {ke.fit.r2 - f.r2:+.3f}), "
                    f"durations {look.duration_span:.1f} orders of magnitude"))
    else:
        out.append((None, "event clock against calendar: undefined"))
    sc = look.scaling
    if sc is not None and sc.slope is not None:
        out.append((-1.5 <= sc.slope <= -0.5,
                    f"log beta on log depth across {sc.n_used} windows: slope between -1.5 and -0.5: "
                    f"{sc.slope:+.2f} (R-squared {sc.r2:.2f}, {sc.n_skipped} windows skipped)"))
    else:
        out.append((None, "depth scaling: too few windows to fit"))
    cal = sorted((t for t in look.fits if t.clock == "calendar"), key=lambda t: t.bucket)
    r2s = [t.fit.r2 for t in cal]
    if len(cal) >= 2 and all(r is not None for r in r2s):
        mono = all(r2s[i] < r2s[i + 1] for i in range(len(r2s) - 1))
        out.append((mono, "R-squared rises with the calendar bucket: "
                          + ", ".join(f"{t.label} {t.fit.r2:.3f}" for t in cal)))
    out.append((None, "nothing in this list says anything about prediction; the predictive "
                      "question is Week 6's and is defined in the registry before it runs"))
    return out


# ----------------------------------------------------------- the page
def fmt(x, nd=3, plus=False) -> str:
    if x is None:
        return "n/a"
    return f"{x:+.{nd}f}" if plus else f"{x:.{nd}f}"


def render_block(look: Look) -> str:
    ids = look.trial_ids
    lines = [BEGIN,
             f"Generated by `scripts/{SCRIPT}` on {look.ticker}, level {look.level}, {DATE}; "
             f"registry rows #{min(ids)} to #{max(ids)} of `results/trials.csv`. Every number "
             f"below was a registry row before it was a number on this page.", ""]
    lines += ["### The contemporaneous relation, five ways", "",
              "Bucket mid change in cents on bucket OFI in shares. Beta is in cents per "
              "1,000 shares of imbalance. The R-squared through the origin uses the centered "
              "total sum, so it is comparable to the intercept fit's and never exceeds it. "
              "The hit rate is over buckets where both the flow and the price change are "
              "nonzero; the excluded count is the rest, empty bins included.", "",
              "| clock | bucket | n | beta, origin | R2, origin | beta, intercept | alpha (cents) | R2 | hit rate | excluded | trial |",
              "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|"]
    for t in look.fits:
        f = t.fit
        hr = "n/a" if f.hit_rate is None else f"{100 * f.hit_rate:.1f}% of {f.both_nonzero:,}"
        lines.append(f"| {t.clock} | {t.label} | {f.n:,} | "
                     f"{fmt(None if f.beta0 is None else f.beta0 * KSH, 3, True)} | {fmt(f.r2_0)} | "
                     f"{fmt(None if f.beta is None else f.beta * KSH, 3, True)} | {fmt(f.alpha, 4, True)} | "
                     f"{fmt(f.r2)} | {hr} | {f.excluded:,} | #{t.trial_id} |")
    sc = look.scaling
    lines += ["", f"### Depth scaling, {look.sizes.window_s / 60:g}-minute windows, calendar {look.key.label}", "",
              "Beta through the origin per window, beside the window's time-weighted touch "
              "depth per side (from Week 4's `time_weighted`).", "",
              "| window | n | beta, origin | R2, origin | depth per side (sh) | trial |",
              "|---|---:|---:|---:|---:|---|"]
    for r, tid in zip(sc.rows, look.window_trials):
        lines.append(f"| {window_label(r.start_ns, r.end_ns)} | {r.n:,} | "
                     f"{fmt(None if r.beta0 is None else r.beta0 * KSH, 3, True)} | {fmt(r.r2_0)} | "
                     f"{fmt(r.depth, 0)} | #{tid} |")
    if sc.slope is not None:
        lines += ["", f"log beta on log depth across {sc.n_used} windows ({sc.n_skipped} skipped): "
                      f"slope {sc.slope:+.2f}, intercept {sc.intercept:+.2f}, R-squared {sc.r2:.2f}. "
                      f"Trial #{look.scaling_trial}."]
    else:
        lines += ["", f"log beta on log depth: too few usable windows ({sc.n_used}) to fit. "
                      f"Trial #{look.scaling_trial}."]
    lines += ["", "### Expected, written before the run, graded", ""]
    for ok, text in grade(look):
        mark = {True: "held", False: "FAILED", None: "n/a"}[ok]
        lines.append(f"- **{mark}**: {text}")
    lines += [END]
    return "\n".join(lines)


SKELETON = """# OFI first look: contemporaneous and descriptive

This is the relation inside an interval, between the flow and the price
move that happened together. It is the check that the machinery is
right, not evidence that OFI at time t knows anything about the price at
t + h; that question is Week 6's, and its definition goes into the
registry before it runs.

{block}
"""


def write_report(path: Path, look: Look, ticker: str) -> None:
    """Splice the generated block into the page, leaving the prose
    around it untouched; a missing page gets a skeleton."""
    path = Path(path)
    block = render_block(look)
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


# ----------------------------------------------------------- the print
MARKS = {True: " ok ", False: "FAIL", None: " n/a"}


def print_look(look: Look) -> None:
    print(f"FIRST LOOK {look.ticker} {DATE} level {look.level}: contemporaneous and "
          f"descriptive; registry rows #{min(look.trial_ids)} to #{max(look.trial_ids)}")
    print(f"  {'clock':<9} {'bucket':<12} {'n':>7} {'beta0 c/ksh':>12} {'R2 orig':>8} "
          f"{'beta c/ksh':>11} {'alpha c':>8} {'R2':>6} {'hit rate':>9} {'excl':>7} trial")
    for t in look.fits:
        f = t.fit
        print(f"  {t.clock:<9} {t.label:<12} {f.n:>7,} "
              f"{fmt(None if f.beta0 is None else f.beta0 * KSH, 3, True):>12} {fmt(f.r2_0):>8} "
              f"{fmt(None if f.beta is None else f.beta * KSH, 3, True):>11} {fmt(f.alpha, 4, True):>8} "
              f"{fmt(f.r2):>6} {('n/a' if f.hit_rate is None else f'{100 * f.hit_rate:.1f}%'):>9} "
              f"{f.excluded:>7,} #{t.trial_id}")
    sc = look.scaling
    print(f"\n  depth scaling, {look.sizes.window_s / 60:g}-minute windows on calendar {look.key.label}:")
    for r, tid in zip(sc.rows, look.window_trials):
        print(f"    {window_label(r.start_ns, r.end_ns)}  n {r.n:>5,}  beta0 "
              f"{fmt(None if r.beta0 is None else r.beta0 * KSH, 3, True):>8}  R2 {fmt(r.r2_0):>6}  "
              f"depth/side {fmt(r.depth, 0):>6}  #{tid}")
    if sc.slope is not None:
        print(f"    log beta0 on log depth: slope {sc.slope:+.2f}, R2 {sc.r2:.2f}, "
              f"{sc.n_used} windows ({sc.n_skipped} skipped)  #{look.scaling_trial}")
    else:
        print(f"    log beta0 on log depth: too few windows ({sc.n_used})  #{look.scaling_trial}")
    print("\n  expected, written before the run:")
    for ok, text in grade(look):
        print(f"    [{MARKS[ok]}] {text}")


# ----------------------------------------------------------- the figure
def draw(look: Look, target: Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.ticker import FuncFormatter, LogLocator, NullFormatter

    colour = SERIES.get(look.ticker, "#52514e")
    ink, muted, grid, spine = "#0b0b0b", "#52514e", "#e6e5e0", "#c3c2b7"
    fig, (ax, ax2) = plt.subplots(1, 2, figsize=(11, 5.2),
                                  gridspec_kw={"width_ratios": [1.35, 1], "wspace": 0.3})
    k = look.key
    xs = [b.ofi for b in k.buckets]
    ys = [b.dmid_cents for b in k.buckets]
    ax.scatter(xs, ys, s=7, color=colour, alpha=0.25, linewidths=0)
    f = k.fit
    if f.beta0 is not None:
        lo, hi = min(xs), max(xs)
        ax.plot([lo, hi], [f.beta0 * lo, f.beta0 * hi], color=ink, linewidth=2)
        ax.text(0.03, 0.97,
                f"beta = {f.beta0 * KSH:.2f} cents per 1,000 sh (through the origin)\n"
                f"R-squared {f.r2_0:.2f} (origin), {f.r2:.2f} (with intercept)\n"
                f"hit rate {100 * f.hit_rate:.0f}% over {f.both_nonzero:,} nonzero buckets",
                transform=ax.transAxes, va="top", ha="left", fontsize=9.5, color=muted)
    ax.axhline(0, color=spine, linewidth=1)
    ax.axvline(0, color=spine, linewidth=1)
    ax.set_xlabel(f"OFI over the {k.label} bin, shares")
    ax.set_ylabel(f"mid change over the same bin, cents")
    ax.xaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:+,.0f}" if v else "0"))
    ax.set_title(f"{k.fit.n:,} bins, contemporaneous", loc="left", fontsize=10.5, color=muted)

    sc = look.scaling
    rows = [r for r in sc.rows if r.beta0 is not None and r.beta0 > 0 and r.depth]
    dx = [r.depth for r in rows]
    dy = [r.beta0 * KSH for r in rows]
    ax2.scatter(dx, dy, s=42, color=colour, linewidths=0)
    if sc.slope is not None:
        lo, hi = min(dx) * 0.9, max(dx) * 1.1
        line = [math.exp(sc.intercept) * d ** sc.slope * KSH for d in (lo, hi)]
        ax2.plot([lo, hi], line, color=ink, linewidth=2)
        ax2.text(0.97, 0.97, f"slope {sc.slope:+.2f} (R-squared {sc.r2:.2f})\n"
                             f"{sc.n_used} half-hour windows",
                 transform=ax2.transAxes, va="top", ha="right", fontsize=9.5, color=muted)
    for r in (rows[0], rows[-1]) if rows else ():
        ax2.annotate(hms(r.start_ns)[:5], (r.depth, r.beta0 * KSH), xytext=(7, 0),
                     textcoords="offset points", fontsize=9, color=muted, va="center")
    ax2.set_xscale("log")
    ax2.set_yscale("log")
    for axis in (ax2.xaxis, ax2.yaxis):
        axis.set_major_locator(LogLocator(base=10, subs=(1.0, 2.0, 5.0)))
        axis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:g}"))
        axis.set_minor_formatter(NullFormatter())
    ax2.set_xlabel("touch depth per side, shares (time-weighted, per half hour)")
    ax2.set_ylabel("beta through the origin, cents per 1,000 sh")
    ax2.set_title("thinner book, steeper slope", loc="left", fontsize=10.5, color=muted)
    for a in (ax, ax2):
        a.grid(True, color=grid, linewidth=0.8)
        a.set_axisbelow(True)
        for side in ("top", "right"):
            a.spines[side].set_visible(False)
        a.spines["left"].set_color(spine)
        a.spines["bottom"].set_color(spine)
        a.tick_params(colors=muted)
        a.xaxis.label.set_color(muted)
        a.yaxis.label.set_color(muted)
    fig.suptitle(f"{look.ticker} {DATE}: price change against order flow imbalance, "
                 f"inside the same interval", x=0.07, ha="left", fontsize=12, color=ink)
    fig.subplots_adjust(top=0.86, bottom=0.12, left=0.07, right=0.97)
    target.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(target, dpi=150, facecolor="#fcfcfb")
    print(f"\n  drew {target}")


# ----------------------------------------------------------- main
def run(evs, depth_bins, ticker: str, level: int, sizes: Sizes, registry: Path,
        page: Path, figure_path: Path | None = None, level1=None,
        script: str = SCRIPT, open_ns: int = OPEN_NS, close_ns: int = CLOSE_NS) -> Look:
    """The whole look in its only legal order: log everything, then
    print, then write the page, then draw. ``level1`` is a callable
    given the key buckets, run after the regressions are logged (it is
    a known-answer check, not a trial)."""
    look = first_look(evs, depth_bins, ticker, level, sizes, registry,
                      open_ns, close_ns, script)
    if level1 is not None:
        look.level1_same = level1(look.key.buckets)
    # everything is logged; now, and only now, the numbers may be seen
    print_look(look)
    write_report(page, look, ticker)
    print(f"\n  wrote the generated block into {page}")
    if figure_path is not None:
        try:
            draw(look, figure_path)
        except ImportError:
            print("  (matplotlib is not installed; pip install -e '.[analysis]' to draw)",
                  file=sys.stderr)
    return look


def level1_identity(args, key_buckets) -> bool | None:
    """The known answer by construction, checked on the real files when
    the level-1 pair is on disk: the key calendar series must be the
    same, flow and mids, from both files."""
    mp, bp = paths(Path(args.data), args.ticker, 1)
    if not (mp.exists() and bp.exists()):
        return None
    evs1 = events(touches(read_messages(mp), read_orderbook(bp, 1)))
    b1 = aggregate_calendar(evs1, int(round(args.key_s * NS)))
    same = [(b.ofi, b.abs_flow, b.mid2_open, b.mid2_close) for b in b1]
    ours = [(b.ofi, b.abs_flow, b.mid2_open, b.mid2_close) for b in key_buckets]
    return same == ours


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ticker", default="AAPL")
    ap.add_argument("--level", type=int, default=10, choices=(1, 5, 10))
    ap.add_argument("--data", default="data/lobster")
    ap.add_argument("--out", default="results")
    ap.add_argument("--registry", default=str(REGISTRY))
    ap.add_argument("--write", default=None,
                    help="splice the tables into this page (default results/ofi_first_look.md)")
    ap.add_argument("--figure", action="store_true", help="draw results/figures/ofi_first_look.png")
    ap.add_argument("--key-s", type=float, default=Sizes.key_s, dest="key_s")
    args = ap.parse_args()

    msg_path, book_path = paths(Path(args.data), args.ticker, args.level)
    for p in (msg_path, book_path):
        if not p.exists():
            raise SystemExit(f"no such file: {p}")
    msgs = read_messages(msg_path)
    ref = read_orderbook(book_path, args.level)
    skipped: list[Touch] = []
    evs = list(events(touches(msgs, ref), skipped))
    sizes = Sizes(key_s=args.key_s)
    depth_bins = time_weighted(msgs, ref, int(round(sizes.window_s * NS)))
    out = Path(args.out)
    page = Path(args.write) if args.write else out / "ofi_first_look.md"
    run(evs, depth_bins, args.ticker, args.level, sizes, Path(args.registry), page,
        figure_path=(out / "figures" / "ofi_first_look.png") if args.figure else None,
        level1=lambda key_buckets: level1_identity(args, key_buckets))


if __name__ == "__main__":
    main()
