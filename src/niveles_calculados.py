"""
niveles_calculados.py
==============

Reconstruccion del algoritmo de "niveles calculados" del canal Crecetrader,
obtenida por ingenieria inversa de sus graficos publicos (BTC y ETH,
27-29 agosto 2026; el 29-ago la envolvente PREDIJO los 7 anillos publicados
antes del video, error max $1).

El sistema tiene tres capas independientes que conviven en el grafico:

    1. ENVOLVENTE DE SESION  (intradia, cualquier temporalidad < 1D)
       centro  = apertura diaria (00:00 UTC)
       anillos = centro * (1 +/- 0.382% / 1% / 1.5% / 2%)

    2. REJILLA DIARIA
       nivel = ancla + n * 25% * rango_base   (con medio paso en 62.5%)
       ancla = minimo del ultimo ano; rango_base = primer impulso desde ahi.

    3. FRACCIONES MACRO (semanal)
       nivel = ancla + n * 12.5% * caida_macro
       caida_macro = techo de ciclo - ancla.

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

ENVELOPE_BANDS: tuple[float, ...] = (0.382, 1.0, 1.5, 2.0)

DAILY_STEPS: tuple[float, ...] = (
    0.0, 25.0, 50.0, 62.5, 75.0, 100.0, 125.0, 150.0, 175.0, 200.0, 225.0, 250.0, 275.0,
)

DAILY_VERIFIED: frozenset[float] = frozenset({62.5, 125.0, 175.0, 225.0, 250.0, 275.0})

MACRO_STEPS: tuple[float, ...] = tuple(i * 12.5 for i in range(9))

# Calibracion de respaldo (grafico BTC 1D del 28-ago-2026).
FALLBACK_BASE_RATIO: float = 18294 / 57670
FALLBACK_MACRO_RATIO: float = 68537 / 57670


class Role(str, Enum):
    """Rol del nivel dentro del metodo. NO es una senal de trading."""

    BUY_ZONE = "zona_compra"
    SELL_ZONE = "zona_venta"
    NEUTRAL = "neutro"


@dataclass(frozen=True)
class Level:
    """Un nivel calculado."""

    price: float
    label: str
    layer: str
    pct: float
    role: Role = Role.NEUTRAL
    verified: bool = False
    note: str = ""

    def distance_pct(self, reference: float) -> float:
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
    """Capa 1: envolvente de sesion alrededor de la apertura diaria."""
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
    """Capa 2: rejilla diaria anclada al minimo anual."""
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
    """Capa 3: fracciones de 12.5% de la caida macro."""
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
    """Devuelve (soporte, resistencia) mas proximos a un precio."""
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
    """Motor que combina las tres capas para un activo."""

    price: float
    daily_open: Optional[float] = None
    year_low: Optional[float] = None
    base_range: Optional[float] = None
    macro_range: Optional[float] = None
    _cache: dict = field(default_factory=dict, repr=False, compare=False)

    def envelope(self) -> list[Level]:
        center = self.daily_open if self.daily_open else self.price
        return session_envelope(center, reference=self.price)

    def grid(self) -> list[Level]:
        if not self.year_low:
            raise ValueError("Se requiere year_low para la rejilla diaria.")
        return daily_grid(self.year_low, self.base_range)

    def macro(self) -> list[Level]:
        if not self.year_low:
            raise ValueError("Se requiere year_low para las fracciones macro.")
        return macro_grid(self.year_low, self.macro_range, reference=self.price)

    def all_levels(self) -> list[Level]:
        out = list(self.envelope())
        if self.year_low:
            out += self.grid() + self.macro()
        return sorted(out, key=lambda lv: lv.price, reverse=True)

    def confluences(self, tolerance_pct: float = 0.15) -> list[tuple[Level, Level]]:
        """Niveles de capas distintas que casi coinciden (niveles criticos)."""
        levels = self.all_levels()
        pairs: list[tuple[Level, Level]] = []
        for i, a in enumerate(levels):
            for b in levels[i + 1 :]:
                if a.layer == b.layer:
                    continue
                if abs(a.price - b.price) / a.price * 100.0 <= tolerance_pct:
                    pairs.append((a, b))
        return pairs


# ============================================================================
#  CONFIRMACIONES DE COMPRA / VENTA
# ============================================================================


class SignalType(str, Enum):
    """Tipo de confirmacion detectada."""

    REBOTE_COMPRA = "rebote_compra"
    REBOTE_VENTA = "rebote_venta"
    RUPTURA_ALCISTA = "ruptura_alcista"
    RUPTURA_BAJISTA = "ruptura_bajista"
    RETEST_ALCISTA = "retest_alcista"
    RETEST_BAJISTA = "retest_bajista"


@dataclass(frozen=True)
class Candle:
    """Una vela OHLC. `time` es libre (timestamp, indice, fecha-str...)."""

    time: object
    open: float
    high: float
    low: float
    close: float


@dataclass(frozen=True)
class Signal:
    """Una confirmacion detectada sobre un nivel."""

    time: object
    type: SignalType
    level: Level
    candle: Candle

    @property
    def is_buy(self) -> bool:
        return self.type in (
            SignalType.REBOTE_COMPRA,
            SignalType.RUPTURA_ALCISTA,
            SignalType.RETEST_ALCISTA,
        )

    def __str__(self) -> str:
        rol = "COMPRA" if self.is_buy else "VENTA"
        return (
            f"[{self.time}] {rol:6} {self.type.value:16} "
            f"nivel {self.level.layer}/{self.level.label} @ {self.level.price:,.0f} "
            f"(cierre vela: {self.candle.close:,.0f})"
        )


def _touches(candle: Candle, price: float, tolerance_pct: float) -> bool:
    band = price * tolerance_pct / 100.0
    return candle.low - band <= price <= candle.high + band


def confirm_rebote(
    candles: Sequence[Candle], level: Level, *, wick_tolerance_pct: float = 0.05
) -> list[Signal]:
    """Rebote: mecha toca el nivel, cierre se aleja en contra. Recomendado."""
    out: list[Signal] = []
    for c in candles:
        if not _touches(c, level.price, wick_tolerance_pct):
            continue
        if c.low <= level.price and c.close > level.price:
            out.append(Signal(c.time, SignalType.REBOTE_COMPRA, level, c))
        elif c.high >= level.price and c.close < level.price:
            out.append(Signal(c.time, SignalType.REBOTE_VENTA, level, c))
    return out


def confirm_ruptura(candles: Sequence[Candle], level: Level) -> list[Signal]:
    """Ruptura: el CIERRE cruza el nivel respecto a la vela anterior."""
    out: list[Signal] = []
    for prev, cur in zip(candles, candles[1:]):
        if prev.close <= level.price < cur.close:
            out.append(Signal(cur.time, SignalType.RUPTURA_ALCISTA, level, cur))
        elif prev.close >= level.price > cur.close:
            out.append(Signal(cur.time, SignalType.RUPTURA_BAJISTA, level, cur))
    return out


def confirm_retest(
    candles: Sequence[Candle], level: Level, *, retest_tolerance_pct: float = 0.1
) -> list[Signal]:
    """Retest: tras una ruptura, el precio vuelve al nivel sin recruzarlo."""
    rupturas = confirm_ruptura(candles, level)
    if not rupturas:
        return []

    out: list[Signal] = []
    idx_by_time = {c.time: i for i, c in enumerate(candles)}
    for r in rupturas:
        start = idx_by_time[r.time] + 1
        for c in candles[start:]:
            invalidated = (
                r.type == SignalType.RUPTURA_ALCISTA and c.close < level.price
            ) or (r.type == SignalType.RUPTURA_BAJISTA and c.close > level.price)
            if invalidated:
                break
            if _touches(c, level.price, retest_tolerance_pct):
                sig_type = (
                    SignalType.RETEST_ALCISTA
                    if r.type == SignalType.RUPTURA_ALCISTA
                    else SignalType.RETEST_BAJISTA
                )
                out.append(Signal(c.time, sig_type, level, c))
                break
    return out


def detect_signals(
    candles: Sequence[Candle],
    levels: Iterable[Level],
    *,
    mode: str = "rebote",
    wick_tolerance_pct: float = 0.05,
    retest_tolerance_pct: float = 0.1,
) -> list[Signal]:
    """Punto de entrada unico: confirmaciones para una lista de niveles.

    mode: "rebote" (defecto, recomendado), "ruptura", "retest" o "todos".
    """
    valid_modes = {"rebote", "ruptura", "retest", "todos"}
    if mode not in valid_modes:
        raise ValueError(f"mode debe ser uno de {valid_modes}, recibido {mode!r}")

    out: list[Signal] = []
    for lv in levels:
        if mode in ("rebote", "todos"):
            out += confirm_rebote(candles, lv, wick_tolerance_pct=wick_tolerance_pct)
        if mode in ("ruptura", "todos"):
            out += confirm_ruptura(candles, lv)
        if mode in ("retest", "todos"):
            out += confirm_retest(candles, lv, retest_tolerance_pct=retest_tolerance_pct)

    return sorted(out, key=lambda s: idx_of(candles, s.candle))


def idx_of(candles: Sequence[Candle], candle: Candle) -> int:
    for i, c in enumerate(candles):
        if c is candle:
            return i
    return -1


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

    # Validacion prospectiva 29-ago-2026: con apertura 77.835 la envolvente
    # predijo 78.133 / 78.614 / 79.003 / 79.392 / 77.538 / 77.057 / 76.668
    # antes de publicarse el video (error max $1).
    expected = {80_586, 79_972, 81_082, 79_476, 81_483, 79_075, 81_885, 78_673}
    produced = {round(lv.price) for lv in engine.envelope()}
    print(f"\nanillos verificados reproducidos: {len(expected & produced)}/{len(expected)}")

    # CONFIRMACIONES - ejemplo con velas sinteticas alrededor del 125%.
    print("\n" + "=" * 68)
    print("CONFIRMACIONES sobre el nivel 125% (80.538)")
    print("=" * 68)

    nivel_125 = next(lv for lv in engine.grid() if lv.label == "125%")

    velas = [
        Candle("09:00", open=80_100, high=80_400, low=79_900, close=80_150),
        Candle("10:00", open=80_150, high=80_600, low=80_050, close=80_200),
        Candle("11:00", open=80_200, high=80_950, low=80_150, close=80_900),
        Candle("12:00", open=80_900, high=81_000, low=80_500, close=80_600),
        Candle("13:00", open=80_600, high=80_900, low=80_450, close=80_750),
    ]

    senales = detect_signals(velas, [nivel_125], mode="todos")
    for s in senales:
        print(" ", s)
    if not senales:
        print("  (sin confirmaciones en esta serie de ejemplo)")

    print(
        "\nUso recomendado: mode='rebote' por defecto (mas fiel a como el "
        "confirma en sus graficos), 'retest' para mayor exigencia."
    )
