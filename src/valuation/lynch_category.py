"""Clasificación simple al estilo de las categorías de Peter Lynch (One Up on Wall Street).

Lynch reservaba el PEG (y por extensión, cualquier fórmula de "múltiplo justo = función del
crecimiento") para sus "fast growers". Para cíclicas o negocios de ganancias inestables usaba
otro criterio, no el PEG. Esto NO es una clasificación cualitativa real del negocio (eso
requeriría criterio humano) — es una heurística basada solo en la tasa de crecimiento de EPS
y el beta, pensada para avisar cuándo las señales de crecimiento (PEG, Graham-growth) están
siendo aplicadas fuera del dominio donde Lynch decía que funcionan.
"""

from dataclasses import dataclass


@dataclass
class LynchCategory:
    label: str
    growth_methods_appropriate: bool
    note: str


def classify_lynch_category(eps_growth_rate: float | None, beta: float | None) -> LynchCategory:
    high_beta = (beta or 1.0) >= 1.5

    if eps_growth_rate is None:
        return LynchCategory(
            label="Cíclica / impredecible",
            growth_methods_appropriate=False,
            note="Ganancias inestables o en declive — Lynch no usaría el PEG aquí.",
        )
    if eps_growth_rate >= 0.20:
        return LynchCategory(
            label="Fast grower",
            growth_methods_appropriate=True,
            note="Crecimiento alto y sostenido — el caso de uso original del PEG de Lynch.",
        )
    if eps_growth_rate >= 0.10:
        if high_beta:
            return LynchCategory(
                label="Cíclica de alto crecimiento",
                growth_methods_appropriate=False,
                note="Crecimiento sólido pero con alta volatilidad — Lynch sería cauteloso con el PEG aquí.",
            )
        return LynchCategory(
            label="Growth stalwart",
            growth_methods_appropriate=True,
            note="Crecimiento sólido y estable — el PEG debería ser razonablemente confiable.",
        )
    if eps_growth_rate >= 0.0:
        return LynchCategory(
            label="Stalwart maduro",
            growth_methods_appropriate=True,
            note="Crecimiento moderado — el PEG aplica, pero con menos margen de error que en una fast grower.",
        )
    return LynchCategory(
        label="Cíclica / en declive",
        growth_methods_appropriate=False,
        note="Ganancias en caída — Lynch usaría valor patrimonial o DCF conservador, no el PEG.",
    )
