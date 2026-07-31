# Especulación tab — Design history

Full design rationale for everything in this tab: what was tried and rejected, why the shipped
version looks the way it does, and the real bugs found along the way. Moved out of `SKILL.md`
(where it used to be inline) so the skill's file-map loads fast on every invocation — this
narrative only needs to be read when a task actually touches the history it covers (adding a new
speculative signal, changing the DCA box, changing the support/resistance chart or the Market
Reaction Zone Engine section).

**Support/resistance chart**: "Soportes" and "Resistencias" are one unified section (not two),
and — after real user feedback that 6 levels + 6 metrics at once was too much information —
both the **metrics and the chart show only ONE support/resistance pair at a time**, driven by
the same 4-way `st.segmented_control` ("Diaria" / "Semanal" / "Mensual" / "Anual", default
"Semanal", `key="speculation_chart_view"`). `SPECULATION_CHART_VIEWS[view]` is the single
source of truth both `colored_metric()` (the visible numbers) and `render_levels_chart()` (the
plot + its window) read from — change the mapping there once, both update together; don't
special-case one without the other. The 4 states map 1:1 to the 4 windows
`SupportLevels`/`ResistanceLevels` actually compute — Diaria → 14-day chart zoom + `daily`
level (a `DAILY_WINDOW_DAYS=3`-session trailing min/max in `speculation.py`, since a single
day's close has no range of its own — it's the shortest window the data actually supports, not
a literal single day), Semanal → 30-day chart zoom + `weekly` level, Mensual → 90-day zoom +
`monthly` level, Anual → 365-day zoom + `yearly` level. An earlier version shifted the labels
by one tier (a "Diaria" state that actually showed the `weekly` level, with no `daily` field at
all) to give the chart a shorter-than-weekly view; real usage showed that just reads as a bug
("selecciono diario, la leyenda dice semanal") no matter how it's justified internally, so a
real `daily` tier was added instead of reusing `weekly`. Never rename a state without also
pointing its `support`/`resistance` keys at the matching field name — that mismatch is exactly
what caused the original bug. `colored_metric()` is hand-built HTML since
`st.metric` has no text-color param. Dashed lines are
supports, dotted are resistances (a shared-per-family visual cue on top of each line's own
color). Colors (`LEVEL_CHART_COLORS`) come from the dataviz skill's validated categorical
palette (`references/palette.md`) in its validated adjacent-pair order for 7 of 9 slots — blue
(price), orange/aqua/yellow (weekly/monthly/yearly support), magenta/green/violet
(weekly/monthly/yearly resistance) — not an ad-hoc choice, and
deliberately not the app's status greens/reds (this is identity, not good/bad).
`support_daily`/`resistance_daily` exceed the palette's 8 validated categorical slots (price + 3
supports + 3 resistances already use 7, leaving only 1 free) — chosen by eye (`#e34948` /
`#8a5a2b`) for clear separation from `price`'s blue and from each other, since those 3 lines are
the only ones ever sharing a screen in the Diaria view; not re-run through the dataviz skill's
`validate_palette.js` CVD check because Node isn't installed in this environment — re-validate
if that ever changes. Keep any new
level's `colored_metric()` color in sync with its chart-trace color if one is added. Chart
background is transparent (`rgba(0,0,0,0)`) rather than theme-matched, so it blends with
whatever Streamlit theme is active without needing to detect it — there's no reliable way to
read the client's actual rendered theme from server-side Python here. The price line itself is
drawn at `width=3` (vs. `width=2` for the dashed/dotted support/resistance reference lines) so
it reads as the primary series — a deliberate emphasis exception to the dataviz skill's default
2px mark spec, not an oversight.

