# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A Streamlit dashboard ("Precio Justo — Acciones Americanas") that evaluates whether a fixed
list of US mega-cap stocks (`TICKERS` in `src/config.py`: AAPL, MSFT, AMZN, META, NVDA, TSLA,
UBER, GOOGL) look cheap, fair, or expensive right now, using 6 independent valuation formulas
grouped into 3 families. All user-facing text is in Spanish (Rioplatense).

## Skills — invoke proactively, don't wait to be asked

This repo ships `.claude/skills/financial-advisor-*` skills. They're always listed as available, but
loading one only happens when it's explicitly invoked — so check this table and call the
matching skill via the Skill tool *before* starting work, whenever the request is scoped to one
of these areas:

| Task is scoped to...                                         | Skill                              |
|----------------------------------------------------------------|-------------------------------------|
| "📈 Acciones" tab (valuation cards, ticker list/filter, detail) | `financial-advisor-stocks`                  |
| "🧺 ETFs" tab                                                   | `financial-advisor-etfs`                    |
| "🎲 Especulación" tab (stocks: RSI, S/R, MACD, Bollinger, ADX, OBV) | `financial-advisor-speculation`         |
| "🪙 Cripto" tab (BTC/ETH/SOL: same indicators + multi-method S/R engine) | `financial-advisor-cripto`         |
| "📊 Validación" tab (backtest, verdict history)                 | `financial-advisor-validation`              |
| "💰 Portafolio" tab (COP purchases/sales, holdings, realized gains, contexto) | `financial-advisor-portfolio` |
| Adding a new ticker or a new/modified valuation formula         | `financial-advisor-add-ticker-or-formula`   |
| Launching/checking/stopping the Streamlit app to see a change   | `financial-advisor-run-app`                 |

For requests spanning multiple tabs, invoke each relevant skill. This is in addition to the
general-purpose `dataviz` skill (any chart/plot work) — that one already triggers on its own
description and isn't specific to this repo.

## Keeping `README.md` in sync

`README.md` keeps a one-line-per-file map of every `.py` module with real logic (not an empty
`__init__.py`) and every skill in `.claude/skills/`. **Any new `.py` file with real logic, or any
new skill, gets a row added to the matching table in `README.md` in the same change that creates
it** — one line of role/responsibility, not a full description (the detail belongs in that
file's own docstring or in the skill's `references/design-history.md`, not duplicated here).

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

## Deploying (Streamlit Community Cloud)

