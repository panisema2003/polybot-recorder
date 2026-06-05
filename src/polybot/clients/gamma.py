"""Gamma API client — market discovery & metadata (read-only, no auth).

Gamma is Polymarket's metadata service. We use it to enumerate active markets
and read volume/liquidity/resolution fields. Live order books come from the
CLOB client instead.
"""

from __future__ import annotations

import asyncio

import httpx

from polybot.config import ApiConfig
from polybot.log import get_logger
from polybot.models import Market

log = get_logger(__name__)


class GammaClient:
    def __init__(self, cfg: ApiConfig, client: httpx.AsyncClient | None = None):
        self._cfg = cfg
        self._client = client or httpx.AsyncClient(
            base_url=cfg.gamma_base,
            timeout=cfg.request_timeout_s,
            headers={"User-Agent": "polybot/0.1 (research)"},
        )
        self._owns_client = client is None

    async def __aenter__(self) -> "GammaClient":
        return self

    async def __aexit__(self, *exc) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def _get(self, path: str, params: dict) -> list[dict]:
        for attempt in range(3):
            try:
                resp = await self._client.get(path, params=params)
                resp.raise_for_status()
                data = resp.json()
                # /markets returns a bare list; some endpoints wrap in {"data": [...]}.
                if isinstance(data, dict):
                    return data.get("data", [])
                return data
            except (httpx.HTTPError, ValueError) as exc:
                wait = 1.5 * (attempt + 1)
                log.warning("Gamma GET %s failed (%s); retrying in %.1fs", path, exc, wait)
                await asyncio.sleep(wait)
        log.error("Gamma GET %s gave up after retries", path)
        return []

    async def fetch_active_markets(
        self, scan_limit: int, page_size: int
    ) -> list[Market]:
        """Page through active, open markets up to ``scan_limit`` rows.

        Sorted by 24h volume descending so the first pages contain the liveliest
        markets; we keep paging to reach the long tail where niche markets live.
        """
        markets: list[Market] = []
        offset = 0
        while len(markets) < scan_limit:
            params = {
                "active": "true",
                "closed": "false",
                "archived": "false",
                "limit": min(page_size, scan_limit - len(markets)),
                "offset": offset,
                "order": "volume24hr",
                "ascending": "false",
            }
            batch = await self._get("/markets", params)
            if not batch:
                break
            markets.extend(Market.from_gamma(m) for m in batch)
            offset += len(batch)
            if len(batch) < params["limit"]:
                break  # reached the end
        log.info("Fetched %d active markets from Gamma", len(markets))
        return markets

    async def fetch_market_by_slug(self, slug: str) -> Market | None:
        batch = await self._get("/markets", {"slug": slug})
        return Market.from_gamma(batch[0]) if batch else None