**Using more history / multiple touched levels for support-resistance was investigated and
rejected — same dead-end pattern as Fibonacci below, kept here for the same reason (stop a
future session from re-treading this).** The request was to extend `yearly`'s support/
resistance beyond the current trailing-365-day min/max — either by using all ~5 years already
fetched (`get_historical_prices()` is capped at `period="5y"` regardless — see
`src/data/yfinance_client.py:223` — so "more years" tops out there), and/or by marking multiple
support/resistance levels instead of one, specifically to underpin investment-plan/capital-
management decisions (not just a richer chart). Tested with a leak-free out-of-sample backtest
(same chronological 60/40 split as `REGIME_VALIDATED_COMBOS`): local-extreme pivots (a close
that's the min/max within a ±5-trading-day window, only counted as "known" `window` days after
it occurs — no using a pivot to predict a bounce it wasn't confirmed until later) clustered
within 2% into multi-touch levels (≥2 touches), then measuring the gap between mean forward
return when price is within 2% of a level vs. not, across BTC/ETH/SOL/AAPL/TSLA. Two results,
both against shipping this: (1) no ticker-general, sign-stable-in-train-and-test effect emerged
for either the current single-level or the multi-touch approach — most combinations either
flipped sign test-to-train or didn't clear the minimum-observations bar (stocks only have
~1,255 trading days in that 5y cap, so single-level touch counts were as low as 8-14); (2) the
one pattern that *did* hold sign across all 4 horizons in both train and test — SOL and TSLA,
multi-touch method, support proximity — pointed the **wrong direction for the stated goal**:
being near a well-touched support predicted *lower*, not higher, forward returns (-1% to -8%
across horizons). Same "momentum, not mean-reversion" signature already documented above for
RSI oversold: a support level being tested hard is more a sign it's about to break than a sign
it'll hold. Conclusion: don't build a capital-management/entry-signal feature on support-
resistance proximity, in either form. The existing single-window visual levels are left as pure
chart annotations, not a signal — no code changed as a result of this investigation.

**This exact question was later revisited with a much more sophisticated engine (multi-method
clustering + regression + optimization, now living in the "🪙 Cripto" tab, `us-stocks-cripto`
skill) and this time DID validate — but narrowly, only for BTC (support) and TSLA (support and
resistance, back when that tab still covered stocks).** The simple single-level/multi-touch
approach above still failed; see `us-stocks-cripto` for the newer result and why it's scoped so
much narrower than a first read might suggest.

