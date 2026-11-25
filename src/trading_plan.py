"""Plan de compra y venta del usuario (`portfolio_data/plan_compra_venta.json`) — cómputo puro.

El plan lo definió el usuario (pegado el 2026-11-25, 80 M COP repartidos en 8 CDIs de la BVC).
Acá solo se traduce a números: cuántas acciones comprar por ticker con el capital asignado, y en
qué precios en COP caen los tramos de venta y los controles de riesgo según el costo promedio
real (P) de cada posición. NO es una señal validada fuera de muestra: son reglas del usuario.
"""

import json
import math
from pathlib import Path

PLAN_FILE = Path(__file__).resolve().parent.parent / "portfolio_data" / "plan_compra_venta.json"

# Trii: 0,125% + IVA 19% = 0,14875% del monto, con mínimo de 7.437,50 COP por operación.
COMMISSION_RATE = 0.0014875
COMMISSION_MIN_COP = 7437.5


def load_plan(path: Path = PLAN_FILE) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def commission_cop(amount_cop: float) -> float:
    return max(COMMISSION_MIN_COP, amount_cop * COMMISSION_RATE)


def weights(plan: dict, mode: str) -> dict[str, float]:
    """`mode`: "base" | "agresiva". Pesos en fracción (suman 1)."""
    if mode == "agresiva":
        w = plan["aggressive_allocation"]["weights_percent"]
    else:
        w = {a["symbol"]: a["weight_percent"] for a in plan["base_allocation"]}
    return {t: p / 100 for t, p in w.items()}


def shares_for_budget(budget_cop: float, price_cop: float | None) -> int | None:
    """Máximo de acciones enteras que alcanza el presupuesto, comisión de compra incluida."""
    if not price_cop or price_cop <= 0:
        return None
    n = math.floor(budget_cop / price_cop)
    while n > 0 and n * price_cop + commission_cop(n * price_cop) > budget_cop:
        n -= 1
    return n


def allocation_rows(plan: dict, mode: str, prices: dict, held: dict) -> list[dict]:
    """Una fila por ticker del plan. `held`: {ticker: (acciones, costo promedio)}."""
    names = {a["symbol"]: a["instrument"] for a in plan["base_allocation"]}
    rows = []
    for ticker, w in weights(plan, mode).items():
        budget = plan["capital_total"] * w
        price = prices.get(ticker)
        shares_held, _ = held.get(ticker, (0, None))
        held_value = shares_held * price if price else None
        to_buy = shares_for_budget(budget, price)
        gap = budget - held_value if held_value is not None else None
        rows.append({
            "ticker": ticker, "name": names.get(ticker, ticker), "weight": w, "budget_cop": budget,
            "price_cop": price, "shares_for_budget": to_buy,
            "cost_for_budget_cop": (to_buy * price + commission_cop(to_buy * price)) if to_buy else None,
            "shares_held": shares_held, "held_value_cop": held_value, "gap_cop": gap,
            "shares_missing": shares_for_budget(gap, price) if gap and gap > 0 else 0,
        })
    return rows


def sale_levels(plan: dict, ticker: str, base_price: float, shares: int, current_price: float | None) -> list[dict]:
    """Niveles de venta y de riesgo en COP para `base_price` (P). `shares` reparte los % de venta
    sobre la posición actual (0 si todavía no se compró — entonces solo se muestran precios)."""
    rule = plan["sale_plan"]["by_ticker"].get(ticker, {})
    out = []
    for mult, pct in rule.get("take_profit", []):
        out.append({"kind": "venta", "mult": mult, "price_cop": base_price * mult,
                    "action": f"Vender {pct}% de la posición", "shares": round(shares * pct / 100) if shares else None})
    for mult, action in rule.get("risk_control", []):
        n = None
        if shares:
            n = shares if "Salir" in action else (round(shares / 2) if "50%" in action else None)
        out.append({"kind": "riesgo", "mult": mult, "price_cop": base_price * mult, "action": action, "shares": n})
    for lvl in out:
        lvl["distance"] = (lvl["price_cop"] / current_price - 1) if current_price else None
        if current_price is None:
            lvl["reached"] = None
        elif lvl["kind"] == "venta":
            lvl["reached"] = current_price >= lvl["price_cop"]
        else:
            lvl["reached"] = current_price <= lvl["price_cop"]
    return out
