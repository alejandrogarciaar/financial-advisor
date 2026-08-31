"""Validación OOS de la CAPA 1 de los Niveles Calculados — la envolvente de sesión — sobre
velas 1h. Parte 2 del estudio de `scripts/niveles_oos_validate.py` (capas 2/3/4, diarias,
resultado: 0/24 validan); la envolvente quedó fuera de esa corrida porque es intradía y
juzgarla con velas diarias mide otra cosa.

CORRER LOCAL (Binance bloquea IPs de datacenter, ver CLAUDE.md):

    python scripts/envolvente_oos_validate.py              # BTC, ETH y SOL
    python scripts/envolvente_oos_validate.py BTC

QUÉ SE PRUEBA
La envolvente es la capa MÁS verificada en reproducción (30 anillos al dólar contra los
gráficos del canal, en BTC y ETH) — pero reproducción no es predicción. Señal por hora t,
sin look-ahead: el centro es la apertura de la sesión UTC de t (el open de la primera vela
1h del día, conocido desde las 00:00), anillos = centro·(1 ± banda) con las bandas del
método (0.382/1/1.5/2%). Toque de soporte: algún anillo por debajo del cierre de la hora
anterior queda a menos de θ·ATR(14, horario, desplazado) del mínimo de la hora; resistencia
simétrico. θ barrido en {0.1, 0.25, 0.5} ATR. Retornos forward en HORAS (4/8/12/24) vs la
media incondicional del tramo, split cronológico 60/40 (`run_oos_validation` — es agnóstico
a la unidad del horizonte). ~2 años de velas 1h (~17.500 por moneda).

Sub-capas: la envolvente completa (9 niveles), SOLO el anillo ±0.382% ("Fibonacci", el que
la narrativa institucional señala), y solo los anillos exteriores (±1/1.5/2%).

CONTROL DE DENSIDAD: 40 envolventes placebo — centro desplazado ±0.7% y bandas escaladas
±35% (seed fija). Misma cantidad de niveles anclados al mismo día; si los anillos del
método capturan algo, su gap de test debe quedar en la cola de la distribución placebo.

CRITERIO (el de siempre): 4 horizontes con el signo esperado y consistente train/test, en
los 3 θ, y percentil placebo ≥90 (soporte) / ≤10 (resistencia). Si algo pasara, faltaría
el chequeo de redundancia antes de tocar la UI.
"""

from __future__ import annotations

import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.oos_validate import run_oos_validation
from src.config import CRYPTO_BINANCE_SYMBOLS
from src.data import binance_client
from src.speculation import atr_series

BANDS = (0.382, 1.0, 1.5, 2.0)
THRESHOLDS_ATR = (0.1, 0.25, 0.5)
HORIZONS_HOURS = [4, 8, 12, 24]
N_PLACEBOS = 40
PLACEBO_SEED = 20260830
WARMUP_HOURS = 48
LAYERS = ("completa", "anillo_0382", "exteriores")
SIDES = ("soporte", "resistencia")


def session_centers(prices: list[dict]) -> list[float]:
    """Centro (apertura de la sesión UTC) de cada vela 1h — conocido desde las 00:00."""
    out: list[float] = []
    day, center = None, None
    for c in prices:
        d = str(c["date"])[:10]
        if d != day:
            day, center = d, float(c["open"])
        out.append(center)
    return out


def layer_rings(center: float, layer: str, jitter: tuple[float, float] | None) -> list[float]:
    u, v = jitter if jitter else (0.0, 0.0)
    ctr = center * (1 + u)
    bands = [b * (1 + v) for b in BANDS]
    if layer == "anillo_0382":
        bands = bands[:1]
    elif layer == "exteriores":
        bands = bands[1:]
    rings = [ctr * (1 + s * b / 100) for b in bands for s in (1, -1)]
    if layer == "completa":
        rings.append(ctr)
    return rings


