"""Backtest engine: replay ticks -> strategy -> fills -> PnL."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from polybot.backtest.fills import FillModel
from polybot.backtest.portfolio import Portfolio
from polybot.backtest.strategy import Strategy
from polybot.backtest.types import Fill, Tick


@dataclass
class BacktestResult:
    capital: float
    final_equity: float
    realized: float
    unrealized: float
    settled: bool
    fills: list[Fill]
    equity_curve: list[tuple[int, float]]  # (ts_ms, equity)

    @property
    def pnl(self) -> float:
        return self.final_equity - self.capital

    @property
    def return_pct(self) -> float:
        return 100.0 * self.pnl / self.capital if self.capital else 0.0

    @property
    def max_drawdown_pct(self) -> float:
        peak = -float("inf")
        mdd = 0.0
        for _, eq in self.equity_curve:
            peak = max(peak, eq)
            if peak > 0:
                mdd = max(mdd, (peak - eq) / peak)
        return 100.0 * mdd

    def summary(self) -> str:
        lines = [
            f"  capital deployed : {self.capital:,.2f}",
            f"  final equity     : {self.final_equity:,.2f}",
            f"  net PnL          : {self.pnl:+,.2f}  ({self.return_pct:+.2f}%)",
            f"    realized       : {self.realized:+,.2f}",
            f"    unrealized     : {self.unrealized:+,.2f}",
            f"  settled at resol.: {self.settled}",
            f"  fills            : {len(self.fills)}",
            f"  max drawdown     : {self.max_drawdown_pct:.2f}%",
        ]
        return "\n".join(lines)


class Backtester:
    def __init__(self, capital: float, fill_model: FillModel | None = None):
        self.capital = capital
        self.fill_model = fill_model or FillModel()

    def run(
        self,
        ticks: Iterable[Tick],
        strategy: Strategy,
        resolution: dict[str, float] | None = None,
    ) -> BacktestResult:
        pf = Portfolio(self.capital)
        last_mid: dict[str, float | None] = {}
        curve: list[tuple[int, float]] = []

        for tick in ticks:  # MUST be chronological
            if tick.midpoint is not None:
                last_mid[tick.asset_id] = tick.midpoint

            order = strategy.on_tick(tick, pf)
            if order is not None and order.size > 0:
                fill = self.fill_model.simulate(order, tick)
                if fill is not None:
                    pf.apply_fill(fill)

            curve.append((tick.ts_ms, pf.equity(last_mid)))

        unrealized = pf.unrealized(last_mid)

        settled = False
        if resolution:
            pf.settle(resolution)
            settled = True
            unrealized = 0.0

        final_equity = pf.equity(resolution if settled else last_mid)
        return BacktestResult(
            capital=self.capital,
            final_equity=final_equity,
            realized=pf.realized(),
            unrealized=unrealized,
            settled=settled,
            fills=pf.fills,
            equity_curve=curve,
        )
