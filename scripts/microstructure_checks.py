"""The Week 4 known-answer checks, and the tables of the write-up.

    python scripts/microstructure_checks.py --ticker AAPL
    python scripts/microstructure_checks.py --ticker AAPL --write results/microstructure.md

Three checks, strongest first (see lob.checks): the level-1 and
level-10 files must agree on every touch statistic to the integer; our
replayed book's statistics are graded against the reference with the
sign of each error predicted from the Day 2 taxonomy; and the SEC
MIDAS numbers for the same name on the same day sit beside ours with
the direction of every disagreement written down before the
comparison. Check 1 is a hard failure (exit nonzero). Checks 2 and 3
are measurements whose predicted signs are graded.

Each run saves its check results to results/checks_{ticker}.json.
--write splices the write-up's tables (the headline table for every
name whose CSVs exist, the three named windows, and the three checks
for every name whose JSON exists) into results/microstructure.md
between the markers <!-- tables:begin --> and <!-- tables:end -->,
touching nothing else, so the prose stays hand-written and the numbers
stay machine-produced, and a second name never overwrites the first.
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

from lob.checks import (TOUCH_SUMS, depth_decomposition, error_budget,
                        levels_agree, midas_comparison, ours_on_midas_window,
                        read_midas, replayed_rows)
from lob.lobster import read_messages, read_orderbook
from lob.micro import (ACCUMULATORS, NS, BinStats, day_summary,
                       time_weighted)

DATE = "2012-06-21"
SPAN = "34200000_57600000"
BEGIN, END = "<!-- tables:begin -->", "<!-- tables:end -->"


def paths(data: Path, ticker: str, level: int) -> tuple[Path, Path]:
    stem = f"{ticker}_{DATE}_{SPAN}"
    return (data / f"{stem}_message_{level}.csv",
            data / f"{stem}_orderbook_{level}.csv")


def ok(flag: bool | None) -> str:
    return "[  ? ]" if flag is None else ("[ ok ]" if flag else "[FAIL]")


def f(x, digits=2) -> str:
    return "n/a" if x is None else f"{x:,.{digits}f}"


def pct(x) -> str:
    return "n/a" if x is None else f"{100 * x:.1f}%"


# ------------------------------------------------------------- checks
def run_checks(args) -> dict:
    data = Path(args.data)
    report = {"ticker": args.ticker}
    m10, b10 = paths(data, args.ticker, 10)
    m1, b1 = paths(data, args.ticker, 1)
    for p in (m10, b10):
        if not p.exists():
            raise SystemExit(f"no such file: {p}")
    msgs10 = read_messages(m10)
    ref10 = read_orderbook(b10, 10)
    bins10 = time_weighted(msgs10, ref10)
    report["bins10"] = bins10

    # ---- check 1
    print(f"CHECK 1  level-1 and level-10 files agree on the touch, {args.ticker}")
    if m1.exists() and b1.exists():
        msgs1 = read_messages(m1)
        ref1 = read_orderbook(b1, 1)
        a, b, same = levels_agree(msgs1, ref1, msgs10, ref10)
        for k in TOUCH_SUMS:
            print(f"    {k:<14} level-1 {a[k]:>24,}   level-10 {b[k]:>24,}   "
                  f"{'same' if a[k] == b[k] else 'DIFFER'}")
        print(f"  {ok(same)} identical integer sums over the common window "
              f"({len(msgs1):,} vs {len(msgs10):,} messages)")
        report["check1"] = {"ok": same, "level1": a, "level10": b,
                            "n1": len(msgs1), "n10": len(msgs10)}
    else:
        print(f"  {ok(None)} level-1 files not found; check 1 skipped")
        report["check1"] = None

    # ---- check 2
    print(f"\nCHECK 2  the replay's error budget against the reference, "
          f"{args.ticker} level 10")
    ours, stats = replayed_rows(msgs10, ref10, 10)
    budget = error_budget(msgs10, ref10, ours, 10)
    print(f"  Day 2 machinery: {stats.dark_ops:,} dark ops, "
          f"{stats.ghost_evictions:,} evictions ({stats.ghost_shares:,} shares), "
          f"{len(stats.anomalies)} anomalies, {stats.delete_size_disagreements} "
          f"delete-size disagreements")
    print(f"    {'statistic':<30} {'reference':>12} {'ours':>12} "
          f"{'relative':>10}  predicted")
    all_signs = True
    for bgt in budget:
        rel = bgt.relative
        print(f"    {bgt.name:<30} {f(bgt.reference, 3):>12} {f(bgt.ours, 3):>12} "
              f"{('n/a' if rel is None else f'{100 * rel:+.2f}%'):>10}  "
              f"{bgt.predicted}{'' if bgt.predicted == 'none' else ('  ' + ok(bgt.sign_ok))}")
        if bgt.predicted != "none" and not bgt.sign_ok:
            all_signs = False
    print(f"  {ok(all_signs)} every predicted sign held")
    decomp = depth_decomposition(msgs10, ref10, ours, 10)
    print("  the depth error decomposed (time-weighted shares):")
    for d in decomp:
        print(f"    {d.name:<14} reference {d.reference:>9,.1f}   we hold "
              f"{d.surplus:>7,.1f} the reference lacks (surplus)   the "
              f"reference holds {d.deficit:>7,.1f} we lack (deficit)   net "
              f"{d.net:>+8,.1f}")
    report["check2"] = {"budget": budget, "stats": stats, "signs_ok": all_signs,
                        "decomposition": decomp}

    # ---- check 3
    print("\nCHECK 3  SEC MIDAS, same name, same day (9:35 to 16:00)")
    midas_dir = Path(args.midas)
    recs = read_midas(midas_dir, {args.ticker}) if midas_dir.exists() else {}
    if args.ticker in recs:
        rec = recs[args.ticker]
        mine = ours_on_midas_window(bins10)
        comps = midas_comparison(mine, rec)
        print(f"  MIDAS row from {rec['_file']}: " + ", ".join(
            f"{k}={rec[k]:,.0f}" for k in ("Cancels", "Trades", "OddLots",
                                          "Hidden", "OrderVol", "TradeVol")
            if rec.get(k) is not None))
        print(f"    {'statistic':<40} {'ours':>14} {'MIDAS':>14} "
              f"{'ratio':>8}  expected")
        for c in comps:
            print(f"    {c.statistic:<40} {f(c.ours, 4):>14} {f(c.midas, 4):>14} "
                  f"{f(c.ratio, 3):>8}  {c.expected} {ok(c.as_expected)}")
        report["check3"] = {"rec": rec, "ours": mine, "comparisons": comps}
    else:
        print(f"  {ok(None)} no MIDAS row for {args.ticker} on {DATE} under "
              f"{midas_dir}; check 3 pending (download "
              f"individual_security_2012_q2.zip into data/midas/ and unzip)")
        report["check3"] = None
    return report


def to_json(report: dict) -> dict:
    """The check results as plain data, so the tables can be rendered
    for every name without rerunning anything."""
    c1, c2, c3 = report.get("check1"), report["check2"], report.get("check3")
    st = c2["stats"]
    return {
        "ticker": report["ticker"],
        "check1": None if c1 is None else {
            "ok": c1["ok"], "level1": c1["level1"], "level10": c1["level10"],
            "n1": c1["n1"], "n10": c1["n10"]},
        "check2": {
            "budget": [{"name": b.name, "reference": b.reference, "ours": b.ours,
                        "predicted": b.predicted, "relative": b.relative,
                        "sign_ok": b.sign_ok} for b in c2["budget"]],
            "stats": {"dark_ops": st.dark_ops, "evictions": st.ghost_evictions,
                      "evicted_shares": st.ghost_shares,
                      "anomalies": len(st.anomalies),
                      "delete_size_disagreements": st.delete_size_disagreements},
            "decomposition": [{"name": d.name, "reference": d.reference,
                               "surplus": d.surplus, "deficit": d.deficit,
                               "net": d.net} for d in c2["decomposition"]],
            "signs_ok": c2["signs_ok"]},
        "check3": None if c3 is None else {
            "file": c3["rec"]["_file"],
            "midas": {k: c3["rec"].get(k) for k in ("Cancels", "Trades", "OddLots",
                                                    "Hidden", "TradesForHidden",
                                                    "OrderVol", "TradeVol")},
            "comparisons": [{"statistic": c.statistic, "ours": c.ours,
                             "midas": c.midas, "ratio": c.ratio,
                             "expected": c.expected,
                             "as_expected": c.as_expected, "why": c.why}
                            for c in c3["comparisons"]]},
    }


# --------------------------------------------------------------- tables
def _bins_from_csv(path: Path) -> list[BinStats]:
    out = []
    with open(path, newline="") as fh:
        for r in csv.DictReader(fh):
            b = BinStats(int(r["bin"]), int(r["start_s"]) * NS, 300 * NS)
            for a in ACCUMULATORS:
                if a in r and r[a] != "":
                    setattr(b, a, int(r[a]))
            out.append(b)
    return out


def _stats_from_csv(path: Path) -> dict:
    with open(path, newline="") as fh:
        rows = list(csv.DictReader(fh))
    out = {}
    for r in rows:
        v = r["value"]
        out[(r["section"], r["statistic"])] = (
            None if v == "" else (float(v) if "." in v else int(v)))
    return out


def _profile_from_csv(path: Path) -> dict:
    out = {}
    with open(path, newline="") as fh:
        for r in csv.DictReader(fh):
            out[(r["side"], r["kind"], r["index"])] = r
    return out


def headline_rows(out: Path, tickers: list[str]) -> tuple[list[str], list[list[str]]]:
    """One column per name whose CSVs exist; rows are the statistics
    the write-up leads with, each with its definition."""
    cols = [t for t in tickers if (out / f"intraday_{t}_5min.csv").exists()]
    per: dict[str, dict] = {}
    for t in cols:
        bins = _bins_from_csv(out / f"intraday_{t}_5min.csv")
        d = day_summary(bins)["day"]
        life = _stats_from_csv(out / f"lifecycle_{t}.csv") if (
            out / f"lifecycle_{t}.csv").exists() else {}
        prof = _profile_from_csv(out / f"depth_profile_{t}.csv") if (
            out / f"depth_profile_{t}.csv").exists() else {}
        trades = d.trades + d.hidden

        def gap(side):
            r = prof.get((side, "summary", "adjacent_gap_cents"))
            return None if r is None else float(r["tw_size"])

        def l10(side):
            r = prof.get((side, "level", "10"))
            return None if r is None or not r["tw_gap_cents"] else float(r["tw_gap_cents"])

        per[t] = {
            "messages": f"{d.messages:,}",
            "spread_c": f(d.tw_spread_cents), "spread_bps": f(d.tw_spread_bps),
            "one_cent": pct(d.one_cent_share),
            "touch": f(d.tw_touch_depth, 0), "depth10": f(d.tw_depth10, 0),
            "trades": f"{trades:,}", "volume": f"{d.trade_shares + d.hidden_shares:,}",
            "hidden_rate": pct(d.hidden / trades if trades else None),
            "c2t_msg": f(d.cancels / trades if trades else None),
            "c2t_ord": f(life.get(("cancel_to_trade", "by_orders"))),
            "c2t_sh": f(life.get(("cancel_to_trade", "by_shares"))),
            "orders": (f"{life[('ends', 'orders_added')]:,}"
                       if ("ends", "orders_added") in life else "n/a"),
            "executed": pct(life.get(("ends", "share_executed"))),
            "censored": pct(life.get(("ends", "share_censored"))),
            "km_median": f(life.get(("lifetime_km", "median_ms")), 0),
            "s10": pct(life.get(("lifetime_km", "S_at_10s"))),
            "fill_best": pct(life.get(("fill_by_distance", "at_best_fill_rate_shares"))),
            "fill_2_5": pct(life.get(("fill_by_distance", "2_to_5_fill_rate_shares"))),
            "odd_exec": pct(life.get(("cancel_to_trade", "odd_lot_share_of_executions"))),
            "gap_bid": f(gap("bid")), "gap_ask": f(gap("ask")),
            "l10_bid": f(l10("bid"), 1), "l10_ask": f(l10("ask"), 1),
        }
    rows = [
        ("messages (level-10 file)", "messages", "rows in the message file, 9:30 to 16:00"),
        ("quoted spread, cents", "spread_c", "time-weighted, two-sided states"),
        ("quoted spread, bps of mid", "spread_bps", "time-weighted spread over time-weighted mid"),
        ("spread at one cent", "one_cent", "share of two-sided time"),
        ("depth at the touch, shares", "touch", "best bid size plus best ask size, time-weighted"),
        ("depth in ten levels, shares", "depth10", "both sides, time-weighted"),
        ("executions", "trades", "types 4 and 5"),
        ("volume, shares", "volume", "types 4 and 5, Nasdaq only"),
        ("hidden share of executions", "hidden_rate", "type 5 over types 4 and 5"),
        ("cancel-to-trade, by messages", "c2t_msg", "(types 2 + 3) / (types 4 + 5)"),
        ("cancel-to-trade, by orders", "c2t_ord", "adds ending cancelled / adds ending executed"),
        ("cancel-to-trade, by shares", "c2t_sh", "shares cancelled / shares executed"),
        ("visible orders added", "orders", "type-1 messages"),
        ("orders ending executed", "executed", "share of adds"),
        ("orders censored", "censored", "no observed end: alive at close or died below the band"),
        ("lifetime median, ms", "km_median", "Kaplan-Meier"),
        ("still resting after 10 s", "s10", "Kaplan-Meier S(10 s)"),
        ("fill rate, joined the best", "fill_best", "shares executed / shares added"),
        ("fill rate, 2 to 5 cents behind", "fill_2_5", "shares executed / shares added"),
        ("odd-lot share of executions", "odd_exec", "executions under 100 shares"),
        ("adjacent-level gap, bid / ask, cents", "gap_bid", "mean cents between consecutive occupied levels"),
        ("level 10 from the touch, bid / ask, cents", "l10_bid", "mean distance of the tenth occupied level"),
    ]
    table = []
    for label, key, definition in rows:
        cells = []
        for t in cols:
            v = per[t][key]
            if key == "gap_bid":
                v = f"{per[t]['gap_bid']} / {per[t]['gap_ask']}"
            if key == "l10_bid":
                v = f"{per[t]['l10_bid']} / {per[t]['l10_ask']}"
            cells.append(v)
        table.append([label] + cells + [definition])
    return cols, table


def windows_rows(out: Path, tickers: list[str]) -> list[list[str]]:
    rows = []
    for t in tickers:
        p = out / f"intraday_{t}_5min.csv"
        if not p.exists():
            continue
        w = day_summary(_bins_from_csv(p))
        for key in ("open5", "midday", "close5"):
            b = w[key]
            rows.append([t, b.label, f(b.tw_spread_cents), f(b.tw_spread_bps),
                         pct(b.one_cent_share), f(b.tw_touch_depth, 0),
                         f"{b.trades:,}", f"{b.trade_shares:,}"])
    return rows


def md_table(header: list[str], rows: list[list[str]]) -> str:
    lines = ["| " + " | ".join(header) + " |",
             "|" + "|".join("---" for _ in header) + "|"]
    lines += ["| " + " | ".join(r) + " |" for r in rows]
    return "\n".join(lines)


def render_tables(out: Path, tickers: list[str]) -> str:
    cols, table = headline_rows(out, tickers)
    parts = [BEGIN,
             "### Headline table",
             "",
             md_table(["statistic"] + cols + ["definition"], table),
             "",
             "### The named windows",
             "",
             md_table(["name", "window", "spread c", "spread bps", "one cent",
                       "touch sh", "visible trades", "visible volume"],
                      windows_rows(out, tickers)),
             ""]
    for t in tickers:
        jp = out / f"checks_{t}.json"
        if not jp.exists():
            continue
        r = json.loads(jp.read_text(encoding="utf-8"))
        c1 = r.get("check1")
        parts += [f"### Check 1, {t}: level-1 and level-10 files, common window", ""]
        if c1:
            rows = [[k, f"{c1['level1'][k]:,}", f"{c1['level10'][k]:,}",
                     "same" if c1["level1"][k] == c1["level10"][k] else "DIFFER"]
                    for k in TOUCH_SUMS]
            parts += [md_table(["integer sum", f"level-1 file ({c1['n1']:,} msgs)",
                                f"level-10 file ({c1['n10']:,} msgs)", "agree"], rows),
                      "", f"Verdict: {'identical' if c1['ok'] else 'NOT identical'}.", ""]
        else:
            parts += ["Level-1 files not on disk; not run.", ""]
        c2 = r["check2"]
        rows = [[b["name"], f(b["reference"], 3), f(b["ours"], 3),
                 "n/a" if b["relative"] is None else f"{100 * b['relative']:+.2f}%",
                 b["predicted"],
                 "" if b["predicted"] == "none" else ("held" if b["sign_ok"] else "FLIPPED")]
                for b in c2["budget"]]
        st = c2["stats"]
        drows = [[d["name"], f(d["reference"], 1), f(d["surplus"], 1), f(d["deficit"], 1),
                  f"{d['net']:+,.1f}",
                  f"{100 * d['net'] / d['reference']:+.1f}%" if d["reference"] else "n/a"]
                 for d in c2["decomposition"]]
        parts += [f"### Check 2, {t}: the replay's error budget (level 10)", "",
                  md_table(["statistic", "reference", "our replay", "relative",
                            "predicted sign", "result"], rows), "",
                  f"Day 2 machinery on this run: {st['dark_ops']:,} dark ops, "
                  f"{st['evictions']:,} evictions removing {st['evicted_shares']:,} "
                  f"shares over the day, {st['anomalies']} anomalies, "
                  f"{st['delete_size_disagreements']} delete-size disagreements.", "",
                  "The depth error decomposed, in time-weighted shares: surplus is "
                  "what we hold and the reference does not, deficit is what the "
                  "reference holds and we do not.", "",
                  md_table(["statistic", "reference", "surplus", "deficit", "net",
                            "net, relative"], drows), ""]
        c3 = r.get("check3")
        parts += [f"### Check 3, {t}: SEC MIDAS, same name, same day, 9:35 to 16:00", ""]
        if c3:
            rows = [[c["statistic"], f(c["ours"], 4), f(c["midas"], 4), f(c["ratio"], 3),
                     c["expected"],
                     "" if c["as_expected"] is None else ("yes" if c["as_expected"] else "NO"),
                     c["why"]] for c in c3["comparisons"]]
            parts += [f"MIDAS row from `{c3['file']}`.", "",
                      md_table(["statistic", "ours (Nasdaq, level 10)", "MIDAS (all venues)",
                                "ours / MIDAS", "expected", "as expected", "why"], rows), ""]
        else:
            parts += ["MIDAS file not on disk; pending. Download "
                      "`individual_security_2012_q2.zip` from the SEC's Market "
                      "Structure Data page into `data/midas/`, unzip, and rerun "
                      f"`python scripts/microstructure_checks.py --ticker {t} --write "
                      "results/microstructure.md`.", ""]
    parts.append(END)
    return "\n".join(parts)


def splice(text: str, block: str) -> str:
    s, e = text.find(BEGIN), text.find(END)
    if s == -1 or e == -1 or e < s:
        if not text:
            return block + "\n"
        return text + ("" if text.endswith("\n") else "\n") + "\n" + block + "\n"
    return text[:s] + block + text[e + len(END):]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ticker", default="AAPL")
    ap.add_argument("--tickers", nargs="+", default=["AAPL", "MSFT"],
                    help="names whose CSVs the tables should include")
    ap.add_argument("--data", default="data/lobster")
    ap.add_argument("--midas", default="data/midas")
    ap.add_argument("--out", default="results")
    ap.add_argument("--write", default=None, metavar="PATH")
    args = ap.parse_args()
    report = run_checks(args)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    jp = out / f"checks_{args.ticker}.json"
    jp.write_text(json.dumps(to_json(report), indent=1), encoding="utf-8")
    print(f"\n  saved {jp}")
    if args.write:
        target = Path(args.write)
        block = render_tables(out, args.tickers)
        old = target.read_text(encoding="utf-8") if target.exists() else ""
        target.write_text(splice(old, block), encoding="utf-8")
        print(f"  tables written into {target} between the markers")
    if report["check1"] is not None and not report["check1"]["ok"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