**The multi-method engine came back to stocks later, as descriptive-only infrastructure, not a
restored validation.** User asked directly for support/resistance on stocks. The engine itself
(`src/support_resistance.py`) was never crypto-specific in its logic, but it had picked up one
real crypto-specific dependency since the TSLA-era validation above: its most recent redesign
made its REFERENCE series 4h candles (see `us-stocks-cripto`'s Design history), and yfinance has
no native 4h interval for stocks (the same limitation that made Cripto migrate to Binance in the
first place — reaggregating 60m bars was already tried and rejected as inferior). Fix:
generalized `BARS_PER_UNIT` (a fixed dict that hardcoded "reference = 4h") into `BARS_PER_DAY` +
`_bars_per_unit(tf, reference_tf)`, a ratio that adapts to whichever timeframe is actually the
reference — verified it reproduces the old fixed values exactly when `reference_tf="4h"` (zero
behavior change for Cripto). `daily_reference_config()` (`src/support_resistance.py`) builds an
`SRConfig` with `reference_timeframe="daily"`, `timeframes=("daily", "weekly", "monthly")` (no
4h/1h — no data source for them here), and the bar-count fields (`atr_period`,
`breakout_confirm_bars`, `episode_gap_bars`, `volume_confirmation_avg_period`,
`age_full_credit_bars`, `penalties["short_lifespan_bars"]`) divided by 6 back to their
pre-4h-redesign values — these are literally the engine's own numbers from before 4h became
mandatory, since a daily reference is exactly what the engine used to assume everywhere. ATR-
relative fields, `wilson_z`, `sane_price_*`, pivot lookbacks, etc. are untouched (never scaled
by bar-count in the first place). Confirmed end-to-end against real AAPL data: `age_bars` on
resulting levels read as plausible day-counts (e.g. 284, 296, 474 — roughly 1-1.5 years), not
compressed 6x, which would have been the signature of the bug NOT being fixed.

Explicitly scoped to **descriptive only** — matches Cripto's own current honesty: the TSLA/AAPL
validation above ran under the score formula from BEFORE the "reaction quality over touch count"
redesign (see `us-stocks-cripto`), so it cannot be assumed to still hold, exactly the same rule
already applied to Fibonacci/ADX/OBV (never reuse a validated result after reweighting without
re-running the same train/test split). `STOCK_SR_VALIDATED_TICKERS` in this file ships `{}` —
no fresh out-of-sample validation was run for stocks under the current formula as part of this
change (confirmed with the user as an explicit scope choice, not an oversight) — the UI's
"Lectura validada fuera de muestra" section says so plainly rather than silently reusing the
stale TSLA/AAPL result. If this is revisited, re-run the same chronological 60/40 split (4
horizons, several score percentiles to check threshold-fragility) that both the original TSLA
validation and Cripto's re-validation used — Cripto's own re-run of that exact test, under the
current formula, found `{}` too, so don't be surprised if stocks land there as well.
`scripts/oos_validate.py` (added in a later token-audit pass — see `token-audit` skill) is the
reusable tool for running this kind of chronological-split check without re-deriving the
methodology from scratch.

`render_advanced_levels_chart()` and the method/timeframe label dicts (`SR_KIND_RGB`,
`SR_METHOD_LABELS`, `SR_TIMEFRAME_LABELS`, `SR_TIMEFRAME_ORDER`) moved from `src/ui/cripto.py` to
`src/ui/shared.py` as part of this change — they were never crypto-specific, and once
Especulación needed them too they became genuinely cross-tab (same "confirmed by real use, not
intuition" rule `shared.py`'s own docstring already states). For Especulación's caller, the
chart's `historical_prices` and `reference_prices` arguments are literally the same array (both
are the daily series) — the "nearest reference bar" bisect inside the chart degenerates to an
identity mapping in that case, no special-casing needed.

**Fibonacci levels were tried and removed.** The full arc (kept here because it explains why
"Régimen y retorno histórico" below looks the way it does, and to stop a future session from
re-treading the same ground): reverse-engineered from a user-supplied external chart (4 price
levels matched a Fibonacci extension/retracement fit to within $20 on ~$60-71k values, far
better than classic/Woodie/Camarilla pivots), shipped as a 12-row table with a historical
"P(higher in N days | price at this % of its recent range)" per ratio, then — when asked "which
of these levels do institutions actually use" and "can we improve this with regime/volume" —
put through a real out-of-sample test (train on the older 60% of each ticker's history, test on
the newer 40%). It failed: persistence of the signal's direction from train to test was **at or
below the ~50% you'd expect from pure chance**, and got worse, not better, when conditioned on
trend regime (fuerte/débil/mixta) or on volume (touched the level on above-average vs. normal
volume) — the volume result even swung from 25% to 100% "persistence" across 9 nearby,
equally-reasonable choices of split point and volume-multiplier threshold, the textbook
fragility signature of multiple-comparisons noise, not a real effect. Root cause: ~5 years of
daily crypto closes, once split by ratio bucket (12) and then by regime/volume and then by
train/test, leaves too few observations per cell to distinguish signal from chance. **Do not
re-add Fibonacci-level probability/reaction code without redoing this out-of-sample validation
first** — it isn't a matter of tuning the tolerance or horizon, the whole approach didn't survive
the one test that matters.

**"📋 Plan de DCA sugerido" replaced Fibonacci**, shown only for BTC/ETH/SOL same as Fibonacci
was (today: only when `render_speculation_indicators()` is called with `is_crypto=True`, i.e.
from the "🪙 Cripto" tab — this box no longer renders in Especulación itself, see
`us-stocks-cripto`). Same out-of-sample methodology, different (much coarser,
much more statistically powered) hypothesis: instead of "is the price at this specific % of its
range," just "is the price sustained above/below its 3 moving averages" — literally the
decades-old academic "time-series momentum" effect, not something invented for this ticker.
This went through two UI iterations in the same session, worth knowing before touching it again:

1. First shipped as a 12-row table (régimen × horizon, with a "Validado OOS" ✅ column and a
   blue highlight on today's regime) *plus* the DCA suggestion box above it. The user's
   feedback: the table "valida la estrategia pero no ayuda a decidir" — technically correct but
   not actionable, since a table of historical returns isn't the same as "what do I do today."
2. Then, after adding the DCA box, the user said the table itself "no me aporta" (still true even
   with the actionable box now present) — **the table was removed from the UI entirely**, second
   time this exact "seemed useful, then the user says it isn't, remove it" arc has happened in
   this tab (see the earlier support/resistance "too much info" and "Diaria" mismatch episodes).
   The lesson generalizing across both: *validated* and *useful to look at* are different
   properties, and this app has a real, repeated pattern where a technically-correct
   descriptive/diagnostic table (something we can compute and defend) is not what actually helps
   the user decide — the decision needs to be pre-digested into a recommendation, not left for the
   user to read off a table themselves. Default to shipping the decision, not the raw table,
   for anything speculative going forward; add supporting detail only if asked.

**What's left in the UI is just the DCA box** — `current_regime = classify_trend_state(tr) if
tr is not None else None` (recomputed fresh here, not reusing the EMA/SMA section's own
`trend_state` variable, since that one is scoped inside its own `if tr is not None:` block and
may be undefined) compared against `REGIME_VALIDATED_COMBOS` (`src/ui/speculation.py`): `{("fuerte", 20),
("fuerte", 30)}` for BTC and ETH, empty set for SOL. `regime_has_validated_edge = any(regime ==
current_regime for regime, _horizon in validated_combos)` — `st.success` ("mantené o aumentá tu
aporte") only when true (in practice: BTC/ETH while in "fuerte"), `st.info` ("mantené tu plan
sin cambios") otherwise. Deliberately **not** "reduce/pause" in the neutral case: the
out-of-sample investigation never validated a *negative* edge for débil/mixta (only BTC
confirmed a small negative effect there at 5-10 days, ETH didn't — too thin to act on), so
recommending a reduction would overclaim exactly the way the discarded Fibonacci feature did.
Absence of a validated positive signal is not evidence of a validated negative one.

Both branches quote **real numbers computed from the selected ticker's own history**
(`compute_regime_reactions(closes)`, filtered to `current_regime`, via a small `_stat_phrase()`
helper) rather than a generic sentence with just the ticker name substituted in — this was a
direct user ask ("debe tener texto relacionado con la cripto seleccionada") after the first cut
read as boilerplate. In the `st.success` case this quotes the actual validated 20d/30d mean
return + win rate + sample size for *that* ticker (BTC's numbers differ from ETH's, both change
over time as more price history accumulates). In the neutral `st.info` case it still quotes the
current regime's numbers when available, explicitly labeled "a título informativo, sin
confirmar fuera de muestra" — showing an unvalidated number is fine as long as it's labeled as
such; hiding it entirely would be less informative without being any more rigorous. The SOL
branch of that same message also names BTC/ETH explicitly ("a diferencia de BTC/ETH, que sí
confirmaron...") instead of a ticker-agnostic disclaimer, so a SOL reader understands their
ticker specifically lacks what BTC/ETH have, not just that "some threshold" wasn't met.

`REGIME_VALIDATED_COMBOS` is a static lookup, not recomputed live — that OOS validation was done
once, in a throwaway scratchpad investigation this session (train on the older 60% of history,
test on the newer 40%; 2 of BTC/ETH's 4 "fuerte" checks held the same sign, SOL's didn't).
Recomputing a train/test split live on every rerun would be slow and would invite p-hacking the
split point until something looks good — exactly the failure mode that sank Fibonacci. If this
backtest is ever redone with more history, update `REGIME_VALIDATED_COMBOS` to match.
`compute_regime_reactions()` is called directly by `render_speculation()` again (only to pull
the current-regime phrase, not to build a table — see above), and `classify_regime_series()`/
`RegimeReaction` remain in `speculation.py` (the computation module) regardless of whether
`src/ui/speculation.py` calls them: they're the reproducibility path for
`REGIME_VALIDATED_COMBOS` (re-run them against fresh history to re-verify or update that
lookup), not dead/half-finished code; don't delete them as "unused" without first checking
whether `REGIME_VALIDATED_COMBOS` still needs re-deriving.
`classify_regime_series(closes)` is a day-by-day historical version of the same rule
`classify_trend_state()` (`src/ui/shared.py`) already applies to "today" (price ≥ EMA of `EMA_PERIOD` AND
≥ SMA of `SMA_SHORT_PERIOD` AND ≥ SMA of `SMA_LONG_PERIOD`, all three, imported from `trend.py`
so the two classifiers can't silently drift apart) — reuses `_ema_series()` (the MACD helper
that returns the full seeded EMA series, not just the final value) for the EMA leg and
`pandas.rolling().mean()` for the two SMA legs.

**RSI-within-regime refinement (`REGIME_RSI_OVERBOUGHT_VALIDATED_HORIZONS`, `src/ui/speculation.py`)**: a later
session tried a genuinely different hypothesis before extending `REGIME_VALIDATED_COMBOS` further
— RSI mean-reversion (oversold <30 → expect rebound, overbought >70 → expect pullback). Tested
with the same 60/40 chronological split, but comparing against each slice's own unconditional mean
return (not against zero) — BTC/ETH/SOL all have such strong positive drift over their full history
that nearly every RSI bucket shows a positive raw mean return regardless of whether RSI adds any
information, so the raw-sign check used for the regime signal is meaningless here and excess return
vs. baseline is required instead. Result: the mean-reversion hypothesis itself failed both legs —
oversold's sign flipped between train/test for all 3 tickers, and overbought showed the *opposite*
of reversion (persistent positive excess), which is a momentum signature instead. Rather than
discard that outright, it was tested as a candidate refinement of the momentum signal that already
shipped: within days already classified `"fuerte"`, does separating further by RSI ≥ 70 add
information over `"fuerte"` alone? Measured as the gap between mean forward return of
`"fuerte"+RSI≥70` vs. `"fuerte"` without RSI≥70, same chronological split. That gap held positive
sign in train and test at all 4 horizons for **BTC only** (e.g. 20d gap: +2.55%/train, +6.02%/test;
30d: +1.42%/train, +8.39%/test) — for ETH the gap's sign flipped between train (negative) and test
(positive), so it does **not** validate there, same "confirms for BTC/ETH but not SOL"-shaped
asymmetry as the base regime signal, just one ticker narrower. `RSI_OVERBOUGHT_THRESHOLD` (70.0),
`compute_rsi_series()` (day-by-day Wilder RSI, same smoothing as `compute_rsi()` but returns the
full series — needed to cross RSI against regime day-by-day) and `compute_regime_rsi_reactions()`
(mirrors `compute_regime_reactions()` but restricted to the `"fuerte"+RSI≥70` subset) live in
`speculation.py`. In `render_speculation()`, `regime_rsi_edge` is checked *before*
`regime_has_validated_edge` — when both are true (current regime is "fuerte" AND RSI ≥ 70 AND the
ticker has validated horizons in `REGIME_RSI_OVERBOUGHT_VALIDATED_HORIZONS`) the more specific
reinforced message wins, quoting `compute_regime_rsi_reactions()`'s own numbers rather than
`compute_regime_reactions()`'s, since it's a strictly narrower and more specific condition than
plain "fuerte" whenever it applies. Like `REGIME_VALIDATED_COMBOS`, this is a static lookup from a
one-off scratchpad investigation, not recomputed live — same p-hacking-avoidance rationale. Do not
generalize this to ETH or to other RSI thresholds/horizons without re-running the same train/test
check; it is a narrow, ticker-specific refinement, not a general "RSI adds value" finding (the
underlying RSI mean-reversion hypothesis that motivated the search failed outright).

**ADX was investigated as a further regime refinement and did NOT validate — shown only as a
descriptive indicator, same tier as MACD/Bollinger.** Same motivation as the RSI-overbought
refinement above: does ADX (a classic trend-strength gauge) separate forward returns further
within the "fuerte" regime, tested with the same chronological 60/40 split, same 3 tickers
(BTC/ETH/SOL), same 4 horizons? Two differences from RSI's clean result: (1) the effect's sign
flipped between train and test for most horizon/ticker combinations even at the textbook
ADX=25 threshold (only 2 of 4 horizons held sign for BTC and for ETH, 0 of 4 for SOL); (2)
re-running with nearby-but-equally-defensible thresholds (20 and 30) changed which horizons
"passed" — the same multiple-comparisons fragility signature documented for Fibonacci and for
the rejected support/resistance capital-management investigation above. Conclusion: do not add
an `REGIME_ADX_VALIDATED_HORIZONS`-style lookup or fold ADX into the "📋 Plan de DCA sugerido"
without redoing this test and getting a cleaner result than what this session found.

What *did* ship: `compute_adx()` (`src/speculation.py`) as a plain descriptive indicator,
rendered in `render_speculation()` right after Bollinger Bands, for **all** speculation
tickers (stocks and crypto alike) — same standing as MACD/Bollinger, which were never gated on
this project's own out-of-sample validation either; they're shown because they're standard,
well-established textbook indicators, not because this codebase specifically verified them.
The UI explicitly discloses the failed-refinement investigation in a caption so a user reading
the ADX section understands it doesn't feed into the DCA suggestion. ADX needed a change beyond
`speculation.py`: it's the first indicator in this tab needing daily **high/low**, not just
`close` — `get_historical_prices()` in `src/data/yfinance_client.py` now also returns `"high"`/
`"low"` per day (added, previously only `"date"`/`"close"`). Not touched in `fmp_client.py`
since `_cached_historical_prices()` (`src/ui/shared.py`) is hardcoded to the `yfinance` provider regardless
of which provider is active elsewhere in the app — pre-existing behavior for this whole tab,
unrelated to this change. `compute_adx()` returns `None` (rendered as "no hay suficiente
historial") if either list is short or a stale pre-this-change cache entry is missing
`"high"`/`"low"` — self-heals on the next successful live fetch, no migration needed.

**OBV (On-Balance Volume) was investigated the same session, right after ADX, and hit the
same kind of wall — shown descriptively, not as a validated refinement.** Same regime-
refinement test (60/40 chronological split, BTC/ETH/SOL, 4 horizons): does "fuerte + OBV rising
above its own N-day SMA" separate forward returns from "fuerte + OBV falling" better than
"fuerte" alone? Unlike ADX (which failed cleanly at every threshold tried), OBV's result at one
specific parameter — a 20-day SMA — looked genuinely clean for ETH: all 4 horizons held the
same sign train-to-test, with the gap actually growing larger in the test slice (a result that,
in isolation, would have looked at least as strong as the RSI-overbought finding that did
validate for BTC). It was **not** shipped as validated anyway, because the two neighboring,
equally-defensible choices — a 10-day and a 30-day SMA — did NOT reproduce that cleanness (2/4
and 1/4 horizons holding sign respectively, for the same ticker). A result that appears only at
one specific parameter value and not at its neighbors, discovered by scanning a few nearby
values rather than committing to one in advance, is the textbook multiple-comparisons signature
this project has already been burned by twice (Fibonacci's level/threshold sensitivity, and
ADX's own threshold sensitivity right above this entry) — reporting the 20-day result alone
without mentioning that 10/30 didn't replicate it would have been cherry-picking. No
`REGIME_OBV_VALIDATED_HORIZONS`-style lookup was added.

What shipped: `compute_obv()` (`src/speculation.py`) as a descriptive indicator, rendered right
after ADX in `render_speculation()`, for all speculation tickers. OBV's raw cumulative value is
not independently meaningful (it depends on wherever the available history happens to start),
so unlike RSI/MACD/ADX it isn't interpreted by its absolute level — the UI compares it against
its own `OBV_SMA_PERIOD`-day (20) moving average purely as a descriptive "is volume flow
trending up or down lately" read, then cross-references that against the *current* price trend
(`classify_trend_state(tr)`, recomputed fresh here for the same reason documented below for
`current_regime` — the EMA/SMA section's own `trend_state` is scoped inside its own `if tr is
not None:` block) to surface confirmation ("price up and volume agrees") vs. divergence ("price
up but volume doesn't back it") — the same cross-referencing pattern as `trend_context_note()`/
`quality_context_note()` elsewhere in this app: a logical read of two present-moment signals,
not a backtested claim, so it doesn't need its own OOS validation to be shown this way (same
justification MACD/Bollinger/ADX already rely on). `get_historical_prices()`
(`yfinance_client.py`) now also returns `"volume"` per day (added alongside `"high"`/`"low"`).
