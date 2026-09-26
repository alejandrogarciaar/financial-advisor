"""Derivacion de las entradas de `niveles_calculados.LevelEngine` desde una serie diaria.

`src/niveles_calculados.py` es el modulo tal cual lo entrego el usuario — algoritmo puro,
sin I/O y sin dependencias externas: recibe las 6 entradas (precio, apertura diaria,
apertura semanal, minimo anual, rango base, caida macro) ya resueltas. Este modulo aparte es el puente
entre una serie de velas diarias ya descargada (formato de `src/data/binance_client.py`:
dicts con date/open/high/low/close) y esas 6 entradas, para no tener que cargarlas a
mano en la UI. Vive afuera para que `niveles_calculados.py` siga siendo puro (sin I/O) y
copiable/reusable. (Ya no es byte-for-byte el archivo entregado: el 2026-08-30, a
pedido explicito del usuario, se le agrego la capa 4 — eje semanal.)

Sin dependencias externas tampoco aca, igual que el modulo que acompana.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Optional, Sequence

from src.niveles_calculados import DAILY_STEPS, DAILY_VERIFIED, FALLBACK_BASE_RATIO, FALLBACK_MACRO_RATIO

__all__ = [
    "InferredInputs",
    "infer_inputs",
    "DEFAULT_IMPULSE_RETRACEMENT_PCT",
    "DEFAULT_MIN_REVERSAL_PCT",
    "CALIBRATED_IMPULSE_THRESHOLDS",
    "impulse_thresholds_for",
    "CALIBRATED_DAILY_STEPS",
    "CALIBRATED_DAILY_VERIFIED",
]

# Umbrales genericos del "primer impulso" (ver `infer_inputs`).
DEFAULT_IMPULSE_RETRACEMENT_PCT: float = 50.0
DEFAULT_MIN_REVERSAL_PCT: float = 15.0

# Calibracion por ticker contra graficos publicados de Crecetrader.
#
# BTC, 26-sep-2026 (BTCUSD 1D, "Fases de grado mayor en diario"): Fase 1 = minimo del 1-jul
# (57734.63 en Bitstamp) al techo del 3-sep (82280.62), y la Fase 2 que la cierra retrocedio apenas
# 27.3% del avance y 8.15% del techo (cierre del 15-sep). Con 50% / 15% el impulso seguia "abierto"
# y el techo saltaba al pico de la Fase 3 (87373, 21-sep). 25% / 7.5% cortan en la Fase 1 y no en
# ninguno de los retrocesos previos: el de fines de julio (45% del avance pero solo 6.2% del techo)
# ni el del 10-sep (23.4% / 7.0%). Margen estrecho: reproduce ESE grafico, no es un optimo general.
#
# Solo BTC a proposito: aplicados como default global, 25% / 7.5% achicaban el rango base de SOL,
# AAPL, AMZN y GOOGL entre 70% y 92% (cortaban en retrocesos chicos al inicio del impulso), sin
# ningun grafico de referencia que respalde ese cambio para ellos.
CALIBRATED_IMPULSE_THRESHOLDS: dict[str, tuple[float, float]] = {"BTC": (25.0, 7.5)}


def impulse_thresholds_for(ticker: str) -> tuple[float, float]:
    """(retroceso % del avance, giro minimo % del techo) para `ticker`."""
    return CALIBRATED_IMPULSE_THRESHOLDS.get(
        ticker, (DEFAULT_IMPULSE_RETRACEMENT_PCT, DEFAULT_MIN_REVERSAL_PCT)
    )

# Escalera de la rejilla diaria (capa 2). `src/niveles_calculados.py` esta congelado y su
# DAILY_STEPS no trae 40% ni 60%, pero el mismo grafico marca la "zona de compras con 3 precios
# calculados" en 40 / 50 / 60% del rango base (67553 / 70008 / 72463). `daily_grid()` ya acepta
# `steps=`, asi que se agregan aca sin tocar el modulo congelado. 100% (el breakout, 82281) y 150%
# (94554) tambien quedan verificados con ese grafico.
CALIBRATED_DAILY_STEPS: tuple[float, ...] = tuple(sorted(set(DAILY_STEPS) | {40.0, 60.0}))
CALIBRATED_DAILY_VERIFIED: frozenset[float] = DAILY_VERIFIED | {40.0, 50.0, 60.0, 100.0, 150.0}


def _parse_date(raw) -> Optional[date]:
    """Fecha de una vela, tolerando los dos formatos que circulan en este repo.

    Binance diario y yfinance devuelven "YYYY-MM-DD"; las series intradia de Binance traen
    "YYYY-MM-DD HH:MM:SS". Los primeros 10 caracteres sirven para ambos.
    """
    try:
        return date.fromisoformat(str(raw)[:10])
    except (TypeError, ValueError):
        return None


def _year_window_start(candles: Sequence[dict], year_days: int) -> int:
    """Indice de la primera vela dentro de los ultimos `year_days` DIAS DE CALENDARIO.

    Contar velas en vez de dias seria un error silencioso en acciones: el mercado abre ~252
    dias al ano, asi que las ultimas 365 velas son ~1.45 anos, no uno. En cripto (una vela por
    dia, 24/7) las dos formas coinciden, de modo que este cambio no mueve nada de lo que ya
    estaba calculado para BTC/ETH/SOL.

    Si ninguna fecha se puede interpretar, cae al conteo de velas — un resultado aproximado es
    preferible a romper el calculo entero por un formato inesperado.
    """
    last_date = _parse_date(candles[-1].get("date")) if candles else None
    if last_date is None:
        return max(0, len(candles) - year_days)

    cutoff = last_date - timedelta(days=year_days)
    for i, candle in enumerate(candles):
        parsed = _parse_date(candle.get("date"))
        if parsed is not None and parsed >= cutoff:
            return i
    return max(0, len(candles) - 1)


def _structural_window_start(
    candles: Sequence[dict],
    year_days: int,
    *,
    edge_days: int = 45,
    proximity_pct: float = 2.0,
) -> tuple[int, int, bool]:
    """Inicio de la ventana del ancla, validado como piso estructural.

    El minimo de una ventana fija de `year_days` dias tiene dos fallas conocidas,
    ambas vistas en datos reales (oro, ago-2026, con ancla 3400 = la primera vela
    de la ventana): si el activo subio todo el ano, el "minimo anual" es
    literalmente el borde izquierdo de la ventana — un artefacto del recorte, no
    un piso — y ademas salta cada dia al deslizarse la ventana.

    La validacion: el minimo encontrado tiene que ser un piso real, es decir que
    las velas de los `edge_days` dias ANTERIORES al inicio de la ventana coticen
    por encima de el (con margen `proximity_pct`). Si no — la ventana corto una
    estructura mas vieja — se agranda la ventana un ano mas y se repite, hasta
    encontrar un piso o agotar la historia. Las anclas elegidas a mano por el
    metodo original pueden ser aun mas viejas; para replicar un grafico puntual
    sigue estando la sobreescritura manual.

    Returns:
        (indice de inicio, dias de ventana usados, True si hubo que extender).
    """
    span = year_days
    extended = False
    start = _year_window_start(candles, span)
    while start > 0:
        window_low = min(float(c["low"]) for c in candles[start:])
        threshold = window_low * (1 + proximity_pct / 100.0)
        edge_start = _parse_date(candles[start].get("date"))
        if edge_start is None:
            edge = candles[max(0, start - edge_days) : start]
        else:
            edge_cutoff = edge_start - timedelta(days=edge_days)
            edge = [
                c
                for c in candles[:start]
                if (d := _parse_date(c.get("date"))) is not None and d >= edge_cutoff
            ]
        if all(float(c["low"]) > threshold for c in edge):
            break
        span += year_days
        extended = True
        new_start = _year_window_start(candles, span)
        if new_start >= start:  # no queda mas historia hacia atras
            start = new_start
            break
        start = new_start
    return start, span, extended


@dataclass(frozen=True)
class InferredInputs:
    """Entradas del `LevelEngine` derivadas de una serie diaria.

    Attributes:
        price: ultimo cierre.
        daily_open: apertura de la vela diaria en curso.
        year_low: minimo de los ultimos `year_days` (ancla de las capas 2 y 3).
        year_low_date: fecha de ese minimo.
        base_range: amplitud del primer impulso desde el ancla.
        base_range_top: precio del techo de ese primer impulso.
        base_range_top_date: fecha de ese techo.
        impulse_open: True si el impulso sigue abierto (nunca hubo un giro que
            cumpliera los dos umbrales), o sea que el techo es el maximo
            alcanzado hasta hoy.
        base_range_estimated: True si se cayo a `FALLBACK_BASE_RATIO` por no
            poder medir un impulso real (ancla demasiado reciente).
        macro_range: techo de ciclo menos ancla.
        cycle_high: maximo de toda la historia disponible.
        cycle_high_date: fecha de ese maximo.
        macro_range_estimated: True si se cayo a `FALLBACK_MACRO_RATIO`.
        history_days: cantidad de velas diarias usadas.
        weekly_open: apertura de la semana en curso (eje central de la capa 4).
        weekly_open_date: fecha de la primera vela de esa semana.
        anchor_window_days: dias de calendario que termino cubriendo la ventana del ancla.
        anchor_extended: True si la ventana anual se extendio porque su minimo
            era un artefacto del borde (activo en tendencia durante todo el ano).
    """

    price: float
    daily_open: float
    year_low: float
    year_low_date: str
    base_range: float
    base_range_top: float
    base_range_top_date: str
    impulse_open: bool
    base_range_estimated: bool
    macro_range: float
    cycle_high: float
    cycle_high_date: str
    macro_range_estimated: bool
    history_days: int
    weekly_open: float
    weekly_open_date: str
    anchor_window_days: int
    anchor_extended: bool


def infer_inputs(
    candles: Sequence[dict],
    *,
    year_days: int = 365,
    impulse_retracement_pct: float = DEFAULT_IMPULSE_RETRACEMENT_PCT,
    min_reversal_pct: float = DEFAULT_MIN_REVERSAL_PCT,
    structural_anchor: bool = True,
) -> InferredInputs:
    """Deriva las entradas del motor desde velas diarias.

    El unico paso con margen de interpretacion es el "primer impulso" (capa 2):
    se recorre la serie desde el minimo anual acumulando el maximo (`high`), y
    se corta en la primera vela cuyo CIERRE cumple LAS DOS condiciones a la vez:
    devolver `impulse_retracement_pct` del avance acumulado Y estar
    `min_reversal_pct` por debajo del techo alcanzado.

    Las dos condiciones juntas, y no solo la primera, porque un retroceso del
    50% de un avance del 13% son apenas 6.5% de precio: en cripto eso pasa en
    dos dias y cerraria el impulso con una amplitud que no representa la
    estructura que se ve en el grafico. Exigir ademas un retroceso absoluto
    (default 15%) obliga a que el corte sea un giro real, no ruido. Para los
    tickers con un grafico de referencia (hoy solo BTC: 25% / 7.5%), usar
    `impulse_thresholds_for(ticker)` — ver `CALIBRATED_IMPULSE_THRESHOLDS`.

    El retroceso se mide con cierres y no con minimos intradia por la misma
    razon (una sola mecha rompe cualquier umbral razonable); el techo, en
    cambio, sale del `high`, porque la amplitud del impulso va del minimo al
    maximo efectivamente alcanzado.

    Ninguno de los dos umbrales es "el correcto" — donde termina un impulso es
    subjetivo, es justamente la parte del metodo que Crecetrader traza a ojo.
    Por eso ambos son parametros, y la UI ademas deja sobrescribir el rango
    resultante a mano.

    Args:
        candles: velas diarias en orden cronologico, con las claves
            date/open/high/low/close.
        year_days: ventana del minimo "anual".
        impulse_retracement_pct: retroceso, en % del avance, que da por
            terminado el primer impulso.
        min_reversal_pct: retroceso minimo, en % del techo, para que ese corte
            cuente como giro real.
        structural_anchor: si True (default), la ventana del minimo anual se
            extiende hacia atras cuando su minimo no es un piso real sino un
            artefacto del borde (ver `_structural_window_start`). False
            reproduce la ventana fija anterior.

    Returns:
        `InferredInputs` con las 6 entradas y su trazabilidad.

    Raises:
        ValueError: si la serie esta vacia o el minimo anual no es positivo.
    """
    if not candles:
        raise ValueError("Se requiere al menos una vela diaria.")

    last = candles[-1]
    price = float(last["close"])
    daily_open = float(last["open"])

    if structural_anchor:
        window_start, anchor_window_days, anchor_extended = _structural_window_start(
            candles, year_days
        )
    else:
        window_start = _year_window_start(candles, year_days)
        anchor_window_days, anchor_extended = year_days, False
    window = candles[window_start:]
    low_rel = min(range(len(window)), key=lambda i: float(window[i]["low"]))
    year_low = float(window[low_rel]["low"])
    if year_low <= 0:
        raise ValueError("El minimo anual debe ser positivo.")
    year_low_date = str(window[low_rel].get("date", ""))

    anchor_idx = len(candles) - len(window) + low_rel

    # Capa 2: primer impulso desde el ancla.
    top = float(candles[anchor_idx]["high"])
    top_idx = anchor_idx
    impulse_open = True
    for i in range(anchor_idx + 1, len(candles)):
        high_i = float(candles[i]["high"])
        if high_i > top:
            top, top_idx = high_i, i
        advance = top - year_low
        close_i = float(candles[i]["close"])
        retraced_enough = close_i <= top - advance * impulse_retracement_pct / 100.0
        real_reversal = close_i <= top * (1 - min_reversal_pct / 100.0)
        if retraced_enough and real_reversal:
            impulse_open = False
            break

    base_range = top - year_low
    base_range_estimated = base_range <= 0
    if base_range_estimated:
        base_range = year_low * FALLBACK_BASE_RATIO
        top = year_low + base_range
        top_idx = anchor_idx
    base_range_top_date = str(candles[top_idx].get("date", ""))

    # Capa 3: techo de ciclo sobre toda la historia disponible.
    cycle_idx = max(range(len(candles)), key=lambda i: float(candles[i]["high"]))
    cycle_high = float(candles[cycle_idx]["high"])
    macro_range = cycle_high - year_low
    macro_range_estimated = macro_range <= 0
    if macro_range_estimated:
        macro_range = year_low * FALLBACK_MACRO_RATIO
        cycle_high = year_low + macro_range

    # Capa 4: apertura semanal = open de la primera vela de la semana ISO en curso.
    # Recorriendo hacia atras: la ultima vela cuya fecha sigue siendo >= lunes es la
    # primera de la semana (cubre acciones con lunes feriado: queda el primer dia habil).
    weekly_open = daily_open
    weekly_open_date = str(last.get("date", ""))
    last_date = _parse_date(last.get("date"))
    if last_date is not None:
        monday = last_date - timedelta(days=last_date.weekday())
        for candle in reversed(candles):
            parsed = _parse_date(candle.get("date"))
            if parsed is None or parsed < monday:
                break
            weekly_open = float(candle["open"])
            weekly_open_date = str(candle.get("date", ""))

    return InferredInputs(
        price=price,
        daily_open=daily_open,
        year_low=year_low,
        year_low_date=year_low_date,
        base_range=base_range,
        base_range_top=top,
        base_range_top_date=base_range_top_date,
        impulse_open=impulse_open,
        base_range_estimated=base_range_estimated,
        macro_range=macro_range,
        cycle_high=cycle_high,
        cycle_high_date=str(candles[cycle_idx].get("date", "")),
        macro_range_estimated=macro_range_estimated,
        history_days=len(candles),
        weekly_open=weekly_open,
        weekly_open_date=weekly_open_date,
        anchor_window_days=anchor_window_days,
        anchor_extended=anchor_extended,
    )
