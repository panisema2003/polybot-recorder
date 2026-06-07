"""Read-only SQLite queries for the dashboard (no web dependency)."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class AssetRow:
    asset_id: str
    question: str
    outcome: str
    slug: str
    best_bid: float | None
    best_ask: float | None
    mid: float | None
    spread: float | None
    recv_ms: int
    n_obs: int


def _connect(db_path: str | Path) -> sqlite3.Connection:
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    return con


def latest_tops(db_path: str | Path) -> list[AssetRow]:
    """Latest top-of-book per asset, enriched with question/outcome and obs count."""
    con = _connect(db_path)
    try:
        rows = con.execute(
            """
            WITH last AS (
                SELECT asset_id, MAX(recv_ms) AS mx, COUNT(*) AS n
                FROM book_top GROUP BY asset_id
            )
            SELECT bt.asset_id, bt.best_bid, bt.best_ask, bt.midpoint AS mid,
                   bt.spread, bt.recv_ms, last.n AS n_obs,
                   COALESCE(t.outcome, '')  AS outcome,
                   COALESCE(t.slug, mk.slug, '') AS slug,
                   COALESCE(mk.question, '') AS question
            FROM book_top bt
            JOIN last ON last.asset_id = bt.asset_id AND last.mx = bt.recv_ms
            LEFT JOIN tokens  t  ON t.asset_id = bt.asset_id
            LEFT JOIN markets mk ON mk.condition_id = bt.condition_id
            GROUP BY bt.asset_id
            ORDER BY question, outcome
            """
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    finally:
        con.close()
    return [
        AssetRow(
            asset_id=r["asset_id"],
            question=r["question"],
            outcome=r["outcome"],
            slug=r["slug"],
            best_bid=r["best_bid"],
            best_ask=r["best_ask"],
            mid=r["mid"],
            spread=r["spread"],
            recv_ms=r["recv_ms"] or 0,
            n_obs=r["n_obs"] or 0,
        )
        for r in rows
    ]


def mid_series(db_path: str | Path, asset_id: str, limit: int = 60) -> list[float]:
    """Most recent ``limit`` midpoints for an asset, oldest-first (for sparklines)."""
    con = _connect(db_path)
    try:
        rows = con.execute(
            """SELECT midpoint FROM book_top
               WHERE asset_id = ? AND midpoint IS NOT NULL
               ORDER BY recv_ms DESC LIMIT ?""",
            (asset_id, limit),
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    finally:
        con.close()
    return [r["midpoint"] for r in reversed(rows)]
