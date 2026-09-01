"""¿La capa 5 (ondas de Elliott) de `niveles_calculados_v2.pine` anticipa algo? Estudio OOS.

La capa 5 se publicó como DESCRIPTIVA, igual que las capas 1-4, y a esas ya se las midió:
`niveles_oos_validate.py` dio 0/24 y `envolvente_oos_validate.py` 0/18. Este script cierra el
mismo círculo para el conteo de ondas, con la misma metodología, para que "descriptivo" sea un
resultado medido y no una precaución retórica.

WALK-FORWARD DE VERDAD
----------------------
El conteo de Elliott REPINTA: un pivote se confirma recién cuando el precio ya se alejó el
umbral en sentido contrario. Eso es un problema mortal para un backtest ingenuo — si uno toma
el conteo final del gráfico y lo proyecta hacia atrás, está usando pivotes que en su momento no
existían, y el resultado sale espectacular por construcción. Acá NO pasa: `zigzag_states()`
(en `elliott_check.py`) devuelve los pivotes vigentes en CADA barra, tal como el indicador los
habría tenido ese día, y el conteo de la barra `i` se arma solo con eso más el cierre de `i`.
La misma tardanza que hace repintar al indicador en vivo es la que garantiza que acá no se mire
el futuro.

QUÉ SE PRUEBA
-------------
A. DIRECCIÓN (la afirmación central de Elliott). Cada estado de onda implica una dirección
   esperada para lo que viene: las ondas 3 y 5 son impulsivas a favor de la estructura; las
   ondas 2 y 4 son correctivas contra ella; las ondas A y C corrigen el impulso ya cerrado.
   Multiplicado por el signo del conteo da un "sentido esperado" por barra, y se mide si el
   retorno futuro efectivamente va para ese lado más que el promedio del período.

B. NIVELES (los objetivos de Fibonacci y la invalidación). Mismo diseño que
   `niveles_oos_validate.py`: se marca el día en que el precio queda a menos de θ·ATR(14) de
   alguno de los niveles del día, y se compara el retorno futuro contra el promedio. Se separa
   por lado (nivel por debajo del precio = soporte candidato; por encima = resistencia) porque
   un nivel que "funciona" tiene que empujar en sentidos distintos según de qué lado esté.

CONTROLES QUE HACEN QUE ESTO SIRVA PARA ALGO
--------------------------------------------
1. Split cronológico 60/40 y consistencia de signo en TODOS los horizontes (5/10/20/30 días).
   Un horizonte suelto que da bien es exactamente el patrón que hundió al ADX en este proyecto.
2. Placebo de densidad (K=40) para la parte B: los mismos niveles corridos un ±12% aleatorio.
   Misma cantidad de niveles, mismo vecindario, otras ubicaciones. Si el gap real no queda en
   la cola de esa distribución, lo que se está midiendo es la densidad de líneas, no Fibonacci.
3. El umbral del zigzag queda FIJO en el valor por defecto del indicador (ATR14 × 3). Es la
   decisión metodológica más importante del script: el umbral ES el conteo (Elliott es fractal
   y no define escala), así que barrerlo y quedarse con el que valide sería fabricar el
   resultado. Los múltiplos 2 y 5 se corren aparte y se reportan como SENSIBILIDAD — sirven
   para ver si el resultado es frágil, no como intentos adicionales de aprobar.
4. La cantidad de variantes es alta (7 de dirección + 12 de niveles por símbolo). El titular
   pre-registrado es el AGREGADO de dirección; todo lo demás es exploratorio y se imprime como
   tal. Con ~36 pruebas por símbolo, algunas van a dar bien por azar: eso no es un hallazgo.

Uso:  python scripts/elliott_oos_validate.py            (símbolos por defecto)
      python scripts/elliott_oos_validate.py BTCUSDT    (uno solo)

Correr LOCAL: Binance responde 403/451 a los entornos remotos de este repo.
"""

