"""Tests for the dashboard data layer and rendering (no web server needed)."""

from __future__ import annotations

import pytest

from polybot.dashboard.data import latest_tops, mid_series
from polybot.dashboard.render import page_html, sparkline_svg
from polybot.models import Market, OrderBook
from polybot.storage import Storage


def _populated_db(tmp_path):
    db = tmp_path / "t.db"
    st = Storage(db)
    market = Market.from_gamma(
        {
            "conditionId": "COND",
            "slug": "test-slug",
            "question": "Test question?",
            "outcomes": '["Yes","No"]',
            "clobTokenIds": '["A","B"]',
        }
    )
    st.upsert_market(market)  # populates markets + tokens
    book = OrderBook.from_clob(
        {
            "asset_id": "A", "market": "COND", "timestamp": "1700000000000",
            "tick_size": "0.01",
            "bids": [{"price": "0.40", "size": "100"}],
            "asks": [{"price": "0.42", "size": "80"}],
        }
    )
    st.record_top(book, "rest")
    st.record_top(book, "rest")
    st.close()
    return db


# --- data layer -------------------------------------------------------------

def test_latest_tops_enriches_and_counts(tmp_path):
    db = _populated_db(tmp_path)
    rows = latest_tops(db)
    assert len(rows) == 1                       # only asset A has book rows
    r = rows[0]
    assert r.asset_id == "A"
    assert r.question == "Test question?"
    assert r.outcome == "Yes"
    assert abs(r.mid - 0.41) < 1e-9
    assert abs(r.spread - 0.02) < 1e-9
    assert r.n_obs == 2


def test_mid_series_returns_values(tmp_path):
    db = _populated_db(tmp_path)
    series = mid_series(db, "A")
    assert len(series) == 2
    assert all(abs(v - 0.41) < 1e-9 for v in series)


def test_queries_tolerate_missing_tables(tmp_path):
    empty = tmp_path / "empty.db"
    empty.touch()
    assert latest_tops(empty) == []
    assert mid_series(empty, "A") == []


# --- rendering --------------------------------------------------------------

def test_sparkline_empty_single_multi():
    assert "<svg" in sparkline_svg([]) and "polyline" not in sparkline_svg([])
    assert "<circle" in sparkline_svg([0.5])
    multi = sparkline_svg([0.4, 0.5, 0.6])
    assert "<polyline" in multi
    assert multi.count(",") == 3                # three coordinate pairs


def test_page_html_renders_rows(tmp_path):
    db = _populated_db(tmp_path)
    rows = latest_tops(db)
    series = {r.asset_id: mid_series(db, r.asset_id) for r in rows}
    html = page_html(rows, series, str(db), refresh_s=15)
    assert "Test question?" in html
    assert 'http-equiv="refresh"' in html
    assert "<table>" in html


# --- web wiring (skip if fastapi not installed) -----------------------------

def test_app_endpoints(tmp_path):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from polybot.dashboard.app import create_app

    db = _populated_db(tmp_path)
    client = TestClient(create_app(db))
    assert client.get("/").status_code == 200
    assert client.get("/healthz").json()["assets"] == 1
    assert len(client.get("/api/tops").json()) == 1
