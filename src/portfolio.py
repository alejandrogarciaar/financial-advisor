"""Gestión de capital: persistencia de las compras y ventas reales del usuario y cálculo de
rentabilidad (no realizada, sobre lo que todavía se tiene, y realizada, sobre lo que se vendió).
A diferencia de `.cache/` (respuestas de APIs, reconstruibles y por eso gitignoreadas),
`portfolio_data/` guarda datos que el usuario ingresó a mano y que no se pueden reconstruir si
se borran, así que vive en su propio archivo fuera de la caché.
"""

import json
from pathlib import Path

import pandas as pd

DATA_DIR = Path(__file__).resolve().parent.parent / "portfolio_data"
STORAGE_FILE = DATA_DIR / "purchases.json"
SALES_STORAGE_FILE = DATA_DIR / "sales.json"

COLUMNS = ["ticker", "shares", "price_cop", "commission_cop", "date"]

# Comisión fija por operación que cobra el bróker (compra o venta) — no es un valor de mercado
# que haya que recalcular, así que sirve como default fijo en filas nuevas, editable operación a
# operación (nunca retroactivo sobre lo ya guardado).
DEFAULT_COMMISSION_COP = 7438.0


def _load_movements(storage_file: Path) -> pd.DataFrame:
    if not storage_file.exists():
        return pd.DataFrame(columns=COLUMNS)
    records = json.loads(storage_file.read_text(encoding="utf-8"))
    for record in records:
        record.setdefault("commission_cop", DEFAULT_COMMISSION_COP)
    df = pd.DataFrame(records, columns=COLUMNS)
    if not df.empty:
        df["date"] = pd.to_datetime(df["date"]).dt.date
        df["shares"] = df["shares"].astype(int)
        df["price_cop"] = df["price_cop"].astype(float)
        df["commission_cop"] = df["commission_cop"].astype(float)
    return df


def _save_movements(storage_file: Path, df: pd.DataFrame) -> None:
    storage_file.parent.mkdir(exist_ok=True)
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
    storage_file.write_text(json.dumps(records, indent=2), encoding="utf-8")


def load_purchases() -> pd.DataFrame:
    return _load_movements(STORAGE_FILE)


def save_purchases(df: pd.DataFrame) -> None:
    _save_movements(STORAGE_FILE, df)


def load_sales() -> pd.DataFrame:
    return _load_movements(SALES_STORAGE_FILE)


def save_sales(df: pd.DataFrame) -> None:
    _save_movements(SALES_STORAGE_FILE, df)


def _validate_movement_fields(df: pd.DataFrame, valid_tickers: list[str], price_label: str) -> list[str]:
    """Reglas de negocio que el column_config del data_editor no puede garantizar por sí
    solo (p.ej. un paste puede colar un número fraccionario pese al step=1 de la UI). Compartida
    entre compras y ventas — misma forma de fila (ticker/shares/price_cop/commission_cop/date)."""
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
            errors.append(f"{label}: el precio de {price_label} (en pesos) debe ser mayor a 0.")
        commission = row.get("commission_cop")
        if pd.isna(commission) or commission < 0:
            errors.append(f"{label}: la comisión (en pesos) no puede ser negativa.")
        if pd.isna(row.get("date")):
            errors.append(f"{label}: falta la fecha.")
    return errors


def validate_purchases(df: pd.DataFrame, valid_tickers: list[str]) -> list[str]:
    return _validate_movement_fields(df, valid_tickers, "compra")


def validate_sales(df: pd.DataFrame, valid_tickers: list[str], purchases: pd.DataFrame) -> list[str]:
    """Además de los mismos chequeos de campo que las compras, no deja vender más acciones de
    las que efectivamente se compraron para ese ticker — la validación es agregada (no por lote:
    mismo criterio de costo promedio que el resto del módulo), así que dos filas de venta que en
    conjunto superan lo comprado se marcan juntas."""
    errors = _validate_movement_fields(df, valid_tickers, "venta")
    if errors or df.empty:
        return errors
    purchased_by_ticker = purchases.groupby("ticker")["shares"].sum() if not purchases.empty else pd.Series(dtype=int)
    sold_by_ticker = df.groupby("ticker")["shares"].sum()
    for ticker, sold in sold_by_ticker.items():
        purchased = int(purchased_by_ticker.get(ticker, 0))
        if int(sold) > purchased:
            errors.append(
                f"{ticker}: estás vendiendo {int(sold)} acción(es) pero solo compraste {purchased} en total."
            )
    return errors


def summarize_by_ticker(
    purchases: pd.DataFrame, sales: pd.DataFrame, current_prices_cop: dict
) -> pd.DataFrame:
    """Una fila por ticker TODAVÍA EN CARTERA (acciones compradas menos vendidas > 0) — un
    ticker completamente vendido desaparece de acá, su resultado vive en
    `realized_gains_summary()` en cambio. `avg_price_cop` es el costo promedio de TODAS las
    compras (costo promedio, no por lotes), y `invested_cop` es ese costo promedio aplicado
    solo a las acciones que quedan en cartera — no la suma histórica de todo lo comprado, que
    ya no representa lo que hay invertido hoy si hubo ventas parciales."""
    if purchases.empty:
        return pd.DataFrame()

    sold_by_ticker = sales.groupby("ticker")["shares"].sum() if not sales.empty else pd.Series(dtype=int)

    rows = []
    for ticker, group in purchases.groupby("ticker"):
        shares_purchased = int(group["shares"].sum())
        total_invested_cop = float((group["shares"] * group["price_cop"] + group["commission_cop"]).sum())
        avg_price_cop = total_invested_cop / shares_purchased if shares_purchased else 0.0

        shares = shares_purchased - int(sold_by_ticker.get(ticker, 0))
        if shares <= 0:
            continue
        invested_cop = avg_price_cop * shares

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


