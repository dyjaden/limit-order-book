"""Week 4's known-answer checks, strongest first.

Check 1, a known answer by construction. The level-1 and level-10 files
describe the same touch: every message that changes the best price or
the best size on either side concerns a price inside the top level, so
it is in both files, and executions of visible orders happen at the
touch, so those are in both files too. Therefore the time-weighted
touch statistics computed from the two pairs must agree TO THE INTEGER
over their common window. This is not a tolerance test; a one-nanosecond
difference is a bug in the measurement code or a misunderstanding of
the file format, and either is worth knowing.

Check 2, the replay's error budget. Day 2 proved a replay of a
level-filtered file cannot be exact and classified every divergence.
Here the same statistics are computed from OUR replayed book and from
LOBSTER's reference, and the relative differences are the price of
having no answer key: on raw ITCH there is no reference, and this is
how far a level-filtered replay's descriptive statistics can be
trusted. The signs were predicted before the run from the Day 2
taxonomy (STALE outnumbers BACKFILL): our depth higher, our spread
narrower or equal.

Check 3, numbers we did not produce. The SEC's MIDAS per-security
metrics for the same two names on the same day: cancels, trades, odd
lots, hidden trades, order and trade volume, sampled 9:35 to 16:00 over
every exchange's proprietary feed. Ours is Nasdaq only and level-10
only, so agreement is not expected; explained disagreement is, and the
direction of each disagreement is written down beside the pair.
"""
from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from pathlib import Path

from lob.book import Book, Side
from lob.lobster import LobsterMessage, ReferenceRow
from lob.micro import (CLOSE_NS, NS, OPEN_NS, BinStats, book_shape, states,
                       time_weighted, window)
from lob.replay import LobsterReplayer

# the integer accumulators the two files must agree on exactly
TOUCH_SUMS = ("two_sided_ns", "spread_x_ns", "mid2_x_ns", "one_cent_ns",
              "touch_x_ns", "trades", "trade_shares")

MIDAS_START_NS = 34_500 * NS          # 09:35:00


# ------------------------------------------------------------- check 1
def touch_sums(bins: list[BinStats]) -> dict[str, int]:
    day = window(bins, bins[0].start_ns, bins[-1].end_ns)
    return {k: getattr(day, k) for k in TOUCH_SUMS}


def common_window(*message_lists) -> int:
    """The later of the first timestamps: before it, at least one file
    has no state to speak of."""
    return max(m[0].time_ns for m in message_lists)


def levels_agree(msgs_a, ref_a, msgs_b, ref_b,
                 close_ns: int = CLOSE_NS) -> tuple[dict, dict, bool]:
    """Touch sums from two (message, reference) pairs of the same day
    over their common window, and whether they are identical."""
    start = common_window(msgs_a, msgs_b)
    width = close_ns - start
    a = touch_sums(time_weighted(msgs_a, ref_a, width, start, close_ns))
    b = touch_sums(time_weighted(msgs_b, ref_b, width, start, close_ns))
    return a, b, a == b


# ------------------------------------------------------------- check 2
def replayed_rows(messages: list[LobsterMessage], reference: list[ReferenceRow],
                  levels: int = 10) -> tuple[list[ReferenceRow], object]:
    """OUR book after every message, as reference-shaped rows: the Day 2
    machinery (seed from row 0, dark rule, evictions, witness) applied
    unchanged. Row 0 is the seed, which reproduces the reference's row 0
    at the level totals by construction."""
    book = Book()
    rep = LobsterReplayer(book, levels=levels)
    rep.seed(reference[0])
    rows = [reference[0]]
    for i in range(1, len(messages)):
        rep.apply(i, messages[i])
        rows.append(ReferenceRow(asks=book.top_levels(Side.ASK, levels),
                                 bids=book.top_levels(Side.BID, levels)))
    return rows, rep.stats


@dataclass(frozen=True)
class Budget:
    name: str
    reference: float
    ours: float
    predicted: str                # 'higher' | 'lower or equal' | 'none'

    @property
    def relative(self) -> float | None:
        return None if not self.reference else (self.ours - self.reference) / self.reference

    @property
    def sign_ok(self) -> bool:
        if self.predicted == "higher":
            return self.ours > self.reference
        if self.predicted == "lower or equal":
            return self.ours <= self.reference
        return True


