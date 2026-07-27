"""Zona de acumulación para Portafolio: en qué franja de "% de caída desde su máximo de 1 año"
está un ticker hoy, y si esa franja tiene retorno futuro validado fuera de muestra.

No es un detector de soportes — no mira mínimos/máximos locales ni pivotes (eso ya se probó
para Especulación con soporte/resistencia y no dio una señal confiable, ver CLAUDE.md). Es la
pregunta más simple "¿cuánto bajó desde su propio máximo reciente?", que da muchas más
observaciones por bucket que cualquier enfoque basado en detectar niveles.

`DRAWDOWN_VALIDATED_BUCKETS` (en app.py, no acá) es el gate estático de qué (ticker, bucket)
sobrevivió una validación out-of-sample (split cronológico 60/40, mismo patrón que
`REGIME_VALIDATED_COMBOS` de Especulación) — este módulo solo provee el cómputo reutilizable,
tanto para esa investigación (reproducible corriendo `compute_drawdown_bucket_reactions` sobre
un split) como para el número que se muestra en vivo (sobre todo el historial disponible, no
solo el tramo de test, para que no quede desactualizado)."""

from dataclasses import dataclass

import pandas as pd

DRAWDOWN_TRAILING_WINDOW = 252  # ~1 año de sesiones — mismo concepto que "high de 52 semanas"
DRAWDOWN_MIN_OBSERVATIONS = 15
DRAWDOWN_REACTION_HORIZON_DAYS = 90  # fijo de antemano, ver comentario en app.py

DRAWDOWN_BUCKETS = [
    ("0-5%", 0.00, 0.05),
    ("5-10%", 0.05, 0.10),
    ("10-15%", 0.10, 0.15),
    ("15-20%", 0.15, 0.20),
    ("20-30%", 0.20, 0.30),
    ("30%+", 0.30, 1.01),
]


def current_drawdown_from_high(closes: list[float]) -> float | None:
    """% de caída del último cierre respecto al máximo de los DRAWDOWN_TRAILING_WINDOW cierres
    más recientes (incluido el último). None si no hay suficiente historial todavía."""
    if len(closes) < DRAWDOWN_TRAILING_WINDOW:
        return None
    window = closes[-DRAWDOWN_TRAILING_WINDOW:]
    trailing_high = max(window)
    return (trailing_high - closes[-1]) / trailing_high


@dataclass
class DrawdownSnapshot:
    drawdown: float
    current_price: float
    trailing_high: float
    trailing_high_date: str


def current_drawdown_snapshot(historical_prices: list[dict]) -> DrawdownSnapshot | None:
    """Como `current_drawdown_from_high`, pero para la UI: además del %, devuelve los precios
    de referencia (actual y máximo) y la fecha del máximo, para que se pueda mostrar de dónde
    sale la cuenta en vez de solo el porcentaje pelado. Recibe `historical_prices` (con fecha,
    no solo cierres) por eso — a diferencia del resto de este módulo, que solo necesita
    cierres."""
    dated = sorted(historical_prices, key=lambda p: p["date"])
    if len(dated) < DRAWDOWN_TRAILING_WINDOW:
        return None
    window = dated[-DRAWDOWN_TRAILING_WINDOW:]
    high_point = max(window, key=lambda p: p["close"])
    current_price = dated[-1]["close"]
    return DrawdownSnapshot(
        drawdown=(high_point["close"] - current_price) / high_point["close"],
        current_price=current_price,
        trailing_high=high_point["close"],
        trailing_high_date=high_point["date"],
    )


def classify_drawdown_bucket(drawdown: float) -> str:
    for label, lo, hi in DRAWDOWN_BUCKETS:
        if lo <= drawdown < hi:
            return label
    return DRAWDOWN_BUCKETS[-1][0]


@dataclass
class DrawdownBucketReaction:
    bucket: str
    horizon_days: int
    observations: int
    mean_return: float | None  # retorno promedio histórico a horizon_days, dado ese bucket
    win_rate: float | None


def compute_drawdown_bucket_reactions(
    closes: list[float], horizon_days: int = DRAWDOWN_REACTION_HORIZON_DAYS
) -> list[DrawdownBucketReaction]:
    """Para cada bucket de caída, junta todos los días históricos que estuvieron en esa franja
    y mide qué retorno tuvieron horizon_days después — mismo patrón que
    `compute_regime_reactions` en speculation.py. Sin lookahead: el máximo móvil que define el
    bucket de cada día solo mira hacia atrás desde ese día."""
    n = len(closes)
    closes_series = pd.Series(closes, dtype=float)
    trailing_high = closes_series.rolling(DRAWDOWN_TRAILING_WINDOW, min_periods=DRAWDOWN_TRAILING_WINDOW).max()
    drawdown = (trailing_high - closes_series) / trailing_high
    forward_return = (closes_series.shift(-horizon_days) - closes_series) / closes_series
    valid = forward_return.notna() & drawdown.notna()

    reactions = []
    for label, lo, hi in DRAWDOWN_BUCKETS:
        mask = valid & (drawdown >= lo) & (drawdown < hi)
        n_obs = int(mask.sum())
        if n_obs < DRAWDOWN_MIN_OBSERVATIONS:
            reactions.append(DrawdownBucketReaction(label, horizon_days, n_obs, None, None))
            continue
        vals = forward_return[mask]
        reactions.append(
            DrawdownBucketReaction(label, horizon_days, n_obs, float(vals.mean()), float((vals > 0).mean()))
        )
    return reactions


def current_bucket_reaction(
    closes: list[float], horizon_days: int = DRAWDOWN_REACTION_HORIZON_DAYS
) -> DrawdownBucketReaction | None:
    """La reacción histórica (sobre TODO el historial disponible, no un split) del bucket en el
    que está el ticker HOY — conveniencia para que el caller no tenga que clasificar el bucket
    de hoy y después buscarlo en la lista. None si no hay suficiente historial para saber en
    qué bucket está hoy."""
    drawdown = current_drawdown_from_high(closes)
    if drawdown is None:
        return None
    today_bucket = classify_drawdown_bucket(drawdown)
    reactions = compute_drawdown_bucket_reactions(closes, horizon_days)
    return next(r for r in reactions if r.bucket == today_bucket)
