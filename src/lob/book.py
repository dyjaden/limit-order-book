"""The limit order book: price-time priority, enforced by construction.

This is a RECONSTRUCTION book, not a matching engine. It applies an
exchange's message log -- add, cancel, execute, replace -- and holds the
state those messages imply. It never matches orders itself: a real feed
never delivers a crossing add, because the exchange would have matched it
before publishing, so a crossing add here is refused loudly. Feed
corruption becomes noise you hear instead of state you silently corrupt.

Design laws, stated once and enforced below:

- Prices are integer TICKS. Float money is how backtests lie by fractions
  of a cent; the tick size is display metadata and never arithmetic.
- Within a price level, time priority is a FIFO queue. Cancel from the
  middle preserves the order of everyone else.
- ``replace`` is cancel-plus-add, so an amended order LOSES its queue
  position structurally -- the rule cannot be forgotten, because it is not
  remembered anywhere; it falls out of the shape of the code. (Nasdaq's
  'U' message has exactly these semantics; in-place size reductions that
  keep priority are a different message and arrive with the feed parsers.)
- The book holds no timestamps and no strategy state. It is mechanism,
  the way the exchange's own engine is; everything analytical lives
  outside.
- Failures are loud. Duplicate ids, unknown ids, zero quantities, crossing
  adds, and executes that exceed available liquidity all raise. Silence is
  never an answer.

Simplicity is deliberate: levels live in a dict and the best price is
found by scanning its keys, which is O(levels) per query. Correctness is
this week's product; the throughput baseline is Week 3's row and the fast
twin is Week 8's. Nothing here is clever, so everything here is checkable.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from enum import Enum


class Side(Enum):
    BID = "bid"
    ASK = "ask"


Price = int      # integer ticks -- see the module docstring
Qty = int
OrderId = int


@dataclass
class Order:
    """One resting order: identity, side, price, remaining quantity.

    Deliberately nothing else -- no timestamps, no owner, no flags. Queue
    position is not stored on the order because it is a property of the
    level's FIFO, and storing it twice is how the two copies drift.
    """
    order_id: OrderId
    side: Side
    price: Price
    qty: Qty


@dataclass(frozen=True)
class Fill:
    """One execution against one resting order, at that order's price."""
    order_id: OrderId
    price: Price
    qty: Qty


class PriceLevel:
    """A FIFO queue of orders at one price. First in, first filled."""

    def __init__(self, price: Price) -> None:
        self.price = price
        self.queue: deque[Order] = deque()
        self.total_qty: Qty = 0      # maintained incrementally, never summed

    def append(self, order: Order) -> None:
        """Join the BACK of the line."""
        self.queue.append(order)
        self.total_qty += order.qty

    def remove(self, order: Order) -> None:
        """Leave the line from wherever you stand; everyone else keeps
        their place."""
        self.queue.remove(order)
        self.total_qty -= order.qty

    def __len__(self) -> int:
        return len(self.queue)


