"""Position & portfolio accounting (average-cost method).

Shares are signed: positive = long the outcome (each share pays $1 if it
resolves YES, $0 otherwise), negative = short. The average-cost model handles
adding to, reducing, and flipping a position correctly, including realised PnL
when a position is reduced or crosses zero.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from polybot.backtest.types import Fill, Side

_EPS = 1e-9


@dataclass
class Position:
    asset_id: str
    shares: float = 0.0
    avg_price: float = 0.0  # average cost of the currently open shares
    realized: float = 0.0

    def apply(self, signed_qty: float, price: float) -> float:
        """Apply a signed quantity (+buy / -sell) at ``price``.

        Returns the realised PnL produced by this trade (non-zero only when it
        reduces or flips the position).
        """
        realized = 0.0
        opening_same_dir = self.shares == 0 or (self.shares > 0) == (signed_qty > 0)

        if opening_same_dir:
            new_shares = self.shares + signed_qty
            if abs(new_shares) > _EPS:
                self.avg_price = (
                    self.shares * self.avg_price + signed_qty * price
                ) / new_shares
            self.shares = new_shares
        else:
            closing = min(abs(signed_qty), abs(self.shares))
            if self.shares > 0:  # selling to close a long
                realized = closing * (price - self.avg_price)
            else:  # buying to close a short
                realized = closing * (self.avg_price - price)

            self.shares += signed_qty
            if abs(self.shares) < _EPS:  # fully closed
                self.shares = 0.0
                self.avg_price = 0.0
            elif (self.shares > 0) == (signed_qty > 0):  # flipped through zero
                self.avg_price = price

        self.realized += realized
        return realized

    def unrealized(self, mark: float | None) -> float:
        if mark is None or self.shares == 0:
            return 0.0
        return self.shares * (mark - self.avg_price)


@dataclass
class Portfolio:
    """Cash + per-asset positions. Cash starts at the deployed capital."""

    capital: float
    cash: float = field(init=False)
    positions: dict[str, Position] = field(default_factory=dict)
    fills: list[Fill] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.cash = self.capital

    def position(self, asset_id: str) -> Position:
        return self.positions.setdefault(asset_id, Position(asset_id))

    def apply_fill(self, fill: Fill) -> None:
        signed = fill.side.sign * fill.size
        self.position(fill.asset_id).apply(signed, fill.price)
        # Buying spends cash; selling returns it. Fees always cost cash.
        self.cash -= signed * fill.price
        self.cash -= fill.fee
        self.fills.append(fill)

    def realized(self) -> float:
        return sum(p.realized for p in self.positions.values())

    def unrealized(self, marks: dict[str, float | None]) -> float:
        return sum(p.unrealized(marks.get(p.asset_id)) for p in self.positions.values())

    def equity(self, marks: dict[str, float | None]) -> float:
        """Cash plus mark-to-market value of open positions."""
        pos_value = sum(
            p.shares * (marks.get(p.asset_id) or 0.0) for p in self.positions.values()
        )
        return self.cash + pos_value

    def settle(self, resolution: dict[str, float]) -> None:
        """Resolve positions to terminal prices (1.0 = YES won, 0.0 = lost).

        Booked as a closing trade so realised PnL captures the full outcome.
        """
        for asset_id, price in resolution.items():
            pos = self.position(asset_id)
            if pos.shares != 0:
                self.apply_fill(
                    Fill(
                        ts_ms=0,
                        asset_id=asset_id,
                        side=(Side.SELL if pos.shares > 0 else Side.BUY),
                        size=abs(pos.shares),
                        price=price,
                        fee=0.0,
                        reason="settlement",
                    )
                )
