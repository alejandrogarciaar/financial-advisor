"""Plan de compra y venta del usuario (`portfolio_data/plan_compra_venta.json`) — cómputo puro.

El plan lo definió el usuario (pegado el 2026-11-25, 80 M COP repartidos en 8 CDIs de la BVC).
Acá solo se traduce a números: cuántas acciones comprar por ticker con el capital asignado, y en
qué precios en COP caen los tramos de venta y las recompras para promediar a la baja según el costo promedio
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
    """Niveles de venta (sobre P) y de compra para promediar a la baja (bajo P), en COP. `shares`
    reparte los % sobre la posición actual (0 si todavía no se compró — solo se muestran precios)."""
    rule = plan["sale_plan"]["by_ticker"].get(ticker, {})
    out = []
    # Reparto acumulado: redondear cada tramo por separado puede sumar más o menos que la
    # posición (p. ej. 3 acciones al 25/25/50 → 1/1/2 = 4); así los tramos siempre suman `shares`.
    cum_pct, cum_shares = 0, 0
    for mult, pct in rule.get("take_profit", []):
        cum_pct += pct
        n = None
        if shares:
            target = round(shares * cum_pct / 100 + 1e-9)
            n, cum_shares = target - cum_shares, target
        out.append({"kind": "venta", "mult": mult, "price_cop": base_price * mult, "pct": pct,
                    "action": f"Vender {pct}% de la posición", "shares": n})
    # Sin ventas en pérdida (decisión del usuario, 2026-11-25): al caer a P × nivel se COMPRA el %
    # indicado de la posición que haya en ese momento, y se muestra cómo queda el costo promedio.
    held_now, avg_now = shares, base_price
    for mult, pct in rule.get("average_down", []):
        price = base_price * mult
        lvl = {"kind": "compra", "mult": mult, "price_cop": price, "pct": pct,
               "action": f"Comprar {pct}% más", "shares": None, "cost_cop": None, "new_avg_cop": None}
        if shares:
            n = max(1, round(held_now * pct / 100))
            cost = n * price + commission_cop(n * price)
            avg_now = (held_now * avg_now + cost) / (held_now + n)
            held_now += n
            lvl.update(shares=n, cost_cop=cost, new_avg_cop=avg_now)
        out.append(lvl)
    for lvl in out:
        lvl["distance"] = (lvl["price_cop"] / current_price - 1) if current_price else None
        if current_price is None:
            lvl["reached"] = None
        elif lvl["kind"] == "venta":
            lvl["reached"] = current_price >= lvl["price_cop"]
        else:
            lvl["reached"] = current_price <= lvl["price_cop"]
    return out


def position_start_date(purchases, sales, ticker: str):
    """Fecha en que arrancó la posición ACTUAL de `ticker` (la última vez que pasó de 0 a >0
    acciones), o `None` si no hay acciones. Mismo orden que el ledger de costo promedio: por
    fecha, luego hora si existe, compra antes que venta."""
    events = []
    for df, kind in ((purchases, 0), (sales, 1)):
        if df is None or df.empty:
            continue
        for r in df[df["ticker"] == ticker].itertuples():
            t = getattr(r, "time", None)
            events.append((r.date, t if isinstance(t, str) else "", kind, int(r.shares)))
    held, start = 0, None
    for d, _, kind, n in sorted(events):
        if kind == 0:
            if held == 0:
                start = d
            held += n
        else:
            held -= n
    return start if held > 0 else None


def runner_level(plan: dict, ticker: str, avg_cost: float, runner_shares: int | None,
                 high_since_start: float | None, current_price: float | None) -> dict | None:
    """El último tramo de venta sin techo: se activa cuando el precio (máximo desde que arrancó la
    posición) toca el PENÚLTIMO objetivo; desde ahí se vende solo si cae `trailing_drop_percent`
    desde ese máximo, con piso en costo × (1 + min_gain) — nunca en pérdida."""
    tps = plan["sale_plan"]["by_ticker"].get(ticker, {}).get("take_profit", [])
    cfg = plan["sale_plan"].get("runner")
    if len(tps) < 2 or not cfg:
        return None
    activation = avg_cost * tps[-2][0]
    floor = avg_cost * (1 + cfg["min_gain_over_cost_percent"] / 100)
    high = max(x for x in (high_since_start, current_price) if x) if (high_since_start or current_price) else None
    active = bool(high and high >= activation)
    stop = max(high * (1 - cfg["trailing_drop_percent"] / 100), floor) if active else None
    return {
        "pct": tps[-1][1], "shares": runner_shares, "activation_mult": tps[-2][0], "activation_cop": activation,
        "drop": cfg["trailing_drop_percent"] / 100, "active": active, "high_cop": high, "stop_cop": stop,
        "triggered": bool(active and current_price and current_price <= stop),
    }


def cycle_cash(plan: dict, purchases, sales) -> float:
    """Efectivo del ciclo: lo neto recibido por ventas de acciones del plan desde `start_date`,
    menos lo que ya se volvió a comprar en acciones del plan DESPUÉS de la primera de esas ventas
    (reinversión o promediar a la baja). Nunca negativo."""
    start = plan.get("start_date")
    tickers = {a["symbol"] for a in plan["base_allocation"]}
    if sales is None or sales.empty or not start:
        return 0.0
    s = sales[sales["ticker"].isin(tickers) & (sales["date"].astype(str) >= start)]
    if s.empty:
        return 0.0
    proceeds = float((s["shares"] * s["price_cop"] - s["commission_cop"]).sum())
    first_sale = str(s["date"].min())
    p = purchases[purchases["ticker"].isin(tickers) & (purchases["date"].astype(str) > first_sale)] if not purchases.empty else purchases
    spent = float((p["shares"] * p["price_cop"] + p["commission_cop"]).sum()) if not p.empty else 0.0
    return max(proceeds - spent, 0.0)


def reinvest_plan(plan: dict, mode: str, cash: float, prices: dict, held: dict) -> list[dict]:
    """Reparte `cash` en acciones enteras hacia los pesos objetivo, empezando por la acción más
    por debajo de su peso. El objetivo se mide sobre el valor ACTUAL del plan (posiciones a precio
    de hoy + efectivo), no sobre los 80 M iniciales, para que las ganancias también se repartan."""
    value = {t: held.get(t, (0, None))[0] * (prices.get(t) or 0) for t in weights(plan, mode)}
    total = sum(value.values()) + cash
    rows = []
    for t, w in weights(plan, mode).items():
        rows.append({"ticker": t, "weight": w, "value_cop": value[t], "target_cop": total * w,
                     "gap_cop": total * w - value[t], "price_cop": prices.get(t), "buy_shares": 0, "buy_cost_cop": 0.0})
    left = cash
    for r in sorted(rows, key=lambda r: r["gap_cop"], reverse=True):
        if r["gap_cop"] <= 0 or not r["price_cop"]:
            continue
        n = shares_for_budget(min(r["gap_cop"], left), r["price_cop"]) or 0
        if n:
            cost = n * r["price_cop"] + commission_cop(n * r["price_cop"])
            r["buy_shares"], r["buy_cost_cop"] = n, cost
            left -= cost
    return rows
