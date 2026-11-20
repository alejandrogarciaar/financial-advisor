"""Datos on-chain de BTC para el panel "🧱 ¿Fondo de ciclo?" de la pestaña Cripto — sin API key.

- CoinMetrics Community API: precio diario y MVRV (capitalización de mercado / capitalización
  realizada) desde 2010. De MVRV sale el "precio realizado" (precio / MVRV): el costo promedio
  al que se movieron por última vez todos los BTC en circulación.
- blockchain.com: hashrate diario de la red desde 2009 (para el "hash ribbon").

Mismo patrón de caché-y-fallback que el resto de `src/data/*.py`: la última respuesta buena se
guarda en disco (`src.data.cache`) y se reusa si la llamada en vivo falla.
"""

from datetime import datetime, timezone

import requests

from src.data import cache
from src.data.errors import DataError

_NAMESPACE = "onchain"
_COINMETRICS_URL = "https://community-api.coinmetrics.io/v4/timeseries/asset-metrics"
_HASHRATE_URL = "https://api.blockchain.info/charts/hash-rate"


def _with_cache(path: str, fetch_fn):
    cache_file = cache.file_for(_NAMESPACE, path, {})
    try:
        data = fetch_fn()
        if not data:
            raise DataError(f"respuesta vacía en {path}")
    except (DataError, requests.RequestException, KeyError, ValueError) as exc:
        cached = cache.read(cache_file)
        if cached is not None:
            return cached["data"], {"from_cache": True, "fetched_at": cached["fetched_at"], "error": str(exc)}
        raise DataError(f"Falló la consulta on-chain {path}: {exc}") from exc
    fetched_at = cache.write(cache_file, data)
    return data, {"from_cache": False, "fetched_at": fetched_at, "error": None}


def get_btc_price_mvrv_history() -> tuple[list[dict], dict]:
    """`[{"date": "YYYY-MM-DD", "price": float, "mvrv": float}, ...]` diario desde 2010-07-18."""

    def fetch():
        rows, params = [], {
            "assets": "btc", "metrics": "CapMVRVCur,PriceUSD", "frequency": "1d",
            "page_size": 10000, "start_time": "2010-07-18",
        }
        url = _COINMETRICS_URL
        while url:
            resp = requests.get(url, params=params, timeout=60)
            if resp.status_code != 200:
                raise DataError(f"CoinMetrics respondió {resp.status_code}: {resp.text[:200]}")
            payload = resp.json()
            for r in payload["data"]:
                if r.get("PriceUSD") and r.get("CapMVRVCur"):
                    rows.append({"date": r["time"][:10], "price": float(r["PriceUSD"]), "mvrv": float(r["CapMVRVCur"])})
            url, params = payload.get("next_page_url"), None
        return rows

    return _with_cache("btc-price-mvrv", fetch)


def get_btc_hashrate_history() -> tuple[list[dict], dict]:
    """`[{"date": "YYYY-MM-DD", "hashrate": float (TH/s)}, ...]` diario."""

    def fetch():
        resp = requests.get(_HASHRATE_URL, params={"timespan": "all", "format": "json", "sampled": "false"}, timeout=60)
        if resp.status_code != 200:
            raise DataError(f"blockchain.com respondió {resp.status_code}: {resp.text[:200]}")
        return [
            {"date": datetime.fromtimestamp(v["x"], tz=timezone.utc).strftime("%Y-%m-%d"), "hashrate": float(v["y"])}
            for v in resp.json()["values"]
        ]

    return _with_cache("btc-hashrate", fetch)
