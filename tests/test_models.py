"""Tests for the defensive parsers — the parts most likely to break on real data."""

from __future__ import annotations

from datetime import datetime, timezone

from polybot.models import Market, OrderBook


def test_market_parses_stringified_json_fields():
    raw = {
        "conditionId": "0xabc",
        "questionID": "0xq",
        "slug": "will-x-happen",
        "question": "Will X happen?",
        "description": "Resolves YES if X.",
        # Gamma sends these as JSON *strings*, not arrays:
        "outcomes": '["Yes", "No"]',
        "clobTokenIds": '["111", "222"]',
        "outcomePrices": '["0.62", "0.38"]',
        "endDate": "2026-07-31T12:00:00Z",
        "liquidityNum": 21031.33,
        "volume24hr": 7582.3,
        "volumeNum": 805958.75,
        "enableOrderBook": True,
        "orderPriceMinTickSize": 0.01,
        "orderMinSize": 5,
        "negRisk": False,
    }
    m = Market.from_gamma(raw)
    assert m.condition_id == "0xabc"
    assert [t.token_id for t in m.tokens] == ["111", "222"]
    assert [t.outcome for t in m.tokens] == ["Yes", "No"]
    assert m.outcome_prices == [0.62, 0.38]
    assert m.yes_token is not None and m.yes_token.outcome == "Yes"
    assert m.liquidity == 21031.33
    assert m.enable_order_book is True


def test_market_handles_missing_and_empty_fields():
    m = Market.from_gamma({"conditionId": "0x0"})
    assert m.tokens == []
    assert m.yes_token is None
    assert m.liquidity == 0.0
    assert m.end_date is None
    assert m.days_to_resolution() is None


def test_days_to_resolution_is_positive_for_future():
    raw = {"conditionId": "x", "endDate": "2026-12-31T00:00:00Z"}
    m = Market.from_gamma(raw)
    now = datetime(2026, 6, 1, tzinfo=timezone.utc)
    days = m.days_to_resolution(now)
    assert days is not None and 200 < days < 220


def test_orderbook_normalises_best_levels_regardless_of_order():
    raw = {
        "asset_id": "111",
        "market": "0xabc",
        "timestamp": "1700000000000",
        "tick_size": "0.01",
        "neg_risk": False,
        "last_trade_price": "0.50",
        # Intentionally unsorted input:
        "bids": [{"price": "0.48", "size": "10"}, {"price": "0.50", "size": "5"}],
        "asks": [{"price": "0.53", "size": "7"}, {"price": "0.51", "size": "3"}],
    }
    ob = OrderBook.from_clob(raw)
    assert ob.best_bid.price == 0.50  # highest bid
    assert ob.best_ask.price == 0.51  # lowest ask
    assert ob.midpoint == 0.505
    assert abs(ob.spread - 0.01) < 1e-9
    assert ob.last_trade_price == 0.50


def test_orderbook_empty_book_has_no_touch():
    ob = OrderBook.from_clob({"asset_id": "1", "market": "m", "bids": [], "asks": []})
    assert ob.best_bid is None
    assert ob.midpoint is None
    assert ob.spread is None
