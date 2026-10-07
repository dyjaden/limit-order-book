"""The backtester's spread assumption, read from its own README and audited.

    python scripts/spread_audit.py --readme ../event-driven-backtester/README.md \
        --costs ../event-driven-backtester/src/backtester/costs.py \
        --quote-also ../event-driven-backtester/results/momentum_baseline.md

Week 7 row C of the plan. Step 2 measured what a taker paid on the
consolidated tape for the backtester's universe (results/taq_spreads_{date}.csv,
one row per name, the effective spread in basis points of the mid). This
script holds the event-driven-backtester's transaction-cost assumption
against that measurement, by dollar-volume decile of the S&P 500.

The assumption is READ, not remembered. The plan's memory of the
backtester's README is "one basis point"; the script opens the README at
the path given, finds every paragraph that states a spread figure in
basis points, quotes each one into its output with its line numbers, and
takes the figure from them. With ``--costs`` it also parses the cost
model's source (``HalfSpreadSlippage``) for the default of ``spread_bps``
and for whether the fill price halves it, because a figure the prose
calls a half-spread and the code treats as a full spread is two different
one-way costs, and the audit reports both.

Three one-way figures are audited, each against the same measurement:
the README's figure read as a half-spread (what the prose says a taker
paid), the figure the code charges at that setting (half of it, when the
code halves), and the top of the README's stated range. For each: the
share of names whose measured half effective spread is at or above the
figure (the names where the assumption is not light), the decile whose
median is nearest the figure (nearest in log ratio; a match within 25%
is called a match), and the factor by which the median name and the
bottom decile exceed it. Every comparison is a registry row (status
descriptive) before anything prints. The page results/spread_audit.md
gets the quotes, the decile table, the comparisons with their trial ids,
the expectations graded, and the sentence the backtester's README can
cite.
"""
from __future__ import annotations

import argparse
import ast
import csv
import math
import re
import statistics
import sys
from dataclasses import dataclass, field
from pathlib import Path

from lob.registry import REGISTRY, log_trial

SCRIPT = "spread_audit.py"
BEGIN, END = "<!-- audit:begin -->", "<!-- audit:end -->"
MATCH_BAND = (0.8, 1.25)                 # a decile median inside this ratio of the figure "matches"
WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
         "eight": 8, "nine": 9, "ten": 10}
_NUM = r"(\d+(?:\.\d+)?|" + "|".join(WORDS) + r")"
_UNIT = r"\s*(?:bps?\b|basis\s+points?\b)"
RANGE_RE = re.compile(_NUM + r"\s*(?:-|\u2013|\u2014|to)\s*" + _NUM + _UNIT, re.IGNORECASE)
VALUE_RE = re.compile(r"\b" + _NUM + _UNIT, re.IGNORECASE)
LIST_ITEM = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+")


