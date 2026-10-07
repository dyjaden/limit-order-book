"""Day 7, Step 3: the audit's reading and arithmetic by hand, and its discipline.

The README reader on a hand-written page: the paragraphs it quotes (and
the ones it must not), the range and the figures it reads out of them,
a bullet quoted without its neighbours. The cost-model reader on a
hand-written source: the default, and whether the fill halves it. The
four comparisons on twenty names whose answers are known. Then the rule
of the week on the script itself: every comparison is in the registry
before anything prints or is written, and every number on the page
carries its trial id.
"""
import textwrap

import pytest

from lob.registry import read
import spread_audit as sa


README = textwrap.dedent("""\
    # a backtester

    Engine, portfolio accounting, commission and half-spread costs, impact.

    - **Cost sensitivity**: my expectation was that the spread assumption
      dominates. At ~7x/yr turnover, ten basis points of assumed
      half-spread cost 0.02 Sharpe.
    - **Breadth**: top-100 beats top-50.

    Eight years of re-optimization bought two basis points of Sharpe.

    | Spread | Share of the costs-off result that survives |
    |---|---|
    | 1 bp | 83.0% |
    | 2 bp | 72.3% |

    | | Before | After |
    |---|---|---|
    | Cost at $1bn | 21.69 bp | 19.71 bp |

    ## Limitations

    - **The impact coefficient is an assumption.** Reported across a range.
    - **The same is true of the spread.** Reported across 1-5 bp rather than
      at a single value.
    - **Same-bar fills.** Mildly optimistic.
    """)

COSTS = textwrap.dedent('''\
    class SlippageModel:
        pass


    class HalfSpreadSlippage(SlippageModel):
        """You cross the spread, so you pay half of it.

        `spread_bps` is the FULL quoted spread in basis points. A buyer pays
        mid + spread/2.
        """

        def __init__(self, spread_bps: float = 2.0) -> None:
            self.spread_bps = float(spread_bps)

        def fill_price(self, quantity: int, mid: float) -> float:
            half = mid * (self.spread_bps / 10_000.0) / 2.0
            return mid + half if quantity > 0 else mid - half
    ''')


def test_read_readme_quotes_the_spread_paragraphs_and_reads_the_range(tmp_path):
    p = tmp_path / "README.md"
    p.write_text(README, encoding="utf-8")
    a = sa.read_readme(p)
    assert (a.low_bps, a.high_bps) == (1.0, 5.0)
    assert a.values_bps == [10.0, 1.0, 2.0]               # the word "ten", then the table rows
    quoted = [(q.lines, q.text) for q in a.quotes]
    assert quoted[0][0] == "5-7" and quoted[0][1].startswith("**Cost sensitivity**")   # the marker is stripped
    assert "Breadth" not in quoted[0][1]                   # the next bullet is its own block
    assert ("12", "| Spread | Share of the costs-off result that survives |") in quoted
    assert ("14", "| 1 bp | 83.0% |") in quoted and ("15", "| 2 bp | 72.3% |") in quoted
    assert quoted[-1][0] == "24-25" and "Reported across 1-5 bp" in quoted[-1][1]
    text = " ".join(t for _, t in quoted)
    assert "two basis points of Sharpe" not in text        # no "spread" in that paragraph
    assert "21.69" not in text                             # a table without the word
    assert "Mildly optimistic" not in text
    assert a.figure_bps == 1.0                             # the range's low end, no code read
    assert a.code_one_way_bps is None
    # quoting another page adds its lines and takes no figure from them
    q = tmp_path / "other.md"
    q.write_text("Cost models on the loaded arm: 7 bp half-spread, impact.\n", encoding="utf-8")
    sa.read_readme(q, into=a, collect_figures=False)
    assert a.quotes[-1].source == "other.md" and a.values_bps == [10.0, 1.0, 2.0]
    # a README with figures but no range and no single figure refuses to guess
    r = tmp_path / "vague.md"
    r.write_text("The spread is 1 bp here and 3 bp there.\n", encoding="utf-8")
    with pytest.raises(SystemExit):
        sa.read_readme(r).figure_bps


def test_read_costs_finds_the_default_and_the_halving(tmp_path):
    p = tmp_path / "costs.py"
    p.write_text(COSTS, encoding="utf-8")
    a = sa.Assumption(readme="README.md", low_bps=1.0, high_bps=5.0)
    sa.read_costs(p, a)
    assert a.default_bps == 2.0 and a.halved is True and a.code_one_way_bps == 1.0
    assert a.figure_bps == 2.0                             # the code's default wins over the range's low end
    assert "FULL quoted spread" in a.code_quote and a.code_quote.startswith("You cross the spread")
    rdgs = sa.readings(a)
    assert [(r.key, r.one_way_bps) for r in rdgs] == [("readme", 2.0), ("code", 1.0), ("range_top", 5.0)]
    # a model that charges the whole parameter one way
    q = tmp_path / "costs_full.py"
    q.write_text(COSTS.replace(" / 2.0", ""), encoding="utf-8")
    b = sa.read_costs(q, sa.Assumption(readme="README.md"))
    assert b.halved is False and b.code_one_way_bps == 2.0
    assert [r.key for r in sa.readings(b)] == ["readme"]  # nothing to add: the code agrees and there is no range
    with pytest.raises(SystemExit):
        sa.read_costs(_write(tmp_path / "none.py", "class Other:\n    pass\n"), sa.Assumption(readme="x"))


def _write(path, text):
    path.write_text(text, encoding="utf-8")
    return path


