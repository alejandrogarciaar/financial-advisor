---
name: us-stocks-portfolio
description: Use when the user's request is scoped to the "💰 Portafolio" tab of the USStocks (Precio Justo) dashboard — recording COP purchases or sales (both append-only), the held-position summary, realized gains, or the "Contexto de valoración" cross-reference section. Points to the files that make up this tab so work stays scoped to them.
---

# Portafolio tab — context map

This skill doesn't prescribe steps — it just points to what "the Portafolio tab" is made of, so
work requested for this tab doesn't drift into Acciones/ETFs/Especulación/Cripto code or
require re-discovering the file layout from scratch. `references/design-history.md` has the
full design rationale for everything listed here — read it before changing purchase/sale
semantics, this is the one tab with real user-entered financial data (whole shares only).

**Non-negotiable rule (explicit user instruction, 2026-08-03): sales history is APPEND-ONLY.**
No one — not the user through the UI, not Claude editing `sales.json` directly — may edit or
delete a row in `portfolio_data/sales.json` once saved. The only sanctioned way to change it is
adding a new row (the "➕ Registrar una venta nueva" form in `render_capital()`, or
`scripts/add_sale.py`). If a past sale needs correcting, add a new row that reflects the
correction — never touch the old one. This is stronger than the general
[[feedback-portfolio-data-never-autonomous-delete]] rule ("ask before deleting") — for sales
specifically, deletion isn't just gated on asking, it's off the table entirely, by design, with
no UI path to do it at all (see `render_capital()` below).

**Purchases now use the same UX, by explicit request (not the same policy).** Right after the
sales rule shipped, the user asked to apply "la misma estrategia... exclusivamente la dinámica,
al UX" to "Tus compras" — same read-only-history + add-only-form pattern, no `data_editor`. The
practical effect is identical (no delete/edit path exists in the UI for either table right now),
but the user scoped the request to UX consistency, not to restating the sales golden rule for
purchases — don't treat "purchases are append-only" as a business rule with the same weight as
the sales one unless the user says so explicitly. If a future change needs purchase editing back
(e.g. fixing a typo'd row), that's a UX decision to revisit, not a rule being broken.

That narrative used to live in `CLAUDE.md` directly, then in this file's own "Design history"
section, then out to `references/design-history.md` (loaded on demand instead of on every
invocation of this skill — see the `token-audit` skill).

## `src/ui/portfolio.py`

App code used to be one 2821-line `app.py`; it's split into `src/ui/*.py` (one file per tab,
plus `shared.py` for cross-tab plumbing) with `app.py` now just the thin entry point (page
config + tab wiring). This tab's code lives in `src/ui/portfolio.py`:

- `render_capital()` — page order (top to bottom), final form after 3 rounds of user-requested
  reordering: 1) `_render_cartera_and_total()` — "Mi Cartera" (held-position table, net of sales;
  renamed from "Resumen por acción" through "Portafolio" before landing on "Mi Cartera", to avoid
  duplicating the tab's own title) + `render_portfolio_total_hero()`; 2) "Tus compras" (add-only
  form); 3) "Tus ventas" (read-only history + add-only form); 4) `_render_portfolio_analysis()` —
  "💸 Costo de comisiones", "💵 Ganancias realizadas", "📎 Contexto de valoración",
  diversification/risk-return/goal-projection; 5) "🧮 Simulador de precio promedio". Two SEPARATE
  `@st.fragment`s (not one) is what makes "Tus compras"/"Tus ventas" sit between "Total" and
  "Comisiones" — a single fragment function can't be interrupted mid-render to let other content
  from `render_capital()` appear inside it, so the original one-fragment design (see design
  history) had to split in two, each independently `_compute_held_summary()`-ing its own
  `held_tickers`/`summary` rather than sharing one from the other (fragments don't share local
  variables) — cheap to redo since the underlying price lookups are already `@st.cache_data`
  (900s TTL), so this doesn't double any network calls, just some DataFrame work.
  `purchases`/`sales` are loaded ONCE at the top of `render_capital()` (`load_purchases()`/
  `load_sales()`) and reused everywhere below — no more separate `saved_*`/edited-style variable
  pair, since neither table has an editor to reconcile against anymore (see below). Purchase entry
  AND sale entry are both an `st.form` (`add_purchase_form` / `add_sale_form`) that only ever
  appends — no `data_editor`, no delete/edit path in the UI for either table. Sale entry also
  shows a read-only `st.dataframe` history above its form; purchase entry does not (removed by
  request — see design history). The rest of the page: "📎 Contexto de valoración" section reuses
  `STOCK_EVAL_CACHE_KEY` / `ETF_EVAL_CACHE_KEY` (populated by Acciones/ETFs earlier in the same
  run, both in
  `src/ui/shared.py` — see `_get_or_fetch()`) instead of re-fetching. That same section also
  shows each holding's drawdown-from-1y-high line (`DRAWDOWN_VALIDATED_BUCKETS`, defined right
  above `render_capital()`) — the static gate of which (ticker, drawdown-bucket) pairs are
  validated; don't add a ticker/bucket to it without re-running the same out-of-sample check
  documented below — and captures `underlying_prices: dict[str, list[dict]]` while it loops, so
  the 3 sections right after it don't need to re-fetch anything.
