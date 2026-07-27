"""Gestión de capital: persistencia de las compras reales del usuario y cálculo de
rentabilidad. A diferencia de `.cache/` (respuestas de APIs, reconstruibles y por eso
gitignoreadas), `portfolio_data/` guarda datos que el usuario ingresó a mano y que no se
pueden reconstruir si se borran, así que vive en su propio archivo fuera de la caché.
"""

import json
from pathlib import Path

import pandas as pd

STORAGE_FILE = Path(__file__).resolve().parent.parent / "portfolio_data" / "purchases.json"

COLUMNS = ["ticker", "shares", "price_cop", "commission_cop", "date"]

# Comisión fija por compra que cobra el bróker — no es un valor de mercado que haya que
# recalcular, así que sirve como default fijo en filas nuevas, editable compra a compra
# (nunca retroactivo sobre compras ya guardadas).
DEFAULT_COMMISSION_COP = 7438.0


def load_purchases() -> pd.DataFrame:
    if not STORAGE_FILE.exists():
        return pd.DataFrame(columns=COLUMNS)
    records = json.loads(STORAGE_FILE.read_text(encoding="utf-8"))
    for record in records:
        record.setdefault("commission_cop", DEFAULT_COMMISSION_COP)
    df = pd.DataFrame(records, columns=COLUMNS)
    if not df.empty:
        df["date"] = pd.to_datetime(df["date"]).dt.date
        df["shares"] = df["shares"].astype(int)
        df["price_cop"] = df["price_cop"].astype(float)
        df["commission_cop"] = df["commission_cop"].astype(float)
    return df


def save_purchases(df: pd.DataFrame) -> None:
    STORAGE_FILE.parent.mkdir(exist_ok=True)
    records = [
        {
            "ticker": row.ticker,
            "shares": int(row.shares),
            "price_cop": float(row.price_cop),
            "commission_cop": float(row.commission_cop),
            "date": row.date.isoformat() if hasattr(row.date, "isoformat") else str(row.date),
        }
        for row in df.itertuples(index=False)
    ]
    STORAGE_FILE.write_text(json.dumps(records, indent=2), encoding="utf-8")


def validate_purchases(df: pd.DataFrame, valid_tickers: list[str]) -> list[str]:
    """Reglas de negocio que el column_config del data_editor no puede garantizar por sí
    solo (p.ej. un paste puede colar un número fraccionario pese al step=1 de la UI)."""
    errors = []
    for i, row in df.reset_index(drop=True).iterrows():
        label = f"Fila {i + 1}"
        ticker = row.get("ticker")
        if pd.isna(ticker) or ticker not in valid_tickers:
            errors.append(f"{label}: elegí un ticker válido.")
        shares = row.get("shares")
        if pd.isna(shares) or float(shares) != int(shares) or int(shares) < 1:
            errors.append(f"{label}: las acciones deben ser un número entero mayor a 0 (no se aceptan fracciones).")
        price = row.get("price_cop")
        if pd.isna(price) or price <= 0:
            errors.append(f"{label}: el precio de compra (en pesos) debe ser mayor a 0.")
        commission = row.get("commission_cop")
        if pd.isna(commission) or commission < 0:
            errors.append(f"{label}: la comisión (en pesos) no puede ser negativa.")
        if pd.isna(row.get("date")):
            errors.append(f"{label}: falta la fecha de compra.")
    return errors


def summarize_by_ticker(purchases: pd.DataFrame, current_prices_cop: dict) -> pd.DataFrame:
    """Una fila por ticker: acciones totales, precio promedio de compra e invertido — ambos
    en pesos, tal como se ingresaron (incluida la comisión de cada compra) — y valor
    actual/rentabilidad, a partir del precio actual en COP de cada CDI (ya nativo en pesos,
    sin necesidad de TRM)."""
    if purchases.empty:
        return pd.DataFrame()

    rows = []
    for ticker, group in purchases.groupby("ticker"):
        shares = int(group["shares"].sum())
        invested_cop = float((group["shares"] * group["price_cop"] + group["commission_cop"]).sum())
        avg_price_cop = invested_cop / shares if shares else 0.0
        current_price_cop = current_prices_cop.get(ticker)
        current_value_cop = shares * current_price_cop if current_price_cop is not None else None
        return_pct = (
            (current_value_cop - invested_cop) / invested_cop
            if current_value_cop is not None and invested_cop
            else None
        )
        rows.append(
            {
                "ticker": ticker,
                "shares": shares,
                "avg_price_cop": avg_price_cop,
                "invested_cop": invested_cop,
                "current_price_cop": current_price_cop,
                "current_value_cop": current_value_cop,
                "return_pct": return_pct,
            }
        )
    return pd.DataFrame(rows)


