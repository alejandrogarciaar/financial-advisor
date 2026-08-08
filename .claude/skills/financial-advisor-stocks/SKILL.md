---
name: financial-advisor-stocks
description: Use when the user's request is scoped to the "📈 Acciones" tab of the financial-advisor (Precio Justo) dashboard — the 6-formula valuation cards, ticker list/filter, or a single ticker's detail page. Points to the files that make up this tab so work stays scoped to them.
---

# Acciones tab — context map

This skill doesn't prescribe steps — it just points to what "the Acciones tab" is made of, so
work requested for this tab doesn't drift into ETFs/Portafolio/Especulación code or require
re-discovering the file layout from scratch. `CLAUDE.md` (already in context) has the full
design rationale for everything listed here; this is only the map of where it lives.

## `src/ui/stocks.py`

App code used to be one 2821-line `app.py`; it's split into `src/ui/*.py` (one file per tab,
plus `shared.py` for cross-tab plumbing) with `app.py` now just the thin entry point (page
config + tab wiring). This tab's code lives in `src/ui/stocks.py`:

- `render_list()` — the ticker cards/list view, the multiselect filter (`key="ticker_filter"`,
  persisted via `src/preferences.py`), `render_options_bar()`, `render_method_grid()` /
  `render_method_card()`.
- `render_detail(ticker)` — single-ticker detail page: valuation verdict, quality/solvency
  filters, trend section, `render_sticky_price()` call (shared helper in `src/ui/shared.py`,
  also used by ETFs/Especulación/Cripto — don't fork it, extend the shared one).
- `_maybe_record_verdict()` — called in both `render_list()` and `render_detail()` right after
  `summarize_signals()`, feeds the Validación tab's verdict-history log (`src/verdict_history.py`).
  Don't add a third call site without also considering whether it should count as "the user saw
  this ticker's verdict today."

## `src/ui/shared.py`

- `STOCK_EVAL_CACHE_KEY`, `_cached_evaluation()`, `_parallel_fetch()` / `_get_or_fetch()` — the
  concurrent-fetch + cross-tab dedup plumbing (ETFs and Portafolio also read from this cache
  key when a held ticker's underlying was already evaluated here this run). Genuinely shared
  (confirmed by real usage, not guessed) across Acciones/ETFs/Portafolio — that's why it isn't
  in `stocks.py` alongside the rest of this tab's code.

## `app.py`

- Just the tab wiring now (`st.tabs()` call + one `with tab_acciones:` block importing
  `render_list`/`render_detail` from `src/ui/stocks.py`) — page config and session_state init,
  nothing tab-specific.

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

## `src/verdict_history.py`

- `record_verdict()` / `load_verdict_history()` — one JSON entry per ticker per calendar day
  (`app_data/verdict_history.json`), fed from Acciones (see `_maybe_record_verdict()` above),
  displayed in the Validación tab, not this one. Touch this file if the schema of what gets
  logged needs to change; the recording call sites live in `src/ui/stocks.py`, not here.

## `src/backtest.py`

- `backtest_ticker()` is also called live from the Validación tab now (behind a button, via
  `_cached_backtest_ticker()` in `src/ui/validation.py`) — it's not console-only anymore.
  `run_backtest()` (the console-loop wrapper) is still the one to use for a quick CLI sanity
  check; `src/ui/validation.py` calls `backtest_ticker()` directly per ticker so it can go
  through `_parallel_fetch()` (`src/ui/shared.py`).
