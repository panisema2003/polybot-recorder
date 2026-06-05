"""Strategy interface + a worked example.

A strategy sees one tick at a time plus the current portfolio, and may return a
single taker order (or None). This is intentionally minimal — the point of
Phase 1/2 is to test *one* edge cleanly, not to build a framework zoo.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from polybot.backtest.portfolio import Portfolio
from polybot.backtest.types import Order, Side, Tick


class Strategy(ABC):
    @abstractmethod
    def on_tick(self, tick: Tick, portfolio: Portfolio) -> Order | None:
        """Decide what to do on this tick. Return an Order or None."""


@dataclass
class FairValueStrategy(Strategy):
    """Buy the outcome when the market underprices *your* fair probability,
    sell when it overprices it. This is the canonical 'I have a better model
    than the crowd' edge — the whole reason to fish in niche markets.

    Parameters
    ----------
    asset_id    : the token this strategy trades (one outcome of one market).
    fair        : your estimated true probability (0..1) for that outcome.
    edge        : minimum mispricing (in price points) required to act, i.e. a
                  trade only fires if ask < fair-edge (buy) or bid > fair+edge.
    order_size  : shares per order.
    max_position: absolute cap on net shares held.
    """

    asset_id: str
    fair: float
    edge: float = 0.02
    order_size: float = 50.0
    max_position: float = 200.0

    def on_tick(self, tick: Tick, portfolio: Portfolio) -> Order | None:
        if tick.asset_id != self.asset_id:
            return None
        shares = portfolio.position(self.asset_id).shares

        # Cheap vs our fair -> accumulate (buy), if we have room to go longer.
        if (
            tick.best_ask is not None
            and tick.best_ask < self.fair - self.edge
            and shares < self.max_position
        ):
            size = min(self.order_size, self.max_position - shares)
            return Order(self.asset_id, Side.BUY, size, reason="ask<fair-edge")

        # Rich vs our fair -> lighten / short, if we have room to go shorter.
        if (
            tick.best_bid is not None
            and tick.best_bid > self.fair + self.edge
            and shares > -self.max_position
        ):
            size = min(self.order_size, self.max_position + shares)
            return Order(self.asset_id, Side.SELL, size, reason="bid>fair+edge")

        return None
