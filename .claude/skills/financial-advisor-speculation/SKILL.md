---
name: financial-advisor-speculation
description: Use when the user's request is scoped to the "🎲 Especulación" tab of the financial-advisor (Precio Justo) dashboard — stocks only (RSI, support/resistance, MACD, Bollinger Bands, ADX, OBV/volume). BTC/ETH/SOL live in the separate "🪙 Cripto" tab (`financial-advisor-cripto`), not here. Points to the files that make up this tab so work stays scoped to them.
---

# Especulación tab — context map

This skill doesn't prescribe steps — it just points to what "the Especulación tab" is made of,
so work requested for this tab doesn't drift into Acciones/ETFs/Portafolio/Cripto code or
require re-discovering the file layout from scratch. `references/design-history.md` has the
full design rationale for everything in this tab — including several features that were
tried and removed after failing out-of-sample validation (Fibonacci levels, a régimen/horizonte
table, ADX/OBV as regime refinements) — read it before adding a new speculative signal. That
narrative used to live in `CLAUDE.md` directly, then moved to this file's own "Design history"
section, then out to `references/design-history.md` (this file only loads it on demand instead
of on every invocation of this skill — see the `token-audit` skill for why) — same "only load
what a given task actually needs" principle each move applied, one level further.

**This tab is stocks-only (`TICKERS`) — BTC/ETH/SOL moved out entirely.** Two things used to
live here and don't anymore: the multi-methodology support/resistance engine (moved to its own
"🧭 Niveles" tab first, clustering/KDE/robust trendlines/Volume Profile/VWAP), and then the
crypto tickers themselves, indicators and all (moved to the renamed "🪙 Cripto" tab once it
absorbed Niveles). See `financial-advisor-cripto` for both.

## `src/ui/speculation.py`

App code used to be one 2821-line `app.py`; it's split into `src/ui/*.py` (one file per tab,
plus `shared.py` for cross-tab plumbing) with `app.py` now just the thin entry point (page
config + tab wiring). This tab's code — AND the indicator stack shared with Cripto — lives in
`src/ui/speculation.py`:

- `render_speculation()` — thin: ticker selectbox (`TICKERS` only), fetch via
  `_cached_historical_prices(ticker)` (`src/ui/shared.py`), sticky price, defines a
  `_render_zone_engine()` closure (see below), then a single call to
  `render_speculation_indicators("speculation", ticker, historical_prices, closes,
  current_price, is_crypto=False, render_zone_engine=_render_zone_engine)`.
- `render_speculation_indicators(key_prefix, ticker, historical_prices, closes, current_price,
  is_crypto, render_zone_engine)` — the actual indicator stack: RSI, EMA/SMA section,
  `render_zone_engine()` (the caller-supplied Market Reaction Zone Engine block — see below),
  the "📋 Plan de DCA sugerido" box (`is_crypto` only), MACD, Bollinger Bands, ADX, OBV — shared
  with the Cripto tab: `financial-advisor-cripto`'s `render_crypto()` (`src/ui/cripto.py`) imports this
  exact function and calls it with `key_prefix="crypto"`, `is_crypto=True`, its own
  `_render_zone_engine()` closure. Things to know before touching it:
  - `render_zone_engine` is a required `Callable[[], None]` parameter — each caller renders its
    own Market Reaction Zone Engine block (own data source, own cache function, own validated-
    tickers lookup) via a closure defined in its own scope, called at the exact point inside
    this shared function where the simple "Soportes y Resistencias" trailing-min/max chart used
    to render (removed by explicit user request — see Design history). This callback pattern,
    not a straight function call, exists specifically because `cripto.py` already imports this
    function from `speculation.py` — `speculation.py` importing anything back from `cripto.py`
    would be a circular import, so each tab's own Zone Engine code has to stay in its own file.
  - `key_prefix` exists purely to keep widget keys unique across the two callers — `st.tabs()`
    isn't lazy (see CLAUDE.md), so both tabs' bodies run every rerun, and any hardcoded
    `key=` inside this function WILL collide between Especulación and Cripto
    (`StreamlitDuplicateElementKey`, hit for real while splitting this out).
  - `is_crypto` gates exactly one thing: the DCA box, which doesn't apply to stocks (no DCA-plan
    concept for them in this project). Everything else runs identically for either caller.
- `REGIME_VALIDATED_COMBOS`, `REGIME_RSI_OVERBOUGHT_VALIDATED_HORIZONS` — static lookups from
  one-off out-of-sample backtests, keyed by BTC/ETH/SOL — these only ever matter when
  `render_speculation_indicators()` is called with `is_crypto=True`, i.e. from the Cripto tab,
  even though the constants/functions themselves still live in this file/`speculation.py` (the
  computation module, not this UI one). Only update by re-running the same chronological
  train/test methodology, never by loosening the threshold to make a ticker pass. Note: they
  were derived from yfinance-sourced crypto history; Cripto now feeds them Binance-sourced
  history instead — see `financial-advisor-cripto`'s Design history for why that wasn't re-validated (a
  deliberate judgment call, not an oversight).
