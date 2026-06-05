"""Tests for the backtest harness — correctness of money math is non-negotiable."""

from __future__ import annotations

import math

from polybot.backtest import (
    Backtester,
    FairValueStrategy,
    FillModel,
    Order,
    Portfolio,
    Position,
    Side,
    Tick,
)


# --- Position / average-cost accounting -------------------------------------

def test_position_long_then_settle_profit():
    p = Position("a")
    p.apply(+100, 0.40)            # buy 100 @ 0.40
    assert p.shares == 100 and math.isclose(p.avg_price, 0.40)
    realized = p.apply(-100, 1.00)  # settle at 1.0 (YES won)
    assert math.isclose(realized, 60.0)  # 100 * (1.0 - 0.40)
    assert p.shares == 0


def test_position_partial_close_realizes_proportionally():
    p = Position("a")
    p.apply(+100, 0.50)
    realized = p.apply(-40, 0.60)   # sell 40 @ 0.60
    assert math.isclose(realized, 40 * 0.10)
    assert p.shares == 60
    assert math.isclose(p.avg_price, 0.50)  # avg unchanged on a reduction


def test_position_averages_up_on_add():
    p = Position("a")
    p.apply(+100, 0.40)
    p.apply(+100, 0.60)
    assert p.shares == 200
    assert math.isclose(p.avg_price, 0.50)


def test_position_flip_through_zero():
    p = Position("a")
    p.apply(+50, 0.40)
    realized = p.apply(-80, 0.50)   # close 50 long, open 30 short
    assert math.isclose(realized, 50 * 0.10)
    assert math.isclose(p.shares, -30)
    assert math.isclose(p.avg_price, 0.50)  # new short basis at trade price


# --- Portfolio cash accounting ---------------------------------------------

def test_portfolio_cash_and_settlement():
    from polybot.backtest.types import Fill

    pf = Portfolio(capital=1000.0)
    pf.apply_fill(Fill(0, "a", Side.BUY, 100, 0.40, fee=0.0))
    assert math.isclose(pf.cash, 1000.0 - 40.0)         # spent 40
    assert math.isclose(pf.equity({"a": 0.40}), 1000.0)  # mark flat => no PnL yet

    pf.settle({"a": 1.0})
    assert math.isclose(pf.equity({"a": 1.0}), 1060.0)   # +60 realised
    assert math.isclose(pf.realized(), 60.0)


def test_fee_reduces_cash():
    from polybot.backtest.types import Fill

    pf = Portfolio(capital=100.0)
    pf.apply_fill(Fill(0, "a", Side.BUY, 10, 0.50, fee=0.25))
    assert math.isclose(pf.cash, 100.0 - 5.0 - 0.25)


# --- Fill model -------------------------------------------------------------

def test_fill_caps_at_displayed_size():
    fm = FillModel()
    tick = Tick("a", 1, best_bid=0.49, best_bid_sz=10, best_ask=0.51, best_ask_sz=3)
    fill = fm.simulate(Order("a", Side.BUY, 50), tick)
    assert fill is not None
    assert fill.size == 3 and fill.price == 0.51  # lifted the ask, capped at 3


def test_fill_none_without_liquidity():
    fm = FillModel()
    tick = Tick("a", 1, best_bid=None, best_bid_sz=None, best_ask=None, best_ask_sz=None)
    assert fm.simulate(Order("a", Side.BUY, 10), tick) is None


def test_fill_fee_bps_applied():
    fm = FillModel(fee_bps=100.0)  # 1%
    tick = Tick("a", 1, best_bid=0.49, best_bid_sz=10, best_ask=0.50, best_ask_sz=10)
    fill = fm.simulate(Order("a", Side.BUY, 10), tick)
    assert math.isclose(fill.fee, 10 * 0.50 * 0.01)


# --- End-to-end engine ------------------------------------------------------

def _tick(ts, bid, ask, sz=1000):
    return Tick("a", ts, best_bid=bid, best_bid_sz=sz, best_ask=ask, best_ask_sz=sz)


def test_engine_buys_underpriced_and_profits_on_resolution():
    # Market trades ~0.30 but our fair is 0.70 -> strategy should buy, and if it
    # resolves YES (1.0) we should make money.
    ticks = [_tick(1, 0.29, 0.31), _tick(2, 0.30, 0.32), _tick(3, 0.30, 0.31)]
    strat = FairValueStrategy("a", fair=0.70, edge=0.02, order_size=50, max_position=100)
    bt = Backtester(capital=1000.0, fill_model=FillModel())
    res = bt.run(ticks, strat, resolution={"a": 1.0})

    assert res.settled is True
    assert len(res.fills) >= 2          # bought on multiple ticks
    assert res.pnl > 0                  # profited
    # Bought 100 shares around ~0.31, settled at 1.0 -> ~ +69 gross.
    assert 60 < res.pnl < 75


def test_engine_no_trade_when_fairly_priced():
    ticks = [_tick(1, 0.49, 0.51), _tick(2, 0.49, 0.51)]
    strat = FairValueStrategy("a", fair=0.50, edge=0.02)
    res = Backtester(1000.0).run(ticks, strat)
    assert len(res.fills) == 0
    assert math.isclose(res.final_equity, 1000.0)


def test_engine_loss_when_resolves_against():
    ticks = [_tick(1, 0.29, 0.31)]
    strat = FairValueStrategy("a", fair=0.70, edge=0.02, order_size=50, max_position=50)
    res = Backtester(1000.0).run(ticks, strat, resolution={"a": 0.0})
    assert res.pnl < 0  # bought ~0.31, resolved 0 -> loss


# --- History adapter --------------------------------------------------------

def test_synthetic_ticks_wraps_price_in_assumed_spread():
    from polybot.backtest import synthetic_ticks
    from polybot.models import PricePoint

    pts = [PricePoint(1000, 0.50), PricePoint(2000, 0.60)]
    ticks = synthetic_ticks(pts, "a", assumed_spread=0.04, depth=1234)
    assert len(ticks) == 2
    assert math.isclose(ticks[0].best_bid, 0.48) and math.isclose(ticks[0].best_ask, 0.52)
    assert math.isclose(ticks[0].midpoint, 0.50)
    assert ticks[0].best_ask_sz == 1234


def test_synthetic_ticks_clamp_to_probability_range():
    from polybot.backtest import synthetic_ticks
    from polybot.models import PricePoint

    ticks = synthetic_ticks([PricePoint(1, 0.99)], "a", assumed_spread=0.10)
    assert 0.0 <= ticks[0].best_bid <= 1.0
    assert ticks[0].best_ask == 1.0  # clamped, not 1.04
