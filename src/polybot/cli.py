"""Command-line interface.

    polybot discover [--top N] [--enrich K] [--save]
    polybot book SLUG
    polybot record (--slug SLUG ... | --discover-top N)

Phase 1 only: every command is read-only. Nothing here can place an order.
"""

from __future__ import annotations

import argparse
import asyncio

from rich.console import Console
from rich.table import Table

from polybot.backtest import Backtester, FairValueStrategy, FillModel, synthetic_ticks
from polybot.backtest.loader import load_ticks, resolve_asset
from polybot.basket import read_basket
from polybot.clients import ClobClient, GammaClient
from polybot.config import Settings
from polybot.discovery import discover, filter_min_days_to_resolution
from polybot.log import get_logger, setup_logging
from polybot.models import Market
from polybot.recorder import Recorder

console = Console()
log = get_logger(__name__)


def _fmt(x: float | None, nd: int = 3) -> str:
    return f"{x:.{nd}f}" if x is not None else "-"


async def cmd_discover(settings: Settings, args: argparse.Namespace) -> None:
    async with GammaClient(settings.api) as gamma, ClobClient(settings.api) as clob:
        scored = await discover(gamma, clob, settings.discovery, enrich_top=args.enrich)

    top = scored[: args.top]
    table = Table(title=f"Niche-market candidates (top {len(top)})", show_lines=False)
    table.add_column("#", justify="right", style="dim")
    table.add_column("Score", justify="right", style="bold green")
    table.add_column("Themes", style="cyan")
    table.add_column("Question", max_width=46)
    table.add_column("Mid", justify="right")
    table.add_column("Spread", justify="right", style="yellow")
    table.add_column("Liq $", justify="right")
    table.add_column("Vol24h $", justify="right")
    table.add_column("Days", justify="right")
    table.add_column("Slug", style="dim", max_width=28)

    for i, sm in enumerate(top, 1):
        m = sm.market
        table.add_row(
            str(i),
            _fmt(sm.score, 2),
            ",".join(sm.themes) or "-",
            m.question,
            _fmt(sm.midpoint, 3),
            _fmt(sm.spread, 3),
            f"{m.liquidity:,.0f}",
            f"{m.volume_24h:,.0f}",
            _fmt(m.days_to_resolution(), 1),
            m.slug,
        )
    console.print(table)

    if args.save:
        from polybot.storage import Storage

        storage = Storage(settings.db_path)
        for sm in top:
            storage.upsert_market(sm.market)
        storage.close()
        console.print(f"[green]Saved {len(top)} markets to {settings.db_path}[/]")


async def cmd_book(settings: Settings, args: argparse.Namespace) -> None:
    async with GammaClient(settings.api) as gamma, ClobClient(settings.api) as clob:
        market = await gamma.fetch_market_by_slug(args.slug)
        if market is None:
            console.print(f"[red]No market found for slug '{args.slug}'[/]")
            return
        console.print(f"[bold]{market.question}[/]")
        for tok in market.tokens:
            book = await clob.get_book(tok.token_id)
            if book is None:
                console.print(f"  {tok.outcome}: [red]no book[/]")
                continue
            console.print(
                f"  [cyan]{tok.outcome}[/]: bid {_fmt(book.best_bid.price if book.best_bid else None)}"
                f" / ask {_fmt(book.best_ask.price if book.best_ask else None)}"
                f"  mid {_fmt(book.midpoint)}  spread {_fmt(book.spread)}"
            )


async def cmd_record(settings: Settings, args: argparse.Namespace) -> None:
    markets: list[Market] = []
    slugs: list[str] = list(args.slug)
    if args.basket:
        try:
            slugs = read_basket(args.basket)
        except OSError as exc:
            console.print(f"[red]Could not read basket '{args.basket}': {exc}[/]")
            return
        console.print(f"[dim]Loaded {len(slugs)} slug(s) from {args.basket}[/]")

    async with GammaClient(settings.api) as gamma, ClobClient(settings.api) as clob:
        if args.discover_top:
            scored = await discover(
                gamma, clob, settings.discovery, enrich_top=max(args.discover_top, 30)
            )
            markets = [sm.market for sm in scored[: args.discover_top]]
        else:
            for slug in slugs:
                m = await gamma.fetch_market_by_slug(slug)
                if m is None:
                    console.print(f"[yellow]Skipping unknown slug '{slug}'[/]")
                else:
                    markets.append(m)

    # Resolution-horizon guard: drop markets that would resolve mid-run.
    if args.min_days_to_resolution > 0:
        markets, skipped = filter_min_days_to_resolution(
            markets, args.min_days_to_resolution
        )
        for m in skipped:
            d = m.days_to_resolution()
            console.print(
                f"[yellow]Skipping[/] (resolves in "
                f"{('%.1f' % d) if d is not None else '?'}d "
                f"< {args.min_days_to_resolution}d): {m.slug}"
            )

    if not markets:
        console.print("[red]No markets selected to record.[/]")
        return

    console.print(f"[green]Recording {len(markets)} market(s). Ctrl-C to stop.[/]")
    for m in markets:
        d = m.days_to_resolution()
        days = f"{d:.0f}d" if d is not None else "?"
        console.print(f"  - {m.question[:64]}  [dim]({m.slug}) ~{days}[/]")

    recorder = Recorder(settings, markets)
    try:
        await recorder.run()
    except asyncio.CancelledError:
        pass


