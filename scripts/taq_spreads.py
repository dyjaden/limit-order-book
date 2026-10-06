"""TAQ spreads for the backtester's universe: quoted, effective, realized.

    python scripts/taq_spreads.py --date 2012-06-21 --sp500 --per-decile 5 --username j7aden
    python scripts/taq_spreads.py --date 2012-06-21 --names data/taq/names.txt --username j7aden
    python scripts/taq_spreads.py --date 2012-06-21 --cache-only          # recompute, no network

Runs on the machine that holds the WRDS credentials (a .pgpass entry;
the password never passes through this script, and if the account uses
Duo the connection waits for the push to be approved). Three stages,
each of which leaves its work on disk so a failed connection never costs
a finished pull:

1. The universe. ``--sp500`` reads the S&P 500 membership on the date
   from CRSP (the backtester's own source), with each member's closing
   price, share volume and ticker on that date, sorts the members into
   dollar-volume deciles and takes ``--per-decile`` names from each
   (evenly spaced inside the decile, so the sample spans it), writing
   data/taq/{date}/universe.csv with every member and the chosen flag.
   ``--names`` takes a plain list of tickers instead.
2. The pull. For each chosen name, the day's NBBO (taqm_YYYY.nbbom_YYYYMMDD:
   best bid and ask, regular hours) and trades (taqm_YYYY.ctm_YYYYMMDD,
   regular hours, correction code 00, the usual sale conditions dropped)
   are pulled once and cached as data/taq/{date}/{TICKER}_nbbo.csv.gz and
   _trades.csv.gz with times in integer nanoseconds. data/ is gitignored;
   TAQ is licensed.
3. The measurement. ``lob.taq.summarize_name`` on each cached pair: the
   time-weighted quoted spread, Lee-Ready signs, effective, realized (five
   minutes) and impact spreads, all in basis points of the mid, simple and
   dollar-weighted; results/taq_spreads_{date}.csv, one row per name; the
   distribution by dollar-volume decile printed; AAPL's NBBO quoted
   spread printed beside Day 4's Nasdaq-only 15.13 cents when AAPL is in
   the sample; the table spliced into results/taq_spreads.md.

The 2012 consolidated tape EXCLUDES odd lots (until December 2013), and
Day 4 found more than half of AAPL's executions that day were odd lots,
so these are the round-lot taker's spreads; the header line of every
output says so. Nothing here is a regression and no registry row is
added; the comparisons that need rows are Step 3's.

Week 7 row B of the plan.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import os
import sys
from pathlib import Path

from lob.taq import NS, Quote, Trade, summarize_name

OPEN_NS, CLOSE_NS = 34_200 * NS, 57_600 * NS
BAD_CONDITIONS = set("OZBTLGWJK")       # Holden and Jacobsen's exclusions for 2012-era TAQ
TAQ_NOTE = ("consolidated tape, regular hours, round lots only (the 2012 tape excludes "
            "odd lots), correction code 00, sale conditions O Z B T L G W J K dropped")
COLUMNS = ("ticker", "permno", "decile", "price", "dollar_volume", "shares", "trades",
           "signed", "unsigned_share", "no_realized_share", "buy_share", "quoted_bps",
           "quoted_cents", "two_sided_share", "effective_bps", "effective_bps_dw",
           "realized_bps", "realized_bps_dw", "impact_bps", "impact_bps_dw")
BEGIN, END = "<!-- taq:begin -->", "<!-- taq:end -->"


# ---------------------------------------------------------------- paths
def cache_dir(cache: Path, date: str) -> Path:
    d = Path(cache) / date
    d.mkdir(parents=True, exist_ok=True)
    return d


def nbbo_path(cache: Path, date: str, ticker: str) -> Path:
    return cache_dir(cache, date) / f"{ticker}_nbbo.csv.gz"


def trades_path(cache: Path, date: str, ticker: str) -> Path:
    return cache_dir(cache, date) / f"{ticker}_trades.csv.gz"


def universe_path(cache: Path, date: str) -> Path:
    return cache_dir(cache, date) / "universe.csv"


# -------------------------------------------------------------- the pull
def connect(username: str | None):
    import wrds
    user = username or os.environ.get("WRDS_USERNAME")
    if not user:
        raise SystemExit("give --username or set WRDS_USERNAME; the password comes from .pgpass")
    print(f"  connecting to WRDS as {user} (if the account uses Duo, approve the push now)",
          flush=True)
    return wrds.Connection(wrds_username=user)


def time_to_ns(value) -> int:
    """A TAQ time (datetime.time with microseconds, or 'HH:MM:SS.ffffff')
    to integer nanoseconds after midnight."""
    if isinstance(value, str):
        hh, mm, ss = value.split(":")
        sec, _, frac = ss.partition(".")
        frac = (frac + "000000000")[:9]
        return (int(hh) * 3600 + int(mm) * 60 + int(sec)) * NS + int(frac)
    return ((value.hour * 3600 + value.minute * 60 + value.second) * NS
            + value.microsecond * 1000)


def sp500_universe(db, date: str) -> list[dict]:
    """Every S&P 500 member on the date with its ticker, close and volume
    that day, from CRSP (CIZ), the backtester's own tables."""
    rows = db.raw_sql("""
        SELECT m.permno, s.ticker, d.dlyclose AS close, d.dlyvol AS volume
        FROM crsp_a_indexes.dsp500list_v2 AS m
        JOIN crsp.stksecurityinfohist AS s
          ON s.permno = m.permno
         AND s.secinfostartdt <= %(d)s AND %(d)s <= s.secinfoenddt
        LEFT JOIN crsp.dsf_v2 AS d
          ON d.permno = m.permno AND d.dlycaldt = %(d)s
        WHERE m.mbrstartdt <= %(d)s AND %(d)s <= m.mbrenddt
        ORDER BY m.permno
    """, params={"d": date})
    out = []
    seen = set()
    for r in rows.itertuples(index=False):
        if r.permno in seen or r.ticker is None:
            continue
        seen.add(r.permno)
        close = None if r.close is None else abs(float(r.close))
        vol = None if r.volume is None else float(r.volume)
        out.append({"permno": int(r.permno), "ticker": str(r.ticker).strip().upper(),
                    "close": close, "volume": vol,
                    "dollar_volume": (close * vol) if close is not None and vol is not None else None})
    return out


