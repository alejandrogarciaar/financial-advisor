---
name: us-stocks-stocks
description: Use when the user's request is scoped to the "📈 Acciones" tab of the USStocks (Precio Justo) dashboard — the 6-formula valuation cards, ticker list/filter, or a single ticker's detail page. Points to the files that make up this tab so work stays scoped to them.
---

# Acciones tab — context map

This skill doesn't prescribe steps — it just points to what "the Acciones tab" is made of, so
work requested for this tab doesn't drift into ETFs/Portafolio/Especulación code or require
re-discovering the file layout from scratch. `CLAUDE.md` (already in context) has the full
design rationale for everything listed here; this is only the map of where it lives.

## `app.py`

- `render_list()` — the ticker cards/list view, the multiselect filter (`key="ticker_filter"`,
  persisted via `src/preferences.py`), `render_options_bar()`, `render_method_grid()` /
  `render_method_card()`.
- `render_detail(ticker)` — single-ticker detail page: valuation verdict, quality/solvency
  filters, trend section, `render_sticky_price()` call (shared helper, also used by
  Especulación — don't fork it, extend the shared one).
- `STOCK_EVAL_CACHE_KEY`, `_cached_evaluation()`, `_parallel_fetch()` / `_get_or_fetch()` — the
  concurrent-fetch + cross-tab dedup plumbing (ETFs and Portafolio also read from this cache
  key when a held ticker's underlying was already evaluated here this run).
- `tab_acciones` block near the bottom (`st.tabs()` call) — the tab wiring itself.

## `src/valuation/`

- `fair_value.py` — orchestration: `evaluate_ticker()` (I/O) / `_evaluate_from_data()` (pure),
  `summarize_signals()`, `SIGNAL_FAMILIES`, `ZONE_THRESHOLDS`, `quality_context_note()`,
  `trend_context_note()`, `compare_providers()`.
- `dcf.py`, `multiples.py`, `book_value.py`, `growth.py`, `graham.py`, `graham_growth.py` — the
  6 formulas across the 3 families (cash flow / book value / earnings multiples).
- `quality.py`, `solvency.py`, `analyst_view.py` — filters shown alongside the verdict but
  excluded from `summarize_signals()`'s vote.
- `lynch_category.py` — growth-formula applicability warning label.
- `trend.py` — EMA-55 / SMA-50/200 momentum filter, also reused by Especulación's regime logic.
- `risk_return.py` — CAGR/volatility/Sharpe/drawdown, shared with `etf_analysis.py`.

## `src/data/`

- `fmp_client.py` / `yfinance_client.py` — the provider abstraction `fair_value.py` is agnostic
  to; `cache.py` (disk cache + stale fallback); `errors.py` (`DataError`).

## `src/config.py`

- `TICKERS` — the universe for this tab (single source of truth; nothing else hardcodes a count).

## `src/preferences.py`

- Ticker filter persistence (`app_data/preferences.json`) — read `CLAUDE.md`'s note on the
  `key=` vs. `default=` widget-identity bug before touching the multiselect wiring.

## `src/backtest.py`

- Not part of the UI, but validates this tab's triangulation verdict against historical
  returns — relevant if a formula or the verdict logic changes.
