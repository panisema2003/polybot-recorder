"""CLOB API client — live order books & prices (read-only, no auth).

The CLOB serves the actual matching-engine state: order books, midpoints,
spreads. These endpoints are public for reads; placing orders (Phase 2) needs
signed requests, which this client deliberately does not implement.
"""

from __future__ import annotations

import asyncio

import httpx

from polybot.config import ApiConfig
from polybot.log import get_logger
from polybot.models import OrderBook, PricePoint

log = get_logger(__name__)


class ClobClient:
    def __init__(self, cfg: ApiConfig, client: httpx.AsyncClient | None = None):
        self._cfg = cfg
        self._client = client or httpx.AsyncClient(
            base_url=cfg.clob_base,
            timeout=cfg.request_timeout_s,
            headers={"User-Agent": "polybot/0.1 (research)"},
        )
        self._owns_client = client is None
        self._sem = asyncio.Semaphore(cfg.max_concurrency)

    async def __aenter__(self) -> "ClobClient":
        return self

    async def __aexit__(self, *exc) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def get_book(self, token_id: str) -> OrderBook | None:
        async with self._sem:
            try:
                resp = await self._client.get("/book", params={"token_id": token_id})
                resp.raise_for_status()
                return OrderBook.from_clob(resp.json())
            except (httpx.HTTPError, ValueError) as exc:
                log.warning("CLOB /book failed for %s: %s", token_id[:12], exc)
                return None

    async def get_books(self, token_ids: list[str]) -> dict[str, OrderBook]:
        """Fetch many books concurrently (bounded by max_concurrency)."""
        results = await asyncio.gather(*(self.get_book(t) for t in token_ids))
        return {
            tid: book for tid, book in zip(token_ids, results) if book is not None
        }

    async def get_price_history(
        self, token_id: str, interval: str = "max", fidelity: int = 60
    ) -> list[PricePoint]:
        """Fetch historical price (midpoint/last) for a token.

        ``interval`` is the look-back window ("max", "1w", "1d", ...) and
        ``fidelity`` is the resolution in minutes. Returns chronological points.
        NOTE: this is price only — no spread or depth. For execution-realistic
        backtests, record the live book instead.
        """
        async with self._sem:
            try:
                resp = await self._client.get(
                    "/prices-history",
                    params={"market": token_id, "interval": interval, "fidelity": fidelity},
                )
                resp.raise_for_status()
                history = resp.json().get("history", [])
            except (httpx.HTTPError, ValueError) as exc:
                log.warning("CLOB /prices-history failed for %s: %s", token_id[:12], exc)
                return []
        return [
            PricePoint(ts_ms=int(pt["t"]) * 1000, price=float(pt["p"]))
            for pt in history
            if "t" in pt and "p" in pt
        ]