- There is no `_render_movements_editor()`/`data_editor`-with-delete-confirmation anymore for
  either table — it existed briefly (purchases-only, after sales moved off it) and was removed
  once purchases moved to the same form pattern, since it had no remaining caller. Both add-forms
  are structurally identical: build a 1-row DataFrame from the form inputs, `pd.concat` onto the
  loaded DataFrame, run `validate_purchases()`/`validate_sales()` on the **candidate** (not just
  the new row — `validate_sales()` needs the full picture to check total-sold-vs-purchased), show
  `st.error()` per message and skip the save if any fail, otherwise `save_purchases()`/
  `save_sales()` and `st.rerun()`.
- "Mi Cartera" (renamed from "Resumen por acción")/"Total" (unrealized) only include tickers with net shares held
  (purchased − sold) > 0 — a fully-sold ticker drops out of these and shows up in "Ganancias
  realizadas" instead. "📎 Contexto de valoración" and the 3 sections after it (see below) are
  gated on `held_tickers` being non-empty for the same reason.
- Right after "Contexto de valoración", 3 more sections gated on `held_tickers` non-empty:
  "🥧 Diversificación" (bar chart, `PORTFOLIO_CDI_SECTOR`), "📈 Retorno y riesgo del portafolio"
  (`build_synthetic_portfolio_series()` + the existing `evaluate_risk_return()`), "🎯 Proyección
  de meta" (`project_future_value()`, depends on the risk/return section's output). None of these
  three need OOS validation — see "Design history" below for why (descriptive composition +
  deterministic financial math, not a price prediction) — but the goal projection carries a
  strong disclaimer anyway since a dollar figure reads as a forecast.

## `src/ui/shared.py`

- `_cached_portfolio_price()` — current COP price for a held CDI, straight from yfinance
  (`PORTFOLIO_CDI_TICKERS[ticker]`), no FX conversion. Also used by ETFs (BVC price reference),
  so it lives here, not in `portfolio.py`.

## `app.py`

- Just the tab wiring now (`st.tabs()` call + one `with tab_capital:` block importing
  `render_capital` from `src/ui/portfolio.py`) — and it stays **last** in that wiring specifically
  so `render_capital()` can reuse `STOCK_EVAL_CACHE_KEY`/`ETF_EVAL_CACHE_KEY` populated by
  Acciones/ETFs earlier in the same script run.

## `src/portfolio.py`

- `load_purchases()` / `save_purchases()` — persisted to `portfolio_data/purchases.json`.
  `load_sales()` / `save_sales()` — persisted to `portfolio_data/sales.json`, same shape
  (ticker/shares/price_cop/commission_cop/date), separate file. Both gitignored, real user
  data — never delete without asking, see memory. Purchases are **never** mutated by a sale —
  see "sales" in design history for why (user explicitly asked for this: buy history has to
  stay intact as a price-paid reference for future purchases).
- `validate_purchases()` / `validate_sales()` — both reject fractional shares; `validate_sales()`
  additionally rejects selling more shares (summed across all its rows for a ticker) than that
  ticker's total in `purchases`.
