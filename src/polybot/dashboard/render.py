"""HTML + inline-SVG rendering for the dashboard (no web dependency).

Sparklines are generated as inline SVG server-side, so the page needs no
JavaScript charting library — it stays a single self-contained HTML response.
"""

from __future__ import annotations

import html
import time

from polybot.dashboard.data import AssetRow


def sparkline_svg(values: list[float], width: int = 130, height: int = 28) -> str:
    """Inline SVG sparkline, autoscaled to the series' own min/max."""
    pad = 3
    if not values:
        return f'<svg width="{width}" height="{height}"></svg>'
    if len(values) == 1:
        cy = height / 2
        return (
            f'<svg width="{width}" height="{height}">'
            f'<circle cx="{width/2:.1f}" cy="{cy:.1f}" r="2" fill="#0969da"/></svg>'
        )
    mn, mx = min(values), max(values)
    span = (mx - mn) or 1.0
    n = len(values)
    pts = []
    for i, v in enumerate(values):
        x = pad + i / (n - 1) * (width - 2 * pad)
        y = height - pad - (v - mn) / span * (height - 2 * pad)
        pts.append(f"{x:.1f},{y:.1f}")
    last_up = values[-1] >= values[0]
    color = "#1a7f37" if last_up else "#cf222e"
    return (
        f'<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}">'
        f'<polyline fill="none" stroke="{color}" stroke-width="1.3" '
        f'points="{" ".join(pts)}"/></svg>'
    )


def _fmt(x: float | None, nd: int = 3) -> str:
    return f"{x:.{nd}f}" if x is not None else "-"


def _age_cell(recv_ms: int, now_ms: int | None = None) -> str:
    now_ms = now_ms or int(time.time() * 1000)
    age_s = max(0, (now_ms - recv_ms) / 1000.0)
    if age_s < 90:
        color, label = "#1a7f37", f"{age_s:.0f}s"
    elif age_s < 300:
        color, label = "#9a6700", f"{age_s:.0f}s"
    else:
        mins = age_s / 60.0
        color, label = "#cf222e", (f"{mins:.0f}m" if mins < 90 else f"{mins/60:.1f}h")
    return f'<td style="color:{color};font-weight:600">{label}</td>'


_CSS = """
body{font-family:-apple-system,Segoe UI,Roboto,sans-serif;margin:1.5rem auto;max-width:1100px;color:#1f2328;padding:0 1rem}
h1{font-size:1.3rem;margin-bottom:.2rem}
.meta{color:#656d76;font-size:.85rem;margin-bottom:1rem}
table{border-collapse:collapse;width:100%;font-size:.86rem}
th,td{border-bottom:1px solid #d8dee4;padding:.4rem .55rem;text-align:left}
th{background:#f6f8fa;position:sticky;top:0}
.num{text-align:right;font-variant-numeric:tabular-nums}
.q{max-width:320px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.wide{background:#fff8c5}
tr:hover{background:#f6f8fa}
"""


def page_html(
    rows: list[AssetRow],
    series: dict[str, list[float]],
    db_path: str,
    refresh_s: int = 15,
    now_ms: int | None = None,
) -> str:
    now_ms = now_ms or int(time.time() * 1000)
    body_rows = []
    for r in rows:
        spark = sparkline_svg(series.get(r.asset_id, []))
        # Flag unusually wide spreads (the interesting, less-efficient markets).
        wide = ' class="wide"' if (r.spread or 0) >= 0.03 else ""
        body_rows.append(
            f"<tr>"
            f'<td class="q" title="{html.escape(r.question)}">{html.escape(r.question) or "-"}</td>'
            f"<td>{html.escape(r.outcome) or '-'}</td>"
            f'<td class="num">{_fmt(r.best_bid)}</td>'
            f'<td class="num">{_fmt(r.best_ask)}</td>'
            f'<td class="num">{_fmt(r.mid)}</td>'
            f'<td class="num"{wide}>{_fmt(r.spread)}</td>'
            f"<td>{spark}</td>"
            f'<td class="num">{r.n_obs}</td>'
            f"{_age_cell(r.recv_ms, now_ms)}"
            f"</tr>"
        )
    table = (
        "<table><thead><tr><th>question</th><th>outcome</th><th>bid</th>"
        "<th>ask</th><th>mid</th><th>spread</th><th>mid (recent)</th>"
        "<th>obs</th><th>updated</th></tr></thead>"
        f"<tbody>{''.join(body_rows) or '<tr><td colspan=9>No data yet.</td></tr>'}</tbody></table>"
    )
    gen = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(now_ms / 1000))
    return f"""<!doctype html><html><head><meta charset="utf-8">
<meta http-equiv="refresh" content="{refresh_s}">
<title>polybot recorder</title><style>{_CSS}</style></head><body>
<h1>polybot recorder &middot; live</h1>
<div class="meta">{len(rows)} assets &middot; {html.escape(db_path)} &middot;
generated {gen} UTC &middot; auto-refresh {refresh_s}s &middot;
<span style="color:#9a6700">yellow spread = &ge; 0.03 (wider/less efficient)</span></div>
{table}
</body></html>"""
