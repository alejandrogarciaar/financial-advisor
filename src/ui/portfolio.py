"""Pestaña "💰 Portafolio" — la única que persiste datos ingresados por el usuario (no
respuestas de API), ver `portfolio_data/` en el skill de Portafolio. Extraído de app.py (que
llegó a 2821 líneas) para modularizar. Siempre corre DESPUÉS de Acciones/ETFs en el orden de
pestañas de app.py (st.tabs() no es lazy) para poder reusar `STOCK_EVAL_CACHE_KEY`/
`ETF_EVAL_CACHE_KEY` de `shared.py` en vez de re-consultar lo que esas dos ya trajeron."""

from datetime import date, datetime

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from src.config import (
    ETF_TICKERS,
    PORTFOLIO_CDI_SECTOR,
    PORTFOLIO_CDI_TICKERS,
    PORTFOLIO_CDI_UNDERLYING,
    RISK_FREE_RATE,
)
from src.data.errors import DataError
from src.drawdown_dca import (
    build_laddered_buy_plan,
    classify_drawdown_bucket,
    current_bucket_reaction,
    current_drawdown_snapshot,
)
from src.portfolio import (
    DEFAULT_COMMISSION_COP,
    build_synthetic_portfolio_series,
    commission_summary,
    load_purchases,
    load_sales,
    project_future_value,
    realized_gains_summary,
    save_purchases,
    save_sales,
    simulate_additional_purchase,
    summarize_by_ticker,
    validate_purchases,
    validate_sales,
)
from src.ui.shared import (
    ETF_EVAL_CACHE_KEY,
    STOCK_EVAL_CACHE_KEY,
    ZONE_COLOR,
    _cached_etf_evaluation,
    _cached_evaluation,
    _cached_historical_prices,
    _cached_portfolio_price,
    _get_or_fetch,
    _parallel_fetch,
    triangulation_badge,
    zone_badge,
)
from src.valuation.fair_value import multiple_quality_context_note, quality_context_note, summarize_signals
from src.valuation.risk_return import evaluate_risk_return

PORTFOLIO_TICKERS = list(PORTFOLIO_CDI_TICKERS.keys())


