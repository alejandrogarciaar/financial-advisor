"""Fórmula de crecimiento de Benjamin Graham (The Intelligent Investor, 1962):

    V = EPS × (8.5 + 2g) × 4.4 / Y

Donde g es el crecimiento esperado de EPS a 7-10 años (en % entero) y 8.5 es el
P/E que Graham asignaba a una empresa sin crecimiento. El factor 4.4/Y ajusta
por el nivel de tasas de interés actual: 4.4% era el rendimiento de bonos AAA
cuando Graham calibró la fórmula en 1962 — si las tasas de hoy son más altas,
el múltiplo "justo" baja (y viceversa). Sin este ajuste la fórmula sobrevalora
sistemáticamente en entornos de tasas altas; es el error más común al aplicarla
hoy en día (muchas calculadoras online la usan "pelada", sin este factor).
"""

from dataclasses import dataclass

from src.valuation.growth import estimate_eps_growth_rate

GRAHAM_BASE_YIELD = 0.044  # rendimiento de bonos AAA cuando Graham calibró la fórmula (1962)


@dataclass
class GrahamGrowthResult:
    fair_value: float
    growth_rate: float       # decimal, ej. 0.15 = 15%
    implied_multiple: float  # múltiplo P/E "justo" ya ajustado por tasas


def evaluate_graham_growth(
    current_eps: float,
    historical_eps: list[float],
    current_bond_yield: float,
    base_yield: float = GRAHAM_BASE_YIELD,
) -> GrahamGrowthResult:
    if current_eps <= 0:
        raise ValueError("Esta fórmula requiere ganancias positivas")
    if current_bond_yield <= 0:
        raise ValueError("Se necesita una tasa de referencia positiva para el ajuste")

    growth_rate = estimate_eps_growth_rate(historical_eps)
    if growth_rate <= 0:
        raise ValueError("Esta fórmula requiere un crecimiento histórico de EPS positivo")

    g_pct = growth_rate * 100
    implied_multiple = (8.5 + 2 * g_pct) * base_yield / current_bond_yield
    fair_value = current_eps * implied_multiple

    return GrahamGrowthResult(
        fair_value=fair_value,
        growth_rate=growth_rate,
        implied_multiple=implied_multiple,
    )