def error_budget(messages, reference, ours, levels: int = 10,
                 open_ns: int = OPEN_NS, close_ns: int = CLOSE_NS) -> list[Budget]:
    """Day-level statistics from the reference rows and from our rows,
    with the predicted direction of our error beside each."""
    width = close_ns - open_ns
    ref_day = window(time_weighted(messages, reference, width, open_ns, close_ns),
                     open_ns, close_ns)
    our_day = window(time_weighted(messages, ours, width, open_ns, close_ns),
                     open_ns, close_ns)
    lv_ref, _ = book_shape(states(messages, reference, close_ns), levels)
    lv_our, _ = book_shape(states(messages, ours, close_ns), levels)
    out = [
        Budget("spread, cents", ref_day.tw_spread_cents, our_day.tw_spread_cents,
               "lower or equal"),
        Budget("one-cent share", ref_day.one_cent_share, our_day.one_cent_share,
               "none"),
        Budget("touch depth, shares", ref_day.tw_touch_depth,
               our_day.tw_touch_depth, "higher"),
        Budget("depth-5, shares", ref_day.tw_depth5, our_day.tw_depth5, "higher"),
        Budget("depth-10, shares", ref_day.tw_depth10, our_day.tw_depth10,
               "higher"),
    ]
    for side in ("bid", "ask"):
        out.append(Budget(f"{side} level-10 distance, cents",
                          lv_ref[side].tw_gap_cents(levels),
                          lv_our[side].tw_gap_cents(levels), "none"))
        out.append(Budget(f"{side} adjacent gap, cents",
                          lv_ref[side].tw_adjacent_gap_cents,
                          lv_our[side].tw_adjacent_gap_cents, "none"))
    return out


@dataclass(frozen=True)
class Decomposition:
    """A net depth error split into the two forces behind it, both in
    time-weighted shares: SURPLUS is what we hold that the reference
    does not (stale liquidity the filter never told us left), DEFICIT is
    what the reference holds that we do not (liquidity that arrived
    below the band, plus whatever our repair rules evicted alive)."""
    name: str
    reference: float
    surplus: float
    deficit: float

    @property
    def net(self) -> float:
        return self.surplus - self.deficit


def depth_decomposition(messages, reference, ours, levels: int = 10,
                        close_ns: int = CLOSE_NS) -> list[Decomposition]:
    """Why a net depth error has the sign it has: the row taxonomy
    counts rows, and this counts shares x time. Computed per state on
    the touch and on the top `levels`, both sides together."""
    def depth(row: ReferenceRow, k: int) -> int:
        return (sum(sz for _, sz in row.asks[:k])
                + sum(sz for _, sz in row.bids[:k]))

    acc = {1: [0, 0, 0], levels: [0, 0, 0]}          # ref x ns, surplus, deficit
    two_sided = 0
    for s_ref, s_our in zip(states(messages, reference, close_ns),
                            states(messages, ours, close_ns)):
        ns = s_ref.hold_ns
        if not ns or not s_ref.asks or not s_ref.bids:
            continue
        two_sided += ns
        for k, cell in acc.items():
            r = depth(ReferenceRow(s_ref.asks, s_ref.bids), k)
            o = depth(ReferenceRow(s_our.asks, s_our.bids), k)
            cell[0] += r * ns
            if o > r:
                cell[1] += (o - r) * ns
            else:
                cell[2] += (r - o) * ns
    out = []
    for k, (r, sur, de) in acc.items():
        out.append(Decomposition(
            "touch depth" if k == 1 else f"depth-{k}",
            r / two_sided if two_sided else 0.0,
            sur / two_sided if two_sided else 0.0,
            de / two_sided if two_sided else 0.0))
    return out


# ------------------------------------------------------------- check 3
MIDAS_COLUMNS = ("Cancels", "Trades", "LitTrades", "OddLots", "Hidden",
                 "TradesForHidden", "OrderVol", "TradeVol", "LitVol",
                 "OddLotVol", "HiddenVol", "TradeVolForHidden")

_DATE_FORMATS = (
    re.compile(r"^(\d{4})(\d{2})(\d{2})$"),                 # 20120621
    re.compile(r"^(\d{4})-(\d{2})-(\d{2})"),                # 2012-06-21
    re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})"),            # 6/21/2012
)


def norm_date(text: str) -> str | None:
    """Whatever MIDAS wrote, as YYYY-MM-DD; None if unrecognised."""
    t = text.strip()
    m = _DATE_FORMATS[0].match(t) or _DATE_FORMATS[1].match(t)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    m = _DATE_FORMATS[2].match(t)
    if m:
        return f"{m.group(3)}-{int(m.group(1)):02d}-{int(m.group(2)):02d}"
    return None