def render_portfolio_total_hero(total_invested_cop: float, total_value_cop: float | None) -> None:
    """La rentabilidad total es el único número que esta sección debería 'liderar' — figura
    hero grande y con color de estado (verde/rojo), con Invertido/Valor actual como tiles de
    apoyo más chicas y neutras al lado. Reusa la misma paleta que zone_badge/quality_badge,
    no una nueva."""
    if total_value_cop is None:
        st.markdown(
            """
            <div style="background:rgba(128,128,128,0.10);border:1px solid rgba(128,128,128,0.35);
                        border-radius:16px;padding:28px;text-align:center;">
                <div style="font-size:0.8rem;font-weight:600;letter-spacing:0.04em;
                            text-transform:uppercase;opacity:0.65;">Rentabilidad total</div>
                <div style="font-size:3rem;font-weight:700;opacity:0.55;line-height:1.1;margin-top:6px;">
                    No disponible
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    else:
        gain_cop = total_value_cop - total_invested_cop
        gain_pct = gain_cop / total_invested_cop if total_invested_cop else 0.0
        color = ZONE_COLOR["Acumulación"] if gain_cop >= 0 else ZONE_COLOR["Sobrevalorado"]
        sign = "+" if gain_cop >= 0 else "-"
        st.markdown(
            f"""
            <div style="background:{color}15;border:1px solid {color}55;border-radius:16px;
                        padding:28px;text-align:center;">
                <div style="font-size:0.8rem;font-weight:600;letter-spacing:0.04em;
                            text-transform:uppercase;color:{color};opacity:0.9;">Rentabilidad total</div>
                <div style="font-size:3rem;font-weight:700;color:{color};line-height:1.1;margin-top:6px;">
                    {gain_pct:+.1%}
                </div>
                <div style="font-size:1.05rem;font-weight:600;color:{color};opacity:0.85;margin-top:2px;">
                    {sign}${abs(gain_cop):,.0f} COP
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    st.write("")
    tile1, tile2 = st.columns(2)
    for col, label, value in (
        (tile1, "Invertido", f"${total_invested_cop:,.0f} COP"),
        (
            tile2,
            "Valor actual",
            f"${total_value_cop:,.0f} COP" if total_value_cop is not None else "No disponible",
        ),
    ):
        col.markdown(
            f"""
            <div style="background:rgba(128,128,128,0.08);border:1px solid rgba(128,128,128,0.25);
                        border-radius:12px;padding:14px 18px;">
                <div style="font-size:0.72rem;font-weight:600;letter-spacing:0.03em;
                            text-transform:uppercase;opacity:0.65;">{label}</div>
                <div style="font-size:1.4rem;font-weight:700;margin-top:2px;">{value}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )


def render_realized_gains_hero(cost_basis_cop: float, gross_proceeds_cop: float, sale_commission_cop: float) -> None:
    """Misma forma que `render_portfolio_total_hero` (hero grande + tiles de apoyo), pero para
    lo YA VENDIDO — plata que ya se realizó, no una posición que sigue fluctuando. La comisión de
    venta se muestra como tile propio (no solo restada adentro del número final) porque el pedido
    puntual que originó esta sección fue justamente poder ver ese costo, no solo el neto."""
    net_proceeds_cop = gross_proceeds_cop - sale_commission_cop
    gain_cop = net_proceeds_cop - cost_basis_cop
    gain_pct = gain_cop / cost_basis_cop if cost_basis_cop else 0.0
    color = ZONE_COLOR["Acumulación"] if gain_cop >= 0 else ZONE_COLOR["Sobrevalorado"]
    sign = "+" if gain_cop >= 0 else "-"
    st.markdown(
        f"""
        <div style="background:{color}15;border:1px solid {color}55;border-radius:16px;
                    padding:28px;text-align:center;">
            <div style="font-size:0.8rem;font-weight:600;letter-spacing:0.04em;
                        text-transform:uppercase;color:{color};opacity:0.9;">Ganancia realizada</div>
            <div style="font-size:3rem;font-weight:700;color:{color};line-height:1.1;margin-top:6px;">
                {gain_pct:+.1%}
            </div>
            <div style="font-size:1.05rem;font-weight:600;color:{color};opacity:0.85;margin-top:2px;">
                {sign}${abs(gain_cop):,.0f} COP
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    st.write("")
    tile1, tile2, tile3 = st.columns(3)
    for col, label, value in (
        (tile1, "Costo de compra (vendido)", f"${cost_basis_cop:,.0f} COP"),
        (tile2, "Ingreso bruto por venta", f"${gross_proceeds_cop:,.0f} COP"),
        (tile3, "Comisión de venta pagada", f"${sale_commission_cop:,.0f} COP"),
    ):
        col.markdown(
            f"""
            <div style="background:rgba(128,128,128,0.08);border:1px solid rgba(128,128,128,0.25);
                        border-radius:12px;padding:14px 18px;">
                <div style="font-size:0.72rem;font-weight:600;letter-spacing:0.03em;
                            text-transform:uppercase;opacity:0.65;">{label}</div>
                <div style="font-size:1.4rem;font-weight:700;margin-top:2px;">{value}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )


# Gate estático de qué (ticker subyacente, franja de caída) se validó fuera de muestra en la
# investigación de esta sesión (split cronológico 60/40, mismo patrón que
# REGIME_VALIDATED_COMBOS de Especulación — no recalculado en vivo, para evitar p-hacking).
# Horizonte fijo en 90 días (DRAWDOWN_REACTION_HORIZON_DAYS en drawdown_dca.py): elegido de
# antemano por ser el horizonte medio de los 4 testeados (20/60/90/180), no porque haya sido el
# que mejor dio — no agregar otros horizontes al gate sin repetir la validación completa.
# Las franjas de caída profunda (20%+) NO validan para casi ningún ticker: el período de test
# (últimos ~2 años) fue mayormente alcista y casi no tuvo caídas grandes — no es que la señal
# falle ahí, es que no hay con qué medirla todavía. MSFT es la única excepción con muestra
# suficiente en 20-30%. CSPX (S&P 500) no tiene ningún bucket validado — ni bajó lo suficiente
# en el período de test como para poder chequear nada más allá de 0-5%. Ver CLAUDE.md para el
# detalle completo del backtest (train/test, n por celda).
DRAWDOWN_VALIDATED_BUCKETS = {
    "GOOGL": {"5-10%"},
    "AMZN": {"5-10%", "10-15%"},
    "AAPL": {"5-10%", "10-15%"},
    "MSFT": {"5-10%", "10-15%", "20-30%"},
    "CSPXCO": set(),
}

# La caché de precios/evaluaciones subyacente (_cached_portfolio_price, _cached_evaluation,
# _cached_etf_evaluation, _cached_historical_prices en shared.py) vence cada 900s (15 min) —
# ese es el piso real de frescura de los datos. 5 min es más seguido de lo estrictamente
# necesario mirando solo ese ttl, elegido para acortar la ventana de precio desactualizado tras
# el vencimiento (pedido explícito del usuario) a costa de más re-renders del fragmento (la
# mayoría son cache-hit y por lo tanto baratos).
PORTFOLIO_AUTOREFRESH_INTERVAL = "5m"


def _compute_held_summary(purchases: pd.DataFrame, sales: pd.DataFrame) -> tuple[list[str], pd.DataFrame]:
    """held_tickers (compradas menos vendidas > 0) + `summarize_by_ticker()` — compartido entre
    los dos fragmentos de abajo (`_render_cartera_and_total`, `_render_portfolio_analysis`), que
    ahora corren por separado (con "Tus compras"/"Tus ventas" en el medio, pedido explícito del
    usuario) y por lo tanto necesitan cada uno su propia copia en vez de reusar una variable local
    compartida como antes. Barato de recalcular dos veces: el precio por ticker ya está cacheado
    (`@st.cache_data`, 900s) así que esto no vuelve a pegarle a la red, solo rehace un poco de
    trabajo de DataFrame."""
    sold_by_ticker = sales.groupby("ticker")["shares"].sum() if not sales.empty else pd.Series(dtype=int)
    purchased_by_ticker = purchases.groupby("ticker")["shares"].sum() if not purchases.empty else pd.Series(dtype=int)
    net_shares = purchased_by_ticker.subtract(sold_by_ticker, fill_value=0)
    held_tickers = sorted(net_shares[net_shares > 0].index.tolist())
    if not held_tickers:
        return held_tickers, pd.DataFrame()

    price_results = _parallel_fetch({t: (_cached_portfolio_price, (t,)) for t in held_tickers})
    current_prices_cop = {t: result for t, (result, error) in price_results.items()}
    summary = summarize_by_ticker(purchases, sales, current_prices_cop)
    return held_tickers, summary


@st.fragment(run_every=PORTFOLIO_AUTOREFRESH_INTERVAL)
def _render_cartera_and_total(purchases: pd.DataFrame, sales: pd.DataFrame) -> None:
    """"Mi Cartera" (holdings netos) + "Total" (ganancia no realizada) — el encabezado de la
    pestaña, primero en la página por pedido del usuario. Separado de
    `_render_portfolio_analysis()` (Comisiones en adelante) específicamente para que "Tus
    compras"/"Tus ventas" puedan quedar sandwicheadas entre los dos, inmediatamente después de
    "Total" — también pedido explícito. Sigue siendo su propio fragmento con auto-refresh, igual
    que antes de la separación, así no resetea nada de lo que quedó en el medio."""
    st.caption(f"🕒 Última actualización: {datetime.now():%H:%M:%S}")

    st.divider()
    st.subheader("Mi Cartera")
    st.caption("Solo lo que todavía tenés en cartera (comprado menos vendido) — lo ya vendido está en \"Ganancias realizadas\" más abajo.")

    held_tickers, summary = _compute_held_summary(purchases, sales)

    if not held_tickers:
        st.caption("No tenés ninguna posición abierta ahora mismo." if purchases.empty else "Vendiste todas tus posiciones — no tenés nada en cartera ahora mismo.")
    else:
        display = pd.DataFrame(
            {
                "Ticker": summary["ticker"],
                "Acciones": summary["shares"],
                "Precio prom. compra (COP)": summary["avg_price_cop"],
                "Precio actual (COP)": summary["current_price_cop"],
                "Invertido (COP)": summary["invested_cop"],
                "Valor actual (COP)": summary["current_value_cop"],
                "Rentabilidad": summary["return_pct"],
            }
        )

        def _color_return(value):
            if pd.isna(value):
                return ""
            color = ZONE_COLOR["Acumulación"] if value >= 0 else ZONE_COLOR["Sobrevalorado"]
            return f"color:{color};font-weight:700;"

        def _format_return(value):
            if pd.isna(value):
                return "—"
            arrow = "▲" if value >= 0 else "▼"
            return f"{arrow} {value:+.1%}"

        styled = display.style.format(
            {
                "Precio prom. compra (COP)": "${:,.0f}",
                "Precio actual (COP)": lambda v: f"${v:,.0f}" if pd.notna(v) else "No disponible",
                "Invertido (COP)": "${:,.0f}",
                "Valor actual (COP)": lambda v: f"${v:,.0f}" if pd.notna(v) else "—",
                "Rentabilidad": _format_return,
            }
        ).map(_color_return, subset=["Rentabilidad"])
        st.dataframe(styled, hide_index=True, use_container_width=True)

    st.divider()
    st.subheader("Total")

    total_invested_cop = float(summary["invested_cop"].sum()) if not summary.empty else 0.0
    valued_rows = summary["current_value_cop"].dropna() if not summary.empty else pd.Series(dtype=float)
    total_value_cop = float(valued_rows.sum()) if not valued_rows.empty else None

    render_portfolio_total_hero(total_invested_cop, total_value_cop)

    if not summary.empty and len(valued_rows) < len(summary):
        st.caption("⚠️ El precio actual de algún ticker no está disponible ahora — el total no lo incluye.")


@st.fragment(run_every=PORTFOLIO_AUTOREFRESH_INTERVAL)
def _render_portfolio_analysis(purchases: pd.DataFrame, sales: pd.DataFrame) -> None:
    """Todo lo que va DESPUÉS de "Tus compras"/"Tus ventas" en el orden de la página: Comisiones,
    Ganancias realizadas, Contexto de valoración, Diversificación, Retorno y riesgo, Proyección de
    meta. Recalcula `held_tickers`/`summary` con `_compute_held_summary()` en vez de recibirlos de
    `_render_cartera_and_total()` — son dos fragmentos separados (cada uno con su propio timer de
    auto-refresh), no hay forma de pasarse variables entre ellos directamente."""
    st.divider()
    st.subheader("💸 Costo de comisiones")
    st.caption("Lo que pagaste en comisiones no proyecta nada — es plata real que ya salió de tu bolsillo y le resta a la rentabilidad, tanto al comprar como al vender.")

    commissions = commission_summary(purchases, sales)
    cm1, cm2, cm3 = st.columns(3)
    cm1.metric("Comisiones pagadas (COP)", f"${commissions['total_commission_cop']:,.0f}")
    cm2.metric("Comisión promedio por operación", f"${commissions['avg_commission_cop']:,.0f}" if commissions["avg_commission_cop"] is not None else "—")
    if commissions["pct_of_invested"] is not None:
        cm3.metric("% del capital invertido", f"{commissions['pct_of_invested']:.2%}")
    st.caption(
        f"Sobre {commissions['num_purchases']} compra(s) (${commissions['total_buy_commission_cop']:,.0f} COP) "
        f"y {commissions['num_sales']} venta(s) (${commissions['total_sale_commission_cop']:,.0f} COP) registrada(s)."
    )

    st.divider()
    st.subheader("💵 Ganancias realizadas")
    st.caption(
        "Lo que ya vendiste, con la comisión de venta ya descontada — plata que ya se realizó, "
        "no una posición que sigue fluctuando. Usa el costo promedio de compra (no por lotes) "
        "como base de costo, el mismo criterio que el resto de esta pestaña."
    )
    if sales.empty:
        st.caption("Todavía no registraste ninguna venta.")
    else:
        gains = realized_gains_summary(purchases, sales)
        total_cost_basis_cop = float(gains["cost_basis_cop"].sum())
        total_gross_proceeds_cop = float(gains["gross_proceeds_cop"].sum())
        total_sale_commission_cop = float(gains["sale_commission_cop"].sum())

        render_realized_gains_hero(total_cost_basis_cop, total_gross_proceeds_cop, total_sale_commission_cop)

        st.write("")
        gains_display = pd.DataFrame(
            {
                "Ticker": gains["ticker"],
                "Acciones vendidas": gains["shares_sold"],
                "Costo prom. compra (COP)": gains["avg_buy_price_cop"],
                "Precio venta neto (COP)": gains["net_sale_price_cop"],
                "Comisión de venta (COP)": gains["sale_commission_cop"],
                "Ganancia (COP)": gains["realized_gain_cop"],
                "Ganancia (%)": gains["realized_gain_pct"],
            }
        )

        def _color_gain(value):
            if pd.isna(value):
                return ""
            color = ZONE_COLOR["Acumulación"] if value >= 0 else ZONE_COLOR["Sobrevalorado"]
            return f"color:{color};font-weight:700;"

        def _format_gain_pct(value):
            if pd.isna(value):
                return "—"
            arrow = "▲" if value >= 0 else "▼"
            return f"{arrow} {value:+.1%}"

        gains_styled = gains_display.style.format(
            {
                "Costo prom. compra (COP)": "${:,.0f}",
                "Precio venta neto (COP)": lambda v: f"${v:,.0f}" if pd.notna(v) else "—",
                "Comisión de venta (COP)": "${:,.0f}",
                "Ganancia (COP)": "${:+,.0f}",
                "Ganancia (%)": _format_gain_pct,
            }
        ).map(_color_gain, subset=["Ganancia (COP)", "Ganancia (%)"])
        st.dataframe(gains_styled, hide_index=True, use_container_width=True)

    held_tickers, summary = _compute_held_summary(purchases, sales)

    if held_tickers:
        st.divider()
        st.subheader("📎 Contexto de valoración")
        st.caption(
            "Cada CDI sigue 1:1 a su acción/ETF matriz — esto es la misma señal que ya calculamos "
            "en las pestañas Acciones/ETFs, no un análisis nuevo."
        )
        provider = st.session_state.get("last_provider", "yfinance")

        # Si Acciones/ETFs ya evaluaron esta acción/fondo matriz en esta misma corrida (porque
        # está entre los tickers filtrados ahí), _get_or_fetch la reusa directo de
        # STOCK_EVAL_CACHE_KEY/ETF_EVAL_CACHE_KEY — recién arma un job nuevo para las que no
        # estén. El número de jobs sale de `held_tickers` (lo que hay en el Portafolio), nunca
        # de una cantidad fija.
        stock_underlying = {t: u for t, (k, u) in ((t, PORTFOLIO_CDI_UNDERLYING[t]) for t in held_tickers) if k == "stock"}
        etf_underlying = {t: u for t, (k, u) in ((t, PORTFOLIO_CDI_UNDERLYING[t]) for t in held_tickers) if k == "etf"}

        stock_results = _get_or_fetch(
            STOCK_EVAL_CACHE_KEY,
            {(u, provider): (_cached_evaluation, (u, provider)) for u in set(stock_underlying.values())},
        )
        etf_results = _get_or_fetch(
            ETF_EVAL_CACHE_KEY,
            {u: (_cached_etf_evaluation, (u,)) for u in set(etf_underlying.values())},
        )
        context_results = {t: stock_results[(u, provider)] for t, u in stock_underlying.items()}
        context_results.update({t: etf_results[u] for t, u in etf_underlying.items()})

        # Capturado durante el loop de abajo (no un fetch aparte) para que las secciones de
        # Diversificación/Retorno y riesgo/Proyección, más abajo, puedan reusar el mismo
        # historial de precios del subyacente sin volver a pedirlo.
        underlying_prices: dict[str, list[dict]] = {}

        context_cols = st.columns(2)
        for i, ticker in enumerate(held_tickers):
            kind, underlying = PORTFOLIO_CDI_UNDERLYING[ticker]
            ev, error = context_results[ticker]
            with context_cols[i % 2]:
                with st.container(border=True):
                    st.markdown(f"**{ticker}** → {underlying}")
                    if error is not None:
                        st.caption(
                            "No pudimos consultar esta acción ahora mismo."
                            if kind == "stock"
                            else "No pudimos consultar este ETF ahora mismo."
                        )
                        continue
                    if kind == "stock":
                        ev_summary = summarize_signals(ev)
                        st.markdown(triangulation_badge(ev_summary, small=True), unsafe_allow_html=True)
                        note = quality_context_note(ev, ev_summary) or multiple_quality_context_note(ev)
                        if note:
                            st.caption(f"💡 {note}")
                    else:
                        if ev.zone is not None:
                            st.markdown(zone_badge(ev.zone, small=True), unsafe_allow_html=True)
                        else:
                            st.caption("Sin señal de valoración disponible para este ETF.")

                    # Zona de acumulación: % de caída desde el máximo de 1 año del SUBYACENTE.
                    # DRAWDOWN_VALIDATED_BUCKETS es el gate estático de qué franja está
                    # confirmada fuera de muestra para este ticker puntual; el número mostrado
                    # se recalcula en vivo sobre todo el historial disponible. Para acciones,
                    # `ev.historical_prices` ya está en memoria (TickerEvaluation lo trae) — sin
                    # fetch nuevo. ETFEvaluation NO trae ese campo, así que para ETFs hace falta
                    # un fetch aparte (cacheado, y casi siempre ya tibio si Especulación lo pidió
                    # este mismo run) contra el símbolo real de yfinance (ETF_TICKERS[underlying],
                    # no el "CSPXCO" pelado).
                    if kind == "stock":
                        dca_prices = ev.historical_prices
                    else:
                        try:
                            dca_prices, _ = _cached_historical_prices(ETF_TICKERS[underlying])
                        except DataError:
                            dca_prices = []
                    underlying_prices[ticker] = dca_prices
                    closes = [p["close"] for p in dca_prices]
                    snapshot = current_drawdown_snapshot(dca_prices)
                    if snapshot is not None:
                        bucket = classify_drawdown_bucket(snapshot.drawdown)
                        validated_for_ticker = DRAWDOWN_VALIDATED_BUCKETS.get(underlying, set())
                        reaction = current_bucket_reaction(closes) if bucket in validated_for_ticker else None
                        # st.metric SIEMPRE (no un caption) — el % en sí es útil incluso sin
                        # confirmación histórica, y la mayoría de los holdings, en un momento
                        # dado, van a estar en una franja no validada (ver rama de abajo); si
                        # el número solo se destacara cuando SÍ está validado, en la práctica se
                        # vería chico/perdido la mayor parte del tiempo, que fue el problema
                        # reportado.
                        st.metric("📉 Vs. máximo de 1 año", f"-{snapshot.drawdown:.0%}")
                        # Precio de referencia en USD (moneda del subyacente, no del CDI en
                        # COP) — mismo criterio que el resto de "Contexto de valoración", que ya
                        # muestra datos del subyacente sin convertir. Se nombra el ticker
                        # (`underlying`) explícitamente para que no se confunda con el precio en
                        # COP del CDI que se ve en el resto de la tarjeta/tabla.
                        st.caption(
                            f"{underlying} (USD): ${snapshot.current_price:,.2f} hoy vs. máximo "
                            f"de ${snapshot.trailing_high:,.2f} el {snapshot.trailing_high_date}."
                        )
                        if reaction is not None and reaction.mean_return is not None:
                            # st.success acá SÍ, porque este es el caso interesante y accionable
                            # (confirmado fuera de muestra) — mismo peso visual que el DCA box
                            # de Especulación.
                            st.success(
                                f"**Zona de acumulación** — esta franja ({bucket}) rindió, en "
                                f"promedio y confirmado fuera de muestra, "
                                f"**{reaction.mean_return:+.0%} a {reaction.horizon_days} días** "
                                f"(tasa de acierto {reaction.win_rate:.0%}, n={reaction.observations})."
                            )
                        else:
                            st.caption("Sin confirmación histórica suficiente para esta franja todavía.")

                        if validated_for_ticker:
                            with st.expander("🪜 Plan de compra escalonada"):
                                st.caption(
                                    "Reparte un presupuesto entre las franjas de caída YA VALIDADAS "
                                    "fuera de muestra para este ticker, con más peso a la franja más "
                                    "profunda (cuanto más bajó, mejor rindió en promedio en la "
                                    "validación) — el reparto en sí es un criterio de gestión de "
                                    "riesgo, no algo testeado por separado; lo validado es que cada "
                                    "franja individual tuvo retorno promedio positivo a 90 días."
                                )
                                budget_cop = st.number_input(
                                    "Presupuesto a repartir (COP)",
                                    min_value=0,
                                    step=100_000,
                                    value=1_000_000,
                                    key=f"ladder_budget_{ticker}",
                                )
                                plan = build_laddered_buy_plan(
                                    validated_for_ticker, closes, snapshot.trailing_high, float(budget_cop)
                                )
                                for rung in plan:
                                    reaction_text = (
                                        f"{rung['mean_return']:+.0%} a 90d, acierto {rung['win_rate']:.0%}, "
                                        f"n={rung['observations']}"
                                        if rung["mean_return"] is not None
                                        else "sin confirmación suficiente ahora"
                                    )
                                    st.markdown(
                                        f"**{rung['bucket']}** ({underlying} entre "
                                        f"${rung['price_low']:,.2f} y ${rung['price_high']:,.2f}): "
                                        f"**{rung['weight_pct']:.0%}** del presupuesto → "
                                        f"**${rung['allocated_cop']:,.0f} COP** · {reaction_text}"
                                    )
                        else:
                            st.caption("Sin franja de caída validada para este ticker todavía — no hay plan escalonado.")

        st.divider()
        st.subheader("🥧 Diversificación")
        st.caption(
            "Cuánto pesa cada posición sobre el total y a qué sector pertenece (clasificación "
            "aproximada) — para ver qué tan concentrado estás en pocos nombres o en un solo rubro."
        )
        weighted_rows = summary[summary["current_value_cop"].notna()].sort_values(
            "current_value_cop", ascending=False
        )
        if weighted_rows.empty:
            st.caption("No hay precios actuales disponibles para calcular esto todavía.")
        else:
            total_weighted_value = float(weighted_rows["current_value_cop"].sum())
            # Colores de la paleta categórica validada de la dataviz skill, orden fijo por
            # sector (nunca por orden de aparición) — mismo criterio que LEVEL_CHART_COLORS en
            # Especulación. 4 sectores posibles acá, slots 1-4 de la paleta de 8.
            sector_colors = {
                "Comunicación": "#2a78d6",
                "Consumo discrecional": "#eb6834",
                "Tecnología": "#1baf7a",
                "Diversificado (ETF S&P 500)": "#eda100",
            }
            div_fig = go.Figure()
            for sector in dict.fromkeys(PORTFOLIO_CDI_SECTOR[t] for t in weighted_rows["ticker"]):
                sector_rows = weighted_rows[weighted_rows["ticker"].map(PORTFOLIO_CDI_SECTOR) == sector]
                div_fig.add_trace(
                    go.Bar(
                        x=sector_rows["ticker"],
                        y=sector_rows["current_value_cop"] / total_weighted_value,
                        name=sector,
                        marker_color=sector_colors.get(sector, "#898781"),
                        hovertemplate="%{x}: %{y:.0%}<extra>" + sector + "</extra>",
                    )
                )
            div_fig.update_layout(
                plot_bgcolor="rgba(0,0,0,0)",
                paper_bgcolor="rgba(0,0,0,0)",
                font=dict(color="#898781"),
                legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
                margin=dict(l=10, r=10, t=10, b=10),
                height=320,
                yaxis=dict(tickformat=".0%", gridcolor="rgba(128,128,128,0.2)"),
                xaxis=dict(showgrid=False),
            )
            st.plotly_chart(div_fig, use_container_width=True)
            largest = weighted_rows.iloc[0]
            st.caption(
                f"Tu posición más grande es **{largest['ticker']}**, con "
                f"{largest['current_value_cop'] / total_weighted_value:.0%} del total."
            )

        st.divider()
        st.subheader("📈 Retorno y riesgo del portafolio")
        st.caption(
            "Simulación con tu asignación ACTUAL aplicada a todo el historial disponible — no "
            "reconstruye cuándo compraste cada cosa realmente. Usa el precio del subyacente en "
            "USD, no el del CDI en COP (no incorpora el efecto de la TRM). 100% histórico, no "
            "proyecta nada."
        )
        # Solo entran a la serie sintética los tickers con peso Y precio histórico disponibles
        # a la vez — build_synthetic_portfolio_series ya renormaliza entre los que califican.
        weights_for_series = {
            t: float(v) for t, v in zip(summary["ticker"], summary["current_value_cop"]) if pd.notna(v)
        }
        holdings_for_series = {t: underlying_prices[t] for t in weights_for_series if underlying_prices.get(t)}
        weights_for_series = {t: w for t, w in weights_for_series.items() if t in holdings_for_series}
        portfolio_series = build_synthetic_portfolio_series(holdings_for_series, weights_for_series)
        portfolio_rr = evaluate_risk_return(portfolio_series, RISK_FREE_RATE) if portfolio_series else None

        if portfolio_rr is None:
            st.caption("No hay suficiente historial disponible para calcular esto todavía.")
        else:
            prr1, prr2, prr3 = st.columns(3)
            if portfolio_rr.cagr_1y is not None:
                prr1.metric("Retorno anualizado (1 año)", f"{portfolio_rr.cagr_1y:+.1%}")
            if portfolio_rr.cagr_3y is not None:
                prr2.metric("Retorno anualizado (3 años)", f"{portfolio_rr.cagr_3y:+.1%}")
            if portfolio_rr.cagr_5y is not None:
                prr3.metric("Retorno anualizado (5 años)", f"{portfolio_rr.cagr_5y:+.1%}")
            prr4, prr5, prr6 = st.columns(3)
            if portfolio_rr.annualized_volatility is not None:
                prr4.metric("Volatilidad anualizada", f"{portfolio_rr.annualized_volatility:.1%}")
            if portfolio_rr.sharpe_ratio is not None:
                prr5.metric("Sharpe ratio", f"{portfolio_rr.sharpe_ratio:.2f}")
            if portfolio_rr.max_drawdown is not None:
                prr6.metric("Máxima caída", f"{portfolio_rr.max_drawdown:.1%}")

        st.divider()
        st.subheader("🎯 Proyección de meta")
        # total_value_cop se recalcula acá (no viene de _render_cartera_and_total — fragmentos
        # separados, sin estado compartido) a partir del mismo `summary` de arriba.
        valued_rows = summary["current_value_cop"].dropna()
        total_value_cop = float(valued_rows.sum()) if not valued_rows.empty else None
        if portfolio_rr is None or total_value_cop is None:
            st.caption("No disponible todavía — depende del retorno agregado calculado arriba.")
        else:
            st.caption(
                "Proyección matemática (capital inicial + aportes mensuales, interés compuesto) "
                "usando el retorno histórico de arriba — **no es una promesa ni garantía de "
                "retorno futuro**. El desempeño pasado no asegura nada hacia adelante."
            )
            proj1, proj2 = st.columns(2)
            monthly_contribution = proj1.number_input(
                "Aporte mensual (COP)", min_value=0, step=50_000, value=200_000, key="goal_monthly_cop"
            )
            horizon_years = proj2.number_input(
                "Horizonte (años)", min_value=1, max_value=40, step=1, value=10, key="goal_horizon_years"
            )

            # Total aportado no depende de la tasa (es capital de hoy + aportes futuros) — un
            # solo número, no uno por escenario, para no repetirlo 3 veces sin necesidad.
            months = round(horizon_years * 12)
            total_contributed = total_value_cop + monthly_contribution * months
            st.metric("Total aportado (lo que ya tenés + tus aportes futuros)", f"${total_contributed:,.0f}")

            proj_cols = st.columns(3)
            for proj_col, label, rate in zip(
                proj_cols,
                ["1 año", "3 años", "5 años"],
                [portfolio_rr.cagr_1y, portfolio_rr.cagr_3y, portfolio_rr.cagr_5y],
            ):
                if rate is None:
                    continue
                projected_value = project_future_value(total_value_cop, monthly_contribution, rate, horizon_years)
                gain = projected_value - total_contributed
                # delta = la ganancia proyectada (aportado vs. aportado+ganancia) — Streamlit la
                # colorea sola (verde/rojo según signo), es justo la distinción que se pidió.
                proj_col.metric(
                    f"Total proyectado — retorno de {label}",
                    f"${projected_value:,.0f}",
                    delta=f"${gain:,.0f} de ganancia",
                )


def render_capital():
    st.title("💰 Portafolio")
    st.caption(
        "Registrá tus compras y ventas reales de estas acciones y seguí cuánto llevás invertido "
        "en pesos, a qué precio promedio, y cómo viene la rentabilidad hoy — tanto la de lo que "
        "todavía tenés como la que ya realizaste al vender."
    )

    purchases = load_purchases()
    sales = load_sales()

    _render_cartera_and_total(purchases, sales)

    st.divider()
    st.subheader("Tus compras")
    st.caption(
        "Solo de agregar, no editable ni eliminable desde acá — el detalle fila por fila queda "
        "guardado igual (no se pierde nada), pero lo que importa día a día es \"Mi Cartera\" más "
        "arriba, que ya muestra tu posición neta y precio promedio. Las acciones son unidades "
        "enteras — no se aceptan compras fraccionarias (1.2, 2.3, etc.)."
    )

    with st.form("add_purchase_form", clear_on_submit=True):
        st.markdown("**➕ Registrar una compra nueva**")
        p1, p2, p3, p4 = st.columns(4)
        new_purchase_ticker = p1.selectbox("Ticker", PORTFOLIO_TICKERS, key="new_purchase_ticker")
        new_purchase_shares = p2.number_input("Acciones", min_value=1, step=1, value=1, key="new_purchase_shares")
        new_purchase_price = p3.number_input(
            "Precio de compra (COP)", min_value=1.0, step=1000.0, value=1_000_000.0, format="%.0f",
            key="new_purchase_price",
        )
        new_purchase_commission = p4.number_input(
            "Comisión (COP)", min_value=0.0, step=100.0, value=DEFAULT_COMMISSION_COP, format="%.0f",
            key="new_purchase_commission",
        )
        new_purchase_date = st.date_input(
            "Fecha de compra", value=date.today(), max_value=date.today(), key="new_purchase_date"
        )
        submitted_purchase = st.form_submit_button("➕ Registrar compra")

    if submitted_purchase:
        new_row = pd.DataFrame(
            [
                {
                    "ticker": new_purchase_ticker,
                    "shares": int(new_purchase_shares),
                    "price_cop": float(new_purchase_price),
                    "commission_cop": float(new_purchase_commission),
                    "date": new_purchase_date,
                }
            ]
        )
        candidate = pd.concat([purchases, new_row], ignore_index=True)
        purchase_errors = validate_purchases(candidate, PORTFOLIO_TICKERS)
        if purchase_errors:
            for err in purchase_errors:
                st.error(err)
        else:
            save_purchases(candidate)
            st.success(f"Compra registrada: {int(new_purchase_shares)} acción(es) de {new_purchase_ticker}.")
            st.rerun()

    st.divider()
    st.subheader("Tus ventas")
    st.caption(
        "Registro histórico de tus ventas — **regla no negociable: esta tabla es solo de "
        "agregar.** Una vez guardada, una venta no se puede editar ni borrar desde acá (ni vos "
        "ni yo). Si cargaste algo mal, decímelo y sumamos una fila nueva que lo corrija — nunca "
        "borramos ni tocamos la anterior."
    )

    if sales.empty:
        st.caption("Todavía no registraste ninguna venta.")
    else:
        st.dataframe(
            sales,
            hide_index=True,
            use_container_width=True,
            column_config={
                "ticker": st.column_config.TextColumn("Ticker"),
                "shares": st.column_config.NumberColumn("Acciones", format="%d"),
                "price_cop": st.column_config.NumberColumn("Precio de venta (COP)", format="$%.0f"),
                "commission_cop": st.column_config.NumberColumn("Comisión de venta (COP)", format="$%.0f"),
                "date": st.column_config.DateColumn("Fecha de venta", format="DD/MM/YYYY"),
            },
        )

    with st.form("add_sale_form", clear_on_submit=True):
        st.markdown("**➕ Registrar una venta nueva**")
        f1, f2, f3, f4 = st.columns(4)
        new_sale_ticker = f1.selectbox("Ticker", PORTFOLIO_TICKERS, key="new_sale_ticker")
        new_sale_shares = f2.number_input("Acciones", min_value=1, step=1, value=1, key="new_sale_shares")
        new_sale_price = f3.number_input(
            "Precio de venta (COP)", min_value=1.0, step=1000.0, value=1_000_000.0, format="%.0f",
            key="new_sale_price",
        )
        new_sale_commission = f4.number_input(
            "Comisión de venta (COP)", min_value=0.0, step=100.0, value=DEFAULT_COMMISSION_COP, format="%.0f",
            key="new_sale_commission",
        )
        new_sale_date = st.date_input("Fecha de venta", value=date.today(), max_value=date.today(), key="new_sale_date")
        submitted_sale = st.form_submit_button("➕ Registrar venta")

    if submitted_sale:
        new_row = pd.DataFrame(
            [
                {
                    "ticker": new_sale_ticker,
                    "shares": int(new_sale_shares),
                    "price_cop": float(new_sale_price),
                    "commission_cop": float(new_sale_commission),
                    "date": new_sale_date,
                }
            ]
        )
        candidate = pd.concat([sales, new_row], ignore_index=True)
        sale_errors = validate_sales(candidate, PORTFOLIO_TICKERS, purchases)
        if sale_errors:
            for err in sale_errors:
                st.error(err)
        else:
            save_sales(candidate)
            st.success(f"Venta registrada: {int(new_sale_shares)} acción(es) de {new_sale_ticker}.")
            st.rerun()

    _render_portfolio_analysis(purchases, sales)

    st.divider()
    st.subheader("🧮 Simulador de precio promedio")
    st.caption(
        "Una compra hipotética — no se guarda nada acá. Sirve para planificar cuánto se movería "
        "tu precio promedio (y tu rentabilidad) si sumaras acciones a un precio determinado."
    )

    sim1, sim2, sim3, sim4 = st.columns(4)
    sim_ticker = sim1.selectbox("Ticker", PORTFOLIO_TICKERS, key="sim_ticker")

    try:
        sim_current_price = _cached_portfolio_price(sim_ticker)
    except DataError:
        sim_current_price = None

    sim_shares = sim2.number_input("Acciones a comprar", min_value=1, step=1, value=1, key="sim_shares")
    sim_price = sim3.number_input(
        "Precio hipotético (COP)",
        min_value=1.0,
        step=1000.0,
        value=float(sim_current_price) if sim_current_price else 1000000.0,
        format="%.0f",
        key="sim_price",
    )
    sim_commission = sim4.number_input(
        "Comisión (COP)", min_value=0.0, step=100.0, value=DEFAULT_COMMISSION_COP, format="%.0f", key="sim_commission"
    )

    sim = simulate_additional_purchase(
        purchases, sales, sim_ticker, int(sim_shares), float(sim_price), float(sim_commission)
    )

    r1, r2, r3 = st.columns(3)
    if sim["current_shares"]:
        r1.metric("Precio promedio actual", f"${sim['current_avg_price_cop']:,.0f}")
        delta_avg = sim["new_avg_price_cop"] - sim["current_avg_price_cop"]
        delta_sign = "-" if delta_avg < 0 else "+"
        # bajar el promedio es bueno para el inversor -> delta_color="inverse" lo pinta verde
        # cuando el delta es negativo (mismo cuidado con el signo antes del "$" que en Total).
        r2.metric(
            "Precio promedio si comprás",
            f"${sim['new_avg_price_cop']:,.0f}",
            f"{delta_sign}${abs(delta_avg):,.0f}",
            delta_color="inverse",
        )
    else:
        r1.metric("Precio promedio actual", "Todavía no tenés esta posición")
        r2.metric("Precio promedio si comprás", f"${sim['new_avg_price_cop']:,.0f}")
    r3.metric("Acciones totales después", f"{sim['new_shares']:,d}")

    if sim_current_price is not None and sim["new_avg_price_cop"]:
        sim_return_pct = (sim_current_price - sim["new_avg_price_cop"]) / sim["new_avg_price_cop"]
        st.caption(
            f"Con el precio de mercado de hoy (${sim_current_price:,.0f}), tu rentabilidad en "
            f"{sim_ticker} quedaría en **{sim_return_pct:+.1%}** después de esta compra."
        )
    else:
        st.caption("No pudimos obtener el precio de mercado de hoy para calcular la rentabilidad proyectada.")
