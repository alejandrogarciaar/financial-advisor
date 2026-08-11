"""Adaptador de yfinance a la misma forma de datos que usa fmp_client.

Expone las mismas 7 funciones que fmp_client (get_quote, get_profile,
get_income_statement, get_cash_flow_statement, get_balance_sheet,
get_key_metrics, get_historical_prices) devolviendo los mismos nombres de
campo (price, beta, eps, weightedAverageShsOut, freeCashFlow, netDebt,
earningsYield, date/close). Así fair_value.py no necesita saber qué
proveedor está usando. Usa la misma caché en disco que fmp_client para
poder seguir probando el dashboard aunque Yahoo también falle puntualmente.
"""

import pandas as pd
import yfinance as yf

from src.data import cache
from src.data.errors import DataError

_NAMESPACE = "yfinance"


def _ticker(ticker: str) -> yf.Ticker:
    return yf.Ticker(ticker)


def _to_float(value):
    if value is None or pd.isna(value):
        return None
    return float(value)


def _row(df: pd.DataFrame, row_name: str, col):
    return _to_float(df.loc[row_name, col]) if row_name in df.index else None


def _with_cache(path: str, params: dict, fetch_fn):
    cache_file = cache.file_for(_NAMESPACE, path, params)
    try:
        data = fetch_fn()
        if not data:
            raise DataError(f"yfinance no devolvió datos en {path} para {params}")
    except Exception as exc:
        cached = cache.read(cache_file)
        if cached is not None:
            return cached["data"], {
                "from_cache": True,
                "fetched_at": cached["fetched_at"],
                "error": str(exc),
            }
        raise DataError(f"yfinance falló en {path} para {params}: {exc}") from exc

    fetched_at = cache.write(cache_file, data)
    return data, {"from_cache": False, "fetched_at": fetched_at, "error": None}


def get_quote(ticker: str) -> tuple[dict, dict]:
    def fetch():
        info = _ticker(ticker).info
        price = info.get("currentPrice") or info.get("regularMarketPrice")
        if not price:
            raise DataError(f"Sin cotización disponible para {ticker} en yfinance")
        return {"price": float(price), "marketCap": info.get("marketCap")}

    return _with_cache("quote", {"symbol": ticker}, fetch)


def get_profile(ticker: str) -> tuple[dict, dict]:
    def fetch():
        info = _ticker(ticker).info
        # nombramos el campo igual que FMP ("lastDividend" = dividendo anual por acción)
        # aunque en yfinance viene de "dividendRate", para que fair_value.py sea agnóstico al proveedor
        return {"beta": info.get("beta"), "lastDividend": info.get("dividendRate")}

    return _with_cache("profile", {"symbol": ticker}, fetch)


def get_income_statement(ticker: str, limit: int = 5) -> tuple[list[dict], dict]:
    def fetch():
        inc = _ticker(ticker).income_stmt
        eps_row = "Diluted EPS" if "Diluted EPS" in inc.index else "Basic EPS"
        shares_row = (
            "Diluted Average Shares" if "Diluted Average Shares" in inc.index else "Basic Average Shares"
        )
        rows = []
        for col in inc.columns[:limit]:
            rows.append(
                {
                    "date": col.strftime("%Y-%m-%d"),
                    "eps": _row(inc, eps_row, col),
                    "weightedAverageShsOut": _row(inc, shares_row, col),
                    "ebit": _row(inc, "EBIT", col),
                    "ebitda": _row(inc, "EBITDA", col),
                    "interestExpense": _row(inc, "Interest Expense", col),
                    "incomeBeforeTax": _row(inc, "Pretax Income", col),
                    "incomeTaxExpense": _row(inc, "Tax Provision", col),
                }
            )
        return rows

    return _with_cache("income-statement", {"symbol": ticker, "limit": limit}, fetch)


def get_cash_flow_statement(ticker: str, limit: int = 5) -> tuple[list[dict], dict]:
    def fetch():
        cf = _ticker(ticker).cashflow
        return [
            {
                "date": col.strftime("%Y-%m-%d"),
                "freeCashFlow": _row(cf, "Free Cash Flow", col),
                "stockBasedCompensation": _row(cf, "Stock Based Compensation", col),
            }
            for col in cf.columns[:limit]
        ]

    return _with_cache("cash-flow-statement", {"symbol": ticker, "limit": limit}, fetch)


