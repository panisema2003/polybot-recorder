"""Adapt historical price points into backtestable ticks.

Polymarket's price history has no spread or depth, so to run it through the
same engine we *synthesise* a book around each price: bid = price - half_spread,
ask = price + half_spread, with an assumed (large) depth.

This is an OPTIMISTIC first-pass: the assumed spread is a guess and the assumed
depth ignores that you can't always trade size. Treat results as an upper bound
and confirm against execution-realistic backtests on live-recorded books.
"""

from __future__ import annotations

from collections.abc import Iterable

from polybot.backtest.types import Tick
from polybot.models import PricePoint


def synthetic_ticks(
    points: Iterable[PricePoint],
    asset_id: str,
    assumed_spread: float = 0.04,
    depth: float = 100_000.0,
) -> list[Tick]:
    """Build ticks from price points, wrapping each price in an assumed spread.

    Prices are clamped to [0, 1] (probabilities). ``assumed_spread`` is the full
    bid-ask width; half is applied on each side.
    """
    half = assumed_spread / 2.0
    ticks: list[Tick] = []
    for pp in points:
        bid = max(0.0, min(1.0, pp.price - half))
        ask = max(0.0, min(1.0, pp.price + half))
        ticks.append(
            Tick(
                asset_id=asset_id,
                ts_ms=pp.ts_ms,
                best_bid=bid,
                best_bid_sz=depth,
                best_ask=ask,
                best_ask_sz=depth,
            )
        )
    return ticks
