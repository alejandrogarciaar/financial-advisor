"""Backtest simple: ¿el veredicto de triangulación de hace N años habría anticipado el
retorno real hasta hoy?

Limitaciones honestas (léelas antes de confiar en el resultado):
- yfinance solo expone 4-5 columnas anuales en el income statement (no hay forma de pedir
  más — es el techo real de Yahoo, no un límite nuestro), y la columna más vieja viene casi
  siempre `NaN` en "Diluted EPS" (a veces por una pérdida real de la empresa esos años —
  UBER 2021-2022 —, a veces por un simple hueco de cobertura de Yahoo — AAPL 2021, NVDA
  2022). Eso dejaba `years_ago=2` (el default anterior) sin suficiente historia de P/E para
  CUALQUIER ticker (0/8) — por eso el default es `years_ago=1`, que da 6/8. AMZN y UBER
  fallan incluso así (les faltan 2 años de datos válidos, no 1) — es un límite real del
  dato, no un bug: `run_backtest()` ya tolera fallos por ticker sin romper el resto.
- Usamos el beta ACTUAL de la empresa, no el de la fecha histórica (no tenemos forma de
  obtener el beta exacto de hace N años con estos proveedores).
- Muestra pequeña (8 tickers, todas mega-caps que ya "ganaron") — esto es un chequeo
  direccional, no una validación estadística rigurosa.
"""

from datetime import datetime, timedelta

from src.config import TICKERS
from src.valuation.fair_value import PROVIDERS, _evaluate_from_data, summarize_signals


def _price_years_ago(historical_prices: list[dict], years_ago: int) -> tuple[float, str]:
    dated = sorted(historical_prices, key=lambda p: p["date"])
    if not dated:
        raise ValueError("Sin historial de precios")
    latest_date = datetime.strptime(dated[-1]["date"], "%Y-%m-%d")
    target_date = latest_date - timedelta(days=years_ago * 365)
    closest = min(dated, key=lambda p: abs(datetime.strptime(p["date"], "%Y-%m-%d") - target_date))
    return closest["close"], closest["date"]


def backtest_ticker(ticker: str, provider: str = "yfinance", years_ago: int = 1) -> dict:
    client = PROVIDERS[provider]
    quote, _ = client.get_quote(ticker)
    profile, _ = client.get_profile(ticker)
    income_statements, _ = client.get_income_statement(ticker)
    cash_flows, _ = client.get_cash_flow_statement(ticker)
    balance_sheets, _ = client.get_balance_sheet(ticker)
    key_metrics, _ = client.get_key_metrics(ticker)
    historical_prices, _ = client.get_historical_prices(ticker)

    current_price = quote["price"]
    price_then, date_then = _price_years_ago(historical_prices, years_ago)

    # simula "solo lo que se sabía entonces": descarta los `years_ago` años fiscales más recientes
    income_then = income_statements[years_ago:]
    cash_flows_then = cash_flows[years_ago:]
    balance_then = balance_sheets[years_ago:]
    key_metrics_then = key_metrics[years_ago:]

    if len(income_then) < 2 or len(cash_flows_then) < 2 or not balance_then:
        raise ValueError(f"No queda suficiente historia fiscal previa para years_ago={years_ago}")

    evaluation_then = _evaluate_from_data(
        ticker=ticker,
        current_price=price_then,
        quote={"price": price_then, "marketCap": quote.get("marketCap")},
        profile=profile,
        income_statements=income_then,
        cash_flows=cash_flows_then,
        balance_sheets=balance_then,
        key_metrics=key_metrics_then,
        historical_prices=historical_prices,
        data_as_of=date_then,
        is_stale=False,
        stale_reason=None,
    )
    summary_then = summarize_signals(evaluation_then)
    actual_return = (current_price - price_then) / price_then

    return {
        "ticker": ticker,
        "date_then": date_then,
        "price_then": price_then,
        "price_now": current_price,
        "actual_return": actual_return,
        "verdict_then": summary_then["verdict"],
        "headline_then": summary_then["headline"],
    }


def run_backtest(tickers: list[str] = TICKERS, provider: str = "yfinance", years_ago: int = 1) -> list[dict]:
    results = []
    for t in tickers:
        try:
            results.append(backtest_ticker(t, provider=provider, years_ago=years_ago))
        except Exception as exc:
            results.append({"ticker": t, "error": str(exc)})
    return results
