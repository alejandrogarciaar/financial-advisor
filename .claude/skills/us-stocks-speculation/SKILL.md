---
name: us-stocks-speculation
description: Use when the user's request is scoped to the "🎲 Especulación" tab of the USStocks (Precio Justo) dashboard — RSI, support/resistance, MACD, Bollinger Bands, the DCA suggestion box, or the BTC/ETH/SOL crypto tickers. Points to the files that make up this tab so work stays scoped to them.
---

# Especulación tab — context map

This skill doesn't prescribe steps — it just points to what "the Especulación tab" is made of,
so work requested for this tab doesn't drift into Acciones/ETFs/Portafolio code or require
re-discovering the file layout from scratch. `CLAUDE.md` (already in context) has the full
design rationale for everything listed here — including two features that were tried and
removed after failing out-of-sample validation (Fibonacci levels, a régimen/horizonte table) —
read it before adding a new speculative signal; this skill is only the map of where it lives.

## `app.py`

- `render_speculation()` — the whole tab: ticker selectbox (`TICKERS +
  SPECULATION_CRYPTO_TICKERS`), price display, EMA/SMA section, support/resistance section
  (`render_levels_chart()`, driven by `SPECULATION_CHART_VIEWS` + the `st.segmented_control`),
  MACD, Bollinger Bands, and the "📋 Plan de DCA sugerido" box.
- `render_sticky_price()` — shared helper (also used by Acciones' `render_detail()`) for the
  floating price card; don't fork a speculation-only copy.
- `REGIME_VALIDATED_COMBOS`, `REGIME_RSI_OVERBOUGHT_VALIDATED_HORIZONS` — static lookups from
  one-off out-of-sample backtests (not recomputed live — see `CLAUDE.md` for why). Only update
  these by re-running the same chronological train/test methodology, never by loosening the
  threshold to make a ticker pass.
- `_cached_historical_prices()` — resolves `SPECULATION_CRYPTO_TICKERS.get(ticker, ticker)` to
  the real yfinance symbol (`BTC-USD`, etc.) at this one call only; every other use of `ticker`
  in this function keeps the bare display symbol.
- `tab_especulacion` block near the bottom (`st.tabs()` call) — the tab wiring itself.

## `src/speculation.py`

- `compute_rsi()` / `compute_rsi_series()` — Wilder's smoothed RSI.
- `compute_support_levels()` / `compute_resistance_levels()` — trailing min/max per window
  (`daily`/`weekly`/`monthly`/`yearly`), sharing `_sorted_dated_closes()` / `_extreme_since()`.
- `compute_macd()` — 12/26/9, via `_ema_series()` (full-series EMA, unlike `trend.py`'s
  final-value-only `_ema()`).
- `compute_bollinger_bands()` — 20-period SMA ± 2 population std devs.
- `classify_regime_series()` / `RegimeReaction` / `compute_regime_reactions()` /
  `compute_regime_rsi_reactions()` — the reproducibility path for the two validated-combo
  lookups in `app.py`; keep even if `app.py` stops calling one of them directly.

## `src/config.py`

- `SPECULATION_CRYPTO_TICKERS` — maps a bare display symbol (`BTC`/`ETH`/`SOL`) to its
  yfinance spot symbol. Crypto is speculation-only, never added to `TICKERS`/Portfolio/ETFs.

## `src/valuation/trend.py`

- `evaluate_trend()`, `classify_trend_state()` — reused here (not recomputed) so the "current
  regime" classification can't silently drift from the Acciones tab's own trend section.

## Do not re-add without re-validating

- Fibonacci-level probability/reaction code (removed — failed out-of-sample).
- The régimen × horizonte table (removed twice — technically valid but user feedback was "no
  ayuda a decidir"; the DCA box is what replaced it).
