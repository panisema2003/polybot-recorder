"""Configuration loading.

Single source of truth for runtime settings. Reads ``config.yaml`` (path
overridable via ``POLYBOT_CONFIG``) and ``.env`` for secrets, then exposes a
set of frozen dataclasses so the rest of the code never touches raw dicts.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv


@dataclass(frozen=True)
class ApiConfig:
    gamma_base: str
    clob_base: str
    ws_market_url: str
    max_concurrency: int
    request_timeout_s: float


@dataclass(frozen=True)
class Filters:
    require_order_book: bool
    min_liquidity: float
    max_days_to_resolution: int
    min_days_to_resolution: int
    min_volume_24h: float
    max_volume_24h: float


@dataclass(frozen=True)
class DiscoveryConfig:
    scan_limit: int
    page_size: int
    filters: Filters
    weights: dict[str, float]
    themes: dict[str, list[str]]


@dataclass(frozen=True)
class RecorderConfig:
    rest_snapshot_interval_s: int
    ws_ping_interval_s: int
    reconnect_backoff_s: tuple[float, float]


@dataclass(frozen=True)
class Settings:
    api: ApiConfig
    discovery: DiscoveryConfig
    recorder: RecorderConfig
    db_path: Path
    log_level: str
    project_root: Path

    @classmethod
    def load(cls, config_path: str | os.PathLike[str] | None = None) -> "Settings":
        root = Path(__file__).resolve().parents[2]
        load_dotenv(root / ".env")

        cfg_path = Path(
            config_path or os.getenv("POLYBOT_CONFIG") or root / "config.yaml"
        )
        raw: dict[str, Any] = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))

        api = ApiConfig(**raw["api"])
        disc_raw = raw["discovery"]
        discovery = DiscoveryConfig(
            scan_limit=disc_raw["scan_limit"],
            page_size=disc_raw["page_size"],
            filters=Filters(**disc_raw["filters"]),
            weights={k: float(v) for k, v in disc_raw["weights"].items()},
            themes={k: [s.lower() for s in v] for k, v in disc_raw["themes"].items()},
        )
        rec_raw = raw["recorder"]
        recorder = RecorderConfig(
            rest_snapshot_interval_s=rec_raw["rest_snapshot_interval_s"],
            ws_ping_interval_s=rec_raw["ws_ping_interval_s"],
            reconnect_backoff_s=tuple(rec_raw["reconnect_backoff_s"]),  # type: ignore[arg-type]
        )

        db_path = Path(os.getenv("POLYBOT_DB") or raw["storage"]["db_path"])
        if not db_path.is_absolute():
            db_path = root / db_path

        return cls(
            api=api,
            discovery=discovery,
            recorder=recorder,
            db_path=db_path,
            log_level=os.getenv("POLYBOT_LOG_LEVEL", "INFO").upper(),
            project_root=root,
        )
