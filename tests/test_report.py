"""Tests for the HTML backtest report."""

from __future__ import annotations

from polybot.backtest.engine import BacktestResult
from polybot.backtest.report import build_report_html, write_report
from polybot.backtest.types import Fill, Side


def _result() -> BacktestResult:
    fills = [Fill(1_700_000_000_000, "tok123", Side.BUY, 50, 0.40, 0.0, "ask<fair")]
    curve = [(1_700_000_000_000, 1000.0), (1_700_000_030_000, 1020.0)]
    return BacktestResult(
        capital=1000.0, final_equity=1020.0, realized=0.0, unrealized=20.0,
        settled=False, fills=fills, equity_curve=curve,
    )


def test_build_report_html_has_key_sections():
    html = build_report_html(_result(), {"asset_id": "tok123", "source": "history",
                                         "fair": 0.7, "edge": 0.02, "question": "Q?"})
    assert html.startswith("<!doctype html>")
    assert "Net PnL" in html
    assert "BUY" in html
    assert "history" in html  # the optimistic-upper-bound warning path
    assert html.rstrip().endswith("</html>")


def test_write_report_creates_file(tmp_path):
    path = write_report(_result(), {"asset_id": "tok123", "source": "recorded"}, tmp_path)
    assert path.exists()
    assert path.suffix == ".html"
    assert "polybot backtest report" in path.read_text(encoding="utf-8")


def test_report_survives_without_equity_curve():
    empty = BacktestResult(1000.0, 1000.0, 0.0, 0.0, False, [], [])
    html = build_report_html(empty, {"asset_id": "x", "source": "recorded"})
    assert "No trades" in html
