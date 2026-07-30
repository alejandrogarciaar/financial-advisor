---
name: us-stocks-add-ticker-or-formula
description: Use when the user wants to add a new stock ticker to the dashboard, or add/modify a valuation formula/signal in the USStocks (Precio Justo) project. Guides both workflows following the existing patterns in src/config.py and src/valuation/.
---

# Add a ticker or a valuation formula (USStocks / Precio Justo)

Two distinct workflows. Ask the user which one applies if it's not obvious, then follow the
matching checklist below. Read `CLAUDE.md` first if you haven't already this session — it
explains the family/signal split this skill depends on.

## Workflow A: Add a new ticker

1. Edit `TICKERS` in `src/config.py`. Add the ticker with an inline `# Company Name` comment,
   matching the existing style.
2. **Before adding, check both providers actually support it** — this project has already hit
   provider gaps twice (see the comments above `TICKERS` in `src/config.py` for CSPXCO and NU):
   - Does it have real financial statements (not an ETF/fund)?
   - Query FMP's `quote`, `income-statement`, and `historical-price-eod/full` endpoints for the
     ticker — a 402/"not available in your plan" response means it's blocked on the free plan.
   - Try `yfinance.Ticker(ticker).info`, `.income_stmt`, `.cashflow`, `.balance_sheet` — confirm
     they return non-empty data.
3. If a provider can't serve it, don't silently add the ticker — either exclude it with a
   comment explaining why (matching the CSPXCO/NU precedent), or proceed with only the working
   provider and note the limitation.
4. No other code changes needed — `src/ui/stocks.py`'s ticker selector and `evaluate_ticker()`
   both iterate over `TICKERS` generically.
5. **Check for a Colombian BVC homolog (CDI) and offer to add it to Portfolio too** — standing
   user preference: whenever a stock ticker is added, also look for its Colombian exchange CDI,
   normally `{TICKER}CO` (e.g. `META` → `METACO`), on yfinance as `{TICKER}CO.CL` (matches
   `GOOGLCO.CL`/`AMZNCO.CL`/`AAPLCO.CL`/`MSFTCO.CL`/`METACO.CL` already in
   `PORTFOLIO_CDI_TICKERS`). Verify it actually returns price history
   (`yf.Ticker("{TICKER}CO.CL").history(period="5d")` non-empty) before adding — don't assume
   the `{TICKER}CO.CL` pattern holds for every ticker without checking. If it exists, add it to
   all 3 dicts in `src/config.py` together: `PORTFOLIO_CDI_TICKERS` (`"{TICKER}CO":
   "{TICKER}CO.CL"`), `PORTFOLIO_CDI_UNDERLYING` (`"{TICKER}CO": ("stock", "{TICKER}")`), and
   `PORTFOLIO_CDI_SECTOR` (GICS sector — reuse an existing sector if the company clearly matches
   one already in the map, e.g. Communication Services for another media/tech-platform name; ask
   if genuinely ambiguous). Do NOT add it to `DRAWDOWN_VALIDATED_BUCKETS`
   (`src/ui/portfolio.py`) — that requires its own out-of-sample validation per ticker, adding an
   unvalidated entry there would violate this project's core validate-before-shipping rule (see
   `us-stocks-portfolio`'s Design history). If no BVC homolog exists for a given ticker, just
   say so — not every US stock has one.

## Workflow B: Add a new valuation formula/signal

Decide first: is this a **price signal** (estimates a fair value, competes with the existing 6
formulas) or a **filter** (answers a different question, like quality/solvency — doesn't
participate in the cheap/expensive vote)? This determines whether it joins `SIGNAL_FAMILIES` in
`fair_value.py` or stays separate like `quality.py`/`solvency.py`.

1. **Create the module** in `src/valuation/<name>.py`, following the shape of existing modules
   (`book_value.py` for a minimal example, `growth.py` for one with validation/`ValueError` for
   "doesn't apply" cases):
   - A module docstring explaining the formula's logic and where it comes from (this codebase
     documents *why*, not just *what*, in every valuation module — keep that up).
   - A `@dataclass` result type (e.g. `FooResult`) holding the computed values.
   - An `evaluate_foo(...)` pure function — no I/O, no imports from `src.data`. Raise
     `ValueError` for "formula doesn't apply to this company" cases (mirrors `growth.py`,
     `graham.py`, `graham_growth.py`) rather than returning a sentinel.

2. **Wire it into `fair_value.py`**:
   - Import the result type and `evaluate_foo` at the top.
   - In `TickerEvaluation`, add a `foo: FooResult | None` field (`None` if it can not apply to
     every company — add a one-line comment explaining when, matching the existing fields).
   - In `_evaluate_from_data()`, call `evaluate_foo(...)` wrapped in `try/except ValueError:
     foo_result = None` if it can fail to apply, then pass `foo=foo_result` into the returned
     `TickerEvaluation`.
   - If it's a **price signal**: compute `foo_margin = (foo_result.fair_value - current_price)
     / current_price` (or `None`), add `foo_margin`/`foo_zone` fields to `TickerEvaluation`
     (`foo_zone = classify_margin(foo_margin) if foo_margin is not None else None`), and decide
     which family it belongs to in `SIGNAL_FAMILIES` — if it's derived from the same EPS/growth
     input as PEG/Graham/Graham-growth, it likely joins "Múltiplos de ganancias", not a new
     family (re-read the comment above `SIGNAL_FAMILIES` on why over-splitting correlated
     formulas inflates the consensus count artificially).
   - If it's a **filter** (like quality/solvency): do NOT add it to `SIGNAL_FAMILIES` or give it
     a margin/zone — it must not affect `summarize_signals()`'s cheap/expensive vote.

3. **Update `src/backtest.py` only if needed** — `_evaluate_from_data` is shared with the
   backtest, so a new field flows through automatically. No changes required unless the new
   signal needs data that the backtest's truncated historical slicing doesn't provide.

4. **Update `src/ui/stocks.py`**: add a render block in `render_detail()` (and an
   `explain_foo()` helper if it's a price signal, following `explain_book_value`/
   `explain_growth` for tone — plain-language Spanish, no imperative "buy/sell" language per the
   existing `FRIENDLY_ZONE` comment). If it's a price signal, also add it to the
   `st.write(details)` block in the "detalle técnico" expander if it has an interesting internal
   parameter to expose.

5. Manually sanity-check by running the app (`streamlit run app.py`) against a couple of
   tickers, including at least one where you expect the new formula to raise `ValueError` (to
   confirm it degrades gracefully instead of crashing the page).
