---
name: us-stocks-portfolio
description: Use when the user's request is scoped to the "💰 Portafolio" tab of the USStocks (Precio Justo) dashboard — recording/editing COP purchases, the held-position summary, or the "Contexto de valoración" cross-reference section. Points to the files that make up this tab so work stays scoped to them.
---

# Portafolio tab — context map

This skill doesn't prescribe steps — it just points to what "the Portafolio tab" is made of, so
work requested for this tab doesn't drift into Acciones/ETFs/Especulación/Cripto code or
require re-discovering the file layout from scratch. `references/design-history.md` has the
full design rationale for everything listed here — read it before changing purchase
semantics, this is the one tab with real user-entered financial data (buy-only, no sale
mechanism, whole shares only). That narrative used to live in `CLAUDE.md` directly, then in
this file's own "Design history" section, then out to `references/design-history.md` (loaded
on demand instead of on every invocation of this skill — see the `token-audit` skill).

## `src/ui/portfolio.py`

App code used to be one 2821-line `app.py`; it's split into `src/ui/*.py` (one file per tab,
plus `shared.py` for cross-tab plumbing) with `app.py` now just the thin entry point (page
config + tab wiring). This tab's code lives in `src/ui/portfolio.py`:

- `render_capital()` — the whole tab: purchase entry form, held-position table,
  `render_portfolio_total_hero()`, and the "📎 Contexto de valoración" section that reuses
  `STOCK_EVAL_CACHE_KEY` / `ETF_EVAL_CACHE_KEY` (populated by Acciones/ETFs earlier in the same
  run, both in `src/ui/shared.py` — see `_get_or_fetch()`) instead of re-fetching. That same
  section also shows each holding's drawdown-from-1y-high line (`DRAWDOWN_VALIDATED_BUCKETS`,
  defined right above `render_capital()`) — the static gate of which (ticker, drawdown-bucket)
  pairs are validated; don't add a ticker/bucket to it without re-running the same out-of-sample
  check documented below — and captures `underlying_prices: dict[str, list[dict]]` while it
  loops, so the 3 sections right after it don't need to re-fetch anything.
- Right after "Contexto de valoración", 3 more sections in the same `else:` branch (need
  `purchases` non-empty): "🥧 Diversificación" (bar chart, `PORTFOLIO_CDI_SECTOR`), "📈 Retorno
  y riesgo del portafolio" (`build_synthetic_portfolio_series()` + the existing
  `evaluate_risk_return()`), "🎯 Proyección de meta" (`project_future_value()`, depends on the
  risk/return section's output). None of these three need OOS validation — see "Design history"
  below for why (descriptive composition + deterministic financial math, not a price prediction)
  — but the goal projection carries a strong disclaimer anyway since a dollar figure reads as a
  forecast.

## `src/ui/shared.py`

- `_cached_portfolio_price()` — current COP price for a held CDI, straight from yfinance
  (`PORTFOLIO_CDI_TICKERS[ticker]`), no FX conversion. Also used by ETFs (BVC price reference),
  so it lives here, not in `portfolio.py`.

## `app.py`

- Just the tab wiring now (`st.tabs()` call + one `with tab_capital:` block importing
  `render_capital` from `src/ui/portfolio.py`) — and it stays **last** in that wiring specifically
  so `render_capital()` can reuse `STOCK_EVAL_CACHE_KEY`/`ETF_EVAL_CACHE_KEY` populated by
  Acciones/ETFs earlier in the same script run.

## `src/portfolio.py`

- `load_purchases()` / `save_purchases()` — persisted to `portfolio_data/purchases.json`
  (gitignored, real user data — never delete without asking, see memory).
- `validate_purchases()` — rejects fractional shares.
- `summarize_by_ticker()`, `commission_summary()`, `simulate_additional_purchase()` —
  aggregation and commission math. `DEFAULT_COMMISSION_COP` (7,438 COP) is the per-purchase
  default, editable per row, never retroactive.
- `build_synthetic_portfolio_series()` — combines each holding's underlying (USD) price history
  into one weighted synthetic index (today's allocation applied across the whole lookback, not a
  real historical reconstruction), feeds `evaluate_risk_return()` (`src/valuation/risk_return.py`,
  untouched — no new risk math).
- `project_future_value()` — standard future-value formula (lump sum + monthly annuity,
  compounded monthly) for "🎯 Proyección de meta". Pure deterministic arithmetic; the caller
  decides what rate to feed it.

## `src/drawdown_dca.py`

- `current_drawdown_from_high()` (bare %, used internally by `current_bucket_reaction()`),
  `current_drawdown_snapshot()` (the one the UI actually calls — also returns the reference
  prices and the trailing-high date, for the "AMZN (USD): $231 hoy vs. máximo de $275 el
  2026-05-06" context line), `classify_drawdown_bucket()`, `compute_drawdown_bucket_reactions()`,
  `current_bucket_reaction()` — the reusable computation behind the drawdown-bucket line (mirrors
  `speculation.py`'s regime-reaction functions, same shape). For stocks, runs on
  `ev.historical_prices` already in memory; `ETFEvaluation` does **not** carry that field (a real
  bug this session), so the ETF branch fetches separately via `_cached_historical_prices()`. This
  is a **deliberate exception** to the project's no-timing-language rule (the second one, after
  Especulación) — see `CLAUDE.md` for why it's scoped to Portfolio and why the language must stay
  descriptive, not imperative.

## `src/config.py`

- `PORTFOLIO_CDI_TICKERS` — the only tickers selectable here (Colombian CDIs, not the plain USD
  `TICKERS`). `PORTFOLIO_CDI_UNDERLYING` — maps each CDI to the `TICKERS`/`ETF_TICKERS` company
  its "Contexto de valoración" card should reference (also the key `DRAWDOWN_VALIDATED_BUCKETS`
  is indexed by — `"CSPXCO"`, not `"CSPX"`, for the ETF). `PORTFOLIO_CDI_SECTOR` — static
  CDI→sector map for "🥧 Diversificación" (not sourced from either provider — see `CLAUDE.md`
  for why).

## `src/data/fx.py`

- Exists but is **not** used by this tab on purpose (CDIs already quote in COP) — don't wire it
  in without re-reading why in `CLAUDE.md`.

## Known dead end

- A target-allocation/rebalancing section was tried here and removed — not wanted. If revisited
  it needs to be buy-only, since `purchases.json` only ever grows.

## Design history

Full rationale for every design decision in this tab — see `references/design-history.md`. Read
it before changing purchase semantics, the drawdown-bucket accumulation zone, or the
diversification/risk-return/goal-projection sections.
