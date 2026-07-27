"""Filtro de calidad: ROIC vs. WACC — no mide precio, mide si la empresa crea valor.

    NOPAT = EBIT × (1 - tasa de impuesto efectiva)
    ROIC  = NOPAT / Capital Invertido
    Capital Invertido ≈ Deuda total + Patrimonio - Caja

Si ROIC > WACC, cada dólar que la empresa reinvierte vale más de lo que
cuesta financiarlo — crea valor. Si ROIC < WACC, lo destruye, sin importar
qué tan barata se vea la acción por otros métodos. Es el filtro que Buffett
aplica antes de mirar el precio.

También calculamos la TENDENCIA del ROIC en los últimos años (no solo el nivel actual):
una empresa con ROIC 20% en caída sostenida cuenta una historia distinta a una con ROIC
20% que viene mejorando, aunque el filtro binario "crea/destruye valor" sea igual hoy.
"""

from dataclasses import dataclass, field

DEFAULT_TAX_RATE = 0.21  # tasa corporativa estatutaria en EE. UU., usada como respaldo
TREND_THRESHOLD = 0.03   # 3 puntos porcentuales de diferencia para considerar mejora/deterioro


@dataclass
class QualityResult:
    roic: float
    wacc: float
    creates_value: bool
    roic_trend: str = "Sin suficiente historia"  # "Mejorando" / "Deteriorándose" / "Estable"
    roic_series: list[float] = field(default_factory=list)  # más reciente primero


def estimate_effective_tax_rate(income_tax_expense: float | None, pretax_income: float | None) -> float:
    if not pretax_income or pretax_income <= 0 or income_tax_expense is None:
        return DEFAULT_TAX_RATE
    return max(0.0, min(0.50, income_tax_expense / pretax_income))


def compute_roic_series(income_statements: list[dict], balance_sheets: list[dict]) -> list[float]:
    """ROIC año por año (más reciente primero), usando el mismo criterio que evaluate_quality
    pero repetido para cada año fiscal disponible en vez de solo el más reciente."""
    series = []
    for inc, bs in zip(income_statements, balance_sheets):
        ebit = inc.get("ebit")
        if ebit is None:
            continue
        tax_rate = estimate_effective_tax_rate(inc.get("incomeTaxExpense"), inc.get("incomeBeforeTax"))
        total_debt = bs.get("totalDebt") or 0
        total_equity = bs.get("totalStockholdersEquity") or 0
        cash = bs.get("cashAndCashEquivalents") or 0
        invested_capital = total_debt + total_equity - cash
        if invested_capital <= 0:
            continue
        series.append((ebit * (1 - tax_rate)) / invested_capital)
    return series


def classify_roic_trend(roic_series: list[float]) -> str:
    if len(roic_series) < 2:
        return "Sin suficiente historia"
    diff = roic_series[0] - roic_series[-1]  # más reciente - más viejo
    if diff >= TREND_THRESHOLD:
        return "Mejorando"
    if diff <= -TREND_THRESHOLD:
        return "Deteriorándose"
    return "Estable"


def evaluate_quality(
    ebit: float,
    tax_rate: float,
    invested_capital: float,
    wacc: float,
    roic_series: list[float] | None = None,
) -> QualityResult:
    if not invested_capital or invested_capital <= 0:
        raise ValueError("No hay capital invertido positivo para calcular ROIC")
    nopat = ebit * (1 - tax_rate)
    roic = nopat / invested_capital
    series = roic_series or []
    return QualityResult(
        roic=roic,
        wacc=wacc,
        creates_value=roic > wacc,
        roic_trend=classify_roic_trend(series),
        roic_series=series,
    )