# ------------------------------------------------------------- the data
def read_spreads(path: Path) -> list[dict]:
    """Step 2's rows: ticker, decile (1 the thinnest tenth of the index by
    dollar volume), dollar volume, the effective spread in bps and, from
    it, the half effective spread: the one-way cost of crossing."""
    out = []
    with open(path, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if not r.get("effective_bps") or not r.get("decile"):
                continue
            out.append({"ticker": r["ticker"], "decile": int(r["decile"]),
                        "price": float(r["price"]) if r.get("price") else None,
                        "dollar_volume": float(r["dollar_volume"]) if r.get("dollar_volume") else None,
                        "half_bps": float(r["effective_bps"]) / 2,
                        "unsigned_share": float(r["unsigned_share"]) if r.get("unsigned_share") else None})
    if not out:
        raise SystemExit(f"{path}: no rows with an effective spread and a decile")
    return out


def quartiles(xs) -> tuple[float, float, float]:
    """Lower quartile, median, upper quartile; one value is all three."""
    xs = sorted(xs)
    if len(xs) == 1:
        return xs[0], xs[0], xs[0]
    q = statistics.quantiles(xs, n=4, method="inclusive")
    return q[0], statistics.median(xs), q[2]


def by_decile(rows: list[dict]) -> list[dict]:
    """One summary per decile present, ascending (the first is the bottom)."""
    out = []
    for dec in range(1, 11):
        members = [r for r in rows if r["decile"] == dec]
        if not members:
            continue
        hs = [r["half_bps"] for r in members]
        q1, med, q3 = quartiles(hs)
        dv = [r["dollar_volume"] for r in members if r["dollar_volume"] is not None]
        out.append({"decile": dec, "names": len(members), "q1": q1, "median": med, "q3": q3,
                    "min": min(hs), "max": max(hs),
                    "median_dollar_volume": statistics.median(dv) if dv else None,
                    "tickers": ", ".join(sorted(r["ticker"] for r in members))})
    return out


# ------------------------------------------------------- the assumption
@dataclass(frozen=True)
class Quote:
    source: str            # file name
    lines: str             # "515-516"
    text: str              # the paragraph, unwrapped, or one table row


@dataclass
class Assumption:
    readme: str
    quotes: list[Quote] = field(default_factory=list)
    low_bps: float | None = None            # the stated range, when there is one
    high_bps: float | None = None
    values_bps: list[float] = field(default_factory=list)   # every single figure stated beside "spread"
    # from --costs, when given
    costs: str | None = None
    default_bps: float | None = None        # HalfSpreadSlippage's default spread_bps
    halved: bool | None = None              # does fill_price charge spread_bps / 2 one way?
    code_quote: str | None = None

    @property
    def figure_bps(self) -> float:
        """The figure the prose states: the code's default when the source
        was read, else the low end of the README's range, else the one
        figure the README gives. Never a remembered number."""
        if self.default_bps is not None:
            return self.default_bps
        if self.low_bps is not None:
            return self.low_bps
        if len(set(self.values_bps)) == 1:
            return self.values_bps[0]
        raise SystemExit(f"{self.readme}: no single spread figure and no range to audit; "
                         f"figures seen: {sorted(set(self.values_bps))}")

    @property
    def code_one_way_bps(self) -> float | None:
        if self.default_bps is None or self.halved is None:
            return None
        return self.default_bps / 2 if self.halved else self.default_bps


def _number(token: str) -> float:
    return float(WORDS.get(token.lower(), token))


def paragraphs(text: str):
    """(first line, last line, lines) for every block: blank lines separate
    blocks, and a list item starts one, so a bullet is quoted alone."""
    start, block = None, []
    for i, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            if block:
                yield start, i - 1, block
            start, block = None, []
            continue
        if block and LIST_ITEM.match(line):
            yield start, i - 1, block
            start, block = None, []
        if start is None:
            start = i
        block.append(line)
    if block:
        yield start, start + len(block) - 1, block


def read_readme(path: Path, into: Assumption | None = None, collect_figures: bool = True) -> Assumption:
    """Quote every paragraph that puts a figure in basis points beside the
    word "spread", and read the range and the single figures out of them.
    A table whose text names the spread is quoted header and hit rows."""
    a = into or Assumption(readme=path.name)
    text = path.read_text(encoding="utf-8")
    for first, last, lines in paragraphs(text):
        is_table = all(l.lstrip().startswith("|") for l in lines)
        flat = " ".join(l.strip() for l in lines)
        if "spread" not in flat.lower():
            continue
        if is_table:
            rows = [(first + k, l.strip()) for k, l in enumerate(lines)
                    if not re.match(r"^\|[\s:\-|]+\|$", l.strip())]
            hit = [(n, l) for n, l in rows if VALUE_RE.search(l)]
            if not hit:
                continue
            for n, l in [rows[0]] + [h for h in hit if h[0] != rows[0][0]]:
                a.quotes.append(Quote(path.name, str(n), l))
            if collect_figures:
                a.values_bps += [_number(m.group(1)) for _, l in hit for m in VALUE_RE.finditer(l)]
            continue
        if not VALUE_RE.search(flat):
            continue
        a.quotes.append(Quote(path.name, f"{first}-{last}" if last > first else str(first),
                              LIST_ITEM.sub("", flat, count=1)))
        if not collect_figures:
            continue
        spans = []
        for m in RANGE_RE.finditer(flat):
            lo, hi = _number(m.group(1)), _number(m.group(2))
            if a.low_bps is None:
                a.low_bps, a.high_bps = lo, hi
            spans.append(m.span())
        for m in VALUE_RE.finditer(flat):
            if any(s <= m.start() < e for s, e in spans):
                continue
            a.values_bps.append(_number(m.group(1)))
    return a


def read_costs(path: Path, a: Assumption) -> Assumption:
    """Parse the cost model's source: HalfSpreadSlippage's default
    ``spread_bps`` and whether ``fill_price`` divides it by two."""
    src = path.read_text(encoding="utf-8")
    tree = ast.parse(src)
    cls = next((n for n in ast.walk(tree) if isinstance(n, ast.ClassDef) and n.name == "HalfSpreadSlippage"), None)
    if cls is None:
        raise SystemExit(f"{path}: no class HalfSpreadSlippage")
    a.costs = path.name
    for fn in (n for n in cls.body if isinstance(n, ast.FunctionDef)):
        if fn.name == "__init__":
            args = fn.args
            names = [x.arg for x in args.args]
            defaults = [None] * (len(names) - len(args.defaults)) + list(args.defaults)
            for name, d in zip(names, defaults):
                if name == "spread_bps" and isinstance(d, ast.Constant):
                    a.default_bps = float(d.value)
        if fn.name == "fill_price":
            a.halved = any(isinstance(n, ast.BinOp) and isinstance(n.op, ast.Div)
                           and isinstance(n.right, ast.Constant)
                           and isinstance(n.right.value, (int, float)) and n.right.value == 2
                           for n in ast.walk(fn))
    doc = ast.get_docstring(cls) or ""
    first = " ".join(doc.split("\n\n")[0].split()) if doc else ""
    detail = next((" ".join(p.split()) for p in doc.split("\n\n")[1:] if "spread_bps" in p), "")
    a.code_quote = (first + (" " + detail if detail else "")).strip() or None
    if a.default_bps is None:
        raise SystemExit(f"{path}: HalfSpreadSlippage has no default for spread_bps")
    if a.low_bps is not None and not (a.low_bps <= a.default_bps <= (a.high_bps or a.low_bps)):
        print(f"  warning: the code's default {a.default_bps:g} bp is outside the README's range "
              f"{a.low_bps:g}-{a.high_bps:g} bp", file=sys.stderr)
    return a


# ------------------------------------------------------------ the audit
@dataclass(frozen=True)
class Reading:
    key: str               # 'readme' | 'code' | 'range_top'
    one_way_bps: float
    label: str             # one line: the figure and where it came from


def readings(a: Assumption) -> list[Reading]:
    out = [Reading("readme", a.figure_bps,
                   f"{a.figure_bps:g} bp one way: the README's figure read as a half-spread, the way its prose names it")]
    cw = a.code_one_way_bps
    if cw is not None and cw != a.figure_bps:
        out.append(Reading("code", cw,
                           f"{cw:g} bp one way: what HalfSpreadSlippage(spread_bps={a.default_bps!r}) charges, "
                           f"half of spread_bps"))
    if a.high_bps is not None and a.high_bps != a.figure_bps:
        out.append(Reading("range_top", a.high_bps,
                           f"{a.high_bps:g} bp one way: the top of the README's {a.low_bps:g}-{a.high_bps:g} bp range"))
    return out


@dataclass(frozen=True)
class Comparison:
    reading: str
    metric: str
    value: float
    n_obs: int
    question: str
    note: str
    aux: float | None = None       # nearest_decile: that decile's median over the figure
    trial_id: int | None = None


def _names(below: list[dict], cap: int = 8) -> str:
    if not below:
        return "none"
    if len(below) <= cap:
        return ", ".join(f"{x['ticker']} {x['half_bps']:.2f}" for x in below)
    return (f"{len(below)} names, from {below[0]['ticker']} {below[0]['half_bps']:.2f} "
            f"to {below[-1]['ticker']} {below[-1]['half_bps']:.2f}")


def nearest_decile(deciles: list[dict], figure: float) -> dict:
    return min(deciles, key=lambda d: abs(math.log(d["median"] / figure)))


def matches(factor: float) -> bool:
    return MATCH_BAND[0] <= factor <= MATCH_BAND[1]


def audit(rows: list[dict], deciles: list[dict], r: Reading) -> list[Comparison]:
    """The four comparisons for one reading of the figure; nothing is
    logged here, so the arithmetic can be tested alone."""
    f = r.one_way_bps
    covered = [x for x in rows if x["half_bps"] >= f]
    below = sorted((x for x in rows if x["half_bps"] < f), key=lambda x: x["half_bps"])
    near = nearest_decile(deciles, f)
    near_factor = near["median"] / f
    med = statistics.median(x["half_bps"] for x in rows)
    bottom = deciles[0]
    label = f"the backtester's {r.label}"
    return [
        Comparison(r.key, "share_covered", len(covered) / len(rows), len(rows),
                   f"audit: share of the sampled names whose half effective spread (one-way cost of "
                   f"crossing) is at or above {label}",
                   f"{len(covered)} of {len(rows)} names; below the figure: " + _names(below)),
        Comparison(r.key, "nearest_decile", near["decile"], near["names"],
                   f"audit: the dollar-volume decile (10 the heaviest) whose median half effective "
                   f"spread is nearest {label}",
                   f"median {near['median']:.2f} bps, factor {near_factor:.2f} over the figure, "
                   + ("a match" if matches(near_factor) else "no decile within 25%")
                   + "; decile medians " + ", ".join(f"{d['decile']}:{d['median']:.2f}" for d in deciles),
                   aux=near_factor),
        Comparison(r.key, "factor_median", med / f, len(rows),
                   f"audit: the median name's half effective spread over {label} (above 1 the "
                   f"assumption is light, below 1 heavy)",
                   f"median half effective spread {med:.2f} bps over {f:g} bp"),
        Comparison(r.key, "factor_bottom_decile", bottom["median"] / f, bottom["names"],
                   f"audit: the bottom decile's median half effective spread over {label}",
                   f"decile {bottom['decile']} median {bottom['median']:.2f} bps over {f:g} bp "
                   f"({bottom['names']} names: {bottom['tickers']})"),
    ]


# ---------------------------------------------------- the expectations
EXPECTED = (
    "at or below the measured half effective spread for at least 80% of the names",
    "right for the top decile",
    "two to three times light for the median name",
    "five times or more for the bottom decile",
)


def grade(comps: list[Comparison], top_decile: int) -> list[str]:
    """held / failed for the four lines of the guide's audit expectation,
    against one reading's four comparisons."""
    c = {x.metric: x for x in comps}
    near = c["nearest_decile"]
    return ["held" if c["share_covered"].value >= 0.8 else "failed",
            "held" if int(near.value) == top_decile and matches(near.aux) else "failed",
            "held" if 2.0 <= c["factor_median"].value <= 3.0 else "failed",
            "held" if c["factor_bottom_decile"].value >= 5.0 else "failed"]


# ----------------------------------------------------------- the words
def fmt(x, nd=2) -> str:
    return "" if x is None else f"{x:.{nd}f}"


def light(factor: float) -> str:
    if matches(factor):
        return f"a match at {factor:.2f}x"
    return f"{factor:.1f}x light" if factor > 1 else f"{1 / factor:.1f}x heavy"


def ordinal(n: int) -> str:
    return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def decile_name(dec: int, deciles: list[dict]) -> str:
    if dec == deciles[-1]["decile"]:
        return "top"
    if dec == deciles[0]["decile"]:
        return "bottom"
    return ordinal(dec)


def citation(a: Assumption, rows: list[dict], deciles: list[dict], results: dict[str, list[Comparison]],
             date: str) -> list[str]:
    """The sentence the backtester's README can cite, and the code sentence
    when the code and the prose disagree."""
    n = len(rows)
    by_num = {d["decile"]: d for d in deciles}
    med = statistics.median(x["half_bps"] for x in rows)
    rd = {c.metric: c for c in results["readme"]}
    near = by_num[int(rd["nearest_decile"].value)]
    name = decile_name(near["decile"], deciles)
    if matches(rd["nearest_decile"].aux):
        where = f"right for the {name} dollar-volume decile (median {near['median']:.1f} bps)"
    else:
        where = (f"nearest the {name} dollar-volume decile (median {near['median']:.1f} bps, "
                 f"{light(rd['nearest_decile'].aux)}) and right for none")
    s1 = (f"Measured on the consolidated tape for {n} S&P 500 names on {date} (five per dollar-volume "
          f"decile plus AAPL; limit-order-book, results/spread_audit.md), the median half effective "
          f"spread, the one-way cost of crossing, was {med:.1f} bps: a {a.figure_bps:g} bp half-spread is "
          f"{where}, {light(rd['factor_median'].value)} for the median name and "
          f"{light(rd['factor_bottom_decile'].value)} in the bottom decile")
    if "range_top" in results:
        rt = {c.metric: c for c in results["range_top"]}
        s1 += (f", and the top of the {a.low_bps:g}-{a.high_bps:g} bp range covers "
               f"{int(round((1 - rt['share_covered'].value) * n))} of the {n} names "
               f"({light(rt['factor_bottom_decile'].value)} in the bottom decile)")
    s1 += "."
    out = [s1]
    if "code" in results:
        cd = {c.metric: c for c in results["code"]}
        out.append(f"HalfSpreadSlippage halves spread_bps, so the runs labelled {a.default_bps:g} bp charged "
                   f"{a.code_one_way_bps:g} bp one way, which is {light(cd['factor_median'].value)} for the "
                   f"median name and {light(cd['factor_bottom_decile'].value)} in the bottom decile.")
    return out


def sources(a: Assumption) -> list[str]:
    out = [a.readme]
    for q in a.quotes:
        if q.source not in out:
            out.append(q.source)
    if a.costs and a.costs not in out:
        out.append(a.costs)
    return out


def print_audit(a: Assumption, rows: list[dict], deciles: list[dict], results: dict[str, list[Comparison]],
                rdgs: list[Reading], sentences: list[str], date: str) -> None:
    print("\nRead from " + ", ".join(sources(a)) + ":")
    for q in a.quotes:
        print(f"  {q.source}:{q.lines}  {q.text}")
    if a.code_quote:
        print(f"  {a.costs}: HalfSpreadSlippage(spread_bps={a.default_bps!r}), fill_price "
              f"{'halves' if a.halved else 'does not halve'} it: \"{a.code_quote}\"")
    print(f"\nHalf effective spread, bps one way, {len(rows)} names on {date}; decile 1 is the thinnest "
          f"tenth of the index:")
    print("  decile  names  q1    median  q3    min   max")
    for d in deciles:
        print(f"  {d['decile']:>6}  {d['names']:>5}  {d['q1']:.2f}  {d['median']:.2f}    {d['q3']:.2f}  "
              f"{d['min']:.2f}  {d['max']:.2f}")
    q1, med, q3 = quartiles(x["half_bps"] for x in rows)
    print(f"  all     {len(rows):>5}  {q1:.2f}  {med:.2f}    {q3:.2f}")
    for r in rdgs:
        print(f"\n{r.label}")
        for c in results[r.key]:
            print(f"  trial #{c.trial_id}  {c.metric:<22} {c.value:.4f}  n={c.n_obs}  {c.note}")
    print("\nThe sentence:")
    for s in sentences:
        print(f"  {s}")


# ------------------------------------------------------------ the page
def render_block(a: Assumption, rows: list[dict], deciles: list[dict], results: dict[str, list[Comparison]],
                 rdgs: list[Reading], sentences: list[str], date: str, spreads_name: str) -> str:
    by_num = {d["decile"]: d for d in deciles}
    L = [BEGIN, f"Generated by `scripts/{SCRIPT}` from `{spreads_name}` ({len(rows)} names, {date}) and the "
                f"backtester's own files; nothing below was remembered.", "",
         "**Read from " + ", ".join(f"`{x}`" for x in sources(a)) + ":**", ""]
    for q in a.quotes:
        L.append(f"- `{q.source}:{q.lines}`: {q.text}")
    if a.code_quote:
        L.append(f"- `{a.costs}`: `HalfSpreadSlippage(spread_bps={a.default_bps!r})`, and `fill_price` "
                 f"{'charges half of it' if a.halved else 'charges all of it'} one way: \"{a.code_quote}\"")
    L += ["", "Half effective spread in basis points, one way, by dollar-volume decile of the S&P 500 "
              "(1 the thinnest tenth); quartiles over the sampled names:", "",
          "| decile | names | median $ volume (M, tape) | q1 | median | q3 | min | max | sampled |",
          "|---:|---:|---:|---:|---:|---:|---:|---:|---|"]
    for d in deciles:
        L.append(f"| {d['decile']} | {d['names']} | "
                 f"{fmt(None if d['median_dollar_volume'] is None else d['median_dollar_volume'] / 1e6, 1)} | "
                 f"{d['q1']:.2f} | {d['median']:.2f} | {d['q3']:.2f} | {d['min']:.2f} | {d['max']:.2f} | {d['tickers']} |")
    q1, med, q3 = quartiles(x["half_bps"] for x in rows)
    L.append(f"| all | {len(rows)} | | {q1:.2f} | {med:.2f} | {q3:.2f} | "
             f"{min(x['half_bps'] for x in rows):.2f} | {max(x['half_bps'] for x in rows):.2f} | |")
    L += ["", "The comparisons, each a registry row (#id):", "",
          "| figure, one way | names at or above it | nearest decile (median) | median name | bottom decile |",
          "|---|---|---|---|---|"]
    for r in rdgs:
        c = {x.metric: x for x in results[r.key]}
        near = by_num[int(c["nearest_decile"].value)]
        L.append(f"| {r.label} | {c['share_covered'].value:.1%} (#{c['share_covered'].trial_id}) | "
                 f"{near['decile']} ({near['median']:.2f} bps, {light(c['nearest_decile'].aux)}; "
                 f"#{c['nearest_decile'].trial_id}) | "
                 f"{light(c['factor_median'].value)} (#{c['factor_median'].trial_id}) | "
                 f"{light(c['factor_bottom_decile'].value)} (#{c['factor_bottom_decile'].trial_id}) |")
    graded = [r for r in rdgs if r.key in ("readme", "code")]
    L += ["", "The expectation written before the run (\"one basis point one-way is ...\"), graded against "
              "the README's figure as its prose reads it"
              + (" and, beside it, against what the code charged" if len(graded) > 1 else "") + ":", "",
          "| expected | " + " | ".join(f"{r.one_way_bps:g} bp" for r in graded) + " |",
          "|---|" + "---|" * len(graded)]
    grades = {r.key: grade(results[r.key], deciles[-1]["decile"]) for r in graded}
    for i, line in enumerate(EXPECTED):
        L.append(f"| {line} | " + " | ".join(grades[r.key][i] for r in graded) + " |")
    L += ["", "**The sentence the backtester's README can cite:**", ""]
    for s in sentences:
        L.append(f"> {s}")
        L.append(">")
    L.pop()
    L.append(END)
    return "\n".join(L)


SKELETON = """# The backtester's spread assumption, audited

Week 7 row C of the plan, and the second half of the bridge to the
[event-driven-backtester](https://github.com/dyjaden/event-driven-backtester).
That engine charges a transaction cost in basis points per side and
reports its results across a range of spread assumptions; this page
holds the assumption against the spreads Step 2 measured on the
consolidated tape for the same universe (`results/taq_spreads_2012-06-21.csv`,
51 S&P 500 names, five per dollar-volume decile plus AAPL).

## What is compared with what

The backtester's number is read, not remembered. `scripts/spread_audit.py`
opens the backtester's README at a path given on the command line,
quotes every paragraph that states a spread figure in basis points with
its line numbers, and takes the figure and the range from those
paragraphs; with `--costs` it also parses the cost model's source for
the default of `spread_bps` and for whether the fill price halves it.
The measured side is the half effective spread per name, half of
2 q (P − M) / M averaged over the day's trades, which is the one-way
cost a taker paid against the mid; the names are grouped by the S&P
500's own dollar-volume deciles on the day (CRSP close times volume,
decile 1 the thinnest tenth) and each decile is summarized by its
quartiles over the five sampled names. Three readings of the
backtester's figure are audited: the README's figure taken as a
half-spread, the way its prose names it; the amount the code charges at
that setting; and the top of the README's stated range. For each
reading, four comparisons, each a registry row before it prints: the
share of names whose measured one-way cost is at or above the figure,
the decile whose median is nearest it (nearest in log ratio; within 25%
is a match), and the factor by which the median name and the bottom
decile exceed it.

{block}

## What the numbers say

(Written after the run.)

## Expected, written before the run

The audit line from the Day 7 guide, written before Step 2's pull ran
and before the README was opened, graded above and kept whether or not
it was right:

- The audit: one basis point one-way is at or below the measured half
  effective spread for at least 80% of the names, right for the top
  decile, two to three times light for the median name and five times
  or more for the bottom decile. If the backtester's figure is not one
  basis point, the README wins and the line is rewritten before the
  comparison runs.

## Limitations

- **One day, one sample.** The spreads are 2012-06-21's, five names per
  decile; the decile medians are medians of five, and a second day is a
  rerun of Step 2 with another `--date`.
- **Half the effective spread is the spread cost alone.** The
  backtester also charges commission and square-root impact; this page
  audits only the spread leg, which is the one that has a market
  measurement.
- **The backtester's universe is the S&P 500; its results are 2015 to
  2025.** Spreads in 2012 on the same index are the closest measurement
  this repo's data can make; the tape of the backtester's own decade is
  another pull.
- **Round lots only.** The 2012 consolidated tape excluded odd lots, so
  these are the round-lot taker's spreads (Step 2's page says the same).
"""


def splice(path: Path, block: str) -> None:
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


def write_report(path: Path, *args) -> None:
    splice(path, render_block(*args))


# ------------------------------------------------------------- the run
def run(rows: list[dict], a: Assumption, registry: Path, report: Path, date: str,
        spreads_name: str, script: str = SCRIPT) -> dict[str, list[Comparison]]:
    """Log every comparison, then print, then write the page. Returns the
    comparisons with their trial ids."""
    deciles = by_decile(rows)
    rdgs = readings(a)
    results: dict[str, list[Comparison]] = {}
    for r in rdgs:
        logged = []
        for c in audit(rows, deciles, r):
            tid = log_trial(registry, script=script, question=c.question, ticker="SP500 sample",
                            date=date, clock="none", bucket="", horizon=0, window="09:30-16:00",
                            split="none", n_obs=c.n_obs, metric=c.metric, value=c.value,
                            status="descriptive",
                            note=c.note + f"; figure from {a.readme}"
                                 + (f" and {a.costs}" if r.key == "code" else ""))
            logged.append(Comparison(c.reading, c.metric, c.value, c.n_obs, c.question, c.note, c.aux, tid))
        results[r.key] = logged
    sentences = citation(a, rows, deciles, results, date)
    print_audit(a, rows, deciles, results, rdgs, sentences, date)
    write_report(report, a, rows, deciles, results, rdgs, sentences, date, spreads_name)
    print(f"\n{len(rdgs) * 4} registry rows; page written to {report}")
    return results


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--readme", required=True, help="the backtester's README.md, on this machine")
    ap.add_argument("--costs", default=None, help="the backtester's src/backtester/costs.py (optional)")
    ap.add_argument("--quote-also", nargs="*", default=[], dest="quote_also",
                    help="more backtester pages whose spread lines are quoted (figures not taken from them)")
    ap.add_argument("--date", default="2012-06-21")
    ap.add_argument("--spreads", default=None, help="Step 2's CSV; default results/taq_spreads_{date}.csv")
    ap.add_argument("--registry", default=str(REGISTRY))
    ap.add_argument("--out", default="results/spread_audit.md")
    args = ap.parse_args()
    spreads = Path(args.spreads or f"results/taq_spreads_{args.date}.csv")
    rows = read_spreads(spreads)
    a = read_readme(Path(args.readme))
    if args.costs:
        read_costs(Path(args.costs), a)
    for extra in args.quote_also:
        read_readme(Path(extra), into=a, collect_figures=False)
    run(rows, a, Path(args.registry), Path(args.out), args.date, spreads.name)


if __name__ == "__main__":
    main()
