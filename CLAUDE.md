# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A Streamlit dashboard ("Precio Justo — Acciones Americanas") that evaluates whether a fixed
list of US mega-cap stocks (`TICKERS` in `src/config.py`: AAPL, MSFT, AMZN, META, NVDA, TSLA,
UBER, GOOGL) look cheap, fair, or expensive right now, using 6 independent valuation formulas
grouped into 3 families. All user-facing text is in Spanish (Rioplatense).

## Skills — invoke proactively, don't wait to be asked

This repo ships `.claude/skills/us-stocks-*` skills. They're always listed as available, but
loading one only happens when it's explicitly invoked — so check this table and call the
matching skill via the Skill tool *before* starting work, whenever the request is scoped to one
of these areas:

| Task is scoped to...                                         | Skill                              |
|----------------------------------------------------------------|-------------------------------------|
| "📈 Acciones" tab (valuation cards, ticker list/filter, detail) | `us-stocks-stocks`                  |
| "🧺 ETFs" tab                                                   | `us-stocks-etfs`                    |
| "🎲 Especulación" tab (RSI, S/R, MACD, Bollinger, DCA, crypto)  | `us-stocks-speculation`             |
| "📊 Validación" tab (backtest, verdict history)                 | `us-stocks-validation`              |
| "💰 Portafolio" tab (COP purchases, holdings, contexto)         | `us-stocks-portfolio`               |
| Adding a new ticker or a new/modified valuation formula         | `us-stocks-add-ticker-or-formula`   |
| Launching/checking/stopping the Streamlit app to see a change   | `us-stocks-run-app`                 |

For requests spanning multiple tabs, invoke each relevant skill. This is in addition to the
general-purpose `dataviz` skill (any chart/plot work) — that one already triggers on its own
description and isn't specific to this repo.

## Running the app

```
pip install -r requirements.txt
streamlit run app.py
```

Requires `FMP_API_KEY` in `.env` (see `.env.example`) only if using the `fmp` data provider;
the `yfinance` provider works without any API key and is the default in the UI.

There is no test suite and no lint/build tooling configured in this repo.

To run the backtest (sanity-checks the triangulation verdict against actual historical
returns, not part of the app UI):

```
python -c "from src.backtest import run_backtest; print(run_backtest())"
```

## Architecture

**Provider abstraction (`src/data/`)**: `fmp_client.py` and `yfinance_client.py` both expose
the same 7 functions (`get_quote`, `get_profile`, `get_income_statement`,
`get_cash_flow_statement`, `get_balance_sheet`, `get_key_metrics`, `get_historical_prices`,
`get_analyst_view`) returning dicts with identical field names, so `fair_value.py` is agnostic
to which provider is active (`PROVIDERS = {"fmp": fmp_client, "yfinance": yfinance_client}`).
Every call is cached to disk under `.cache/` (`src/data/cache.py`); if a live call fails, the
provider falls back to its last good cached response and marks the result `from_cache=True`
rather than raising, as long as *some* prior cache exists. FMP's free plan blocks
`get_analyst_view` and limits statement history to 5 years; yfinance has no analyst-view
limitation but requires reconstructing historical P/E manually (see `get_key_metrics` in
`yfinance_client.py`).

