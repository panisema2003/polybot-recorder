"""Load recorded ticks from the SQLite store for backtesting."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from polybot.backtest.types import Tick


def _connect(db_path: str | Path) -> sqlite3.Connection:
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    return con


def resolve_asset(db_path: str | Path, slug: str, outcome: str = "Yes") -> str | None:
    """Map a market slug + outcome label to its CLOB asset id (needs the
    ``tokens`` table, populated by the recorder)."""
    con = _connect(db_path)
    try:
        row = con.execute(
            "SELECT asset_id FROM tokens WHERE slug = ? AND LOWER(outcome) = LOWER(?)",
            (slug, outcome),
        ).fetchone()
        return row["asset_id"] if row else None
    except sqlite3.OperationalError:
        # Old DB recorded before the tokens table existed; re-record to populate.
        return None
    finally:
        con.close()


def load_ticks(db_path: str | Path, asset_id: str) -> list[Tick]:
    """All recorded top-of-book ticks for one asset, in chronological order."""
    con = _connect(db_path)
    try:
        rows = con.execute(
            """SELECT asset_id, recv_ms, best_bid, best_bid_sz, best_ask, best_ask_sz
               FROM book_top WHERE asset_id = ? ORDER BY recv_ms""",
            (asset_id,),
        ).fetchall()
    finally:
        con.close()
    return [
        Tick(
            asset_id=r["asset_id"],
            ts_ms=r["recv_ms"],
            best_bid=r["best_bid"],
            best_bid_sz=r["best_bid_sz"],
            best_ask=r["best_ask"],
            best_ask_sz=r["best_ask_sz"],
        )
        for r in rows
    ]