def get_balance_sheet(ticker: str, limit: int = 5) -> tuple[list[dict], dict]:
    def fetch():
        bs = _ticker(ticker).balance_sheet
        return [
            {
                "date": col.strftime("%Y-%m-%d"),
                "netDebt": _row(bs, "Net Debt", col),
                "totalStockholdersEquity": _row(bs, "Stockholders Equity", col),
                "totalDebt": _row(bs, "Total Debt", col),
                "cashAndCashEquivalents": _row(bs, "Cash And Cash Equivalents", col),
            }
            for col in bs.columns[:limit]
        ]

    return _with_cache("balance-sheet-statement", {"symbol": ticker, "limit": limit}, fetch)


def get_key_metrics(ticker: str, limit: int = 5) -> tuple[list[dict], dict]:
    """Reconstruye un P/E histórico por año: EPS anual / precio de cierre más
    cercano a la fecha de cierre de ese año fiscal (yfinance no expone P/E
    histórico directamente como sí lo hace el key-metrics de FMP)."""

    def fetch():
        t = _ticker(ticker)
        inc = t.income_stmt
        eps_row = "Diluted EPS" if "Diluted EPS" in inc.index else "Basic EPS"
        close = t.history(period="5y")["Close"]
        close.index = close.index.tz_localize(None)

        rows = []
        for col in inc.columns[:limit]:
            eps = _to_float(inc.loc[eps_row, col]) if eps_row in inc.index else None
            if not eps or eps <= 0 or close.empty:
                rows.append({"date": col.strftime("%Y-%m-%d"), "earningsYield": None})
                continue
            target = col.tz_localize(None) if col.tzinfo else col
            pos = close.index.get_indexer([target], method="nearest")[0]
            price = float(close.iloc[pos])
            rows.append({"date": col.strftime("%Y-%m-%d"), "earningsYield": eps / price})
        return rows

    return _with_cache("key-metrics", {"symbol": ticker, "limit": limit}, fetch)


def get_analyst_view(ticker: str) -> tuple[dict, dict]:
    """Consenso de analistas: precio objetivo y tendencia de revisión de estimados de EPS.
    No disponible en fmp_client (bloqueado en el plan free de FMP)."""

    def fetch():
        t = _ticker(ticker)
        targets = t.analyst_price_targets or {}

        forward_growth = None
        try:
            growth_df = t.growth_estimates
            if growth_df is not None and "0y" in growth_df.index and "stockTrend" in growth_df.columns:
                forward_growth = _to_float(growth_df.loc["0y", "stockTrend"])
        except Exception:
            pass

        eps_now = eps_90d = None
        try:
            trend_df = t.eps_trend
            if trend_df is not None and "0y" in trend_df.index:
                eps_now = _to_float(trend_df.loc["0y", "current"])
                eps_90d = _to_float(trend_df.loc["0y", "90daysAgo"])
        except Exception:
            pass

        return {
            "priceTargetMean": _to_float(targets.get("mean")),
            "priceTargetMedian": _to_float(targets.get("median")),
            "forwardGrowth": forward_growth,
            "epsEstimateNow": eps_now,
            "epsEstimate90dAgo": eps_90d,
        }

    return _with_cache("analyst-view", {"symbol": ticker}, fetch)


def get_etf_info(ticker: str) -> tuple[dict, dict]:
    """Datos de un ETF (no una empresa): sin estados financieros, así que se toma todo del
    `.info` de yfinance en vez de las funciones de income/balance/cash-flow usadas para acciones."""

    def fetch():
        info = _ticker(ticker).info
        price = info.get("regularMarketPrice") or info.get("currentPrice")
        if not price:
            raise DataError(f"Sin cotización disponible para {ticker} en yfinance")
        return {
            "longName": info.get("longName") or info.get("shortName"),
            "price": float(price),
            "currency": info.get("currency"),
            "trailingPE": info.get("trailingPE"),
            "epsTrailingTwelveMonths": info.get("epsTrailingTwelveMonths"),
            "netExpenseRatio": info.get("netExpenseRatio"),
            "fiftyTwoWeekHigh": info.get("fiftyTwoWeekHigh"),
            "fiftyTwoWeekLow": info.get("fiftyTwoWeekLow"),
            "allTimeHigh": info.get("allTimeHigh"),
        }

    return _with_cache("etf-info", {"symbol": ticker}, fetch)


