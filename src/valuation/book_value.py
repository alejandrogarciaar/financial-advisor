"""Valor Patrimonial (Book Value): el método más conservador.

La lógica: si vendieras todos los activos de la empresa y pagaras todas sus
deudas, ¿cuánto quedaría por acción? Graham lo usaba como piso — comprar por
debajo del valor en libros es comprar activos con descuento.
"""

from dataclasses import dataclass


@dataclass
class BookValueResult:
    book_value_per_share: float
    total_equity: float


def evaluate_book_value(total_equity: float, shares_outstanding: float) -> BookValueResult:
    if not shares_outstanding:
        raise ValueError("No hay acciones en circulación para calcular el valor patrimonial")
    return BookValueResult(
        book_value_per_share=total_equity / shares_outstanding,
        total_equity=total_equity,
    )