from __future__ import annotations

import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.elliott_check import atr, count, zigzag_states  # noqa: E402
from scripts.oos_validate import run_oos_validation  # noqa: E402
from src.data import binance_client  # noqa: E402
from src.speculation import classify_regime_series  # noqa: E402

SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT")
YEARS_BACK = 5.0
HORIZONS = [5, 10, 20, 30]

ZIGZAG_ATR_MULT = 3.0            # el default del indicador; NO se barre (ver docstring)
SENSITIVITY_MULTS = (2.0, 5.0)   # solo para ver fragilidad, no para aprobar

TOUCH_THETAS = (0.25, 0.5, 1.0)  # tolerancia del toque, en ATR(14)
PLACEBO_THETA = 0.5              # el θ central es el único con placebo
N_PLACEBOS = 40
PLACEBO_JITTER = 0.12            # ±12%: misma densidad, otra ubicación
PLACEBO_SEED = 20260831

NEWLINE = chr(10)  # evita escapes en el codigo generado; ver historial de este archivo

# Sentido que la teoría le asigna a cada estado, relativo a la estructura contada.
# +1 = a favor del impulso, -1 = en contra (corrección en curso).
WAVE_DIRECTION = {
    "onda 3 en curso": +1,
    "onda 5 en curso": +1,
    "onda 2 en curso": -1,
    "onda 4 en curso": -1,
    "onda A en curso": -1,
    "onda C en curso": -1,
}


def walk_forward_counts(prices: list[dict], atr_mult: float):
    """Conteo vigente en cada barra, sin mirar una sola barra futura.

    Devuelve (atr_series, counts) donde `counts[i]` es lo que el indicador habría mostrado al
    cerrar la barra `i`: (patrón, onda, confianza, signo, niveles).
    """
    highs = [p["high"] for p in prices]
    lows = [p["low"] for p in prices]
    closes = [p["close"] for p in prices]
    atr_series = atr(highs, lows, closes, 14)
    dev = [None if a is None else a * atr_mult for a in atr_series]
    states = zigzag_states(highs, lows, closes, dev)
    counts = [count(states[i], closes[i]) for i in range(len(closes))]
    return atr_series, counts


def direction_conditions(counts) -> dict[str, tuple[list[bool], int]]:
    """Condición por barra + SIGNO ESPERADO del gap, para cada lectura direccional.

    Cada variante viene con el sentido que la teoría le atribuye, fijado de antemano. Sin eso
    una variante no es interpretable: un mismo estado de onda aparece en conteos alcistas y
    bajistas, y si se los mezcla el gap resultante no dice nada (se estaría midiendo "días en
    los que hay onda 3", que en una serie mayormente alcista es casi lo mismo que "días
    alcistas"). Exigir el signo por adelantado es lo que impide que cualquier resultado
    "confirme" algo.
    """
    n = len(counts)
    out: dict[str, tuple[list[bool], int]] = {
        "agregado alcista": ([False] * n, +1),
        "agregado bajista": ([False] * n, -1),
    }
    for wave in WAVE_DIRECTION:
        out[f"{wave} / esperado alcista"] = ([False] * n, +1)
        out[f"{wave} / esperado bajista"] = ([False] * n, -1)

    for i, (_pat, wave, conf, sgn, _lv) in enumerate(counts):
        if conf == 0 or wave not in WAVE_DIRECTION:
            continue
        expected = sgn * WAVE_DIRECTION[wave]
        if expected > 0:
            out["agregado alcista"][0][i] = True
            out[f"{wave} / esperado alcista"][0][i] = True
        elif expected < 0:
            out["agregado bajista"][0][i] = True
            out[f"{wave} / esperado bajista"][0][i] = True
    return out