class Book:
    """Two sides of price levels plus an O(1) id index.

    The id index exists because most of a real feed is cancellations --
    order-to-trade ratios in US equities run well past ten to one -- and a
    book that scans for every cancel would spend its life searching.
    """

    def __init__(self) -> None:
        self._levels: dict[Side, dict[Price, PriceLevel]] = {
            Side.BID: {}, Side.ASK: {}}
        self._by_id: dict[OrderId, Order] = {}

    # ------------------------------------------------------------ queries
    def best_bid(self) -> Price | None:
        """Highest price anyone will pay; None on an empty side, never a
        fake zero."""
        bids = self._levels[Side.BID]
        return max(bids) if bids else None

    def best_ask(self) -> Price | None:
        """Lowest price anyone will sell at; None on an empty side."""
        asks = self._levels[Side.ASK]
        return min(asks) if asks else None

    def spread(self) -> Price | None:
        """Ask minus bid, in ticks. None unless BOTH sides are alive --
        a one-sided market has no spread, and pretending otherwise is a
        lie downstream statistics would happily average."""
        bid, ask = self.best_bid(), self.best_ask()
        if bid is None or ask is None:
            return None
        return ask - bid

    def depth(self, side: Side, levels: int = 1) -> list[tuple[Price, Qty]]:
        """(price, total quantity) for the best `levels` levels, best
        first."""
        best_first = sorted(self._levels[side],
                            reverse=(side is Side.BID))[:levels]
        return [(p, self._levels[side][p].total_qty) for p in best_first]

    def order(self, order_id: OrderId) -> Order:
        """The resting order behind an id, loudly."""
        if order_id not in self._by_id:
            raise KeyError(f"unknown order id {order_id}")
        return self._by_id[order_id]

    def __len__(self) -> int:
        return len(self._by_id)

    # --------------------------------------------------------- operations
    def add(self, order_id: OrderId, side: Side, price: Price,
            qty: Qty) -> Order:
        """A new order joins the BACK of the queue at its price.

        A crossing add (a bid at or through the best ask, or vice versa)
        is refused: this book replays an exchange's log, and the exchange
        would have matched that order before ever publishing it. Seeing
        one means the feed, the parser, or the caller is wrong -- which is
        exactly the moment to be loud.
        """
        if qty <= 0:
            raise ValueError(f"order {order_id}: quantity must be positive, "
                             f"got {qty}")
        if order_id in self._by_id:
            raise ValueError(f"duplicate order id {order_id}")
        if side is Side.BID:
            ask = self.best_ask()
            if ask is not None and price >= ask:
                raise ValueError(f"bid {order_id} at {price} crosses the "
                                 f"ask {ask}: a real feed never delivers "
                                 f"this")
        else:
            bid = self.best_bid()
            if bid is not None and price <= bid:
                raise ValueError(f"ask {order_id} at {price} crosses the "
                                 f"bid {bid}: a real feed never delivers "
                                 f"this")

        order = Order(order_id=order_id, side=side, price=price, qty=qty)
        level = self._levels[side].setdefault(price, PriceLevel(price))
        level.append(order)
        self._by_id[order_id] = order
        return order

    def cancel(self, order_id: OrderId) -> Order:
        """An order leaves its queue -- from the middle, usually, because
        most orders die unexecuted. An emptied level is deleted so the
        best price moves correctly."""
        order = self.order(order_id)
        level = self._levels[order.side][order.price]
        level.remove(order)
        if not level.queue:
            del self._levels[order.side][order.price]
        del self._by_id[order_id]
        return order

    def replace(self, order_id: OrderId, new_price: Price,
                new_qty: Qty) -> Order:
        """Amend an order: cancel it, add it back. It rejoins at the BACK
        of its (possibly new) level -- queue position is lost by
        construction, which is the price-time rule working as law rather
        than as something an implementation remembered to do."""
        old = self.cancel(order_id)
        try:
            return self.add(order_id, old.side, new_price, new_qty)
        except ValueError:
            # the replacement was invalid (crossing, bad qty); the cancel
            # half must not silently stand alone
            self.add(old.order_id, old.side, old.price, old.qty)
            raise

    def execute(self, side: Side, qty: Qty) -> list[Fill]:
        """Consume `qty` from the FRONT of `side`'s best queue, walking to
        the next level when one empties -- price priority across levels,
        time priority within them. Each fill happens at the RESTING
        order's price, because that is who was there first.

        Demanding more than the side holds raises: an aggregate execute
        that exceeds visible liquidity is feed corruption or caller error,
        and a silent partial would launder it into plausible state.
        """
        if qty <= 0:
            raise ValueError(f"execute quantity must be positive, got {qty}")
        available = sum(lv.total_qty for lv in self._levels[side].values())
        if qty > available:
            raise ValueError(f"execute of {qty} exceeds available "
                             f"{available} on {side.value} side")

        fills: list[Fill] = []
        remaining = qty
        while remaining > 0:
            prices = self._levels[side]
            best = max(prices) if side is Side.BID else min(prices)
            level = prices[best]
            head = level.queue[0]
            take = min(head.qty, remaining)
            fills.append(Fill(order_id=head.order_id, price=best, qty=take))
            head.qty -= take
            level.total_qty -= take
            remaining -= take
            if head.qty == 0:
                level.queue.popleft()
                del self._by_id[head.order_id]
            if not level.queue:
                del prices[best]
        return fills
