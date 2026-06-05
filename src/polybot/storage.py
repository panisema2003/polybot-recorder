"""SQLite persistence for captured market data.

Two grains are stored:
  - ``book_top``: a row per top-of-book observation (compact time series, the
    thing you'll actually backtest/plot against);
  - ``book_snapshot``: occasional full-depth JSON snapshots for when you need
    the whole book, not just the touch.

SQLite is intentional: zero-ops, single file, trivially queryable from pandas
later. The connection is opened with ``check_same_thread=False`` and guarded by
a lock so the async recorder can write via ``asyncio.to_thread``.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

from polybot.models import Market, OrderBook

_SCHEMA = """
CREATE TABLE IF NOT EXISTS markets (
    condition_id   TEXT PRIMARY KEY,
    slug           TEXT,
    question       TEXT,
    end_date       TEXT,
    liquidity      REAL,
    volume_total   REAL,
    neg_risk       INTEGER,
    first_seen_ms  INTEGER
);

CREATE TABLE IF NOT EXISTS tokens (
    asset_id      TEXT PRIMARY KEY,
    condition_id  TEXT,
    slug          TEXT,
    outcome       TEXT
);
CREATE INDEX IF NOT EXISTS ix_tokens_slug ON tokens(slug);

CREATE TABLE IF NOT EXISTS book_top (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    asset_id      TEXT NOT NULL,
    condition_id  TEXT,
    exchange_ms   INTEGER,
    recv_ms       INTEGER NOT NULL,
    best_bid      REAL,
    best_bid_sz   REAL,
    best_ask      REAL,
    best_ask_sz   REAL,
    midpoint      REAL,
    spread        REAL,
    source        TEXT
);
CREATE INDEX IF NOT EXISTS ix_book_top_asset_time ON book_top(asset_id, recv_ms);

CREATE TABLE IF NOT EXISTS book_snapshot (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    asset_id      TEXT NOT NULL,
    condition_id  TEXT,
    exchange_ms   INTEGER,
    recv_ms       INTEGER NOT NULL,
    bids_json     TEXT,
    asks_json     TEXT,
    source        TEXT
);
CREATE INDEX IF NOT EXISTS ix_book_snap_asset_time ON book_snapshot(asset_id, recv_ms);

CREATE TABLE IF NOT EXISTS trades (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    asset_id      TEXT NOT NULL,
    condition_id  TEXT,
    exchange_ms   INTEGER,
    recv_ms       INTEGER NOT NULL,
    price         REAL
);
CREATE INDEX IF NOT EXISTS ix_trades_asset_time ON trades(asset_id, recv_ms);
"""


def _now_ms() -> int:
    return int(time.time() * 1000)


class Storage:
    def __init__(self, db_path: str | Path):
        path = Path(db_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._lock = threading.Lock()
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    def upsert_market(self, m: Market) -> None:
        with self._lock:
            self._conn.execute(
                """INSERT INTO markets
                   (condition_id, slug, question, end_date, liquidity,
                    volume_total, neg_risk, first_seen_ms)
                   VALUES (?,?,?,?,?,?,?,?)
                   ON CONFLICT(condition_id) DO UPDATE SET
                     liquidity=excluded.liquidity,
                     volume_total=excluded.volume_total""",
                (
                    m.condition_id,
                    m.slug,
                    m.question,
                    m.end_date.isoformat() if m.end_date else None,
                    m.liquidity,
                    m.volume_total,
                    int(m.neg_risk),
                    _now_ms(),
                ),
            )
            # Persist the asset_id -> outcome mapping so backtests can resolve
            # 'Yes'/'No' offline without re-hitting Gamma.
            for tok in m.tokens:
                self._conn.execute(
                    """INSERT INTO tokens (asset_id, condition_id, slug, outcome)
                       VALUES (?,?,?,?)
                       ON CONFLICT(asset_id) DO UPDATE SET
                         outcome=excluded.outcome, slug=excluded.slug""",
                    (tok.token_id, m.condition_id, m.slug, tok.outcome),
                )
            self._conn.commit()

    def record_top(self, book: OrderBook, source: str) -> None:
        bid = book.best_bid
        ask = book.best_ask
        with self._lock:
            self._conn.execute(
                """INSERT INTO book_top
                   (asset_id, condition_id, exchange_ms, recv_ms, best_bid,
                    best_bid_sz, best_ask, best_ask_sz, midpoint, spread, source)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    book.asset_id,
                    book.condition_id,
                    book.timestamp_ms or None,
                    _now_ms(),
                    bid.price if bid else None,
                    bid.size if bid else None,
                    ask.price if ask else None,
                    ask.size if ask else None,
                    book.midpoint,
                    book.spread,
                    source,
                ),
            )
            self._conn.commit()

    def record_snapshot(self, book: OrderBook, source: str) -> None:
        with self._lock:
            self._conn.execute(
                """INSERT INTO book_snapshot
                   (asset_id, condition_id, exchange_ms, recv_ms,
                    bids_json, asks_json, source)
                   VALUES (?,?,?,?,?,?,?)""",
                (
                    book.asset_id,
                    book.condition_id,
                    book.timestamp_ms or None,
                    _now_ms(),
                    json.dumps([(lv.price, lv.size) for lv in book.bids]),
                    json.dumps([(lv.price, lv.size) for lv in book.asks]),
                    source,
                ),
            )
            self._conn.commit()

    def record_trade(
        self, asset_id: str, condition_id: str, price: float, exchange_ms: int | None
    ) -> None:
        with self._lock:
            self._conn.execute(
                """INSERT INTO trades
                   (asset_id, condition_id, exchange_ms, recv_ms, price)
                   VALUES (?,?,?,?,?)""",
                (asset_id, condition_id, exchange_ms, _now_ms(), price),
            )
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()
