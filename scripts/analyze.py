#!/usr/bin/env python
"""Analyse recorded order-book data from the polybot SQLite store.

This is the eyeball-the-data step before any backtesting: how often do these
niche markets actually update, how wide/stable is the spread, how does the mid
move? That tells you whether there's slack to capture and whether the market is
liquid enough to bother with.

Usage:
    py scripts/analyze.py --list
    py scripts/analyze.py --slug fif-cdr-chl-2026-06-09-draw
    py scripts/analyze.py --asset 9802249026...   --out reports

Requires the analysis extras:  pip install -e ".[analysis]"
"""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # headless; we save PNGs, never open a window
import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402


def _default_db() -> Path:
    root = Path(__file__).resolve().parents[1]
    return root / "data" / "polybot.db"


def _load_top(con: sqlite3.Connection, where: str = "", params: tuple = ()) -> pd.DataFrame:
    df = pd.read_sql(f"SELECT * FROM book_top {where} ORDER BY recv_ms", con, params=params)
    if not df.empty:
        df["ts"] = pd.to_datetime(df["recv_ms"], unit="ms")
    return df


def cmd_list(con: sqlite3.Connection) -> None:
    """Per-asset summary: rows, time span, update cadence, spread, mid range."""
    df = _load_top(con)
    if df.empty:
        print("No data recorded yet. Run `polybot record ...` first.")
        return

    try:
        markets = pd.read_sql("SELECT condition_id, slug, question FROM markets", con)
        qmap = markets.set_index("condition_id")["question"].to_dict()
    except Exception:
        qmap = {}

    rows = []
    for asset_id, g in df.groupby("asset_id"):
        span_s = (g["recv_ms"].max() - g["recv_ms"].min()) / 1000.0
        cond = g["condition_id"].iloc[0]
        rows.append(
            {
                "asset_id": asset_id[:14] + "...",
                "question": (qmap.get(cond, "") or "")[:40],
                "rows": len(g),
                "span_min": round(span_s / 60.0, 1),
                "upd/min": round(len(g) / (span_s / 60.0), 1) if span_s > 0 else None,
                "mean_spread": round(g["spread"].mean(), 4),
                "mid_min": round(g["midpoint"].min(), 3),
                "mid_max": round(g["midpoint"].max(), 3),
            }
        )
    summary = pd.DataFrame(rows).sort_values("rows", ascending=False)
    print(summary.to_string(index=False))


def _resolve_assets(con: sqlite3.Connection, slug: str) -> list[str]:
    row = con.execute("SELECT condition_id FROM markets WHERE slug = ?", (slug,)).fetchone()
    if not row:
        return []
    cur = con.execute(
        "SELECT DISTINCT asset_id FROM book_top WHERE condition_id = ?", (row[0],)
    )
    return [r[0] for r in cur.fetchall()]


def plot_asset(con: sqlite3.Connection, asset_id: str, out_dir: Path) -> None:
    df = _load_top(con, "WHERE asset_id = ?", (asset_id,))
    if df.empty:
        print(f"No rows for asset {asset_id[:14]}...")
        return

    fig, ax_mid = plt.subplots(figsize=(11, 5))
    ax_mid.plot(df["ts"], df["midpoint"], color="tab:blue", lw=1.4, label="mid")
    ax_mid.fill_between(
        df["ts"], df["best_bid"], df["best_ask"], color="tab:blue", alpha=0.12,
        label="bid–ask",
    )
    ax_mid.set_ylabel("price (probability)", color="tab:blue")
    ax_mid.tick_params(axis="y", labelcolor="tab:blue")
    ax_mid.set_ylim(0, 1)

    ax_sp = ax_mid.twinx()
    ax_sp.plot(df["ts"], df["spread"], color="tab:red", lw=0.9, alpha=0.7, label="spread")
    ax_sp.set_ylabel("spread", color="tab:red")
    ax_sp.tick_params(axis="y", labelcolor="tab:red")

    ax_mid.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d %H:%M"))
    fig.autofmt_xdate()
    ax_mid.set_title(f"asset {asset_id[:18]}…  ({len(df)} obs)")
    ax_mid.grid(True, alpha=0.25)

    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{asset_id[:16]}.png"
    fig.tight_layout()
    fig.savefig(out_path, dpi=120)
    plt.close(fig)

    print(
        f"  {asset_id[:14]}...  obs={len(df):<5} "
        f"mean_spread={df['spread'].mean():.4f}  "
        f"mid {df['midpoint'].min():.3f}-{df['midpoint'].max():.3f}  "
        f"std={df['midpoint'].std():.4f}  -> {out_path}"
    )


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--db", type=Path, default=_default_db(), help="SQLite path")
    p.add_argument("--list", action="store_true", help="List recorded assets + stats")
    p.add_argument("--slug", help="Plot all assets of this market slug")
    p.add_argument("--asset", help="Plot a single asset/token id")
    p.add_argument("--out", type=Path, default=Path("reports"), help="PNG output dir")
    args = p.parse_args()

    if not args.db.exists():
        print(f"No database at {args.db}. Record some data first.")
        return

    con = sqlite3.connect(str(args.db))
    try:
        if args.list or not (args.slug or args.asset):
            cmd_list(con)
            return
        assets = [args.asset] if args.asset else _resolve_assets(con, args.slug)
        if not assets:
            print(f"No recorded assets for that selection.")
            return
        print(f"Plotting {len(assets)} asset(s) -> {args.out}/")
        for a in assets:
            plot_asset(con, a, args.out)
    finally:
        con.close()


if __name__ == "__main__":
    main()
