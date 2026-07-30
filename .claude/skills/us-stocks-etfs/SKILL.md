---
name: us-stocks-etfs
description: Use when the user's request is scoped to the "🧺 ETFs" tab of the USStocks (Precio Justo) dashboard — the ETF list or a single ETF's detail page. Points to the files that make up this tab so work stays scoped to them.
---

# ETFs tab — context map

This skill doesn't prescribe steps — it just points to what "the ETFs tab" is made of, so work
requested for this tab doesn't drift into Acciones/Portafolio/Especulación code or require
re-discovering the file layout from scratch. `CLAUDE.md` (already in context) has the full
design rationale for everything listed here; this is only the map of where it lives.

## `src/ui/etfs.py`

App code used to be one 2821-line `app.py`; it's split into `src/ui/*.py` (one file per tab,
plus `shared.py` for cross-tab plumbing) with `app.py` now just the thin entry point (page
config + tab wiring). This tab's code lives in `src/ui/etfs.py`:

- `render_etf_list()` — the ETF cards/list view; prefetches all `ETF_TICKERS` concurrently via
  `_get_or_fetch(ETF_EVAL_CACHE_KEY, ...)`, same pattern as Acciones.
- `render_etf_detail(ticker)` — single-ETF detail page.

## `src/ui/shared.py`

- `ETF_EVAL_CACHE_KEY`, `_cached_etf_evaluation()`, `_cached_portfolio_price()` — cache keys/
  functions that Portafolio's "Contexto de valoración" section (and this tab's own BVC-price
  reference) also read from. Genuinely shared across ETFs/Portafolio (confirmed by usage) —
  that's why they aren't in `etfs.py` alongside the rest of this tab's code.

## `app.py`

- Just the tab wiring now (`st.tabs()` call + one `with tab_etfs:` block importing
  `render_etf_list`/`render_etf_detail` from `src/ui/etfs.py`).

## `src/valuation/`

- `etf_analysis.py` — `evaluate_etf()`, `REFERENCE_PE`. ETFs get risk/return metrics, not the 6
  stock valuation formulas (no per-share financial statements to value).
- `risk_return.py` — CAGR/volatility/Sharpe/drawdown, shared with the stocks tab's
  `fair_value.py` so the math isn't duplicated. Filters stray `NaN` closes before computing
  anything (the `CSPX.L` bug mentioned in `CLAUDE.md`).

## `src/config.py`

- `ETF_TICKERS` — the universe for this tab (single source of truth).

## `src/data/`

- Same provider abstraction as Acciones (`fmp_client.py` / `yfinance_client.py`, `cache.py`,
  `errors.py`) — ETFs go through the same `get_historical_prices()` etc.
