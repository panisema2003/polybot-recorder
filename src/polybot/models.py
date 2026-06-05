"""Domain models.

Parsers are defensive: the Gamma API returns several numeric fields as JSON
*strings* (e.g. ``outcomePrices`` is a stringified JSON array), and optional
fields are sometimes absent or empty. Each ``from_*`` classmethod normalises
that mess into clean typed objects so nothing downstream sees a raw dict.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone


def _f(value: object, default: float = 0.0) -> float:
    """Best-effort float parse for strings/None/numbers."""
    if value is None or value == "":
        return default
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _json_list(value: object) -> list:
    """Gamma encodes some arrays as JSON strings; handle both forms."""
    if isinstance(value, list):
        return value
    if isinstance(value, str) and value:
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, list) else []
        except json.JSONDecodeError:
            return []
    return []


def _parse_dt(value: object) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


@dataclass(frozen=True)
class Token:
    """One outcome of a market (e.g. 'Yes') and its CLOB asset id."""

    token_id: str
    outcome: str


@dataclass(frozen=True)
class Market:
    """A tradeable Polymarket market, as seen via the Gamma metadata API."""

    condition_id: str
    question_id: str
    slug: str
    question: str
    description: str
    tokens: list[Token]
    outcome_prices: list[float]
    end_date: datetime | None
    liquidity: float
    volume_24h: float
    volume_total: float
    enable_order_book: bool
    min_tick_size: float
    min_order_size: float
    neg_risk: bool

    @classmethod
    def from_gamma(cls, raw: dict) -> "Market":
        outcomes = _json_list(raw.get("outcomes"))
        token_ids = _json_list(raw.get("clobTokenIds"))
        tokens = [
            Token(token_id=str(tid), outcome=str(out))
            for tid, out in zip(token_ids, outcomes)
        ]
        return cls(
            condition_id=str(raw.get("conditionId", "")),
            question_id=str(raw.get("questionID", "")),
            slug=str(raw.get("slug", "")),
            question=str(raw.get("question", "")),
            description=str(raw.get("description", "")),
            tokens=tokens,
            outcome_prices=[_f(p) for p in _json_list(raw.get("outcomePrices"))],
            end_date=_parse_dt(raw.get("endDate")),
            liquidity=_f(raw.get("liquidityNum", raw.get("liquidity"))),
            volume_24h=_f(raw.get("volume24hr")),
            volume_total=_f(raw.get("volumeNum", raw.get("volume"))),
            enable_order_book=bool(raw.get("enableOrderBook", False)),
            min_tick_size=_f(raw.get("orderPriceMinTickSize"), 0.01),
            min_order_size=_f(raw.get("orderMinSize"), 5.0),
            neg_risk=bool(raw.get("negRisk", False)),
        )

    @property
    def yes_token(self) -> Token | None:
        for t in self.tokens:
            if t.outcome.strip().lower() == "yes":
                return t
        return self.tokens[0] if self.tokens else None

    def days_to_resolution(self, now: datetime | None = None) -> float | None:
        if self.end_date is None:
            return None
        now = now or datetime.now(timezone.utc)
        return (self.end_date - now).total_seconds() / 86400.0

    @property
    def text_blob(self) -> str:
        return f"{self.question}\n{self.description}".lower()


@dataclass(frozen=True)
class PricePoint:
    """One point of historical price (midpoint/last) — no spread/depth.

    This is all Polymarket's ``/prices-history`` exposes: a timestamp and a
    price. Useful for a directional first-pass backtest, but it cannot tell you
    execution cost (that needs the live order book the recorder captures).
    """

    ts_ms: int
    price: float


@dataclass(frozen=True)
class BookLevel:
    price: float
    size: float


@dataclass(frozen=True)
class OrderBook:
    """A CLOB order book snapshot.

    Levels are normalised so ``bids[0]`` is the best (highest) bid and
    ``asks[0]`` is the best (lowest) ask, regardless of the source ordering.
    """

    asset_id: str
    condition_id: str
    bids: list[BookLevel]
    asks: list[BookLevel]
    timestamp_ms: int
    tick_size: float
    neg_risk: bool
    last_trade_price: float | None

    @classmethod
    def from_clob(cls, raw: dict) -> "OrderBook":
        def levels(key: str, *, reverse: bool) -> list[BookLevel]:
            out = [
                BookLevel(price=_f(lvl.get("price")), size=_f(lvl.get("size")))
                for lvl in raw.get(key, [])
            ]
            out.sort(key=lambda lv: lv.price, reverse=reverse)
            return out

        ts = raw.get("timestamp")
        return cls(
            asset_id=str(raw.get("asset_id", "")),
            condition_id=str(raw.get("market", "")),
            bids=levels("bids", reverse=True),
            asks=levels("asks", reverse=False),
            timestamp_ms=int(ts) if ts else 0,
            tick_size=_f(raw.get("tick_size"), 0.01),
            neg_risk=bool(raw.get("neg_risk", False)),
            last_trade_price=(
                _f(raw["last_trade_price"]) if raw.get("last_trade_price") else None
            ),
        )

    @property
    def best_bid(self) -> BookLevel | None:
        return self.bids[0] if self.bids else None

    @property
    def best_ask(self) -> BookLevel | None:
        return self.asks[0] if self.asks else None

    @property
    def midpoint(self) -> float | None:
        if self.best_bid and self.best_ask:
            return (self.best_bid.price + self.best_ask.price) / 2.0
        return None

    @property
    def spread(self) -> float | None:
        """Absolute spread in price points (0..1)."""
        if self.best_bid and self.best_ask:
            return self.best_ask.price - self.best_bid.price
        return None
