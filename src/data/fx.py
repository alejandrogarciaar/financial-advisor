"""Tasa de cambio USD/COP (TRM), independiente del proveedor de acciones activo (FMP o
yfinance). Usa yfinance porque ninguno de los dos expone forex propio para el plan free de
FMP; comparte la misma caché en disco que fmp_client/yfinance_client para poder seguir
mostrando la última TRM conocida si Yahoo falla puntualmente.
"""

import yfinance as yf

from src.data import cache
from src.data.errors import DataError

_NAMESPACE = "fx"
_USD_COP_TICKER = "COP=X"


def get_usd_cop_rate() -> tuple[float, dict]:
    """Devuelve (tasa, meta). `tasa` = cuántos pesos colombianos vale 1 dólar hoy."""

    def fetch() -> float:
        info = yf.Ticker(_USD_COP_TICKER).info
        rate = info.get("regularMarketPrice") or info.get("previousClose")
        if not rate:
            raise DataError("Sin cotización disponible para USD/COP en yfinance")
        return float(rate)

    cache_file = cache.file_for(_NAMESPACE, "usd-cop", {})
    try:
        rate = fetch()
    except Exception as exc:
        cached = cache.read(cache_file)
        if cached is not None:
            return cached["data"], {
                "from_cache": True,
                "fetched_at": cached["fetched_at"],
                "error": str(exc),
            }
        raise DataError(f"No se pudo obtener la TRM (USD/COP): {exc}") from exc

    fetched_at = cache.write(cache_file, rate)
    return rate, {"from_cache": False, "fetched_at": fetched_at, "error": None}
