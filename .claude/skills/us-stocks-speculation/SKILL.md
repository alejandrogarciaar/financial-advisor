---
name: us-stocks-speculation
description: Use when the user's request is scoped to the "🎲 Especulación" tab of the USStocks (Precio Justo) dashboard — RSI, support/resistance, MACD, Bollinger Bands, ADX, OBV/volume, the DCA suggestion box, or the BTC/ETH/SOL crypto tickers. Points to the files that make up this tab so work stays scoped to them.
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
  MACD, Bollinger Bands, ADX, OBV, and the "📋 Plan de DCA sugerido" box.
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
- `compute_adx()` — Wilder's ADX/+DI/-DI (14). Needs daily high/low, not just close — the only
  indicator in this file that does. Investigated as a regime refinement (same pattern as
  RSI-overbought) and did NOT validate out-of-sample; shown only as a descriptive indicator,
  same tier as MACD/Bollinger. See CLAUDE.md before trying to fold it into the DCA box.
- `compute_obv()` — On-Balance Volume vs. its own `OBV_SMA_PERIOD`-day (20) SMA. Needs daily
  volume. Investigated as a regime refinement too — looked clean for ETH at exactly one SMA
  period (20) but NOT at neighboring ones (10/30), the same fragility signature as ADX/
  Fibonacci, so it did NOT ship as validated either. Shown as a confirmation/divergence cross-
  reference against the current price trend (same pattern as `trend_context_note()`/
  `quality_context_note()`), not tied into the DCA box.
- `classify_regime_series()` / `RegimeReaction` / `compute_regime_reactions()` /
  `compute_regime_rsi_reactions()` — the reproducibility path for the two validated-combo
  lookups in `app.py`; keep even if `app.py` stops calling one of them directly.

## `src/config.py`

- `SPECULATION_CRYPTO_TICKERS` — maps a bare display symbol (`BTC`/`ETH`/`SOL`) to its
  yfinance spot symbol. Crypto is speculation-only, never added to `TICKERS`/Portfolio/ETFs.

## `src/data/yfinance_client.py`

- `get_historical_prices()` now returns `"high"`/`"low"`/`"volume"` per day alongside
  `"date"`/`"close"` (high/low added for ADX, volume added for OBV). `_cached_historical_prices()`
  in `app.py` is hardcoded to this provider for this whole tab regardless of which provider is
  active elsewhere — pre-existing, not specific to either indicator.

## `src/valuation/trend.py`

- `evaluate_trend()`, `classify_trend_state()` — reused here (not recomputed) so the "current
  regime" classification can't silently drift from the Acciones tab's own trend section.

## Do not re-add without re-validating

- Fibonacci-level probability/reaction code (removed — failed out-of-sample).
- The régimen × horizonte table (removed twice — technically valid but user feedback was "no
  ayuda a decidir"; the DCA box is what replaced it).
- ADX as a `REGIME_ADX_VALIDATED_HORIZONS`-style refinement of the DCA box's regime signal
  (tested, sign flipped train/test and across nearby thresholds — failed the same bar the RSI
  refinement cleared). ADX itself stays, but only as a descriptive indicator.
- OBV as a `REGIME_OBV_VALIDATED_HORIZONS`-style refinement (tested, clean result at exactly
  one SMA period — 20 days — but not at 10 or 30, the same nearby-parameter fragility as ADX;
  do not re-add on the strength of the 20-day result alone). OBV itself stays, but only as a
  descriptive confirmation/divergence cross-reference.
