"""Wrapper delgado sobre la API "stable" de Financial Modeling Prep.

Cada llamada cachea su última respuesta buena en disco (.cache/). Si la API
falla (límite de requests alcanzado, error de red, etc.) y hay una respuesta
cacheada disponible, se devuelve esa respuesta marcada como `from_cache=True`
en vez de romper. Solo se propaga el error si no existe ningún dato previo.

Además, si el mercado está cerrado (ver `market_hours.py`) y ya hay una respuesta cacheada,
directamente no se llama a la API — los datos no cambian fuera de horario, así que golpear a
FMP ahí solo gasta cuota del plan free sin ganar nada.
"""

import requests

from src.config import FMP_API_KEY, FMP_BASE_URL
from src.data import cache
from src.data.errors import DataError
from src.data.market_hours import is_market_open

_session = requests.Session()
_NAMESPACE = "fmp"


def _get(path: str, **params) -> tuple[list | dict, dict]:
    """Devuelve (data, meta). meta = {from_cache, fetched_at, error, market_closed}."""
    cache_file = cache.file_for(_NAMESPACE, path, params)

    if not is_market_open():
        cached = cache.read(cache_file)
        if cached is not None:
            return cached["data"], {
                "from_cache": True,
                "fetched_at": cached["fetched_at"],
                "error": None,
                "market_closed": True,
            }

    request_params = {**params, "apikey": FMP_API_KEY}
    try:
        resp = _session.get(f"{FMP_BASE_URL}/{path}", params=request_params, timeout=15)
        if resp.status_code != 200:
            raise DataError(f"FMP respondió {resp.status_code} en {path}: {resp.text[:200]}")
        data = resp.json()
        if isinstance(data, dict) and "Error Message" in data:
            raise DataError(data["Error Message"])
    except (DataError, requests.RequestException) as exc:
        cached = cache.read(cache_file)
        if cached is not None:
            return cached["data"], {
                "from_cache": True,
                "fetched_at": cached["fetched_at"],
                "error": str(exc),
                "market_closed": False,
            }
        raise

    fetched_at = cache.write(cache_file, data)
    return data, {"from_cache": False, "fetched_at": fetched_at, "error": None, "market_closed": False}


def get_quote(ticker: str) -> tuple[dict, dict]:
    data, meta = _get("quote", symbol=ticker)
    if not data:
        raise DataError(f"Sin cotización disponible para {ticker}")
    return data[0], meta


def get_profile(ticker: str) -> tuple[dict, dict]:
    data, meta = _get("profile", symbol=ticker)
    if not data:
        raise DataError(f"Sin perfil disponible para {ticker}")
    return data[0], meta


# el plan free de FMP limita 'limit' a un máximo de 5 en estos endpoints
def get_income_statement(ticker: str, limit: int = 5) -> tuple[list[dict], dict]:
    return _get("income-statement", symbol=ticker, period="annual", limit=limit)


def get_cash_flow_statement(ticker: str, limit: int = 5) -> tuple[list[dict], dict]:
    return _get("cash-flow-statement", symbol=ticker, period="annual", limit=limit)


def get_balance_sheet(ticker: str, limit: int = 5) -> tuple[list[dict], dict]:
    return _get("balance-sheet-statement", symbol=ticker, period="annual", limit=limit)


def get_key_metrics(ticker: str, limit: int = 5) -> tuple[list[dict], dict]:
    return _get("key-metrics", symbol=ticker, period="annual", limit=limit)


def get_historical_prices(ticker: str) -> tuple[list[dict], dict]:
    return _get("historical-price-eod/full", symbol=ticker)


def get_analyst_view(ticker: str) -> tuple[dict, dict]:
    # el endpoint de estimados/price-target de FMP está bloqueado en el plan free
    # (402 "Premium Query Parameter"); lo dejamos explícito en vez de fingir que no existe
    raise DataError("Estimados de analistas no disponibles en el plan free de FMP")