def choose(universe: list[dict], per_decile: int) -> list[dict]:
    """Dollar-volume deciles (1 the thinnest, 10 the heaviest) over the
    members with a volume, and ``per_decile`` names from each, evenly
    spaced inside the decile."""
    ranked = sorted((u for u in universe if u["dollar_volume"]), key=lambda u: u["dollar_volume"])
    n = len(ranked)
    for i, u in enumerate(ranked):
        u["decile"] = min(10, 1 + (10 * i) // n) if n else None
    for u in universe:
        u.setdefault("decile", None)
        u["chosen"] = False
    for dec in range(1, 11):
        members = [u for u in ranked if u["decile"] == dec]
        if not members:
            continue
        k = min(per_decile, len(members))
        for j in range(k):
            members[(j * len(members)) // k + (len(members) // (2 * k))]["chosen"] = True
    return [u for u in universe if u["chosen"]]


def write_universe(universe: list[dict], path: Path) -> None:
    cols = ("permno", "ticker", "close", "volume", "dollar_volume", "decile", "chosen")
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, lineterminator="\n")
        w.writeheader()
        for u in universe:
            w.writerow({c: ("" if u.get(c) is None else u.get(c)) for c in cols})


def read_universe(path: Path) -> list[dict]:
    with open(path, newline="") as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        for k in ("close", "volume", "dollar_volume"):
            r[k] = float(r[k]) if r[k] else None
        r["decile"] = int(r["decile"]) if r["decile"] else None
        r["permno"] = int(r["permno"]) if r["permno"] else None
        r["chosen"] = r["chosen"] == "True"
    return rows


def pull_name(db, date: str, ticker: str, cache: Path) -> tuple[int, int]:
    """NBBO and trades for one name on one day, cached. Returns the row
    counts; (0, 0) when TAQ has no such symbol. CRSP tickers carry a
    share class as a trailing letter where TAQ uses a suffix ('BRKB' is
    sym_root 'BRK', sym_suffix 'B'), so a whole ticker that returns
    nothing is retried split."""
    yyyy, mmdd = date[:4], date[5:7] + date[8:10]
    tab_q, tab_t = f"taqm_{yyyy}.nbbom_{yyyy}{mmdd}", f"taqm_{yyyy}.ctm_{yyyy}{mmdd}"
    attempts = [(ticker, "")]
    if len(ticker) > 1 and ticker[-1].isalpha():
        attempts.append((ticker[:-1], ticker[-1]))
    for root, suffix in attempts:
        suffix_clause = ("AND (sym_suffix IS NULL OR sym_suffix = '')" if not suffix
                         else "AND sym_suffix = %(suffix)s")
        q = db.raw_sql(f"""
            SELECT time_m, best_bid, best_ask
            FROM {tab_q}
            WHERE sym_root = %(root)s {suffix_clause}
              AND time_m BETWEEN '09:30:00' AND '16:00:00'
            ORDER BY time_m
        """, params={"root": root, "suffix": suffix})
        if q.empty:
            continue
        t = db.raw_sql(f"""
            SELECT time_m, price, size, tr_scond
            FROM {tab_t}
            WHERE sym_root = %(root)s {suffix_clause}
              AND time_m BETWEEN '09:30:00' AND '16:00:00'
              AND tr_corr = '00'
            ORDER BY time_m
        """, params={"root": root, "suffix": suffix})
        with gzip.open(nbbo_path(cache, date, ticker), "wt", newline="") as f:
            w = csv.writer(f, lineterminator="\n")
            w.writerow(["time_ns", "bid", "ask"])
            for r in q.itertuples(index=False):
                w.writerow([time_to_ns(r.time_m), r.best_bid, r.best_ask])
        kept = 0
        with gzip.open(trades_path(cache, date, ticker), "wt", newline="") as f:
            w = csv.writer(f, lineterminator="\n")
            w.writerow(["time_ns", "price", "size"])
            for r in t.itertuples(index=False):
                cond = (r.tr_scond or "").strip()
                if any(c in BAD_CONDITIONS for c in cond):
                    continue
                w.writerow([time_to_ns(r.time_m), r.price, r.size])
                kept += 1
        return len(q), kept
    return 0, 0


# --------------------------------------------------------- the measurement
def read_cached(cache: Path, date: str, ticker: str) -> tuple[list[Quote], list[Trade]]:
    quotes, trades = [], []
    with gzip.open(nbbo_path(cache, date, ticker), "rt", newline="") as f:
        for r in csv.DictReader(f):
            if r["bid"] and r["ask"]:
                quotes.append(Quote(int(r["time_ns"]), float(r["bid"]), float(r["ask"])))
    with gzip.open(trades_path(cache, date, ticker), "rt", newline="") as f:
        for r in csv.DictReader(f):
            trades.append(Trade(int(r["time_ns"]), float(r["price"]), int(float(r["size"]))))
    return quotes, trades


def measure(cache: Path, date: str, ticker: str, meta: dict, horizon_ns: int) -> dict:
    quotes, trades = read_cached(cache, date, ticker)
    s = summarize_name(trades, quotes, OPEN_NS, CLOSE_NS, horizon_ns)
    session = CLOSE_NS - OPEN_NS
    return {"ticker": ticker, "permno": meta.get("permno"), "decile": meta.get("decile"),
            "price": s["mid"], "dollar_volume": s["dollar_volume"], "shares": s["shares"],
            "trades": s["trades"], "signed": s["signed"], "unsigned_share": s["unsigned_share"],
            "no_realized_share": s["no_realized_share"], "buy_share": s["buy_share"],
            "quoted_bps": s["quoted_bps"], "quoted_cents": s["quoted_cents"],
            "two_sided_share": s["two_sided_ns"] / session,
            "effective_bps": s["effective_bps"], "effective_bps_dw": s["effective_bps_dw"],
            "realized_bps": s["realized_bps"], "realized_bps_dw": s["realized_bps_dw"],
            "impact_bps": s["impact_bps"], "impact_bps_dw": s["impact_bps_dw"]}


def fmt(x, nd=2) -> str:
    return "n/a" if x is None else f"{x:,.{nd}f}"


def pct(x, nd=1) -> str:
    return "n/a" if x is None else f"{100 * x:.{nd}f}%"


def _median(xs):
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return None
    m = len(xs) // 2
    return xs[m] if len(xs) % 2 else (xs[m - 1] + xs[m]) / 2


def by_decile(rows: list[dict]) -> list[dict]:
    out = []
    for dec in range(1, 11):
        members = [r for r in rows if r["decile"] == dec]
        if not members:
            continue
        out.append({"decile": dec, "names": len(members),
                    "median_dollar_volume": _median(r["dollar_volume"] for r in members),
                    "median_quoted_bps": _median(r["quoted_bps"] for r in members),
                    "median_half_effective_bps": _median(r["effective_bps"] / 2 for r in members
                                                        if r["effective_bps"] is not None),
                    "median_realized_bps": _median(r["realized_bps"] for r in members),
                    "median_impact_bps": _median(r["impact_bps"] for r in members)})
    return out


def print_results(rows: list[dict], date: str) -> None:
    print(f"TAQ SPREADS {date}: {len(rows)} names; {TAQ_NOTE}")
    print(f"  {'name':<6} {'dec':>3} {'price':>8} {'$ vol (M)':>10} {'trades':>7} {'unsigned':>8} "
          f"{'quoted bps':>10} {'quoted c':>9} {'eff bps':>8} {'eff dw':>7} {'real bps':>8} {'impact':>7}")
    for r in sorted(rows, key=lambda r: (-(r["decile"] or 0), r["ticker"])):
        print(f"  {r['ticker']:<6} {str(r['decile'] or ''):>3} {fmt(r['price']):>8} "
              f"{fmt(None if r['dollar_volume'] is None else r['dollar_volume'] / 1e6, 1):>10} "
              f"{r['trades']:>7,} {pct(r['unsigned_share']):>8} {fmt(r['quoted_bps']):>10} "
              f"{fmt(r['quoted_cents']):>9} {fmt(r['effective_bps']):>8} {fmt(r['effective_bps_dw']):>7} "
              f"{fmt(r['realized_bps']):>8} {fmt(r['impact_bps']):>7}")
    print("\n  by dollar-volume decile of the universe (medians over the sampled names):")
    print(f"  {'decile':>6} {'names':>5} {'median $ vol (M)':>16} {'quoted bps':>10} "
          f"{'half eff bps':>12} {'realized':>9} {'impact':>7}")
    for d in by_decile(rows):
        print(f"  {d['decile']:>6} {d['names']:>5} "
              f"{fmt(None if d['median_dollar_volume'] is None else d['median_dollar_volume'] / 1e6, 1):>16} "
              f"{fmt(d['median_quoted_bps']):>10} {fmt(d['median_half_effective_bps']):>12} "
              f"{fmt(d['median_realized_bps']):>9} {fmt(d['median_impact_bps']):>7}")
    aapl = next((r for r in rows if r["ticker"] == "AAPL"), None)
    if aapl is not None and aapl["quoted_cents"] is not None:
        print(f"\n  AAPL: NBBO quoted spread {aapl['quoted_cents']:.2f} cents time-weighted, against "
              f"Day 4's Nasdaq-only 15.13 cents from LOBSTER "
              f"({100 * (aapl['quoted_cents'] / 15.13 - 1):+.0f}%)")


def render_block(rows: list[dict], date: str) -> str:
    L = [BEGIN, f"Generated by `scripts/taq_spreads.py` for {date}, {len(rows)} names; {TAQ_NOTE}. "
                f"Spreads in basis points of the prevailing mid; the effective spread is the full "
                f"spread (twice what the taker paid against the mid); dw is dollar-weighted; the "
                f"realized spread is at five minutes.", "",
         "| name | decile | price | $ volume (M) | trades | unsigned | quoted bps | quoted c | "
         "effective bps | effective dw | realized bps | impact bps |",
         "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for r in sorted(rows, key=lambda r: (-(r["decile"] or 0), r["ticker"])):
        L.append(f"| {r['ticker']} | {r['decile'] or ''} | {fmt(r['price'])} | "
                 f"{fmt(None if r['dollar_volume'] is None else r['dollar_volume'] / 1e6, 1)} | "
                 f"{r['trades']:,} | {pct(r['unsigned_share'])} | {fmt(r['quoted_bps'])} | "
                 f"{fmt(r['quoted_cents'])} | {fmt(r['effective_bps'])} | {fmt(r['effective_bps_dw'])} | "
                 f"{fmt(r['realized_bps'])} | {fmt(r['impact_bps'])} |")
    L += ["", "By dollar-volume decile of the universe (1 the thinnest), medians over the sampled names:", "",
          "| decile | names | median $ volume (M) | quoted bps | half effective bps | realized bps | impact bps |",
          "|---:|---:|---:|---:|---:|---:|---:|"]
    for d in by_decile(rows):
        L.append(f"| {d['decile']} | {d['names']} | "
                 f"{fmt(None if d['median_dollar_volume'] is None else d['median_dollar_volume'] / 1e6, 1)} | "
                 f"{fmt(d['median_quoted_bps'])} | {fmt(d['median_half_effective_bps'])} | "
                 f"{fmt(d['median_realized_bps'])} | {fmt(d['median_impact_bps'])} |")
    aapl = next((r for r in rows if r["ticker"] == "AAPL"), None)
    if aapl is not None and aapl["quoted_cents"] is not None:
        L += ["", f"AAPL's NBBO quoted spread: {aapl['quoted_cents']:.2f} cents time-weighted, against "
                  f"Day 4's Nasdaq-only 15.13 cents ({100 * (aapl['quoted_cents'] / 15.13 - 1):+.0f}%)."]
    L.append(END)
    return "\n".join(L)


SKELETON = """# TAQ spreads: what a taker paid

Quoted, effective and realized spreads from the consolidated tape for
the backtester's universe on one day, in basis points of the mid.

{block}
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


def write_rows(rows: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(COLUMNS), lineterminator="\n")
        w.writeheader()
        for r in rows:
            w.writerow({c: ("" if r.get(c) is None else
                            (repr(r[c]) if isinstance(r[c], float) else r[c])) for c in COLUMNS})


def compute(args, cache: Path, out: Path, meta: dict[str, dict]) -> list[dict]:
    rows = []
    for ticker in sorted(meta):
        if not (nbbo_path(cache, args.date, ticker).exists() and trades_path(cache, args.date, ticker).exists()):
            print(f"  {ticker}: not cached, skipped", file=sys.stderr)
            continue
        rows.append(measure(cache, args.date, ticker, meta[ticker], int(args.horizon * NS)))
    if not rows:
        raise SystemExit("nothing cached to measure; run the pull first")
    print_results(rows, args.date)
    target = out / f"taq_spreads_{args.date}.csv"
    write_rows(rows, target)
    splice(out / "taq_spreads.md", render_block(rows, args.date))
    print(f"\n  wrote {target} ({len(rows)} rows) and spliced the tables into {out / 'taq_spreads.md'}")
    return rows


# ----------------------------------------------------------------- main
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default="2012-06-21")
    ap.add_argument("--sp500", action="store_true", help="the S&P 500 on the date, from CRSP")
    ap.add_argument("--names", default=None, help="a file with one ticker per line")
    ap.add_argument("--per-decile", type=int, default=5, dest="per_decile")
    ap.add_argument("--username", default=None, help="WRDS username (password from .pgpass)")
    ap.add_argument("--cache", default="data/taq")
    ap.add_argument("--out", default="results")
    ap.add_argument("--horizon", type=float, default=300, help="realized-spread horizon, seconds")
    ap.add_argument("--cache-only", action="store_true", dest="cache_only",
                    help="measure what is cached; no network")
    args = ap.parse_args()
    cache, out = Path(args.cache), Path(args.out)
    upath = universe_path(cache, args.date)

    meta: dict[str, dict] = {}
    if args.cache_only:
        if upath.exists():
            meta = {u["ticker"]: u for u in read_universe(upath) if u["chosen"]}
        else:
            meta = {p.name[:-len("_nbbo.csv.gz")]: {} for p in cache_dir(cache, args.date).glob("*_nbbo.csv.gz")}
        compute(args, cache, out, meta)
        return

    db = connect(args.username)
    try:
        if args.names:
            tickers = [l.strip().upper() for l in Path(args.names).read_text().splitlines() if l.strip()]
            meta = {t: {"ticker": t} for t in tickers}
        elif args.sp500:
            if upath.exists():
                universe = read_universe(upath)
                print(f"  universe from {upath} ({len(universe)} members)")
            else:
                universe = sp500_universe(db, args.date)
                choose(universe, args.per_decile)
                write_universe(universe, upath)
                print(f"  S&P 500 on {args.date}: {len(universe)} members from CRSP, "
                      f"{sum(1 for u in universe if u['chosen'])} chosen, written to {upath}")
            meta = {u["ticker"]: u for u in universe if u["chosen"]}
        else:
            raise SystemExit("give --sp500 or --names")
        for i, ticker in enumerate(sorted(meta), start=1):
            if nbbo_path(cache, args.date, ticker).exists() and trades_path(cache, args.date, ticker).exists():
                print(f"  [{i}/{len(meta)}] {ticker}: cached", flush=True)
                continue
            nq, nt = pull_name(db, args.date, ticker, cache)
            print(f"  [{i}/{len(meta)}] {ticker}: {nq:,} NBBO rows, {nt:,} trades kept"
                  + ("" if nq else " (no such symbol in TAQ)"), flush=True)
    finally:
        db.close()
    compute(args, cache, out, meta)


if __name__ == "__main__":
    main()
