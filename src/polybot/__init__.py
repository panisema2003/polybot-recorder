"""polybot — read-only research & data-capture layer for Polymarket.

Phase 1 of a deliberately staged build:
  1. (this) discover niche markets + record order books, paper-trade only;
  2. backtest a single edge with a tiny live stake;
  3. scale only if the edge is real and repeatable.

Nothing in this package can place an order. That is by design.
"""

__version__ = "0.1.0"
