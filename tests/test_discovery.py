"""Tests for filtering and scoring logic (pure functions, no network)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from polybot.config import DiscoveryConfig, Filters
from polybot.discovery import NicheScreener, match_themes, passes_filters
from polybot.models import BookLevel, Market, OrderBook

NOW = datetime(2026, 6, 1, tzinfo=timezone.utc)


def _market(**over) -> Market:
    base = {
        "conditionId": "0x1",
        "question": "Will Petro do X in Bogota?",
        "description": "About Colombia.",
        "outcomes": '["Yes","No"]',
        "clobTokenIds": '["111","222"]',
        "outcomePrices": '["0.5","0.5"]',
        "endDate": (NOW + timedelta(days=30)).isoformat(),
        "liquidityNum": 5000.0,
        "volume24hr": 1000.0,
        "enableOrderBook": True,
    }
    base.update(over)
    return Market.from_gamma(base)


def _cfg(**filter_over) -> DiscoveryConfig:
    filters = {
        "require_order_book": True,
        "min_liquidity": 2000.0,
        "max_days_to_resolution": 120,
        "min_days_to_resolution": 1,
        "min_volume_24h": 50.0,
        "max_volume_24h": 75000.0,
    }
    filters.update(filter_over)
    return DiscoveryConfig(
        scan_limit=100,
        page_size=50,
        filters=Filters(**filters),
        weights={
            "theme_match": 3.0,
            "spread": 2.5,
            "liquidity": 1.0,
            "low_attention": 2.0,
            "time_decay": 1.0,
        },
        themes={"colombia": ["colombia*", "petro", "bogot*"], "latam": ["milei"]},
    )


def test_theme_matching_is_case_insensitive_and_multi():
    m = _market(question="PETRO vs Milei", description="bogota latam")
    themes = match_themes(m, _cfg().themes)
    assert set(themes) == {"colombia", "latam"}


def test_theme_matching_whole_word_avoids_false_positives():
    # The real bug we hit: 'petro' must not match 'petroleum'.
    oil = _market(question="Iranian oil sanctions?", description="petroleum exports")
    assert match_themes(oil, _cfg().themes) == []


def test_theme_prefix_stem_matches_nationality_forms():
    cfg = _cfg()
    cfg.themes["latam"] = ["peru*"]
    peruvian = _market(question="2026 Peruvian presidential election", description="")
    assert "latam" in match_themes(peruvian, cfg.themes)


def test_filters_reject_illiquid_market():
    assert not passes_filters(_market(liquidityNum=100.0), _cfg().filters, NOW)


def test_filters_reject_bot_saturated_volume():
    assert not passes_filters(_market(volume24hr=500_000.0), _cfg().filters, NOW)


def test_filters_reject_far_resolution():
    far = _market(endDate=(NOW + timedelta(days=400)).isoformat())
    assert not passes_filters(far, _cfg().filters, NOW)


def test_filters_accept_good_market():
    assert passes_filters(_market(), _cfg().filters, NOW)


def test_scoring_rewards_theme_and_wide_spread():
    screener = NicheScreener(_cfg())
    themed = _market()
    plain = _market(question="Will it rain in Ohio?", description="weather")

    s_themed = screener.score_metadata(themed, match_themes(themed, _cfg().themes), NOW)
    s_plain = screener.score_metadata(plain, match_themes(plain, _cfg().themes), NOW)
    assert s_themed.score > s_plain.score  # theme match dominates

    # Applying a wide live spread should raise the score further.
    before = s_themed.score
    wide = OrderBook(
        asset_id="111", condition_id="0x1",
        bids=[BookLevel(0.45, 100)], asks=[BookLevel(0.55, 100)],
        timestamp_ms=0, tick_size=0.01, neg_risk=False, last_trade_price=None,
    )
    screener.apply_book(s_themed, wide)
    assert s_themed.score > before
    assert s_themed.spread is not None and abs(s_themed.spread - 0.10) < 1e-9


def test_screen_metadata_sorts_and_filters():
    screener = NicheScreener(_cfg())
    good = _market()
    illiquid = _market(conditionId="0x2", liquidityNum=10.0)
    ranked = screener.screen_metadata([illiquid, good], NOW)
    assert len(ranked) == 1
    assert ranked[0].market.condition_id == "0x1"
