"""Core value types for the backtester."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Side(str, Enum):
    BUY = "BUY"
    SELL = "SELL"

    @property
    def sign(self) -> int:
        return 1 if self is Side.BUY else -1


@dataclass(frozen=True)
class Tick:
    """One top-of-book observation of a single asset (one row of book_top)."""

    asset_id: str
    ts_ms: int
    best_bid: float | None
    best_bid_sz: float | None
    best_ask: float | None
    best_ask_sz: float | None

    @property
    def midpoint(self) -> float | None:
        if self.best_bid is not None and self.best_ask is not None:
            return (self.best_bid + self.best_ask) / 2.0
        return None


@dataclass(frozen=True)
class Order:
    """A taker (marketable) order the strategy wants to place on this tick."""

    asset_id: str
    side: Side
    size: float  # shares / contracts
    reason: str = ""


@dataclass(frozen=True)
class Fill:
    ts_ms: int
    asset_id: str
    side: Side
    size: float
    price: float
    fee: float
    reason: str = ""

    @property
    def notional(self) -> float:
        return self.size * self.price
