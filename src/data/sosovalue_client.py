"""Cliente de la API oficial de SoSoValue (openapi.sosovalue.com) — ETFs spot cripto listados en
EE. UU. (BTC, ETH, SOL — cualquier `symbol` que SoSoValue soporte, no hardcodeado a una sola
moneda): lista de fondos, historia diaria de flujos/AUM (últimos ~30 días, límite de la API, no
de este cliente) y snapshot de hoy (AUM, flujo neto, prima/descuento vs. NAV, expense ratio).

Empezó acotado a Solana (2026-08-16, primera versión de este archivo) porque la exploración que
lo motivó arrancó ahí, pero el endpoint `/etfs` de SoSoValue es genérico por `symbol` — confirmado
llamándolo con `"BTC"`/`"ETH"` además de `"SOL"`, los 3 devuelven datos reales — así que
`get_etf_list()` quedó parametrizado por símbolo desde el principio en vez de encerrarse en
Solana con un nombre que después habría que romper.

Reemplaza a un scraper exploratorio de solanafloor.com/etf-tracker (2026-08-16, Solana-only):
esa página no tiene API pública — lo que exponía era el JSON interno que Next.js renderiza en el
servidor (un `self.__next_f.push([...])` RSC, sin versionar, sin ToS de reuso claro). SoSoValue
expone la misma información (y algo más: prima/descuento, expense ratio) bajo una API REST
documentada, con tier gratuito (20 req/min, 100.000 req/mes por key — sosovalue.com/developer).
Cross-validado contra ese scraper el mismo día, solo para Solana (el scraper nunca cubrió BTC/
ETH): mismo universo de 9 tickers, mismo AUM total (~$900M) y la misma anomalía real en TSOL
(flujo acumulado muy negativo pese a AUM chico, probablemente un fondo que heredó/convirtió de
otro vehículo) — dos fuentes independientes coincidiendo hace más creíble que sea un dato real y
no un glitch de una sola fuente. BTC/ETH no tienen ese cruce porque nunca hubo un scraper de esos.

Todavía NO está conectado a ningún tab de la app — es solo el cliente de datos, mismo alcance
que se acordó explícitamente ("provider real" == este archivo + la key en `.env`), no una
sección nueva en Cripto/Portafolio. Ver `.claude/skills/financial-advisor-cripto/references/
design-history.md` si en algún momento se decide surfacearlo en la UI.

Mismo patrón de caché en disco que fmp_client.py/binance_client.py: la última respuesta buena se
guarda en `.cache/` (vía `src.data.cache`) y se usa como fallback si una llamada en vivo falla —
incluyendo el caso de `SOSOVALUE_API_KEY` ausente, que levanta `DataError` recién al llamar
(nunca al importar este módulo ni `src/config.py`, mismo criterio que `FMP_API_KEY`).
"""

import time

import requests

from src.config import SOSOVALUE_API_KEY, SOSOVALUE_BASE_URL
from src.data import cache
from src.data.errors import DataError

_session = requests.Session()
_NAMESPACE = "sosovalue"

# La API respeta 20 req/min por key, pero un lote de llamadas seguidas (lista + historia +
# snapshot por cada uno de los 9 tickers) puede pegarle igual al límite cerca del borde de la
# ventana de un minuto — confirmado en la exploración previa. Un reintento corto con backoff
# evita que eso rompa una corrida entera; no es una garantía de nunca ver 429, solo tolerancia
# a que pase una vez.
_MAX_RETRIES = 3
_RETRY_BACKOFF_SECONDS = 10


def _get(path: str, **params) -> tuple[dict | list, dict]:
    """Devuelve (data, meta). meta = {from_cache, fetched_at, error}."""
    if not SOSOVALUE_API_KEY:
        raise DataError("Falta SOSOVALUE_API_KEY (ver .env.example) para consultar SoSoValue.")

    cache_file = cache.file_for(_NAMESPACE, path, params)
    try:
        body = None
        for attempt in range(_MAX_RETRIES):
            resp = _session.get(
                f"{SOSOVALUE_BASE_URL}{path}",
                params=params,
                headers={"x-soso-api-key": SOSOVALUE_API_KEY},
                timeout=15,
            )
            if resp.status_code == 429 and attempt < _MAX_RETRIES - 1:
                time.sleep(_RETRY_BACKOFF_SECONDS * (attempt + 1))
                continue
            if resp.status_code != 200:
                raise DataError(f"SoSoValue respondió {resp.status_code} en {path}: {resp.text[:200]}")
            body = resp.json()
            break
        if body is None:
            raise DataError(f"SoSoValue: límite de rate persistente en {path} tras {_MAX_RETRIES} intentos")
        if body.get("code") != 0:
            raise DataError(f"SoSoValue: {body.get('message')} ({path})")
        data = body["data"]
    except (DataError, requests.RequestException) as exc:
        cached = cache.read(cache_file)
        if cached is not None:
            return cached["data"], {"from_cache": True, "fetched_at": cached["fetched_at"], "error": str(exc)}
        raise DataError(f"SoSoValue falló en {path}: {exc}") from exc

    fetched_at = cache.write(cache_file, data)
    return data, {"from_cache": False, "fetched_at": fetched_at, "error": None}


def get_etf_list(symbol: str, country_code: str = "US") -> tuple[list[dict], dict]:
    """Lista de ETFs spot de `symbol` (`"BTC"`/`"ETH"`/`"SOL"`, lo que SoSoValue soporte)
    listados en `country_code` — cada entrada trae ticker/name/exchange. Es la fuente de verdad
    del universo de tickers para este proveedor: a diferencia de `TICKERS`/
    `CRYPTO_BINANCE_SYMBOLS` en `config.py`, acá SÍ tiene sentido reflejar en vivo si SoSoValue
    suma o saca un fondo — este universo se mueve solo (se siguen aprobando ETFs cripto nuevos),
    y hardcodearlo lo dejaría desactualizado."""
    return _get("/etfs", symbol=symbol, country_code=country_code)


def get_etf_history(ticker: str) -> tuple[list[dict], dict]:
    """Historia diaria de flujos/AUM de un ETF puntual. La API solo devuelve el ÚLTIMO MES
    (limitación documentada por SoSoValue, no de este cliente) — para acumular más historial que
    eso hace falta correr esto periódicamente y persistir el resultado en otro lado (mismo
    patrón que `verdict_history.json`), no algo que este cliente resuelva por sí solo. Nota: el
    campo `volume` de cada entrada es un bug conocido y documentado por la propia SoSoValue —
    devuelve el turnover en USD (`value_traded`) en vez de volumen en unidades; para volumen
    real, usar `get_etf_market_snapshot()`."""
    return _get(f"/etfs/{ticker}/history")


def get_etf_market_snapshot(ticker: str) -> tuple[dict, dict]:
    """Snapshot de HOY de un ETF puntual: AUM (`net_assets`), flujo neto del día (`net_inflow`),
    flujo acumulado desde el inicio del fondo (`cum_inflow`), prima/descuento vs. NAV
    (`prem_dsc` — puede venir `None` si SoSoValue todavía no lo calculó para la sesión de hoy,
    visto en la exploración previa para los 9 fondos a la vez, no es un error de este cliente),
    expense ratio del emisor (`sponsor_fee`) y volumen real en unidades (a diferencia del
    `volume` de `get_etf_history()`, ver esa función)."""
    return _get(f"/etfs/{ticker}/market-snapshot")
