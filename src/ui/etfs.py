"""Pestaña "🧺 ETFs" — lista + detalle. Extraído de app.py (que llegó a 2821 líneas) para
modularizar."""

import streamlit as st

from src.config import ETF_TICKERS, PORTFOLIO_CDI_TICKERS
from src.data.errors import DataError
from src.ui.shared import (
    ETF_EVAL_CACHE_KEY,
    _cached_etf_evaluation,
    _cached_portfolio_price,
    _get_or_fetch,
    _parallel_fetch,
    format_as_of,
    scroll_to_top,
    zone_badge,
)
from src.valuation.etf_analysis import REFERENCE_PE


def explain_etf_valuation(evaluation) -> str:
    if evaluation.fair_value is None:
        return (
            f"No pudimos calcular esta señal para **{evaluation.ticker}**: Yahoo Finance no "
            "reportó ganancias por acción (EPS) de los últimos 12 meses para este fondo."
        )
    word = "más barato" if evaluation.margin >= 0 else "más caro"
    return (
        f"**{evaluation.ticker}** cotiza hoy a **{evaluation.trailing_pe:.1f} veces** sus "
        "ganancias (P/E), contra un promedio histórico de largo plazo del S&P 500 de "
        f"**{REFERENCE_PE:.0f} veces** — a diferencia de una acción individual, un fondo índice "
        "no tiene un P/E propio con el cual compararse, así que usamos esa referencia fija en su "
        f"lugar. Con esa vara, debería valer cerca de **${evaluation.fair_value:,.2f}**, un "
        f"**{abs(evaluation.margin):.0%} {word}** que el precio de hoy."
    )


def render_etf_list():
    st.title("¿A qué precio están estos ETFs hoy?")
    st.caption(
        "Un ETF no tiene estados financieros propios, así que las 6 fórmulas de acciones no "
        "aplican acá — mostramos otro set de métricas pensado para fondos índice."
    )

    # mismo patrón que Acciones: el número de jobs sale de ETF_TICKERS (única fuente de verdad),
    # y lo que ya se resolvió queda en ETF_EVAL_CACHE_KEY para que Portafolio lo reuse después.
    etf_results = _get_or_fetch(
        ETF_EVAL_CACHE_KEY, {ticker: (_cached_etf_evaluation, (ticker,)) for ticker in ETF_TICKERS}
    )

    # Precio de referencia en la BVC (Colombia) para los ETFs que también tienen CDI — mismo
    # criterio que en el detalle: informativo, no reemplaza el análisis en USD de arriba.
    bvc_tickers = [t for t in ETF_TICKERS if t in PORTFOLIO_CDI_TICKERS]
    bvc_prices = _parallel_fetch({t: (_cached_portfolio_price, (t,)) for t in bvc_tickers})

    columns = st.columns(3)
    for i, ticker in enumerate(ETF_TICKERS):
        with columns[i % 3]:
            with st.container(border=True):
                evaluation, error = etf_results[ticker]
                if error is not None:
                    st.markdown(f"**{ticker}**")
                    st.caption("No pudimos consultar este ETF ahora mismo.")
                    continue

                st.markdown(f"### {ticker}")
                st.caption(evaluation.name or "")
                bvc_price = bvc_prices[ticker][0] if ticker in bvc_prices else None
                if bvc_price is not None:
                    st.markdown(f"#### ${bvc_price:,.0f} COP")
                    st.caption(f"≈ ${evaluation.current_price:,.2f} USD")
                else:
                    st.markdown(f"#### ${evaluation.current_price:,.2f}")
                if evaluation.zone is not None:
                    st.markdown(zone_badge(evaluation.zone, small=True), unsafe_allow_html=True)

                st.write("")
                if st.button("Ver detalle →", key=f"detail_etf_{ticker}", use_container_width=True):
                    st.session_state.selected_etf = ticker
                    st.rerun()


