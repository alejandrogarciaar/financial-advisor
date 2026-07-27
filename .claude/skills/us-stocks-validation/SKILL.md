---
name: us-stocks-validation
description: Use when the user's request is scoped to the "📊 Validación" tab of the USStocks (Precio Justo) dashboard — the in-UI backtest or the per-ticker verdict-history log/chart. Points to the files that make up this tab so work stays scoped to them.
---

# Validación tab — context map

This skill doesn't prescribe steps — it just points to what "the Validación tab" is made of,
so work requested for this tab doesn't drift into Acciones/ETFs/Portafolio/Especulación code or
require re-discovering the file layout from scratch. `CLAUDE.md` (already in context) has the
full design rationale for everything listed here — including why the backtest is button-gated,
why `years_ago` isn't a configurable control, and why the verdict-history chart hides itself
below 2 data points — this skill is only the map of where it lives.

## `app.py`

- `render_validation()` — the whole tab: the backtest button + results table + caveats
  (`st.info`), and the verdict-history ticker selectbox + chart + table.
- `_cached_backtest_ticker()` — wraps `src/backtest.py`'s `backtest_ticker()`,
  `@st.cache_data(ttl=86400)` (a day, not the usual 900s — financials don't move intraday).
  Submitted to `_parallel_fetch()` per ticker on button click, not auto-run.
- `BACKTEST_YEARS_AGO` — fixed at 1, not a UI control (see `src/backtest.py`'s docstring for
  why `years_ago=2` doesn't work for any ticker).
- `_maybe_record_verdict()` / the `record_verdict()` call sites — these actually live in
  `render_list()` / `render_detail()` (Acciones), not here; this tab only *reads*
  `load_verdict_history()`. See `us-stocks-stocks` skill if the recording logic itself needs to
  change.
- `VERDICT_COLOR` / `VERDICT_LABEL` — reused for the chart, not redefined here; keep in sync
  with `triangulation_badge()`'s colors if either changes.

## `src/verdict_history.py`

- `record_verdict()` (writer, called from Acciones) / `load_verdict_history()` (reader, called
  here) — one entry per ticker per calendar day in `app_data/verdict_history.json`
  (gitignored, not reconstructible from an API — same reasoning as `src/preferences.py`).

## `src/backtest.py`

- `backtest_ticker()` — per-ticker backtest logic, called directly (not through
  `run_backtest()`'s loop) so `app.py` can parallelize it via `_parallel_fetch()`. Read the
  module docstring before changing `years_ago` handling or the caveats shown in the UI — they're
  real, documented data limits (yfinance EPS history gaps, today's beta not the historical one,
  small survivorship-biased sample), not boilerplate disclaimers.

## `src/config.py`

- `TICKERS` — the universe for both sections (ETFs are excluded: `evaluate_etf()` has no
  cheap/expensive verdict to backtest or log).