- **"🧭 Market Reaction Zone Engine" section — rendered via the `render_zone_engine` callback
  described above**, at the position right after "Medias móviles" (originally added at the end
  of `render_speculation()`, moved here — see Design history). The same multi-methodology
  support/resistance engine as Cripto (`src/support_resistance.py`), also available for stocks.
  `_cached_stock_sr_levels()` (`@st.cache_data(ttl=21600)`, same CPU-cost rationale as Cripto's
  `_cached_sr_levels`) calls `daily_reference_config()` (see that module's section below) and
  `detect_levels(historical_prices, config, daily_prices=historical_prices)` — the same
  yfinance daily array passed as both arguments on purpose (reference series AND the source for
  weekly/monthly resampling), not a copy-paste bug. `STOCK_SR_VALIDATED_TICKERS` (this file) is
  `{"TSLA": {"support"}}` as of 2026-08-08 — re-validated under the current score formula
  (chronological 60/40 split, horizons `[5, 10, 20, 30]`, score percentiles 40/55/70 to check
  threshold-fragility) across all 8 `TICKERS`; only TSLA-soporte held the same sign at every
  percentile and horizon, train and test. See Design history below for the full result table
  and why every other (ticker, kind) combo — including the pre-redesign AAPL-resistance result —
  didn't reproduce. UI mirrors `render_crypto()`'s
  engine section closely (config expander, compute button, levels table, chart via
  `render_advanced_levels_chart()` from `src/ui/shared.py`) with one real difference: "Antigüedad
  (días)" is `lv.age_bars` **directly**, not `÷6` like Cripto's table — here the reference series
  already IS daily, so `age_bars` is already in days.
- **"📐 Golden Cross / Death Cross" section**, appended at the very end of `render_speculation()`
  (not inside `render_speculation_indicators()` — never tested for crypto, so it must never
  render on the Cripto tab, same reasoning as `STOCK_SR_VALIDATED_TICKERS` above). Validated
  2026-08-08: SMA50 vs SMA200 tested as a sustained REGIME (not the rare crossover day itself —
  too few crossovers in ~5 years to get a usable sample; the regime STATE gives hundreds of
  observations per ticker instead). `GOLDEN_CROSS_VALIDATED_TICKERS = {"AAPL", "TSLA", "UBER"}`
  — see Design history for the full result and why the sign differs by ticker (AAPL/TSLA
  underperform in "golden cross" vs "death cross"; UBER outperforms). The UI never labels either
  state "alcista"/"bajista" — it always shows the ticker's own measured number
  (`compute_golden_cross_reactions()` in `src/speculation.py`, same "frozen validated set, live-
  recomputed number" pattern as `current_bucket_reaction()`/`compute_regime_reactions()`), and
  picks `st.success`/`st.error` by the actual sign of that number, not by which state it is.

## `src/ui/shared.py`

- `render_sticky_price()` — shared helper (also used by Acciones' `render_detail()`, ETFs, and
  Cripto) for the floating price card; don't fork a speculation-only copy.

## `app.py`

- Just the tab wiring now (`st.tabs()` call + one `with tab_especulacion:` block importing
  `render_speculation` from `src/ui/speculation.py`).

## `src/speculation.py`

- `compute_rsi()` / `compute_rsi_series()` — Wilder's smoothed RSI.
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
  lookups in `src/ui/speculation.py`; keep even if `src/ui/speculation.py` stops calling one of
  them directly.
- `classify_golden_cross_series()` / `GoldenCrossReaction` / `compute_golden_cross_reactions()`
  — same shape as the regime functions above, but for SMA50-vs-SMA200 state instead of the
  EMA/SMA-triple "fuerte/débil/mixta" regime. Feeds `GOLDEN_CROSS_VALIDATED_TICKERS` in
  `src/ui/speculation.py`.

## `src/data/yfinance_client.py`

- `get_historical_prices()` now returns `"high"`/`"low"`/`"volume"` per day alongside
  `"date"`/`"close"` (high/low added for ADX, volume added for OBV). `_cached_historical_prices()`
  in `src/ui/shared.py` is hardcoded to this provider for this whole tab regardless of which
  provider is active elsewhere — pre-existing, not specific to either indicator.

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
- Wyckoff "Spring" pattern (proposed 2026-08-07, tested and rejected — never shipped, no code
  in the repo). User asked about adding the Wyckoff method to Acciones; redirected to
  Especulación instead since it's a technical/timing pattern, same category as RSI/ADX/OBV, not
  a fundamentals signal (Acciones is 100% timing-language-free by design — see `CLAUDE.md`).
  Defined as: `low[t] < trailing_min(low, lookback)` (support undercut) AND
  `close[t] >= trailing_min(low, lookback)` (closed back above it same day) — tested at
  lookback 10/20/30 across all 8 `TICKERS`, horizons `[5, 10, 20, 30]` (this tab's own
  convention, not Portfolio's `[20, 60, 90, 180]`). Price-only version: AAPL validated at
  lookback=10 (negative sign) but failed at 20/30; UBER validated at lookback=20 (positive
  sign, i.e. the opposite direction from AAPL) but failed at 10/30 — the exact same
  one-parameter-only fragility signature that sank Fibonacci/ADX/OBV. No ticker validated at
  all 3 neighboring lookbacks. Volume-confirmed version (added `volume[t] < trailing 20-day
  average volume` — the classic "low volume undercut = no real supply" Wyckoff reading, MA
  window fixed at 20 to match `OBV_SMA_PERIOD` rather than swept too, to avoid multiplying the
  search space) came back even weaker: the added filter roughly halves the sample per ticker/
  lookback, pushing most train/test cells below `min_observations=15` before a sign could even
  be checked, and zero tickers validated across all 4 horizons at any lookback. Do not re-add
  on the strength of either version's isolated positive combos (AAPL@10, UBER@20) — both are
  single-parameter hits that don't survive the neighbor check this project always requires.

## Design history

Full rationale for every design decision in this tab, what was tried and rejected, and the real
bugs found along the way — see `references/design-history.md`. Read it before adding a new
speculative signal, touching the DCA box, the support/resistance chart, or the Market Reaction
Zone Engine section.