def _num(text: str) -> float | None:
    t = text.strip().replace(",", "")
    if t in ("", "NA", "N/A", "null"):
        return None
    try:
        return float(t)
    except ValueError:
        return None


def read_midas(folder: Path, tickers: set[str],
               date: str = "2012-06-21") -> dict[str, dict]:
    """The rows for `tickers` on `date` from every delimited file under
    `folder` (the SEC zip unpacks to one file per quarter). Header names
    are matched case-insensitively; the delimiter is sniffed; the date
    column may be in any of the formats MIDAS has used."""
    found: dict[str, dict] = {}
    files = sorted(p for p in Path(folder).rglob("*")
                   if p.suffix.lower() in (".csv", ".txt", ".psv", ".tsv"))
    for path in files:
        with open(path, newline="", encoding="utf-8-sig", errors="replace") as f:
            head = f.readline()
            if not head:
                continue
            delim = max(",|\t;", key=head.count)
            f.seek(0)
            reader = csv.reader(f, delimiter=delim)
            header = [h.strip() for h in next(reader)]
            lower = [h.lower() for h in header]
            if "ticker" not in lower or "date" not in lower:
                continue
            it, id_ = lower.index("ticker"), lower.index("date")
            for row in reader:
                if len(row) <= max(it, id_):
                    continue
                ticker = row[it].strip().upper()
                if ticker not in tickers or norm_date(row[id_]) != date:
                    continue
                rec = {header[i]: (_num(row[i]) if header[i] in MIDAS_COLUMNS
                                   else row[i].strip())
                       for i in range(min(len(header), len(row)))}
                rec["_file"] = path.name
                found[ticker] = rec
    return found


@dataclass(frozen=True)
class Comparison:
    statistic: str
    ours: float | None
    midas: float | None
    expected: str                 # 'ours lower' | 'ours higher' | 'close' | 'share'
    why: str

    @property
    def ratio(self) -> float | None:
        if self.ours is None or not self.midas:
            return None
        return self.ours / self.midas

    @property
    def as_expected(self) -> bool | None:
        r = self.ratio
        if r is None:
            return None
        if self.expected == "ours lower":
            return r < 1
        if self.expected == "ours higher":
            return r > 1
        if self.expected == "close":
            return 0.5 <= r <= 2.0
        if self.expected == "share":
            return 0.15 <= r <= 0.5            # Nasdaq's plausible share
        return None


def ours_on_midas_window(bins: list[BinStats]) -> dict[str, float | None]:
    """Our counts on MIDAS's 9:35 to 16:00 window, from the intraday
    bins (five-minute bins put 9:35 on a bin edge)."""
    w = window(bins, MIDAS_START_NS, CLOSE_NS)
    trades = w.trades + w.hidden
    return {
        "cancel_to_trade": w.cancels / trades if trades else None,
        "hidden_rate": w.hidden / trades if trades else None,
        "odd_lot_rate": w.odd_lot_trades / trades if trades else None,
        "trade_to_order_volume": ((w.trade_shares + w.hidden_shares) / w.add_shares
                                  if w.add_shares else None),
        "volume": float(w.trade_shares + w.hidden_shares),
        "trades": float(trades),
        "cancels": float(w.cancels),
    }


def midas_comparison(ours: dict, rec: dict) -> list[Comparison]:
    """Ours beside MIDAS's, with the relation predicted before looking."""
    def g(col):
        return rec.get(col)

    def ratio(a, b):
        return None if a is None or not b else a / b

    return [
        Comparison("cancel-to-trade (messages, 9:35 to 16:00)",
                   ours["cancel_to_trade"], ratio(g("Cancels"), g("Trades")),
                   "ours lower", "our cancels are level-filtered, executions are not"),
        Comparison("hidden rate", ours["hidden_rate"],
                   ratio(g("Hidden"), g("TradesForHidden")), "close",
                   "same feed family; venue mix explains the rest"),
        Comparison("odd-lot rate", ours["odd_lot_rate"],
                   ratio(g("OddLots"), g("Trades")), "close",
                   "ITCH carries odd lots and MIDAS reads the same feeds"),
        Comparison("trade-to-order volume", ours["trade_to_order_volume"],
                   ratio(g("TradeVol"), g("OrderVol")), "ours higher",
                   "OrderVol counts every add, ours only in-band adds"),
        Comparison("daily volume, shares", ours["volume"], g("TradeVol"),
                   "share", "ours / MIDAS is Nasdaq's share: 25 to 35% expected"),
        Comparison("trades", ours["trades"], g("Trades"), "share",
                   "Nasdaq's share of the day's executions"),
    ]
