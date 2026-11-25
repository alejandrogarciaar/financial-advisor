"""Sub-pestaña "🗺️ Plan de compra y venta" de Portafolio — el plan del usuario
(`portfolio_data/plan_compra_venta.json`) traducido a acciones y precios en COP con la cotización
de hoy y el costo promedio real de cada posición. Cómputo en `src/trading_plan.py`."""

import pandas as pd
import streamlit as st

from src.config import PORTFOLIO_CDI_TICKERS
from src.valuation.fair_value import PROVIDERS
from src.portfolio import summarize_by_ticker
from src.trading_plan import allocation_rows, commission_cop, load_plan, sale_levels
from src.ui.shared import _cached_portfolio_price, _parallel_fetch

LOW_LIQUIDITY_SHARES = 100  # volumen diario promedio (acciones) por debajo del cual se avisa


@st.cache_data(ttl=21600, show_spinner=False)
def _cached_avg_volume(ticker: str) -> tuple[float | None, float | None]:
    """(acciones/día, COP/día) promedio de las últimas 20 ruedas en la BVC."""
    rows, _ = PROVIDERS["yfinance"].get_historical_prices_multi_timeframe(PORTFOLIO_CDI_TICKERS[ticker], "1d")
    last = rows[-20:]
    if not last:
        return None, None
    return (sum(r["volume"] for r in last) / len(last), sum(r["volume"] * r["close"] for r in last) / len(last))


def _money(v) -> str:
    return "—" if v is None or pd.isna(v) else f"${v:,.0f}"