def touch_condition(counts, closes, atr_series, theta, kind, side,
                    jitter: float | None = None) -> list[bool]:
    """¿El cierre del día quedó a menos de θ·ATR de algún nivel de ese tipo y ese lado?

    `kind` es "objetivo" (proyecciones de Fibonacci) o "invalidacion". `side` es "abajo"
    (nivel por debajo del precio, soporte candidato) o "arriba". `jitter` construye la
    versión placebo: los mismos niveles corridos ese porcentaje.
    """
    out = []
    for i, (_pat, _wave, conf, _sgn, levels) in enumerate(counts):
        a = atr_series[i]
        if conf == 0 or a is None or not levels:
            out.append(False)
            continue
        price = closes[i]
        hit = False
        for lvl, _txt, is_inv in levels:
            if (kind == "invalidacion") != bool(is_inv):
                continue
            if jitter is not None:
                lvl = lvl * (1.0 + jitter)
            if side == "abajo" and lvl > price:
                continue
            if side == "arriba" and lvl < price:
                continue
            if abs(price - lvl) <= theta * a:
                hit = True
                break
        out.append(hit)
    return out


def stage2_redundancy(dates, closes, cond, regimes) -> tuple[bool, list[str]]:
    """Etapa 2: ¿aporta algo MÁS ALLÁ de la tendencia, o es la tendencia dicha de otro modo?

    Es el mismo control que dejó afuera a un combo del estudio de VWAP y que declaró redundante
    al Fear & Greed. La condición se compara contra un baseline restringido al MISMO régimen de
    tendencia (`classify_regime_series`), así que un conteo que solo marca "el precio viene
    subiendo" queda con gap ~0 y no sobrevive. Basta que sobreviva en UN régimen con datos
    suficientes; si no sobrevive en ninguno, el efecto de etapa 1 era la tendencia.
    """
    lines = []
    survives = False
    for regime in ("fuerte", "debil", "mixta"):
        in_regime = [r == regime for r in regimes]
        c = [cond[i] and in_regime[i] for i in range(len(cond))]
        marked = sum(c)
        if marked < 30:
            lines.append(f"           etapa2 {regime}: solo {marked} dias, sin datos")
            continue
        res = run_oos_validation(dates, closes, c, horizons_days=HORIZONS,
                                 baseline_condition=in_regime)
        gap = mean_test_gap(res)
        ok = res.all_validated
        survives = survives or ok
        lines.append(f"           etapa2 {regime}: {'SOBREVIVE' if ok else 'redundante'} "
                     f"({marked} dias, gap test medio "
                     f"{'n/d' if gap is None else format(gap, '+.2%')})")
    return survives, lines


def mean_test_gap(result) -> float | None:
    gaps = [h.test_gap for h in result.horizons if h.test_gap is not None]
    return sum(gaps) / len(gaps) if gaps else None


def study_direction(dates, closes, counts, regimes) -> tuple[list[str], dict]:
    """Parte A. El titular pre-registrado son las dos primeras variantes."""
    lines = ["  A. DIRECCION DEL CONTEO"]
    results: dict[str, tuple[bool, float | None]] = {}
    conds = direction_conditions(counts)
    order = ["agregado alcista", "agregado bajista"] + [
        f"{w} / esperado {d}" for w in WAVE_DIRECTION for d in ("alcista", "bajista")]
    for name in order:
        cond, expected_sign = conds[name]
        n_days = sum(cond)
        if n_days < 30:
            lines.append(f"    [-]    {name}: solo {n_days} dias marcados, no alcanza")
            results[name] = (False, None)
            continue
        res = run_oos_validation(dates, closes, cond, horizons_days=HORIZONS)
        head = "titular" if name.startswith("agregado") else "exploratorio"
        gap = mean_test_gap(res)
        stage2_lines: list[str] = []
        # El signo del gap tiene que ser el que la teoría predijo, no "alguno consistente".
        sign_ok = all((h.test_gap or 0.0) * expected_sign > 0 for h in res.horizons)
        passed = res.all_validated and sign_ok
        if not sign_ok and res.all_validated:
            stage2_lines.append("           signo del gap CONTRARIO al que predice la teoria")
        if passed:
            survives, stage2_lines = stage2_redundancy(dates, closes, cond, regimes)
            passed = survives
        tag = "VALIDA" if passed else "no valida"
        lines.append(f"    [{tag}] {name} ({head}, {n_days} dias, gap test medio "
                     f"{'n/d' if gap is None else format(gap, '+.2%')})")
        for h in res.horizons:
            mark = "OK  " if h.validated else "FAIL"
            lines.append(f"           {mark} {h.horizon_days}d: train "
                         f"{'n/d' if h.train_gap is None else format(h.train_gap, '+.2%')} "
                         f"(n={h.train_n}) / test "
                         f"{'n/d' if h.test_gap is None else format(h.test_gap, '+.2%')} "
                         f"(n={h.test_n})")
        lines += stage2_lines
        results[name] = (passed, gap)
    return lines, results


