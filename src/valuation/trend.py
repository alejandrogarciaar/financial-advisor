"""Indicadores de tendencia: EMA de 55 períodos y SMA de 50/200 días. Ninguno mide si la
acción está barata o cara — miden si el precio de hoy viene por encima o por debajo de su
propio recorrido reciente. Por eso viven aparte, igual que `quality.py`/`solvency.py`, y no
participan en el voto de `summarize_signals()`.

    EMA_hoy = (precio_hoy - EMA_ayer) × multiplicador + EMA_ayer
    multiplicador = 2 / (período + 1)

Se usa 55 (número de Fibonacci) por ser un período intermedio entre el corto plazo (21) y el
largo plazo (144) que suelen usar quienes siguen medias basadas en Fibonacci. 50 y 200 días son
las dos ventanas de SMA (promedio simple, sin ponderar por recencia) más usadas como referencia
general — se muestran junto a la EMA como otro ángulo de la misma idea, no como señal aparte.
"""

from dataclasses import dataclass

EMA_PERIOD = 55
SMA_SHORT_PERIOD = 50
SMA_LONG_PERIOD = 200


@dataclass
class TrendResult:
    ema: float
    price_vs_ema: float  # (precio actual - ema) / ema
    sma_50: float | None
    price_vs_sma_50: float | None
    sma_200: float | None
    price_vs_sma_200: float | None


def _ema(closes: list[float], period: int) -> float | None:
    if len(closes) < period:
        return None
    ema = sum(closes[:period]) / period  # semilla: SMA de los primeros `period` valores
    multiplier = 2 / (period + 1)
    for close in closes[period:]:
        ema = (close - ema) * multiplier + ema
    return ema


def simple_moving_average(closes: list[float], period: int) -> float | None:
    if len(closes) < period:
        return None
    return sum(closes[-period:]) / period


def evaluate_trend(historical_prices: list[dict], current_price: float) -> TrendResult | None:
    if not current_price:
        return None
    closes = [p["close"] for p in historical_prices]
    ema = _ema(closes, EMA_PERIOD)
    if not ema:
        return None

    sma_50 = simple_moving_average(closes, SMA_SHORT_PERIOD)
    sma_200 = simple_moving_average(closes, SMA_LONG_PERIOD)

    return TrendResult(
        ema=ema,
        price_vs_ema=(current_price - ema) / ema,
        sma_50=sma_50,
        price_vs_sma_50=(current_price - sma_50) / sma_50 if sma_50 else None,
        sma_200=sma_200,
        price_vs_sma_200=(current_price - sma_200) / sma_200 if sma_200 else None,
    )