def _rows():
    """Twenty names, two per decile; decile d costs (11 - d) / 2 bps one
    way and a tenth more, so decile 10 is 0.5 and 0.6, decile 1 is 5.0
    and 5.1."""
    rows = []
    for d in range(1, 11):
        base = (11 - d) / 2
        for k, extra in enumerate((0.0, 0.1)):
            rows.append({"ticker": f"D{d:02d}{'AB'[k]}", "decile": d, "price": 50.0,
                         "dollar_volume": 1e6 * d, "half_bps": base + extra, "unsigned_share": 0.0})
    return rows


def test_the_four_comparisons_by_hand():
    rows = _rows()
    deciles = sa.by_decile(rows)
    assert [d["decile"] for d in deciles] == list(range(1, 11))
    assert deciles[0]["median"] == pytest.approx(5.05) and deciles[-1]["median"] == pytest.approx(0.55)
    assert (deciles[0]["q1"], deciles[0]["q3"]) == (pytest.approx(5.025), pytest.approx(5.075))
    assert deciles[0]["tickers"] == "D01A, D01B"
    r = sa.Reading("readme", 1.0, "1 bp one way")
    c = {x.metric: x for x in sa.audit(rows, deciles, r)}
    assert c["share_covered"].value == pytest.approx(18 / 20) and c["share_covered"].n_obs == 20
    assert "D10A 0.50, D10B 0.60" in c["share_covered"].note
    assert c["nearest_decile"].value == 9 and c["nearest_decile"].aux == pytest.approx(1.05)
    assert "a match" in c["nearest_decile"].note
    assert c["factor_median"].value == pytest.approx(2.8)           # (2.6 + 3.0) / 2 over 1
    assert c["factor_bottom_decile"].value == pytest.approx(5.05) and c["factor_bottom_decile"].n_obs == 2
    assert sa.grade(list(c.values()), top_decile=10) == ["held", "failed", "held", "held"]
    # at half a basis point the top decile matches and the bottom is ten times light
    c2 = {x.metric: x for x in sa.audit(rows, deciles, sa.Reading("code", 0.5, "0.5 bp"))}
    assert c2["share_covered"].value == 1.0 and c2["nearest_decile"].value == 10
    assert c2["factor_bottom_decile"].value == pytest.approx(10.1)
    assert sa.grade(list(c2.values()), top_decile=10) == ["held", "held", "failed", "held"]
    # a long list of names below the figure is summarized, not printed
    c3 = {x.metric: x for x in sa.audit(rows, deciles, sa.Reading("range_top", 5.0, "5 bp"))}
    assert c3["share_covered"].value == pytest.approx(2 / 20)
    assert c3["share_covered"].note.endswith("18 names, from D10A 0.50 to D02B 4.60")
    assert sa.light(1.0) == "a match at 1.00x" and sa.light(1.7) == "1.7x light" and sa.light(0.5) == "2.0x heavy"
    assert sa.ordinal(2) == "2nd" and sa.ordinal(11) == "11th" and sa.ordinal(3) == "3rd"


def test_every_comparison_is_logged_before_anything_prints(tmp_path, monkeypatch, capsys):
    readme = _write(tmp_path / "README.md", README)
    costs = _write(tmp_path / "costs.py", COSTS)
    a = sa.read_costs(costs, sa.read_readme(readme))
    rows = _rows()
    registry = tmp_path / "trials.csv"
    report = tmp_path / "spread_audit.md"
    seen = {}
    real_print, real_write = sa.print_audit, sa.write_report

    def spy_print(*args, **kw):
        seen["at_print"] = len(read(registry))
        return real_print(*args, **kw)

    def spy_write(path, *args, **kw):
        seen["at_write"] = len(read(registry))
        return real_write(path, *args, **kw)

    monkeypatch.setattr(sa, "print_audit", spy_print)
    monkeypatch.setattr(sa, "write_report", spy_write)
    results = sa.run(rows, a, registry, report, "2012-06-21", "taq_spreads_2012-06-21.csv", script="test")
    assert seen["at_print"] == 12 and seen["at_write"] == 12        # three readings, four comparisons each
    logged = read(registry)
    assert len(logged) == 12
    assert {r["status"] for r in logged} == {"descriptive"}
    assert {r["script"] for r in logged} == {"test"} and {r["horizon"] for r in logged} == {"0"}
    assert [r["metric"] for r in logged[:4]] == ["share_covered", "nearest_decile", "factor_median",
                                                   "factor_bottom_decile"]
    assert all("figure from README.md" in r["note"] for r in logged)
    assert "and costs.py" in logged[4]["note"] and "and costs.py" not in logged[0]["note"]
    assert [c.trial_id for k in ("readme", "code", "range_top") for c in results[k]] == list(range(1, 13))
    out = capsys.readouterr().out
    assert "trial #1 " in out and "trial #12 " in out and "Reported across 1-5 bp" in out
    page = report.read_text(encoding="utf-8")
    assert sa.BEGIN in page and sa.END in page
    for r in logged:
        assert f"#{r['trial_id']}" in page
    assert "`README.md:24-25`" in page and "Reported across 1-5 bp" in page
    assert "`costs.py`: `HalfSpreadSlippage(spread_bps=2.0)`" in page and "charges half of it" in page
    assert "| expected | 2 bp | 1 bp |" in page
    assert "a 2 bp half-spread is" in page and "HalfSpreadSlippage halves spread_bps" in page
    assert "(Written after the run.)" in page                      # the skeleton's prose, left for the author
    # a second run replaces the block and leaves the prose alone
    edited = page.replace("(Written after the run.)", "The prose, by hand.")
    report.write_text(edited, encoding="utf-8")
    sa.run(rows, a, registry, report, "2012-06-21", "taq_spreads_2012-06-21.csv", script="test")
    again = report.read_text(encoding="utf-8")
    assert "The prose, by hand." in again and again.count(sa.BEGIN) == 1 and "#24" in again and "#12" not in again.split(sa.BEGIN)[1]
