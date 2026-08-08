---
name: us-stocks-portfolio
description: Use when the user's request is scoped to the "💰 Portafolio" tab of the USStocks (Precio Justo) dashboard — recording COP purchases or sales (both append-only), the held-position summary, realized gains, or the "Plan de compra escalonada" laddered-buy section. Points to the files that make up this tab so work stays scoped to them.
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

**Non-negotiable rule (explicit user instruction, 2026-08-06): "Ganancias realizadas" is
READ-ONLY, derived data.** No skill or process — not the user through some future UI control,
not Claude editing files or code directly — may modify what `realized_gains_summary()` (in
`src/portfolio.py`, rendered in `render_capital()`) shows, except by adding a new sale via the
"➕ Registrar una venta nueva" form or `scripts/add_sale.py`. Concretely: never hand-edit
`purchases.json`/`sales.json` to change a realized-gain figure, never add a UI path that edits or
deletes a purchase/sale row, and never alter the `realized_gains_summary()` calculation itself as
a way to "fix" a number. If a figure looks wrong, the fix is a new sale record that corrects it
going forward — same pattern as the sales-append-only rule above, extended explicitly to this
derived section.

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
  "💸 Costo de comisiones", "💵 Ganancias realizadas", "🪜 Plan de compra escalonada",
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
  request — see design history). The rest of the page: "🪜 Plan de compra escalonada" (see
  `src/drawdown_dca.py` below) is its own independent section now — removed by explicit user
  request (2026-08-06) was the old "📎 Contexto de valoración" section that used to wrap it (per-
  ticker cards pulling each CDI's underlying valuation verdict via `STOCK_EVAL_CACHE_KEY`/
  `ETF_EVAL_CACHE_KEY`, populated by Acciones/ETFs — see design history for why); the laddered
  plan only ever needed raw price history, not a full valuation evaluation, so it now fetches
  that directly via `_cached_historical_prices()` for every held ticker's underlying (stock or
  ETF alike, no more of the old stock-vs-ETF fetch-path split) instead of going through
  Acciones/ETFs' cross-tab caches. Still gated on `held_tickers` being non-empty (only for what
  you actually hold) and still captures `underlying_prices: dict[str, list[dict]]` while it
  loops, so the 3 sections right after it don't need to re-fetch anything. Redesigned again
  the same day (2026-08-06, second request): each card now leads with an explicit 3-way zone
  read — 🟢 acumulación / 🔴 distribución-venta / 🟡 en rango, see `src/drawdown_dca.py` below
  for how those 3 map onto the existing bucket machinery — and both the current/máximo price
  caption and the plan's per-rung price levels now show in COP (converted from the underlying's
  USD history via today's live CDI/underlying ratio, see below) instead of USD whenever the
  CDI's live COP quote is available. The rung table itself moved from stacked `st.markdown`
  lines to an `st.dataframe` (pre-formatted string columns, not `column_config` numeric
  formatting — simpler and matches how "Ganancias realizadas" already formats its money/percent
  columns), plus a caption right above the budget input explaining what it actually does
  (spread one lump sum across levels instead of buying it all at today's price).
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
  realizadas" instead. "🪜 Plan de compra escalonada" and the 3 sections after it (see below) are
  gated on `held_tickers` being non-empty for the same reason.
- Right after "Plan de compra escalonada", 3 more sections gated on `held_tickers` non-empty:
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
  `render_capital` from `src/ui/portfolio.py`) — stays **last** in that wiring by user request
  (see `CLAUDE.md`'s "`st.tabs()` is not lazy" section). Used to also be justified by reusing
  `STOCK_EVAL_CACHE_KEY`/`ETF_EVAL_CACHE_KEY` from Acciones/ETFs for the old "Contexto de
  valoración" section — that consumer is gone (see above), so the ordering is purely the user's
  preferred page layout now, not a data dependency.

## `src/portfolio.py`

- `load_purchases()` / `save_purchases()` — persisted to `portfolio_data/purchases.json`.
  `load_sales()` / `save_sales()` — persisted to `portfolio_data/sales.json`, same shape
  (ticker/shares/price_cop/commission_cop/date), separate file. Real user data — never delete
  without asking, see memory. **Committed to git since 2026-08-08** (explicit user request, so
  the public Streamlit Cloud deploy shows real data — see `CLAUDE.md`'s "Deploying" section),
  no longer gitignored like `app_data/` still is — a local edit via the UI/`scripts/add_sale.py`
  needs an explicit `git add`/commit/push to actually reach the public deploy, nothing auto-
  syncs. Purchases are **never** mutated by a sale —
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
  `speculation.py`'s regime-reaction functions, same shape). Used to run on `ev.historical_prices`
  already in memory for stocks (piggybacking on the old "Contexto de valoración" valuation fetch)
  while ETFs fetched separately via `_cached_historical_prices()` (`ETFEvaluation` never carried
  that field — a real bug found this session, before Contexto de valoración existed). Now that
  the valuation fetch is gone (see above), both stocks and ETFs go through
  `_cached_historical_prices()` the same way — one fetch path instead of two. This is a
  **deliberate exception** to the project's no-timing-language rule (the second one, after
  Especulación) — see `CLAUDE.md` for why it's scoped to Portfolio and why the language must stay
  descriptive, not imperative.
- **3-way zone classification** (`src/ui/portfolio.py`, inside the "🪜 Plan de compra
  escalonada" loop, not in `drawdown_dca.py` itself — no new module-level constant, built
  entirely from existing pieces): 🟢 "acumulación" if today's bucket
  (`classify_drawdown_bucket(snapshot.drawdown)`) is in the ticker's validated set (see the
  COP-vs-USD basis note below — this can be `DRAWDOWN_VALIDATED_BUCKETS_COP` or
  `DRAWDOWN_VALIDATED_BUCKETS`, never both at once for the same ticker); 🔴 "distribución/venta"
  if today's bucket is `DRAWDOWN_BUCKETS[0][0]` (the "0-5%" bucket — closest to the 1-year high)
  — purely positional, **not** a validated sell signal, this project has never backtested a
  sell-side thesis, only the accumulation side; 🟡 "en rango" for everything else (a real
  pullback that isn't, for this specific ticker, one of the confirmed buckets). No new threshold
  was invented for this — it reuses the existing `DRAWDOWN_BUCKETS` boundaries and the existing
  validated-bucket gate, on explicit request from the user (2026-08-06) who wanted to read the
  zone at a glance without inventing an unvalidated new signal. The "📉 Vs. máximo 1 año -X%"
  metric that originally sat next to this badge was removed the same day, second request ("no
  le veo utilidad") — the % is still there, just folded into the "hoy vs. máximo" caption below
  instead of its own metric.
- **COP-native basis, when validated (`DRAWDOWN_VALIDATED_BUCKETS_COP` in `src/ui/portfolio.py`,
  right after `DRAWDOWN_VALIDATED_BUCKETS`)**: added 2026-08-06 after the user asked whether the
  drawdown-bucket OOS validation could be re-run on the CDI's own COP price history (its native
  BVC quote, `.CL` suffix) instead of the underlying's USD history. It can — same
  `scripts/oos_validate.py` methodology (60/40 chronological split, horizons 20/60/90/180,
  `min_observations=15`), pointed at `PORTFOLIO_CDI_TICKERS[ticker]`'s own historical prices
  instead of `TICKERS`/`ETF_TICKERS[underlying]`'s. Result: `AMZNCO`/`AAPLCO`/`MSFTCO` don't
  have enough COP history on yfinance yet to even attempt it (74-136 rows, need 252+ just for
  one trailing-high window); `GOOGLCO`/`METACO` have ~2 years but nothing validated (too few
  train-side observations); `CSPXCO` has the longest COP history (~4.5 years, since 2021) and
  its "5-10%" bucket validated across all 4 horizons (train n=110, test n=164-197) — notable
  because CSPXCO has **zero** validated buckets in the USD-based `DRAWDOWN_VALIDATED_BUCKETS`
  (see its comment above: "ni bajó lo suficiente... para poder chequear nada más allá de
  0-5%"). So today `DRAWDOWN_VALIDATED_BUCKETS_COP = {"CSPXCO": {"5-10%"}}` — keyed by the CDI
  ticker itself (not `underlying`), since the series under test IS the CDI now, unlike the USD
  dict which is keyed by the underlying stock/ETF name.

  **Integrity rule** (explicit user request, 2026-08-06 — "revisa integridad de la
  implementación con el backtesting"): a ticker's card NEVER mixes series. If
  `DRAWDOWN_VALIDATED_BUCKETS_COP.get(ticker)` is non-empty AND the CDI's own history actually
  fetched, the card computes `current_drawdown_snapshot()`, `current_bucket_reaction()`, and
  `build_laddered_buy_plan()` **entirely** from that COP series (`basis_prices`/`basis_closes`
  in the code) and shows the resulting price levels as-is, with **no** fx-ratio conversion — a
  real historical COP series, not an approximation. Every other ticker falls back to the
  original USD-basis path (`DRAWDOWN_VALIDATED_BUCKETS`, keyed by `underlying`) with the
  fx-ratio-converted COP display described next. This ticker-by-ticker basis choice is why the
  code computes `underlying_prices[ticker]` (the USD series, for the 3 sections below) and the
  COP-native `cop_prices` as two **separate** fetches even for CSPXCO — `underlying_prices` must
  always stay USD-denominated for every holding (see the risk/return section below, which
  mixes all holdings into one synthetic series and would break if one entry were secretly in a
  different currency), independent of which basis that ticker's own plan card uses.
- **COP display via live ratio, for tickers without their own COP validation** (also in
  `src/ui/portfolio.py`, same loop): the underlying's USD price series stays the actual basis
  for the bucket/validation math for these tickers (unchanged — swapping to a COP series
  without re-validating is exactly what the rule above prevents). To still show levels in COP
  (user request, 2026-08-06: "dejemos de usar USD cuando sea posible"),
  `fx_ratio = _cached_portfolio_price(ticker) / snapshot.current_price` is computed once per
  ticker (CDI's live COP quote ÷ underlying's live USD quote) and applied to both
  `snapshot.trailing_high` and each rung's `price_low`/`price_high` — because the same ratio
  multiplies both the current price and the historical high, the resulting drawdown % is
  mathematically identical to the USD-computed one (the ratio cancels out of the subtraction),
  so this changes zero classifications, only the displayed currency. It's still an
  approximation (assumes the CDI/underlying ratio held roughly constant across the 1-year
  lookback — the same "tracks 1:1" premise `PORTFOLIO_CDI_UNDERLYING` already relies on), and
  the UI caption says so explicitly. Falls back to USD display if `_cached_portfolio_price`
  returns `None` (CDI quote unavailable right now).
- **Validated sell/distribution bucket (`DRAWDOWN_VALIDATED_SELL_BUCKETS`, right after
  `DRAWDOWN_VALIDATED_BUCKETS_COP`)**: added 2026-08-06, same day, after the user asked to
  actually backtest the "0-5%" bucket (the one the 🔴 badge shows) instead of leaving it purely
  positional. Same `scripts/oos_validate.py` methodology, tested against
  GOOGL/AMZN/AAPL/MSFT/META (USD, matching `DRAWDOWN_VALIDATED_BUCKETS`' basis) and CSPXCO.CL
  (COP, matching `DRAWDOWN_VALIDATED_BUCKETS_COP`'s basis) — never a basis mismatch with what
  that ticker's card already uses. Only **AAPL** validated: a consistently **negative** forward-
  return gap across all 4 horizons, both train and test (train n=172, test n=121-192 — a solid
  sample, not a thin one). Everyone else's sign flips between train/test on at least one
  horizon. `DRAWDOWN_VALIDATED_SELL_BUCKETS = {"AAPL": {"0-5%"}}`, keyed by `underlying` like
  its accumulation counterpart (no COP-basis entry yet — nothing validated there). This is the
  first confirmed sell-side result anywhere in this project (see `CLAUDE.md`'s no-timing-
  language discussion) — the 🔴 badge itself doesn't change (still shown for any ticker whose
  bucket is "0-5%"), but when the ticker ALSO has a `DRAWDOWN_VALIDATED_SELL_BUCKETS` entry for
  that bucket, the caption underneath switches from the generic "no es una señal confirmada" to
  an `st.error` quoting the actual confirmed mean return/win rate/n — same visual weight as
  `st.success` on the accumulation side, same descriptive-not-imperative language rule (states
  what the franja historically returned, never "vendé").
- `build_laddered_buy_plan()` — feeds the "🪜 Plan de compra escalonada" section in
  `render_capital()`, now its own independent section (not nested inside a valuation card
  anymore — see above): splits a user-entered COP budget across that ticker's VALIDATED drawdown
  buckets only (never an unvalidated one), weighted more toward the deeper bucket via a simple
  rank ratio (1:2, 1:2:3, ...) — see design history for why that specific weighting is a
  risk-management convention layered on top of the validation, not itself backtested. Only
  rendered when `DRAWDOWN_VALIDATED_BUCKETS.get(underlying)` is non-empty (never for CSPXCO,
  which has no validated bucket) — same "only for what you actually hold" gate as before, since
  the whole section only ever loops over `held_tickers`.

## `scripts/add_sale.py`

- CLI: `add_sale.py TICKER SHARES PRICE_COP COMMISSION_COP FECHA` — appends a sale to
  `sales.json` after running the exact same `validate_sales()` the UI table uses (won't write if
  it would oversell a ticker or fails a field check). Exists so a sale the user reports in chat
  can be recorded without opening the browser or hand-editing JSON — always echoes back what's
  now on file for that ticker so the save can be confirmed.

## `src/config.py`

- `PORTFOLIO_CDI_TICKERS` — the only tickers selectable here (Colombian CDIs, not the plain USD
  `TICKERS`), and the ones `_cached_portfolio_price()` queries directly for each CDI's own COP
  price (see below — these already quote natively in COP on the BVC via yfinance's `.CL`
  suffix, no FX conversion needed). `PORTFOLIO_CDI_UNDERLYING` — maps each CDI to the
  `TICKERS`/`ETF_TICKERS` company whose price history "🪜 Plan de compra escalonada" and
  "🥧 Diversificación"/"📈 Retorno y riesgo" pull (also the key `DRAWDOWN_VALIDATED_BUCKETS` is
  indexed by — `"CSPXCO"`, not `"CSPX"`, for the ETF). `PORTFOLIO_CDI_SECTOR` — static
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
