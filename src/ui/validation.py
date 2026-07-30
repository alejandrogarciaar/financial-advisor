"""Pestaña "📊 Validación" — no evalúa si el precio de hoy está caro/barato, mide qué tan bien
funcionaron las señales que la app ya usa (backtest direccional a 1 año + historial de
veredictos). Extraído de app.py (que llegó a 2821 líneas) para modularizar."""

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from src.backtest import backtest_ticker
from src.config import TICKERS
from src.ui.shared import VERDICT_COLOR, VERDICT_LABEL, _parallel_fetch
from src.verdict_history import load_verdict_history

# TTL largo (24h) a propósito: a diferencia de _cached_evaluation (precio, que se mueve
# intradía), esto son estados financieros que no cambian en el día — no tiene sentido
# refrescarlo cada 15 minutos, y así una segunda corrida del backtest en el mismo día no
# vuelve a pegarle a los ~6 endpoints por ticker de backtest_ticker().
@st.cache_data(ttl=86400, show_spinner=False)
def _cached_backtest_ticker(ticker: str, provider: str, years_ago: int) -> dict:
    return backtest_ticker(ticker, provider=provider, years_ago=years_ago)


BACKTEST_YEARS_AGO = 1  # ver docstring de src/backtest.py: years_ago=2 no tiene suficiente
# historia de EPS para NINGÚN ticker (0/8) — no exponerlo como control configurable, ya que
# eso sugeriría que otros valores funcionan igual de bien.


def render_validation():
    """A diferencia de las otras pestañas, esta no evalúa si el precio de HOY está caro o
    barato — mide qué tan bien funcionaron las señales que la app ya usa. Ninguna sección corre
    sola al abrir la pestaña (el backtest es un botón, el historial solo lee un archivo local),
    así que no le agrega latencia a los otros reruns aunque st.tabs() no sea lazy."""
    st.title("📊 Validación")
    st.caption(
        "Evidencia de qué tan bien funcionaron las señales de este dashboard hasta ahora — no "
        "es una señal nueva, es un chequeo de las que ya existen."
    )

    st.divider()
    st.subheader("🔁 Backtest: ¿el veredicto de hace 1 año acertó?")
    st.caption(
        "Para cada acción, reconstruye qué habría dicho la triangulación de precio hace 1 año "
        "(con los datos que existían en ese momento) y lo compara contra el retorno real hasta "
        "hoy. No corre solo — son ~6 llamadas de red por ticker, pedilo cuando lo quieras ver."
    )
    if st.button("Correr backtest"):
        provider = st.session_state.get("last_provider", "yfinance")
        with st.spinner("Corriendo backtest..."):
            st.session_state["_backtest_results"] = _parallel_fetch(
                {t: (_cached_backtest_ticker, (t, provider, BACKTEST_YEARS_AGO)) for t in TICKERS}
            )

    results = st.session_state.get("_backtest_results")
    if results:
        rows = []
        failures = []
        for ticker in TICKERS:
            result, error = results.get(ticker, (None, None))
            if error is not None:
                failures.append((ticker, str(error)))
                continue
            if "error" in result:
                failures.append((ticker, result["error"]))
                continue
            if result["verdict_then"] == "cheap" and result["actual_return"] > 0:
                hit = "✅"
            elif result["verdict_then"] == "expensive" and result["actual_return"] < 0:
                hit = "✅"
            elif result["verdict_then"] == "mixed":
                hit = "—"
            else:
                hit = "❌"
            rows.append(
                {
                    "Ticker": ticker,
                    "Veredicto hace 1 año": result["headline_then"],
                    "Precio entonces": f"${result['price_then']:,.2f}",
                    "Precio ahora": f"${result['price_now']:,.2f}",
                    "Retorno real": f"{result['actual_return']:+.1%}",
                    "¿Acertó?": hit,
                }
            )
        if rows:
            st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
        if failures:
            st.caption("No se pudo calcular para: " + ", ".join(f"{t} ({reason})" for t, reason in failures))
        st.info(
            "⚠️ Esto es un chequeo direccional, no una validación estadística: muestra chica (8 "
            "mega-caps que ya 'ganaron' hasta ahora), usa el beta ACTUAL de la empresa en vez "
            "del de hace 1 año, y algunos tickers no tienen suficiente historia de EPS en "
            "yfinance para poder calcularse."
        )

    st.divider()
    st.subheader("📈 Historial de veredictos por ticker")
    st.caption(
        "Cómo cambió el veredicto de cada acción con el tiempo. Se registra automáticamente, "
        "una vez por día, la primera vez que ves cada ticker en Acciones — no hay forma de "
        "reconstruir veredictos de días pasados que la app no vio."
    )
    ticker = st.selectbox("Ticker", TICKERS, key="validation_ticker")
    history = load_verdict_history(ticker)
    if len(history) < 2:
        st.caption(
            "Todavía no hay suficiente historial para este ticker — el registro recién empieza "
            "a acumularse desde hoy. Volvé a revisar en unos días."
        )
    else:
        verdict_rank = {"expensive": 0, "mixed": 1, "cheap": 2}
        fig = go.Figure()
        fig.add_trace(
            go.Scatter(
                x=[e["date"] for e in history],
                y=[verdict_rank[e["verdict"]] for e in history],
                mode="lines",
                line=dict(color="rgba(137,135,129,0.4)", width=2),
                showlegend=False,
                hoverinfo="skip",
            )
        )
        for verdict in ["cheap", "mixed", "expensive"]:
            xs = [e["date"] for e in history if e["verdict"] == verdict]
            if not xs:
                continue
            fig.add_trace(
                go.Scatter(
                    x=xs,
                    y=[verdict_rank[verdict]] * len(xs),
                    mode="markers",
                    name=VERDICT_LABEL[verdict],
                    marker=dict(color=VERDICT_COLOR[verdict], size=10),
                    hovertemplate="%{x}<extra></extra>",
                )
            )
        fig.update_layout(
            plot_bgcolor="rgba(0,0,0,0)",
            paper_bgcolor="rgba(0,0,0,0)",
            font=dict(color="#898781"),
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
            margin=dict(l=10, r=10, t=10, b=10),
            height=280,
            xaxis=dict(showgrid=False),
            yaxis=dict(
                tickmode="array",
                tickvals=[0, 1, 2],
                ticktext=[VERDICT_LABEL["expensive"], VERDICT_LABEL["mixed"], VERDICT_LABEL["cheap"]],
                gridcolor="rgba(128,128,128,0.2)",
            ),
        )
        st.plotly_chart(fig, use_container_width=True)

        table_rows = [
            {
                "Fecha": e["date"],
                "Veredicto": VERDICT_LABEL[e["verdict"]],
                "Detalle": e["headline"],
                "Precio": f"${e['price']:,.2f}",
            }
            for e in reversed(history)
        ]
        st.dataframe(pd.DataFrame(table_rows), hide_index=True, use_container_width=True)