def touch_condition(
    prices: list[dict],
    centers: list[float],
    atr_prev: list[float | None],
    layer: str,
    side: str,
    theta: float,
    jitter: tuple[float, float] | None = None,
) -> list[bool]:
    cond = [False] * len(prices)
    for t in range(WARMUP_HOURS, len(prices)):
        a = atr_prev[t]
        if a is None or a <= 0:
            continue
        prev_close = float(prices[t - 1]["close"])
        lo, hi = float(prices[t]["low"]), float(prices[t]["high"])
        tol = theta * a
        for lvl in layer_rings(centers[t], layer, jitter):
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
    jitters = [(rng.uniform(-0.007, 0.007), rng.uniform(-0.35, 0.35)) for _ in range(N_PLACEBOS)]

    validated_any = []
    for ticker in tickers:
        symbol = CRYPTO_BINANCE_SYMBOLS.get(ticker, ticker)
        prices, _meta = binance_client.get_historical_prices_intraday_1h(symbol)
        dates = [str(c["date"]) for c in prices]
        closes = [float(c["close"]) for c in prices]
        atr = atr_series([float(c["high"]) for c in prices], [float(c["low"]) for c in prices], closes, 14)
        atr_prev = [None] + [float(x) if x == x else None for x in atr.tolist()][:-1]
        centers = session_centers(prices)

        print("=" * 78)
        print(f"{ticker} ({symbol}) - {len(prices)} velas 1h ({dates[0][:10]} a {dates[-1][:10]})")
        print("=" * 78)

        for layer in LAYERS:
            for side in SIDES:
                expected_pos = side == "soporte"
                results = {
                    theta: run_oos_validation(
                        dates, closes,
                        touch_condition(prices, centers, atr_prev, layer, side, theta),
                        HORIZONS_HOURS,
                    )
                    for theta in THRESHOLDS_ATR
                }
                per_theta_ok = {
                    theta: r.all_validated
                    and all(
                        (h.test_gap or 0) > 0 if expected_pos else (h.test_gap or 0) < 0
                        for h in r.horizons
                    )
                    for theta, r in results.items()
                }
                passed = all(per_theta_ok.values())

                theta0 = 0.25
                real_gap = mean_test_gap(results[theta0])
                placebo_gaps = []
                for j in jitters:
                    cond_p = touch_condition(prices, centers, atr_prev, layer, side, theta0, jitter=j)
                    g = mean_test_gap(run_oos_validation(dates, closes, cond_p, HORIZONS_HOURS))
                    if g is not None:
                        placebo_gaps.append(g)
                pct = (
                    100.0 * sum(1 for g in placebo_gaps if g < real_gap) / len(placebo_gaps)
                    if real_gap is not None and placebo_gaps
                    else None
                )
                placebo_ok = pct is not None and (pct >= 90 if expected_pos else pct <= 10)

                verdict = "VALIDADO" if (passed and placebo_ok) else "no valida"
                print(f"\n--- {layer} / {side}  ->  {verdict}")
                for theta, r in results.items():
                    n_touch = r.horizons[0].test_n + r.horizons[0].train_n
                    print(f"  θ={theta} ATR (toques~{n_touch}): {'consistente' if per_theta_ok[theta] else 'inconsistente/signo contrario'}")
                    print("    " + r.summary().replace("\n", "\n    "))
                if pct is not None:
                    print(f"  placebo densidad (θ=0.25): gap real {real_gap:+.3%} = percentil {pct:.0f} "
                          f"de {len(placebo_gaps)} envolventes falsas {'[extremo]' if placebo_ok else '[NO extremo -> densidad]'}")
                if passed and placebo_ok:
                    validated_any.append((ticker, layer, side))

    print("\n" + "=" * 78)
    if validated_any:
        print("PASARON criterios (falta chequeo de redundancia antes de tocar la UI):")
        for v in validated_any:
            print(f"  {v}")
    else:
        print("NADA valido: los toques de los anillos de la envolvente no anticipan el retorno")
        print("horario mejor que envolventes arbitrarias de igual densidad.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
