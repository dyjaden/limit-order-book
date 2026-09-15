"""Order lifecycles from the message file: how orders live, die, and trade.

Everything here comes from messages, because only messages carry order
ids; the reference rows are consulted once per add, to learn where the
order landed relative to the touch it joined. One ``Life`` per order
whose ADD is in the file. Operations on ids the file never introduced
are the dark ops Day 2 counted (pre-window and below-band liquidity);
they are counted again here and attributed to nobody.

The finding of Days 2 and 3 arrives here as a statistic: an order added
in view whose death is never seen either survived to the close or died
below the band with no message, and the file cannot tell the two apart.
Both are CENSORED. Lifetime statistics are therefore reported two ways:
naively, over observed deaths only, and with the Kaplan-Meier
estimator, which treats a censored order as alive at least until the
last message that named it (its add, if nothing else) and lets it drop
out of the at-risk set there. The naive median is biased short; the
Kaplan-Meier curve is the honest one, and the censored fraction travels
with every number.

Cancel-to-trade is three different numbers on the same day, and each is
reported with its definition beside it: by messages (the SEC MIDAS
shape: partial plus full cancels over executions, on MIDAS's 9:35 to
16:00 window as well as the full session), by orders (adds that ended
cancelled over adds that ended executed), and by shares.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from lob.book import Side
from lob.lobster import EventType, LobsterMessage, ReferenceRow
from lob.micro import CLOSE_NS, INCREMENT, NS
from lob.replay import side_of

MIDAS_START_NS = 34_500 * NS          # 09:35:00, MIDAS's sampling window
MIDAS_END_NS = CLOSE_NS               # to 16:00:00

DISTANCE_BUCKETS = ("improve", "at_best", "1", "2_to_5", "6_to_10",
                    "over_10", "unknown")
SURVIVAL_GRID_NS = (10**6, 10**7, 10**8, 10**9, 10**10, 60 * 10**9,
                    600 * 10**9, 3600 * 10**9)     # 1 ms .. 1 h


def distance_bucket(cents: int | None) -> str:
    if cents is None:
        return "unknown"
    if cents < 0:
        return "improve"
    if cents == 0:
        return "at_best"
    if cents == 1:
        return "1"
    if cents <= 5:
        return "2_to_5"
    if cents <= 10:
        return "6_to_10"
    return "over_10"


@dataclass
class Life:
    """One visible order, from its add to its observed end or the file's
    silence."""
    order_id: int
    side: Side
    price: int
    added_ns: int
    size_added: int
    distance_cents: int | None      # vs the same-side best BEFORE the
                                    # add: < 0 improves it, 0 joins it,
                                    # k > 0 rests k cents behind; None when
                                    # there was no prior row or no best
    executed: int = 0               # shares, type 4
    cancelled: int = 0              # shares, types 2 and 3
    fills: int = 0                  # execution messages
    first_fill_ns: int | None = None
    last_seen_ns: int = 0           # the last message naming this id
    ended_ns: int | None = None
    end: str = "censored"           # 'executed' | 'cancelled' | 'censored'

    @property
    def remaining(self) -> int:
        return self.size_added - self.executed - self.cancelled

    @property
    def lifetime_ns(self) -> int | None:
        """Observed lifetime, or None while censored."""
        return None if self.ended_ns is None else self.ended_ns - self.added_ns

    @property
    def censor_ns(self) -> int:
        """The lower bound a censored order's lifetime is known to
        exceed: it was alive at its last message."""
        return self.last_seen_ns - self.added_ns


@dataclass
class LifecycleResult:
    lives: list[Life] = field(default_factory=list)
    dark_ops: Counter = field(default_factory=Counter)       # by type name
    dark_shares: int = 0
    hidden_execs: int = 0                                    # type 5, id 0
    size_disagreements: int = 0     # a removal larger than what rested
    readded: int = 0                # an ADD for an id already alive
    by_type: Counter = field(default_factory=Counter)
    by_type_window: Counter = field(default_factory=Counter)  # MIDAS window
    exec_lots: Counter = field(default_factory=Counter)  # odd/round/mixed,
                                                         # types 4 and 5


def lot(size: int) -> str:
    """Round lot (a multiple of 100), odd lot (under 100), or mixed."""
    if size < 100:
        return "odd"
    return "round" if size % 100 == 0 else "mixed"


def _distance(msg: LobsterMessage, side: Side,
              before: ReferenceRow | None) -> int | None:
    if before is None:
        return None
    levels = before.bids if side is Side.BID else before.asks
    if not levels:
        return None
    best = levels[0][0]
    raw = (best - msg.price) if side is Side.BID else (msg.price - best)
    return raw // INCREMENT if raw >= 0 else -((-raw) // INCREMENT)


def lifecycles(messages: list[LobsterMessage],
               reference: list[ReferenceRow]) -> LifecycleResult:
    """Fold the message file into one Life per visible order."""
    if len(reference) != len(messages):
        raise ValueError(f"{len(messages)} messages but {len(reference)} "
                         f"reference rows; the files must align 1:1")
    res = LifecycleResult()
    alive: dict[int, Life] = {}
    for i, m in enumerate(messages):
        res.by_type[m.kind.name] += 1
        if MIDAS_START_NS <= m.time_ns < MIDAS_END_NS:
            res.by_type_window[m.kind.name] += 1
        kind = m.kind

        if kind is EventType.ADD:
            side = side_of(m.direction)
            if m.order_id in alive:
                res.readded += 1
            before = reference[i - 1] if i > 0 else None
            life = Life(m.order_id, side, m.price, m.time_ns, m.size,
                        _distance(m, side, before), last_seen_ns=m.time_ns)
            alive[m.order_id] = life
            res.lives.append(life)
            continue

        if kind in (EventType.EXEC, EventType.EXEC_HIDDEN):
            res.exec_lots[lot(m.size)] += 1
        if kind is EventType.EXEC_HIDDEN:
            res.hidden_execs += 1            # id 0: no visible order
            continue
        if kind in (EventType.CROSS, EventType.HALT):
            continue

        life = alive.get(m.order_id)
        if life is None:
            res.dark_ops[kind.name] += 1
            res.dark_shares += m.size
            continue

        life.last_seen_ns = m.time_ns
        rest = life.remaining
        take = min(m.size, rest)
        if m.size > rest or (kind is EventType.DELETE and m.size != rest):
            res.size_disagreements += 1
        if kind is EventType.REDUCE:
            life.cancelled += take
        elif kind is EventType.DELETE:
            life.cancelled += rest             # a delete removes what rests
            take = rest
        elif kind is EventType.EXEC:
            life.executed += take
            life.fills += 1
            if life.first_fill_ns is None:
                life.first_fill_ns = m.time_ns
        if life.remaining == 0:
            life.ended_ns = m.time_ns
            life.end = "executed" if kind is EventType.EXEC else "cancelled"
            del alive[m.order_id]
    return res


# ------------------------------------------------------------ survival
def kaplan_meier(events: list[int], censored: list[int]) -> list[tuple[int, float]]:
    """The product-limit survival curve. `events` are observed
    lifetimes; `censored` are lower bounds (alive at least this long).
    Returns (t, S(t)) at every distinct event time; S is 1 before the
    first. Convention: at a tie, deaths happen before censorings, so a
    censored order at t is still at risk at t."""
    d, c = Counter(events), Counter(censored)
    at_risk = len(events) + len(censored)
    s = 1.0
    out: list[tuple[int, float]] = []
    for t in sorted(set(d) | set(c)):
        di = d.get(t, 0)
        if di:
            s *= 1 - di / at_risk
            out.append((t, s))
        at_risk -= di + c.get(t, 0)
    return out


def survival_at(curve: list[tuple[int, float]], t: int) -> float:
    """Step function: S(t) is the value at the last event time <= t."""
    s = 1.0
    for et, v in curve:
        if et > t:
            break
        s = v
    return s


def km_median(curve: list[tuple[int, float]]) -> int | None:
    """Smallest t with S(t) <= 0.5, or None if the curve never gets there
    (more than half the orders are censored before any such time)."""
    for t, s in curve:
        if s <= 0.5:
            return t
    return None


def quantile(sorted_values: list[int], q: float) -> int | None:
    if not sorted_values:
        return None
    k = min(len(sorted_values) - 1, max(0, int(q * len(sorted_values))))
    return sorted_values[k]


# ------------------------------------------------------------- summary
@dataclass
class Stat:
    section: str
    name: str
    value: object
    definition: str


def _ratio(num, den) -> float | None:
    return num / den if den else None


def summarize(res: LifecycleResult) -> list[Stat]:
    """Every statistic with its definition beside it, so the number can
    never travel alone."""
    lives = res.lives
    n = len(lives)
    out: list[Stat] = []

    def add(section, name, value, definition):
        out.append(Stat(section, name, value, definition))

    # ---- ends
    ends = Counter(l.end for l in lives)
    add("ends", "orders_added", n, "orders whose ADD (type 1) is in the file")
    for kind in ("executed", "cancelled", "censored"):
        add("ends", f"orders_{kind}", ends[kind],
            {"executed": "adds whose last observed shares left by execution",
             "cancelled": "adds whose last observed shares left by cancel or delete",
             "censored": "adds with no observed end: alive at the close OR died "
                         "below the band (indistinguishable in a level-K file)"}[kind])
        add("ends", f"share_{kind}", _ratio(ends[kind], n), f"orders_{kind} / orders_added")
    sh_added = sum(l.size_added for l in lives)
    sh_exec = sum(l.executed for l in lives)
    sh_canc = sum(l.cancelled for l in lives)
    add("ends", "shares_added", sh_added, "type-1 shares")
    add("ends", "shares_executed", sh_exec, "type-4 shares against visible adds")
    add("ends", "shares_cancelled", sh_canc, "type-2 plus type-3 shares against visible adds")
    add("ends", "shares_unresolved", sh_added - sh_exec - sh_canc,
        "added shares with no observed removal (censored remainder)")

    # ---- lifetimes
    observed = sorted(l.lifetime_ns for l in lives if l.lifetime_ns is not None)
    censored = [l.censor_ns for l in lives if l.lifetime_ns is None]
    for kind in ("executed", "cancelled"):
        vals = sorted(l.lifetime_ns for l in lives if l.end == kind)
        for q in (0.10, 0.50, 0.90):
            add("lifetime_naive", f"{kind}_p{int(q * 100)}_ms",
                None if not vals else quantile(vals, q) / 1e6,
                f"quantile of observed add-to-{kind} time, ms; observed deaths only")
    add("lifetime_naive", "observed_p50_ms",
        None if not observed else quantile(observed, 0.5) / 1e6,
        "median observed lifetime, ms, both end kinds; biased short by censoring")
    for label, cap in (("under_100ms", 10**8), ("under_1s", 10**9),
                       ("over_1min", 60 * 10**9)):
        if label.startswith("under"):
            cnt = sum(1 for v in observed if v < cap)
        else:
            cnt = sum(1 for v in observed if v >= cap)
        add("lifetime_naive", f"share_{label}", _ratio(cnt, len(observed)),
            f"share of observed lifetimes {label.replace('_', ' ')}")
    curve = kaplan_meier(observed, censored)
    add("lifetime_km", "censored_share", _ratio(len(censored), n),
        "orders with no observed end / orders added; each enters the KM "
        "estimate as alive at least until its last message")
    med = km_median(curve)
    add("lifetime_km", "median_ms", None if med is None else med / 1e6,
        "Kaplan-Meier median lifetime, ms (smallest t with S(t) <= 0.5)")
    for t in SURVIVAL_GRID_NS:
        add("lifetime_km", f"S_at_{_tlabel(t)}", survival_at(curve, t),
            f"Kaplan-Meier survival at {_tlabel(t)}: share of orders still resting")
        naive = _ratio(sum(1 for v in observed if v > t), len(observed))
        add("lifetime_naive", f"S_at_{_tlabel(t)}", naive,
            f"share of OBSERVED lifetimes longer than {_tlabel(t)}")

    # ---- fill rate by distance at submission
    for bucket in DISTANCE_BUCKETS:
        group = [l for l in lives if distance_bucket(l.distance_cents) == bucket]
        orders = len(group)
        added = sum(l.size_added for l in group)
        execd = sum(l.executed for l in group)
        any_fill = sum(1 for l in group if l.fills)
        add("fill_by_distance", f"{bucket}_orders", orders,
            "adds whose price sat in this bucket relative to the same-side "
            "best just before the add (cents; improve < 0, at_best = 0)")
        add("fill_by_distance", f"{bucket}_share_of_adds", _ratio(orders, n),
            "bucket orders / orders added")
        add("fill_by_distance", f"{bucket}_fill_rate_shares", _ratio(execd, added),
            "shares executed / shares added, this bucket")
        add("fill_by_distance", f"{bucket}_fill_rate_orders", _ratio(any_fill, orders),
            "orders with at least one execution / orders, this bucket")

    # ---- sizes
    add_lots = Counter(lot(l.size_added) for l in lives)
    for k in ("round", "odd", "mixed"):
        add("sizes", f"adds_{k}_lot_share", _ratio(add_lots[k], n),
            {"round": "adds with size a multiple of 100",
             "odd": "adds with size under 100",
             "mixed": "adds over 100 and not a multiple of 100"}[k])
    sizes = sorted(l.size_added for l in lives)
    add("sizes", "add_size_p50", quantile(sizes, 0.5), "median add size, shares")
    add("sizes", "add_size_mean", _ratio(sh_added, n), "mean add size, shares")

    # ---- cancel-to-trade, three definitions
    bt, bw = res.by_type, res.by_type_window
    cancels = bt["REDUCE"] + bt["DELETE"]
    trades = bt["EXEC"] + bt["EXEC_HIDDEN"]
    add("cancel_to_trade", "by_messages_full_day", _ratio(cancels, trades),
        "(type 2 + type 3 messages) / (type 4 + type 5 messages), 9:30 to 16:00")
    cw = bw["REDUCE"] + bw["DELETE"]
    tw = bw["EXEC"] + bw["EXEC_HIDDEN"]
    add("cancel_to_trade", "by_messages_midas_window", _ratio(cw, tw),
        "(type 2 + type 3) / (type 4 + type 5), 9:35 to 16:00, the SEC MIDAS window")
    add("cancel_to_trade", "by_orders", _ratio(ends["cancelled"], ends["executed"]),
        "adds ending cancelled / adds ending executed (censored adds excluded)")
    add("cancel_to_trade", "by_shares", _ratio(sh_canc, sh_exec),
        "shares cancelled / shares executed, visible adds only")
    add("cancel_to_trade", "hidden_share_of_executions",
        _ratio(bt["EXEC_HIDDEN"], trades), "type 5 / (type 4 + type 5)")
    execs = sum(res.exec_lots.values())
    add("cancel_to_trade", "odd_lot_share_of_executions",
        _ratio(res.exec_lots["odd"], execs),
        "executions (types 4 and 5) under 100 shares / all executions")

    # ---- accounting
    add("accounting", "dark_ops", sum(res.dark_ops.values()),
        "operations on ids the file never added (pre-window or below-band), by any type")
    add("accounting", "dark_shares", res.dark_shares, "shares in dark ops")
    add("accounting", "hidden_execs", res.hidden_execs, "type-5 messages (order id 0)")
    add("accounting", "size_disagreements", res.size_disagreements,
        "removals larger than what rested, or deletes naming a different size (the Day 2 counter)")
    add("accounting", "readded_ids", res.readded, "ADDs for an id already alive")
    return out


def _tlabel(ns: int) -> str:
    if ns < 10**9:
        return f"{ns // 10**6}ms"
    if ns < 60 * 10**9:
        return f"{ns // 10**9}s"
    if ns < 3600 * 10**9:
        return f"{ns // (60 * 10**9)}min"
    return f"{ns // (3600 * 10**9)}h"