Free, official host for a Streamlit app — connects directly to this repo's GitHub remote, no
server to manage. Steps: sign in at [share.streamlit.io](https://share.streamlit.io) with the
GitHub account that owns this repo → "New app" → pick this repo, branch `main`, main file
`app.py` → "Deploy".

**All 6 tabs, including "💰 Portafolio", are public on this deploy — explicit user choice
(2026-08-08), made in two steps the same day.** First, a `SHOW_PORTFOLIO_TAB` secret/env-var
flag that dropped Portfolio from `st.tabs()` on a public deploy was added, then removed a few
messages later at the user's explicit request ("subamos el portafolio, completo") — don't re-add
that flag without being asked again. Then, since a public deploy pulls this git repo fresh with
none of the user's actual purchase/sale history (that data lived only in the gitignored
`portfolio_data/` on their local machine), the user asked for the real data to appear too — see
"Portfolio tracking" below: `portfolio_data/*.json` is now committed to git, not gitignored,
also an explicit, informed choice (the amounts/dates are in git history permanently, publicly).
Consequence worth knowing before touching this further: with no authentication anywhere in this
app, anyone with the deploy's URL can see the real portfolio data AND submit the purchase/sale
forms (they write to the live instance's `portfolio_data/`, same as the owner using the UI) —
this was flagged to the user before each of these two changes, who chose to proceed anyway both
times. If auth is ever wanted, that's a separate, not-yet-built feature — don't assume it exists.

`FMP_API_KEY` isn't needed for a public deploy — the UI defaults to the `yfinance` provider,
which requires no key. **A real bug hit deploying this for the first time (2026-08-08)**:
`src/config.py` used to read it as `os.environ["FMP_API_KEY"]` (bracket access, raises
`KeyError` if unset) instead of `.get()` — crashed the whole app at import time on Streamlit
Cloud, where there's no `.env` and the key was never set as a secret, even though nothing in
the app actually needs it unless the user explicitly switches to the `fmp` provider. Fixed to
`os.environ.get("FMP_API_KEY")` (`None` if absent); `fmp_client.py` only ever uses it as a
request query param, so `None` there just means an FMP call would fail with an auth error if
someone actually selected that provider — not an app-breaking crash on startup regardless of
provider choice, which is what the docstring above already promised and the old code didn't
actually deliver. `.cache/` (API responses) and `app_data/` (ticker-filter/verdict-history)
are also gitignored and start empty on a fresh deploy — both self-heal on first use (cache
refills, preferences/history start accumulating from scratch); Portfolio's data doesn't self-
heal the same way (nothing to reconstruct it from), which is why it's committed to git instead
(see "Portfolio tracking" below) rather than just left to start empty like these two.

**"🪙 Cripto" does not work on this deploy — known limitation, not a bug, confirmed
2026-08-08.** Binance (`src/data/binance_client.py`, this tab's only data source) returns HTTP
451 to every request from Streamlit Community Cloud's IP range: `"Service unavailable from a
restricted location according to 'b. Eligibility' in https://www.binance.com/en/terms"` —
Binance blocks broad swaths of cloud-datacenter IPs outright, not just by country, so this isn't
specific to Streamlit Cloud and would likely reproduce on most free hosts. `src/ui/cripto.py`
already surfaces the real `DataError` detail (not just a generic "no pudimos consultar") in an
`st.caption` under the error, specifically so this is self-diagnosable from the deployed UI
without digging through host logs. Discussed with the user, who chose to leave this as-is for
now rather than migrate the tab's data source (yfinance has `BTC-USD`/`ETH-USD`/`SOL-USD` with
no geo-block, but no native 4h/1h klines — would require reworking the Market Reaction Zone
Engine's reference-series assumptions and re-validating every Binance-derived OOS result, e.g.
BTC's RSI-overbought regime refinement and support validation, against the new series; not a
small change) or try Binance.US (separate entity, unconfirmed whether it's blocked too, shorter
history). Revisit only if explicitly asked — don't silently start a data-source migration on
the assumption that this limitation needs fixing.

## Architecture

**`app.py` is a thin entry point.** It used to be one 2821-line file holding all 6 tabs; it's
now split into `src/ui/*.py` (one module per tab — `stocks.py`, `etfs.py`, `speculation.py`,
`cripto.py`, `validation.py`, `portfolio.py` — plus `shared.py` for the cross-tab plumbing:
caching helpers, `_parallel_fetch()`/`_get_or_fetch()`, `render_sticky_price()`,
`scroll_to_top()`, badge/formatting helpers). `app.py` itself now only does page config,
`session_state` init, and the `st.tabs()` wiring.

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
`.cache/`. `portfolio_data/` used to also be gitignored (real data, not reconstructible from an
API — same category as `app_data/` still is) but is now **committed to git** as of 2026-08-08,
explicit user request so the public Streamlit Cloud deploy shows real purchase/sale data instead
of starting empty (see "Deploying" above) — the user was told this means the actual COP amounts
and dates are in git history permanently, publicly, and chose to proceed anyway. Concretely:
`purchases.json`/`sales.json` need a manual `git add`/commit/push after every local edit (via
the UI forms or `scripts/add_sale.py`) for the public deploy to reflect it — there's no
auto-sync. Full design history — the COP
commission model, the drawdown-bucket accumulation zone (`DRAWDOWN_VALIDATED_BUCKETS`), and the
diversification/aggregate-risk-return/goal-projection sections — moved to
`.claude/skills/financial-advisor-portfolio/SKILL.md`'s "Design history" section. Invoke that skill
before touching Portfolio code; it's not repeated here because it's only relevant when work is
actually scoped to this tab, unlike this file which loads on every conversation.

**Zone thresholds**: `ZONE_THRESHOLDS` in `fair_value.py` classify each margin
`(fair_value - price) / price` into "Acumulación fuerte" / "Acumulación" / "Precio justo" /
"Sobrevalorado", calibrated to the classic Graham/Buffett margin-of-safety bands.

**DCF range table and family fair-value evolution chart (`src/ui/stocks.py`)**: two additions to
the detail page, both surfacing numbers that already existed rather than computing anything new.
`render_dcf_range_table()` replaces the DCF method card's old plain-text "Rango: $X — $Y"
caption with a small table (Pesimista/Base/Optimista, each with $ value and % vs. today's price)
— first built as a compact Plotly range-bar chart, replaced with this table same-session on user
feedback that the mini-chart didn't read any clearer than text in that little a space; a table
did. `render_family_fair_value_chart()` is new state consumption, not a new
computation: it plots price vs. each of the 3 `SIGNAL_FAMILIES`' fair value in dollars
(`price_that_day × (1 + family_margin_that_day)`) across `verdict_history.json`'s daily entries
(see "Verdict-history section" above for the `family_margins` field this reads) — same "starts
accumulating from whenever this shipped, skip the chart below 2 points" honesty pattern as
Validación's own verdict-history chart, and a `st.dataframe` table view underneath for the same
reason that chart has one. Unlike `VERDICT_COLOR`/`ZONE_COLOR`, the 3 families genuinely need
categorical (identity) color — they're 3 co-equal series the reader must tell apart, not a
status — so `FAMILY_COLOR`/`FAMILY_SHORT_LABEL` in `stocks.py` assign fixed slots by
`zip()`-ing `SIGNAL_FAMILIES`' iteration order against 3 hex values (not a literal dict keyed by
the family name strings), so a future rename of a family's display text in `fair_value.py`
can't silently desync the color/label from the family it's supposed to describe.

**`st.tabs()` is not lazy**: all 6 tabs' bodies execute every rerun, in fixed script order —
`tab_acciones` → `tab_validacion` → `tab_etfs` → `tab_especulacion` → `tab_cripto` → `tab_capital` (Acciones,
Validación, ETFs, Especulación, Cripto, then Portafolio always last, by user request) — regardless of
which tab is visually active; this is a Streamlit characteristic, not a bug here. The tab
*labels'* order in the `st.tabs([...])` call controls the visual left-to-right order, and the
`with tab_X:` blocks further down are written in that same order on purpose — the two are
independent in Streamlit (code order drives execution order regardless of label order), but
keeping them in sync avoids the dependency between them silently drifting apart. A slow tab
earlier in that order delays every tab after it, including its spinner (nothing renders for a
later tab until its `with tab_X:` block is actually reached) — Portafolio stays last by user
request (see above); Validación/Especulación/Cripto are safe to sit in between Acciones/ETFs and
Portafolio because none of them does any eager network fetch of its own (Validación's backtest
is button-gated; Especulación and Cripto only load the one ticker selected, and Cripto's S/R
engine is additionally button-gated on top of that). `_parallel_fetch()` in `src/ui/shared.py` (a thin `ThreadPoolExecutor` wrapper) is
the mitigation for the tabs that DO eager-fetch: `render_list()` and `render_capital()`
prefetch all their tickers' evaluations concurrently instead of looping sequentially, cutting
wall-clock time roughly 8x for 8 tickers. Safe to call `@st.cache_data`-wrapped functions from
worker threads here specifically because they all use `show_spinner=False` and never call any
other `st.*` internally — they don't need Streamlit's per-thread `ScriptRunContext`. Verified
with `streamlit.testing.v1.AppTest` (no browser needed): 0 exceptions across all 6 tabs.

Job counts are always **dynamic**, derived from whatever's actually filtered/held at that
moment — `len(selected)` in Acciones (the multiselect, not `len(TICKERS)`), `len(held_tickers)`
in Portafolio (`purchases["ticker"].unique()`, not `len(PORTFOLIO_TICKERS)`). `TICKERS` /
`PORTFOLIO_CDI_TICKERS` in `config.py` remain the single source of truth for the *universe* of
tickers; nothing else hardcodes a count. `MAX_PARALLEL_WORKERS` in `src/ui/shared.py` is just a thread
safety cap, unrelated to any ticker count — don't read meaning into its value.

`_get_or_fetch()` builds on `_parallel_fetch()` with in-session dedup via
`st.session_state[STOCK_EVAL_CACHE_KEY]` / `[ETF_EVAL_CACHE_KEY]`, populated by Acciones/ETFs
(`render_list()`/ETFs' own list render) so a rerun within the same session doesn't re-request a
ticker it already fetched. Only successful results are cached this way (errors aren't
remembered, so a failed fetch gets retried next time it's needed). This is *belt-and-suspenders*
on top of `st.cache_data`'s own memoization (which would already return the cached value on a
second call within the TTL) — the session_state layer mainly makes the "don't re-request what
this tab already has" intent explicit in the code, and skips resubmitting a redundant future to
the thread pool. Portafolio used to be a cross-tab consumer of these same keys (its old "📎
Contexto de valoración" section, reusing whatever Acciones/ETFs had already evaluated that run)
— removed by user request (2026-08-06) in favor of an independent "🪜 Plan de compra escalonada"
section that only needs each holding's raw price history, not a full valuation evaluation (see
the Portfolio skill's design history).

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

**`scroll_to_top()` in `src/ui/shared.py`**: Streamlit's whole app runs inside an iframe, so a `<script>`
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

**"🎲 Especulación" tab (`src/speculation.py`, `render_speculation()` in `src/ui/speculation.py`)**: stocks-only (`TICKERS`) — BTC/ETH/SOL moved to their own "🪙 Cripto" tab (see below). The one deliberate exception to this whole project's "no timing language" rule — the user explicitly asked for a zone where short-term technical speculation is allowed, separate from everything else. `src/speculation.py` lives outside `src/valuation/` on purpose, to keep it visually and structurally apart from the carefully non-speculative signal code. Full detail on RSI/support-resistance/MACD/Bollinger/ADX/OBV and the shared `render_speculation_indicators()` function (also used by Cripto) lives in `.claude/skills/financial-advisor-speculation/SKILL.md` and `.claude/skills/financial-advisor-cripto/SKILL.md` — not repeated here.

**Current-price display (`render_sticky_price()` in `src/ui/shared.py`)**: shared by Acciones's detail
page (`render_detail()`), Especulación, and Cripto (`key_prefix` "acciones"/"speculation"/"niveles"
respectively) — originally built Especulación-only, then extended to Acciones on request;
extracted into a helper the same session it was duplicated, rather than leaving two copies to
drift. The price renders twice — once normally wherever it's called
(`st.container(key=f"{key_prefix}_top_price")`) and once in a floating card
(`st.container(key=f"{key_prefix}_sticky_price")`) that starts `display: none`. `key_prefix`
("acciones", "speculation", "niveles") must be unique per caller: `st.tabs()` isn't lazy (see
below), so on a rerun where a ticker's Acciones detail page, Especulación, AND Cripto are all
mounted, two calls sharing one `key_prefix` would collide on the same CSS selector and the same
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

**Especulación tab (`src/speculation.py`, `render_speculation()` in `src/ui/speculation.py`)** — **stocks only**
(`TICKERS`); BTC/ETH/SOL moved out to their own "🪙 Cripto" tab (see below). Design history — the
simple support/resistance chart, the rejected "more history/multi-touch levels" investigation,
the Fibonacci saga, the "📋 Plan de DCA sugerido" box, and the RSI/ADX/OBV regime-refinement
investigations (only RSI validated, for BTC only) — moved to
`.claude/skills/financial-advisor-speculation/SKILL.md`'s "Design history" section. Invoke that skill
before touching any of this; it's not repeated here because it's only relevant when work is
actually scoped to this tab, unlike this file which loads on every conversation.

**"🪙 Cripto" tab (`src/support_resistance.py`, `src/data/binance_client.py`, `render_crypto()`
in `src/ui/cripto.py`)**: BTC/ETH/SOL only, sourced from Binance (not yfinance — more history, native 4h
klines). Has two parts: the full speculation indicator stack shared with Especulación
(`render_speculation_indicators()`, same RSI/MACD/Bollinger/ADX/OBV/DCA-box code, just fed
Binance data and `is_crypto=True`), and a 20-methodology support/resistance engine (DBSCAN, KDE,
RANSAC/Theil-Sen/Huber, Hough transform, Volume Profile, VWAP, candle/volume confirmation,
multi-timeframe via daily/weekly/monthly/4h, touch-point optimization, channels) producing a
0-100 confidence score per level. The engine's confidence score validated as an actionable signal
for BTC (support) once, under an earlier version of the score — a later statistical-consistency
adjustment (Wilson-bound shrinkage) broke that result on re-test, so `SR_VALIDATED_TICKERS` is
currently `{}` (nothing validated) — see the skill for the full round-1/round-2 history and why
it isn't loosened just to make something pass again. This tab absorbed both the former
"🧭 Niveles" tab (which used to also cover stocks) and Especulación's crypto handling in the same
session — full design history, the real bugs found and fixed while building it, and the exact
validation results live in `.claude/skills/financial-advisor-cripto/SKILL.md`; invoke that skill before
touching this tab.

**Fear & Greed Index (`src/data/fear_greed_client.py`, `render_fear_greed_index()` in
`src/ui/cripto.py`)**: one external, well-known market-sentiment gauge (alternative.me, no API
key) — genuinely different from every other `src/data/*.py` client in that it takes no symbol at
all, since there's only ONE value for the whole crypto market, not one per BTC/ETH/SOL. Rendered
as **static** content, above the ticker selectbox and its own `st.divider()`, specifically so it
reads as independent of whatever ticker is chosen below (verified via `AppTest`: switching the
selectbox leaves this section's caption byte-for-byte identical). Same standing as ADX/OBV —
shown descriptively because it's a standard, widely-cited index, not because this project
validated it; the caption says so explicitly. The gauge's red→green band coloring is a
deliberate exception to this project's usual palette choices: it's the universally recognized
convention for this specific index everywhere it's displayed, so matching that (rather than a
generic diverging pair) is the more accessible/recognizable choice here. Classification label
("Miedo"/"Codicia"/etc.) always renders as visible text via `fear_greed_badge()` — same
`zone_badge()`/`quality_badge()` HTML pattern used elsewhere, never color-alone. Cached at
`ttl=3600` (the index only updates once a day upstream, unlike price at 900s).

`FEAR_GREED_BANDS`/`FEAR_GREED_LABEL_ES`/`_cached_fear_greed_index()`/`fear_greed_badge()` live
in `src/ui/shared.py`, not `cripto.py` — moved there once the "📋 Plan de DCA sugerido" box in
`src/ui/speculation.py` (the `is_crypto` branch of `render_speculation_indicators()`) became a
second real caller, same threshold this project always uses before extracting to `shared.py`.
That box now cross-references today's Fear & Greed reading against whichever regime message it
already shows (`st.caption`, unconditional — runs regardless of which of the 3 regime branches
fired), disclosing a real OOS finding tested the same session: Fear & Greed DOES correlate with
BTC/ETH forward returns at moderate thresholds (≤45/≥55), but in the momentum direction (fear →
worse, greed → better), not the classic contrarian reversal story, and it does NOT add
information beyond the already-validated regime signal (`REGIME_VALIDATED_COMBOS`) — same
underlying momentum, tested and confirmed redundant via the identical within-regime-refinement
methodology already used for the RSI-overbought check. See
`financial-advisor-cripto`'s design-history for the full numbers. This caption is disclosure,
not a new decision input — nothing about when the box shows `st.success`/`st.info` changed.

**Wyckoff Spring (`render_wyckoff_spring()` in `src/ui/cripto.py`, own section, appended at the
end of `render_crypto()`)**: rejected for the 8 stock `TICKERS` in
`financial-advisor-speculation`'s design-history, then re-tested for BTC/ETH/SOL the same
session as Fear & Greed above and validated cleanly for BTC/ETH (all 3 swept lookbacks
10/20/30, all 4 horizons, no threshold-fragility) — but with the sign OPPOSITE to what Wyckoff
theory claims a spring predicts (underperformance vs. the ticker's own average, not a bounce).
SOL didn't validate. Deliberately NOT inside the shared `render_speculation_indicators()` (same
reasoning as Golden Cross: never tested for stocks, must never silently appear in Especulación —
verified via `AppTest` that exactly one such section exists app-wide). See
`financial-advisor-cripto`'s design-history for the full numbers and the BTC-vs-ETH nuance
(ETH's spring-day return is negative in absolute terms both train and test; BTC's is only
below-average, not reliably negative outright).

**VWAP (`rolling_vwap_series()` in `src/speculation.py`, `render_vwap()` in `src/ui/cripto.py`,
own section between the shared indicator stack and Wyckoff Spring)**: VWAP already existed in
this repo but was inert and invisible — `_rolling_vwap()` was computed only inside the Zone
Engine as a BOOLEAN `vwap_confluence` component ("does any VWAP pass within 0.5 ATR of this
level?"), that component has weighed 0 since the score redesign, and `component_scores` is never
rendered anywhere, so toggling "Confluencia con VWAP" in the methods multiselect changed nothing
observable (`SRConfig.vwap_confluence_bonus` is likewise defined and never read — still dead,
left alone deliberately). `rolling_vwap_series()` is now the single implementation (windows
7/30/365 **calendar** days over the daily Binance series, typical price (H+L+C)/3, sliding-window
sums); `_rolling_vwap()` is a thin "last element" wrapper over it, verified numerically identical
to the old scalar code across 48 shape/window combinations (daily and 4h-style timestamps, `None`
volumes, n=1..900) so the engine's behavior didn't move. The UI section is **descriptive only**,
same standing as ADX/OBV/Fear & Greed and disclosed as such in its own caption: price vs. VWAP at
3 horizons, a 3-way reading (above all / below all / mixed, same shape as `classify_trend_state()`),
and a chart+table. **No OOS validation has been run for VWAP** — the user chose the display-only
scope first (2026-08-16) and the study (distance-to-VWAP normalized by ATR → forward returns,
60/40 split, 4 horizons, via `scripts/oos_validate.py`) is the agreed next step, to be run
locally since Binance is unreachable from the remote sessions. Don't promote this to an actionable
message, and don't give `vwap_confluence` a weight, until that study actually passes. Windows
whose span the history doesn't cover are dropped rather than shown (a "VWAP de 1 año" computed
over 3 days is a mislabeled number, not a value).

**"📊 Validación" tab (`render_validation()` in `src/ui/validation.py`)**: not a price signal like the other 4
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
  expensive, price, family_margins}` entry per ticker per calendar day to
  `app_data/verdict_history.json`. `family_margins` (`{family: median_margin}`, added alongside
  Acciones' own fair-value-evolution chart below — not used by this Validación section) is
  `summarize_signals()`'s per-family median margin, the same number that already decided each
  family's zone — entries recorded before this field existed simply lack the key, which the
  chart below treats as "no data that day" rather than 0.
  `record_verdict()` dedupes on `entries[-1]["date"] == today` so it's safe to call more than
  once; `_maybe_record_verdict()` in `src/ui/stocks.py` additionally gates on a
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

**Telegram alerting was built here, then extracted (2026-08-09) to its own repo,
`market-signals-telegram`, once the user wanted the same mechanism to cover other, non-
financial-advisor products.** This app has no Telegram code anymore — `evaluate_ticker()`/
`summarize_signals()`/`evaluate_drawdown_zone()`/`classify_golden_cross_series()`/
`classify_regime_series()`/`detect_levels()` etc. all stayed here (real dashboard code, used by
Portfolio/Especulación/Cripto's own UI, not telegram-specific), but the alerting orchestration
(the Telegram client, the signal-state dedupe file, the per-strategy `SignalDefinition`
wrappers, the scheduled GitHub Actions workflow) now lives entirely in that sibling repo, which
imports this project's evaluators directly from a checkout rather than duplicating any of this
logic. The gold-trading-bot market-structure (BOS/CHoCH) investigation that happened while
scoping the original alert feature is unrelated to this move and still lives in
`financial-advisor-speculation`'s design-history — it was OOS-tested and rejected on its own
merits, nothing about that result changed by moving the alerting code elsewhere.

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
