"""
crecetrader.py
==============

Reconstruccion del algoritmo de "niveles calculados" del canal Crecetrader,
obtenida por ingenieria inversa de sus graficos publicos (BTC y ETH,
27-28 agosto 2026).

El sistema tiene tres capas independientes que conviven en el grafico:

    1. ENVOLVENTE DE SESION  (intradia, cualquier temporalidad < 1D)
       centro  = apertura diaria (00:00 UTC)
       anillos = centro * (1 +/- 0.382% / 1% / 1.5% / 2%)
       Verificada al dolar en 15 niveles, dos jornadas, dos activos.

    2. REJILLA DIARIA
       nivel = ancla + n * 25% * rango_base   (con medio paso en 62.5%)
       ancla = minimo del ultimo ano; rango_base = primer impulso desde ahi.
       Verificada en 7 niveles, incluida una prediccion algebraica (125%).

    3. FRACCIONES MACRO (semanal)
       nivel = ancla + n * 12.5% * caida_macro
       caida_macro = techo de ciclo - ancla.  Verificada en 1 nivel.

Una cuarta capa (pivots trazados a mano) no es algoritmizable y queda fuera.

AVISO
-----
Este modulo reproduce COMO se generan los niveles; no implica que tengan
poder predictivo. Una rejilla densa "acierta" toques por construccion.
No es asesoramiento de inversion.

Sin dependencias externas. Python 3.9+.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable, Optional, Sequence

__all__ = [
    "Role",
    "Level",
    "session_envelope",
    "daily_grid",
    "macro_grid",
    "LevelEngine",
    "nearest_levels",
    "format_levels",
]

# ---------------------------------------------------------------- constantes --

#: Anillos de la envolvente de sesion, en porcentaje sobre el centro.
ENVELOPE_BANDS: tuple[float, ...] = (0.382, 1.0, 1.5, 2.0)

#: Pasos de la rejilla diaria, en porcentaje del rango base.
DAILY_STEPS: tuple[float, ...] = (
    0.0, 25.0, 50.0, 62.5, 75.0, 100.0, 125.0, 150.0, 175.0, 200.0, 225.0, 250.0, 275.0,
)

#: Pasos verificados contra sus graficos publicos.
DAILY_VERIFIED: frozenset[float] = frozenset({62.5, 125.0, 175.0, 225.0, 250.0, 275.0})

#: Fracciones de la caida macro, en porcentaje.
MACRO_STEPS: tuple[float, ...] = tuple(i * 12.5 for i in range(9))

#: Calibracion de respaldo (grafico BTC 1D del 28-ago-2026).
#: rango_base = 18294 sobre un ancla de 57670.
FALLBACK_BASE_RATIO: float = 18294 / 57670

#: caida_macro = 68537 sobre la misma ancla.
FALLBACK_MACRO_RATIO: float = 68537 / 57670


class Role(str, Enum):
    """Rol del nivel dentro del metodo. NO es una senal de trading."""

    BUY_ZONE = "zona_compra"
    SELL_ZONE = "zona_venta"
    NEUTRAL = "neutro"


@dataclass(frozen=True)
class Level:
    """Un nivel calculado.

    Attributes:
        price: precio del nivel.
        label: identificador corto ("125%", "+1%", "centro").
        layer: capa que lo genero ("envelope" | "daily" | "macro").
        pct: porcentaje aplicado en la formula.
        role: rol operativo dentro del metodo.
        verified: True si el nivel fue confirmado contra un grafico publico.
        note: descripcion legible.
    """

    price: float
    label: str
    layer: str
    pct: float
    role: Role = Role.NEUTRAL
    verified: bool = False
    note: str = ""

    def distance_pct(self, reference: float) -> float:
        """Distancia porcentual del nivel respecto a un precio de referencia."""
        if reference == 0:
            raise ValueError("El precio de referencia no puede ser cero.")
        return (self.price - reference) / reference * 100.0


# ------------------------------------------------------------------- capa 1 --


def session_envelope(
    center: float,
    *,
    bands: Sequence[float] = ENVELOPE_BANDS,
    reference: Optional[float] = None,
) -> list[Level]:
    """Capa 1: envolvente de sesion alrededor de la apertura diaria.

    Args:
        center: apertura diaria (00:00 UTC) del dia en curso. Si no se dispone,
            el cierre diario anterior es la mejor aproximacion.
        bands: anillos en porcentaje.
        reference: precio actual usado para asignar el rol de cada anillo.
            Si es None se usa el propio centro.

    Returns:
        Lista de niveles ordenada de mayor a menor precio.

    Raises:
        ValueError: si el centro no es positivo.
    """
    if center <= 0:
        raise ValueError("El centro de la envolvente debe ser positivo.")

    ref = center if reference is None else reference
    levels = [
        Level(
            price=center,
            label="centro",
            layer="envelope",
            pct=0.0,
            role=Role.NEUTRAL,
            verified=True,
            note="apertura diaria (00:00 UTC)",
        )
    ]

    for band in bands:
        for sign in (1, -1):
            price = center * (1 + sign * band / 100.0)
            levels.append(
                Level(
                    price=price,
                    label=f"{'+' if sign > 0 else '-'}{band}%",
                    layer="envelope",
                    pct=band * sign,
                    role=Role.SELL_ZONE if price > ref else Role.BUY_ZONE,
                    verified=True,
                    note="anillo Fibonacci" if band == 0.382 else "anillo de sesion",
                )
            )

    return sorted(levels, key=lambda lv: lv.price, reverse=True)


# ------------------------------------------------------------------- capa 2 --


def daily_grid(
    anchor: float,
    base_range: Optional[float] = None,
    *,
    steps: Sequence[float] = DAILY_STEPS,
) -> list[Level]:
    """Capa 2: rejilla diaria anclada al minimo anual.

    Args:
        anchor: minimo del ultimo ano (la "onda V" en su nomenclatura).
        base_range: amplitud del primer impulso desde el ancla. Si es None se
            estima con la proporcion calibrada (~31.7% del ancla).
        steps: pasos porcentuales del rango base.

    Returns:
        Lista de niveles ordenada de mayor a menor precio.

    Raises:
        ValueError: si el ancla no es positiva o el rango base no es positivo.
    """
    if anchor <= 0:
        raise ValueError("El ancla debe ser positiva.")

    rng = anchor * FALLBACK_BASE_RATIO if base_range is None else base_range
    if rng <= 0:
        raise ValueError("El rango base debe ser positivo.")

    levels: list[Level] = []
    for pct in steps:
        if pct <= 75.0:
            role, note = Role.BUY_ZONE, "refugio"
        elif pct >= 125.0:
            role, note = Role.SELL_ZONE, "objetivo"
        else:
            role, note = Role.NEUTRAL, "extremo de la Fase 1"

        if pct == 125.0:
            note = "objetivo estrella"
        elif pct == 0.0:
            note = "ancla - minimo anual"

        levels.append(
            Level(
                price=anchor + rng * pct / 100.0,
                label=f"{pct:g}%",
                layer="daily",
                pct=pct,
                role=role,
                verified=pct in DAILY_VERIFIED,
                note=note,
            )
        )

    return sorted(levels, key=lambda lv: lv.price, reverse=True)


# ------------------------------------------------------------------- capa 3 --


def macro_grid(
    anchor: float,
    macro_range: Optional[float] = None,
    *,
    steps: Sequence[float] = MACRO_STEPS,
    reference: Optional[float] = None,
) -> list[Level]:
    """Capa 3: fracciones de 12.5% de la caida macro.

    Args:
        anchor: minimo del ultimo ano.
        macro_range: techo de ciclo menos ancla. Si es None se estima con la
            proporcion calibrada (~118.9% del ancla).
        steps: fracciones porcentuales.
        reference: precio actual para asignar roles.

    Returns:
        Lista de niveles ordenada de mayor a menor precio.
    """
    if anchor <= 0:
        raise ValueError("El ancla debe ser positiva.")

    rng = anchor * FALLBACK_MACRO_RATIO if macro_range is None else macro_range
    if rng <= 0:
        raise ValueError("El rango macro debe ser positivo.")

    ref = anchor if reference is None else reference
    levels: list[Level] = []
    for pct in steps:
        price = anchor + rng * pct / 100.0
        if price < ref * 0.995:
            role = Role.BUY_ZONE
        elif price > ref * 1.005:
            role = Role.SELL_ZONE
        else:
            role = Role.NEUTRAL
        levels.append(
            Level(
                price=price,
                label=f"{pct:g}%",
                layer="macro",
                pct=pct,
                role=role,
                verified=pct == 37.5,
                note=f"fraccion {pct / 12.5:.0f}/8 de la caida macro",
            )
        )

    return sorted(levels, key=lambda lv: lv.price, reverse=True)


# ---------------------------------------------------------------- utilidades --


def nearest_levels(
    levels: Iterable[Level], price: float
) -> tuple[Optional[Level], Optional[Level]]:
    """Devuelve (soporte, resistencia) mas proximos a un precio.

    Args:
        levels: niveles a evaluar.
        price: precio de referencia.

    Returns:
        Tupla (nivel inmediatamente inferior, nivel inmediatamente superior).
        Cualquiera puede ser None si el precio queda fuera de la rejilla.
    """
    below = above = None
    for lv in levels:
        if lv.price <= price and (below is None or lv.price > below.price):
            below = lv
        if lv.price >= price and (above is None or lv.price < above.price):
            above = lv
    return below, above


def format_levels(levels: Sequence[Level], price: Optional[float] = None) -> str:
    """Renderiza los niveles como tabla de texto monoespaciada."""
    rows = ["  nivel      precio      rol            dist.   nota"]
    for lv in levels:
        dist = f"{lv.distance_pct(price):+7.2f}%" if price else "       -"
        mark = "*" if lv.verified else " "
        rows.append(
            f"{mark} {lv.label:<9} {lv.price:>10,.0f}  {lv.role.value:<14} {dist}  {lv.note}"
        )
    if price is not None:
        rows.append(f"\n  precio actual: {price:,.0f}   (* = nivel verificado)")
    return "\n".join(rows)


# -------------------------------------------------------------------- motor --


@dataclass
class LevelEngine:
    """Motor que combina las tres capas para un activo.

    Los parametros son independientes del instrumento: el mismo motor sirve
    para BTC, ETH o cualquier otro simbolo, solo cambian las entradas.

    Attributes:
        price: precio actual.
        daily_open: apertura diaria del dia en curso.
        year_low: minimo del ultimo ano (ancla de las capas 2 y 3).
        base_range: amplitud del primer impulso desde el ancla.
        macro_range: techo de ciclo menos ancla.
    """

    price: float
    daily_open: Optional[float] = None
    year_low: Optional[float] = None
    base_range: Optional[float] = None
    macro_range: Optional[float] = None
    _cache: dict = field(default_factory=dict, repr=False, compare=False)

    def envelope(self) -> list[Level]:
        """Capa 1. Usa el precio actual como centro si falta la apertura."""
        center = self.daily_open if self.daily_open else self.price
        return session_envelope(center, reference=self.price)

    def grid(self) -> list[Level]:
        """Capa 2. Requiere `year_low`."""
        if not self.year_low:
            raise ValueError("Se requiere year_low para la rejilla diaria.")
        return daily_grid(self.year_low, self.base_range)

    def macro(self) -> list[Level]:
        """Capa 3. Requiere `year_low`."""
        if not self.year_low:
            raise ValueError("Se requiere year_low para las fracciones macro.")
        return macro_grid(self.year_low, self.macro_range, reference=self.price)

    def all_levels(self) -> list[Level]:
        """Las tres capas juntas, ordenadas por precio."""
        out = list(self.envelope())
        if self.year_low:
            out += self.grid() + self.macro()
        return sorted(out, key=lambda lv: lv.price, reverse=True)

    def confluences(self, tolerance_pct: float = 0.15) -> list[tuple[Level, Level]]:
        """Detecta niveles de capas distintas que casi coinciden.

        En el metodo original, cuando una rejilla nueva reproduce un nivel de
        otra capa, ese precio se trata como critico.

        Args:
            tolerance_pct: distancia maxima entre dos niveles, en porcentaje.

        Returns:
            Pares de niveles en confluencia.
        """
        levels = self.all_levels()
        pairs: list[tuple[Level, Level]] = []
        for i, a in enumerate(levels):
            for b in levels[i + 1 :]:
                if a.layer == b.layer:
                    continue
                if abs(a.price - b.price) / a.price * 100.0 <= tolerance_pct:
                    pairs.append((a, b))
        return pairs


# ------------------------------------------------------------------ ejemplo --

if __name__ == "__main__":
    # Datos reales del grafico BTC del 28-ago-2026 (validacion del modulo).
    engine = LevelEngine(
        price=79_744,
        daily_open=80_279,
        year_low=57_670,
        base_range=18_294,
        macro_range=68_537,
    )

    print("=" * 68)
    print("CAPA 1 - envolvente de sesion")
    print("=" * 68)
    print(format_levels(engine.envelope(), engine.price))

    print("\n" + "=" * 68)
    print("CAPA 2 - rejilla diaria")
    print("=" * 68)
    print(format_levels(engine.grid(), engine.price))

    below, above = nearest_levels(engine.all_levels(), engine.price)
    print(f"\nsoporte mas proximo:     {below.price:,.0f}  ({below.layer} {below.label})")
    print(f"resistencia mas proxima: {above.price:,.0f}  ({above.layer} {above.label})")

    print("\nconfluencias entre capas:")
    for a, b in engine.confluences():
        print(f"  {a.price:,.0f}  {a.layer} {a.label}  ~  {b.layer} {b.label}")

    # Comprobacion contra los valores leidos en sus graficos.
    expected = {80_586, 79_972, 81_082, 79_476, 81_483, 79_075, 81_885, 78_673}
    produced = {round(lv.price) for lv in engine.envelope()}
    print(f"\nanillos verificados reproducidos: {len(expected & produced)}/{len(expected)}")
