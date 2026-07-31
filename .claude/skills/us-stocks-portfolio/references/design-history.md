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

The "📎 Contexto de valoración" section cross-references each held CDI against its underlying
`TICKERS`/ETF evaluation (`PORTFOLIO_CDI_UNDERLYING` in `config.py`) — reuses the existing
cached evaluations, no new computation.

**Drawdown-bucket accumulation zone (`src/drawdown_dca.py`, `DRAWDOWN_VALIDATED_BUCKETS` in
`src/ui/portfolio.py`)**: a line inside each "Contexto de valoración" card showing how far the underlying is
below its own trailing-252-session (~1y) high, and — only for the specific (ticker, bucket)
combinations that survived out-of-sample validation — a real, live-computed historical
reaction. This is one of several deliberate exceptions to the project's no-timing-language rule
(alongside Especulación and Cripto) — this time inside Portfolio, because the user explicitly
asked for it there: they wanted a DCA-planning aid, but were explicit that they did NOT want
support/resistance-style level-guessing (see `us-stocks-speculation`'s rejected investigation) —
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

**Diversification / aggregate risk-return / goal projection (3 sections after "Contexto de
valoración", `render_capital()` in `src/ui/portfolio.py`)**: added after the user reframed the project as
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