async def _ticks_from_history(settings: Settings, args: argparse.Namespace):
    """Resolve a token and synthesise ticks from Polymarket price history."""
    async with GammaClient(settings.api) as gamma, ClobClient(settings.api) as clob:
        asset_id = args.asset
        if asset_id is None:
            market = await gamma.fetch_market_by_slug(args.slug)
            if market is None:
                return None, []
            tok = next(
                (t for t in market.tokens if t.outcome.lower() == args.outcome.lower()),
                market.yes_token,
            )
            if tok is None:
                return None, []
            asset_id = tok.token_id
        points = await clob.get_price_history(
            asset_id, interval=args.interval, fidelity=args.fidelity
        )
    ticks = synthetic_ticks(
        points, asset_id, assumed_spread=args.assumed_spread, depth=args.depth
    )
    return asset_id, ticks


async def cmd_backtest(settings: Settings, args: argparse.Namespace) -> None:
    if args.source == "history":
        asset_id, ticks = await _ticks_from_history(settings, args)
        if not asset_id:
            console.print(f"[red]Could not resolve a token for '{args.slug}'.[/]")
            return
        if not ticks:
            console.print("[red]No price history returned for that token.[/]")
            return
    else:  # recorded
        db = settings.db_path
        asset_id = args.asset or resolve_asset(db, args.slug, args.outcome)
        if not asset_id:
            console.print(
                f"[red]Could not resolve an asset.[/] Pass --asset, or record "
                f"'{args.slug}' first so the tokens table is populated "
                f"(or use --source history)."
            )
            return
        ticks = load_ticks(db, asset_id)
        if not ticks:
            console.print(f"[red]No recorded ticks for asset {asset_id[:14]}…[/]")
            return

    strategy = FairValueStrategy(
        asset_id=asset_id,
        fair=args.fair,
        edge=args.edge,
        order_size=args.size,
        max_position=args.max_pos,
    )
    bt = Backtester(capital=args.capital, fill_model=FillModel(fee_bps=args.fee_bps))
    resolution = {asset_id: args.resolve} if args.resolve is not None else None
    result = bt.run(ticks, strategy, resolution)

    src_note = (
        f"history (assumed spread {args.assumed_spread})"
        if args.source == "history"
        else "recorded book (real spread)"
    )
    console.print(
        f"[bold]Backtest[/] [{args.source}] asset {asset_id[:18]}…  "
        f"({len(ticks)} ticks, {src_note})  fair={args.fair}  edge={args.edge}"
    )
    console.print(result.summary())
    if args.source == "history":
        console.print(
            "[yellow]Reminder:[/] history mode is an optimistic first pass - the "
            "spread is assumed and depth is unlimited. Confirm survivors against "
            "live-recorded books (--source recorded)."
        )
    if not result.settled:
        console.print(
            "[yellow]Note:[/] no --resolve given, so PnL is marked-to-market at "
            "the last mid (open position not realised). Pass --resolve 1 or 0 "
            "for a resolved market to see true settled PnL."
        )

    if args.report:
        from polybot.backtest.report import write_report

        meta = {
            "asset_id": asset_id,
            "source": args.source,
            "fair": args.fair,
            "edge": args.edge,
            "question": getattr(args, "slug", None) or asset_id,
        }
        path = write_report(result, meta, args.report_dir)
        console.print(f"[green]Report written:[/] {path}")


