"""Self-contained HTML report for a backtest run.

Produces a single .html file (equity curve embedded as base64 PNG + summary
stats + trades table) you can open in any browser or share. matplotlib is
imported lazily so the core backtester has no hard dependency on it — if it's
missing, the report is still written, just without the chart.
"""

from __future__ import annotations

import base64
import html
import io
from datetime import datetime, timezone
from pathlib import Path

from polybot.backtest.engine import BacktestResult


def _fmt_ts(ts_ms: int) -> str:
    if not ts_ms:
        return "settlement"
    return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _equity_png_b64(result: BacktestResult) -> str | None:
    """Render the equity curve to a base64 PNG, or None if matplotlib absent."""
    if not result.equity_curve:
        return None
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.dates as mdates
        import matplotlib.pyplot as plt
    except ImportError:
        return None

    times = [datetime.fromtimestamp(ts / 1000, tz=timezone.utc) for ts, _ in result.equity_curve]
    equity = [eq for _, eq in result.equity_curve]

    fig, ax = plt.subplots(figsize=(10, 4))
    ax.plot(times, equity, color="tab:blue", lw=1.6)
    ax.axhline(result.capital, color="gray", ls="--", lw=0.9, label="starting capital")
    ax.fill_between(
        times, result.capital, equity,
        where=[e >= result.capital for e in equity], color="tab:green", alpha=0.12,
    )
    ax.fill_between(
        times, result.capital, equity,
        where=[e < result.capital for e in equity], color="tab:red", alpha=0.12,
    )
    ax.set_ylabel("equity")
    ax.set_title("Equity curve (mark-to-market)")
    ax.grid(True, alpha=0.25)
    ax.legend(loc="best", fontsize=8)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d %H:%M"))
    fig.autofmt_xdate()

    buf = io.BytesIO()
    fig.tight_layout()
    fig.savefig(buf, format="png", dpi=120)
    plt.close(fig)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _trades_table(result: BacktestResult) -> str:
    if not result.fills:
        return "<p><em>No trades.</em></p>"
    rows = []
    for f in result.fills:
        side_color = "#1a7f37" if f.side.value == "BUY" else "#cf222e"
        rows.append(
            f"<tr><td>{_fmt_ts(f.ts_ms)}</td>"
            f"<td style='color:{side_color};font-weight:600'>{f.side.value}</td>"
            f"<td class='num'>{f.size:,.2f}</td>"
            f"<td class='num'>{f.price:.3f}</td>"
            f"<td class='num'>{f.fee:.4f}</td>"
            f"<td>{html.escape(f.reason)}</td></tr>"
        )
    return (
        "<table><thead><tr><th>time (UTC)</th><th>side</th><th>size</th>"
        "<th>price</th><th>fee</th><th>reason</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table>"
    )


def _stat_rows(result: BacktestResult, meta: dict) -> str:
    pnl_color = "#1a7f37" if result.pnl >= 0 else "#cf222e"
    items = [
        ("Asset", html.escape(str(meta.get("asset_id", ""))[:24] + "…")),
        ("Source", html.escape(str(meta.get("source", "")))),
        ("Your fair", f"{meta.get('fair', '')}"),
        ("Edge threshold", f"{meta.get('edge', '')}"),
        ("Capital", f"{result.capital:,.2f}"),
        ("Final equity", f"{result.final_equity:,.2f}"),
        ("Net PnL", f"<span style='color:{pnl_color};font-weight:700'>"
                    f"{result.pnl:+,.2f} ({result.return_pct:+.2f}%)</span>"),
        ("Realized", f"{result.realized:+,.2f}"),
        ("Unrealized", f"{result.unrealized:+,.2f}"),
        ("Settled at resolution", str(result.settled)),
        ("Fills", str(len(result.fills))),
        ("Max drawdown", f"{result.max_drawdown_pct:.2f}%"),
    ]
    return "".join(f"<tr><th>{k}</th><td>{v}</td></tr>" for k, v in items)


_CSS = """
body{font-family:-apple-system,Segoe UI,Roboto,sans-serif;margin:2rem auto;max-width:900px;color:#1f2328;padding:0 1rem}
h1{font-size:1.4rem} h2{font-size:1.1rem;margin-top:2rem;border-bottom:1px solid #d0d7de;padding-bottom:.3rem}
table{border-collapse:collapse;width:100%;font-size:.9rem;margin:.5rem 0}
th,td{border:1px solid #d0d7de;padding:.4rem .6rem;text-align:left}
.kv th{width:200px;background:#f6f8fa} .num{text-align:right;font-variant-numeric:tabular-nums}
img{max-width:100%;border:1px solid #d0d7de;border-radius:6px;margin-top:.5rem}
.note{color:#656d76;font-size:.85rem} .warn{color:#9a6700;background:#fff8c5;padding:.5rem;border-radius:6px}
"""


def build_report_html(result: BacktestResult, meta: dict) -> str:
    png = _equity_png_b64(result)
    chart = (
        f'<img src="data:image/png;base64,{png}" alt="equity curve"/>'
        if png
        else '<p class="warn">Equity chart unavailable (install extras: '
        "<code>pip install -e \".[analysis]\"</code>).</p>"
    )
    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    disclaimer = ""
    if meta.get("source") == "history":
        disclaimer = (
            '<p class="warn">History mode: spread is assumed and depth unlimited '
            "— treat as an optimistic upper bound.</p>"
        )
    return f"""<!doctype html><html><head><meta charset="utf-8">
<title>polybot backtest report</title><style>{_CSS}</style></head><body>
<h1>polybot backtest report</h1>
<p class="note">Generated {generated} · question: {html.escape(str(meta.get('question','')))}</p>
{disclaimer}
<h2>Summary</h2><table class="kv">{_stat_rows(result, meta)}</table>
<h2>Equity curve</h2>{chart}
<h2>Trades</h2>{_trades_table(result)}
</body></html>"""


def write_report(result: BacktestResult, meta: dict, out_dir: str | Path) -> Path:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    asset = str(meta.get("asset_id", "asset"))[:12]
    path = out / f"backtest-{asset}-{stamp}.html"
    path.write_text(build_report_html(result, meta), encoding="utf-8")
    return path
