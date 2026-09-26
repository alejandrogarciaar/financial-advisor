"""Cliente de Bitstamp (OHLC público, sin API key) — fuente de la sección "📐 Niveles calculados"
de la pestaña "🪙 Cripto", y SOLO de esa sección.

Por qué una segunda fuente cripto: los gráficos de Crecetrader que esa sección reproduce están
hechos sobre `BTCUSD` de TradingView, que es el feed de Bitstamp. Binance (`BTCUSDT`) cotiza unos
dólares distinto, así que el ancla y el techo del impulso salían corridos ~$20-65 y ningún nivel
calzaba al dólar. Con las velas de Bitstamp, el 26-sep-2026 la rejilla diaria reproduce los 6
niveles publicados (67553 / 70008 / 72463 / 82281 / 88418 / 94554) con diferencias de $1 o menos.
El resto de la pestaña (indicadores, Zone Engine, VWAP...) sigue en Binance, que es sobre lo que
se validó todo lo que tiene validación.

Mismo patrón de caché que `binance_client.py`: la última respuesta buena queda en `.cache/` y se
usa como fallback si la llamada en vivo falla.
"""

from datetime import datetime, timedelta, timezone

import requests

from src.data import cache
from src.data.errors import DataError

_OHLC_URL = "https://www.bitstamp.net/api/v2/ohlc/{pair}/"
_session = requests.Session()
_NAMESPACE = "bitstamp"

# Bitstamp limita cada respuesta a 1000 velas: 5 años de diario (~1825) requieren paginar.
_MAX_CANDLES_PER_REQUEST = 1000
_DAY_SECONDS = 86400

# Ticker de la app → par de Bitstamp.
BITSTAMP_PAIRS = {"BTC": "btcusd", "ETH": "ethusd", "SOL": "solusd"}


def _fetch_daily(pair: str, start_s: int, end_s: int) -> list[dict]:
    # Con `start` y `end` juntos Bitstamp ignora `start` y devuelve las ULTIMAS `limit` velas
    # antes de `end` (verificado 26-sep-2026: pedir 5 anos devolvia 1000 velas desde 2024). Por
    # eso se pagina hacia atras moviendo solo `end`.
    candles: list[dict] = []
    cursor_end = end_s
    while cursor_end > start_s:
        resp = _session.get(
            _OHLC_URL.format(pair=pair),
            params={"step": _DAY_SECONDS, "end": cursor_end, "limit": _MAX_CANDLES_PER_REQUEST},
            timeout=15,
        )
        if resp.status_code != 200:
            raise DataError(f"Bitstamp respondió {resp.status_code} en ohlc {pair}: {resp.text[:200]}")
        batch = resp.json().get("data", {}).get("ohlc", [])
        if not batch:
            break
        candles.extend(batch)
        first_ts = min(int(c["timestamp"]) for c in batch)
        if len(batch) < _MAX_CANDLES_PER_REQUEST or first_ts >= cursor_end:
            break
        cursor_end = first_ts - _DAY_SECONDS
    # Las paginas pueden solaparse en el borde: dedupe por timestamp, en orden, y recorte a `start`.
    unique = {int(c["timestamp"]): c for c in candles if int(c["timestamp"]) >= start_s}
    return [unique[t] for t in sorted(unique)]


def get_historical_prices(ticker: str, years_back: float = 5.0) -> tuple[list[dict], dict]:
    """Velas diarias (00:00 UTC) — mismo shape que `binance_client.get_historical_prices()`."""
    if ticker not in BITSTAMP_PAIRS:
        raise ValueError(f"'{ticker}' no está en BITSTAMP_PAIRS: {sorted(BITSTAMP_PAIRS)}")
    pair = BITSTAMP_PAIRS[ticker]
    cache_file = cache.file_for(_NAMESPACE, "ohlc-1d", {"pair": pair, "years": years_back})
    try:
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=int(years_back * 365))
        raw = _fetch_daily(pair, int(start.timestamp()), int(end.timestamp()))
        if not raw:
            raise DataError(f"Bitstamp no devolvió velas para {pair}")
        data = [
            {
                "date": datetime.fromtimestamp(int(c["timestamp"]), tz=timezone.utc).strftime("%Y-%m-%d"),
                "open": float(c["open"]),
                "high": float(c["high"]),
                "low": float(c["low"]),
                "close": float(c["close"]),
                "volume": float(c["volume"]),
            }
            for c in raw
        ]
    except (DataError, requests.RequestException, ValueError, KeyError) as exc:
        cached = cache.read(cache_file)
        if cached is not None:
            return cached["data"], {"from_cache": True, "fetched_at": cached["fetched_at"], "error": str(exc)}
        raise DataError(f"Bitstamp falló en ohlc {pair}: {exc}") from exc

    fetched_at = cache.write(cache_file, data)
    return data, {"from_cache": False, "fetched_at": fetched_at, "error": None}
