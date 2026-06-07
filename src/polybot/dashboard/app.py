"""FastAPI app for the dashboard. Imports fastapi lazily via create_app()."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

from polybot.dashboard.data import latest_tops, mid_series
from polybot.dashboard.render import page_html


def create_app(db_path: str | Path, refresh_s: int = 15):
    from fastapi import FastAPI
    from fastapi.responses import HTMLResponse, JSONResponse

    db_path = str(db_path)
    app = FastAPI(title="polybot recorder dashboard", docs_url=None, redoc_url=None)

    @app.get("/", response_class=HTMLResponse)
    def index() -> HTMLResponse:
        rows = latest_tops(db_path)
        series = {r.asset_id: mid_series(db_path, r.asset_id) for r in rows}
        return HTMLResponse(page_html(rows, series, db_path, refresh_s))

    @app.get("/api/tops")
    def api_tops() -> JSONResponse:
        return JSONResponse([asdict(r) for r in latest_tops(db_path)])

    @app.get("/healthz")
    def healthz() -> dict:
        return {"ok": True, "assets": len(latest_tops(db_path))}

    return app
