"""Fill model: turn an intended order into a realistic fill against a tick.

Conservative by construction — we only model the *top of book* (which is all
``book_top`` stores), so:
  - a BUY lifts the best ask; a SELL hits the best bid (taker, crosses spread);
  - fill size is capped at the displayed size at the touch (no walking deeper
    levels and no assuming infinite depth);
  - a configurable fee (in bps of notional) is charged. Polymarket's standard
    fee is 0, but Kalshi-style fees can be modelled by setting ``fee_bps``.

This deliberately *under*-fills rather than flattering the strategy: if the
edge survives top-of-book taker execution, it's real.
"""

from __future__ import annotations

from dataclasses import dataclass

from polybot.backtest.types import Fill, Order, Side, Tick


@dataclass(frozen=True)
class FillModel:
    fee_bps: float = 0.0          # fee as basis points of notional (Polymarket=0)
    max_touch_fraction: float = 1.0  # only take this fraction of displayed size

    def simulate(self, order: Order, tick: Tick) -> Fill | None:
        if order.size <= 0 or tick.asset_id != order.asset_id:
            return None

        if order.side is Side.BUY:
            price, avail = tick.best_ask, tick.best_ask_sz
        else:
            price, avail = tick.best_bid, tick.best_bid_sz

        if price is None or avail is None or avail <= 0:
            return None  # no liquidity on that side at this tick

        size = min(order.size, avail * self.max_touch_fraction)
        if size <= 0:
            return None

        fee = size * price * (self.fee_bps / 10_000.0)
        return Fill(
            ts_ms=tick.ts_ms,
            asset_id=order.asset_id,
            side=order.side,
            size=size,
            price=price,
            fee=fee,
            reason=order.reason,
        )
