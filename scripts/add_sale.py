"""Agrega una venta a portfolio_data/sales.json desde la terminal, con la misma validación que
la tabla "Tus ventas" de la Portafolio tab (validate_sales: no vender más de lo comprado, etc.)
— para poder registrar una venta que el usuario dicta por chat sin pasar por el navegador ni
editar el JSON a mano.

Uso:
    ./venv/Scripts/python.exe scripts/add_sale.py TICKER SHARES PRICE_COP COMMISSION_COP FECHA

Ejemplo:
    ./venv/Scripts/python.exe scripts/add_sale.py GOOGLCO 2 1200000 7438 2026-08-01
"""

import sys
from datetime import date as date_cls
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

from src.config import PORTFOLIO_CDI_TICKERS
from src.portfolio import load_purchases, load_sales, save_sales, validate_sales


def main() -> int:
    if len(sys.argv) != 6:
        print("Uso: add_sale.py TICKER SHARES PRICE_COP COMMISSION_COP FECHA(YYYY-MM-DD)")
        return 1

    ticker, shares_str, price_str, commission_str, date_str = sys.argv[1:]
    try:
        new_row = pd.DataFrame(
            [
                {
                    "ticker": ticker,
                    "shares": int(shares_str),
                    "price_cop": float(price_str),
                    "commission_cop": float(commission_str),
                    "date": date_cls.fromisoformat(date_str),
                }
            ]
        )
    except ValueError as exc:
        print(f"No se guardó nada — argumento inválido: {exc}")
        return 1

    purchases = load_purchases()
    sales = load_sales()
    candidate = pd.concat([sales, new_row], ignore_index=True)

    errors = validate_sales(candidate, list(PORTFOLIO_CDI_TICKERS.keys()), purchases)
    if errors:
        print("No se guardó nada — hay errores:")
        for err in errors:
            print(f"  - {err}")
        return 1

    save_sales(candidate)
    print(
        f"Guardado: vendiste {int(shares_str)} acción(es) de {ticker} a "
        f"${float(price_str):,.0f} COP (comisión ${float(commission_str):,.0f} COP) el {date_str}."
    )
    print()
    print(f"Ventas registradas para {ticker} ahora:")
    print(candidate[candidate["ticker"] == ticker].to_string(index=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