def simulate_additional_purchase(
    purchases: pd.DataFrame,
    ticker: str,
    extra_shares: int,
    extra_price_cop: float,
    extra_commission_cop: float,
) -> dict:
    """Cuánto cambiaría el precio promedio de `ticker` si se sumara una compra hipotética —
    puro cálculo, no persiste nada. Sirve para planificar antes de cargarla de verdad."""
    ticker_purchases = purchases[purchases["ticker"] == ticker]
    current_shares = int(ticker_purchases["shares"].sum())
    current_invested_cop = float(
        (ticker_purchases["shares"] * ticker_purchases["price_cop"] + ticker_purchases["commission_cop"]).sum()
    )
    current_avg_price_cop = current_invested_cop / current_shares if current_shares else None

    extra_invested_cop = extra_shares * extra_price_cop + extra_commission_cop
    new_shares = current_shares + extra_shares
    new_invested_cop = current_invested_cop + extra_invested_cop
    new_avg_price_cop = new_invested_cop / new_shares if new_shares else None

    return {
        "current_shares": current_shares,
        "current_avg_price_cop": current_avg_price_cop,
        "current_invested_cop": current_invested_cop,
        "new_shares": new_shares,
        "new_avg_price_cop": new_avg_price_cop,
        "new_invested_cop": new_invested_cop,
    }


def build_synthetic_portfolio_series(
    holdings_prices: dict[str, list[dict]], weights: dict[str, float]
) -> list[dict]:
    """Combina el historial de precios (del subyacente, en USD) de cada holding en un único
    índice sintético {date, close} ponderado por el peso ACTUAL de cada posición — asume que
    esa asignación se mantuvo constante durante todo el período, no reconstruye cuándo se
    compró cada cosa realmente (misma simplificación, documentada, que ya usa `backtest.py`
    para otra cosa). El resultado está listo para pasarle directo a
    `evaluate_risk_return()` — no hace falta ninguna matemática de riesgo nueva.

    `weights` no necesita sumar 1: se normaliza acá, así el caller puede pasar valores en COP
    tal cual sin calcular el total primero. Tickers ausentes de `weights` (peso 0) se ignoran."""
    tickers = [t for t in holdings_prices if weights.get(t, 0) > 0]
    if not tickers:
        return []
    total_weight = sum(weights[t] for t in tickers)
    normalized_weights = {t: weights[t] / total_weight for t in tickers}

    # Intersección de fechas: alguna posición puede cotizar en otra plaza (ej. CSPX en
    # Londres) y no calzar 1:1 con el resto día a día.
    common_dates = None
    for t in tickers:
        dates = {p["date"] for p in holdings_prices[t]}
        common_dates = dates if common_dates is None else common_dates & dates
    if not common_dates or len(common_dates) < 2:
        return []
    sorted_dates = sorted(common_dates)

    closes_by_ticker = {t: {p["date"]: p["close"] for p in holdings_prices[t]} for t in tickers}

    index_value = 100.0
    series = [{"date": sorted_dates[0], "close": index_value}]
    for i in range(1, len(sorted_dates)):
        prev_date, date = sorted_dates[i - 1], sorted_dates[i]
        weighted_return = sum(
            normalized_weights[t] * (closes_by_ticker[t][date] / closes_by_ticker[t][prev_date] - 1)
            for t in tickers
        )
        index_value *= 1 + weighted_return
        series.append({"date": date, "close": index_value})
    return series


def project_future_value(
    current_value: float, monthly_contribution: float, annual_rate: float, years: float
) -> float:
    """Valor futuro de un capital inicial + aportes mensuales, a una tasa anual compuesta
    mensualmente (fórmula estándar de capital inicial + anualidad ordinaria) — matemática
    determinística, no un modelo que haya que validar fuera de muestra. Qué tasa pasar (y si
    es razonable asumirla hacia adelante) es responsabilidad de quien llama a esta función."""
    months = round(years * 12)
    if months <= 0:
        return current_value
    monthly_rate = (1 + annual_rate) ** (1 / 12) - 1
    if monthly_rate == 0:
        return current_value + monthly_contribution * months
    future_value_lump = current_value * (1 + monthly_rate) ** months
    future_value_contributions = monthly_contribution * (((1 + monthly_rate) ** months - 1) / monthly_rate)
    return future_value_lump + future_value_contributions


def commission_summary(purchases: pd.DataFrame) -> dict:
    """Cuánto se pagó en comisiones en total y qué proporción representa del capital
    invertido — costo real que reduce la rentabilidad, no una proyección de nada."""
    if purchases.empty:
        return {
            "num_purchases": 0,
            "total_commission_cop": 0.0,
            "total_invested_cop": 0.0,
            "pct_of_invested": None,
            "avg_commission_cop": None,
        }
    total_commission_cop = float(purchases["commission_cop"].sum())
    total_invested_cop = float((purchases["shares"] * purchases["price_cop"] + purchases["commission_cop"]).sum())
    num_purchases = len(purchases)
    return {
        "num_purchases": num_purchases,
        "total_commission_cop": total_commission_cop,
        "total_invested_cop": total_invested_cop,
        "pct_of_invested": total_commission_cop / total_invested_cop if total_invested_cop else None,
        "avg_commission_cop": total_commission_cop / num_purchases if num_purchases else None,
    }
