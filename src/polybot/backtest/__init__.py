"""Phase 2 backtest harness.

Replays recorded top-of-book data and runs a strategy against it with a
realistic fill model, so an edge can be *measured* before any real capital is
risked. Still no live trading here — this consumes the SQLite captures produced
by the recorder.

Public API:
    from polybot.backtest import Backtester, FairValueStrategy, FillModel, Tick
"""

from polybot.backtest.engine import Backtester, BacktestResult
from polybot.backtest.fills import FillModel
from polybot.backtest.history import synthetic_ticks
from polybot.backtest.portfolio import Portfolio, Position
from polybot.backtest.strategy import FairValueStrategy, Strategy
from polybot.backtest.types import Fill, Order, Side, Tick

__all__ = [
    "Backtester",
    "BacktestResult",
    "FillModel",
    "Portfolio",
    "Position",
    "Strategy",
    "FairValueStrategy",
    "synthetic_ticks",
    "Tick",
    "Order",
    "Fill",
    "Side",
]
