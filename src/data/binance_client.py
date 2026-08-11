"""Cliente de Binance (klines públicos, sin API key) — fuente de datos para BTC/ETH/SOL en la
pestaña "🪙 Cripto" (indicadores de especulación + el motor multi-metodología de soportes/
resistencias, `src/support_resistance.py`). Especulación (acciones) se queda con yfinance — es
una pestaña solo-acciones, no usa este cliente en absoluto.

Por qué acá y no en yfinance: yfinance tapa el diario en "5y" y no tiene intervalo nativo de 4h
(hay que reagregar barras de 60m, que Yahoo limita a los últimos ~730 días). Binance ofrece
klines nativos de 4h (sin reagregar nada) y devuelve todo el historial del par listado — BTC/ETH
desde 2017, SOL desde su listado (~2020) — muy por encima de los ~5 años que pidió el usuario.

Mismo patrón de caché que fmp_client.py/yfinance_client.py: la última respuesta buena se guarda
en disco (`.cache/`, vía `src.data.cache`) y se usa como fallback si una llamada en vivo falla.
"""

from datetime import datetime, timedelta, timezone

import requests

from src.data import cache
from src.data.errors import DataError

_KLINES_URL = "https://api.binance.com/api/v3/klines"
_session = requests.Session()
_NAMESPACE = "binance"

# Binance limita cada respuesta a 1000 velas sin importar el intervalo — para "5 años o más" de
# diario (~1825 velas) o de 4h (~10950 velas) hace falta paginar avanzando startTime.
_MAX_KLINES_PER_REQUEST = 1000


def _fetch_klines(symbol: str, interval: str, start_ms: int, end_ms: int) -> list[list]:
    klines: list[list] = []
    cursor = start_ms
    while cursor < end_ms:
        resp = _session.get(
            _KLINES_URL,
            params={
                "symbol": symbol,
                "interval": interval,
                "startTime": cursor,
                "endTime": end_ms,
                "limit": _MAX_KLINES_PER_REQUEST,
            },
            timeout=15,
        )
        if resp.status_code != 200:
            raise DataError(f"Binance respondió {resp.status_code} en klines {symbol}/{interval}: {resp.text[:200]}")
        batch = resp.json()
        if not batch:
            break
        klines.extend(batch)
        cursor = batch[-1][0] + 1  # próximo startTime = open_time de la última vela + 1ms
        if len(batch) < _MAX_KLINES_PER_REQUEST:
            break  # esa página no vino llena — no hay más velas en el rango
    return klines


def _klines_to_prices(klines: list[list], date_fmt: str) -> list[dict]:
    # Formato de kline de Binance: [open_time, open, high, low, close, volume, close_time, ...]
    return [
        {
            "date": datetime.fromtimestamp(k[0] / 1000, tz=timezone.utc).strftime(date_fmt),
            "open": float(k[1]),
            "high": float(k[2]),
            "low": float(k[3]),
            "close": float(k[4]),
            "volume": float(k[5]),
        }
        for k in klines
    ]


def _get_klines(symbol: str, interval: str, date_fmt: str, years_back: float) -> tuple[list[dict], dict]:
    cache_file = cache.file_for(_NAMESPACE, f"klines-{interval}", {"symbol": symbol, "years": years_back})
    try:
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=int(years_back * 365))
        klines = _fetch_klines(symbol, interval, int(start.timestamp() * 1000), int(end.timestamp() * 1000))
        if not klines:
            raise DataError(f"Binance no devolvió velas para {symbol}/{interval}")
        data = _klines_to_prices(klines, date_fmt)
    except (DataError, requests.RequestException) as exc:
        cached = cache.read(cache_file)
        if cached is not None:
            return cached["data"], {"from_cache": True, "fetched_at": cached["fetched_at"], "error": str(exc)}
        raise DataError(f"Binance falló en klines {symbol}/{interval}: {exc}") from exc

    fetched_at = cache.write(cache_file, data)
    return data, {"from_cache": False, "fetched_at": fetched_at, "error": None}


def get_historical_prices(symbol: str, years_back: float = 5.0) -> tuple[list[dict], dict]:
    """Velas diarias — mismo shape que yfinance_client.get_historical_prices() (date en
    "YYYY-MM-DD", un cierre por día)."""
    return _get_klines(symbol, "1d", "%Y-%m-%d", years_back)


def get_historical_prices_intraday_4h(symbol: str, years_back: float = 5.0) -> tuple[list[dict], dict]:
    """Velas de 4h NATIVAS de Binance — a diferencia de
    yfinance_client.get_historical_prices_intraday_4h() (que reagrega barras de 60m porque
    yfinance no tiene un intervalo de 4h), acá Binance ya las da armadas, sin reagregar nada, y
    sin el tope de ~730 días que Yahoo impone a las barras de 60m. "date" incluye la hora
    ("YYYY-MM-DD HH:MM:SS") porque puede haber varias velas de 4h en el mismo día calendario."""
    return _get_klines(symbol, "4h", "%Y-%m-%d %H:%M:%S", years_back)


def get_historical_prices_intraday_1h(symbol: str, years_back: float = 2.0) -> tuple[list[dict], dict]:
    """Velas de 1h nativas — temporalidad "operativa" del Market Reaction Zone Engine
    (`src/support_resistance.py`). `years_back` por defecto es más chico que el de 4h/diario
    (2 años en vez de 5): a este intervalo, 5 años implicarían ~44 requests paginados (Binance
    limita cada respuesta a 1000 velas), y una temporalidad "operativa" de corto plazo no
    necesita ese historial completo de todos modos."""
    return _get_klines(symbol, "1h", "%Y-%m-%d %H:%M:%S", years_back)


# Intervalos nativos de Binance, del más fino al más grueso — Binance no tiene el tope de
# historia de yfinance en ninguno de estos, así que "todas las temporalidades" es un ejercicio
# real acá (no lo es para acciones vía yfinance, ver scripts/oos_validate.py y su
# run_timeframe_sweep()). Los 3 de arriba (daily/4h/1h) ya tenían su propia función con un
# years_back por defecto pensado para su caso de uso puntual (el motor de S/R); esta lista y
# get_historical_prices_multi_timeframe() son para pedir CUALQUIER otro intervalo sin agregar una
# función nueva por cada uno.
BINANCE_INTERVALS = ["1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "8h", "12h", "1d", "3d", "1w", "1M"]

_INTRADAY_INTERVALS = {"1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "8h", "12h"}


def get_historical_prices_multi_timeframe(symbol: str, interval: str, years_back: float) -> tuple[list[dict], dict]:
    """Genérico sobre cualquier intervalo nativo de Binance (`BINANCE_INTERVALS`) — a diferencia
    de las 3 funciones de arriba, sin un `years_back` por defecto a propósito: a temporalidades
    finas (1m/3m/5m), pedir "todo lo disponible" son miles de requests paginados (Binance limita
    cada respuesta a 1000 velas) — quien llama tiene que elegir una ventana consciente para el
    intervalo que está pidiendo, no heredar un default pensado para otro caso."""
    if interval not in BINANCE_INTERVALS:
        raise ValueError(f"Intervalo '{interval}' no es uno de los nativos de Binance: {BINANCE_INTERVALS}")
    date_fmt = "%Y-%m-%d %H:%M:%S" if interval in _INTRADAY_INTERVALS else "%Y-%m-%d"
    return _get_klines(symbol, interval, date_fmt, years_back)