async def cmd_dashboard(settings: Settings, args: argparse.Namespace) -> None:
    try:
        import uvicorn

        from polybot.dashboard.app import create_app
    except ImportError:
        console.print(
            "[red]Dashboard extras not installed.[/] Run: "
            'pip install -e ".[dashboard]"'
        )
        return
    if not settings.db_path.exists():
        console.print(f"[yellow]No database at {settings.db_path} yet.[/]")
    app = create_app(settings.db_path, refresh_s=args.refresh)
    console.print(
        f"[green]Dashboard on http://{args.host}:{args.port}[/]  "
        f"(reading {settings.db_path})"
    )
    if args.host == "127.0.0.1":
        console.print(
            "[dim]Bound to localhost. On EC2, tunnel from your laptop:\n"
            f"  ssh -i key.pem -L {args.port}:localhost:{args.port} ubuntu@HOST\n"
            f"then open http://localhost:{args.port}[/]"
        )
    config = uvicorn.Config(app, host=args.host, port=args.port, log_level="warning")
    await uvicorn.Server(config).serve()


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="polybot", description=__doc__)
    p.add_argument("--config", help="Path to config.yaml (overrides POLYBOT_CONFIG)")
    sub = p.add_subparsers(dest="command", required=True)

    d = sub.add_parser("discover", help="Find & rank niche markets")
    d.add_argument("--top", type=int, default=25, help="Rows to display")
    d.add_argument("--enrich", type=int, default=60, help="Top-K to fetch live books for")
    d.add_argument("--save", action="store_true", help="Save shown markets to the DB")
    d.set_defaults(func=cmd_discover)

    b = sub.add_parser("book", help="Show the current order book for one market")
    b.add_argument("slug", help="Market slug")
    b.set_defaults(func=cmd_book)

    r = sub.add_parser("record", help="Record order books to SQLite")
    g = r.add_mutually_exclusive_group(required=True)
    g.add_argument("--slug", action="append", default=[], help="Market slug (repeatable)")
    g.add_argument(
        "--discover-top", type=int, metavar="N", help="Record the top-N discovered markets"
    )
    g.add_argument(
        "--basket", metavar="FILE",
        help="Read slugs from a basket file (one slug per line; see baskets/)",
    )
    r.add_argument(
        "--min-days-to-resolution", type=float, default=0.0, metavar="N",
        help="Skip markets resolving in < N days (guards against mid-run staleness). "
             "0 = no guard.",
    )
    r.set_defaults(func=cmd_record)

    bt = sub.add_parser("backtest", help="Replay a strategy over recorded data")
    sel = bt.add_mutually_exclusive_group(required=True)
    sel.add_argument("--slug", help="Market slug (uses recorded tokens table)")
    sel.add_argument("--asset", help="Token id to backtest directly")
    bt.add_argument("--outcome", default="Yes", help="Outcome for --slug (default Yes)")
    bt.add_argument("--fair", type=float, required=True, help="Your fair probability 0..1")
    bt.add_argument("--edge", type=float, default=0.02, help="Min mispricing to act")
    bt.add_argument("--capital", type=float, default=500.0, help="Capital deployed")
    bt.add_argument("--size", type=float, default=50.0, help="Shares per order")
    bt.add_argument("--max-pos", type=float, default=200.0, help="Max net shares")
    bt.add_argument("--fee-bps", type=float, default=0.0, help="Fee bps of notional")
    bt.add_argument(
        "--resolve", type=float, default=None,
        help="Settlement price (1=YES won, 0=lost) for true realised PnL",
    )
    bt.add_argument(
        "--source", choices=["recorded", "history"], default="recorded",
        help="recorded=live books from SQLite (real spread); "
             "history=Polymarket price history online (assumed spread)",
    )
    bt.add_argument("--interval", default="max", help="history: look-back window")
    bt.add_argument("--fidelity", type=int, default=60, help="history: minutes per point")
    bt.add_argument(
        "--assumed-spread", type=float, default=0.04,
        help="history: full bid-ask spread to assume around each price",
    )
    bt.add_argument("--depth", type=float, default=100_000.0, help="history: assumed depth")
    bt.add_argument(
        "--report", action="store_true",
        help="Write a self-contained HTML report (equity curve + trades)",
    )
    bt.add_argument("--report-dir", default="reports", help="Where to write the report")
    bt.set_defaults(func=cmd_backtest)

    dash = sub.add_parser("dashboard", help="Live web view of the recorder (read-only)")
    dash.add_argument("--host", default="127.0.0.1", help="Bind host (default localhost)")
    dash.add_argument("--port", type=int, default=8000, help="Bind port")
    dash.add_argument("--refresh", type=int, default=15, help="Auto-refresh seconds")
    dash.set_defaults(func=cmd_dashboard)
    return p


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    settings = Settings.load(args.config)
    setup_logging(settings.log_level)
    try:
        asyncio.run(args.func(settings, args))
    except KeyboardInterrupt:
        console.print("\n[dim]Interrupted.[/]")


if __name__ == "__main__":
    main()
