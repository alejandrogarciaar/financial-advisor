"""Valoración relativa: bandas históricas de P/E propio (mean-reversion), con el promedio
ponderado por recencia — un año viejo de múltiplo bajo no debería arrastrar el 'precio justo'
hacia abajo para siempre si la empresa se re-calificó estructuralmente hace poco (hallazgo del
backtest: ver `multiple_quality_context_note()` en `fair_value.py`). El año más reciente pesa
más, los más viejos pesan cada vez menos — sigue siendo 100% histórico, no predice nada, solo
resume el pasado de otra forma.
"""

from dataclasses import dataclass

import numpy as np

# Peso relativo de cada año hacia atrás: RECENCY_DECAY ** años_atrás. 0.65 → el año pasado
# pesa 65% de lo que pesa el más reciente, el anteúltimo 42%, etc. La historia vieja sigue
# contando (no es "solo el último año"), pero pesa cada vez menos en vez de todos por igual.
RECENCY_DECAY = 0.65


@dataclass
class MultipleBand:
    mean_pe: float
    std_pe: float
    p25_pe: float
    p75_pe: float
    current_pe: float
    fair_value: float  # eps actual * mean_pe histórico (ponderado por recencia)


def historical_pe_band(historical_pe: list[float]) -> dict:
    """`historical_pe` viene ordenado del año más reciente al más viejo (índice 0 = último
    año). `mean` se pondera por recencia usando esa posición ORIGINAL (no la posición después
    de descartar años inválidos, para no tratar un año realmente viejo como si fuera reciente
    solo porque el de en medio faltaba). `std`/`p25`/`p75` quedan sin ponderar — son el rango
    histórico completo para contexto, no alimentan la cuenta del precio justo."""
    valid = [(i, v) for i, v in enumerate(historical_pe) if v and v > 0]
    if len(valid) < 3:
        raise ValueError("Historia de P/E insuficiente para construir bandas")
    indices = np.array([i for i, _ in valid])
    series = np.array([v for _, v in valid])
    weights = RECENCY_DECAY**indices
    return {
        "mean": float(np.average(series, weights=weights)),
        "std": float(np.std(series)),
        "p25": float(np.percentile(series, 25)),
        "p75": float(np.percentile(series, 75)),
    }


def evaluate_multiple(current_eps: float, current_pe: float, historical_pe: list[float]) -> MultipleBand:
    band = historical_pe_band(historical_pe)
    fair_value = current_eps * band["mean"] if current_eps > 0 else 0.0
    return MultipleBand(
        mean_pe=band["mean"],
        std_pe=band["std"],
        p25_pe=band["p25"],
        p75_pe=band["p75"],
        current_pe=current_pe,
        fair_value=fair_value,
    )
