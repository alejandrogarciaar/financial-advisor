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
clustering + regression + optimization, now living in the "🪙 Cripto" tab, `financial-advisor-cripto`
skill) and this time DID validate — but narrowly, only for BTC (support) and TSLA (support and
resistance, back when that tab still covered stocks).** The simple single-level/multi-touch
approach above still failed; see `financial-advisor-cripto` for the newer result and why it's scoped so
much narrower than a first read might suggest.

**The multi-method engine came back to stocks later, as descriptive-only infrastructure, not a
restored validation.** User asked directly for support/resistance on stocks. The engine itself
(`src/support_resistance.py`) was never crypto-specific in its logic, but it had picked up one
real crypto-specific dependency since the TSLA-era validation above: its most recent redesign
made its REFERENCE series 4h candles (see `financial-advisor-cripto`'s Design history), and yfinance has
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
redesign (see `financial-advisor-cripto`), so it cannot be assumed to still hold, exactly the same rule
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

**The simple "Soportes y Resistencias" trailing-min/max chart (Diaria/Semanal/Mensual/Anual) was
removed entirely, and the Market Reaction Zone Engine section moved into its exact position —
a later, explicit user request, once both engines existed side by side in the same tab.** With
the sophisticated engine now available right below it, the simple chart read as redundant (same
question, answered twice, once cheaply/without evidence and once with real statistical scoring)
— the user asked to drop the simple one and have the sophisticated one take its place in the
render order, rather than keep both or leave the sophisticated one at the bottom of the page
where it used to sit. Deleted along with the chart: `compute_support_levels()`/
`compute_resistance_levels()` (`src/speculation.py`, plus their only helpers
`_sorted_dated_closes()`/`_extreme_since()`/`DAILY_WINDOW_DAYS`/`SupportLevels`/
`ResistanceLevels`) and `LEVEL_CHART_COLORS`/`colored_metric()`/`SPECULATION_CHART_VIEWS`/
`render_levels_chart()` (`src/ui/speculation.py`) — confirmed via repo-wide grep that nothing
else referenced any of them before deleting, consistent with this project's "if you're certain
it's unused, delete it, don't leave dead code" rule.

Moving the Zone Engine into that exact slot required a real signature change:
`render_speculation_indicators()` gained a required `render_zone_engine: Callable[[], None]`
parameter, called via `st.divider(); render_zone_engine()` at the position the simple chart used
to occupy (right after "Medias móviles", before the DCA box / MACD / Bollinger / ADX / OBV).
Each caller (`render_speculation()`/`render_crypto()`) defines its own `_render_zone_engine()`
closure — capturing `ticker`/`historical_prices`/`closes`/`current_price` from its own enclosing
scope — and passes it in, rather than the shared function calling either tab's Zone Engine code
directly: `src/ui/cripto.py` already imports `render_speculation_indicators` FROM
`src/ui/speculation.py`, so `speculation.py` importing anything back from `cripto.py` (e.g. its
`_cached_sr_levels`) would be a circular import. The callback pattern sidesteps that entirely —
`speculation.py` never needs to know Cripto's caching function exists, and vice versa. Both
tabs' Zone Engine block content is otherwise byte-for-byte what it was before (verified: `python
scripts/verify_app.py` plus an `AppTest` run that actually clicks both "🔍 Calcular niveles
multi-metodología" buttons — not just the default unclicked render — 0 exceptions on both).

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
`financial-advisor-cripto`). Same out-of-sample methodology, different (much coarser,
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

**Wyckoff "Spring" was proposed for Acciones, redirected to Especulación, tested, and rejected
(2026-08-07) — no code shipped, nothing to revert.** The user asked whether the Wyckoff method
could be added to the Acciones tab. Acciones is deliberately the one part of this app with zero
timing language — 6 purely fundamental valuation formulas, no chart patterns of any kind (see
`CLAUDE.md`) — so a technical/timing pattern like Wyckoff belongs with RSI/MACD/ADX/OBV in
Especulación instead, same reasoning that's kept every other speculative signal out of Acciones
since this tab split existed. Confirmed with the user via `AskUserQuestion` before doing any
work (both the tab and which specific Wyckoff element to test — Wyckoff is a whole framework,
not one formula, so "spring" needed to be picked out and made concrete before it could be
backtested at all).

Defined the classic "Spring" shape in the simplest form that's actually testable: on day t, let
`support = min(low[t-lookback : t])` (trailing, excludes today — no lookahead). A spring fires
when `low[t] < support` (price undercuts the recent range intraday) AND `close[t] >= support`
(closes back at/above the broken level same day — the "recovery" that makes it a spring, not
just a breakdown). Ran the exact same OOS methodology as every prior investigation in this
tab — chronological 60/40 split, `REGIME_REACTION_HORIZONS_DAYS` (`[5, 10, 20, 30]`, this tab's
own convention, not Portfolio's `[20, 60, 90, 180]`), `min_observations=15` — across all 8
`TICKERS`, and (learning from the ADX/OBV/Fibonacci pattern above) tested 3 neighboring
lookbacks (10/20/30 days) up front rather than committing to one value and checking neighbors
only if it looked good.

Price-only result: AAPL validated at lookback=10 (negative sign, all 4 horizons, train n=47/
test n=24) but failed at 20 and 30. UBER validated at lookback=20 (positive sign — the opposite
direction from AAPL's result, train n=52/test n=25-27) but failed at 10 and 30. Every other
ticker (MSFT, AMZN, META, NVDA, TSLA, GOOGL) failed at all 3 lookbacks. This is the identical
one-parameter-only fragility signature documented for Fibonacci/ADX/OBV above — a result that
appears at exactly one lookback and vanishes at its immediate neighbors isn't a real effect,
it's noise that happened to land in a gap. Two different tickers validating in *opposite*
directions at *different* lookbacks is, if anything, stronger evidence this is noise than either
alone would be.

Then tried a volume-confirmed variant, since the "spring" in Wyckoff literature is specifically
characterized by *light* volume on the undercut (read as an absence of real supply, not a
capitulation) — added `volume[t] < trailing 20-day average volume` (window fixed at 20 to match
`OBV_SMA_PERIOD` rather than swept too, since sweeping both the price lookback and a volume
window at once would have multiplied the parameter search space and made any surviving hit even
more likely to be noise, not less). This came back weaker, not stronger: the added filter
roughly halved the sample size at every ticker/lookback, which pushed most train/test cells
below the 15-observation floor before a sign could even be evaluated (most horizons reported
`n/a`, not fail). Zero tickers validated across all 4 horizons at any lookback with volume
confirmation.

Conclusion: neither version of Wyckoff Spring cleared this project's bar. Nothing was added to
`src/speculation.py` or `render_speculation_indicators()` — do not re-add on the strength of
AAPL@10 or UBER@20's isolated results without redoing this test and getting a cleaner outcome
than what this session found (same standing bar as Fibonacci/ADX/OBV above). Unlike ADX/OBV,
this wasn't kept as a plain descriptive indicator either — a spring is a rare, binary event (a
few dozen occurrences over ~5 years per ticker, not a continuously-valued line like ADX/OBV),
so there's no equivalent "show it anyway, just don't claim it predicts anything" middle ground
the way there was for those two.

**Market Reaction Zone Engine re-validated for stocks under the current score formula
(2026-08-08) — TSLA-support cleared the bar; nothing else did.** After the Wyckoff investigation
above came back negative, the user asked what other strategies could be backtested; this was the
strongest concrete candidate on hand: `STOCK_SR_VALIDATED_TICKERS` had shipped as `{}` since the
"reaction quality" score redesign (see `financial-advisor-cripto`'s Design history for the redesign
itself), an explicit, disclosed gap rather than an oversight — the old TSLA/AAPL result was
derived under the PRE-redesign formula and, per this project's standing rule (never reuse a
validated result after reweighting without re-running the split), couldn't be assumed to still
hold.

Replicated the exact methodology already proven out for Cripto's own re-validation (same file,
"Round 1"/"Round 2" above): chronological 60/40 split, horizons `[5, 10, 20, 30]`, score
percentiles `[40, 55, 70]` to check threshold-fragility (same-sign-in-train-and-test required at
ALL 3 percentiles and ALL 4 horizons to count as validated — no partial credit). Ran as a
throwaway script (not committed), against `detect_levels()` output for each of the 8 `TICKERS`
under `daily_reference_config()` (support+resistance kinds, so 16 combos total).

Result — one clean pass, everything else negative:

| Ticker | Support | Resistance |
|---|---|---|
| **TSLA** | **✅ validated** (positive sign, all 3 percentiles, all 4 horizons; train n=43-87, test n=21) | not validated (sign flips) |
| AAPL, MSFT, AMZN, META, NVDA, UBER, GOOGL | not validated | not validated |

TSLA-support is a genuinely non-fragile result — same sign at 40/55/70 (not just one lucky
threshold) and at all 4 horizons, with the qualifying-zone day count large enough (43-87 train,
21 test) that this isn't a thin-sample fluke either. Notable that it's the SAME ticker (and same
side — support) that validated under the OLD pre-redesign formula too, i.e. this isn't a result
appearing out of nowhere; TSLA's support behavior held up across two structurally different
versions of the scoring engine. AAPL's old resistance result, by contrast, did not reproduce
under the current formula — consistent with that older AAPL result having been flagged even at
the time as having "threshold fragility."

Shipped as `STOCK_SR_VALIDATED_TICKERS = {"TSLA": {"support"}}` in `src/ui/speculation.py` — no
other code changes needed, since the "📋 Lectura validada fuera de muestra" section already
reads this dict generically (same rendering path Cripto's equivalent section uses). The
"no evidence for this ticker" caption shown for the other 7 tickers was updated to mention that
a fresh, full re-validation was actually run (not just that the old one is stale), and to point
at TSLA's card as the one exception, so a user browsing MSFT/AAPL/etc. understands the section
isn't uniformly untested — it was tested and came back negative for their specific ticker.

**Channel kind of the Market Reaction Zone Engine, tested the same day — negative result, no
code change.** Immediately after the support/resistance re-validation above, the user asked what
else could be backtested; extending to `kind="channel"` (paired near-parallel support/resistance
trendlines, `_detect_channels()` in `src/support_resistance.py`) was the cheapest extension of
work already done. One real wrinkle: channels don't carry a fixed `zone_low`/`zone_high` like
support/resistance (they're diagonal, so their price band moves day to day) — "is price inside
the channel on day i" had to be computed via `channel_support.value_at(i)`/
`channel_resistance.value_at(i)` (min/max-ordered) instead of a static range check. Same
methodology otherwise (60/40 split, horizons `[5, 10, 20, 30]`, percentiles `[40, 55, 70]`).
Result: very few channels detected per ticker (0-3; AAPL and META had none at all), and every
ticker that had any either had zero recent "near the channel" observations (the channel only
existed in the older portion of history) or failed at least one horizon. No ticker validated.
Nothing shipped — this is a pure negative result, recorded so a future session doesn't re-run
the exact same channel test expecting a different answer without new price history.

**Golden Cross / Death Cross (SMA50 vs SMA200), tested the same day as a follow-up — 3 tickers
validated with unusually large samples, but the sign is NOT uniform across them.** Also proposed
in the same "what else can we backtest" conversation. Key design decision: tested as a sustained
REGIME STATE (is SMA50 currently above or below SMA200, checked every day) rather than the rare
crossover EVENT itself — a crossover happens only a handful of times per ticker in ~5 years of
daily data, nowhere near enough for a meaningful chronological split, while the regime-state
framing (matching this file's own `classify_regime_series()` pattern, not a new idea) yields
hundreds of observations per ticker. 50/200 is the single canonical definition of this signal —
unlike Wyckoff's Spring lookback or the drawdown-bucket windows, there was no threshold to sweep
for fragility here, since there's no invented parameter to have gotten lucky with.

Result, run once against the regime state for all 8 `TICKERS`, horizons `[5, 10, 20, 30]`:

| Ticker | Result | Sign of the golden-cross-vs-death-cross gap | Sample (train / test) |
|---|---|---|---|
| **AAPL** | ✅ validated | negative | n=300 / n=362-387 |
| **TSLA** | ✅ validated | negative | n=163 / n=336 |
| **UBER** | ✅ validated | positive | n=393 / n=301 |
| MSFT, AMZN, META, NVDA, GOOGL | ❌ not validated | sign flips | — |

These are the largest samples of any validated result in this project so far (hundreds of
observations per cell, not dozens) — statistically the most robust finding to date, even though
it's also the most counterintuitive one. For AAPL and TSLA, being in "golden cross" (the state
folk wisdom calls bullish) predicted a WORSE forward return than being in "death cross" — the
opposite of the textbook story. This isn't a bug: a moving-average crossover is a lagging
indicator, so "golden cross confirmed" tends to arrive after a meaningful chunk of a rally has
already happened, while the "death cross" state spans the sharp initial snapback off a bottom —
exactly the kind of well-documented real-world critique of MA-crossover strategies (buying
confirmation instead of the move itself). UBER validated with the "traditional" positive sign
instead. Discussed with the user before shipping anything, specifically because a uniform
"golden cross = bullish" label would have been actively wrong for 2 of the 3 validated tickers —
the user agreed the fix is to show each ticker's own measured number instead of a shared
directional label.

Shipped in `src/speculation.py`: `classify_golden_cross_series()` (day-by-day `True`/`False`/
`None`, same shape as `classify_regime_series()`), `GoldenCrossReaction` +
`compute_golden_cross_reactions()` (raw conditional mean return per state — same "frozen
validated set, live-recomputed number" pattern as `compute_regime_reactions()`/
`current_bucket_reaction()` elsewhere in this project, NOT the train/test gap used to decide
validation — those are two different numbers on purpose, same relationship as everywhere else
this pattern appears). `GOLDEN_CROSS_VALIDATED_TICKERS = {"AAPL", "TSLA", "UBER"}` and the "📐
Golden Cross / Death Cross" UI section live in `src/ui/speculation.py`, appended at the very end
of `render_speculation()` — deliberately NOT inside the shared `render_speculation_indicators()`
function, since this was never tested for BTC/ETH/SOL and must not silently appear on the Cripto
tab (same reasoning already applied to `STOCK_SR_VALIDATED_TICKERS`). The UI never prints
"alcista"/"bajista" — it shows the ticker's own measured number for whichever state it's
currently in, and picks `st.success` (green) or `st.error` (red) by the actual sign of that
number, the same rule Portfolio's confirmed sell-zone message uses, not by any assumption about
which state "should" be good.

Verified live via `AppTest` for all 4 relevant cases (AAPL currently in golden-cross, positive
raw number, green; TSLA and UBER currently in death-cross, both positive raw numbers, green;
MSFT as an unvalidated ticker, correctly shows the "no evidence" caption instead) — 0 exceptions
in every case.

**Market-structure (BOS/CHoCH) signal from an external gold-trading bot was investigated and
rejected (2026-08-09) — same multiple-comparisons fragility signature as ADX/OBV/Fibonacci/
Wyckoff above.** The user shared a standalone XAU/USD Telegram-alert bot (SMC-style: M15 trend
filter, M5 structure setup with order blocks/liquidity sweeps, M1 entry confirmation) while
scoping the unrelated `scripts/telegram_alerts.py` feature (verdict-change notifications, see
`CLAUDE.md`), and asked what could be extracted from it. Two pieces of that bot's logic —
market-structure tracking (BOS: a close breaking a confirmed swing pivot; CHoCH: a BOS that also
flips the prevailing bias) and liquidity-sweep detection — were identified as genuinely novel vs.
anything in Especulación/Cripto today (RSI/MACD/Bollinger/ADX/OBV are all oscillators; nothing
here tracks directional swing breaks). Rather than porting either straight into a tab, BOS/CHoCH
was tested first, following this project's standing rule that nothing gets trusted without the
same OOS treatment as every signal above.

Extracted `pivot_high`/`pivot_low` + the `market_bias` bull_bos/bear_bos/bull_choch/bear_choch
loop faithfully from the bot's source (a `left`/`right`-bar-confirmed swing pivot, no lookahead —
the pivot at bar i genuinely isn't knowable until `right` bars later, matching how the bot's own
live loop behaves, not a backtest bug). Real methodological compromise, disclosed rather than
glossed over: the bot's `STRUCT_LEN=2` was designed for M5 candles (~10-minute swings); yfinance
doesn't have enough intraday history for a chronological 60/40 split with a meaningful sample, so
this ran on **daily** GC=F (gold futures, same Yahoo Finance symbol the bot itself used as its
own fallback) instead — a materially different swing size than the strategy was built for, which
is exactly why `STRUCT_LEN` was swept (2/3/5/10) rather than committed to one value, same
fragility-check habit as every investigation above. Tested BOS and CHoCH separately, both
directions (4 signal types × 4 `STRUCT_LEN` values × 4 horizons `[5, 10, 20, 30]` = 64 cells),
against ~26 years of GC=F daily closes (2000-08-30 to 2026-08-07, 6,508 candles).

Result: only 1 of the 16 (signal-type × `STRUCT_LEN`) combinations validated across all 4
horizons — bullish BOS at `STRUCT_LEN=2` (train_gap +0.06% to +0.56%, test_gap +0.14% to +0.92%,
same sign at every horizon, n=340-697) — and its own neighbors (`STRUCT_LEN=3/5/10`) each failed
at least one horizon (mostly 20d/30d), the identical "looks good at one parameter, fails next
door" pattern that sank ADX/OBV/Fibonacci/Wyckoff. Bearish BOS validated at 0 of 4 `STRUCT_LEN`
values. CHoCH (the reversal-only subset, far fewer signals per variant — as few as 29-48
train/test observations) validated at 0 of 4 for the bullish side and only 1 of 4
(`STRUCT_LEN=10`, bearish, n as low as 14-15 — right at the `min_observations=15` floor) for the
bearish side. Conclusion: market structure (BOS/CHoCH), at least as tested here, does not clear
this project's validation bar — not built into Especulación, Cripto, or the Telegram alert
script. Liquidity-sweep detection (the other piece flagged as novel) was NOT tested this session;
if revisited, test it the same way before shipping it either. Do not re-add BOS/CHoCH signal
code on the strength of the lone `STRUCT_LEN=2` bullish-BOS result without redoing this test
(ideally on intraday data closer to the bot's original M5 design, if a data source with enough
history for a real train/test split becomes available) and getting a cleaner outcome across
neighboring parameters than what this session found. Ran as a throwaway scratchpad script
(`run_oos_validation_sweep` from `scripts/oos_validate.py`, not committed) — not part of the
repo.

**Re-run same day on 30-minute GC=F candles — bearish BOS looked clean (16/16 cells), but this
is read as a single-regime artifact, not a stronger confirmation, and the rejection above
stands.** Requested explicitly to get closer to the bot's original M5 design than the daily run
above managed. yfinance caps 30m history at roughly 60-70 calendar days for this symbol (2,266
candles, 2026-05-29 to 2026-08-07) — the honest tradeoff is the reverse of the daily run's: much
closer to the bot's real timeframe, but only ~2.5 months of one continuous market move (gold fell
from ~$4,531 to ~$4,397 over the whole window) instead of 26 years spanning many regimes.
Horizons `[5, 10, 20, 30]` are 30-minute BARS here (2.5h–15h forward), not calendar days.

Bearish BOS validated at all 4 swept `STRUCT_LEN` values (2/3/5/10), all 4 horizons each — 16/16
cells, zero threshold-fragility on its own terms (e.g. `STRUCT_LEN=5`: train_gap -0.20% to -0.51%,
test_gap -0.14% to -0.38%, n=76-197). Read in isolation this would clear the bar. It is **not**
trusted anyway, for a reason specific to this data window rather than the fragility check: train
and test here are just the earlier and later halves of the *same* ~2.5-month downtrend, not two
genuinely different market conditions — `run_oos_validation`'s baseline subtraction already nets
out each half's own average drift, but a bearish-continuation signal validating cleanly during a
period that was, start to finish, one continuous decline is exactly the setup where a real effect
and "the trend kept going" are hardest to tell apart. The daily run above — 26 years, many
regimes, the far more powerful test — found bearish BOS did NOT validate at any `STRUCT_LEN`. A
short, single-regime window "confirming" what a multi-decade, multi-regime window rejected is a
reason for *more* suspicion of the short window, not less (same logic already applied when two
tickers validated Wyckoff Spring in opposite directions at different lookbacks, above — agreement
that only shows up in a narrow slice isn't corroboration). Bullish BOS and both CHoCH directions
still failed on 30m too (CHoCH sample sizes as thin as 10-35 total signals, mostly below
`min_observations`). Conclusion unchanged: nothing from this ships. If revisited with a data
source that has enough 30m/M5 history to cover more than one regime, that would be the version of
this test actually worth trusting.
