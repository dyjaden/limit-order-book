"""Limit order book reconstruction and short-horizon prediction.

A market is a state machine and the feed is its event log: this package
folds exchange messages -- add, cancel, execute, replace -- into the book
state they imply, exactly and verifiably. The companion project
(event-driven-backtester) replayed one event per day; this one replays one
event per message, and earns its claims the same way: against references,
with the trial count attached.
"""

__version__ = "0.1.0"