def get_historical_prices(ticker: str) -> tuple[list[dict], dict]:
    def fetch():
        hist = _ticker(ticker).history(period="5y")
        # la barra del día en curso a veces viene con Close = NaN (la sesión de ese mercado
        # todavía no cerró cuando yfinance arma el historial) — se descarta acá, en la fuente,
        # para que ningún consumidor (EMA, SMA, CAGR, volatilidad) tenga que lidiar con eso.
        return [
            {
                "date": idx.strftime("%Y-%m-%d"),
                "open": float(row["Open"]),
                "close": float(row["Close"]),
                "high": float(row["High"]),
                "low": float(row["Low"]),
                "volume": float(row["Volume"]),
            }
            for idx, row in hist.iterrows()
            if pd.notna(row["Close"])
        ]

    return _with_cache("historical-price-eod-full", {"symbol": ticker}, fetch)


# Yahoo/yfinance recorta durísimo la historia intradía — a diferencia de Binance (sin ese tope
# para cripto, ver binance_client.py), acá "todas las temporalidades" NO es un ejercicio real
# para todas: 1m son 7 días, 2m/5m/15m/30m/90m son 60 días — insuficiente para un split 60/40 con
# muestra seria (mismo problema ya documentado para el bot de oro). Solo estas 4 tienen historia
# suficiente: 1h (~730 días, el tope real de Yahoo para 60m) y 1d/1wk/1mo (historia completa, sin
# tope). `get_historical_prices_multi_timeframe()` solo acepta estas — pedir 1m/5m/etc. acá sería
# prometer una muestra que yfinance no puede dar.
YFINANCE_VIABLE_INTERVALS = ["1h", "1d", "1wk", "1mo"]

# Período máximo real de Yahoo para 1h (60m); 1d/1wk/1mo no tienen ese tope, "max" trae todo.
_MAX_PERIOD_BY_INTERVAL = {"1h": "730d", "1d": "max", "1wk": "max", "1mo": "max"}


def get_historical_prices_multi_timeframe(ticker: str, interval: str) -> tuple[list[dict], dict]:
    """Genérico sobre las temporalidades de yfinance con historia suficiente
    (`YFINANCE_VIABLE_INTERVALS`) — usa automáticamente el período máximo real de Yahoo para esa
    temporalidad (`_MAX_PERIOD_BY_INTERVAL`), no un valor a elegir por quien llama (a diferencia
    de `binance_client.get_historical_prices_multi_timeframe()`, donde años_back sí es una
    decisión real porque Binance no tiene un tope que lo fije de antemano)."""
    if interval not in YFINANCE_VIABLE_INTERVALS:
        raise ValueError(
            f"'{interval}' no tiene historia suficiente en yfinance para un split 60/40 — "
            f"usar una de {YFINANCE_VIABLE_INTERVALS}."
        )
    date_fmt = "%Y-%m-%d %H:%M:%S" if interval == "1h" else "%Y-%m-%d"

    def fetch():
        hist = _ticker(ticker).history(period=_MAX_PERIOD_BY_INTERVAL[interval], interval=interval)
        return [
            {
                "date": idx.strftime(date_fmt),
                "open": float(row["Open"]),
                "close": float(row["Close"]),
                "high": float(row["High"]),
                "low": float(row["Low"]),
                "volume": float(row["Volume"]),
            }
            for idx, row in hist.iterrows()
            if pd.notna(row["Close"])
        ]

    return _with_cache("historical-price-multi-timeframe", {"symbol": ticker, "interval": interval}, fetch)
