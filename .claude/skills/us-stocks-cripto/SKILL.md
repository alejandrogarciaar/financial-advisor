---
name: us-stocks-cripto
description: Use when the user's request is scoped to the "🪙 Cripto" tab of the USStocks (Precio Justo) dashboard — BTC/ETH/SOL only. Covers both the shared speculation indicators (RSI, MACD, Bollinger, ADX, OBV, DCA box) and the multi-methodology support/resistance engine (clustering, KDE, robust trendlines, Hough transform, Volume Profile, VWAP, channels, confidence scoring), all sourced from Binance. Points to the files that make up this tab so work stays scoped to them.
---

# Cripto tab — context map

This skill doesn't prescribe steps — it just points to what "the Cripto tab" is made of, so work
requested for this tab doesn't drift into Especulación/Portafolio code or require re-discovering
the file layout from scratch. `references/design-history.md` has the full design
rationale, the real bugs found while building it, and the exact out-of-sample validation result
— read it before adding a new methodology, touching the scoring weights, or changing which
tickers/tab this content lives in.

**This tab is BTC/ETH/SOL only.** Especulación (`us-stocks-speculation`) is stocks-only
(`TICKERS`) — the two used to overlap (crypto lived in both places, under different tab names)
until this tab absorbed all of crypto's speculation content and Especulación dropped crypto
entirely. See "Design history" for why.

## `src/ui/cripto.py`

App code used to be one 2821-line `app.py`; it's split into `src/ui/*.py` (one file per tab,
plus `shared.py` for cross-tab plumbing) with `app.py` now just the thin entry point (page
config + tab wiring). This tab's code lives in `src/ui/cripto.py`:

- `render_crypto()` — the whole tab: its own ticker selectbox (`CRYPTO_BINANCE_SYMBOLS` keys —
  `["BTC", "ETH", "SOL"]`, independent state from Especulación's), sticky price
  (`render_sticky_price("niveles", ...)`, from `src/ui/shared.py`), a call to
  `render_speculation_indicators("crypto", ...)` (imported from `src/ui/speculation.py` — the
  shared indicator stack, see `us-stocks-speculation`), and then the "🧭 Market Reaction Zone
  Engine" section (renamed from "Soportes y Resistencias — multi-metodología", see Design
  history): the "⚙️ Configuración avanzada" expander (method toggles, top_n, min_touch_points —
  default 3, was 2 — and `sr_include_1h`; `sr_include_4h` was REMOVED, 4h is now mandatory/always
  fetched, see Design history), the compute button, the display filters (`sr_max_distance_pct`,
  `sr_timeframe_filter`), the levels table (now includes "Magnitud rebote (ATR)",
  `lv.avg_rebound_magnitude_atr`, and "Antigüedad (días)" — `lv.age_bars / 6`, since `age_bars`
  is now in 4h-bar units, not daily), the chart (`render_advanced_levels_chart()`), and the
  "📋 Lectura validada fuera de muestra" section.
- `render_speculation_indicators(key_prefix, ticker, historical_prices, closes, current_price,
  is_crypto)` — lives in `src/ui/speculation.py`, imported here and called with
  `key_prefix="crypto"`, `is_crypto=True` (Especulación calls the same function with
  `key_prefix="speculation"`, `is_crypto=False`). `key_prefix` exists ONLY to keep widget keys
  unique between the two callers — `st.tabs()` isn't lazy (see CLAUDE.md), so both tabs' bodies
  execute every rerun, and a hardcoded key here collides (`StreamlitDuplicateElementKey`, hit for
  real while building this — see "Design history"). `is_crypto` gates just one thing: whether
  the "📋 Plan de DCA sugerido" section renders (doesn't apply to stocks — no DCA plan concept
  for them in this project). See `us-stocks-speculation` for what's actually inside this
  function (RSI, EMA/SMA, simple Soportes/Resistencias, DCA box, MACD, Bollinger, ADX, OBV) —
  it's the same code either way, just fed Binance data here instead of yfinance.
- `_cached_binance_historical_prices()` / `_cached_binance_historical_prices_4h()` /
  `_cached_binance_historical_prices_1h()` — thin `@st.cache_data(ttl=900)` wrappers over
  `src/data/binance_client.py`. The 4h wrapper now requests `years_back=2.0` explicitly (not
  Binance's 5.0 default) — it's the engine's reference series now (see the
  `src/support_resistance.py` section above), and it's the only caller of that wrapper, so the
  override is safe. `_cached_sr_levels()` (the engine's cache, `ttl=21600` — CPU cost, not
  network) resolves `CRYPTO_BINANCE_SYMBOLS[ticker]`, fetches daily (for `daily_prices=`) and 4h
  (the new primary arg to `detect_levels()`) unconditionally, plus 1h if `include_1h` is checked.
