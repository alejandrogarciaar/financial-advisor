"""Cliente del Índice de Miedo y Codicia cripto (alternative.me, sin API key) — a diferencia del
resto de `src/data/*.py`, este NO pide un símbolo: es UN solo número para todo el mercado cripto
(no hay "Fear & Greed de BTC" vs. "de ETH"), así que la pestaña Cripto lo muestra como contenido
estático, independiente del ticker seleccionado.

Mismo patrón de caché-y-fallback que fmp_client.py/yfinance_client.py/binance_client.py
(`src.data.cache`): la última respuesta buena se guarda en disco y se reusa si la llamada en
vivo falla — nada nuevo, mismo criterio ya establecido en este proyecto.
"""

from datetime import datetime, timezone

import requests

from src.data import cache
from src.data.errors import DataError

_FNG_URL = "https://api.alternative.me/fng/"
_NAMESPACE = "feargreed"


def _datetime_from_unix(unix_ts: str) -> str:
    return datetime.fromtimestamp(int(unix_ts), tz=timezone.utc).strftime("%Y-%m-%d")


def get_fear_greed_index() -> tuple[dict, dict]:
    """Devuelve `{"value": int (0-100), "classification": str, "timestamp": "YYYY-MM-DD"}`.
    El índice de alternative.me se actualiza una vez por día — no hace falta pedir más de
    `limit=1` (el valor de hoy)."""
    cache_file = cache.file_for(_NAMESPACE, "fng", {"limit": 1})
    try:
        resp = requests.get(_FNG_URL, params={"limit": 1, "format": "json"}, timeout=15)
        if resp.status_code != 200:
            raise DataError(f"alternative.me respondió {resp.status_code}: {resp.text[:200]}")
        payload = resp.json()
        entry = payload["data"][0]
        data = {
            "value": int(entry["value"]),
            "classification": entry["value_classification"],
            "timestamp": _datetime_from_unix(entry["timestamp"]),
        }
    except (DataError, requests.RequestException, KeyError, IndexError, ValueError) as exc:
        cached = cache.read(cache_file)
        if cached is not None:
            return cached["data"], {"from_cache": True, "fetched_at": cached["fetched_at"], "error": str(exc)}
        raise DataError(f"alternative.me falló en el Índice de Miedo y Codicia: {exc}") from exc

    fetched_at = cache.write(cache_file, data)
    return data, {"from_cache": False, "fetched_at": fetched_at, "error": None}
