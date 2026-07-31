---
name: us-stocks-cripto
description: Use when the user's request is scoped to the "🪙 Cripto" tab of the USStocks (Precio Justo) dashboard — BTC/ETH/SOL only. Covers both the shared speculation indicators (RSI, MACD, Bollinger, ADX, OBV, DCA box) and the multi-methodology support/resistance engine (clustering, KDE, robust trendlines, Hough transform, Volume Profile, VWAP, channels, confidence scoring), all sourced from Binance. Points to the files that make up this tab so work stays scoped to them.
---

# Cripto tab — context map

This skill doesn't prescribe steps — it just points to what "the Cripto tab" is made of, so work
requested for this tab doesn't drift into Especulación/Portafolio code or require re-discovering
the file layout from scratch. The "## Design history" section below has the full design
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
- `render_advanced_levels_chart()` — draws the top levels as lines (colored by kind:
  green=support, red=resistance, purple=channel; opacity scaled by `confidence_score`) with
  shaded zone bands, over the last `window_days` (default 365) of the DAILY series (nicer x-axis
  than 4h bars) — but `SRLevel.value_at(bar_index)` expects an index in the 4h reference series
  now, so the chart takes a second param (`reference_prices`, the 4h series, fetched again via
  the already-cached `_cached_binance_historical_prices_4h()` — instant, not a new network call)
  and converts each visible daily date to its nearest 4h bar (a local bisect closure) before
  calling `value_at()`. Deliberately NOT `LEVEL_CHART_COLORS` (Especulación's fixed per-category
  palette) — the number of levels here is dynamic, so color-by-type + score-driven opacity is
  used instead (`SR_KIND_RGB`).
- `SR_METHOD_LABELS` — Spanish labels for the `multiselect` that toggles
  `SRConfig.enabled_methods`.
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
`BARS_PER_UNIT` is now barras-de-4h-per-unit (`{"4h": 1.0, "daily": 6.0, "weekly": 42.0,
"monthly": 182.64, "1h": 0.25}`), and `_nearest_daily_index` was renamed
`_nearest_reference_index` (same bisect logic, name no longer lies about granularity).
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

**Walking touches against the 4h reference series instead of daily (most recent).** Follow-up to
the statistical-consistency fix (below): with only ~1825 daily bars in 5 years, BTC/ETH/SOL
levels accumulated too few touches for the Wilson/confidence adjustments to have much to work
with — that's what broke the previous OOS validation. The user's idea: walk against 4h candles
instead, giving ~6x more touch/rebound opportunities in the same calendar span. Confirmed with
the user first (`EnterPlanMode`, since this touches core engine architecture): the 4h reference
is capped at 2 years (same cost-control precedent as 1h), and this went into `detect_levels()` as
the new primary series with `daily_prices` demoted to an optional input for weekly/monthly
resampling + native "daily" candidates only (see the `src/support_resistance.py` section above
for the full parameter-rescaling list). A real bug surfaced during implementation:
`_rolling_vwap()` parsed dates with a hardcoded `"%Y-%m-%d"` format, which broke once the
reference series' dates started carrying a time component (4h dates are
`"YYYY-MM-DD HH:MM:SS"`) — fixed by slicing `d[:10]` before parsing, since that function only
ever needed the calendar day.

Measured effect on BTC: touch counts per level went from ~3-7 (daily-walked) to ~9-14 (4h-walked)
— exactly the intended effect — and computation time for the app's real config (`top_n=8`) came
out to ~8.7s, well within what a button-gated, 6h-cached computation can absorb.

**Re-validation result: mixed, and reported honestly rather than smoothed over.** Under the
strict rule this project always applies (same sign at every one of 3 percentile cuts — 40/55/70
— all 4 horizons, both train and test), the verdict is still `NO VALIDADO` for all 6 (ticker,
kind) combinations — same bottom line as before this change, so `SR_VALIDATED_TICKERS` stays
`{}`. The underlying pattern did shift: BTC-resistance and ETH-resistance now hold the same sign
across all 4 horizons at both the 40th and 55th percentile, breaking only at the 70th.

