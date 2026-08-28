"""Derivacion de las entradas de `crecetrader.LevelEngine` desde una serie diaria.

`src/crecetrader.py` es el modulo tal cual lo entrego el usuario — algoritmo puro,
sin I/O y sin dependencias externas: recibe las 5 entradas (precio, apertura diaria,
minimo anual, rango base, caida macro) ya resueltas. Este modulo aparte es el puente
entre una serie de velas diarias ya descargada (formato de `src/data/binance_client.py`:
dicts con date/open/high/low/close) y esas 5 entradas, para no tener que cargarlas a
mano en la UI. Vive afuera para que `crecetrader.py` quede intacto y siga siendo
copiable/reusable tal como llego.

Sin dependencias externas tampoco aca, igual que el modulo que acompana.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Optional, Sequence

from src.crecetrader import FALLBACK_BASE_RATIO, FALLBACK_MACRO_RATIO

__all__ = ["InferredInputs", "infer_inputs"]


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


def infer_inputs(
    candles: Sequence[dict],
    *,
    year_days: int = 365,
    impulse_retracement_pct: float = 50.0,
    min_reversal_pct: float = 15.0,
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
    (default 15%) obliga a que el corte sea un giro real, no ruido.

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

    Returns:
        `InferredInputs` con las 5 entradas y su trazabilidad.

    Raises:
        ValueError: si la serie esta vacia o el minimo anual no es positivo.
    """
    if not candles:
        raise ValueError("Se requiere al menos una vela diaria.")

    last = candles[-1]
    price = float(last["close"])
    daily_open = float(last["open"])

    window = candles[_year_window_start(candles, year_days) :]
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
    )
