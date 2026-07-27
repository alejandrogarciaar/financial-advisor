---
name: us-stocks-portfolio
description: Use when the user's request is scoped to the "💰 Portafolio" tab of the USStocks (Precio Justo) dashboard — recording/editing COP purchases, the held-position summary, or the "Contexto de valoración" cross-reference section. Points to the files that make up this tab so work stays scoped to them.
---

# Portafolio tab — context map

This skill doesn't prescribe steps — it just points to what "the Portafolio tab" is made of, so
work requested for this tab doesn't drift into Acciones/ETFs/Especulación code or require
re-discovering the file layout from scratch. `CLAUDE.md` (already in context) has the full
design rationale for everything listed here — read it before changing purchase semantics, this
is the one tab with real user-entered financial data (buy-only, no sale mechanism, whole
shares only) — this skill is only the map of where it lives.

## `app.py`

- `render_capital()` — the whole tab: purchase entry form, held-position table,
  `render_portfolio_total_hero()`, and the "📎 Contexto de valoración" section that reuses
  `STOCK_EVAL_CACHE_KEY` / `ETF_EVAL_CACHE_KEY` (populated by Acciones/ETFs earlier in the same
  run — see `_get_or_fetch()`) instead of re-fetching.
- `_cached_portfolio_price()` — current COP price for a held CDI, straight from yfinance
  (`PORTFOLIO_CDI_TICKERS[ticker]`), no FX conversion.
- `tab_capital` block near the bottom (`st.tabs()` call) — the tab wiring itself.

## `src/portfolio.py`

- `load_purchases()` / `save_purchases()` — persisted to `portfolio_data/purchases.json`
  (gitignored, real user data — never delete without asking, see memory).
- `validate_purchases()` — rejects fractional shares.
- `summarize_by_ticker()`, `commission_summary()`, `simulate_additional_purchase()` —
  aggregation and commission math. `DEFAULT_COMMISSION_COP` (7,438 COP) is the per-purchase
  default, editable per row, never retroactive.

## `src/config.py`

- `PORTFOLIO_CDI_TICKERS` — the only tickers selectable here (Colombian CDIs, not the plain USD
  `TICKERS`). `PORTFOLIO_CDI_UNDERLYING` — maps each CDI to the `TICKERS`/`ETF_TICKERS` company
  its "Contexto de valoración" card should reference.

## `src/data/fx.py`

- Exists but is **not** used by this tab on purpose (CDIs already quote in COP) — don't wire it
  in without re-reading why in `CLAUDE.md`.

## Known dead end

- A target-allocation/rebalancing section was tried here and removed — not wanted. If revisited
  it needs to be buy-only, since `purchases.json` only ever grows.
