# Cripto tab — Design history

Full design rationale, the real bugs found while building this, and the exact out-of-sample
validation results. Moved out of `SKILL.md` (where it used to live inline under "## Design
history") so the skill's file-map loads fast on every invocation — read this before adding a new
methodology, touching the scoring weights, or changing which tickers/tab this content lives in.

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

`SR_VALIDATED_TICKERS` (`src/ui/cripto.py`) encodes only the validated combinations. Both round-1/
round-2 re-validation scripts and this original Phase B script were throwaway scratchpads (not
committed to the repo, same pattern as every other OOS check in this project up until
`scripts/oos_validate.py` was added in a later token-audit pass — see `token-audit` skill — to
stop re-deriving this exact methodology from scratch each time) — if re-run with more history,
update `SR_VALIDATED_TICKERS` to match, don't just loosen the threshold to make a ticker pass.

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