def study_levels(dates, closes, counts, atr_series, rng) -> tuple[list[str], dict]:
    """Parte B. Toques de nivel + placebo de densidad sobre el θ central."""
    lines = ["  B. NIVELES (objetivos de Fibonacci e invalidacion)"]
    results: dict[str, tuple[bool, float | None]] = {}
    jitters = [rng.uniform(-PLACEBO_JITTER, PLACEBO_JITTER) for _ in range(N_PLACEBOS)]

    for kind in ("objetivo", "invalidacion"):
        for side in ("abajo", "arriba"):
            # Un soporte que funciona empuja hacia ARRIBA (gap positivo); una resistencia,
            # hacia abajo. Sin fijar esto de antemano, cualquier signo "confirma".
            expect_pos = side == "abajo"
            for theta in TOUCH_THETAS:
                cond = touch_condition(counts, closes, atr_series, theta, kind, side)
                n_days = sum(cond)
                if n_days < 30:
                    lines.append(f"    [-]    {kind}/{side} th={theta}: solo {n_days} toques")
                    results[f"{kind}/{side} th={theta}"] = (False, None)
                    continue
                res = run_oos_validation(dates, closes, cond, horizons_days=HORIZONS)
                passed = res.all_validated and all(
                    (h.test_gap or 0) > 0 if expect_pos else (h.test_gap or 0) < 0
                    for h in res.horizons)
                gap = mean_test_gap(res)
                extra = ""
                if theta == PLACEBO_THETA:
                    real_gap = gap
                    placebo_gaps = []
                    for j in jitters:
                        pc = touch_condition(counts, closes, atr_series, theta, kind, side, jitter=j)
                        if sum(pc) >= 30:
                            g = mean_test_gap(run_oos_validation(dates, closes, pc,
                                                                 horizons_days=HORIZONS))
                            if g is not None:
                                placebo_gaps.append(g)
                    if real_gap is not None and placebo_gaps:
                        pct = 100.0 * sum(1 for g in placebo_gaps if g < real_gap) / len(placebo_gaps)
                        ok = pct >= 90 if expect_pos else pct <= 10
                        extra = (f"  | placebo: percentil {pct:.0f} de {len(placebo_gaps)} "
                                 f"{'[extremo]' if ok else '[NO extremo -> es densidad]'}")
                        passed = passed and ok
                    else:
                        extra = "  | placebo sin datos suficientes"
                        passed = False
                tag = "VALIDA" if passed else "no valida"
                lines.append(f"    [{tag}] {kind}/{side} th={theta} ({n_days} toques, "
                             f"gap test medio {'n/d' if gap is None else format(gap, '+.2%')})"
                             f"{extra}")
                results[f"{kind}/{side} th={theta}"] = (passed, gap)
    return lines, results