- `render_advanced_levels_chart()` (moved to `src/ui/shared.py` once Especulación needed it too
  for stocks — see below and `us-stocks-speculation`'s Design history — imported from there now,
  not defined in this file) — draws the top levels as lines (colored by kind: green=support,
  red=resistance, purple=channel; opacity scaled by `confidence_score`) with shaded zone bands,
  over the last `window_days` (default 365) of the DAILY series (nicer x-axis than 4h bars) —
  but `SRLevel.value_at(bar_index)` expects an index in the reference series (4h here, daily for
  Especulación's stock caller), so the chart takes a second param (`reference_prices`, fetched
  again via the already-cached `_cached_binance_historical_prices_4h()` for this tab — instant,
  not a new network call) and converts each visible daily date to its nearest reference bar (a
  local bisect closure) before calling `value_at()`. Deliberately NOT `LEVEL_CHART_COLORS`
  (Especulación's fixed per-category palette) — the number of levels here is dynamic, so
  color-by-type + score-driven opacity is used instead (`SR_KIND_RGB`, also now in `shared.py`).
- `SR_METHOD_LABELS` (also moved to `src/ui/shared.py`, same reason) — Spanish labels for the
  `multiselect` that toggles `SRConfig.enabled_methods`.
- `sr_max_distance_pct` (`st.slider`, "Mostrar niveles a menos de X% del precio actual") and
  `sr_timeframe_filter` (`st.multiselect`, "Temporalidades a mostrar") — both **display-only**
  filters on top of the already-computed `sr_levels`, combined with AND. Neither touches
  `SRConfig`/`detect_levels()` — explicit user request ("no toques la fuente de datos del
  cálculo, solo quiero filtrar en la web"). `_level_distance_pct()` (local closure) reads
  `SRLevel.distance_to_price_pct` directly for support/resistance; for a channel (whose own
  `distance_to_price_pct` is always 0, set that way in `_detect_channels()`) it uses the closer
  of `channel_support`/`channel_resistance`'s distance instead. The timeframe filter's *options*
  are derived from whatever's actually present in `sr_levels` (`SR_TIMEFRAME_ORDER` just controls
  display order — `{"1h": 0, "4h": 1, "daily": 2, "weekly": 3, "monthly": 4}`, finest to
  coarsest) — "4h" now always appears (it's mandatory); "1h" only appears when its checkbox was
  on for that computation.
  Deselecting every timeframe (or narrowing the % to where nothing qualifies) correctly shows the
  empty-state caption — it does NOT silently fall back to "show everything." The "📋 Lectura
  validada fuera de muestra" section deliberately keeps reading the **unfiltered** `sr_levels` —
  the validated finding shouldn't disappear just because the display filters happen to be narrow.
- `sr_include_1h` (`st.checkbox`, inside "⚙️ Configuración avanzada") — opt-in, off by default.
  4h no longer has a matching checkbox — it's mandatory now (the engine's reference series), so
  toggling it made no sense once it stopped being an optional extra. Binance has native 4h/1h
  klines (no reaggregation needed, unlike yfinance's old 60m-based version — see "Design
  history"), so the only real cost left for 1h is the extra network round-trip.
- `SR_VALIDATED_SCORE_PERCENTILE` (55.0), `SR_VALIDATED_HORIZONS_DAYS` ([5, 10, 20, 30]),
  `SR_VALIDATED_TICKERS` — the out-of-sample-derived lookup gating the "📋 Lectura validada fuera
  de muestra" section's `st.success` message, same pattern as `REGIME_VALIDATED_COMBOS` in
  `src/ui/speculation.py`. The cutoff is now a **percentile** of each ticker/kind's own score
  distribution (`score_percentile_threshold()` in `src/support_resistance.py`), not a fixed
  absolute number — a fixed cutoff stopped meaning the same thing once the score's scale shifted
  (see the statistical-consistency adjustment below and Design history).
  **`SR_VALIDATED_TICKERS` is currently `{}` (empty) — nothing is validated today.** See Design
  history for the full story: two validation rounds ran the same day, and the second one (after
  adding the statistical-consistency adjustment) invalidated the first one's clean result. This
  was a deliberate, considered outcome, not an oversight — don't restore the old
  `{"BTC": {"support"}, "ETH": {"support"}, "SOL": {"support"}}` (or the even older
  `{"BTC": {"support"}, "TSLA": {"support", "resistance"}}`) values without re-running the
  validation fresh.

## `src/support_resistance.py` — "Market Reaction Zone Engine"

Renamed from an informal "support/resistance engine" after a user-driven redesign (see Design
history): the score now prioritizes REACTION QUALITY (rebound size + volume) over touch count,
which used to be the dominant weight.

**The engine's REFERENCE series is 4h, not daily** (a later follow-up change, same session — see
Design history). Every touch/rebound/breakout is walked against 4h candles (capped at 2 years,
`src/ui/cripto.py`) instead of daily ones — BTC/ETH/SOL only have ~1825 daily bars in 5 years,
too few for the Wilson/confidence adjustments below to have much to work with; 4h gives ~6x more
touch opportunities in the same calendar span. `daily_prices` (a new, optional `detect_levels()`
param) is still used, but only to resample weekly/monthly candidates and generate native "daily"
candidates — not as the walking substrate anymore. Every `SRConfig` field expressed in bar counts
(not ATR multiples) is rescaled ×6 accordingly: `atr_period` 14→84, `breakout_confirm_bars`
3→18, `episode_gap_bars` 3→18, `age_full_credit_bars` 180→1080,
`volume_confirmation_avg_period` 20→120, `short_lifespan_bars` (in `DEFAULT_PENALTIES`) 10→60,
`pivot_lookback_4h` 5→30 (4h's role changed from optional-secondary to primary-reference, so it
now gets the same ±5-DAY pivot window `pivot_lookback_daily` always represented, not ±5 raw 4h
bars ≈ 20 hours). ATR-multiple fields (`dbscan_eps_atr_mult`, `reaction_magnitude_full_credit_
atr_mult`, etc.) are untouched — they're already self-scaling to whatever resolution is in use.
`BARS_PER_UNIT` was a fixed dict (barras-de-4h-per-unit: `{"4h": 1.0, "daily": 6.0, "weekly":
42.0, "monthly": 182.64, "1h": 0.25}`) hardcoding the assumption that the reference is always
4h — since generalized (when Especulación needed a DAILY reference for stocks, see
`us-stocks-speculation`'s Design history) into `BARS_PER_DAY` (a fixed physical bars-per-day
table, independent of which timeframe is "reference") + `_bars_per_unit(tf, reference_tf)`
computing the ratio dynamically. Verified to reproduce the old fixed dict's values exactly when
`reference_tf="4h"` — zero behavior change for this tab. `SRConfig.reference_timeframe` (new
field, default `"4h"`) is what tells `_bars_per_unit()` which timeframe is the reference;
Especulación's stocks caller uses `daily_reference_config()` instead of `SRConfig()` directly to
get `reference_timeframe="daily"` plus the matching ÷6 bar-count fields. `_nearest_daily_index`
was renamed `_nearest_reference_index` (same bisect logic, name no longer lies about
granularity).
`SRLevel.value_at(bar_index)` (renamed from `day_index`) now expects a 4h-series index —
`src/ui/cripto.py`'s chart converts each visible daily date to its nearest 4h bar before calling
it (see that file's section below). `compute_level_zone_reactions()` deliberately did NOT
change — it still walks the DAILY series to measure N-day forward returns, since `zone_low`/
`zone_high` are plain prices that don't depend on which index space produced them, and the
project's horizon convention (5/10/20/30 **days**) is shared with backtest.py/regime-reactions/
drawdown-buckets.

- `SRConfig` — every parameter (pivot lookback per timeframe including `pivot_lookback_1h`, ATR
  period/tolerance, DBSCAN eps, KDE bandwidth, Hough resolution, optimizer bounds, scoring
  weights/penalties, `enabled_methods`, `timeframes` (now defaults to
  `("4h", "daily", "weekly", "monthly")`, `4h` no longer optional), `top_n`, `min_touch_points`
  (3, was 2), `sane_price_min_mult`/`sane_price_max_mult`, `reaction_magnitude_full_credit_atr_
  mult` (2.0 — a rebound of ≥2x ATR gets full credit for that component)) is externally
  configurable, per the original spec's "completamente configurable" requirement.
  `dbscan_eps_atr_mult` is `0.15` (was `1.5`) — ATR(14)×0.15, the "Cluster Tolerance" the
  redesign asked for, applied to both DBSCAN/KDE clustering and candidate-merging, same shared
  field as before, just a much tighter default (narrower zones). Nothing here is crypto-specific
  — this module doesn't know or care that its only caller now is the Cripto tab.
- `TIMEFRAME_IMPORTANCE` (new) — institutional→operational hierarchy:
  `{"monthly": 1.0, "weekly": 0.9, "daily": 0.75, "4h": 0.5, "1h": 0.3}`. Feeds the
  `timeframe_weight` score component (see below) — a level found on a longer timeframe is
  intrinsically more significant, not just "more confirmed by confluence" (confluence is still a
  separate bonus on top).
- `DEFAULT_WEIGHTS` — 6 scored components summing to 100 (was 8, with `touch_points` dominant at
  30):
  - `reaction_magnitude`: 25 (NEW — average rebound size, in ATR multiples; the centerpiece of
    the redesign)
  - `volume_during_rebounds` (renamed from `volume_confirmation`): 20
  - `respect_rate` (renamed from `rebounds`): 20 — fraction of touches the level held (same
    counter as "número de rupturas fallidas" in the user's spec, just as a ratio)
  - `timeframe_weight` (renamed from `multi_timeframe`, now hierarchy-aware): 20
  - `age`: 10 (unchanged)
  - `touch_points`: 5 (was 30 — explicitly demoted per the redesign: "touch points are NOT the
    primary criterion")
  - `volume_profile`/`candle_confirmation`/`proximity`/`vwap_confluence` are NOT in this dict
    anymore — `_score_level()` sums via `config.weights.get(k, 0.0)` (not direct indexing), so
    these 4 still compute and appear in `component_scores` (still toggleable via
    `enabled_methods`, still shown for inspection) but contribute zero points. They were dropped
    from scoring, not from the engine, per an explicit user choice — see Design history.
- **Statistical-consistency adjustment (added same day as the redesign, see Design history)**:
  `respect_rate` and `volume_during_rebounds` no longer use the raw ratio (rebounds/touches,
  volume_confirmations/rebound_count) — they use `_wilson_lower_bound(successes, n,
  config.wilson_z)`, the lower bound of the Wilson score interval, so a level with just
  `min_touch_points` touches and a 100% ratio scores meaningfully lower than one with the same
  ratio over many more touches. `reaction_magnitude` similarly uses
  `_mean_lower_confidence_bound(rebound_magnitudes_atr, config.wilson_z)` instead of a plain
  mean (subtracts `z * std/sqrt(n)`, with a single-observation case falling back to half the raw
  value). `SRConfig.wilson_z` (default 1.96, ~95% confidence) is the shared knob for both. This
  is a fix to the CALCULATION's statistical soundness, not a historical calibration — but it
  measurably changed which levels rank highest, which is why the OOS validation had to be
  re-run (see Design history for why that broke the previous result).
- `_walk_touches()`'s rebound-magnitude tracking uses the MEAN of the lookahead window's
  ATR-normalized distance, not the max — the max let a single outlier bar (a wick) dominate the
  whole `reaction_magnitude` reading for that episode; the mean is steadier.
- **`level_atr` (in `_score_level`, not `atr_current`) normalizes `zone_half`/`dispersion_ratio`.**
  Added as a follow-up consistency fix: a level's zone width and dispersion penalty used to be
  measured against `atr_current` (a single snapshot of TODAY's ATR), even for a level whose
  touches happened years ago in a completely different volatility regime — for BTC, comparing a
  2022 line's residuals against today's (much larger, in dollar terms) ATR was an apples-to-
  oranges measuring stick. `level_atr` is the mean of `atr_arr` at this specific level's own touch
  indices, falling back to `atr_current` only if no valid ATR exists at any touch. Verified this
  actually changes zone widths (e.g., a 2022-era BTC level: zone width ~$1,100 using its own
  ~$1,100 ATR-at-the-time vs what would've been ~$1,670-based if measured against today's ATR).
  `atr_current` stays as the baseline everywhere the calculation is genuinely "relative to
  today" — the optimizer's search bounds, `_merge_candidates`' merge tolerance, the (informational,
  unscored) `proximity` component, and the VWAP-confluence check — since those really are about
  today's regime, not the level's own history. Re-ran the OOS validation after this fix: still
  `{}` (no regression, no new pass either — see Design history).
- **Fixed "full credit" thresholds (`touch_component_full_credit=6`, `age_full_credit_bars=180`)
  were reconsidered and found NOT to be an inconsistency, despite initially looking like one.**
  Touches/age are always walked against the SAME full daily series (`_optimize_and_score` in
  `detect_levels()` always passes the daily `dates/opens/highs/lows/closes/volumes` arrays,
  never a timeframe-specific subset) regardless of which timeframe (daily/weekly/monthly/4h/1h)
  originally proposed a candidate — so there's no confound where a 1h-sourced candidate is held
  to the same absolute touch target over effectively less opportunity to touch. A fixed target
  is fair here because the denominator (the daily series length) is already the same for every
  candidate on a given ticker. This WOULD become a real concern if BTC/ETH/SOL ever had
  meaningfully different history lengths from each other, but they don't today (all ~5 years).
- `detect_levels(historical_prices, config=None, intraday_4h_prices=None,
  intraday_1h_prices=None)` — the entry point. Returns `[]` early if the daily input is too short
  (<30 bars) or missing any of `open`/`high`/`low`/`close`/`volume` (self-heals on next fetch,
  same pattern as ADX/OBV in `speculation.py`). `intraday_4h_prices`/`intraday_1h_prices` are
  optional and, unlike weekly/monthly (resampled from `historical_prices` right here, no
  network), must arrive **already fetched** by the caller — this function stays pure/I/O-free,
  same separation as `_evaluate_from_data()` in `fair_value.py`. If `"4h"`/`"1h"` is in
  `config.timeframes` but the corresponding arg is `None`/too short, that timeframe is just
  skipped, not an error.
- `_build_candidates_for_timeframe()` — per timeframe (daily / weekly-resampled / monthly-
  resampled via `pandas.resample` / 4h), runs pivot detection + DBSCAN + KDE (horizontal levels)
  and RANSAC/Theil-Sen/Huber/Hough (diagonal trendlines), converting each line into a common
  (slope-per-day, intercept-at-daily-index-0) representation via `_nearest_daily_index`
  anchoring, so every candidate — regardless of which timeframe proposed it — can be scored
  against the same daily series. The 4h series' own "date" field carries a full datetime
  (`"YYYY-MM-DD HH:MM:SS"`, several bars/day possible), unlike daily/weekly/monthly's date-only
  strings — `_nearest_daily_index`'s `bisect` against the daily series' plain date strings still
  works unmodified because of Python's string-prefix ordering
  (`"2026-07-29" < "2026-07-29 14:00:00"`), so no special-casing was needed there.
- `_merge_candidates()` — fuses candidates (same kind, close value + close slope) from different
  methods/timeframes, unioning their `methods`/`timeframes` sets. This is what lets
  `multi_timeframe`'s score component count genuine confluence.
- `_optimize_line()` / `_touch_optimization_objective()` — Nelder-Mead refinement of each
  merged candidate's (slope, intercept), maximizing the spec's literal objective (touches +
  rebounds + volume + age − breaks − avg distance). **Bounded** around the initial candidate
  (`optimize_max_slope_shift_atr_mult` / `optimize_max_intercept_shift_atr_mult`) — see "real
  bugs found" below for why an unbounded version was wrong.
- `_walk_touches()` — the touch/rebound/break/retest state machine, walked against the full
  daily series with ATR-scaled dynamic tolerance. Takes `atr_arr`/`avg_vol_arr` as **numpy
  arrays**, not `pd.Series` — see "real bugs found" below, this was the actual performance
  bottleneck. For each rebound episode (a touch that did NOT break), also measures the max
  distance (in ATR multiples) between price and the line over the same `lookahead_idx` window
  already used to confirm/reject a break, accumulating into `rebound_magnitudes_atr` — this is
  the raw data behind the new `reaction_magnitude` score component.
- `_score_level()` — assembles the 6 SCORED components (`DEFAULT_WEIGHTS`, see above) into the
  final `confidence_score` via `sum(components[k] * config.weights.get(k, 0.0) for k in
  components)`, applying penalties (breaks, dispersion, short lifespan), then a sanity gate on
  the resulting price (`sane_price_min_mult`/`sane_price_max_mult` — see "real bugs found"
  below). Also computes the 4 informational-only components (volume_profile/candle_confirmation/
  proximity/vwap_confluence) and includes them in `component_scores` even though they don't
  affect `raw_score`. `SRLevel.avg_rebound_magnitude_atr` (new field) carries the raw ATR-multiple
  average (not the 0-1 component) for display in the levels table ("Magnitud rebote (ATR)"
  column in `src/ui/cripto.py`).
- `compute_level_zone_reactions()` — the live-recomputed stat quoted in the "Lectura validada"
  `st.success` message (mean return / win rate / n for days the price was inside a qualifying
  zone), same pattern as `compute_regime_reactions()` in `speculation.py` — the validated-ticker
  *set* is frozen, but the quoted number freshens as history accumulates.
- `detect_channels()` — pairs the best support/resistance trendlines per timeframe when their
  slopes are within `channel_max_slope_diff_pct` of each other.

## `src/data/binance_client.py`

- Public Binance klines (`/api/v3/klines`, no API key) — the ONLY data source for this tab
  (`CRYPTO_BINANCE_SYMBOLS` in `config.py`: `{"BTC": "BTCUSDT", "ETH": "ETHUSDT", "SOL":
  "SOLUSDT"}`). Especulación (stocks) uses yfinance exclusively and never touches this module.
- `get_historical_prices(symbol, years_back=5.0)` — daily klines (`interval="1d"`). Binance
  returns whatever's actually available if the pair is younger than `years_back` (SOL/USDT
  listed ~2020) — no special-casing needed, it just returns a shorter list.
- `get_historical_prices_intraday_4h(symbol, years_back=5.0)` — **native** 4h klines
  (`interval="4h"`) — no reaggregation, no ~730-day cap (unlike yfinance's now-removed
  equivalent, see "Design history"). Same `years_back=5.0` default, giving ~10,950 4h candles
  for BTC/ETH (SOL less, per its shorter listing history).
- `get_historical_prices_intraday_1h(symbol, years_back=2.0)` — native 1h klines, added for the
  Market Reaction Zone Engine's "operational" timeframe tier. Shorter default `years_back` than
  4h/daily (2 years, not 5) — at 1h resolution, 5 years would mean ~44 paginated requests
  (Binance caps every response at 1000 klines regardless of interval) for a timeframe that's
  meant for short-term levels anyway, so the shorter default keeps the fetch fast without losing
  anything relevant to that timeframe's purpose.
- `_fetch_klines()` — Binance caps every response at 1000 klines regardless of interval, so all
  3 functions paginate (advance `startTime` to the last returned candle's `open_time + 1ms`, stop
  when a page comes back short or `startTime` reaches `endTime`). ~2 requests for 5y daily, ~11
  for 5y of 4h, ~18 for 2y of 1h — all sequential, took a few seconds total in testing, well
  within Binance's public rate limit.
- Same disk-cache-fallback pattern as `fmp_client.py`/`yfinance_client.py` (`src/data/cache.py`,
  last good response reused if a live call fails) — nothing provider-specific there.

## `app.py`

- Just the tab wiring now (`st.tabs()` call + one `with tab_cripto:` block importing
  `render_crypto` from `src/ui/cripto.py`).

## Design history

Full rationale, the real bugs found while building this, and the exact OOS validation results —
see `references/design-history.md`. Read before adding a methodology, touching scoring weights,
or changing which tickers/tab this content lives in.

## Do not re-add without re-validating

- An actionable message for ANY ticker/kind right now — `SR_VALIDATED_TICKERS` is `{}` (see
  Design history's "Round 2" for why the previous BTC/ETH/SOL-support result didn't survive the
  statistical-consistency adjustment). Everything in this tab stays descriptive-only until a
  fresh validation actually passes.
- Loosening `wilson_z` (lower = less shrinkage) or `SR_VALIDATED_SCORE_PERCENTILE` specifically to
  make something re-validate — that's the exact "adjust the parameter until it passes" failure
  mode this project's validation discipline exists to prevent, just applied to a statistical
  parameter instead of a score cutoff this time. Re-testing later with more accumulated history is
  fine; tuning the adjustment's strength to force today's data to pass is not.
- Any intraday interval finer than 1h — not requested, and each additional interval is another
  real network fetch plus another `BARS_PER_UNIT`/lookback/`TIMEFRAME_IMPORTANCE` entry to
  maintain.
- Re-adding `volume_profile`/`candle_confirmation`/`proximity`/`vwap_confluence` to
  `DEFAULT_WEIGHTS` without re-checking against the user's explicit spec — they were deliberately
  demoted to informational-only, not omitted by accident.
- Adding stock tickers back to this tab's selector without an explicit new ask — the "Cripto"
  name and Binance-only wiring are a deliberate scope decision, not an incidental gap.
- Re-adding crypto to Especulación's ticker selector — same reasoning in reverse; crypto's
  speculation indicators live here now, not there.
