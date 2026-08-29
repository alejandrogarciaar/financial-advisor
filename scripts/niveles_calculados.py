#!/usr/bin/env python3
"""
niveles_calculados.py
=====================

Calcula, desde la terminal, los mismos niveles que muestra la pestana interna
"Niveles calculados" de la app (Cripto y Especulacion).

AUTOCONTENIDO A PROPOSITO: un solo archivo, cero dependencias externas (solo
stdlib), sin importar nada de `src/`. Se puede copiar a cualquier carpeta o
maquina con Python 3.9+ y correr tal cual. Dentro de este repo, la fuente de
verdad para la app sigue siendo `src/niveles_calculados.py` (las tres formulas) y
`src/niveles_calculados_inputs.py` (la derivacion de las entradas); esto reproduce sus
numeros, verificado nivel por nivel contra los 11 tickers de la app.

Uso
---
    python niveles_calculados.py BTC
    python niveles_calculados.py AAPL
    python niveles_calculados.py ETH --capa diaria

    # replicar un grafico ajeno: se sobrescribe lo que haga falta
    python niveles_calculados.py BTC --precio 79744 --apertura 80279 \\
        --minimo-anual 57670 --rango-base 18294 --caida-macro 68537

    python niveles_calculados.py --help    # todas las opciones

Datos
-----
Cripto (BTC/ETH/SOL o cualquier par que termine en USDT) sale de la API publica
de Binance; cualquier otro simbolo, de la API publica de Yahoo Finance. Ninguna
de las dos pide API key. Binance responde 451 desde IPs de datacenter (nube),
asi que para cripto conviene correrlo desde una maquina propia.

Las tres capas
--------------
    1. ENVOLVENTE DE SESION
       centro = apertura diaria; anillos = centro * (1 +/- 0.382/1/1.5/2%)
    2. REJILLA DIARIA
       nivel = ancla + n * 25% del rango base   (con medio paso en 62.5%)
       ancla = minimo del ultimo ano; rango base = primer impulso desde ahi
    3. FRACCIONES MACRO
       nivel = ancla + n * 12.5% de la caida macro (techo de ciclo - ancla)

AVISO
-----
Reproduce COMO se generan los niveles; no implica que tengan poder predictivo.
Una rejilla densa "acierta" toques por construccion, y nada de esto se valido
fuera de muestra. No es asesoramiento de inversion.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone

# --------------------------------------------------------------- constantes --

ENVELOPE_BANDS = (0.382, 1.0, 1.5, 2.0)

DAILY_STEPS = (
    0.0, 25.0, 50.0, 62.5, 75.0, 100.0, 125.0, 150.0, 175.0, 200.0, 225.0, 250.0, 275.0,
)
DAILY_VERIFIED = frozenset({62.5, 125.0, 175.0, 225.0, 250.0, 275.0})

MACRO_STEPS = tuple(i * 12.5 for i in range(9))
MACRO_VERIFIED = frozenset({37.5})

CRYPTO_SYMBOLS = {"BTC": "BTCUSDT", "ETH": "ETHUSDT", "SOL": "SOLUSDT"}

USER_AGENT = "Mozilla/5.0 (niveles_calculados.py)"


# ------------------------------------------------------------------- datos --


def _http_json(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def fetch_binance(symbol: str, years_back: float = 5.0) -> list[dict]:
    """Velas diarias de Binance. Pagina de a 1000 (su tope por respuesta)."""
    end_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    start_ms = end_ms - int(years_back * 365.25 * 24 * 3600 * 1000)
    out: list[dict] = []
    while start_ms < end_ms:
        url = (
            "https://api.binance.com/api/v3/klines"
            f"?symbol={symbol}&interval=1d&startTime={start_ms}&endTime={end_ms}&limit=1000"
        )
        page = _http_json(url)
        if not page:
            break
        for k in page:
            out.append(
                {
                    "date": datetime.fromtimestamp(k[0] / 1000, timezone.utc).strftime("%Y-%m-%d"),
                    "open": float(k[1]),
                    "high": float(k[2]),
                    "low": float(k[3]),
                    "close": float(k[4]),
                }
            )
        if len(page) < 1000:
            break
        start_ms = page[-1][0] + 1
    return out


def fetch_yahoo(ticker: str, rng: str = "5y") -> list[dict]:
    """Velas diarias de Yahoo Finance (mismo endpoint que usa su propio grafico)."""
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?range={rng}&interval=1d"
    data = _http_json(url)
    result = (data.get("chart") or {}).get("result") or []
    if not result:
        raise RuntimeError(f"Yahoo no devolvio datos para {ticker}.")
    res = result[0]
    quote = res["indicators"]["quote"][0]
    out: list[dict] = []
    for i, ts in enumerate(res["timestamp"]):
        o, h, l, c = quote["open"][i], quote["high"][i], quote["low"][i], quote["close"][i]
        if None in (o, h, l, c):
            continue  # dias sin datos (feriados a medias, suspensiones)
        out.append(
            {
                "date": datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d"),
                "open": float(o),
                "high": float(h),
                "low": float(l),
                "close": float(c),
            }
        )
    return out


def load_candles(symbol: str, source: str) -> tuple[list[dict], str]:
    """Devuelve (velas, descripcion de la fuente)."""
    upper = symbol.upper()
    if source == "auto":
        source = "binance" if (upper in CRYPTO_SYMBOLS or upper.endswith("USDT")) else "yahoo"
    if source == "binance":
        pair = CRYPTO_SYMBOLS.get(upper, upper)
        return fetch_binance(pair), f"Binance {pair}"
    return fetch_yahoo(upper), f"Yahoo Finance {upper}"


# -------------------------------------------------------------- inferencia --


def _parse_date(raw):
    try:
        return date.fromisoformat(str(raw)[:10])
    except (TypeError, ValueError):
        return None


def _year_window_start(candles: list[dict], year_days: int) -> int:
    """Primera vela dentro de los ultimos `year_days` DIAS DE CALENDARIO.

    Contar velas seria un error en acciones: el mercado abre ~252 dias al ano,
    asi que las ultimas 365 velas son ~1.45 anos. En cripto da lo mismo.
    """
    last = _parse_date(candles[-1].get("date")) if candles else None
    if last is None:
        return max(0, len(candles) - year_days)
    cutoff = last - timedelta(days=year_days)
    for i, candle in enumerate(candles):
        parsed = _parse_date(candle.get("date"))
        if parsed is not None and parsed >= cutoff:
            return i
    return max(0, len(candles) - 1)


def infer_inputs(
    candles: list[dict],
    year_days: int = 365,
    retracement_pct: float = 50.0,
    min_reversal_pct: float = 15.0,
) -> dict:
    """Deriva las 5 entradas del calculo desde las velas diarias.

    Lo unico con margen de interpretacion es donde termina el primer impulso:
    se corta en el primer CIERRE que cumple las dos condiciones a la vez —
    devolver `retracement_pct` del avance acumulado Y estar `min_reversal_pct`
    por debajo del techo alcanzado. Sin la segunda, en cripto el 50% de un
    avance del 13% son 6.5% de precio y el impulso se cierra a los dos dias del
    minimo, con una amplitud que no representa nada.
    """
    if not candles:
        raise ValueError("Sin velas para calcular.")

    price = float(candles[-1]["close"])
    daily_open = float(candles[-1]["open"])

    window = candles[_year_window_start(candles, year_days) :]
    low_rel = min(range(len(window)), key=lambda i: float(window[i]["low"]))
    year_low = float(window[low_rel]["low"])
    if year_low <= 0:
        raise ValueError("El minimo anual debe ser positivo.")
    anchor_idx = len(candles) - len(window) + low_rel

    top = float(candles[anchor_idx]["high"])
    top_idx = anchor_idx
    impulse_open = True
    for i in range(anchor_idx + 1, len(candles)):
        high_i = float(candles[i]["high"])
        if high_i > top:
            top, top_idx = high_i, i
        advance = top - year_low
        close_i = float(candles[i]["close"])
        if close_i <= top - advance * retracement_pct / 100.0 and close_i <= top * (
            1 - min_reversal_pct / 100.0
        ):
            impulse_open = False
            break

    cycle_idx = max(range(len(candles)), key=lambda i: float(candles[i]["high"]))
    cycle_high = float(candles[cycle_idx]["high"])

    return {
        "price": price,
        "daily_open": daily_open,
        "year_low": year_low,
        "year_low_date": candles[anchor_idx]["date"],
        "base_range": top - year_low,
        "base_top": top,
        "base_top_date": candles[top_idx]["date"],
        "impulse_open": impulse_open,
        "macro_range": cycle_high - year_low,
        "cycle_high": cycle_high,
        "cycle_high_date": candles[cycle_idx]["date"],
        "n_candles": len(candles),
    }


# ---------------------------------------------------------------- las capas --


def _level(price, label, layer, role, verified, note):
    return {
        "price": price,
        "label": label,
        "layer": layer,
        "role": role,
        "verified": verified,
        "note": note,
    }


def session_envelope(center: float, reference: float) -> list[dict]:
    """Capa 1: anillos de +/-0.382/1/1.5/2% sobre la apertura diaria."""
    levels = [_level(center, "centro", "envelope", "neutro", True, "apertura diaria (00:00 UTC)")]
    for band in ENVELOPE_BANDS:
        for sign in (1, -1):
            price = center * (1 + sign * band / 100.0)
            levels.append(
                _level(
                    price,
                    f"{'+' if sign > 0 else '-'}{band}%",
                    "envelope",
                    "zona_venta" if price > reference else "zona_compra",
                    True,
                    "anillo Fibonacci" if band == 0.382 else "anillo de sesion",
                )
            )
    return sorted(levels, key=lambda lv: lv["price"], reverse=True)


def daily_grid(anchor: float, base_range: float) -> list[dict]:
    """Capa 2: pasos de 25% del rango base sobre el minimo anual."""
    levels = []
    for pct in DAILY_STEPS:
        if pct <= 75.0:
            role, note = "zona_compra", "refugio"
        elif pct >= 125.0:
            role, note = "zona_venta", "objetivo"
        else:
            role, note = "neutro", "extremo de la Fase 1"
        if pct == 125.0:
            note = "objetivo estrella"
        elif pct == 0.0:
            note = "ancla - minimo anual"
        levels.append(
            _level(
                anchor + base_range * pct / 100.0,
                f"{pct:g}%",
                "daily",
                role,
                pct in DAILY_VERIFIED,
                note,
            )
        )
    return sorted(levels, key=lambda lv: lv["price"], reverse=True)


def macro_grid(anchor: float, macro_range: float, reference: float) -> list[dict]:
    """Capa 3: octavos de la caida macro (techo de ciclo -> minimo anual)."""
    levels = []
    for pct in MACRO_STEPS:
        price = anchor + macro_range * pct / 100.0
        if price < reference * 0.995:
            role = "zona_compra"
        elif price > reference * 1.005:
            role = "zona_venta"
        else:
            role = "neutro"
        levels.append(
            _level(
                price,
                f"{pct:g}%",
                "macro",
                role,
                pct in MACRO_VERIFIED,
                f"fraccion {pct / 12.5:.0f}/8 de la caida macro",
            )
        )
    return sorted(levels, key=lambda lv: lv["price"], reverse=True)


def nearest_levels(levels: list[dict], price: float):
    """(nivel inmediatamente por debajo, nivel inmediatamente por encima)."""
    below = above = None
    for lv in levels:
        if lv["price"] <= price and (below is None or lv["price"] > below["price"]):
            below = lv
        if lv["price"] >= price and (above is None or lv["price"] < above["price"]):
            above = lv
    return below, above


def confluences(all_levels: list[dict], tolerance_pct: float = 0.15):
    """Pares de capas distintas que casi coinciden en precio."""
    pairs = []
    for i, a in enumerate(all_levels):
        for b in all_levels[i + 1 :]:
            if a["layer"] == b["layer"]:
                continue
            if abs(a["price"] - b["price"]) / a["price"] * 100.0 <= tolerance_pct:
                pairs.append((a, b))
    return pairs


# ---------------------------------------------------------------- impresion --

LAYER_TITLE = {
    "envelope": "CAPA 1 - envolvente de sesion (intradia)",
    "daily": "CAPA 2 - rejilla diaria",
    "macro": "CAPA 3 - fracciones macro (semanal)",
}


def format_levels(levels: list[dict], price: float, mark_nearest: bool = True) -> str:
    below, above = nearest_levels(levels, price) if mark_nearest else (None, None)
    rows = ["    nivel      precio          rol           dist.    nota"]
    for lv in levels:
        dist = (lv["price"] - price) / price * 100.0
        flag = "*" if lv["verified"] else " "
        near = ">" if lv is below or lv is above else " "
        rows.append(
            f"{near}{flag} {lv['label']:<9} {lv['price']:>13,.2f}  {lv['role']:<12} "
            f"{dist:+7.2f}%  {lv['note']}"
        )
    return "\n".join(rows)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Niveles calculados (3 capas) para una cripto o una accion.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Ejemplos:\n  python niveles_calculados.py BTC\n"
        "  python niveles_calculados.py AAPL --capa diaria\n"
        "  python niveles_calculados.py BTC --minimo-anual 57670 --rango-base 18294",
    )
    ap.add_argument("simbolo", help="BTC / ETH / SOL, un par de Binance (…USDT) o un ticker de Yahoo (AAPL).")
    ap.add_argument("--fuente", choices=["auto", "binance", "yahoo"], default="auto")
    ap.add_argument(
        "--capa",
        choices=["todas", "intradia", "diaria", "macro"],
        default="todas",
        help="Que capa imprimir (default: todas).",
    )
    ap.add_argument("--retroceso", type=float, default=50.0, help="%% del avance que cierra el primer impulso (default 50).")
    ap.add_argument("--giro-minimo", type=float, default=15.0, help="%% de caida desde el techo para que ese retroceso cuente (default 15).")
    ap.add_argument("--ventana-anual", type=int, default=365, help="Dias de calendario del 'minimo anual' (default 365).")
    ap.add_argument("--precio", type=float, help="Sobrescribe el precio actual.")
    ap.add_argument("--apertura", type=float, help="Sobrescribe la apertura diaria.")
    ap.add_argument("--minimo-anual", type=float, dest="minimo_anual", help="Sobrescribe el ancla.")
    ap.add_argument("--rango-base", type=float, dest="rango_base", help="Sobrescribe la amplitud del primer impulso.")
    ap.add_argument("--caida-macro", type=float, dest="caida_macro", help="Sobrescribe la caida macro.")
    args = ap.parse_args(argv)

    try:
        candles, source_label = load_candles(args.simbolo, args.fuente)
    except (urllib.error.URLError, urllib.error.HTTPError, RuntimeError) as exc:
        print(f"No se pudieron traer los datos de {args.simbolo}: {exc}", file=sys.stderr)
        if args.fuente != "yahoo":
            print("Binance responde 451 desde IPs de datacenter; proba --fuente yahoo.", file=sys.stderr)
        return 1
    if not candles:
        print(f"Sin velas para {args.simbolo}.", file=sys.stderr)
        return 1

    inputs = infer_inputs(candles, args.ventana_anual, args.retroceso, args.giro_minimo)
    price = args.precio if args.precio else inputs["price"]
    daily_open = args.apertura if args.apertura else inputs["daily_open"]
    anchor = args.minimo_anual if args.minimo_anual else inputs["year_low"]
    base_range = args.rango_base if args.rango_base else inputs["base_range"]
    macro_range = args.caida_macro if args.caida_macro else inputs["macro_range"]

    if min(price, daily_open, anchor, base_range, macro_range) <= 0:
        print("Alguna entrada quedo en cero o negativa; revisa los valores manuales.", file=sys.stderr)
        return 1

    line = "=" * 78
    print(line)
    print(f"NIVELES CALCULADOS - {args.simbolo.upper()}   ({source_label}, {candles[-1]['date']})")
    print(line)
    print(f"  precio actual        {price:>15,.2f}")
    print(f"  apertura diaria      {daily_open:>15,.2f}")
    print(f"  ancla (min. anual)   {anchor:>15,.2f}   {inputs['year_low_date']}")
    impulso = "impulso todavia abierto" if inputs["impulse_open"] else f"impulso hasta {inputs['base_top_date']}"
    print(f"  rango base           {base_range:>15,.2f}   techo {inputs['base_top']:,.2f} ({impulso})")
    print(f"  caida macro          {macro_range:>15,.2f}   techo de ciclo {inputs['cycle_high']:,.2f} ({inputs['cycle_high_date']})")
    print(f"  velas diarias        {inputs['n_candles']:>15,}")

    envelope = session_envelope(daily_open, price)
    daily = daily_grid(anchor, base_range)
    macro = macro_grid(anchor, macro_range, price)
    wanted = {
        "todas": ["envelope", "daily", "macro"],
        "intradia": ["envelope"],
        "diaria": ["daily"],
        "macro": ["macro"],
    }[args.capa]
    by_layer = {"envelope": envelope, "daily": daily, "macro": macro}

    for layer in wanted:
        print(f"\n{LAYER_TITLE[layer]}")
        print("-" * 78)
        print(format_levels(by_layer[layer], price))

    every = sorted(envelope + daily + macro, key=lambda lv: lv["price"], reverse=True)
    below, above = nearest_levels(every, price)
    print("\n" + "-" * 78)
    if below:
        print(f"  soporte mas proximo:     {below['price']:>13,.2f}   ({below['layer']} {below['label']}, {(below['price'] - price) / price * 100:+.2f}%)")
    if above:
        print(f"  resistencia mas proxima: {above['price']:>13,.2f}   ({above['layer']} {above['label']}, {(above['price'] - price) / price * 100:+.2f}%)")

    pairs = confluences(every)
    if pairs:
        print("\n  confluencias entre capas (menos de 0.15% de distancia):")
        for a, b in pairs:
            print(f"    {a['price']:>13,.2f}   {a['layer']} {a['label']}  ~  {b['layer']} {b['label']}")

    print("\n  (*) nivel verificado contra los graficos de referencia; (>) rodea al precio de hoy.")
    print("  Descriptivo, sin validacion fuera de muestra. No es asesoramiento de inversion.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