def render_etf_detail(ticker: str):
    if st.session_state.get("_last_rendered_etf") != ticker:
        scroll_to_top()
        st.session_state._last_rendered_etf = ticker

    if st.button("← Volver a la lista", key="back_from_etf"):
        st.session_state.selected_etf = None
        st.session_state._last_rendered_etf = None
        st.rerun()

    try:
        evaluation = _cached_etf_evaluation(ticker)
    except DataError:
        st.error(f"No pudimos obtener información de **{ticker}** ahora mismo. Probá de nuevo en unos minutos.")
        return

    st.title(f"{ticker}")
    st.caption(evaluation.name or "")

    # El precio en COP (BVC) es el que importa para alguien que piensa en pesos, así que va
    # primero. La base del análisis de abajo (P/E, expense ratio, riesgo/retorno) sigue en USD
    # de todas formas — el CDI de la BVC no reporta esos campos y su histórico trae ruido de
    # mercado local, no del fondo en sí — por eso el aviso explícito.
    bvc_price_cop = None
    if ticker in PORTFOLIO_CDI_TICKERS:
        try:
            bvc_price_cop = _cached_portfolio_price(ticker)
        except DataError:
            bvc_price_cop = None

    if bvc_price_cop is not None:
        st.metric("Precio actual (BVC, Colombia)", f"${bvc_price_cop:,.0f} COP")
        st.caption(f"≈ ${evaluation.current_price:,.2f} USD — el análisis de abajo sigue basado en esta cotización en dólares.")
    else:
        st.metric("Precio actual", f"${evaluation.current_price:,.2f}")

    if evaluation.zone is not None:
        st.markdown(zone_badge(evaluation.zone), unsafe_allow_html=True)

    as_of = format_as_of(evaluation.data_as_of)
    if evaluation.is_stale:
        st.warning(
            "⚠️ No pudimos actualizar los datos ahora mismo (la fuente de datos alcanzó su límite). "
            f"Te mostramos la última información que guardamos, del {as_of}."
        )
    else:
        st.caption(f"Información actualizada el {as_of}")

    st.divider()
    st.subheader("💰 ¿Está caro el mercado hoy?")
    if evaluation.fair_value is not None:
        st.metric(
            "Precio 'justo' según P/E de referencia",
            f"${evaluation.fair_value:,.2f}",
            f"{evaluation.margin:+.1%}",
        )
        st.markdown(zone_badge(evaluation.zone), unsafe_allow_html=True)
    else:
        st.metric("Precio 'justo' según P/E de referencia", "No aplica")
    st.write("")
    st.write(explain_etf_valuation(evaluation))

    st.divider()
    st.subheader("📈 Tendencia")
    t1, t2 = st.columns(2)
    if evaluation.sma_50 is not None:
        t1.metric(
            "Vs. media móvil de 50 días",
            f"${evaluation.sma_50:,.2f}",
            f"{(evaluation.current_price - evaluation.sma_50) / evaluation.sma_50:+.1%}",
        )
    if evaluation.sma_200 is not None:
        t2.metric(
            "Vs. media móvil de 200 días",
            f"${evaluation.sma_200:,.2f}",
            f"{(evaluation.current_price - evaluation.sma_200) / evaluation.sma_200:+.1%}",
        )
    t3, t4 = st.columns(2)
    if evaluation.pct_from_52w_high is not None:
        t3.metric("Distancia al máximo de 52 semanas", f"{evaluation.pct_from_52w_high:+.1%}")
    if evaluation.pct_from_ath is not None:
        t4.metric("Distancia al máximo histórico", f"{evaluation.pct_from_ath:+.1%}")

    st.divider()
    st.subheader("📊 Riesgo y retorno")
    st.caption("Calculado sobre los últimos 5 años de precios disponibles.")
    r1, r2, r3 = st.columns(3)
    if evaluation.cagr_1y is not None:
        r1.metric("Retorno anualizado (1 año)", f"{evaluation.cagr_1y:+.1%}")
    if evaluation.cagr_3y is not None:
        r2.metric("Retorno anualizado (3 años)", f"{evaluation.cagr_3y:+.1%}")
    if evaluation.cagr_5y is not None:
        r3.metric("Retorno anualizado (5 años)", f"{evaluation.cagr_5y:+.1%}")
    r4, r5, r6 = st.columns(3)
    if evaluation.annualized_volatility is not None:
        r4.metric("Volatilidad anualizada", f"{evaluation.annualized_volatility:.1%}")
    if evaluation.sharpe_ratio is not None:
        r5.metric("Sharpe ratio", f"{evaluation.sharpe_ratio:.2f}")
    if evaluation.max_drawdown is not None:
        r6.metric("Máxima caída (5 años)", f"{evaluation.max_drawdown:.1%}")

    st.divider()
    st.subheader("💸 Costo")
    if evaluation.expense_ratio is not None:
        st.metric("Expense ratio (costo anual del fondo)", f"{evaluation.expense_ratio:.2%}")
    else:
        st.caption("No disponible.")
