"""Niche-market discovery & scoring.

The thesis (see config.yaml): we cannot out-run the sub-100ms arbitrage bots,
so we hunt where they aren't — markets that are in our circle of competence,
liquid enough to trade, *low-attention* (so under-priced by lazy money), with a
wide live spread and a near-ish resolution date.

Two stages:
  1. Cheap metadata pass over every active market: hard filters + a score that
     uses only Gamma fields (theme, liquidity, attention, time-to-resolution).
  2. Enrich the top candidates with a live CLOB order book to measure the real
     spread, then re-score. This keeps us from hammering the book endpoint for
     thousands of markets.

All scoring components are normalised to 0..1 and combined with the weights
from config, so the final score is interpretable and tunable.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

from polybot.clients import ClobClient, GammaClient
from polybot.config import DiscoveryConfig, Filters
from polybot.log import get_logger
from polybot.models import Market, OrderBook

log = get_logger(__name__)


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def compile_themes(themes: dict[str, list[str]]) -> dict[str, list[re.Pattern]]:
    """Compile theme keywords into word-boundary regexes.

    Matching is whole-word by default so ``petro`` no longer matches
    ``petroleum``. A trailing ``*`` makes a keyword a prefix stem, so
    ``peru*`` matches both ``peru`` and ``peruvian`` and ``bogot*`` matches
    ``bogota``/``bogotá`` (``\\w`` is Unicode-aware). Multi-word phrases like
    ``"latin america"`` work too.
    """
    compiled: dict[str, list[re.Pattern]] = {}
    for name, keywords in themes.items():
        patterns: list[re.Pattern] = []
        for kw in keywords:
            kw = kw.strip().lower()
            if not kw:
                continue
            if kw.endswith("*"):
                patterns.append(re.compile(r"\b" + re.escape(kw[:-1]) + r"\w*"))
            else:
                patterns.append(re.compile(r"\b" + re.escape(kw) + r"\b"))
        compiled[name] = patterns
    return compiled


def _match_compiled(blob: str, compiled: dict[str, list[re.Pattern]]) -> list[str]:
    return [name for name, pats in compiled.items() if any(p.search(blob) for p in pats)]


def match_themes(market: Market, themes: dict[str, list[str]]) -> list[str]:
    """Return the names of themes whose keywords appear in the market text.

    Convenience wrapper that compiles on each call — fine for tests/one-offs.
    Hot paths use a pre-compiled matcher (see ``NicheScreener``).
    """
    return _match_compiled(market.text_blob, compile_themes(themes))


def passes_filters(market: Market, f: Filters, now: datetime | None = None) -> bool:
    if f.require_order_book and not market.enable_order_book:
        return False
    if market.liquidity < f.min_liquidity:
        return False
    if not (f.min_volume_24h <= market.volume_24h <= f.max_volume_24h):
        return False
    days = market.days_to_resolution(now)
    if days is None:
        return False
    if not (f.min_days_to_resolution <= days <= f.max_days_to_resolution):
        return False
    return True


@dataclass
class ScoredMarket:
    market: Market
    themes: list[str]
    components: dict[str, float] = field(default_factory=dict)
    score: float = 0.0
    book: OrderBook | None = None

    @property
    def spread(self) -> float | None:
        return self.book.spread if self.book else None

    @property
    def midpoint(self) -> float | None:
        return self.book.midpoint if self.book else None


class NicheScreener:
    """Filters and ranks markets per the discovery config."""

    def __init__(self, cfg: DiscoveryConfig):
        self.cfg = cfg
        self._themes = compile_themes(cfg.themes)  # compile once, reuse per market

    # --- component scorers (each returns 0..1) ---

    def _theme_component(self, themes: list[str]) -> float:
        # One matched theme is most of the signal; a second adds a little.
        return _clamp(min(len(themes), 2) / 2.0)

    def _liquidity_component(self, liquidity: float) -> float:
        # Log-scaled between the filter floor and a generous cap (~$200k).
        floor = max(self.cfg.filters.min_liquidity, 1.0)
        lo, hi = math.log10(floor), math.log10(200_000.0)
        return _clamp((math.log10(max(liquidity, floor)) - lo) / (hi - lo))

    def _attention_component(self, volume_24h: float) -> float:
        # Inverse of 24h volume within the configured band: quieter => higher.
        lo = self.cfg.filters.min_volume_24h
        hi = self.cfg.filters.max_volume_24h
        if hi <= lo:
            return 0.0
        return _clamp(1.0 - (volume_24h - lo) / (hi - lo))

    def _time_component(self, days: float | None) -> float:
        if days is None:
            return 0.0
        return _clamp(1.0 - days / self.cfg.filters.max_days_to_resolution)

    def _spread_component(self, spread: float | None) -> float:
        # A 10-cent spread (0.10) maxes this out; tighter scales down linearly.
        if spread is None or spread <= 0:
            return 0.0
        return _clamp(spread / 0.10)

    def _combine(self, components: dict[str, float]) -> float:
        w = self.cfg.weights
        return sum(w.get(name, 0.0) * value for name, value in components.items())

    def score_metadata(
        self, market: Market, themes: list[str], now: datetime | None = None
    ) -> ScoredMarket:
        """Stage-1 score from Gamma fields only (no live book yet)."""
        components = {
            "theme_match": self._theme_component(themes),
            "liquidity": self._liquidity_component(market.liquidity),
            "low_attention": self._attention_component(market.volume_24h),
            "time_decay": self._time_component(market.days_to_resolution(now)),
            "spread": 0.0,  # filled in stage 2
        }
        sm = ScoredMarket(market=market, themes=themes, components=components)
        sm.score = self._combine(components)
        return sm

    def apply_book(self, sm: ScoredMarket, book: OrderBook) -> None:
        """Stage-2: fold the live spread into an already-scored market."""
        sm.book = book
        sm.components["spread"] = self._spread_component(book.spread)
        sm.score = self._combine(sm.components)

    def screen_metadata(
        self, markets: list[Market], now: datetime | None = None
    ) -> list[ScoredMarket]:
        now = now or datetime.now(timezone.utc)
        out: list[ScoredMarket] = []
        for m in markets:
            if not passes_filters(m, self.cfg.filters, now):
                continue
            themes = _match_compiled(m.text_blob, self._themes)
            out.append(self.score_metadata(m, themes, now))
        out.sort(key=lambda s: s.score, reverse=True)
        log.info("%d markets passed filters (of %d scanned)", len(out), len(markets))
        return out


async def discover(
    gamma: GammaClient,
    clob: ClobClient,
    cfg: DiscoveryConfig,
    *,
    enrich_top: int = 60,
    now: datetime | None = None,
) -> list[ScoredMarket]:
    """End-to-end discovery: fetch -> filter -> score -> enrich top N -> rank."""
    markets = await gamma.fetch_active_markets(cfg.scan_limit, cfg.page_size)
    screener = NicheScreener(cfg)
    scored = screener.screen_metadata(markets, now)

    candidates = scored[:enrich_top]
    token_ids = [
        sm.market.yes_token.token_id
        for sm in candidates
        if sm.market.yes_token is not None
    ]
    books = await clob.get_books(token_ids)
    for sm in candidates:
        tok = sm.market.yes_token
        if tok and tok.token_id in books:
            screener.apply_book(sm, books[tok.token_id])

    candidates.sort(key=lambda s: s.score, reverse=True)
    return candidates
