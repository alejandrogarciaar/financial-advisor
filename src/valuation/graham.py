"""Número de Graham: valor intrínseco conservador de Benjamin Graham.

    Valor = √(22.5 × EPS × BVPS)

22.5 = P/E "normal" de 15 × P/B "normal" de 1.5, los múltiplos que Graham
consideraba razonables para un inversor defensivo. Usa solo datos ya
reportados (EPS y valor en libros por acción), sin proyectar nada a futuro.
"""

import math
from dataclasses import dataclass


@dataclass
class GrahamResult:
    fair_value: float


def evaluate_graham(eps: float, book_value_per_share: float) -> GrahamResult:
    if eps <= 0 or book_value_per_share <= 0:
        raise ValueError("El número de Graham requiere EPS y valor en libros positivos")
    return GrahamResult(fair_value=math.sqrt(22.5 * eps * book_value_per_share))
