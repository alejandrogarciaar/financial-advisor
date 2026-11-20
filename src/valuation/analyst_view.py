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
    # Rango y cobertura del consenso (agregados para Portafolio, 2026-11-17). Con default para
    # no romper a quien construya AnalystView sin ellos.
    price_target_low: float | None = None
    price_target_high: float | None = None
    number_of_analysts: int | None = None
    recommendation_key: str | None = None    # "strong_buy" / "buy" / "hold" / "sell" / ...
    recommendation_mean: float | None = None  # 1 = compra fuerte ... 5 = venta fuerte


def build_analyst_view(
    price_target_mean: float | None,
    price_target_median: float | None,
    forward_growth_rate: float | None,
    eps_estimate_now: float | None,
    eps_estimate_90d_ago: float | None,
    price_target_low: float | None = None,
    price_target_high: float | None = None,
    number_of_analysts: int | None = None,
    recommendation_key: str | None = None,
    recommendation_mean: float | None = None,
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
        price_target_low=price_target_low,
        price_target_high=price_target_high,
        number_of_analysts=number_of_analysts,
        recommendation_key=recommendation_key,
        recommendation_mean=recommendation_mean,
    )


def analyst_view_from_raw(raw: dict) -> AnalystView:
    """Arma un AnalystView desde el dict de `get_analyst_view()` del proveedor — un solo lugar
    que conoce los nombres de campo, compartido por Acciones (`fair_value.py`) y Portafolio."""
    return build_analyst_view(
        price_target_mean=raw.get("priceTargetMean"),
        price_target_median=raw.get("priceTargetMedian"),
        forward_growth_rate=raw.get("forwardGrowth"),
        eps_estimate_now=raw.get("epsEstimateNow"),
        eps_estimate_90d_ago=raw.get("epsEstimate90dAgo"),
        price_target_low=raw.get("priceTargetLow"),
        price_target_high=raw.get("priceTargetHigh"),
        number_of_analysts=raw.get("numberOfAnalysts"),
        recommendation_key=raw.get("recommendationKey"),
        recommendation_mean=raw.get("recommendationMean"),
    )
