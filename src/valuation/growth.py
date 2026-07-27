"""Método de Crecimiento (PEG / PEGY de Peter Lynch): P/E dividido por la tasa de
crecimiento de EPS (más el dividend yield, para empresas que también pagan dividendo).

La lógica: una empresa que crece rápido merece un P/E más alto. El PEG ratio
normaliza el P/E por esa tasa de crecimiento. Regla de bolsillo (Peter Lynch):
PEG < 1 sugiere infravaloración, PEG > 2 sugiere sobrevaloración.

Lynch también usaba una variante para empresas que además reparten dividendo —
el PEGY— sumando el dividend yield a la tasa de crecimiento antes de dividir,
para no castigar injustamente a compañías maduras que crecen más lento pero
devuelven capital vía dividendos. Con dividend_yield=0 (el caso por defecto),
esta fórmula se reduce exactamente al PEG clásico.
"""

from dataclasses import dataclass


@dataclass
class GrowthResult:
    peg_ratio: float          # PEG si dividend_yield=0, PEGY si la empresa paga dividendo
    eps_growth_rate: float    # decimal, ej. 0.15 = 15%
    dividend_yield: float     # decimal, ej. 0.02 = 2%
    fair_value: float         # precio "justo" implícito si el ratio valiera 1


def estimate_eps_growth_rate(historical_eps: list[float], cap: tuple[float, float] = (-0.05, 0.30)) -> float:
    series = [v for v in historical_eps if v is not None]
    if len(series) < 2 or series[-1] <= 0 or series[0] <= 0:
        raise ValueError("Historial de EPS insuficiente o negativo para estimar crecimiento")
    years = len(series) - 1
    cagr = (series[0] / series[-1]) ** (1 / years) - 1
    return max(cap[0], min(cap[1], cagr))


def evaluate_growth(
    current_eps: float,
    current_price: float,
    historical_eps: list[float],
    dividend_yield: float = 0.0,
) -> GrowthResult:
    if current_eps <= 0:
        raise ValueError("PEG no aplica: la empresa no tiene ganancias positivas")
    growth_rate = estimate_eps_growth_rate(historical_eps)

    growth_pct = growth_rate * 100
    yield_pct = max(0.0, dividend_yield) * 100
    denominator = growth_pct + yield_pct
    if denominator <= 0:
        raise ValueError("PEG/PEGY no aplica: ni el crecimiento ni el dividendo son positivos")

    current_pe = current_price / current_eps
    peg_ratio = current_pe / denominator
    # regla PEG=1: el P/E "justo" sería igual al crecimiento (+ dividend yield) en %
    fair_value = current_eps * denominator

    return GrowthResult(
        peg_ratio=peg_ratio,
        eps_growth_rate=growth_rate,
        dividend_yield=max(0.0, dividend_yield),
        fair_value=fair_value,
    )
