"""Lightweight read-only web dashboard for the live recorder.

Layered so the web framework is optional:
  - ``data``   : SQLite queries (pure, no web deps) — testable on its own.
  - ``render`` : HTML + inline-SVG sparklines (pure, no web deps).
  - ``app``    : the FastAPI wiring (imports fastapi/uvicorn lazily).
"""