def realized_gains_summary(purchases: pd.DataFrame, sales: pd.DataFrame) -> pd.DataFrame:
    """Una fila por ticker con al menos una venta: ganancia realizada usando costo promedio de
    compra (no FIFO por lotes — mismo criterio que `avg_price_cop` en `summarize_by_ticker`) como
    base de costo, y el precio de venta NETO de la comisión de venta como ingreso — misma lógica
    simétrica que ya usa el lado de compra (`invested_cop` ya suma la comisión al costo), así que
    la comisión de venta reduce la ganancia acá en vez de quedar afuera del cálculo."""
    if sales.empty:
        return pd.DataFrame()

    rows = []
    for ticker, sale_group in sales.groupby("ticker"):
        ticker_purchases = purchases[purchases["ticker"] == ticker]
        shares_purchased = int(ticker_purchases["shares"].sum())
        invested_cop = float(
            (ticker_purchases["shares"] * ticker_purchases["price_cop"] + ticker_purchases["commission_cop"]).sum()
        )
        avg_buy_price_cop = invested_cop / shares_purchased if shares_purchased else 0.0

        shares_sold = int(sale_group["shares"].sum())
        gross_proceeds_cop = float((sale_group["shares"] * sale_group["price_cop"]).sum())
        sale_commission_cop = float(sale_group["commission_cop"].sum())
        net_proceeds_cop = gross_proceeds_cop - sale_commission_cop
        cost_basis_cop = avg_buy_price_cop * shares_sold
        realized_gain_cop = net_proceeds_cop - cost_basis_cop
        realized_gain_pct = realized_gain_cop / cost_basis_cop if cost_basis_cop else None

        rows.append(
            {
                "ticker": ticker,
                "shares_sold": shares_sold,
                "avg_buy_price_cop": avg_buy_price_cop,
                "net_sale_price_cop": net_proceeds_cop / shares_sold if shares_sold else None,
                "gross_proceeds_cop": gross_proceeds_cop,
                "sale_commission_cop": sale_commission_cop,
                "cost_basis_cop": cost_basis_cop,
                "net_proceeds_cop": net_proceeds_cop,
                "realized_gain_cop": realized_gain_cop,
                "realized_gain_pct": realized_gain_pct,
            }
        )
    return pd.DataFrame(rows)


def simulate_additional_purchase(
    purchases: pd.DataFrame,
    sales: pd.DataFrame,
    ticker: str,
    extra_shares: int,
    extra_price_cop: float,
    extra_commission_cop: float,
) -> dict:
    """Cuánto cambiaría el precio promedio de `ticker` si se sumara una compra hipotética —
    puro cálculo, no persiste nada. Sirve para planificar antes de cargarla de verdad.
    `current_avg_price_cop` es el costo promedio de TODO lo comprado (una venta parcial no
    cambia el costo promedio de las acciones que quedan, solo cuántas quedan), pero
    `current_shares`/`current_invested_cop` reflejan lo que efectivamente se tiene hoy (compradas
    menos vendidas), igual que `summarize_by_ticker`."""
    ticker_purchases = purchases[purchases["ticker"] == ticker]
    shares_purchased = int(ticker_purchases["shares"].sum())
    total_invested_cop = float(
        (ticker_purchases["shares"] * ticker_purchases["price_cop"] + ticker_purchases["commission_cop"]).sum()
    )
    current_avg_price_cop = total_invested_cop / shares_purchased if shares_purchased else None

    shares_sold = int(sales[sales["ticker"] == ticker]["shares"].sum()) if not sales.empty else 0
    current_shares = shares_purchased - shares_sold
    current_invested_cop = current_avg_price_cop * current_shares if current_avg_price_cop else 0.0

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


def commission_summary(purchases: pd.DataFrame, sales: pd.DataFrame) -> dict:
    """Cuánto se pagó en comisiones en total (compra + venta) y qué proporción representa del
    capital invertido — costo real que reduce la rentabilidad, no una proyección de nada.
    `total_invested_cop` sigue siendo solo el lado de compra (lo que efectivamente se puso), así
    que `pct_of_invested` mide comisión total contra ese capital, no contra el volumen bruto
    operado (compra + venta)."""
    total_buy_commission_cop = float(purchases["commission_cop"].sum()) if not purchases.empty else 0.0
    total_sale_commission_cop = float(sales["commission_cop"].sum()) if not sales.empty else 0.0
    total_commission_cop = total_buy_commission_cop + total_sale_commission_cop
    total_invested_cop = (
        float((purchases["shares"] * purchases["price_cop"] + purchases["commission_cop"]).sum())
        if not purchases.empty
        else 0.0
    )
    num_purchases = len(purchases)
    num_sales = len(sales)
    num_movements = num_purchases + num_sales
    return {
        "num_purchases": num_purchases,
        "num_sales": num_sales,
        "total_commission_cop": total_commission_cop,
        "total_buy_commission_cop": total_buy_commission_cop,
        "total_sale_commission_cop": total_sale_commission_cop,
        "total_invested_cop": total_invested_cop,
        "pct_of_invested": total_commission_cop / total_invested_cop if total_invested_cop else None,
        "avg_commission_cop": total_commission_cop / num_movements if num_movements else None,
    }