def run_symbol(symbol: str, atr_mult: float, rng, full: bool) -> tuple[list[str], dict]:
    rows, _meta = binance_client.get_historical_prices(symbol, years_back=YEARS_BACK)
    dates = [r["date"] for r in rows]
    closes = [r["close"] for r in rows]
    atr_series, counts = walk_forward_counts(rows, atr_mult)

    counted = sum(1 for c in counts if c[2] > 0)
    lines = [f"\n=== {symbol}  (zigzag ATR14 x {atr_mult}, {len(rows)} velas diarias, "
             f"{counted} con conteo vivo = {100.0*counted/len(rows):.0f}%) ==="]
    regimes = classify_regime_series(closes)
    dir_lines, results = study_direction(dates, closes, counts, regimes)
    lines += dir_lines
    if full:
        level_lines, level_results = study_levels(dates, closes, counts, atr_series, rng)
        lines += level_lines
        results.update(level_results)
    return lines, results


def main(argv: list[str]) -> int:
    symbols = tuple(a.upper() for a in argv[1:]) or SYMBOLS
    rng = random.Random(PLACEBO_SEED)

    print("ESTUDIO OOS DE LA CAPA 5 (ondas de Elliott)")
    print("Walk-forward causal, split 60/40, consistencia de signo en 5/10/20/30 dias,")
    print(f"placebo de densidad K={N_PLACEBOS}. Umbral del zigzag FIJO en el default del")
    print("indicador (ATR14 x 3): barrerlo seria fabricar el resultado.")
    print()

    default: dict[str, dict[str, tuple[bool, float | None]]] = {}
    for symbol in symbols:
        lines, res = run_symbol(symbol, ZIGZAG_ATR_MULT, rng, full=True)
        print(NEWLINE.join(lines))
        default[symbol] = res

    print()
    print("--- SENSIBILIDAD (no son intentos adicionales de aprobar) ---")
    sensitivity: dict[float, dict[str, dict]] = {}
    for mult in SENSITIVITY_MULTS:
        sensitivity[mult] = {}
        for symbol in symbols:
            lines, res = run_symbol(symbol, mult, rng, full=False)
            print(NEWLINE.join(lines))
            sensitivity[mult][symbol] = res

    # ------------------------------------------------------------------ veredicto ---
    # Dos barras, las dos necesarias, ninguna negociable:
    #   (1) que valide en TODOS los simbolos al umbral por defecto — algo que funciona en
    #       una moneda de tres es una coincidencia, no una senal;
    #   (2) que el signo del efecto no se de vuelta en ninguna corrida de sensibilidad —
    #       si cambiar un parametro que la teoria no fija invierte la conclusion sobre los
    #       mismos datos, no habia conclusion.
    print()
    print("=== VEREDICTO ===")
    all_variants = sorted({k for r in default.values() for k in r})
    robust: list[str] = []
    for variant in all_variants:
        passed_all = all(default[s].get(variant, (False, None))[0] for s in symbols)
        if not passed_all:
            continue
        signs = []
        for mult in SENSITIVITY_MULTS:
            for s in symbols:
                g = sensitivity[mult].get(s, {}).get(variant, (False, None))[1]
                if g is not None:
                    signs.append(g > 0)
        base = default[symbols[0]][variant][1]
        stable = bool(signs) and all(x == (base is not None and base > 0) for x in signs)
        flag = "ROBUSTO" if stable else "FRAGIL AL UMBRAL"
        robust.append(f"{variant}: valida en los {len(symbols)} simbolos, {flag}")
    if robust:
        for line in robust:
            print("  " + line)
    else:
        print("  Ninguna variante valida en los 3 simbolos al umbral por defecto.")

    confirmed = [r for r in robust if "ROBUSTO" in r]
    print()
    if confirmed:
        print("HAY ALGO. Revisar a mano antes de tocar el indicador.")
    else:
        print("NADA VALIDA de forma robusta. La capa 5 queda DESCRIPTIVA, igual que las")
        print("capas 1-4 (0/24 y 0/18 en sus propios estudios). Lo que valide en un solo")
        print("simbolo, o solo a un umbral, es ruido de las ~36 pruebas por simbolo.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
