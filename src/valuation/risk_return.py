"""Riesgo y retorno histórico: CAGR (1/3/5 años), volatilidad anualizada, Sharpe ratio y
máxima caída — todo calculado directamente del historial de precios. 100% retrospectivo, no
proyecta nada a futuro. Compartido entre acciones (`fair_value.py`) y ETFs
(`etf_analysis.py`) para no calcular la misma matemática dos veces.
"""

import math
from dataclasses import dataclass
from datetime import datetime, timedelta

TRADING_DAYS_PER_YEAR = 252


@dataclass
class RiskReturnResult:
    cagr_1y: float | None
    cagr_3y: float | None
    cagr_5y: float | None
    annualized_volatility: float | None
    sharpe_ratio: float | None
    max_drawdown: float | None


def _parse_date(date_str: str) -> datetime:
    return datetime.strptime(date_str, "%Y-%m-%d")


def _cagr_trailing(prices: list[dict], years: int) -> float | None:
    """CAGR desde el precio más cercano a `years` atrás hasta el último dato disponible."""
    if len(prices) < 2:
        return None
    end = prices[-1]
    end_date = _parse_date(end["date"])
    start_date_actual = _parse_date(prices[0]["date"])
    target_date = end_date - timedelta(days=365 * years)
    if start_date_actual > target_date:
        return None  # no hay suficiente historia para esta ventana
    closest = min(prices, key=lambda p: abs((_parse_date(p["date"]) - target_date).days))
    if closest["close"] <= 0:
        return None
    return (end["close"] / closest["close"]) ** (1 / years) - 1


def _cagr_full_period(prices: list[dict]) -> float | None:
    """CAGR sobre exactamente la ventana de precios disponible — para emparejar con la
    volatilidad (calculada sobre la misma ventana) al armar el Sharpe ratio."""
    if len(prices) < 2:
        return None
    start, end = prices[0], prices[-1]
    years = (_parse_date(end["date"]) - _parse_date(start["date"])).days / 365.25
    if years <= 0 or start["close"] <= 0:
        return None
    return (end["close"] / start["close"]) ** (1 / years) - 1


def _annualized_volatility(closes: list[float]) -> float | None:
    if len(closes) < 3:
        return None
    daily_returns = [closes[i] / closes[i - 1] - 1 for i in range(1, len(closes))]
    mean = sum(daily_returns) / len(daily_returns)
    variance = sum((r - mean) ** 2 for r in daily_returns) / (len(daily_returns) - 1)
    return math.sqrt(variance) * math.sqrt(TRADING_DAYS_PER_YEAR)


def _sharpe_ratio(cagr: float | None, volatility: float | None, risk_free_rate: float) -> float | None:
    if cagr is None or not volatility:
        return None
    return (cagr - risk_free_rate) / volatility


def _max_drawdown(closes: list[float]) -> float | None:
    if not closes:
        return None
    peak = closes[0]
    max_dd = 0.0
    for price in closes:
        peak = max(peak, price)
        max_dd = min(max_dd, (price - peak) / peak)
    return max_dd


def evaluate_risk_return(historical_prices: list[dict], risk_free_rate: float) -> RiskReturnResult:
    # yfinance a veces devuelve un cierre NaN puntual (hueco de datos de un día sin operatoria
    # detectada) — un solo NaN en la serie contamina la suma completa de la varianza/CAGR, así
    # que se filtra acá antes de que llegue a cualquier cálculo.
    prices = [p for p in historical_prices if p.get("close") is not None and not math.isnan(p["close"])]
    closes = [p["close"] for p in prices]
    volatility = _annualized_volatility(closes)
    full_period_cagr = _cagr_full_period(prices)
    return RiskReturnResult(
        cagr_1y=_cagr_trailing(prices, 1),
        cagr_3y=_cagr_trailing(prices, 3),
        cagr_5y=_cagr_trailing(prices, 5),
        annualized_volatility=volatility,
        sharpe_ratio=_sharpe_ratio(full_period_cagr, volatility, risk_free_rate),
        max_drawdown=_max_drawdown(closes),
    )
