"""Banco de pruebas de la capa 5 (ondas de Elliott) de `scripts/niveles_calculados_v2.pine`.

Pine Script solo compila y corre dentro de TradingView, así que un indicador no se puede
testear desde acá. Lo que sí se puede testear es su ALGORITMO: este archivo reimplementa en
Python, línea por línea, el zigzag por umbral, las 3 reglas duras del impulso, el orden en
que se prueban los 6 casos de conteo y los objetivos de cada uno — exactamente como están
escritos en el `.pine` — y los ejercita contra datos reales de Binance y contra series
sintéticas armadas a mano.

No es código de la app: nada en `src/` lo importa, y la app no muestra ondas de Elliott en
ninguna pestaña. Es el arnés con el que se verificó la capa 5 antes de publicarla, y el lugar
donde re-verificarla si alguna vez se toca el `.pine`.

Qué comprueba, y por qué cada cosa:

1. Datos reales (BTC/ETH/SOL, 3 años de velas diarias, 9 umbrales de ATR cada uno):
   - El zigzag SIEMPRE alterna máximo/mínimo. Si esto se rompiera, todo el mapeo
     pivote -> número de onda queda corrido y el conteo pasa a ser ruido.
   - El nivel de invalidación que se dibuja NUNCA está ya rebasado por el precio de hoy.
     Un conteo cuyo propio precio de invalidación quedó del lado equivocado es un conteo
     roto que se sigue mostrando como si valiera: es el bug que encontró esta prueba
     (BTC diario, ATR14 x 5, devolvía una "onda 4 en curso" con el precio 19.000 USD por
     encima de su nivel de invalidación por solapamiento). El arreglo fue exigir la regla
     R3 también contra el precio EN VIVO mientras la onda 4 sigue abierta, no solo contra
     el pivote que la cierre.

2. Series sintéticas (una por rama, porque los datos reales no las ejercitan todas):
   - El caso ABC (impulso cerrado + A y B cerradas, C en curso) no apareció en ninguna de
     las 27 corridas reales; sin este test quedaría como código nunca ejecutado.
   - Cada caso se prueba alcista Y bajista. El `.pine` no tiene dos implementaciones
     espejadas: multiplica los precios por el signo del conteo y evalúa una sola vez, así
     que un error de signo se manifiesta como "funciona en una dirección y no en la otra".
   - Las 3 reglas duras, violadas de a una. Lo que se exige no es que el script se quede
     sin conteo, sino que no devuelva el impulso 1-5 completo: al fallar el impulso de 6
     pivotes, la búsqueda sigue con ventanas más cortas y re-ancla el conteo en un pivote
     posterior — que es lo que hace un analista cuando un conteo se le rompe.

Uso:  python scripts/elliott_check.py        (sale 0 si todo pasa, 1 si algo falla)
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.data import binance_client  # noqa: E402

# Símbolos y umbrales del barrido sobre datos reales. Los múltiplos van de 1.5 a 10 a
# propósito: el umbral ES el conteo (Elliott es fractal y no define una escala), así que un
# barrido amplio es la única forma de ejercitar las 6 ramas con la misma serie.
SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT")
ATR_MULTS = (1.5, 2.0, 2.5, 3.0, 4.0, 5.0, 6.5, 8.0, 10.0)
YEARS_BACK = 3.0
MAX_PIVOTS = 24  # mismo tope que el .pine


# --------------------------------------------------------------------- algoritmo ---
def atr(highs: list[float], lows: list[float], closes: list[float], n: int = 14) -> list[float | None]:
    """ATR de Wilder, igual que `ta.atr()` de Pine."""
    trs = [highs[0] - lows[0]]
    for i in range(1, len(closes)):
        trs.append(max(highs[i] - lows[i],
                       abs(highs[i] - closes[i - 1]),
                       abs(lows[i] - closes[i - 1])))
    out: list[float | None] = [None] * len(trs)
    prev = sum(trs[:n]) / n
    out[n - 1] = prev
    for i in range(n, len(trs)):
        prev = (prev * (n - 1) + trs[i]) / n
        out[i] = prev
    return out


def zigzag_states(highs, lows, closes, dev_series) -> list[list[tuple[float, int, int]]]:
    """Máquina de estados del zigzag del `.pine`, devolviendo los pivotes VIGENTES en CADA barra.

    El elemento `i` contiene exactamente los pivotes que el indicador habría tenido confirmados
    al cerrar la barra `i`, sin una sola barra de información futura. La máquina ya era causal
    (avanza barra a barra y solo confirma un pivote cuando el precio YA se alejó el umbral en
    sentido contrario); esto nada más expone los estados intermedios en vez de descartarlos, que
    es lo que hace posible el estudio fuera de muestra (`elliott_oos_validate.py`).

    Un pivote se confirma tarde, por definición: eso es lo que hace que el conteo repinte en
    vivo, y también lo que evita que este walk-forward mire el futuro.
    """
    states: list[list[tuple[float, int, int]]] = []
    piv: list[tuple[float, int, int]] = []
    z_dir, z_ext, z_bar = 0, None, 0
    for i in range(len(closes)):
        dev = dev_series[i]
        if dev is not None:
            if z_ext is None:
                z_ext, z_bar = closes[i], i
            if z_dir == 1:
                if highs[i] > z_ext:
                    z_ext, z_bar = highs[i], i
                elif lows[i] < z_ext - dev:
                    piv.append((z_ext, z_bar, 1))
                    z_dir, z_ext, z_bar = -1, lows[i], i
            elif z_dir == -1:
                if lows[i] < z_ext:
                    z_ext, z_bar = lows[i], i
                elif highs[i] > z_ext + dev:
                    piv.append((z_ext, z_bar, -1))
                    z_dir, z_ext, z_bar = 1, highs[i], i
            else:
                # Todavía sin dirección: se define con el primer recorrido de `dev`, sin
                # publicar pivote (el extremo inicial es un cierre arbitrario, no un giro).
                if highs[i] > z_ext + dev:
                    z_dir, z_ext, z_bar = 1, highs[i], i
                elif lows[i] < z_ext - dev:
                    z_dir, z_ext, z_bar = -1, lows[i], i
        states.append(list(piv[-MAX_PIVOTS:]))
    return states


def zigzag(highs, lows, closes, dev_series) -> list[tuple[float, int, int]]:
    """Los pivotes vigentes en la ÚLTIMA barra — que es lo único que el indicador dibuja.

    Envoltorio sobre `zigzag_states()` a propósito: una segunda copia de la máquina de estados
    se desincronizaría de la primera en cuanto alguien tocara una.
    """
    states = zigzag_states(highs, lows, closes, dev_series)
    return states[-1] if states else []


def impulse_ok(v, allow_overlap: bool = False, allow_trunc: bool = True) -> bool:
    """Las 3 reglas duras, en espacio transformado (precio x signo del conteo)."""
    v0, v1, v2, v3, v4, v5 = v
    w1, w3, w5 = v1 - v0, v3 - v2, v5 - v4
    shape = w1 > 0 and w3 > 0 and w5 > 0 and v2 < v1 and v4 < v3
    return (shape
            and v2 > v0                          # R1: la 2 no se come toda la 1
            and not (w3 < w1 and w3 < w5)        # R2: la 3 no es la más corta
            and (allow_overlap or v4 > v1)       # R3: la 4 no invade a la 1
            and (allow_trunc or v5 > v3))        # guía: la 5 supera el techo de la 3


def seed012(v0, v1, v2) -> bool:
    return v1 > v0 and v2 < v1 and v2 > v0


def seed0123(v0, v1, v2, v3) -> bool:
    return seed012(v0, v1, v2) and v3 > v1


def seed01234(v0, v1, v2, v3, v4, allow_overlap: bool = False) -> bool:
    return seed0123(v0, v1, v2, v3) and v4 < v3 and (allow_overlap or v4 > v1)


def count(piv, live):
    """Devuelve (patrón, onda, confianza, signo, objetivos[(precio, texto, invalida)]).

    Se prueban las estructuras de más a menos confirmada y se toma la primera que valida.
    El orden importa: un conteo con 5 ondas cerradas dice mucho más que uno con 2, y los
    casos cortos "encajan" casi siempre si se los deja primero.
    """
    n = len(piv)
    p = lambda b: piv[n - 1 - b][0]  # noqa: E731
    d = lambda b: piv[n - 1 - b][2]  # noqa: E731

    # Caso 0 - impulso 1-5 cerrado + A y B cerradas, onda C en curso.
    if n >= 8:
        s = d(2)
        a = [s * p(k) for k in (7, 6, 5, 4, 3, 2)]
        vA, vB, vL = s * p(1), s * p(0), s * live
        if impulse_ok(a) and vA < a[5] and vB > vA and vB < a[5] and vL < vB:
            leg = a[5] - vA
            return ("impulso 1-5 + ABC", "onda C en curso", 6, s, [
                (s * (vB - leg * 0.618), "C = 0.618 x A", False),
                (s * (vB - leg), "C = A", False),
                (s * (vB - leg * 1.618), "C = 1.618 x A", False),
                (s * a[5], "invalida el ABC", True)])

    # Caso 1 - impulso 1-5 cerrado, corrección recién arrancando.
    if n >= 6:
        s = d(0)
        c = [s * p(k) for k in (5, 4, 3, 2, 1, 0)]
        cL = s * live
        # cL < c[5]: si el precio ya pasó el extremo de la onda 5, la 5 se está
        # extendiendo y esto no es una corrección en curso, es otro conteo.
        if impulse_ok(c) and cL < c[5]:
            tot = c[5] - c[0]
            return ("impulso 1-5 completo", "onda A en curso", 5, s, [
                (s * (c[5] - tot * 0.382), "correccion 0.382", False),
                (s * (c[5] - tot * 0.5), "correccion 0.5", False),
                (s * (c[5] - tot * 0.618), "correccion 0.618", False),
                (s * c[4], "extremo de la 4", False),
                (s * c[5], "invalida el fin del impulso", True)])

    # Caso 2 - 0-1-2-3-4 cerradas, onda 5 en curso.
    if n >= 5:
        s = -d(0)
        q = [s * p(k) for k in (4, 3, 2, 1, 0)]
        vL = s * live
        if seed01234(*q) and vL > q[4]:
            w1 = q[1] - q[0]
            return ("impulso en curso", "onda 5 en curso", 4, s, [
                (s * (q[4] + w1 * 0.618), "5 = 0.618 x 1", False),
                (s * (q[4] + w1), "5 = 1 x 1", False),
                (s * (q[4] + w1 * 1.618), "5 = 1.618 x 1", False),
                (s * (q[0] + (q[3] - q[0]) * 1.236), "5 = 1.236 x (1-3)", False),
                (s * q[4], "invalida (extremo de la 4)", True)])

    # Caso 3 - 0-1-2-3 cerradas, onda 4 en curso.
    if n >= 4:
        s = d(0)
        q = [s * p(k) for k in (3, 2, 1, 0)]
        vL = s * live
        # `vL > q[1]` es la R3 aplicada al precio de AHORA: mientras la onda 4 sigue
        # abierta, el propio precio ya puede haber invadido a la onda 1, y en ese caso el
        # conteo está roto aunque los 4 pivotes cerrados encajen (ver el docstring).
        if seed0123(*q) and vL < q[3] and vL > q[1]:
            w3 = q[3] - q[2]
            return ("impulso en curso", "onda 4 en curso", 3, s, [
                (s * (q[3] - w3 * 0.236), "4 = 0.236 de la 3", False),
                (s * (q[3] - w3 * 0.382), "4 = 0.382 de la 3", False),
                (s * (q[3] - w3 * 0.5), "4 = 0.5 de la 3", False),
                (s * q[1], "invalida (solapa con la 1)", True)])

    # Caso 4 - 0-1-2 cerradas, onda 3 en curso.
    if n >= 3:
        s = -d(0)
        q = [s * p(k) for k in (2, 1, 0)]
        vL = s * live
        if seed012(*q) and vL > q[2]:
            w1 = q[1] - q[0]
            return ("impulso en curso", "onda 3 en curso", 2, s, [
                (s * (q[2] + w1), "3 = 1 x 1", False),
                (s * (q[2] + w1 * 1.618), "3 = 1.618 x 1", False),
                (s * (q[2] + w1 * 2.618), "3 = 2.618 x 1", False),
                (s * q[2], "invalida (extremo de la 2)", True)])

    # Caso 5 - solo 0-1 cerradas, onda 2 en curso. El conteo más débil: dos pivotes
    # cualesquiera lo cumplen, por eso queda último y se reporta con confianza baja.
    if n >= 2:
        s = d(0)
        v0, v1, vL = s * p(1), s * p(0), s * live
        if v1 > v0 and v0 < vL < v1:
            w1 = v1 - v0
            return ("impulso en curso", "onda 2 en curso", 1, s, [
                (s * (v1 - w1 * 0.5), "2 = 0.5 de la 1", False),
                (s * (v1 - w1 * 0.618), "2 = 0.618 de la 1", False),
                (s * (v1 - w1 * 0.786), "2 = 0.786 de la 1", False),
                (s * v0, "invalida (arranque de la 1)", True)])

    return ("sin conteo", "-", 0, 1, [])


# ------------------------------------------------------------------ verificación ---
def check_real(symbol: str, mult: float, verbose: bool) -> list[str]:
    rows, _meta = binance_client.get_historical_prices(symbol, years_back=YEARS_BACK)
    highs = [r["high"] for r in rows]
    lows = [r["low"] for r in rows]
    closes = [r["close"] for r in rows]
    dev = [None if x is None else x * mult for x in atr(highs, lows, closes, 14)]
    piv = zigzag(highs, lows, closes, dev)
    pat, wave, conf, sgn, objs = count(piv, closes[-1])

    problems: list[str] = []
    if not all(piv[i][2] != piv[i + 1][2] for i in range(len(piv) - 1)):
        problems.append(f"{symbol} x{mult}: el zigzag no alterna máximo/mínimo")

    # La invalidación es un PISO en los casos con el impulso todavía abierto y un TECHO
    # cuando el impulso ya cerró; en ambos, el precio de hoy tiene que estar del lado válido.
    ceiling = wave in ("onda C en curso", "onda A en curso")
    for price, text, is_invalid in objs:
        if not is_invalid:
            continue
        v_live, v_inv = sgn * closes[-1], sgn * price
        if (v_live >= v_inv) if ceiling else (v_live <= v_inv):
            problems.append(f"{symbol} x{mult}: '{text}' en {price:,.2f} ya está rebasado "
                            f"(precio {closes[-1]:,.2f}, conteo {wave})")

    if verbose:
        print(f"  {symbol:<9} ATR14 x {mult:<4}  {len(piv):>2} pivotes  ->  {pat} / {wave} "
              f"(conf {conf})")
    return problems


def synthetic_pivots(prices: list[float], first_dir: int):
    """Pivotes a mano, en orden cronológico; first_dir = -1 si el primero es un mínimo."""
    return [(p, i, first_dir * (-1) ** i) for i, p in enumerate(prices)]


def check_synthetic(verbose: bool) -> list[str]:
    problems: list[str] = []

    def expect(name, pivots, live, want_wave, want_conf):
        _pat, wave, conf, _s, _objs = count(pivots, live)
        if wave != want_wave or conf != want_conf:
            problems.append(f"{name}: esperaba {want_wave}/conf {want_conf}, dio {wave}/conf {conf}")
        elif verbose:
            print(f"  {name:<44} -> {wave} (conf {conf})")

    def expect_rejects_full(name, pivots, live):
        _pat, wave, conf, _s, _objs = count(pivots, live)
        if conf >= 5:
            problems.append(f"{name}: aceptó un impulso que viola una regla dura")
        elif verbose:
            print(f"  {name:<44} -> rechazado; queda {wave} (conf {conf})")

    # El caso ABC, que ninguna corrida sobre datos reales ejercitó. Alcista y su espejo.
    expect("ABC alcista, C en curso",
           synthetic_pivots([100, 200, 150, 400, 250, 500, 350, 450], -1), 380, "onda C en curso", 6)
    expect("ABC bajista, C en curso",
           synthetic_pivots([500, 400, 450, 200, 350, 100, 250, 150], 1), 220, "onda C en curso", 6)
    expect("impulso alcista completo",
           synthetic_pivots([100, 200, 150, 400, 250, 500], -1), 470, "onda A en curso", 5)
    expect("impulso bajista completo",
           synthetic_pivots([500, 400, 450, 200, 350, 100], 1), 130, "onda A en curso", 5)

    # Las 3 reglas duras, violadas de a una.
    expect_rejects_full("R1 violada (la 2 pasa el arranque de la 1)",
                        synthetic_pivots([100, 200, 90, 400, 250, 500], -1), 470)
    expect_rejects_full("R2 violada (la 3 es la más corta)",
                        synthetic_pivots([100, 300, 250, 320, 280, 600], -1), 580)
    expect_rejects_full("R3 violada (la 4 solapa con la 1)",
                        synthetic_pivots([100, 200, 150, 400, 180, 500], -1), 470)

    # El resto de las ramas, incluida la de "no hay nada que contar".
    expect("onda 5 en curso", synthetic_pivots([100, 200, 150, 400, 250], -1), 430, "onda 5 en curso", 4)
    expect("onda 4 en curso", synthetic_pivots([100, 200, 150, 400], -1), 320, "onda 4 en curso", 3)
    expect("onda 3 en curso", synthetic_pivots([100, 200, 150], -1), 260, "onda 3 en curso", 2)
    expect("onda 2 en curso", synthetic_pivots([100, 200], -1), 170, "onda 2 en curso", 1)
    expect("un solo pivote", synthetic_pivots([100], -1), 105, "-", 0)
    return problems


def main(verbose: bool = True) -> int:
    problems: list[str] = []

    print("Series sintéticas (una por rama del conteo):")
    problems += check_synthetic(verbose)

    print(f"\nDatos reales de Binance ({len(SYMBOLS)} símbolos x {len(ATR_MULTS)} umbrales, "
          f"{YEARS_BACK:g} años diarios):")
    for symbol in SYMBOLS:
        for mult in ATR_MULTS:
            problems += check_real(symbol, mult, verbose)

    if problems:
        print(f"\n{len(problems)} problema(s):")
        for p in problems:
            print(f"  - {p}")
        return 1
    print("\nTodo OK: el zigzag alterna siempre, ningún conteo se muestra con su propio "
          "nivel de invalidación ya rebasado, y las 6 ramas están cubiertas.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
