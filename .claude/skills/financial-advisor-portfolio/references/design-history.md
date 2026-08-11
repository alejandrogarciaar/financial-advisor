# Portafolio tab — Design history

Full design rationale for everything in this tab. Moved out of `SKILL.md` (where it used to live
inline under "## Design history") so the skill's file-map loads fast on every invocation — read
this when a task actually needs the "why" (changing purchase semantics, the drawdown-bucket
accumulation zone, or the diversification/risk-return/goal-projection sections).

**Portfolio tracking (`src/portfolio.py`, "Portafolio" tab)**: the only part of the app that
persists user-entered data rather than API responses, so it deliberately lives outside
`.cache/` (gitignored, safe to delete — `portfolio_data/` is not). Purchases are entered in
**COP** (price paid per share + a fixed per-purchase commission, `DEFAULT_COMMISSION_COP` =
7,438 COP), not USD — this is the money the user actually spent, so `invested_cop` is a
straight sum of what was entered, with no FX conversion involved. The commission column
pre-fills that default on new rows only (`column_config.NumberColumn(default=...)` in
`src/ui/portfolio.py`), never retroactively on saved rows — editable per purchase, not a global setting.
Purchases are whole shares only (`validate_purchases()` rejects fractional input a pasted value
might sneak past the UI's `step=1`).

The ticker selector is restricted to `PORTFOLIO_CDI_TICKERS` (`src/config.py`) — Colombian
CDIs like `GOOGLCO`/`AAPLCO`/`MSFTCO`/`AMZNCO`/`CSPXCO` that track a `TICKERS`/`ETF_TICKERS`
company but trade natively in COP on the BVC (yfinance symbols like `GOOGLCO.CL`). The plain
USD tickers from `TICKERS` are intentionally **not** selectable here — the user only actually
buys the COP-denominated CDI, and since its quote is already in pesos, no TRM/FX conversion is
needed anywhere in this tab (`src/data/fx.py` exists but isn't currently used by the app for
this reason). CDIs also never get the 6 valuation formulas: their yfinance financials (EPS,
equity, FCF) are reported in USD (reused from the parent company's filings) while their quote
is in COP, so running DCF/Graham/multiples there would compare a USD fair value against a COP
price — meaningless, and redundant with the card that already exists for the underlying
ticker.

The "📎 Contexto de valoración" section used to cross-reference each held CDI against its
underlying `TICKERS`/ETF evaluation (`PORTFOLIO_CDI_UNDERLYING` in `config.py`) — reusing the
existing cached evaluations, no new computation. **Removed entirely by explicit user request
(2026-08-06)**: the user found it added little value and asked to delete it outright, keeping
only the laddered-buy-plan piece it used to host (see "Laddered buy plan" below) as its own
independent section. Everything in this and the next few paragraphs describing "Contexto de
valoración" cards is now historical — it explains why `PORTFOLIO_CDI_UNDERLYING` and
`DRAWDOWN_VALIDATED_BUCKETS` are shaped the way they are, not a description of what currently
renders. The drawdown snapshot line (📉 % vs. 1-year high, the "Zona de acumulación" success
message) survived the removal since it's the direct justification for the laddered plan, not
itself a valuation cross-reference — it's now shown inline in "🪜 Plan de compra escalonada"
instead of inside a valuation card.

**Drawdown-bucket accumulation zone (`src/drawdown_dca.py`, `DRAWDOWN_VALIDATED_BUCKETS` in
`src/ui/portfolio.py`)**: a line inside each "Contexto de valoración" card showing how far the underlying is
below its own trailing-252-session (~1y) high, and — only for the specific (ticker, bucket)
combinations that survived out-of-sample validation — a real, live-computed historical
reaction. This is one of several deliberate exceptions to the project's no-timing-language rule
(alongside Especulación and Cripto) — this time inside Portfolio, because the user explicitly
asked for it there: they wanted a DCA-planning aid, but were explicit that they did NOT want
support/resistance-style level-guessing (see `financial-advisor-speculation`'s rejected investigation) —
just "if this drops to around here, has that historically been a decent time to add." The
language stays descriptive ("esa franja rindió, en promedio, +7% a 90 días"), never imperative
("comprá ahora") — same register as Especulación's DCA box.

The underlying hypothesis is deliberately simpler than support/resistance: no pivot detection,
no clustering, just "% below its own trailing 1-year high," bucketed
(`DRAWDOWN_BUCKETS` in `drawdown_dca.py`: 0-5%, 5-10%, 10-15%, 15-20%, 20-30%, 30%+). Tested
with the same chronological 60/40 split used throughout this project (`compute_regime_reactions`
in `speculation.py` is the closest sibling — same shape: bucket → forward-return mean/win-rate/n,
gated on `DRAWDOWN_MIN_OBSERVATIONS=15`) across the 5 tickers Portfolio actually holds exposure
to (GOOGL, AMZN, AAPL, MSFT, and CSPX/S&P 500 via `PORTFOLIO_CDI_UNDERLYING`). Horizon fixed at
90 trading days (`DRAWDOWN_REACTION_HORIZON_DAYS`) — chosen *before* looking at which horizon
performed best (the middle of the 4 tested: 20/60/90/180), specifically to avoid the
"9 nearby, equally-reasonable thresholds" fragility that sank the original Fibonacci
investigation; no other horizon is exposed anywhere. `scripts/oos_validate.py` (added in a
later token-audit pass — see `token-audit` skill) is the reusable tool for running this kind of
chronological-split check without re-deriving the methodology from scratch.

Result, and why `DRAWDOWN_VALIDATED_BUCKETS` looks the way it does: moderate pullbacks (5-15%
off the 1y high) held the same sign of forward-return gap vs. the "0-5%, near the high" baseline
in both train and test, with enough observations, for GOOGL (5-10% only), AMZN and AAPL (both
5-10% and 10-15%), and MSFT (5-10%, 10-15%, **and** 20-30% — the one ticker with enough deep-
drawdown observations in the test slice to check). Deep drawdowns (20%+) could not be validated
for GOOGL/AMZN/AAPL/CSPX at all — not because the effect failed, but because the test slice
(roughly the last ~2 years) was mostly a bull run and simply didn't contain enough deep-pullback
days to measure anything (as low as 0-5 test observations). CSPX/S&P 500 has **no** validated
bucket — the ETF never fell far enough in the test window to check even the 5-10% bucket.
`DRAWDOWN_VALIDATED_BUCKETS` is keyed by the same `underlying` identifier
`PORTFOLIO_CDI_UNDERLYING` already uses (`"CSPXCO"`, not `"CSPX"`, for the ETF). Same honest
caveat as `backtest.py`'s docstring: this is 4 stocks + 1 ETF that "won" during a mostly-bullish
5-year window — a directional check, not a rigorous statistical validation, and don't assume it
would hold in a genuinely bearish regime that this data simply doesn't contain enough of yet.

Unlike `REGIME_VALIDATED_COMBOS` (which quotes numbers computed only from the train/test split
itself), the number actually shown to the user is recomputed live from the ticker's **entire**
available history via `current_bucket_reaction()` — the validated-bucket *set* stays frozen
(avoids re-running train/test on every rerun, same p-hacking-avoidance rationale as Especulación),
but the quoted mean return / win rate / n freshens automatically as more history accumulates,
without needing to redo the split. If a ticker's live-recomputed reaction for an otherwise-
validated bucket ever drops below `DRAWDOWN_MIN_OBSERVATIONS`, the UI falls back to the neutral
"sin confirmación histórica suficiente" message rather than showing a thin number — same
"absence of a validated signal is not evidence of a negative one" principle used in Especulación.
The UI-facing computation actually goes through `current_drawdown_snapshot()`, not
`current_drawdown_from_high()` directly — it additionally returns the reference prices
(current + trailing high, in USD) and the trailing high's date, so the card can show *why* the
% is what it is (e.g. "AMZN (USD): $231.39 hoy vs. máximo de $274.99 el 2026-05-06"), not just
the bare percentage — added after the first cut shipped with only the %, which user feedback
called out as not visible/prioritized enough (the fix that made it visible was promoting it to
a real `st.metric`, always shown, not gated on validation — a caption-only line was getting lost
for the common case of "not currently in a validated bucket", which is most holdings most of the
time). For stocks, `ev.historical_prices` is already in memory (`TickerEvaluation` carries it) —
no new fetch. `ETFEvaluation`, unlike `TickerEvaluation`, does **not** carry `historical_prices`
(a real bug hit this session — `AttributeError` on CSPXCO), so the ETF branch fetches separately
via `_cached_historical_prices(ETF_TICKERS[underlying])` (cached, usually already warm if
Especulación pulled it this run). This same per-ticker `dca_prices` is captured into
`underlying_prices: dict[str, list[dict]]` during this loop specifically so the 3 sections below
(diversification, aggregate risk/return, goal projection) can reuse it without fetching again.

**Laddered buy plan ("🪜 Plan de compra escalonada", `build_laddered_buy_plan()` in
`src/drawdown_dca.py`)**: added right after the sales/realized-gains feature, when the user
asked for help defining a scaled buy-sell pattern "taking advantage of the trend." Explicitly
steered away from inventing a new trend signal — every trend/momentum idea tried in this project
for stocks (Fibonacci, simple support/resistance, ADX, OBV) failed out-of-sample validation; RSI
only passed for BTC, not stocks (see `financial-advisor-speculation`). Instead of designing something new
and unvalidated, this formalizes what the drawdown-bucket accumulation zone (above) already
proved: split a user-entered COP budget across a ticker's VALIDATED buckets only, using the
existing `DRAWDOWN_VALIDATED_BUCKETS` gate — never all of `DRAWDOWN_BUCKETS`, never a bucket that
hasn't survived the chronological 60/40 split. Originally shown as an `st.expander` inside each
stock/ETF "Contexto de valoración" card; since that section's removal (2026-08-06, see above) it
renders inline in its own independent "🪜 Plan de compra escalonada" section instead — same
per-ticker card layout (2-column grid), just without the expander wrapper (redundant once the
section itself already carries that name) and without the valuation badge/quality-note content
that used to sit above it. Still gated on `DRAWDOWN_VALIDATED_BUCKETS.get(underlying)` being
non-empty (so it never appears for CSPXCO, which has none) and still only loops over
`held_tickers` (only what's actually bought).

The weighting across buckets — more budget to the deeper bucket — was an explicit user choice
among 3 options (equal split, user-defined per rung, weighted deeper) offered via
`AskUserQuestion`. Implemented as the simplest thing that satisfies "more weight to deeper": a
rank ratio over the ordered validated buckets (1 bucket → 100%; 2 buckets → 1:2 i.e. 33%/67%; 3
buckets, MSFT's case → 1:2:3 i.e. 17%/33%/50%), normalized to sum to 1. This ratio is **not**
itself something backtested — only using each bucket individually (buy the dip at a
statistically-confirmed level) was validated; the specific ranking-based split across buckets is
disclosed in the UI caption as a risk-management convention layered on top, not a separately-
proven claim. Deliberately not weighted by the live `mean_return` magnitude of each bucket
instead — that number is recomputed live and drifts as more history accumulates (same "avoid
p-hacking, don't refit on live data" principle as freezing `DRAWDOWN_VALIDATED_BUCKETS` itself),
so basing position sizing on its exact value would be a much stronger and shakier claim than what
the OOS test actually established (direction of the effect, not a precise magnitude to size by).

Price levels per rung were originally shown in the underlying's own currency (USD) —
`trailing_high × (1 − hi)` to `trailing_high × (1 − lo)` from each bucket's `(lo, hi)` bounds in
`DRAWDOWN_BUCKETS` — same "show the subyacente's USD price, not the CDI's COP price" convention
used everywhere else in the (now-removed) "Contexto de valoración" card, so the budget (COP)
sat next to a USD price range by design.

**Update (2026-08-06, same day as the "Contexto de valoración" removal, second request that
day): zone read + COP prices.** The user asked for two more things on top of making the section
independent: (1) an explicit, at-a-glance read of whether each holding is in an accumulation
zone, a "distribución/venta" zone, or neither ("en rango"); (2) show price levels in COP
instead of USD wherever possible, since the whole point of this tab is the COP-denominated CDI.

For (1): rather than invent a new signal, the 3-way split reuses pieces that already exist.
"Acumulación" = today's bucket is in the ticker's `DRAWDOWN_VALIDATED_BUCKETS` entry (unchanged
gate). "Distribución/venta" = today's bucket is `DRAWDOWN_BUCKETS[0]` ("0-5%", the shallowest
bucket, meaning price is near its own 1-year high) — deliberately **not** framed as a validated
sell signal in the UI copy, because this project has only ever backtested the accumulation side
(buy-the-dip); there is no equivalent OOS-validated "sell near highs" result to point to, and
claiming one would break the no-timing-language discipline this tab already commits to
elsewhere. "En rango" is the honest catch-all: a real, sometimes deep, pullback that isn't (for
this specific ticker) one of the statistically-confirmed buckets — better to say "no evidence
either way yet" than to either call it accumulation (overclaiming) or lump it in with
"near the highs" (factually wrong, since it's clearly not near the highs).

For (2): the underlying's USD price history stays the actual computation basis for the
bucket/validation math — switching to the CDI's own COP price history would silently invalidate
`DRAWDOWN_VALIDATED_BUCKETS` (validated against the USD series specifically) without re-running
the chronological 60/40 split, which is out of scope for a display change. Instead, a live
same-day conversion ratio (`_cached_portfolio_price(ticker)` in COP ÷ `snapshot.current_price`
in USD) is applied to both the current price and the trailing high (and to each rung's price
bounds) for display only. Because the same ratio scales both numbers, the computed drawdown %
— and therefore the bucket, and therefore the zone and the validated-reaction lookup — comes
out bit-for-bit identical to the USD-only computation; only what's printed on screen changes.
This is an approximation (assumes the CDI/underlying ratio held roughly steady across the
lookback window, the same premise `PORTFOLIO_CDI_UNDERLYING`'s "tracks 1:1" comment already
relies on) and the UI caption discloses that explicitly rather than presenting it as an exact
historical COP series. Falls back to the old USD display if the CDI's live quote isn't
available that run.

The per-rung table also moved from a stack of dense `st.markdown` lines to an `st.dataframe`
(user's other ask: "que esos valores se vean más fácil") — pre-formatted string columns rather
than `column_config` numeric formatting, matching how "Ganancias realizadas" already renders
its money/percent columns elsewhere on this tab, plus a new caption directly above the budget
input spelling out in plain language what entering a number there actually does (split it
across validated levels instead of buying it all today).

**Update (2026-08-06, third request that day): re-validating in COP, and cleaning up the zone
badge.** After the zone-read/COP-display redesign above, the user asked a direct feasibility
question: could the same drawdown-bucket OOS validation be re-run on the CDI's own COP price
history (its native BVC quote) instead of approximating from the underlying's USD history? The
methodology is fully reusable (`scripts/oos_validate.py`, same 60/40 split, same horizons
20/60/90/180, same `min_observations=15`) — the only change is which historical series feeds
it: `PORTFOLIO_CDI_TICKERS[ticker]` (e.g. `GOOGLCO.CL`) instead of `TICKERS`/`ETF_TICKERS`.

Run as a throwaway scratch script (not committed — same "not every investigation earns a
permanent file" convention as everything else in this document) against all 6 CDIs. Findings:
`AMZNCO`/`AAPLCO`/`MSFTCO` only have 74-136 rows of COP history on yfinance (the CDI itself
started trading recently) — not even enough for one 252-day trailing-high window, so the
question is currently unanswerable for them, not failed. `GOOGLCO`/`METACO` have ~472-493 rows
(~2 years) — enough to attempt it, but nothing validated (the 60% "train" slice barely clears
the 252-day warm-up before the split point, leaving too few train observations per bucket).
`CSPXCO` has by far the longest COP history (1,176 rows, since 2021-09) and its "5-10%" bucket
validated cleanly across all 4 horizons (train n=110, test n=164-197 depending on horizon) —
a real, evidence-backed result, not a thin one. This is the interesting case: CSPXCO has **zero**
validated buckets in the original USD-based analysis (the ETF's test-window drawdowns never
went deep enough in USD terms to check anything past 0-5%) — but its COP-native series, which
also carries the peso/dollar exchange-rate path on top of the S&P 500's own moves, apparently
did dip enough (and recover predictably enough) to produce a confirmed signal. Added as
`DRAWDOWN_VALIDATED_BUCKETS_COP = {"CSPXCO": {"5-10%"}}` right after
`DRAWDOWN_VALIDATED_BUCKETS`, keyed by the CDI ticker (the series actually under test) rather
than `underlying`.

The user's follow-up ("revisa integridad de la implementación con el backtesting") was a
correctness check, not a new feature: does the live UI, for a COP-validated ticker, actually
compute everything from that same COP series, or does it silently keep using the USD series and
just relabel the currency (which would make the displayed "confirmado fuera de muestra" claim a
lie)? The fix: each ticker now picks ONE basis for its whole card —
`DRAWDOWN_VALIDATED_BUCKETS_COP` (native COP, no conversion, real historical data) if that
ticker has an entry there and the fetch succeeds, else the original USD basis with the
fx-ratio-converted display from the redesign above. `snapshot`, `bucket`, `reaction`, and the
laddered plan all derive from whichever single series was chosen — never a mix. Separately, the
USD-denominated `underlying_prices[ticker]` used by "Retorno y riesgo del portafolio" (which
combines every holding into one synthetic series) is still fetched unconditionally for every
ticker regardless of which basis its own plan card uses, since that section's math assumes
every holding is in the same currency (USD) — CSPXCO having its own COP-native plan doesn't
change what feeds the portfolio-wide risk/return number.

Same request also asked to drop the "📉 Vs. máximo 1 año -X%" `st.metric` that sat next to the
zone badge ("no le veo utilidad") — removed; the percentage is still visible, just inside the
"hoy vs. máximo" caption underneath rather than its own metric tile. And a nomenclature note:
the card's title stays the CDI ticker itself (e.g. "AAPLCO", not "AAPL") regardless of which
basis that ticker's card uses internally — the title always names the instrument actually being
tested/tracked from the user's point of view, not an implementation detail of which price
series happened to back the math that day.

**Update (2026-08-06, fourth request that day): the "distribución" zone gets an actual
backtest.** After the COP re-validation above, the user asked directly: is the 🔴 "distribución/
venta" badge itself backed by any backtesting, or is it just the positional label its caption
already admits it is? Honest answer at the time: purely positional, never tested. The user then
asked to actually run that test, then — after seeing the result — to extend it to every
available ticker rather than special-casing the one that happened to validate.

Ran the same OOS methodology (chronological 60/40 split, horizons 20/60/90/180,
`min_observations=15`) with the condition "today's bucket is `DRAWDOWN_BUCKETS[0]`" (0-5%
drawdown from the 1-year high) against every ticker a Portfolio card could show it for, each
tested on the SAME series that ticker's card actually uses: GOOGL/AMZN/AAPL/MSFT/META in USD
(matching `DRAWDOWN_VALIDATED_BUCKETS`'s basis) and CSPXCO.CL in COP (matching
`DRAWDOWN_VALIDATED_BUCKETS_COP`'s basis, since that's what CSPXCO's card actually computes
from). Result: **AAPL is the only one that validated** — a negative forward-return gap, same
sign in train and test, across all 4 horizons (train n=172: -0.3%/-4.9%/-6.0%/-4.2%; test
n=121-192: -1.8%/-4.6%/-9.1%/-11.4%). GOOGL, AMZN, MSFT, META (USD), and CSPXCO (COP) all failed
at least one horizon (sign flips between train and test), so none of them get the confirmed
treatment. Added as `DRAWDOWN_VALIDATED_SELL_BUCKETS = {"AAPL": {"0-5%"}}`, keyed by
`underlying` like the accumulation dict (no COP-basis version needed yet, since nothing there
validated).

This is a real milestone for the project's discipline around timing language: every other
"validated" claim anywhere in this app (drawdown accumulation, RSI for BTC, the Market Reaction
Zone Engine) is a buy-side/accumulation thesis — this is the first time a sell/distribution-side
effect has actually cleared the same bar. It gets treated with exactly the same rigor and the
same visual/language pattern as the accumulation side, not a lesser one: when AAPL's card is
showing bucket "0-5%", the caption switches from the generic "no es una señal confirmada" to an
`st.error` (same visual weight as `st.success` on the accumulation side) quoting the actual
mean return/win rate/n — but still descriptive language ("esta franja rindió, en promedio..."),
never imperative ("vendé ahora"), matching every other DCA-adjacent caption in this tab. Every
other ticker keeps the original unconfirmed caption unchanged. `reaction` (the live-recomputed
`current_bucket_reaction()` call) now triggers whenever today's bucket is in EITHER the
accumulation gate or the sell gate for that ticker, not just the accumulation one, so the same
"recomputed live over full history, frozen validated-bucket set" pattern applies to both sides
identically.

**Diversification / aggregate risk-return / goal projection (3 sections after "🪜 Plan de compra
escalonada", `render_capital()` in `src/ui/portfolio.py`)**: added after the user reframed the project as
"somos un asesor financiero, damos herramientas de inversión" and asked what else would serve
that framing — all 3 chosen together from a shortlist. Unlike the drawdown-zone feature above,
none of these needed out-of-sample validation: they don't predict whether a price move is coming,
they describe composition (diversification) or apply standard, deterministic financial math
(aggregate risk/return, goal projection) to the user's own historical data — the same category
as `evaluate_risk_return()` itself ("100% retrospective, projects nothing"). The goal projection
is the one exception that gets a strong disclaimer anyway, since a forward-looking dollar figure
reads as a prediction even when the underlying math is just arithmetic on a historical rate.

- **🥧 Diversificación**: `PORTFOLIO_CDI_SECTOR` (`src/config.py`, next to
  `PORTFOLIO_CDI_UNDERLYING`) is a static CDI→sector map — not sourced from an API because
  neither provider's `get_profile()` reliably exposes sector/industry in this project (checked
  this session: yfinance's `.info` sector/industry came back `None` for all 4 stocks through our
  own `get_profile()`, which only maps `beta`/`lastDividend` anyway), and the universe is small,
  fixed, and unambiguous enough (Alphabet/Communication, Amazon/Consumer Discretionary,
  Apple+Microsoft/Technology per GICS; CSPX is a diversified fund, not a single sector) to hardcode
  safely — same justification as every other static fact table in `config.py`. Weight per holding
  = `current_value_cop / total` (computed from `summary`, already built above for the "Resumen
  por acción" table — no new computation), excluding/renormalizing around tickers with no live
  price. Rendered as a bar chart (not pie — the dataviz skill's form guidance disfavors
  pie/donut for magnitude comparison), one `go.Bar` trace per sector so Plotly's legend comes for
  free, colored from the dataviz skill's validated categorical palette in fixed slot order (blue
  Comunicación, orange Consumo discrecional, aqua Tecnología, yellow Diversificado — assigned by
  sector name, never by order of appearance, same rule `LEVEL_CHART_COLORS` in Especulación
  follows). Caption names the largest position and its % — descriptive, no invented "risk"
  threshold (no rule anywhere in this codebase for what concentration counts as "too much").

- **📈 Retorno y riesgo del portafolio**: `build_synthetic_portfolio_series()` (new,
  `src/portfolio.py`, next to `summarize_by_ticker`) combines each holding's underlying (USD)
  price history into one synthetic {date, close} index, weighted by *today's* value split,
  compounded from a daily weighted-return series (base 100). Feeds directly into the existing
  `evaluate_risk_return()` (`src/valuation/risk_return.py`, untouched) — zero new risk math, pure
  reuse. Explicitly a simplification: assumes today's allocation held constant across the entire
  lookback, not a real reconstruction of when each purchase happened (same category of disclosed
  simplification as `backtest.py`'s documented limits) — and it's USD/underlying-based, not
  COP/CDI-based, so it doesn't capture COP↔USD FX volatility the user's actual holding
  experiences. Dates are inner-joined across holdings before combining (CSPX trades in London,
  so its calendar doesn't perfectly match the US-listed names) — a holding missing from either
  weights or price history that run is dropped and the rest reweighted, rather than failing the
  whole section. Displayed with the exact same 2-row/3-column `st.metric` layout already used for
  a single ticker's risk/return in Acciones/ETFs (`src/ui/stocks.py`/`src/ui/etfs.py`) — same
  labels, same "100% pasado" caption, plus one added line clarifying the USD/no-FX simplification
  above.

**Sales / realized gains (`sales.json`, "Tus ventas", "💵 Ganancias realizadas")**: added after
the user sold their entire position in every held ticker except CSPX and asked for a section to
track how the resulting gains — net of the commission paid on the sale — were doing. Until this
point the tab was buy-only by design (see the removed rebalancing dead-end note above, which used
to cite this as the reason not to build it); this is the first place a sale is represented at
all.

- **Purchases stay untouched, forever** — a sale never edits, deletes, or reduces a row in
  `purchases.json`. The user was explicit about this while the feature was being built: they want
  their original purchase-price history to stay visible even after fully exiting a position, so
  that if they buy back in later they have an easy reference to compare the new price against.
  This is why sales live in their own file (`sales.json`, same row shape as purchases:
  ticker/shares/price_cop/commission_cop/date) instead of, say, adding a `sold` flag to purchase
  rows or deleting/shrinking them — net position is always computed as
  `purchased - sold` at read time, never persisted as a mutation.
- **Sales history is APPEND-ONLY — no edit, no delete, ever, by anyone.** A follow-up,
  non-negotiable rule the user stated explicitly right after the feature shipped: "Tus ventas"
  was rebuilt as a **read-only** `st.dataframe` history plus a separate
  `st.form("add_sale_form")` that only ever concatenates a new row and calls `save_sales()` —
  there is no code path in this form, or anywhere else in the UI, that can shrink or mutate
  `sales.json`. This is deliberately stricter than "ask before deleting" (the general
  portfolio-data rule) — for sales specifically, there's no confirmation dialog to click through
  at all, the capability doesn't exist. If a past sale was entered wrong, the fix is a new row
  that corrects it, never touching the old one — same principle as an accounting ledger, not a
  spreadsheet. This also applies to Claude directly: `scripts/add_sale.py` only ever appends
  (validated, same `validate_sales()` rules) and has no delete/edit subcommand — don't add one
  without the user asking for it explicitly.
- **Purchases moved to the identical UX right after, but for consistency, not policy.** The user
  followed up asking to apply "the same strategy" to "Tus compras" — explicitly scoped to "la
  dinámica, al UX," not a restatement of the sales golden rule. `_render_movements_editor()` (the
  `data_editor` + delete-confirmation state machine, originally shared by both tables, then
  purchases-only once sales moved off it) was deleted entirely once purchases moved to the same
  read-only-history + `st.form("add_purchase_form")` pattern as sales — it had no remaining
  caller. Net effect: neither table has an edit/delete path in the UI today, but that's a UX
  decision for purchases, not a business rule with the same "no one, ever" weight as the sales
  one — don't assume future purchase-editing work would be "breaking a rule" the way touching
  `sales.json` would be.
- **The row-by-row purchase history table was then removed from display entirely** (sales' still
  shows). Follow-up request, same session: the user's mental model is "current portfolio net
  holdings" + "sold shares," not a transaction ledger to review row by row — "Resumen por acción"
  (further down at the time, net of sales; renamed to "Mi Cartera" and moved up, see below)
  already answers what they actually check day to day, so seeing every individual purchase in
  "Tus compras" was redundant clutter, not useful detail. `st.form` stayed (still the only way to
  add a purchase); `load_purchases()` stayed (still needed to validate a new row and seed
  `purchases` for the rest of the tab) — only the `st.dataframe(saved_purchases, ...)` display
  block was cut. Explicit constraint from the user while asking for this: don't touch the storage
  layer (`purchases.json`, `load_purchases`/`save_purchases`) at all — display-only change. Sales
  keeps its visible history table for now (not asked to remove it) — don't assume the same
  simplification applies there without being told.
- **"Resumen por acción" renamed and moved to be the first thing on the page.** Same session,
  next follow-up: the user wanted their current holdings visible immediately on opening the tab,
  before the add-purchase/add-sale forms, not buried below them. Implemented by moving the
  `_render_price_dependent_sections(purchases, sales)` call from the bottom of `render_capital()`
  to right after the title/caption, and loading `purchases`/`sales` once at the top
  (`load_purchases()`/`load_sales()`) instead of loading them inline right before each form —
  since neither form needs an "edited vs. saved" reconciliation anymore (both are add-only forms,
  not `data_editor`s), there was no longer a reason to load them anywhere but once, up front. The
  whole fragment moved together (holdings table, unrealized-gain hero, comisiones, ganancias
  realizadas, contexto de valoración, diversificación, retorno/riesgo, proyección de meta) — the
  user only named "resumen por acción," but splitting that one table from the rest of the block it
  logically belongs with (e.g. "Total" right after it) would have produced a worse layout than
  moving the whole thing up together. The rename itself went through two names in quick
  succession: first "Portafolio" (matching the tab title), then the user immediately asked for
  "Cartera" instead since the tab is already called "💰 Portafolio" — a subsection repeating that
  exact word read as redundant — then refined that one more step to "Mi Cartera." Final name:
  **"Mi Cartera."** If asked to rename this again, check the current subheader text in
  `_render_cartera_and_total()` (see next bullet — the single fragment this used to be got split
  in two right after this) rather than trusting any name mentioned earlier in this file.
- **Third round: "Tus compras"/"Tus ventas" pulled apart from the rest of the analysis block,
  and `_render_price_dependent_sections()` split into two fragments.** Same session, one more
  follow-up: the user actually wanted "Tus compras"/"Tus ventas" immediately after "Total," not
  after the WHOLE block (comisiones through proyección de meta) as the previous round had shipped
  — asked and confirmed via `AskUserQuestion` with two concrete orderings shown side by side. This
  is NOT possible with a single `@st.fragment`-decorated function, since a fragment renders its
  entire body as one atomic unit — you can't pause it mid-way to let `render_capital()` inject
  "Tus compras"/"Tus ventas" and then resume. Fixed by splitting the one fragment into two:
  `_render_cartera_and_total()` ("Mi Cartera" + "Total" only) and `_render_portfolio_analysis()`
  (comisiones onward), called from `render_capital()` with the two add-forms sandwiched in
  between. Each fragment independently calls a new small helper, `_compute_held_summary()`
  (`held_tickers` + `summarize_by_ticker()`), instead of sharing one copy of `held_tickers`/
  `summary` the way the single fragment used to — fragments are separate rerun units with no
  shared local state, so there's no way to compute it once and hand it to the other.
  `_render_portfolio_analysis()`'s "Proyección de meta" similarly recomputes `total_value_cop`
  from its own `summary` instead of reusing the one `_render_cartera_and_total()`'s "Total"
  section computed — same reason. None of this duplication costs a real network call: the
  underlying price lookups are `@st.cache_data`-cached (900s TTL, see `PORTFOLIO_AUTOREFRESH_
  INTERVAL`'s comment above `_compute_held_summary()`), so a second `_compute_held_summary()`
  call within that window is a cache hit, not a re-fetch. Both fragments keep their own
  `run_every=PORTFOLIO_AUTOREFRESH_INTERVAL` auto-refresh, same as the original single fragment
  had, so this split doesn't change the auto-refresh behavior — it still won't reset the
  add-purchase/add-sale forms sandwiched between them.
  - **A real debugging detour along the way, worth remembering if "the app looks stale after a
    restart" ever comes up again:** while diagnosing why the user reported not seeing this
    reordering despite the server clearly serving current code (confirmed independently via
    `streamlit.testing.v1.AppTest`, which runs `app.py` fresh with no dependency on any running
    server process), `Get-CimInstance Win32_Process` revealed Streamlit spawns an internal CHILD
    process on Windows that ends up being the one actually bound to the port — and that child's
    `CommandLine` shows the SYSTEM Python install, not the venv, even though the venv Python was
    what `run_app.sh` invoked directly. Tried and ruled out two fixes that seemed plausible but
    didn't change anything: prepending `venv/Scripts` to `PATH` before launch (child still showed
    system Python — so it's not a PATH-lookup issue), and `--server.fileWatcherType none` (child
    still spawned — so it's not the file-watcher). Left as an open, apparently benign quirk (the
    child process still serves current on-disk code correctly on every fresh restart — confirmed
    via `AppTest` matching what a fresh `stop_app.sh`+`run_app.sh` cycle actually served) rather
    than sunk further time into it; `--server.fileWatcherType none` was kept in `run_app.sh`
    anyway since disabling a watcher this project never relies on (always restarts manually) is a
    reasonable simplification on its own, independent of whether it fixed anything. The ACTUAL
    resolution to "user doesn't see the change" turned out to be exactly what it looked like at
    first glance: the user genuinely wanted a different order than what had shipped, not a caching
    or stale-process bug at all — worth remembering before spending another long detour down the
    process-forensics path next time this complaint comes up.
- **Average-cost method, not FIFO lots.** A ticker's cost basis for a sale is
  `avg_price_cop × shares_sold`, where `avg_price_cop` is the same weighted-average-of-all-
  purchases number `summarize_by_ticker()` already computed for the held-position table before
  this feature existed. Deliberately not lot-tracking (which lot got sold first) — the existing
  avg-cost number was already the app's answer to "what did this ticker cost me," so reusing it
  for realized gains keeps one definition of cost basis instead of introducing a second,
  competing one. A consequence: selling shares does **not** change the average cost of whatever
  remains — `simulate_additional_purchase()`'s `current_avg_price_cop` is intentionally still
  computed from the full purchase history, not reduced to just the held remainder.
- **Commission treated symmetrically on both legs.** Buys already baked commission into cost
  (`invested_cop = shares×price + commission`, pre-existing). Sales mirror that:
  `net_proceeds_cop = shares×price − commission`, so the sale's commission reduces the gain on
  the way out just as the purchase's commission increased the cost on the way in. This was the
  specific thing the user called out when asking for the feature ("estas ganancias pagaron
  comisión") — `render_realized_gains_hero()` breaks out "Comisión de venta pagada" as its own
  tile precisely so that cost stays visible, not just folded into a net number.
- **`summarize_by_ticker()`/"Resumen por acción"/"Total" (unrealized) now only include tickers
  with net shares > 0.** A ticker fully sold has nothing to show a live price or unrealized
  return for, so it drops out of the held-position table and the unrealized-gain hero, and shows
  up in "Ganancias realizadas" instead. `held_tickers` (computed as `purchased_by_ticker -
  sold_by_ticker`, filtered to > 0 — later extracted into `_compute_held_summary()` when the
  single fragment this lived in got split in two, see further down) is what "📎 Contexto de
  valoración" and the 3 sections after it are now gated on, replacing
  the old `purchases.empty` gate — a ticker can have purchase rows and still not appear in any of
  those sections if it's been fully sold.
- **`_render_movements_editor()`** — the purchase and sale `st.data_editor` tables share this
  helper (extracted when the sale editor was added, since the delete-confirmation state machine
  was about to be duplicated verbatim). One real bug caught while building it: the first draft
  read `st.session_state[editor_key]` for the edited DataFrame, but `st.data_editor` with a `key=`
  set stores the widget's edit-delta dict there (added/edited/deleted row indices), not the
  merged DataFrame — only the function's **return value** has that. Fixed by having both call
  sites capture `st.data_editor(...)`'s return value and pass it in explicitly.
- **No FIFO, no partial-lot UI, no "which shares did I sell" tracking** — out of scope for what
  was asked. If the user ever needs tax-lot-accurate accounting (e.g. for a jurisdiction that
  requires FIFO/specific-lot reporting) this would need a real rework, not an extension.

- **🎯 Proyección de meta**: `project_future_value()` (new, `src/portfolio.py`) is the standard
  future-value formula for a lump sum plus a monthly ordinary annuity, compounded monthly from
  an annual rate — deterministic arithmetic, not a model needing validation; the caller owns
  whether the rate it passes is reasonable. Only shown if the aggregate risk/return section above
  produced a result (depends on it directly). Two inputs (aporte mensual COP, horizonte años,
  same column-of-`number_input`s pattern as "Simulador de precio promedio" below it) and 3
  output columns — one per CAGR window already computed above (1/3/5 años) — deliberately **not**
  labeled "pesimista/base/optimista" like the DCF's 3 scenarios, since which of the 3 historical
  windows is highest isn't consistent (order depends on the ticker/period, unlike DCF's
  deliberately-ordered scenarios) — labeling them "using your 1/3/5-year return" instead of
  inventing an optimistic/pessimistic frame avoids implying an ordering that isn't real. Carries
  the strongest disclaimer language in the app ("no es una promesa ni garantía... el desempeño
  pasado no asegura nada") specifically because a projected dollar figure reads as a prediction
  regardless of how mechanical the underlying math is — this is the one place in these 3 new
  sections where that risk is real, even though nothing here is an unvalidated signal.

**Drawdown-bucket coverage sweep (2026-08-10): re-tested against each ticker's FULL available
daily history (back to IPO) and 4 horizons (20/60/90/180d) instead of the single 90-day horizon
the live `current_bucket_reaction()` number uses — investigation only, no code touched, no
constant updated yet.** Same session as the Especulación coverage sweep (see that skill's
design-history for régimen/Golden Cross/Zona de Reacción) — this piece covers
`DRAWDOWN_VALIDATED_BUCKETS` specifically, run against all 8 `TICKERS` (not just the 4 the
current dict covers — GOOGL/AMZN/AAPL/MSFT), via a throwaway scratchpad script (not committed)
reusing `compute_drawdown_bucket_reactions`'s exact trailing-high/bucket math (`src/drawdown_dca.py`)
against `yfinance_client.get_historical_prices_multi_timeframe(ticker, "1d")`'s full IPO-to-date
series and `run_oos_validation` (`scripts/oos_validate.py`) — same chronological 60/40 split, same
"every horizon must hold sign in both halves" bar as everywhere else in this project. Horizons
20/60/90/180 were chosen to match the precedent already set for the CSPXCO COP-native
re-validation above, not confirmed to be identical to whatever horizon set originally produced
today's `DRAWDOWN_VALIDATED_BUCKETS` — so this is the same methodology, not a strict
apples-to-apples re-run of the exact original test. Treat the comparison below as directionally
meaningful, not as a byte-for-byte reproduction.

Result, bucket-by-bucket against what `DRAWDOWN_VALIDATED_BUCKETS`/`DRAWDOWN_VALIDATED_SELL_
BUCKETS` claim today:

- **GOOGL 5-10%** (currently validated) — **confirmed**, holds cleanly across all 4 horizons with
  full history. The one existing claim that survived unscathed.
- **AMZN 5-10% and 10-15%** (both currently validated) — **do NOT survive** full history + this
  horizon set. Both fail at least one horizon.
- **MSFT 5-10%, 10-15%, and 20-30%** (all 3 currently validated — MSFT has the most buckets of
  any ticker today) — **none of the 3 survive**. Full invalidation for MSFT specifically.
- New candidates that were never tested before (none of these tickers/buckets are in either
  dict today): **AAPL 15-20%**, **META 20-30%**, **NVDA 5-10%**, and — the strongest result in
  the whole sweep — **TSLA**, which validates cleanly in 4 of its 6 buckets at once (10-15%,
  15-20%, 20-30%, 30%+). TSLA has never been in `DRAWDOWN_VALIDATED_BUCKETS` at all; this sweep
  suggests it may be the single most drawdown-predictable ticker of the 8, not just absent from
  the dict for lack of testing.
- UBER: nothing validates (consistent with UBER's short 2019- history giving thin per-bucket
  samples at some horizons).

Net read: the picture this sweep produced is substantially different from what
`DRAWDOWN_VALIDATED_BUCKETS` currently encodes — one claim confirmed (GOOGL), four claims
(AMZN×2, MSFT×3) that don't reproduce with more data and a wider horizon set, and four new
candidates never tested before (AAPL, META, NVDA, and especially TSLA). Nothing in
`DRAWDOWN_VALIDATED_BUCKETS`/`DRAWDOWN_VALIDATED_SELL_BUCKETS` has been changed off this sweep —
flagged here for a deliberate decision, since `render_capital()`'s 🟢/🔴 badges and the "🪜 Plan
de compra escalonada" section currently show `st.success`/build a plan off the OLD dict, and
some of what they claim as confirmed did not reproduce here.
