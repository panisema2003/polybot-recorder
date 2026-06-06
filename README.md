# polybot

A **read-only research & data-capture layer for Polymarket**, built as Phase 1
of a deliberately staged trading project.

## Why this exists (the strategy in one paragraph)

Pure cross-venue arbitrage on prediction markets is, in 2026, a latency war we
can't win: arb windows last ~2.7s and ~73% of the profit is taken by sub-100ms
bots. Median spreads (~0.3%) don't cover costs, and capital is locked until
resolution. So this project does **not** chase arbitrage. It hunts a *statistical
edge in niche, low-attention markets* where a better probability estimate — not
speed — is the moat. Before risking a cent, we build the boring, valuable thing:
a tool to **find** those markets and **record** their order books so an edge can
actually be measured.

```
Phase 1  (this repo)  discover niche markets + record books, paper only
Phase 2               backtest ONE edge with a tiny live stake ($100–200)
Phase 3               scale only if the edge is real and repeatable
```

> **Safety by design:** nothing in this codebase can place an order. It only
> reads Polymarket's public Gamma (metadata) and CLOB (order book) endpoints,
> which need no authentication.

## Install

```powershell
cd polymarket
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
```

## Use

```powershell
# 1. Find & rank niche-market candidates (live order books for the top 60)
polybot discover --top 25

# 2. Inspect one market's book by slug
polybot book new-rhianna-album-before-gta-vi-926

# 3. Record order books to SQLite (Ctrl-C to stop)
polybot record --slug some-market-slug --slug another-slug
polybot record --discover-top 10        # auto-pick the top 10 niche markets
polybot record --basket baskets/colombia-runoff-2026.txt   # curated, versioned list
#    --min-days-to-resolution N  skips markets resolving in < N days, so a long
#    run never ends up recording markets that settle (go dead) mid-run:
polybot record --basket baskets/colombia-runoff-2026.txt --min-days-to-resolution 1

# 4. Inspect what you've captured (needs the analysis extras)
py scripts/analyze.py --list
py scripts/analyze.py --slug some-market-slug --out reports   # writes PNGs

# 5. Backtest a fair-value edge.
#    "I think YES is really 0.80; the market shows ~0.61" — does it pay?
#    (a) recorded books = real captured spread (the honest test):
polybot backtest --slug some-market-slug --outcome Yes --fair 0.80 --resolve 1
#    (b) price history = instant, no waiting, but spread is ASSUMED (optimistic):
polybot backtest --slug some-market-slug --fair 0.80 --source history \
                 --assumed-spread 0.04 --resolve 1
```

### Two backtest data sources

| `--source` | Data | Spread | Use it for |
|------------|------|--------|------------|
| `recorded` (default) | live books captured by `record`, from SQLite | **real** | the honest verdict |
| `history` | Polymarket `/prices-history`, fetched online | **assumed** | instant first-pass while live data accrues |

Price history is *price only* — no spread or depth — so `history` mode wraps
each price in an assumed bid-ask and unlimited depth. It's an **optimistic
upper bound**; an edge that survives only here hasn't been proven. Always
confirm survivors on `--source recorded`.

> `--resolve 1` (YES won) / `--resolve 0` (lost) settles the position for *true*
> realised PnL; omit it to mark-to-market at the last mid. `--slug` resolution
> needs the `tokens` table, which is populated whenever you `record` a market.

Captured data lands in `data/polybot.db` (SQLite). Analyze it later with pandas:

```python
import sqlite3, pandas as pd
con = sqlite3.connect("data/polybot.db")
df = pd.read_sql("SELECT * FROM book_top ORDER BY recv_ms", con)
```

## How discovery scores a market

Hard filters (drop if failed): must be CLOB-tradeable, ≥ min liquidity, 24h
volume inside a band (not dead, not bot-saturated), resolution within a sane
window. Then a weighted, normalised (0..1) score over:

| Component       | Signal                                              |
|-----------------|-----------------------------------------------------|
| `theme_match`   | in our circle of competence (Colombia/LatAm/etc.)   |
| `spread`        | wider live spread ⇒ more pricing slack to capture   |
| `low_attention` | lower 24h volume ⇒ fewer bots camping it            |
| `liquidity`     | enough resting size to enter/exit (log-scaled)      |
| `time_decay`    | nearer resolution ⇒ less capital lock-up            |

Tune the weights, filters, and themes in [`config.yaml`](config.yaml).

## Layout

```
src/polybot/
  config.py        settings loaded from config.yaml + .env
  models.py        Market / OrderBook (defensive parsers for the real API)
  clients/
    gamma.py       market discovery (REST)
    clob.py        order books (REST)
    ws.py          real-time market channel (WebSocket, reconnect + keepalive)
  discovery.py     niche filtering + scoring
  storage.py       SQLite persistence (markets, tokens, book_top, snapshots, trades)
  recorder.py      WS + REST -> SQLite order-book recorder
  backtest/        Phase 2 harness (replay recorded data, no live trading)
    types.py       Tick / Order / Fill / Side
    portfolio.py   average-cost Position + Portfolio (cash, PnL, settlement)
    fills.py       conservative top-of-book taker fill model (+ optional fees)
    strategy.py    Strategy ABC + FairValueStrategy (the worked example edge)
    engine.py      Backtester + BacktestResult (PnL, drawdown, summary)
    loader.py      load ticks / resolve slug+outcome -> asset id, from SQLite
    history.py     synthesise ticks from price history (assumed-spread mode)
  basket.py        read version-controlled basket files (slug lists)
  cli.py           `polybot discover | book | record | backtest`
baskets/           curated market lists for `record --basket` (in version control)
scripts/
  analyze.py       pandas/matplotlib: list captures, plot mid+spread -> PNG
deploy/
  polybot-recorder.service   systemd unit for the long-running cloud recorder
  README.md                  VPS setup, backup, verification
tests/             pytest (parsing, filtering, scoring, and money math)
```

## Running the recorder long-term (cloud)

For the real 1–2 week data collection, run the recorder on a cheap VPS under
systemd so it survives reboots/crashes/disconnects. See
[`deploy/README.md`](deploy/README.md).

## Tests

```powershell
pytest -q
```

## Do you need a Polymarket API key?

**Not for discovery, recording, analysis, or backtesting** — those use public
read endpoints. You only need credentials to *place live orders* (the final
step of Phase 2), and there's nothing to buy:

1. Create and fund a **Polygon wallet** (USDC on Polygon) — your trading account.
2. The CLOB API credentials are **derived from that wallet**, not purchased:
   `py-clob-client` signs a message with your private key and the exchange
   returns an API key / secret / passphrase ("L2" creds).
3. Those go in `.env` (see `.env.example`). Never commit them.

We don't set this up until the backtest shows a real, repeatable edge.

## Phase 2 status

The backtest harness is built and tested. Live order placement (signing via
`py-clob-client`) is intentionally **not** wired yet — first collect a couple
weeks of recorded data on ~10 niche markets, backtest your probability
estimates against it, and only then risk a tiny live stake ($100–200).
