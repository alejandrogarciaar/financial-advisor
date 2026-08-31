"""Validación fuera de muestra de los Niveles Calculados — ¿el precio reacciona en los
niveles del método más de lo que reaccionaría en niveles arbitrarios de igual densidad?

CORRER LOCAL, NO DESDE UNA SESIÓN REMOTA: Binance responde 403/451 a los proxies de los
entornos remotos (mismo bloqueo documentado en CLAUDE.md).

    python scripts/niveles_oos_validate.py              # BTC, ETH y SOL
    python scripts/niveles_oos_validate.py BTC          # uno solo

No modifica nada de la app: imprime el reporte. La sección "📐 Niveles calculados" se
publicó como descriptiva SIN validar (st.warning en la UI); este script es el estudio que
esa advertencia dice que falta. Mientras nada valide, la UI no cambia.

QUÉ SE PRUEBA
Señal por día t, construida SIN look-ahead (walk-forward): las capas 2 (rejilla 25%) y 3
(fracciones macro 12.5%) se recalculan cada día con `infer_inputs()` del CLI de referencia
(`scripts/niveles_calculados.py`, ancla estructural incluida) sobre las velas hasta t-1; el
eje semanal (capa 4) es la apertura del lunes de la semana de t, conocida al abrir t. La
capa 1 (envolvente intradía) queda FUERA de esta corrida: opera dentro de la sesión y
juzgarla con velas diarias mediría otra cosa — pendiente con velas 1h.

Condición "toque de soporte" de una capa: algún nivel de la capa por DEBAJO del cierre de
ayer queda a menos de θ·ATR(14, desplazado un día) del mínimo de hoy. "Toque de
resistencia": simétrico con el máximo. θ se barre en {0.1, 0.25, 0.5} ATR — el chequeo de
fragilidad que este proyecto siempre aplica. Resultado: retorno forward a 5/10/20/30 días
vs la media incondicional del tramo (run_oos_validation, split cronológico 60/40).

Signo esperado: soporte tocado → gap POSITIVO (rebote); resistencia tocada → gap NEGATIVO.
Un gap "validado" con el signo contrario se reporta como momentum, no como confirmación
del método (mismo criterio que el Índice de Miedo y Codicia).

CONTROL DE DENSIDAD (la objeción central, y lo nuevo de este estudio)
Una rejilla con 20+ niveles "acierta" toques por construcción. Por eso, además del gap vs
baseline, se corren K=40 rejillas PLACEBO: la misma construcción walk-forward pero con el
ancla y los rangos multiplicados por factores aleatorios fijos por rejilla (±12%, seed
fija). Si los niveles del método capturan algo real, su gap de test debería quedar en la
cola de la distribución placebo; si queda en el medio, el "efecto" es la densidad, no el
método. Se reporta el percentil.

CRITERIO PARA CANTAR "VALIDADO" (el de siempre, deliberadamente estricto)
1. Split cronológico 60/40; los 4 horizontes mantienen el signo entre train y test.
2. Los 3 umbrales θ dan lo mismo, con el signo ESPERADO por el rol del nivel.
3. El percentil placebo es extremo (≥90 para soporte, ≤10 para resistencia).
Si algo pasara 1-3, faltaría todavía el chequeo de redundancia contra el régimen de
tendencia (`classify_regime_series`) antes de tocar la UI — igual que VWAP y Fear & Greed.
"""

from __future__ import annotations

import random
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.niveles_calculados import DAILY_STEPS, MACRO_STEPS, infer_inputs
from scripts.oos_validate import run_oos_validation
from src.config import CRYPTO_BINANCE_SYMBOLS
from src.data import binance_client
from src.speculation import atr_series

WARMUP_DAYS = 400          # la ventana anual necesita historia antes del primer día evaluado
THRESHOLDS_ATR = (0.1, 0.25, 0.5)
HORIZONS_DAYS = [5, 10, 20, 30]
N_PLACEBOS = 40
PLACEBO_JITTER = 0.12      # ±12% sobre ancla/rangos: misma densidad, otra ubicación
PLACEBO_SEED = 20260830
LAYERS = ("rejilla", "macro", "eje", "todas")
SIDES = ("soporte", "resistencia")


def _parse(d: str) -> date:
    return date.fromisoformat(str(d)[:10])


def walk_forward_inputs(prices: list[dict]) -> list[dict | None]:
    """Entradas del método por día t, usando SOLO velas hasta t-1 (None en el warmup)."""
    out: list[dict | None] = [None] * len(prices)
    for t in range(WARMUP_DAYS, len(prices)):
        out[t] = infer_inputs(prices[:t])
    return out


def weekly_opens(prices: list[dict]) -> list[float]:
    """Apertura de la semana ISO de cada vela (conocida al abrir el día — sin look-ahead)."""
    out: list[float] = []
    week_open, week_monday = None, None
    for c in prices:
        d = _parse(c["date"])
        monday = d - timedelta(days=d.weekday())
        if monday != week_monday:
            week_monday, week_open = monday, float(c["open"])
        out.append(week_open)
    return out


def layer_levels(inp: dict, wk_open: float, layer: str, jitter: tuple[float, float] | None) -> list[float]:
    """Niveles de una capa para un día. `jitter=(u, v)` construye la versión placebo:
    ancla*(1+u) y rangos*(1+v) — misma cantidad de niveles, otra ubicación."""
    u, v = jitter if jitter else (0.0, 0.0)
    anchor = inp["year_low"] * (1 + u)
    base = inp["base_range"] * (1 + v)
    macro = inp["macro_range"] * (1 + v)
    eje = wk_open * (1 + u)
    if layer == "rejilla":
        return [anchor + base * p / 100 for p in DAILY_STEPS]
    if layer == "macro":
        return [anchor + macro * p / 100 for p in MACRO_STEPS]
    if layer == "eje":
        return [eje]
    return (
        [anchor + base * p / 100 for p in DAILY_STEPS]
        + [anchor + macro * p / 100 for p in MACRO_STEPS]
        + [eje]
    )