**Valuation orchestration (`src/valuation/fair_value.py`)**: `evaluate_ticker()` does I/O
(fetches from the active provider) and delegates the actual computation to
`_evaluate_from_data()`, which is pure/network-free. This split exists so `src/backtest.py`
can reuse the exact same valuation logic against truncated historical financials ("what would
this formula have said N years ago") without duplicating it.

**The 6 formulas / 3 families**: `dcf.py`, `multiples.py`, `book_value.py`, `growth.py`
(PEG/PEGY), `graham.py` (Graham Number), and `graham_growth.py` (Graham's 8.5+2g formula) each
independently return a fair value and margin vs. current price. They are NOT treated as 6
equal votes — PEG, Graham Number, and Graham-growth all derive from the same EPS input and
are highly correlated, so `summarize_signals()` groups them into 3 genuinely distinct families
(`SIGNAL_FAMILIES` in `fair_value.py`): cash flow (DCF), book value, and earnings multiples
(median of the 4 EPS-derived methods). The headline verdict is a vote across these 3 families,
not across 6 formulas.

**Filters vs. signals**: `quality.py` (ROIC vs. WACC — does the business create value when it
reinvests?), `solvency.py` (interest coverage, debt/EBITDA), and `analyst_view.py` (Wall
Street consensus, yfinance-only) are deliberately excluded from `summarize_signals()` — they
answer different questions ("is this a good business", "can it pay its debt", "what does the
market expect going forward") than "is the price cheap or expensive right now", and mixing
them in would distort the price-based vote. `quality_context_note()` cross-references the
quality filter against the price verdict to flag cases like "looks expensive but ROIC
justifies a premium" or "looks cheap but is destroying value."

**Lynch category (`lynch_category.py`)**: a heuristic classifier that flags when
growth-dependent formulas (PEG, Graham-growth) are being applied outside their intended domain
(e.g. cyclicals, negative/erratic earnings) — it's a warning label, not a valuation method
itself.

**Trend (`trend.py`) and risk/return (`risk_return.py`)**: neither is a price signal — `trend`
is the EMA-55 momentum filter, `risk_return` is CAGR (1/3/5y) + annualized volatility + Sharpe
+ max drawdown, all 100% backward-looking. `risk_return.py` is shared between stocks
(`fair_value.py`) and ETFs (`etf_analysis.py`) so the math isn't duplicated — it filters out
stray `NaN` closes from yfinance gaps before computing anything (found via `CSPX.L` silently
producing `NaN` volatility from a single bad data point). `trend_context_note()` cross-
references the EMA against the price verdict, same pattern as `quality_context_note()`.

**Portfolio tracking (`src/portfolio.py`, "Portafolio" tab)**: the only part of the app that
persists user-entered data rather than API responses, so it deliberately lives outside
`.cache/` (gitignored, safe to delete — `portfolio_data/` is not). Purchases are entered in
**COP** (price paid per share + a fixed per-purchase commission, `DEFAULT_COMMISSION_COP` =
7,438 COP), not USD — this is the money the user actually spent, so `invested_cop` is a
straight sum of what was entered, with no FX conversion involved. The commission column
pre-fills that default on new rows only (`column_config.NumberColumn(default=...)` in
`app.py`), never retroactively on saved rows — editable per purchase, not a global setting.
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
`app.py`)**: a line inside each "Contexto de valoración" card showing how far the underlying is
below its own trailing-252-session (~1y) high, and — only for the specific (ticker, bucket)
combinations that survived out-of-sample validation — a real, live-computed historical
reaction. This is the **second deliberate exception** to the project's no-timing-language rule
(the first being Especulación) — this time inside Portfolio, because the user explicitly asked
for it there: they wanted a DCA-planning aid, but were explicit that they did NOT want
support/resistance-style level-guessing (see the rejected investigation above) — just "if this
drops to around here, has that historically been a decent time to add." The language stays
descriptive ("esa franja rindió, en promedio, +7% a 90 días"), never imperative ("comprá
ahora") — same register as Especulación's DCA box.

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
investigation; no other horizon is exposed anywhere.

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
valoración", `render_capital()` in `app.py`)**: added after the user reframed the project as
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
  a single ticker's risk/return in Acciones/ETFs (`app.py` ~line 871-887) — same labels, same
  "100% pasado" caption, plus one added line clarifying the USD/no-FX simplification above.

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

(A target-allocation/rebalancing section was tried and removed — not wanted as part of
Portfolio. If revisited, remember it needs to be buy-only: this app has no mechanism to
record a sale, `purchases.json` only ever grows.)

**Zone thresholds**: `ZONE_THRESHOLDS` in `fair_value.py` classify each margin
`(fair_value - price) / price` into "Acumulación fuerte" / "Acumulación" / "Precio justo" /
"Sobrevalorado", calibrated to the classic Graham/Buffett margin-of-safety bands.

**`st.tabs()` is not lazy**: all 5 tabs' bodies execute every rerun, in fixed script order —
`tab_acciones` → `tab_validacion` → `tab_etfs` → `tab_especulacion` → `tab_capital` (Acciones,
Validación, ETFs, Especulación, then Portafolio always last, by user request) — regardless of
which tab is visually active; this is a Streamlit characteristic, not a bug here. The tab
*labels'* order in the `st.tabs([...])` call controls the visual left-to-right order, and the
`with tab_X:` blocks further down are written in that same order on purpose — the two are
independent in Streamlit (code order drives execution order regardless of label order), but
keeping them in sync avoids the dependency between them silently drifting apart. A slow tab
earlier in that order delays every tab after it, including its spinner (nothing renders for a
later tab until its `with tab_X:` block is actually reached) — this is exactly why Portafolio
must stay last: it reuses `STOCK_EVAL_CACHE_KEY`/`ETF_EVAL_CACHE_KEY` populated by Acciones and
ETFs earlier in the same run (see `_get_or_fetch()` below), so it has to execute after both,
and Validación/Especulación are safe to sit in between them because neither does any eager
network fetch of its own (Validación's backtest is button-gated; Especulación only loads the
one ticker selected). `_parallel_fetch()` in `app.py` (a thin `ThreadPoolExecutor` wrapper) is
the mitigation for the tabs that DO eager-fetch: `render_list()` and `render_capital()`
prefetch all their tickers' evaluations concurrently instead of looping sequentially, cutting
wall-clock time roughly 8x for 8 tickers. Safe to call `@st.cache_data`-wrapped functions from
worker threads here specifically because they all use `show_spinner=False` and never call any
other `st.*` internally — they don't need Streamlit's per-thread `ScriptRunContext`. Verified
with `streamlit.testing.v1.AppTest` (no browser needed): 0 exceptions across all 5 tabs.

Job counts are always **dynamic**, derived from whatever's actually filtered/held at that
moment — `len(selected)` in Acciones (the multiselect, not `len(TICKERS)`), `len(held_tickers)`
in Portafolio (`purchases["ticker"].unique()`, not `len(PORTFOLIO_TICKERS)`). `TICKERS` /
`PORTFOLIO_CDI_TICKERS` in `config.py` remain the single source of truth for the *universe* of
tickers; nothing else hardcodes a count. `MAX_PARALLEL_WORKERS` in `app.py` is just a thread
safety cap, unrelated to any ticker count — don't read meaning into its value.

`_get_or_fetch()` builds on `_parallel_fetch()` with cross-tab dedup via
`st.session_state[STOCK_EVAL_CACHE_KEY]` / `[ETF_EVAL_CACHE_KEY]`: since Acciones and ETFs
always execute before Portafolio in the same run (see above), if a CDI's underlying ticker was
already evaluated there this run, Portafolio's "📎 Contexto de valoración" section reuses that
result instead of building a new fetch job for it. Only successful results are cached this way
(errors aren't remembered, so a failed fetch gets retried next time it's needed). Note this is
*belt-and-suspenders* on top of `st.cache_data`'s own memoization (which would already return
the cached value on a second call within the TTL) — the session_state layer mainly makes the
"don't re-request what another tab already has" intent explicit in the code, and skips
resubmitting a redundant future to the thread pool.

**Ticker filter persistence (`src/preferences.py`)**: the Acciones multiselect's filter
(`selected`) survives an app restart, stored in `app_data/preferences.json` — same "real user
choice, not reconstructible from an API" reasoning as `portfolio_data/` (see above), so it's
gitignored but deliberately **not** inside `.cache/`. `load_selected_tickers(TICKERS)` falls
back to the full `TICKERS` list whenever there's no saved file yet, or every saved ticker has
since been removed from `TICKERS` — never returns an empty selection.

The multiselect itself is seeded from that file into `st.session_state.ticker_filter` exactly
**once** per session (`if "ticker_filter" not in st.session_state:`), then driven purely by
`key="ticker_filter"` with an `on_change` callback (`_persist_ticker_filter`) that saves to
disk — **never** `default=load_selected_tickers(...)` recomputed inline. That was tried first
and caused a real bug: deselecting a ticker would sometimes silently re-add it a moment later.
Streamlit derives an auto-generated widget identity partly from `default`'s value; since
`default` was re-read from disk (which changes right after each save) on every rerun, the
widget's identity churned between runs and Streamlit occasionally lost the just-made
interaction, falling back to the (momentarily stale) `default` instead. Passing a stable
`key=` and never a changing `default=` is the general fix for this class of bug — reach for it
any time a widget's initial value should come from a persisted/external source.

**`scroll_to_top()` in `app.py`**: Streamlit's whole app runs inside an iframe, so a `<script>`
in `st.markdown` is sanitized away — this needs `st.iframe` (raw HTML string, executes JS)
reaching into `window.parent.document` to actually scroll the real page. It tries several
candidate containers (`stAppViewContainer`, `stMain`, `section.main`, `<html>`, `<body>`) since
the exact scrollable element's name has changed across Streamlit versions and calling
`.scrollTo()` on a non-matching one is a harmless no-op. `height` must be a **positive**
integer for `st.iframe` — `0` raises `StreamlitInvalidHeightError` (unlike the now-deprecated
`st.components.v1.html`, which accepted it); use `height=1`. Called from `render_detail()` /
`render_etf_detail()` only when `st.session_state._last_rendered_ticker` (or `_etf`) actually
*changes* — not on every rerun, or it would reset scroll on every interaction inside an already
-open detail page. Both "← Volver a la lista" buttons reset that tracking var to `None`, so
re-opening the *same* ticker after going back still triggers a fresh scroll.

**"🎲 Especulación" tab (`src/speculation.py`, `render_speculation()` in `app.py`)**: the one
deliberate exception to this whole project's "no timing language" rule — the user explicitly
asked for a zone where short-term technical speculation is allowed, separate from everything
else. `src/speculation.py` lives outside `src/valuation/` on purpose, to keep it visually and
structurally apart from the carefully non-speculative signal code. It has its own RSI (Wilder's
smoothed version, not a naive N-period average) and weekly/monthly/annual support levels
(lowest close in that trailing window — the simplest defensible reading of "support"); EMA/SMA
are reused from `trend.py` rather than recomputed. Deliberately **not** tied to Portfolio and
does **not** eagerly fetch every ticker like Acciones does — one `st.selectbox` picks a single
ticker and only that one's history loads. The selectbox options are `TICKERS +
list(SPECULATION_CRYPTO_TICKERS.keys())` — `SPECULATION_CRYPTO_TICKERS` (`src/config.py`) maps
a bare display symbol (`BTC`/`ETH`/`SOL`) to its yfinance spot symbol (`BTC-USD`/etc., the
`-USD` suffix yfinance requires for crypto). Crypto is speculation-only, never added to
`TICKERS` itself or to Portfolio/ETFs: it has no financial statements, so none of the 6
valuation formulas would apply — only price-history-based technicals (RSI, MACD,
support/resistance, EMA/SMA) work on it, which is exactly what this tab is. The real
`-USD` symbol is resolved (`SPECULATION_CRYPTO_TICKERS.get(ticker, ticker)`) only at the one
`_cached_historical_prices()` call — every other use of the `ticker` variable (labels, chart
legend, the sticky-price nonce) keeps the bare display symbol, so the UI never shows the
yfinance suffix.

Also has resistance levels (`compute_resistance_levels`, mirrors supports — highest close per
window instead of lowest, sharing `_sorted_dated_closes`/`_extreme_since`), MACD
(`compute_macd`, 12/26/9 — needs `_ema_series()` returning the *whole* series, unlike
`trend.py`'s `_ema()` which only returns the final value; the fast/slow EMA series are aligned
by taking the last `min(len)` elements of each since the fast one starts earlier), and
Bollinger Bands (`compute_bollinger_bands`, 20-period SMA ± 2 population std devs).

**Current-price display (`render_sticky_price()` in `app.py`)**: shared by both Especulación and
the Acciones detail page (`render_detail()`) — originally built Especulación-only, then extended
to Acciones on request; extracted into a helper the same session it was duplicated, rather than
leaving two copies to drift. The price renders twice — once normally wherever it's called
(`st.container(key=f"{key_prefix}_top_price")`) and once in a floating card
(`st.container(key=f"{key_prefix}_sticky_price")`) that starts `display: none`. `key_prefix`
("acciones", "speculation") must be unique per caller: `st.tabs()` isn't lazy (see below), so on
a rerun where a ticker's Acciones detail page AND the Especulación tab are both mounted, two
calls sharing one `key_prefix` would collide on the same CSS selector and the same
`window.__stickyPriceObservers` entry, each stealing the other's `topEl`. `position: sticky` was
tried first and didn't work in practice (it depends on every ancestor container having
non-`hidden` overflow up to the scrolling root — some Streamlit-internal div in that chain almost
certainly clips it, silently breaking sticky with no error). `position: fixed` replaced it
(anchors to the viewport directly, no ancestor dependency), with `!important` on every layout
property — Streamlit's own CSS for that container (sized for a full-width column) otherwise wins
the specificity fight and the card stretches edge-to-edge instead of staying a narrow corner
card. An `IntersectionObserver` (injected via `st.iframe`, since `st.markdown` strips `<script>`
tags) watches the top price element and toggles the floating card's `display` — visible only once
the top price scrolls out of view, hidden again when it's back in view. The injected HTML
includes a `{nonce_id}-{price}` nonce in a comment specifically so the iframe's content differs
on every rerun, forcing the browser to remount it and re-run the script against the
freshly-rendered elements (identical content might not re-execute); callers pass the bare ticker
as `nonce_id` rather than relying on `label` alone, since Acciones' label ("Precio actual") never
changes across tickers. The background colors are Streamlit's literal default light/dark theme
colors (`#ffffff` / dark-surface variants via `prefers-color-scheme`), not a theme CSS variable —
safe since this project has no `.streamlit/config.toml`; update if a custom theme is ever added.
None of this has been checked in a real browser this session (no browser access) — only that it
compiles and runs with zero exceptions under `streamlit.testing.v1.AppTest`.

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

**"📋 Plan de DCA sugerido" replaced Fibonacci**, shown only for `SPECULATION_CRYPTO_TICKERS`
(BTC/ETH/SOL) same as Fibonacci was. Same out-of-sample methodology, different (much coarser,
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
may be undefined) compared against `REGIME_VALIDATED_COMBOS` (app.py): `{("fuerte", 20),
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
`RegimeReaction` remain in `speculation.py` regardless of whether `app.py` calls them: they're
the reproducibility path for `REGIME_VALIDATED_COMBOS` (re-run them against fresh history to
re-verify or update that lookup), not dead/half-finished code; don't delete them as "unused"
without first checking whether `REGIME_VALIDATED_COMBOS` still needs re-deriving.
`classify_regime_series(closes)` is a day-by-day historical version of the same rule
`classify_trend_state()` (app.py) already applies to "today" (price ≥ EMA of `EMA_PERIOD` AND
≥ SMA of `SMA_SHORT_PERIOD` AND ≥ SMA of `SMA_LONG_PERIOD`, all three, imported from `trend.py`
so the two classifiers can't silently drift apart) — reuses `_ema_series()` (the MACD helper
that returns the full seeded EMA series, not just the final value) for the EMA leg and
`pandas.rolling().mean()` for the two SMA legs.

**RSI-within-regime refinement (`REGIME_RSI_OVERBOUGHT_VALIDATED_HORIZONS`, app.py)**: a later
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
since `_cached_historical_prices()` (app.py) is hardcoded to the `yfinance` provider regardless
of which provider is active elsewhere in the app — pre-existing behavior for this whole tab,
unrelated to this change. `compute_adx()` returns `None` (rendered as "no hay suficiente
historial") if either list is short or a stale pre-this-change cache entry is missing
`"high"`/`"low"` — self-heals on the next successful live fetch, no migration needed.

**"📊 Validación" tab (`render_validation()` in `app.py`)**: not a price signal like the other 4
tabs — it's a check on how well the *existing* signals have performed, added after the user
asked "what else could we add" and picked this + a rejected support/resistance idea (see above)
out of a shortlist. Two independent sections, neither of which runs unprompted, so this tab adds
no latency to any other tab's rerun even though `st.tabs()` isn't lazy (see above):

- **Backtest section** surfaces `src/backtest.py`'s `backtest_ticker()` (previously
  console-only, via `python -c "from src.backtest import run_backtest; ..."`) behind a
  `st.button("Correr backtest")` — same pattern as `render_detail()`'s "🔍 ¿FMP y yfinance
  opinan lo mismo de esta acción?" button, deliberately not auto-run, since it's ~6 network
  calls × 8 tickers. Called per-ticker through `_cached_backtest_ticker()` (new,
  `@st.cache_data(ttl=86400)` — a day-long TTL because financial statements don't move
  intraday, unlike `_cached_evaluation`'s 900s) submitted to the existing `_parallel_fetch()`
  (not `_get_or_fetch` — no other tab needs to reuse a backtest result, so the
  session-cross-tab dedup layer would be pure overhead here). `BACKTEST_YEARS_AGO` is fixed at
  1, not exposed as a UI control — `backtest.py`'s own docstring documents that `years_ago=2`
  fails for 0/8 tickers (not enough EPS history in yfinance), so a free-form control would
  silently invite a value that can't work. The "¿Acertó?" column is a simple directional check
  (`verdict_then` cheap + positive `actual_return`, or expensive + negative, = ✅; `mixed` = "—"
  since there's no directional claim to grade; anything else = ❌) — the 3 caveats already
  written in `backtest.py`'s module docstring (small survivorship-biased sample, today's beta
  not the historical one, yfinance's EPS-history gaps) are reproduced in an `st.info` under the
  table, matching this project's habit of surfacing its own limitations in the UI, not just in
  code comments.
- **Verdict-history section** is genuinely new state, not a recomputation: `src/verdict_history.py`
  (same `app_data/` pattern as `src/preferences.py` — not reconstructible from an API, so it's
  gitignored but lives outside `.cache/`) persists one `{date, verdict, headline, cheap, fair,
  expensive, price}` entry per ticker per calendar day to `app_data/verdict_history.json`.
  `record_verdict()` dedupes on `entries[-1]["date"] == today` so it's safe to call more than
  once; `_maybe_record_verdict()` in `app.py` additionally gates on a
  `st.session_state["_verdict_recorded_today"]` set so a session with many reruns (every widget
  interaction reruns the script) doesn't re-open and re-write the JSON file each time — the
  session-state check is purely a perf optimization, the on-disk dedupe is what makes it
  *correct* (two separate sessions the same day still collapse to one entry). Called right
  after `summarize_signals(evaluation)` in both `render_list()` and `render_detail()` — the only
  two places a `TICKERS` summary already gets computed — so history only accumulates for
  tickers the user actually viewed that day, same "job counts are dynamic" philosophy as the
  rest of the app (no forced background evaluation of all 8 tickers just to backfill history).
  ETFs are excluded from the selector (`evaluate_etf()` has no cheap/expensive verdict to log,
  only risk/return metrics). Since there's no way to reconstruct a past day's verdict without
  that day's financial statements (that's what the backtest section does, with its own real
  limits, for `years_ago=1` only), history starts accumulating from whenever this shipped —
  the UI explicitly says so (and skips the chart) below 2 recorded points rather than showing a
  1-dot plot. The chart reuses `VERDICT_COLOR` (defined for `triangulation_badge()`) and a new
  `VERDICT_LABEL` (`{"cheap": "Barata", "expensive": "Cara", "mixed": "Mixta"}`) — deliberately
  not a new categorical palette, since cheap/mixed/expensive is the same 3-way status this app
  already colors consistently everywhere else. A plain table of the same entries sits below the
  chart (dataviz skill's "a table view exists" companion for any chart carrying meaning in
  color).

## Known data caveats (already handled deliberately — don't "fix" without re-reading the comment)

- `src/config.py` excludes CSPXCO (an ETF, no financial statements) and NU (blocked on FMP's
  free plan) from `TICKERS`, with reasons inline.
- Net debt is derived from gross debt minus cash when a provider doesn't report it directly
  (yfinance omits it for net-cash companies like AMZN/NVDA/TSLA) instead of silently assuming
  zero.
- The DCF's stock-based-compensation-adjusted FCF (shown in the "detalle técnico" expander) is
  a stricter secondary disclosure — it intentionally does NOT feed back into the main DCF fair
  value.
- `src/backtest.py` has real methodological limits documented in its module docstring (only
  `years_ago=1` reliably has enough historical P/E data — yfinance's annual statements cap at
  4-5 columns and the oldest one is frequently `NaN`; even at 1 year, 2 of the 8 tickers can
  still fail for their own data reasons, uses today's beta not the historical one, small
  survivorship-biased sample) — read them before treating its output as validation rather than
  a directional sanity check.
