"""DCF (Discounted Cash Flow) de dos etapas sobre Free Cash Flow to Firm (FCFF).

Corremos 3 escenarios (pesimista/base/optimista) en vez de un único punto,
porque el resultado es muy sensible a la tasa de crecimiento y al WACC — un
solo número exacto transmite una falsa precisión. El "valor esperado" es un
promedio ponderado de los 3, no una verdad objetiva: los pesos (20/50/30) y
los rangos de variación son una elección nuestra, documentada como tal.
"""

from dataclasses import dataclass

from src.config import TERMINAL_GROWTH_RATE

GROWTH_CAP = (-0.05, 0.20)


@dataclass
class DCFResult:
    fair_value_per_share: float
    enterprise_value: float
    equity_value: float
    growth_rate_used: float
    wacc_used: float
    projected_fcf: list[float]


@dataclass
class DCFScenarios:
    pessimistic: DCFResult
    base: DCFResult
    optimistic: DCFResult
    weights: tuple[float, float, float]  # (pesimista, base, optimista)

    @property
    def fair_value_per_share(self) -> float:
        p, b, o = self.weights
        return (
            self.pessimistic.fair_value_per_share * p
            + self.base.fair_value_per_share * b
            + self.optimistic.fair_value_per_share * o
        )

    @property
    def wacc_used(self) -> float:
        return self.base.wacc_used

    @property
    def growth_rate_used(self) -> float:
        return self.base.growth_rate_used


def estimate_wacc(beta: float, risk_free_rate: float, equity_risk_premium: float) -> float:
    """CAPM simplificado (asume financiamiento 100% equity, razonable para MVP)."""
    beta = beta if beta and beta > 0 else 1.0
    return risk_free_rate + beta * equity_risk_premium


def estimate_growth_rate(historical_fcf: list[float], cap: tuple[float, float] = GROWTH_CAP) -> float:
    """CAGR de FCF sobre los años disponibles, acotado a un rango razonable."""
    series = [v for v in historical_fcf if v is not None]
    if len(series) < 2 or series[-1] <= 0:
        return 0.05  # fallback conservador si no hay historia confiable
    years = len(series) - 1
    cagr = (series[0] / series[-1]) ** (1 / years) - 1
    return max(cap[0], min(cap[1], cagr))


def run_dcf(
    historical_fcf: list[float],
    shares_outstanding: float,
    net_debt: float,
    wacc: float,
    projection_years: int = 5,
    terminal_growth: float = TERMINAL_GROWTH_RATE,
    growth_rate: float | None = None,
) -> DCFResult:
    if not historical_fcf or historical_fcf[0] is None:
        raise ValueError("No hay Free Cash Flow reciente para correr el DCF")
    if wacc <= terminal_growth:
        raise ValueError("WACC debe ser mayor que el crecimiento terminal")

    # promedio de los últimos años (no solo el último) para no partir de un año
    # anómalo por un pico puntual de capex (p. ej. inversión fuerte en IA de un solo año)
    recent = [v for v in historical_fcf[:3] if v is not None]
    base_fcf = sum(recent) / len(recent)
    g0 = growth_rate if growth_rate is not None else estimate_growth_rate(historical_fcf)

    projected = []
    fcf = base_fcf
    for year in range(1, projection_years + 1):
        # decae linealmente desde g0 hasta el crecimiento terminal
        g = g0 + (terminal_growth - g0) * (year - 1) / max(1, projection_years - 1)
        fcf = fcf * (1 + g)
        projected.append(fcf)

    discounted = [cf / (1 + wacc) ** t for t, cf in enumerate(projected, start=1)]

    terminal_value = projected[-1] * (1 + terminal_growth) / (wacc - terminal_growth)
    discounted_terminal = terminal_value / (1 + wacc) ** projection_years

    enterprise_value = sum(discounted) + discounted_terminal
    equity_value = enterprise_value - net_debt
    fair_value_per_share = equity_value / shares_outstanding if shares_outstanding else 0.0

    return DCFResult(
        fair_value_per_share=fair_value_per_share,
        enterprise_value=enterprise_value,
        equity_value=equity_value,
        growth_rate_used=g0,
        wacc_used=wacc,
        projected_fcf=projected,
    )


