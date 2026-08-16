"""Validación fuera de muestra del VWAP para BTC/ETH/SOL — ¿la distancia del precio al VWAP
anticipa algo del retorno futuro, o es solo descriptivo?

CORRER LOCAL, NO DESDE UNA SESIÓN REMOTA DE CLAUDE: Binance responde 403/451 a los proxies de
los entornos remotos (mismo bloqueo documentado en CLAUDE.md para el deploy de Streamlit Cloud),
así que este script solo puede traer datos desde una máquina con acceso directo:

    python scripts/vwap_oos_validate.py              # BTC, ETH y SOL
    python scripts/vwap_oos_validate.py BTC          # uno solo

No modifica nada de la app: imprime un reporte y, al final, el diccionario listo para pegar en
`src/ui/cripto.py` si algo valida. Mientras `VWAP_VALIDATED_COMBOS` siga vacío, la sección
"🎯 VWAP" se queda puramente descriptiva (que es como se publicó).

QUÉ SE PRUEBA
La señal es la distancia del precio al VWAP normalizada por ATR(14): `(close - vwap) / atr`.
Normalizar por ATR es lo que la hace comparable entre monedas y entre regímenes de volatilidad
(un 5% sobre el VWAP no significa lo mismo en un mercado tranquilo que en uno que se mueve 8%
por día) — mismo criterio que usa el Market Reaction Zone Engine para todas sus tolerancias.
Se barren las 3 ventanas de VWAP que muestra la UI (7/30/365 días), los 2 lados (precio ARRIBA
del VWAP / precio ABAJO), y 3 umbrales de distancia (0.5/1.0/1.5 ATR) — el barrido de umbral es
el chequeo de fragilidad que este proyecto siempre aplica.

Hay dos historias posibles y opuestas, y el script no asume ninguna: reversión ("el precio
lejos por debajo del costo promedio vuelve") o momentum ("lejos por debajo sigue cayendo").
El signo del resultado es el que decide cuál, igual que pasó con el Índice de Miedo y Codicia,
donde el resultado real fue momentum y no la lectura contrarian clásica.

CRITERIO PARA CANTAR "VALIDADO" (deliberadamente estricto, el mismo del resto del proyecto)
1. Split cronológico 60/40 (nunca aleatorio: mezclaría días futuros dentro del "entrenamiento").
2. Los 4 horizontes (5/10/20/30 días) tienen que mantener el signo entre train y test.
3. Los 3 umbrales tienen que dar TODOS lo mismo, y con el mismo signo entre sí. Un umbral que
   valida solo mientras sus vecinos no es exactamente la fragilidad de múltiples comparaciones
   que ya descartó a Fibonacci, ADX, OBV y las dos rondas del score del Zone Engine.
4. Si pasa lo anterior, todavía falta el chequeo de REDUNDANCIA (etapa 2): ¿agrega información
   sobre el régimen de tendencia que la app YA usa (`classify_regime_series`, el del "📋 Plan de
   DCA sugerido")? Precio arriba del VWAP y precio arriba de las medias móviles son parientes
   cercanos; si la señal no separa nada DENTRO del régimen, es la misma información con otro
   nombre, no una señal nueva. Es el mismo chequeo que dejó al Índice de Miedo y Codicia como
   disclosure y no como input de decisión.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.oos_validate import OOSResult, run_oos_validation, run_oos_validation_sweep
from src.config import CRYPTO_BINANCE_SYMBOLS
from src.data import binance_client
from src.speculation import VWAP_WINDOWS_DAYS, classify_regime_series, rolling_vwap_series

# Privado, pero es el mismo ATR de Wilder que ya usan `compute_adx` y el Zone Engine — un
# investigación que se re-implemente el ATR "para no importar un guión bajo" estaría midiendo
# algo distinto de lo que mide la app, que es justamente lo que hay que evitar acá.
from src.support_resistance import _atr_series

ATR_PERIOD = 14
DISTANCE_THRESHOLDS_ATR = (0.5, 1.0, 1.5)
HORIZONS_DAYS = [5, 10, 20, 30]
MIN_OBSERVATIONS = 15
SIDES = ("arriba", "abajo")


def distance_to_vwap_in_atr(prices: list[dict], window_days: int) -> list[float | None]:
    """`(close - vwap) / atr` por día. `None` donde falta el VWAP (sin volumen en la ventana) o
    el ATR (los primeros `ATR_PERIOD` días, que no tienen suficiente historia)."""
    dates = [p["date"] for p in prices]
    highs = [p["high"] for p in prices]
    lows = [p["low"] for p in prices]
    closes = [p["close"] for p in prices]
    volumes = [p.get("volume") for p in prices]

    vwap = rolling_vwap_series(dates, highs, lows, closes, volumes, window_days)
    atr = _atr_series(highs, lows, closes, ATR_PERIOD)

    out: list[float | None] = []
    for i, close in enumerate(closes):
        atr_i = atr.iloc[i]
        if vwap[i] is None or atr_i is None or atr_i != atr_i or atr_i <= 0:  # atr_i != atr_i => NaN
            out.append(None)
        else:
            out.append((close - vwap[i]) / float(atr_i))
    return out


def build_condition(distances: list[float | None], side: str, threshold: float) -> list[bool]:
    if side == "arriba":
        return [d is not None and d >= threshold for d in distances]
    return [d is not None and d <= -threshold for d in distances]


def _gap_signs(result: OOSResult) -> set[int]:
    """Signos de todos los gaps calculables (train y test, los 4 horizontes). Un conjunto con más
    de un elemento significa "a veces mejor, a veces peor que el promedio" — no una señal."""
    signs = set()
    for h in result.horizons:
        for gap in (h.train_gap, h.test_gap):
            if gap is not None and gap != 0:
                signs.add(1 if gap > 0 else -1)
    return signs


def evaluate_combo(
    dates: list[str], closes: list[float], distances: list[float | None], side: str
) -> tuple[str, dict[str, OOSResult], list[bool] | None]:
    """Corre el barrido de umbrales para un (ventana, lado) y devuelve el veredicto, los
    resultados crudos y —si validó— la condición del umbral del medio, para la etapa 2."""
    variants = {
        f"{t:.1f} ATR": build_condition(distances, side, t) for t in DISTANCE_THRESHOLDS_ATR
    }
    results = run_oos_validation_sweep(
        dates, closes, variants, horizons_days=HORIZONS_DAYS, min_observations=MIN_OBSERVATIONS
    )

    all_pass = all(r.all_validated for r in results.values())
    signs = set()
    for r in results.values():
        signs |= _gap_signs(r)

    if all_pass and len(signs) == 1:
        verdict = "VALIDADO"
    elif any(r.all_validated for r in results.values()):
        verdict = "FRAGIL"
    else:
        verdict = "NO VALIDADO"

    middle = DISTANCE_THRESHOLDS_ATR[len(DISTANCE_THRESHOLDS_ATR) // 2]
    condition = variants[f"{middle:.1f} ATR"] if verdict == "VALIDADO" else None
    return verdict, results, condition


def check_redundancy(dates: list[str], closes: list[float], condition: list[bool]) -> None:
    """Etapa 2: ¿la señal agrega algo DENTRO del régimen de tendencia que la app ya usa?

    Se compara "régimen X + señal" contra "régimen X solo" (no contra todos los días) — para eso
    está `baseline_condition` en `run_oos_validation`. Si el gap se achica a casi nada, la señal
    está diciendo lo mismo que el régimen con otras palabras."""
    regimes = classify_regime_series(closes)
    for regime in ("fuerte", "debil"):
        baseline = [r == regime for r in regimes]
        intersection = [c and b for c, b in zip(condition, baseline)]
        if sum(intersection) < MIN_OBSERVATIONS:
            print(f"      regimen '{regime}': muy pocos dias en la interseccion ({sum(intersection)}) para decidir")
            continue
        result = run_oos_validation(
            dates,
            closes,
            intersection,
            horizons_days=HORIZONS_DAYS,
            min_observations=MIN_OBSERVATIONS,
            baseline_condition=baseline,
        )
        marca = "[OK]" if result.all_validated else "[FAIL]"
        print(f"      regimen '{regime}': {marca} agrega informacion sobre el regimen solo")
        for line in result.summary().splitlines():
            print(f"         {line}")


def run_for_ticker(ticker: str) -> dict[str, set[tuple[int, str]]]:
    symbol = CRYPTO_BINANCE_SYMBOLS[ticker]
    prices, meta = binance_client.get_historical_prices(symbol)
    prices = sorted(prices, key=lambda p: p["date"])
    dates = [p["date"] for p in prices]
    closes = [p["close"] for p in prices]
    cache_note = " (desde cache, no se pudo actualizar en vivo)" if meta.get("from_cache") else ""

    print("=" * 78)
    print(f"{ticker} ({symbol}) — {len(closes)} dias, {dates[0]} a {dates[-1]}{cache_note}")
    print("=" * 78)

    validated: set[tuple[int, str]] = set()
    for window in VWAP_WINDOWS_DAYS:
        distances = distance_to_vwap_in_atr(prices, window)
        computable = sum(1 for d in distances if d is not None)
        print(f"\n  VWAP de {window} dias ({computable} dias con distancia calculable)")
        for side in SIDES:
            verdict, results, condition = evaluate_combo(dates, closes, distances, side)
            print(f"    precio {side.upper()} del VWAP -> {verdict}")
            for label, result in results.items():
                for line in result.summary().splitlines():
                    print(f"      [{label}] {line}")
            if condition is not None:
                # Signo del efecto, en palabras: un gap positivo con el precio ABAJO del VWAP es
                # reversion (vuelve al promedio); negativo es momentum (sigue de largo).
                sign = next(iter(_gap_signs(results[f"{DISTANCE_THRESHOLDS_ATR[len(DISTANCE_THRESHOLDS_ATR) // 2]:.1f} ATR"])))
                if side == "abajo":
                    lectura = "reversion (lejos por debajo -> retorno MAYOR)" if sign > 0 else "momentum (lejos por debajo -> retorno MENOR)"
                else:
                    lectura = "momentum (lejos por encima -> retorno MAYOR)" if sign > 0 else "reversion (lejos por encima -> retorno MENOR)"
                print(f"      lectura: {lectura}")
                print("      etapa 2 — redundancia contra el regimen de tendencia:")
                check_redundancy(dates, closes, condition)
                validated.add((window, side))
    return {ticker: validated}


def main(argv: list[str]) -> int:
    tickers = argv[1:] or list(CRYPTO_BINANCE_SYMBOLS.keys())
    desconocidos = [t for t in tickers if t not in CRYPTO_BINANCE_SYMBOLS]
    if desconocidos:
        print(f"Ticker(s) desconocido(s): {', '.join(desconocidos)}. Disponibles: {', '.join(CRYPTO_BINANCE_SYMBOLS)}")
        return 2

    combos: dict[str, set[tuple[int, str]]] = {}
    for ticker in tickers:
        combos.update(run_for_ticker(ticker))

    print("\n" + "=" * 78)
    print("RESULTADO")
    print("=" * 78)
    if not any(combos.values()):
        print("Nada valido bajo los 3 criterios. `VWAP_VALIDATED_COMBOS` se queda vacio y la")
        print("seccion '🎯 VWAP' se queda descriptiva — que es exactamente el resultado que")
        print("tuvieron Fibonacci, ADX, OBV y las dos rondas del score del Zone Engine. NO")
        print("aflojar los umbrales ni sacar un horizonte para forzar que algo pase.")
    else:
        print("Pegar en src/ui/cripto.py SOLO los combos que ademas pasaron la etapa 2")
        print("(los que no agregan informacion sobre el regimen son redundantes, no señales):")
        print()
        print("VWAP_VALIDATED_COMBOS = {")
        for ticker, valid in combos.items():
            items = ", ".join(f"({w}, {side!r})" for w, side in sorted(valid))
            print(f"    {ticker!r}: {{{items}}},")
        print("}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
