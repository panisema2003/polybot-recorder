"""WebSocket client for the CLOB *market* channel (real-time, read-only).

Subscribing to the market channel with a list of asset (token) ids streams:
  - ``book``            : a full order-book snapshot (sent on connect + on change)
  - ``price_change``    : incremental level updates
  - ``tick_size_change``: the market's tick size changed
  - ``last_trade_price``: a trade printed

This client yields the raw decoded events as dicts (each carrying an
``event_type``) and handles reconnect-with-backoff and an app-level keepalive
ping so the connection survives idle periods. Interpreting events into
``OrderBook`` objects is the recorder's job.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import AsyncIterator

import websockets
from websockets.exceptions import ConnectionClosed

from polybot.config import ApiConfig, RecorderConfig
from polybot.log import get_logger

log = get_logger(__name__)


class MarketStream:
    def __init__(self, api: ApiConfig, rec: RecorderConfig, asset_ids: list[str]):
        self._url = api.ws_market_url
        self._rec = rec
        self._asset_ids = asset_ids

    async def _keepalive(self, ws) -> None:
        """Polymarket drops idle sockets; nudge it with a periodic PING frame."""
        try:
            while True:
                await asyncio.sleep(self._rec.ws_ping_interval_s)
                await ws.send("PING")
        except (ConnectionClosed, asyncio.CancelledError):
            return

    async def stream(self) -> AsyncIterator[dict]:
        """Yield decoded market events forever, reconnecting as needed."""
        backoff_min, backoff_max = self._rec.reconnect_backoff_s
        backoff = backoff_min
        while True:
            try:
                async with websockets.connect(
                    self._url, ping_interval=20, ping_timeout=20, max_size=None
                ) as ws:
                    await ws.send(
                        json.dumps({"assets_ids": self._asset_ids, "type": "market"})
                    )
                    log.info("WS subscribed to %d assets", len(self._asset_ids))
                    backoff = backoff_min  # reset on a healthy connect

                    ka = asyncio.create_task(self._keepalive(ws))
                    try:
                        async for raw in ws:
                            for event in self._decode(raw):
                                yield event
                    finally:
                        ka.cancel()
                        with contextlib.suppress(asyncio.CancelledError):
                            await ka
            except (ConnectionClosed, OSError) as exc:
                log.warning("WS dropped (%s); reconnecting in %.1fs", exc, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, backoff_max)

    @staticmethod
    def _decode(raw: str | bytes) -> list[dict]:
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        if raw == "PONG":
            return []
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return []
        # The server may batch events into a list or send one object.
        events = data if isinstance(data, list) else [data]
        return [e for e in events if isinstance(e, dict)]
