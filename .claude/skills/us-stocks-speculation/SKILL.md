---
name: us-stocks-speculation
description: Use when the user's request is scoped to the "🎲 Especulación" tab of the USStocks (Precio Justo) dashboard — stocks only (RSI, support/resistance, MACD, Bollinger Bands, ADX, OBV/volume). BTC/ETH/SOL live in the separate "🪙 Cripto" tab (`us-stocks-cripto`), not here. Points to the files that make up this tab so work stays scoped to them.
---

# Especulación tab — context map

This skill doesn't prescribe steps — it just points to what "the Especulación tab" is made of,
so work requested for this tab doesn't drift into Acciones/ETFs/Portafolio/Cripto code or
require re-discovering the file layout from scratch. The "## Design history" section below has
the full design rationale for everything in this tab — including several features that were
tried and removed after failing out-of-sample validation (Fibonacci levels, a régimen/horizonte
table, ADX/OBV as regime refinements) — read it before adding a new speculative signal. This
narrative used to live in `CLAUDE.md` directly; it moved here because it's only relevant when
work is actually scoped to this tab, and `CLAUDE.md` is loaded on every single conversation
regardless of topic — moving tab-scoped detail into the skill that's loaded on demand cuts the
always-on token cost without losing any of the detail.

**This tab is stocks-only (`TICKERS`) — BTC/ETH/SOL moved out entirely.** Two things used to
live here and don't anymore: the multi-methodology support/resistance engine (moved to its own
"🧭 Niveles" tab first, clustering/KDE/robust trendlines/Volume Profile/VWAP), and then the
crypto tickers themselves, indicators and all (moved to the renamed "🪙 Cripto" tab once it
absorbed Niveles). See `us-stocks-cripto` for both.

## `src/ui/speculation.py`

App code used to be one 2821-line `app.py`; it's split into `src/ui/*.py` (one file per tab,
plus `shared.py` for cross-tab plumbing) with `app.py` now just the thin entry point (page
config + tab wiring). This tab's code — AND the indicator stack shared with Cripto — lives in
`src/ui/speculation.py`:

- `render_speculation()` — thin: ticker selectbox (`TICKERS` only), fetch via
  `_cached_historical_prices(ticker)` (`src/ui/shared.py`), sticky price, then a single call to
  `render_speculation_indicators("speculation", ticker, historical_prices, closes,
  current_price, is_crypto=False)`.
- `render_speculation_indicators(key_prefix, ticker, historical_prices, closes, current_price,
  is_crypto)` — the actual indicator stack (RSI, EMA/SMA section, the simple "Soportes y
  Resistencias" trailing-min/max chart via `render_levels_chart()`/`SPECULATION_CHART_VIEWS`,
  the "📋 Plan de DCA sugerido" box, MACD, Bollinger Bands, ADX, OBV) — shared with the Cripto
  tab: `us-stocks-cripto`'s `render_crypto()` (`src/ui/cripto.py`) imports this exact function
  and calls it with `key_prefix="crypto"`, `is_crypto=True`. Two things to know before touching
  it:
  - `key_prefix` exists purely to keep widget keys unique across the two callers — `st.tabs()`
    isn't lazy (see CLAUDE.md), so both tabs' bodies run every rerun, and any hardcoded
    `key=` inside this function WILL collide between Especulación and Cripto
    (`StreamlitDuplicateElementKey`, hit for real while splitting this out — currently only the
    S/R window `st.segmented_control` needs it: `key=f"{key_prefix}_chart_view"`). Any new
    widget added inside this function needs the same treatment.
  - `is_crypto` gates exactly one thing: the DCA box, which doesn't apply to stocks (no DCA-plan
    concept for them in this project). Everything else runs identically for either caller.
- `REGIME_VALIDATED_COMBOS`, `REGIME_RSI_OVERBOUGHT_VALIDATED_HORIZONS` — static lookups from
  one-off out-of-sample backtests, keyed by BTC/ETH/SOL — these only ever matter when
  `render_speculation_indicators()` is called with `is_crypto=True`, i.e. from the Cripto tab,
  even though the constants/functions themselves still live in this file/`speculation.py` (the
  computation module, not this UI one). Only update by re-running the same chronological
  train/test methodology, never by loosening the threshold to make a ticker pass. Note: they
  were derived from yfinance-sourced crypto history; Cripto now feeds them Binance-sourced
  history instead — see `us-stocks-cripto`'s Design history for why that wasn't re-validated (a
  deliberate judgment call, not an oversight).

## `src/ui/shared.py`

- `render_sticky_price()` — shared helper (also used by Acciones' `render_detail()`, ETFs, and
  Cripto) for the floating price card; don't fork a speculation-only copy.

## `app.py`

- Just the tab wiring now (`st.tabs()` call + one `with tab_especulacion:` block importing
  `render_speculation` from `src/ui/speculation.py`).

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
  lookups in `src/ui/speculation.py`; keep even if `src/ui/speculation.py` stops calling one of
  them directly.

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

## Design history

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
