"""Filtro de solvencia: ¿puede la empresa pagar su deuda?

    Cobertura de intereses = EBIT / gasto financiero
    Deuda / EBITDA = deuda total / EBITDA

No mide si la acción está barata o cara — mide riesgo de apalancamiento. Regla de bolsillo
usada por la mayoría de analistas de crédito: cobertura < 3x o Deuda/EBITDA > 4x es señal de
alerta. Bancos, aseguradoras y REITs son estructuralmente distintos (viven de apalancarse) y
esta regla no aplica bien ahí — pero ninguno de nuestros tickers actuales es de ese tipo.
"""

from dataclasses import dataclass

COVERAGE_ALERT_THRESHOLD = 3.0
DEBT_TO_EBITDA_ALERT_THRESHOLD = 4.0


@dataclass
class SolvencyResult:
    interest_coverage: float | None  # EBIT / gasto financiero; None si no hay deuda con intereses
    debt_to_ebitda: float | None
    risk_level: str  # "Bajo" / "Moderado" / "Alto" / "No aplica"


def evaluate_solvency(
    ebit: float | None,
    interest_expense: float | None,
    total_debt: float | None,
    ebitda: float | None,
) -> SolvencyResult:
    interest_coverage = None
    if ebit is not None and interest_expense:
        interest_coverage = ebit / abs(interest_expense)

    debt_to_ebitda = None
    if total_debt is not None and ebitda and ebitda > 0:
        debt_to_ebitda = total_debt / ebitda

    if interest_coverage is None and debt_to_ebitda is None:
        return SolvencyResult(interest_coverage=None, debt_to_ebitda=None, risk_level="No aplica")

    checks = flags = 0
    if interest_coverage is not None:
        checks += 1
        if interest_coverage < COVERAGE_ALERT_THRESHOLD:
            flags += 1
    if debt_to_ebitda is not None:
        checks += 1
        if debt_to_ebitda > DEBT_TO_EBITDA_ALERT_THRESHOLD:
            flags += 1

    if flags == 0:
        risk_level = "Bajo"
    elif flags < checks:
        risk_level = "Moderado"
    else:
        risk_level = "Alto"

    return SolvencyResult(interest_coverage=interest_coverage, debt_to_ebitda=debt_to_ebitda, risk_level=risk_level)