**First read of that pattern ("the 70th percentile is just too thin a sample") was wrong, and
was corrected after checking the actual `n` per cut, not just the sign.** BTC-resistance's break
at 70 has train n=21 — barely smaller than the n=24 that passed at 55, not a collapse to
statistical noise. ETH-resistance's break at 70 has train n=69, not small at all. And critically,
**SOL-support breaks in the OPPOSITE direction** — it fails at the loosest cut (40, n=96) and
passes cleanly at both 55 and 70. If "too few observations" were the real explanation, SOL should
have broken at its tightest cut too, not its loosest. This is the exact "sign flips depending on
which nearby, equally-defensible threshold you pick" signature that already disqualified
Fibonacci/ADX/OBV in this project — the 3-percentile check is doing its job here, not being
oversensitive. Discussed directly with the user, who agreed: **do not loosen or drop the 70th
percentile check** — `SR_VALIDATED_TICKERS` staying `{}` is the correct, honest conclusion, not
an artifact of an overly strict methodology.

**Market Reaction Zone Engine redesign (score philosophy change).** The user gave a
formal spec for the S/R engine demanding a change of philosophy: touch count should NOT be the
primary scoring criterion — reaction QUALITY (rebound size + volume during the rebound) should
dominate instead ("three strong rebounds with high volume are worth more than ten touches with
no meaningful reaction"). Also requested: 3 timeframe tiers with an institutional→operational
hierarchy (1D/4H/1H), minimum 3 touch points (was 2), a tighter cluster tolerance (ATR(14)×0.15,
was 1.5x), and the engine renamed to "Market Reaction Zone Engine." Two decisions were confirmed
with the user via AskUserQuestion before implementing (this touches a previously OOS-validated
feature, so it went through `EnterPlanMode` first, not a direct edit):
- **Timeframes**: 1h was ADDED without removing anything — daily/weekly/monthly stay (free,
  resampled from data already in memory) alongside the now-optional 4h/1h (both extra Binance
  fetches, opt-in checkboxes). `TIMEFRAME_IMPORTANCE` extends the requested 3-tier hierarchy
  naturally: monthly/weekly rank even above daily (longer timeframe = more institutional weight),
  down to 1h at the bottom.
- **Score components**: `volume_profile`, `candle_confirmation`, `proximity`, and
  `vwap_confluence` were dropped from `DEFAULT_WEIGHTS` (0 points) but kept as togglable/
  computed-and-visible-in-`component_scores` methods, rather than deleted outright — the user's
  chosen option explicitly said "siguen existiendo como métodos de detección togglables."

New/changed pieces: `reaction_magnitude` (new component, avg rebound size in ATR multiples,
tracked in `_walk_touches()`'s `rebound_magnitudes_atr`), `timeframe_weight` (replaces
`multi_timeframe`, now hierarchy-aware via `TIMEFRAME_IMPORTANCE` + a confluence bonus on top),
`respect_rate`/`volume_during_rebounds` (renamed from `rebounds`/`volume_confirmation`, same
underlying computation). `touch_points` dropped from weight 30 to 5 — explicitly demoted, not
removed (still one of the spec's named criteria). Full weight table and rationale in the
`src/support_resistance.py` section above.

**Performance tradeoff, measured and accepted.** The tighter cluster tolerance (ATR×0.15 vs the
old 1.5x) merges far fewer raw candidates before the expensive optimization step (Nelder-Mead,
~100-200 `_walk_touches` evaluations per candidate — the documented bottleneck in "real bugs
found" below): measured on BTC, 78 merged candidates reach optimization under the new tolerance
vs 46 under the old one, roughly doubling wall-clock time (~3.95s vs ~1.90s for the full daily/
weekly/monthly pass). `top_n` still caps the final output at the same count, so this doesn't
change what the user sees — only how long the (button-gated, `ttl=21600` cached) computation
takes. Asked the user whether to optimize this (e.g. filtering by `min_touch_points` *before*
optimization instead of after, in `_score_level`) — explicitly kept as-is: a few seconds behind
a manual button, cached for 6 hours, isn't worth trading off any precision for. Don't
"optimize" this by loosening `dbscan_eps_atr_mult` back up — that would silently undo the exact
tolerance the user's spec asked for.

**Why `SR_VALIDATED_TICKERS` was reset and then re-derived, not just restored.** The prior OOS
validation (BTC-support, TSLA support+resistance) was derived under the OLD score formula. Since
the formula materially changed (new components, redistributed weights, a new timeframe), that
result could not be assumed to transfer — same rule this project already applied to Fibonacci/
ADX/OBV: never reuse a validated threshold/result after reweighting without re-running the same
train/test split. This was applied directly (not asked about) since it's an already-established
project convention, not a new judgment call.

**Re-validation, round 1 (same session, same chronological 60/40 split, same 4 horizons, same 3
score thresholds 40/50/60 to check threshold-fragility — throwaway script, not committed).**
Universe scoped to BTC/ETH/SOL (AAPL/TSLA weren't re-tested — they're no longer selectable in
this tab, so re-validating them wouldn't inform anything reachable from the UI). Result: SOPORTE
validated for all 3 cryptos — same (positive) sign at all 3 thresholds, all 4 horizons, both
train and test. RESISTENCIA validated for none — sign flipped train→test for all 3. Wider
validated set than the old score ever achieved (which only cleared BTC-support) — looked like
strong evidence for the redesign's core hypothesis.

**Round 2: the statistical-consistency adjustment (Wilson lower bound + confidence-adjusted
reaction_magnitude, see the `src/support_resistance.py` section above) broke round 1's result,
same day.** The user asked, as a separate follow-up, how to improve the score's calculation
itself for statistical consistency (not fit it to history) — landed on Wilson-bound shrinkage for
the ratio components and a confidence-adjusted mean for `reaction_magnitude`, explicitly to stop
small-touch-count levels from scoring as if they were as reliable as high-touch-count ones.
Re-running the exact same round-1 validation script under this adjustment broke BTC-support even
at the SAME fixed threshold (≥50) that had validated cleanly in round 1 — sign flipped at 2 of 4
horizons (20d, 30d). Isolating the cause (running with `wilson_z=0` vs the real `wilson_z=1.96`)
confirmed it's the adjustment itself, not a fluke: it changes WHICH levels rank at the top, and
that changed set no longer reproduces round 1's clean signal. This held regardless of whether the
qualifying cutoff was a fixed absolute score or a percentile of the ticker's own score
distribution (tried 50/70/90 percentiles, too few qualifying levels at the top of a ~10-15-level
population per kind; then 40/55/70, same non-validating outcome either way) —
`SR_VALIDATED_SCORE_PERCENTILE` (55.0) is what shipped as the mechanism for next time, using
`score_percentile_threshold()` in `support_resistance.py` instead of a hand-picked absolute
number, precisely so a future score-scale shift doesn't silently miscalibrate the cutoff again.

**Decision, explicitly asked and confirmed with the user rather than resolved unilaterally: trust
the statistical adjustment and accept the more honest (if less exciting) result.** Two readings
are possible — (a) round 1's clean validation was itself partly propped up by small-sample noise
in low-touch levels that round 2's Wilson correction correctly discounts, or (b) the adjustment
is too aggressive for a domain (daily crypto candles, ~1825 bars over 5 years) that structurally
never accumulates hundreds of touches on any one level. The user chose NOT to soften the
adjustment to force something to re-validate — that would be exactly the "loosen the threshold
until it passes" anti-pattern this project's whole validation discipline exists to prevent, just
applied to a statistical parameter instead of a score cutoff. **Result: `SR_VALIDATED_TICKERS` is
`{}` — nothing is validated today.** Re-testing later, once more history accumulates (more
touches per level naturally reduces how much the Wilson bound shrinks each one), is legitimate;
loosening `wilson_z` or the percentile specifically to make BTC-support pass again is not.

**This tab has had 3 names/shapes: "Especulación section" → "🧭 Niveles" → "🪙 Cripto."** First
built as a section inside Especulación (support/resistance only). Split into its own "Niveles"
tab because the S/R engine's computation was too heavy (~5-25s) for Especulación's
"everything-is-instant" character, and it covered stocks + crypto both, still via yfinance. Then,
in the same broader session: Binance became the data source for BTC/ETH/SOL specifically (more
history, native 4h), and finally the user asked to (1) move ALL of Especulación's crypto
indicator content (RSI/MACD/Bollinger/ADX/OBV/DCA box) into this tab, (2) rename it to "Cripto",
and (3) drop BTC/ETH/SOL from Especulación entirely, confirmed explicitly that stocks should
**lose** S/R access in this tab rather than keep it under the new name. That's the current shape:
Cripto = BTC/ETH/SOL only, both the full speculation indicator stack AND the S/R engine, all on
Binance data; Especulación = stocks only (`TICKERS`), yfinance, same indicator stack via the
shared `render_speculation_indicators()` function.

**Real bug hit during the final consolidation: a hardcoded widget key collided across tabs.**
`render_speculation_indicators()`'s internal `st.segmented_control` (the S/R window selector) had
a fixed `key="speculation_chart_view"`. Once both Especulación and Cripto called the same shared
function in the same script run — and `st.tabs()` isn't lazy, so both bodies execute every rerun
regardless of which tab is visually active — Streamlit raised `StreamlitDuplicateElementKey`
immediately. Fixed by adding a `key_prefix` parameter or every hardcoded key inside a function
shared across tab boundaries; exactly the same class of bug already documented for
`render_sticky_price()`'s `key_prefix`. **General lesson for this codebase: any function called
from more than one tab, holding a widget with an explicit `key=`, needs that key parameterized —
don't assume "it only had one caller before" will stay true.**

**`REGIME_VALIDATED_COMBOS`/`REGIME_RSI_OVERBOUGHT_VALIDATED_HORIZONS` (the DCA box's validated
lookups, in `src/ui/speculation.py`) were derived from yfinance-sourced BTC-USD/ETH-USD/SOL-USD
history, and are now being fed Binance-sourced BTCUSDT/ETHUSDT/SOLUSDT history instead — not
re-validated against the new source.** Judgment call, not an oversight: BTC/ETH are highly liquid
across exchanges, so daily closes track very closely between Yahoo's aggregation and Binance's
own tape (arbitrage keeps them tight) — the "fuerte regime → momentum continues" finding is about
a broad behavioral pattern, not something fragile enough to depend on the exact source of a
BTC daily close. If this ever gets re-validated from scratch, it would be worth re-running
against Binance data directly rather than assuming the old yfinance-derived result still holds
exactly — flagged here so a future session doesn't have to rediscover that the source changed
under it.

**Phase A vs. Phase B (S/R engine).** The user's original spec asked for all 20 methodologies AND
an actionable signal, in the same request. Given this project's established rule (see
`us-stocks-speculation`'s "Do not re-add without re-validating" — and this exact question, in a
simpler form, already failed out-of-sample once), the work was split: Phase A built the full
detection engine as descriptive infrastructure; Phase B ran the same chronological 60/40
out-of-sample validation this project always requires before anything becomes an actionable
claim, **before** wiring any "buy/sell near this level" message into the UI.

**Phase B result.** Tested whether being inside a level's zone with `confidence_score >= 50`
predicts forward returns (vs. baseline) at 4 horizons (5/10/20/30 days), across BTC, ETH, SOL,
AAPL, TSLA (the last two from back when this tab still covered stocks) — checked at score
thresholds 40/50/60 to catch the "changes with nearby, equally-defensible parameters" fragility
that has sunk Fibonacci/ADX/OBV before.

- **BTC (support)**: same sign at all 3 thresholds, all 4 horizons, both train and test —
  validated. The only entry still reachable from this tab today.
- **TSLA (support AND resistance)**: same sign at thresholds 40/50, all 4 horizons; threshold 60
  had zero qualifying levels (untested, not contradicted) — validated with that caveat, back
  when stocks were still in scope here. Resistance validating with a **positive** sign (price
  near a well-scored resistance predicted *higher*, not lower, forward return) fits this
  project's repeated "momentum, not mean-reversion" signature (RSI-overbought-in-"fuerte"-regime
  showed the same shape) rather than being a red flag.
- **AAPL (resistance)**: clean at score ≥ 40/50 but broke (sign flipped) at ≥ 60 — the exact
  threshold-fragility signature that already disqualified ADX/OBV. Not validated.
- **ETH, SOL, AAPL (support)**: sign flipped train→test on most horizons. Not validated.

`SR_VALIDATED_TICKERS` (`src/ui/cripto.py`) encodes only the validated combinations. The validation script
itself was a throwaway scratchpad (not committed to the repo, same pattern as every other OOS
check in this project) — if re-run with more history, update `SR_VALIDATED_TICKERS` to match,
don't just loosen the threshold to make a ticker pass.

**Real bugs found while building the S/R engine (worth knowing before touching it again):**

1. **Zone width used the wrong dispersion measure for diagonal lines.** A candidate's
   "dispersion" (feeding `zone_half` and the dispersion penalty) was originally
   `std(contributing_prices)` — fine for a horizontal DBSCAN/KDE cluster, but for a multi-year
   diagonal trendline the raw price spread of its contributing pivots is huge by construction,
   producing absurdly wide zones (observed: $94-wide zones on AAPL). Fixed by computing
   **residuals** (distance from each pivot to the fitted line at that pivot's own index) instead
   of raw price spread — `_Candidate.residual_std`, computed once at fit time, `max()`-combined
   (not averaged) across a merged bucket so a loose member doesn't get diluted by a tight one.
2. **The optimizer had no bounds and would converge to a different level entirely.** Nelder-Mead
   was free to wander anywhere, and since the objective doesn't know or care whether a candidate
   started as "support" or "resistance," an independently-detected support and resistance
   candidate both converged to the exact same price/slope in testing (AAPL, ~$147). Fixed by
   bounding the optimizer to a local neighborhood of its starting candidate
   (`optimize_max_slope_shift_atr_mult` / `optimize_max_intercept_shift_atr_mult`), turning it
   back into a local refinement (what the spec actually asked for) instead of a global search.
3. **The real performance bottleneck was NOT the sklearn/scipy fitting — it was
   `pd.Series.iloc[i]` in a Python loop.** Profiled with `cProfile` before assuming:
   `_walk_touches()` is called ~100-200 times per candidate during Nelder-Mead optimization, and
   it was (a) recomputing a rolling 20-day volume average from scratch on every single call
   despite it not depending on (slope, intercept) at all, and (b) indexing `atr_series.iloc[i]`
   in a plain Python `for` loop — each scalar `.iloc[]` access goes through pandas' full indexing
   machinery, and 2M+ of them dominated the profile. Fixed by precomputing `atr_arr`/`avg_vol_arr`
   as plain numpy arrays ONCE in `detect_levels()` and vectorizing the raw touch-detection loop —
   brought AAPL from ~6s to ~1.8s and BTC from ~23s to ~1.6s, byte-identical output. **Lesson for
   future perf work in this codebase: profile with `cProfile` before guessing where the time
   goes** — the initial assumption (parallelize with threads because sklearn releases the GIL)
   helped only modestly; the actual fix was removing redundant pandas overhead in a pure-Python
   hot loop, which threading alone would not have solved.
4. The 3 timeframe passes (daily/weekly/monthly/4h) and the per-candidate optimize+score step are
   each independent, so both run through `ThreadPoolExecutor` (same pattern as `_parallel_fetch()`
   in `src/ui/shared.py`) — a real but secondary win once (3) was fixed.
5. **A diagonal line's slope can look modest per-day but land somewhere nonsensical when
   extrapolated over years of bars.** Found while testing Binance's 5-year BTC daily history:
   Hough picked a "best" slope of -55.7/day for a daily-timeframe candidate, which over 1825
   bars extrapolates to **-$59,013** at today's index — a negative price. Nothing in the fitting
   itself or in the optimizer's bounds (relative to the initial candidate, not absolute) prevents
   this. Fixed with a sanity gate in `_score_level()`: a level's current price must fall within
   `[min(closes) * sane_price_min_mult, max(closes) * sane_price_max_mult]` (defaults 0.1x/5x).
   Not Binance-specific — the same failure mode could hit any sufficiently long, sufficiently
   volatile series regardless of provider; it just hadn't been exercised by the stock tickers
   tested before this was found.

**Display filters (% of price, timeframe) — added after the S/R engine shipped.** The user asked
for a way to see only the supports/resistances closest to the current price (for short vs.
long-term planning), explicit that it must be UI-only: "no toques la fuente de datos del cálculo,
solo quiero filtrar en la web." Both filters are pure post-hoc filters on the already-computed
`sr_levels` list — `SRConfig`/`detect_levels()` never see them. Useful pattern to repeat: a
computed-once, cached, possibly-expensive result can have arbitrarily many cheap display filters
layered on top without any recompute, as long as the filters don't need information the stored
objects don't already carry.

**4h timeframe and Binance as the data source — added together, right after the display
filters.** yfinance has no native 4h interval (it had to reaggregate 60m bars, capped at ~730
days by Yahoo) — the user pointed out Binance offers 5+ years of real history including native
4h klines, explicitly scoped to "estas 3 cripto" and confirmed stocks should stay on their
existing (unaffected) path. This is what eventually led to consolidating all of crypto's content
into one tab (see the top of this section) — once BTC/ETH/SOL had their own dedicated data path,
it stopped making sense to have their speculation indicators living in a different tab under a
different data source (yfinance) than their S/R levels (Binance).

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