- `summarize_by_ticker(purchases, sales, current_prices_cop)` — one row per ticker with net
  shares (purchased − sold) > 0 only; a fully-sold ticker doesn't appear.
  `realized_gains_summary(purchases, sales)` — one row per ticker with ≥1 sale: average-cost
  basis (not FIFO lots) vs. net-of-commission sale proceeds. `commission_summary(purchases,
  sales)` — now sums both legs' commissions. `simulate_additional_purchase(purchases, sales,
  ...)` — `current_shares` is net-held, but `current_avg_price_cop` still comes from ALL
  purchases (a partial sale doesn't change the average cost of what's left, only how many
  shares are left). `DEFAULT_COMMISSION_COP` (7,438 COP) is the per-operation (buy or sell)
  default, editable per row, never retroactive.
- `build_synthetic_portfolio_series()` — combines each holding's underlying (USD) price history
  into one weighted synthetic index (today's allocation applied across the whole lookback, not a
  real historical reconstruction), feeds `evaluate_risk_return()` (`src/valuation/risk_return.py`,
  untouched — no new risk math).
- `project_future_value()` — standard future-value formula (lump sum + monthly annuity,
  compounded monthly) for "🎯 Proyección de meta". Pure deterministic arithmetic; the caller
  decides what rate to feed it.

## `src/drawdown_dca.py`

- `current_drawdown_from_high()` (bare %, used internally by `current_bucket_reaction()`),
  `current_drawdown_snapshot()` (the one the UI actually calls — also returns the reference
  prices and the trailing-high date, for the "AMZN (USD): $231 hoy vs. máximo de $275 el
  2026-05-06" context line), `classify_drawdown_bucket()`, `compute_drawdown_bucket_reactions()`,
  `current_bucket_reaction()` — the reusable computation behind the drawdown-bucket line (mirrors
  `speculation.py`'s regime-reaction functions, same shape). For stocks, runs on
  `ev.historical_prices` already in memory; `ETFEvaluation` does **not** carry that field (a real
  bug this session), so the ETF branch fetches separately via `_cached_historical_prices()`. This
  is a **deliberate exception** to the project's no-timing-language rule (the second one, after
  Especulación) — see `CLAUDE.md` for why it's scoped to Portfolio and why the language must stay
  descriptive, not imperative.
- `build_laddered_buy_plan()` — the "🪜 Plan de compra escalonada" expander inside each stock/ETF
  context card in `render_capital()`: splits a user-entered COP budget across that ticker's
  VALIDATED drawdown buckets only (never an unvalidated one), weighted more toward the deeper
  bucket via a simple rank ratio (1:2, 1:2:3, ...) — see design history for why that specific
  weighting is a risk-management convention layered on top of the validation, not itself
  backtested. Only rendered when `DRAWDOWN_VALIDATED_BUCKETS.get(underlying)` is non-empty
  (never for CSPXCO, which has no validated bucket).

## `scripts/add_sale.py`

- CLI: `add_sale.py TICKER SHARES PRICE_COP COMMISSION_COP FECHA` — appends a sale to
  `sales.json` after running the exact same `validate_sales()` the UI table uses (won't write if
  it would oversell a ticker or fails a field check). Exists so a sale the user reports in chat
  can be recorded without opening the browser or hand-editing JSON — always echoes back what's
  now on file for that ticker so the save can be confirmed.

## `src/config.py`

- `PORTFOLIO_CDI_TICKERS` — the only tickers selectable here (Colombian CDIs, not the plain USD
  `TICKERS`). `PORTFOLIO_CDI_UNDERLYING` — maps each CDI to the `TICKERS`/`ETF_TICKERS` company
  its "Contexto de valoración" card should reference (also the key `DRAWDOWN_VALIDATED_BUCKETS`
  is indexed by — `"CSPXCO"`, not `"CSPX"`, for the ETF). `PORTFOLIO_CDI_SECTOR` — static
  CDI→sector map for "🥧 Diversificación" (not sourced from either provider — see `CLAUDE.md`
  for why).

## `src/data/fx.py`

- Exists but is **not** used by this tab on purpose (CDIs already quote in COP) — don't wire it
  in without re-reading why in `CLAUDE.md`.

## Known dead end

- A target-allocation/rebalancing section was tried here and removed — not wanted. If revisited,
  it can now be based on net holdings (purchases minus sales, see `summarize_by_ticker()`) rather
  than the buy-only constraint that applied when this was tried — that constraint no longer holds
  now that sales are tracked separately.

## Design history

Full rationale for every design decision in this tab — see `references/design-history.md`. Read
it before changing purchase semantics, the drawdown-bucket accumulation zone, or the
diversification/risk-return/goal-projection sections.