def render_trading_plan(purchases: pd.DataFrame, sales: pd.DataFrame) -> None:
    plan = load_plan()
    st.subheader(f"🗺️ {plan['plan_name']}")
    st.caption(
        f"{plan['objective']} · {plan['market']} · capital: **{_money(plan['capital_total'])} COP**. "
        "Son reglas definidas por vos, no una señal validada fuera de muestra como las de otras pestañas. "
        + plan["disclaimer"]
    )

    mode = st.radio(
        "Asignación", ["base", "agresiva"], horizontal=True, key="trading_plan_mode",
        format_func=lambda m: "Base" if m == "base" else "Agresiva (NVIDIA 30%)",
    )
    if mode == "agresiva":
        st.caption("⚠️ " + plan["aggressive_allocation"]["description"])

    tickers = [a["symbol"] for a in plan["base_allocation"]]
    jobs = {("price", t): (_cached_portfolio_price, (t,)) for t in tickers}
    jobs |= {("vol", t): (_cached_avg_volume, (t,)) for t in tickers}
    fetched = _parallel_fetch(jobs)
    prices = {t: fetched[("price", t)][0] for t in tickers}
    volumes = {t: fetched[("vol", t)][0] or (None, None) for t in tickers}

    summary = summarize_by_ticker(purchases, sales, prices)
    held = {}
    if not summary.empty:
        held = {r.ticker: (int(r.shares), float(r.avg_price_cop)) for r in summary.itertuples() if r.ticker in tickers}

    rows = allocation_rows(plan, mode, prices, held)

    # --- Compra ---
    st.markdown("#### 🛒 Compra: cuánto le toca a cada acción")
    total_budget = sum(r["budget_cop"] for r in rows)
    held_value = sum(r["held_value_cop"] or 0 for r in rows)
    missing = sum(max(r["gap_cop"] or 0, 0) for r in rows)
    m1, m2, m3 = st.columns(3)
    m1.metric("Capital del plan", _money(total_budget))
    m2.metric("Ya tenés en estas acciones (a precio de hoy)", _money(held_value))
    m3.metric("Falta invertir", _money(missing))

    table = []
    for r in rows:
        shares_vol, cop_vol = volumes[r["ticker"]]
        liquidity = "—" if shares_vol is None else f"{shares_vol:,.0f} acc · {_money(cop_vol)}"
        if shares_vol is not None and shares_vol < LOW_LIQUIDITY_SHARES:
            liquidity = "⚠️ " + liquidity
        table.append({
            "Acción": f"{r['ticker']} · {r['name']}",
            "Peso": f"{r['weight']:.0%}",
            "Monto objetivo": _money(r["budget_cop"]),
            "Precio hoy": _money(r["price_cop"]),
            "Acciones para el monto": "—" if r["shares_for_budget"] is None else f"{r['shares_for_budget']:,}",
            "Costo con comisión": _money(r["cost_for_budget_cop"]),
            "Ya tenés": f"{r['shares_held']:,} ({_money(r['held_value_cop'])})" if r["shares_held"] else "—",
            "Faltan por comprar": f"{r['shares_missing']:,}" if r["shares_missing"] else "✅ completo",
            "Volumen diario (20 ruedas)": liquidity,
        })
    st.dataframe(pd.DataFrame(table), hide_index=True, width="stretch")
    n_ops = sum(1 for r in rows if r["shares_missing"])
    st.caption(
        "Acciones enteras: el monto no se usa al peso exacto y el sobrante queda en efectivo. "
        f"Comisión Trii: 0,14875% con mínimo de {_money(commission_cop(0))} por operación "
        f"({n_ops} compras pendientes). ⚠️ = menos de {LOW_LIQUIDITY_SHARES} acciones/día en promedio: "
        "con tan poco volumen una orden a mercado puede ejecutarse lejos del último precio — conviene "
        "orden límite."
    )

    with st.expander("📋 Reglas de ejecución"):
        for rule in plan["execution_rules"]:
            st.markdown(f"- {rule}")

    # --- Venta ---
    st.markdown("#### 💰 Cuándo vender y cuándo promediar — tus acciones del plan")
    st.caption(
        "Cada celda dice **a qué % sobre tu costo promedio** (comisiones incluidas) vender y **cuántas "
        "acciones**, y el precio en pesos de ese nivel; en las compras, también cómo queda tu costo promedio. ⚡ = el precio de hoy ya llegó a ese nivel."
    )
    sale_rows, pending, extra_cop = [], [], 0.0
    for r in rows:
        t = r["ticker"]
        shares, avg = held.get(t, (0, None))
        if not shares:
            pending.append(t)
            continue
        rule = plan["sale_plan"]["by_ticker"].get(t, {})
        price = r["price_cop"]
        levels = sale_levels(plan, t, avg, shares, price)
        ups = [lv for lv in levels if lv["kind"] == "venta"]
        downs = [lv for lv in levels if lv["kind"] == "compra"]

        def cell(lv):
            mark = "⚡ " if lv["reached"] else ""
            pct = f"{lv['mult'] - 1:+.0%}"
            if lv["kind"] == "venta":
                what = f"vender {lv['shares']:,} acc." if lv["shares"] else "0 acc. (posición chica)"
            else:
                what = f"comprar {lv['shares']:,} acc." if lv["shares"] else lv["action"].lower()
                if lv["new_avg_cop"]:
                    return f"{mark}{pct} → {what} · {_money(lv['price_cop'])} · promedio queda {_money(lv['new_avg_cop'])}"
            return f"{mark}{pct} → {what} · {_money(lv['price_cop'])}"

        row = {
            "Acción": t,
            "Tenés": f"{shares:,} acc.",
            "Tu costo": _money(avg),
            "Hoy": "—" if not price else f"{_money(price)} ({price / avg - 1:+.1%})",
        }
        if ups:
            for i in range(3):
                row[f"{i + 1}ª venta"] = cell(ups[i]) if i < len(ups) else "—"
        else:
            limit = rule.get("rebalance_above_percent")
            weight_now = (r["held_value_cop"] or 0) / held_value if held_value else None
            row["1ª venta"] = f"rebalancear si pesa > {limit}%" + (f" · hoy {weight_now:.0%}" if weight_now is not None else "")
            row["2ª venta"] = row["3ª venta"] = "—"
        row["Si baja: comprar"] = cell(downs[0]) if downs else "—"
        row["Si baja más: comprar"] = cell(downs[1]) if len(downs) > 1 else "—"
        extra_cop += sum(lv["cost_cop"] or 0 for lv in downs)
        sale_rows.append(row)

    if sale_rows:
        st.dataframe(pd.DataFrame(sale_rows), hide_index=True, width="stretch")
    else:
        st.info("Todavía no tenés ninguna acción del plan.")
    if extra_cop:
        st.caption(
            "No hay ventas en pérdida: si baja, se compra más para bajar el costo promedio. Si todas "
            f"tus acciones tocaran todos sus niveles de compra harían falta **{_money(extra_cop)}** "
            "adicionales (comisiones incluidas), fuera de los 80 M del plan."
        )
    if pending:
        st.caption(f"Sin comprar todavía: {', '.join(pending)} — aparecen acá cuando registres la compra.")

    with st.expander("📋 Reglas generales de venta"):
        for rule in plan["sale_plan"]["general_rules"]:
            st.markdown(f"- {rule}")