def touch_condition(
    prices: list[dict],
    inputs: list[dict | None],
    wk: list[float],
    atr_prev: list[float | None],
    layer: str,
    side: str,
    theta: float,
    jitter: tuple[float, float] | None = None,
) -> list[bool]:
    cond = [False] * len(prices)
    for t in range(WARMUP_DAYS, len(prices)):
        inp, a = inputs[t], atr_prev[t]
        if inp is None or a is None or a <= 0:
            continue
        prev_close = float(prices[t - 1]["close"])
        lo, hi = float(prices[t]["low"]), float(prices[t]["high"])
        tol = theta * a
        for lvl in layer_levels(inp, wk[t], layer, jitter):
            if side == "soporte":
                if lvl < prev_close and abs(lo - lvl) <= tol:
                    cond[t] = True
                    break
            else:
                if lvl > prev_close and abs(hi - lvl) <= tol:
                    cond[t] = True
                    break
    return cond


def mean_test_gap(result) -> float | None:
    gaps = [h.test_gap for h in result.horizons if h.test_gap is not None]
    return sum(gaps) / len(gaps) if gaps else None


def main(argv: list[str]) -> int:
    tickers = [a.upper() for a in argv] or list(CRYPTO_BINANCE_SYMBOLS)
    rng = random.Random(PLACEBO_SEED)
    jitters = [
        (rng.uniform(-PLACEBO_JITTER, PLACEBO_JITTER), rng.uniform(-PLACEBO_JITTER, PLACEBO_JITTER))
        for _ in range(N_PLACEBOS)
    ]

    validated_any = []
    for ticker in tickers:
        symbol = CRYPTO_BINANCE_SYMBOLS.get(ticker, ticker)
        prices, _meta = binance_client.get_historical_prices(symbol)
        dates = [str(c["date"]) for c in prices]
        closes = [float(c["close"]) for c in prices]
        atr = atr_series([float(c["high"]) for c in prices], [float(c["low"]) for c in prices], closes, 14)
        atr_prev = [None] + [float(x) if x == x else None for x in atr.tolist()][:-1]

        print("=" * 78)
        print(f"{ticker} ({symbol}) - {len(prices)} velas diarias, warmup {WARMUP_DAYS}")
        print("=" * 78)
        inputs = walk_forward_inputs(prices)
        wk = weekly_opens(prices)

        for layer in LAYERS:
            for side in SIDES:
                expected_pos = side == "soporte"
                results = {}
                for theta in THRESHOLDS_ATR:
                    cond = touch_condition(prices, inputs, wk, atr_prev, layer, side, theta)
                    results[theta] = run_oos_validation(dates, closes, cond, HORIZONS_DAYS)

                # criterio 1+2: los 4 horizontes consistentes en cada θ, signo esperado, 3 θ de acuerdo
                per_theta_ok = {
                    theta: r.all_validated
                    and all(
                        (h.test_gap or 0) > 0 if expected_pos else (h.test_gap or 0) < 0
                        for h in r.horizons
                    )
                    for theta, r in results.items()
                }
                passed = all(per_theta_ok.values())

                # placebo de densidad sobre θ=0.25 (el central)
                theta0 = 0.25
                real_gap = mean_test_gap(results[theta0])
                placebo_gaps = []
                for j in jitters:
                    cond_p = touch_condition(prices, inputs, wk, atr_prev, layer, side, theta0, jitter=j)
                    g = mean_test_gap(run_oos_validation(dates, closes, cond_p, HORIZONS_DAYS))
                    if g is not None:
                        placebo_gaps.append(g)
                if real_gap is not None and placebo_gaps:
                    pct = 100.0 * sum(1 for g in placebo_gaps if g < real_gap) / len(placebo_gaps)
                else:
                    pct = None
                placebo_ok = pct is not None and (pct >= 90 if expected_pos else pct <= 10)

                verdict = "VALIDADO" if (passed and placebo_ok) else "no valida"
                print(f"\n--- capa {layer} / {side}  ->  {verdict}")
                for theta, r in results.items():
                    n_touch = r.horizons[0].test_n + r.horizons[0].train_n
                    print(f"  θ={theta} ATR (toques~{n_touch}): {'consistente' if per_theta_ok[theta] else 'inconsistente/signo contrario'}")
                    print("    " + r.summary().replace("\n", "\n    "))
                if pct is not None:
                    print(f"  placebo densidad (θ=0.25): gap real {real_gap:+.2%} = percentil {pct:.0f} "
                          f"de {len(placebo_gaps)} rejillas falsas {'[extremo]' if placebo_ok else '[NO extremo -> densidad]'}")
                if passed and placebo_ok:
                    validated_any.append((ticker, layer, side))

    print("\n" + "=" * 78)
    if validated_any:
        print("PASARON criterios 1-3 (falta chequeo de redundancia antes de tocar la UI):")
        for v in validated_any:
            print(f"  {v}")
    else:
        print("NADA valido los 3 criterios: los toques de estos niveles no anticipan el retorno")
        print("forward mejor que rejillas arbitrarias de igual densidad. La seccion sigue")
        print("descriptiva, exactamente como esta publicada.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