def run_dcf_scenarios(
    historical_fcf: list[float],
    shares_outstanding: float,
    net_debt: float,
    wacc: float,
    projection_years: int = 5,
    terminal_growth: float = TERMINAL_GROWTH_RATE,
    growth_rate: float | None = None,
    growth_delta: float = 0.03,   # +/- 3 puntos porcentuales de crecimiento entre escenarios
    wacc_delta: float = 0.015,    # +/- 1.5 puntos porcentuales de WACC entre escenarios
    weights: tuple[float, float, float] = (0.20, 0.50, 0.30),
) -> DCFScenarios:
    base_growth = growth_rate if growth_rate is not None else estimate_growth_rate(historical_fcf)

    base = run_dcf(
        historical_fcf, shares_outstanding, net_debt, wacc, projection_years, terminal_growth,
        growth_rate=base_growth,
    )

    pess_growth = max(GROWTH_CAP[0], base_growth - growth_delta)
    pess_wacc = max(wacc + wacc_delta, terminal_growth + 0.01)
    pessimistic = run_dcf(
        historical_fcf, shares_outstanding, net_debt, pess_wacc, projection_years, terminal_growth,
        growth_rate=pess_growth,
    )

    opt_growth = min(GROWTH_CAP[1], base_growth + growth_delta)
    opt_wacc = max(wacc - wacc_delta, terminal_growth + 0.01)
    optimistic = run_dcf(
        historical_fcf, shares_outstanding, net_debt, opt_wacc, projection_years, terminal_growth,
        growth_rate=opt_growth,
    )

    return DCFScenarios(pessimistic=pessimistic, base=base, optimistic=optimistic, weights=weights)


@dataclass
class DCFSensitivity:
    """Mueve UNA variable a la vez (a diferencia de los escenarios, que mueven crecimiento y WACC
    juntos) para ver cuál de las dos supuestos domina el resultado — estándar en cualquier modelo
    de DCF profesional ('tornado chart')."""

    fv_growth_low: float   # fair value con el crecimiento en su punto bajo (WACC fijo en el base)
    fv_growth_high: float  # fair value con el crecimiento en su punto alto (WACC fijo en el base)
    fv_wacc_low: float     # fair value con el WACC en su punto bajo -> mayor valor (crecimiento fijo)
    fv_wacc_high: float    # fair value con el WACC en su punto alto -> menor valor (crecimiento fijo)

    @property
    def growth_swing(self) -> float:
        return self.fv_growth_high - self.fv_growth_low

    @property
    def wacc_swing(self) -> float:
        return self.fv_wacc_low - self.fv_wacc_high

    @property
    def dominant_driver(self) -> str:
        return "el crecimiento" if self.growth_swing >= self.wacc_swing else "el WACC"


def run_dcf_sensitivity(
    historical_fcf: list[float],
    shares_outstanding: float,
    net_debt: float,
    wacc: float,
    projection_years: int = 5,
    terminal_growth: float = TERMINAL_GROWTH_RATE,
    growth_rate: float | None = None,
    growth_delta: float = 0.03,
    wacc_delta: float = 0.015,
) -> DCFSensitivity:
    base_growth = growth_rate if growth_rate is not None else estimate_growth_rate(historical_fcf)

    g_low = max(GROWTH_CAP[0], base_growth - growth_delta)
    g_high = min(GROWTH_CAP[1], base_growth + growth_delta)
    fv_growth_low = run_dcf(
        historical_fcf, shares_outstanding, net_debt, wacc, projection_years, terminal_growth, growth_rate=g_low
    ).fair_value_per_share
    fv_growth_high = run_dcf(
        historical_fcf, shares_outstanding, net_debt, wacc, projection_years, terminal_growth, growth_rate=g_high
    ).fair_value_per_share

    w_low = max(wacc - wacc_delta, terminal_growth + 0.01)
    w_high = max(wacc + wacc_delta, terminal_growth + 0.01)
    fv_wacc_low = run_dcf(
        historical_fcf, shares_outstanding, net_debt, w_low, projection_years, terminal_growth, growth_rate=base_growth
    ).fair_value_per_share
    fv_wacc_high = run_dcf(
        historical_fcf, shares_outstanding, net_debt, w_high, projection_years, terminal_growth, growth_rate=base_growth
    ).fair_value_per_share

    return DCFSensitivity(
        fv_growth_low=fv_growth_low,
        fv_growth_high=fv_growth_high,
        fv_wacc_low=fv_wacc_low,
        fv_wacc_high=fv_wacc_high,
    )
