"""Tests for basket files and the resolution-horizon guard."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from polybot.basket import read_basket
from polybot.discovery import filter_min_days_to_resolution
from polybot.models import Market

NOW = datetime(2026, 6, 6, tzinfo=timezone.utc)


def test_read_basket_skips_comments_blanks_and_dedupes(tmp_path):
    f = tmp_path / "b.txt"
    f.write_text(
        "\n".join(
            [
                "# a comment",
                "",
                "   ",
                "slug-one",
                "slug-two   # inline note",
                "slug-one",  # duplicate
                "  slug-three  ",
            ]
        ),
        encoding="utf-8",
    )
    assert read_basket(f) == ["slug-one", "slug-two", "slug-three"]


def _market(slug: str, days: float | None) -> Market:
    raw = {"conditionId": slug, "slug": slug, "question": slug}
    if days is not None:
        raw["endDate"] = (NOW + timedelta(days=days)).isoformat()
    return Market.from_gamma(raw)


def test_guard_keeps_far_skips_near_and_unknown():
    markets = [
        _market("far", 20),
        _market("near", 3),
        _market("edge", 14),
        _market("unknown", None),
    ]
    keep, skip = filter_min_days_to_resolution(markets, 14, NOW)
    kept = {m.slug for m in keep}
    skipped = {m.slug for m in skip}
    assert kept == {"far", "edge"}            # >= 14 days kept
    assert skipped == {"near", "unknown"}     # too soon / no date skipped


def test_guard_disabled_keeps_everything():
    markets = [_market("a", 1), _market("b", None)]
    keep, skip = filter_min_days_to_resolution(markets, 0, NOW)
    assert len(keep) == 2 and skip == []
