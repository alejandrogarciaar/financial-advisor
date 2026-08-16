# Cripto tab — Design history

Full design rationale, the real bugs found while building this, and the exact out-of-sample
validation results. Moved out of `SKILL.md` (where it used to live inline under "## Design
history") so the skill's file-map loads fast on every invocation — read this before adding a new
methodology, touching the scoring weights, or changing which tickers/tab this content lives in.

**Surfacing VWAP as its own descriptive section (most recent, 2026-08-16).** Started as a review
question ("¿tenemos VWAP en cripto y cómo lo usamos a favor?"), and the review's answer was that
we had it in name only: `_rolling_vwap()` existed but fed exactly one consumer — the boolean
`vwap_confluence` component — which has weighed 0 since the score redesign, and whose output
(`component_scores`) is rendered nowhere. So the "Confluencia con VWAP" chip in the methods
multiselect was a control over nothing observable, and `SRConfig.vwap_confluence_bonus` was (and
still is) a config field no code reads. Left both alone rather than deleting them — the demotion
to informational-only was an explicit user decision, and this session's scope was to make VWAP
useful, not to relitigate the score.

Four options were put to the user: (A) show it as a descriptive indicator, (B) OOS-validate a
distance-to-VWAP signal and only then make it actionable, (C) anchored VWAP (from a cycle low /
spring / halving) as a candidate level inside the Zone Engine, (D) give the existing boolean
component real weight. **The user picked A alone**, with B deferred as the agreed next step and
run locally (Binance answers 403 through the remote sessions' proxy, and yfinance is blocked too,
so no market data is reachable from a Claude session in this repo's remote environment — the
study cannot be run there, only written).

Implementation notes worth keeping: the two VWAP implementations were unified rather than left to
drift — `rolling_vwap_series()` (`src/speculation.py`, sliding-window sums, returns one value per
bar) is now the only one, and `support_resistance._rolling_vwap()` is a `series[-1]` wrapper. That
refactor was verified numerically identical to the old scalar code across 48 combinations (daily
vs. 4h-style timestamps × with/without `None` volumes × n=1/5/40/900 × the 4 engine windows), plus
an end-to-end `detect_levels()` run on synthetic data, precisely because the engine had a
previously OOS-tested feature riding on it. `src/speculation.py` imports nothing from
`support_resistance.py`, so the new dependency direction (engine → indicators) introduces no
cycle. A series (not a scalar) is also what makes option B possible at all later — you can't
backtest a number that only exists "as of today".

Deliberately NOT done: any actionable claim. The section's closing caption states outright that
no OOS test has been run for VWAP in this project, names the test that would have to pass (the
same 60/40 chronological split and 5/10/20/30-day horizons as every other signal here), and
discloses that the engine's VWAP component weighs 0 — so nobody reads the score as being partly
VWAP-driven.

**The study itself (`scripts/vwap_oos_validate.py`), written immediately after, still unrun.**
The user asked for it right away ("ya mismo"), so it exists — but it has never seen real data:
Binance is unreachable from this repo's remote sessions, so it has to be run locally, and until
then nothing about the section's display-only status changes. What it tests: the signal is
`(close - vwap) / atr` — distance normalized by ATR(14), so it means the same thing across coins
and volatility regimes, the same normalization the Zone Engine applies to all its tolerances.
The sweep is 3 VWAP windows (7/30/365) × 2 sides (price above / below) × 3 thresholds
(0.5/1.0/1.5 ATR) × 4 horizons. Deliberately agnostic about direction: mean-reversion ("far below
the average cost comes back") and momentum ("far below keeps falling") are both plausible, and
the sign decides — exactly how the Fear & Greed check ended up finding momentum rather than the
classic contrarian story.

Three bars to clear, and the third is the one that usually kills things here: every horizon holds
its sign train-vs-test; all three thresholds agree with each other AND share one sign (a lone
threshold passing between failing neighbours prints as `FRAGIL`, not as a pass); and then stage 2,
redundancy — "regime + VWAP condition" is compared against "regime alone", not against all days.
Price above its VWAP and price above its moving averages are close relatives, so a VWAP signal
that adds nothing inside `classify_regime_series`'s regimes is the regime restated, not new
information. That stage needed a baseline narrower than "every day in the slice", which
`run_oos_validation` couldn't express — hence the new `baseline_condition` param there (default
`None` keeps the old unconditional behavior; the RSI-overbought refinement and the Fear & Greed
redundancy check had both hand-rolled this same comparison before).

Verified without market data, on synthetic series: an AR(1) mean-reverting process is detected
(`VALIDADO`, with the direction reported as reversion, both sides), and a pure random walk yields
nothing — its two near-misses come out as `FRAGIL` rather than passes, which is the threshold
sweep doing exactly the job it exists for. A validator that can only ever say no would be useless,
so both halves of that check matter.

**Walking touches against the 4h reference series instead of daily.** Follow-up to
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
`financial-advisor-speculation`'s "Do not re-add without re-validating" — and this exact question, in a
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

**Fear & Greed Index added as static content (2026-08-10), then investigated as a possible
signal and found redundant with the already-validated regime signal — shown descriptively only,
same standing as ADX/OBV.** `render_fear_greed_index()` (`src/ui/cripto.py`) + `src/data/
fear_greed_client.py` (alternative.me, no API key, one value for the whole crypto market — see
that module's docstring for why it has no per-symbol parameter unlike every other `src/data/*`
client) render above the ticker selectbox, outside anything the selectbox controls — verified via
`AppTest` that switching BTC/ETH/SOL leaves this section byte-for-byte identical.

Immediately after shipping it, the user asked whether Fear & Greed relates to strong trending
moves — worth testing given the index's own "momentum/volume" component (~25% of its weight per
alternative.me's methodology) already overlaps conceptually with this project's own regime
classification. Ran the full OOS treatment (chronological 60/40 split, `scripts/oos_validate.py`,
horizons `[5, 10, 20, 30]`, `min_observations=15`) against ~8.5 years of daily history
(2018-02-01 onward, alternative.me's full available range) for BTC/ETH/SOL, throwaway script, not
committed:

1. **The moderate-threshold split (Miedo/"Fear or worse" ≤45 vs. Codicia/"Greed or better" ≥55)
   validated cleanly for BTC and ETH — all 4 horizons, same sign, both train and test.** The
   sign is the OPPOSITE of the classic contrarian narrative ("be greedy when others are fearful"):
   Fear predicted returns WORSE than the unconditional mean, Greed predicted returns BETTER —
   a momentum, not mean-reversion, signature. This is the same direction every other reversion
   hypothesis tested in this project has landed on (RSI overbought showing persistence instead of
   pullback, above; the rejected support/resistance proximity investigation in
   `financial-advisor-speculation`'s design-history finding a heavily-touched level is more likely
   to break than hold) — a repeating pattern across this project's tickers, not a one-off.
2. **The TRUE extremes (Miedo/Codicia "extrema", ≤25/≥75 — exactly what the gauge's colored bands
   highlight) did NOT validate for any ticker.** Sign flipped between train and test on most
   horizons for BTC, ETH, and SOL alike — smaller samples (there are always fewer extreme days
   than moderate ones) and noisier. Neither the contrarian story nor the momentum story holds up
   specifically at the extremes with this history.
3. **Refinement test — does Extreme Greed add anything BEYOND already being in a "fuerte" regime,
   or is it the same signal restated?** Filtered to days already classified `"fuerte"` by
   `classify_regime_series()` (`src/speculation.py`) and compared the Extreme-Greed subset's
   forward return against the REST of the `"fuerte"` days specifically (not against all days —
   same methodology as the RSI-overbought-within-regime refinement in
   `financial-advisor-speculation`'s design-history). Failed for all 3 tickers, nearly every
   horizon (one lone `[OK]` cell for BTC at 10d, nowhere near the "all 4 horizons" bar this
   project requires). **Conclusion: whatever real signal Fear & Greed carries for BTC/ETH is
   redundant with `REGIME_VALIDATED_COMBOS`** (`src/ui/speculation.py`) — same underlying
   momentum, not incremental information, consistent with the index's own methodology already
   folding in a momentum/volume component.
4. SOL: none of the 4 direct conditions validated at all — shorter listing history (2020+),
   noisier signs train-to-test.

Nothing shipped as a NEW signal — `render_fear_greed_index()` stays exactly as built, purely
descriptive with its existing "no es una señal validada por este proyecto" caption, same
standing as ADX/OBV. What DID ship, same session, right after this investigation: the user asked
to disclose findings (1)-(4) directly in the "📋 Plan de DCA sugerido" box itself (Especulación/
`speculation.py`, the `is_crypto` branch), cross-referencing today's live Fear & Greed reading
against whichever regime message that box already shows. This is disclosure, not a new trigger —
the box's `st.success`/`st.info` branching logic (`regime_has_validated_edge`/`regime_rsi_edge`)
is completely unchanged; the new `st.caption()` runs unconditionally alongside it, same pattern
as the ADX/OBV disclosure captions elsewhere in that function. Required promoting
`FEAR_GREED_BANDS`/`FEAR_GREED_LABEL_ES`/`_cached_fear_greed_index()`/`fear_greed_badge()` from
`cripto.py` to `shared.py` (the DCA box lives in `speculation.py`, a second real caller — same
threshold as everything else in `shared.py`). Do not build a "buy on Fear, sell on Greed" (or its
momentum-flavored inverse) feature on the strength of finding (1) without first deciding it's
worth surfacing as literally the same thing `REGIME_VALIDATED_COMBOS` already provides — the
honest framing, if this is ever revisited, is "Fear & Greed is a rough, redundant proxy for the
regime signal already in this app," not a new independent one.

**Wyckoff Spring, rejected for the 8 stock `TICKERS` in `financial-advisor-speculation`'s design-
history, was re-tested for BTC/ETH/SOL (2026-08-10) — and this time BTC and ETH validated
cleanly, though the effect runs OPPOSITE to what Wyckoff theory claims a spring predicts.**
Prompted by the user asking whether the stocks-only rejection had ever been checked against
crypto — it hadn't. Same exact definition and methodology as the original stocks test (see that
design-history entry for the full method): on day t, `support = min(low[t-lookback:t])`
(trailing, excludes today), a spring fires when `low[t] < support` AND `close[t] >= support`
(undercuts the recent range intraday, recovers same day) — chronological 60/40 split, horizons
`[5, 10, 20, 30]`, `min_observations=15`, 3 lookbacks (10/20/30) swept up front, plus the same
volume-confirmed variant (`volume[t] <` its own trailing 20-day average). Throwaway script, not
committed, against ~8-9 years of Binance daily history per ticker (BTC/ETH since 2017-08; SOL
since its 2020 listing).

Price-only result: **BTC and ETH validated at ALL 3 lookbacks, all 4 horizons each — 24/24
cells, zero threshold-fragility** (the textbook opposite of what sank ADX/OBV/Fibonacci/the
stocks version of this same investigation). SOL: none of the 3 lookbacks validated (sign
inconsistent train-to-test). The volume-confirmed variant failed for all three tickers — same
pattern as the stocks investigation, the extra filter roughly halves the sample and mostly pushes
cells below `min_observations` before a sign can even be checked.

**The sign is backwards from the textbook interpretation, and the two validated tickers tell
slightly different versions of that story — checked with the raw conditional means, not just the
gap, before concluding anything:**
- **ETH**: spring days show a NEGATIVE absolute mean forward return in BOTH train and test (e.g.
  20d: -2.9% train / -2.4% test) while the unconditional baseline over the same periods is
  strongly positive (+4.7% / +1.5%) — a real decline while the broader market kept climbing, not
  merely "underperforms a strong average."
- **BTC**: the gap vs. baseline is consistently negative across all 3 lookbacks (same validation
  criterion), but the spring's own absolute return flips sign between train (negative, e.g. -3.5%
  at 20d) and test (positive but below baseline, e.g. +1.8% vs. +2.4%) — "underperforms the
  market" is the stable claim here, "loses money outright" is not.

A Wyckoff spring is supposed to mark accumulation/a false breakdown ahead of a bounce — instead,
in this data, it's associated with subsequent underperformance (ETH: outright decline) relative
to how BTC/ETH behaved the rest of the time over the same ~8-9 years. Same class of result as
Golden Cross above (AAPL/TSLA rendering worse in "golden cross" than "death cross") — a real,
non-fragile, validated effect that contradicts the pattern's own textbook narrative, not a
methodology bug.

**Shipped same session, once the finding held up**: `render_wyckoff_spring()` in
`src/ui/cripto.py`, its own section (like Golden Cross, NOT inside the shared
`render_speculation_indicators()` — this never validated for stocks, so it must never silently
appear in Especulación; verified via `AppTest` that exactly one "🌊 Wyckoff Spring" subheader
exists across the whole app). `WYCKOFF_SPRING_VALIDATED_TICKERS = {"BTC", "ETH"}` gates it, same
pattern as `STOCK_SR_VALIDATED_TICKERS`/`SR_VALIDATED_TICKERS`. The pure computation
(`classify_wyckoff_spring_series()`, `WyckoffSpringReaction`,
`compute_wyckoff_spring_reactions()`) lives in `src/speculation.py`, mirroring
`classify_golden_cross_series()`/`compute_golden_cross_reactions()` — except a spring is a rare
discrete EVENT, not a sustained regime, so there's no symmetric "non-spring" state worth
computing; the UI computes the ticker's own unconditional mean per horizon inline instead, as the
comparison baseline. `WYCKOFF_SPRING_LOOKBACK = 20` (the middle of the 3 swept lookbacks, all 3
having validated — no single lucky parameter to defend). The message always shows the real gap
number for whichever ticker is selected (never a generic "alcista"/"bajista" label, same rule as
Golden Cross) and, whether or not a spring is active today, always states the caveat that this
runs opposite to Wyckoff's own textbook claim — `st.error` (not `st.warning`) when a spring IS
active today, matching this project's convention of giving a validated NEGATIVE finding the same
visual weight `st.success` gets for validated positive ones (see Portfolio's AAPL distribution-
zone finding for the same rule applied earlier).

**Simplified twice, same session, on direct user feedback.** First pass: "no es para nada clara"
— the dense one-line-per-horizon text (gap/win-rate/n all crammed together) became a plain
3-column table (horizon, spring return, ticker's own average), and the stocks-didn't-validate
aside was cut outright ("no interesa"). Second pass, after seeing that: "puede ser más sencillo"
— cut further to just two things: an `st.error`/`st.info` state line ("Spring activo hoy" /
"Sin spring activo hoy") and one `st.metric` with a single win-rate probability at a fixed
`WYCKOFF_SPRING_HEADLINE_HORIZON = 20` days. No table, no methodology caveat, no baseline-
comparison number in the visible UI anymore — all of that still lives in this file and in
`CLAUDE.md`, not in the app itself. `WYCKOFF_SPRING_HORIZONS_DAYS` (all 4 horizons) is still what
`compute_wyckoff_spring_reactions()` computes internally; the UI just reads the one entry it
needs off that list now instead of looping over all four.

**Upthrust (the Spring's mirror image, above resistance instead of below support) was tested for
BTC/ETH/SOL the same session and did NOT clear this project's bar — nothing shipped.** The user
asked directly whether Wyckoff's other named zones (accumulation/distribution, capitulation) were
conventional theory; while explaining that Upthrust/UTAD is the genuine formal mirror of Spring
(distribution's equivalent shakeout, above resistance instead of below support — "capitulación"
is not formal Wyckoff vocabulary, it maps loosely onto "Selling Climax" within accumulation), the
user asked to test it. Exact mirror definition: on day t, `resistance = max(high[t-lookback:t])`
(trailing, excludes today), fires when `high[t] > resistance` (pokes above intraday) AND
`close[t] <= resistance` (fails to hold, closes back at/below it same day). Same methodology as
Spring — chronological 60/40 split, horizons `[5, 10, 20, 30]`, `min_observations=15`, 3
lookbacks swept (10/20/30). Volume-confirmation variant tested `volume[t] >` its own trailing
20-day average — the OPPOSITE direction from Spring's `<`, deliberately: Wyckoff literature
characterizes a real Spring by LOW volume (no genuine supply pushing price down) but a real
Upthrust/UTAD by HIGH volume (heavy distribution into the rally), so mirroring Spring's `<`
verbatim would have tested the wrong theoretical claim.

Result: price-only NEVER validated cleanly for any ticker at any lookback — every lookback
failed at least the 5d and/or 10d horizon while 20d/30d tended to pass, an inconsistency-across-
horizons pattern, not a threshold-fragility one, but disqualifying either way under this
project's "all 4 horizons must hold" rule. The volume-confirmed variant (the theoretically
correct one) showed a real directional signal — again backwards from Wyckoff theory, same as
Spring (upthrusts predicted BETTER-than-average forward returns, not the decline UTAD is
supposed to anticipate) — but with real threshold-fragility across the 3 swept lookbacks: ETH
validated at 2 of 3 (10, 20), SOL at 2 of 3 (10, 30), BTC at only 1 of 3 (30). No ticker had all 3
neighboring lookbacks agree, the exact "looks good at one parameter, fails next door" signature
that sank ADX/OBV — a materially weaker result than Spring's clean 24/24 (BTC+ETH × 3 lookbacks
× 4 horizons) with zero failures. Nothing shipped — no `render_upthrust()`, no
`classify_upthrust_series()`. Do not add either without first getting a cleaner sweep than what
this session found; noticing that 20d/30d horizons passed far more often than 5d/10d across
nearly every variant is NOT grounds to retest looking only at those two — that would be exactly
the post-hoc horizon-narrowing this project's methodology exists to prevent. Ran as a throwaway
scratchpad script (not committed), same as every other investigation in this file.

**Multi-timeframe "fractal" sweep of régimen "fuerte", régimen+RSI≥70, and Wyckoff Spring (BTC/
ETH/SOL, 10 Binance timeframes from 15m to 1w) — investigation only, nothing shipped, no code
touched.** Following the timeframe-fragility tooling added for the "de acciones que tenemos
respaldado por backtesting" thread (`run_timeframe_sweep()` in `scripts/oos_validate.py`, plus
`get_historical_prices_multi_timeframe()` in both `binance_client.py` and `yfinance_client.py`),
the user asked directly whether any of Cripto's already-validated signals hold across
temporalidades, not just at the one temporalidad each was originally validated on — and which
ones are genuinely "fractal" (work consistently across adjacent/nested timeframes) vs.
temporalidad-specific. Explicit constraint: run entirely from a scratchpad script outside the
repo that only imports/calls existing pure functions (`classify_regime_series`,
`classify_wyckoff_spring_series`, `compute_rsi_series` from `src/speculation.py`;
`get_historical_prices_multi_timeframe` from `binance_client.py`; `run_timeframe_sweep`/
`run_oos_validation` from `scripts/oos_validate.py`) — do not touch or add to project code for
this. Ladder: 15m/30m/1h/2h/4h/6h/12h/1d/3d/1w (1m/3m/5m excluded — thousands of pages of
1000-candle history for a 5-9 year window would be impractical; 1M excluded — too few candles,
~96 in 8 years), horizons `[5, 10, 20, 30]` **bars** (not days — a 30-bar horizon on 15m data is
~7.5 hours, not 30 days; this is the same "horizon means something different per temporalidad"
caveat `run_timeframe_sweep()`'s own docstring already carries).

Result, per signal:

- **Régimen "fuerte" is the one signal that showed genuine fractal behavior.** BTC validated
  (`all_validated=True`, all 4 horizons, both train/test halves) across a CONTIGUOUS band —
  4h→6h→12h→1d — and failed everywhere finer (15m/30m/1h/2h) or coarser (3d/1w, both of which
  also showed wild, likely-spurious train-half swings, e.g. 1w train_gap=-69.88%, thin n). ETH
  matched closely (4h, 12h, 1d, 1w all passed) but broke at 6h in the middle of that band — not
  quite as clean as BTC's. SOL validated ONLY at 4h — consistent with SOL never validating this
  signal at daily in the original (single-temporalidad) work, and confirming SOL's regime signal
  isn't fractal at all, just a single lucky cell. Working theory for why the band has edges on
  both ends: the signal's underlying lookback (EMA55/SMA-based) represents a wildly different
  real-world window depending on temporalidad — 55 bars is ~14 hours at 15m vs. ~2.5 months at
  daily — so "regime" stops meaning the same thing once bars stop corresponding to a similar
  amount of real elapsed time.
- **Régimen + RSI≥70 refinement did NOT show fractal behavior — scattered, no contiguous band.**
  BTC passed at 15m, 30m, 6h, 12h, 1d, 3d but FAILED at 1h, 2h, 4h — i.e., passes at both very
  fine and mid-to-coarse temporalidades with a gap in between, the opposite of a clean band. ETH
  passed at 15m, 4h, 6h, 12h but not 1d (consistent with this refinement never having validated
  for ETH at daily in the original work) and not at any other cell. SOL failed at every single
  temporalidad. Read as reinforcing, not contradicting, this refinement's already-known fragility
  (originally validated for BTC-at-daily only) — a scattered non-contiguous pass pattern across
  10 cells is closer to what you'd expect from noise occasionally lining up than from a real
  effect, and is flagged to the user as needing the same multiple-comparisons skepticism this
  project already applies to threshold sweeps (testing 10 temporalidades raises the odds of a
  spurious hit, on top of the existing per-cell 4-horizon fragility check).
- **Wyckoff Spring is temporalidad-specific, not fractal — confirmed, not just assumed.** ETH
  validated ONLY at 1d (matching the original single-temporalidad finding exactly) and nowhere
  else in the ladder. BTC validated at 1h, 12h, and 1d — technically 3 passes, but scattered
  (no adjacency: 1h is isolated from the 12h/1d pair by five failing cells in between), so not a
  contiguous band the way régimen "fuerte" showed. SOL failed everywhere, consistent with SOL
  never validating Spring at daily either.

Bottom line reported to the user: régimen "fuerte" is the only signal here that generalizes
across a real temporalidad band (4h-1d, cleanest for BTC) rather than being a single-cell result;
the RSI refinement and Wyckoff Spring both remain what they already were — narrow, mostly
daily-specific findings — and multi-timeframe testing did not turn either into something broader.
No `REGIME_VALIDATED_COMBOS`/`REGIME_RSI_OVERBOUGHT_VALIDATED_HORIZONS`/
`WYCKOFF_SPRING_VALIDATED_TICKERS` constant was touched — all three already only encode the
single (ticker, temporalidad) cells previously validated (implicitly daily, since that's the only
temporalidad the app itself ever fetches for these signals), and this sweep didn't surface a case
strong enough to extend any of them to a new temporalidad or ticker. `git status` confirmed after
the sweep that no project file changed. Ran as a throwaway scratchpad script (not committed),
same as every other investigation in this file.
