"""Consenso de analistas de Wall Street — NO es un método de valoración nuestro, es contexto.

El backtest mostró que nuestros métodos (basados en crecimiento HISTÓRICO) fallan en
compounders de calidad porque el mercado se mueve por revisiones de estimados FUTUROS que
nuestros métodos no capturan. Esto muestra ese dato que nos falta: hacia dónde están moviendo
los analistas sus estimados de ganancias, y qué precio objetivo le ponen — para comparar contra
nuestras propias señales, no para reemplazarlas.
"""

from dataclasses import dataclass


@dataclass
class AnalystView:
    price_target_mean: float | None
    price_target_median: float | None
    forward_growth_rate: float | None        # decimal, crecimiento esperado próximos ~12 meses
    eps_estimate_now: float | None
    eps_estimate_90d_ago: float | None
    estimate_revision_direction: str | None  # "al alza" / "a la baja" / "estable"


def build_analyst_view(
    price_target_mean: float | None,
    price_target_median: float | None,
    forward_growth_rate: float | None,
    eps_estimate_now: float | None,
    eps_estimate_90d_ago: float | None,
) -> AnalystView:
    direction = None
    if eps_estimate_now is not None and eps_estimate_90d_ago:
        change = (eps_estimate_now - eps_estimate_90d_ago) / abs(eps_estimate_90d_ago)
        if change > 0.01:
            direction = "al alza"
        elif change < -0.01:
            direction = "a la baja"
        else:
            direction = "estable"

    return AnalystView(
        price_target_mean=price_target_mean,
        price_target_median=price_target_median,
        forward_growth_rate=forward_growth_rate,
        eps_estimate_now=eps_estimate_now,
        eps_estimate_90d_ago=eps_estimate_90d_ago,
        estimate_revision_direction=direction,
    )
