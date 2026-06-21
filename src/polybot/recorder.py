"""Order-book recorder.

Consumes the live WS market channel for a set of markets and persists a
top-of-book time series (plus periodic full snapshots) to SQLite.

Correctness strategy: the periodic REST snapshot is the *source of truth* — it
fetches the authoritative book and overwrites local state. The WS stream layers
higher-frequency updates on top via a small local book maintained from
``book`` (full) and ``price_change`` (delta) events. If a delta schema ever
drifts, the next REST refresh self-heals the recorded series.
"""

from __future__ import annotations

import asyncio
import time

from polybot.clients import ClobClient, MarketStream
from polybot.config import Settings
from polybot.log import get_logger
from polybot.models import BookLevel, Market, OrderBook
from polybot.storage import Storage

log = get_logger(__name__)


class _BookState:
    """Mutable local book for one asset, kept as price->size maps."""

    def __init__(self, asset_id: str, condition_id: str):
        self.asset_id = asset_id
        self.condition_id = condition_id
        self.bids: dict[float, float] = {}
        self.asks: dict[float, float] = {}
        self.tick_size = 0.01
        self.neg_risk = False
        self.last_trade_price: float | None = None
        self.exchange_ms = 0

    def replace_from(self, book: OrderBook) -> None:
        self.bids = {lv.price: lv.size for lv in book.bids if lv.size > 0}
        self.asks = {lv.price: lv.size for lv in book.asks if lv.size > 0}
        self.tick_size = book.tick_size
        self.neg_risk = book.neg_risk
        self.exchange_ms = book.timestamp_ms
        if book.last_trade_price is not None:
            self.last_trade_price = book.last_trade_price

    def apply_change(self, side: str, price: float, size: float) -> None:
        side = side.lower()
        book = self.bids if side in ("buy", "bid") else self.asks
        if size <= 0:
            book.pop(price, None)
        else:
            book[price] = size

    def to_orderbook(self, exchange_ms: int | None = None) -> OrderBook:
        bids = sorted(
            (BookLevel(p, s) for p, s in self.bids.items()),
            key=lambda lv: lv.price,
            reverse=True,
        )
        asks = sorted(
            (BookLevel(p, s) for p, s in self.asks.items()), key=lambda lv: lv.price
        )
        return OrderBook(
            asset_id=self.asset_id,
            condition_id=self.condition_id,
            bids=bids,
            asks=asks,
            timestamp_ms=exchange_ms or self.exchange_ms,
            tick_size=self.tick_size,
            neg_risk=self.neg_risk,
            last_trade_price=self.last_trade_price,
        )


class Recorder:
    def __init__(self, settings: Settings, markets: list[Market]):
        self.settings = settings
        self.markets = markets
        self.storage = Storage(settings.db_path)
        self.clob = ClobClient(settings.api)

        # asset_id -> (condition_id, _BookState)
        self.state: dict[str, _BookState] = {}
        for m in markets:
            for tok in m.tokens:
                self.state[tok.token_id] = _BookState(tok.token_id, m.condition_id)
        self.asset_ids = list(self.state.keys())
        # Last full-depth snapshot time per asset (ms); 0 = never.
        self._last_snap_ms: dict[str, int] = {a: 0 for a in self.asset_ids}

    def _due_for_snapshot(self, asset_id: str) -> bool:
        """True if it's time to persist a full-depth snapshot for this asset.

        Top-of-book is recorded on every update; full snapshots (the whole book
        as JSON) are throttled to snapshot_interval_s to keep the DB small.
        """
        now_ms = int(time.time() * 1000)
        interval_ms = self.settings.recorder.snapshot_interval_s * 1000
        if now_ms - self._last_snap_ms[asset_id] >= interval_ms:
            self._last_snap_ms[asset_id] = now_ms
            return True
        return False

    async def _persist_top(self, asset_id: str, source: str) -> None:
        st = self.state.get(asset_id)
        if st is None:
            return
        book = st.to_orderbook()
        await asyncio.to_thread(self.storage.record_top, book, source)
        if self._due_for_snapshot(asset_id):
            await asyncio.to_thread(self.storage.record_snapshot, book, source)

    async def _handle_event(self, event: dict) -> None:
        etype = event.get("event_type") or event.get("type") or ""
        asset_id = str(event.get("asset_id") or event.get("token_id") or "")
        # Ignore control frames / events for assets we aren't tracking.
        if not asset_id or asset_id not in self.state:
            return

        if etype == "book":
            book = OrderBook.from_clob(event)
            self.state[asset_id].replace_from(book)
            await self._persist_top(asset_id, "ws_book")

        elif etype in ("price_change", "price_changes"):
            changes = event.get("changes") or event.get("price_changes") or []
            st = self.state[asset_id]
            for ch in changes:
                try:
                    st.apply_change(
                        str(ch.get("side", "")),
                        float(ch["price"]),
                        float(ch["size"]),
                    )
                except (KeyError, TypeError, ValueError):
                    continue
            await self._persist_top(asset_id, "ws_price")

        elif etype in ("last_trade_price", "trade"):
            try:
                price = float(event["price"])
            except (KeyError, TypeError, ValueError):
                return
            st = self.state[asset_id]
            st.last_trade_price = price
            ex_ms = int(event["timestamp"]) if event.get("timestamp") else None
            await asyncio.to_thread(
                self.storage.record_trade, asset_id, st.condition_id, price, ex_ms
            )

    async def _ws_loop(self) -> None:
        stream = MarketStream(self.settings.api, self.settings.recorder, self.asset_ids)
        async for event in stream.stream():
            await self._handle_event(event)

    async def _rest_loop(self) -> None:
        interval = self.settings.recorder.rest_snapshot_interval_s
        while True:
            books = await self.clob.get_books(self.asset_ids)
            for asset_id, book in books.items():
                self.state[asset_id].replace_from(book)
                await asyncio.to_thread(self.storage.record_top, book, "rest")
                if self._due_for_snapshot(asset_id):
                    await asyncio.to_thread(self.storage.record_snapshot, book, "rest")
            log.info("REST refresh: %d/%d books", len(books), len(self.asset_ids))
            await asyncio.sleep(interval)

    async def run(self) -> None:
        for m in self.markets:
            await asyncio.to_thread(self.storage.upsert_market, m)
        log.info(
            "Recording %d markets / %d assets -> %s",
            len(self.markets),
            len(self.asset_ids),
            self.settings.db_path,
        )
        try:
            await asyncio.gather(self._ws_loop(), self._rest_loop())
        finally:
            await self.clob.aclose()
            self.storage.close()
