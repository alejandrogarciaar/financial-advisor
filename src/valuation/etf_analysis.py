"""Análisis de ETFs (no empresas individuales): no tienen estados financieros propios, así
que ninguna de las 6 fórmulas de acciones (DCF, múltiplos, book value, PEG, Graham,
Graham-growth) aplica. En su lugar:

- Valuación: mismo truco que `graham.py` (asumir un P/E "normal" fijo y comparar) pero con el
  P/E histórico de largo plazo del S&P 500 como referencia, ya que no hay un P/E propio del
  fondo contra el cual comparar (a diferencia de `multiples.py`, que sí usa el historial propio
  de la empresa). Reusa `classify_margin` de `fair_value.py` para las mismas 4 zonas/colores
  que ya conoce el usuario.
- Tendencia: calculada directamente del histórico de precios (no del `.info` de Yahoo, que
  puede estar más desactualizado). La SMA la calcula `trend.py`, compartida con las acciones.
- Riesgo/retorno: delegado a `risk_return.py`, compartido con las acciones individuales.
"""

from dataclasses import dataclass

from src.data import yfinance_client
from src.valuation.fair_value import classify_margin
from src.valuation.risk_return import evaluate_risk_return
from src.valuation.trend import simple_moving_average as _sma

# Promedio histórico de largo plazo del P/E trailing del S&P 500 (~15-19x según el período
# considerado). Es una banda de referencia fija, no el propio historial del fondo — avisar esto
# en la explicación que ve el usuario.
REFERENCE_PE = 18.0


@dataclass
class ETFEvaluation:
    ticker: str
    name: str | None
    current_price: float
    fair_value: float | None       # None si no hay EPS trailing (Yahoo no siempre lo reporta)
    margin: float | None
    zone: str | None
    trailing_pe: float | None
    expense_ratio: float | None
    sma_50: float | None
    sma_200: float | None
    pct_from_52w_high: float | None
    pct_from_52w_low: float | None
    pct_from_ath: float | None
    cagr_1y: float | None
    cagr_3y: float | None
    cagr_5y: float | None
    annualized_volatility: float | None
    sharpe_ratio: float | None
    max_drawdown: float | None
    data_as_of: str
    is_stale: bool
    stale_reason: str | None


def _evaluate_from_data(
    display_ticker: str,
    info: dict,
    historical_prices: list[dict],
    risk_free_rate: float,
    data_as_of: str,
    is_stale: bool,
    stale_reason: str | None,
) -> ETFEvaluation:
    current_price = info["price"]
    eps_ttm = info.get("epsTrailingTwelveMonths")

    fair_value = margin = zone = None
    if eps_ttm and eps_ttm > 0:
        fair_value = eps_ttm * REFERENCE_PE
        margin = (fair_value - current_price) / current_price
        zone = classify_margin(margin)

    closes = [p["close"] for p in historical_prices]

    fifty_two_week_high = info.get("fiftyTwoWeekHigh")
    fifty_two_week_low = info.get("fiftyTwoWeekLow")
    all_time_high = info.get("allTimeHigh")

    rr = evaluate_risk_return(historical_prices, risk_free_rate)

    return ETFEvaluation(
        ticker=display_ticker,
        name=info.get("longName"),
        current_price=current_price,
        fair_value=fair_value,
        margin=margin,
        zone=zone,
        trailing_pe=info.get("trailingPE"),
        expense_ratio=info.get("netExpenseRatio"),
        sma_50=_sma(closes, 50),
        sma_200=_sma(closes, 200),
        pct_from_52w_high=(current_price - fifty_two_week_high) / fifty_two_week_high
        if fifty_two_week_high
        else None,
        pct_from_52w_low=(current_price - fifty_two_week_low) / fifty_two_week_low
        if fifty_two_week_low
        else None,
        pct_from_ath=(current_price - all_time_high) / all_time_high if all_time_high else None,
        cagr_1y=rr.cagr_1y,
        cagr_3y=rr.cagr_3y,
        cagr_5y=rr.cagr_5y,
        annualized_volatility=rr.annualized_volatility,
        sharpe_ratio=rr.sharpe_ratio,
        max_drawdown=rr.max_drawdown,
        data_as_of=data_as_of,
        is_stale=is_stale,
        stale_reason=stale_reason,
    )


def evaluate_etf(display_ticker: str, real_ticker: str, risk_free_rate: float) -> ETFEvaluation:
    info, info_meta = yfinance_client.get_etf_info(real_ticker)
    historical_prices, hist_meta = yfinance_client.get_historical_prices(real_ticker)

    metas = [info_meta, hist_meta]
    is_stale = any(m["from_cache"] for m in metas)
    data_as_of = min(m["fetched_at"] for m in metas)
    stale_reason = next((m["error"] for m in metas if m["from_cache"]), None)

    return _evaluate_from_data(
        display_ticker=display_ticker,
        info=info,
        historical_prices=historical_prices,
        risk_free_rate=risk_free_rate,
        data_as_of=data_as_of,
        is_stale=is_stale,
        stale_reason=stale_reason,
    )
