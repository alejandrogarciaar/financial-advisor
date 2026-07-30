"""Pestaña "🪙 Cripto" — BTC/ETH/SOL únicamente, sobre datos de Binance (`src/data/
binance_client.py`), no yfinance. Combina el mismo cuerpo de indicadores que Especulación
(`render_speculation_indicators()`, importado de `src/ui/speculation.py`) con el Market Reaction
Zone Engine (`src/support_resistance.py`) — motor multi-metodología de zonas de soporte/
resistencia rediseñado para priorizar calidad de reacción por sobre cantidad de touches.
Extraído de app.py (que llegó a 2821 líneas) para modularizar — ver `us-stocks-cripto` skill
para el diseño completo, los bugs reales encontrados construyendo el motor, y el estado de la
validación fuera de muestra (pendiente de re-correr tras el rediseño)."""

from datetime import datetime, timedelta

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from src.config import CRYPTO_BINANCE_SYMBOLS
from src.data import binance_client
from src.data.errors import DataError
from src.support_resistance import SRConfig, SRLevel, compute_level_zone_reactions, detect_levels
from src.ui.shared import render_sticky_price
from src.ui.speculation import render_speculation_indicators


@st.cache_data(ttl=900, show_spinner=False)
def _cached_binance_historical_prices(binance_symbol: str):
    return binance_client.get_historical_prices(binance_symbol)


@st.cache_data(ttl=900, show_spinner=False)
def _cached_binance_historical_prices_4h(binance_symbol: str):
    return binance_client.get_historical_prices_intraday_4h(binance_symbol)


@st.cache_data(ttl=900, show_spinner=False)
def _cached_binance_historical_prices_1h(binance_symbol: str):
    return binance_client.get_historical_prices_intraday_1h(binance_symbol)


# TTL largo (6h) a propósito, a diferencia de _cached_evaluation (900s, sigue el precio en
# vivo) — los niveles de soporte/resistencia no se mueven al ritmo del precio intradía, así que
# no hay necesidad de recomputar este pipeline pesado (~5-25s según ticker — DBSCAN/KDE/RANSAC/
# Theil-Sen/Huber/Hough x 3 temporalidades x soporte y resistencia, más el refinamiento por
# optimización) cada 15 minutos. Mismo criterio que el TTL de 86400s de _cached_backtest_ticker
# (Validación): el costo real acá es de CÓMPUTO, no de red, así que el botón en render_crypto()
# (pestaña "🪙 Cripto") no lo dispara automáticamente en cada carga.
@st.cache_data(ttl=21600, show_spinner=False)
def _cached_sr_levels(
    ticker: str,
    enabled_methods: tuple[str, ...],
    top_n: int,
    min_touch_points: int,
    include_4h: bool,
    include_1h: bool,
):
    binance_symbol = CRYPTO_BINANCE_SYMBOLS[ticker]
    historical_prices, _ = _cached_binance_historical_prices(binance_symbol)
    timeframes = ("daily", "weekly", "monthly")
    if include_4h:
        timeframes += ("4h",)
    if include_1h:
        timeframes += ("1h",)
    intraday_4h = None
    if include_4h:
        intraday_4h, _ = _cached_binance_historical_prices_4h(binance_symbol)
    intraday_1h = None
    if include_1h:
        intraday_1h, _ = _cached_binance_historical_prices_1h(binance_symbol)
    config = SRConfig(
        enabled_methods=set(enabled_methods), top_n=top_n, min_touch_points=min_touch_points, timeframes=timeframes
    )
    return detect_levels(historical_prices, config, intraday_4h_prices=intraday_4h, intraday_1h_prices=intraday_1h)


SR_METHOD_LABELS = {
    "dbscan": "Clustering (DBSCAN)",
    "kde": "Densidad (KDE)",
    "ransac": "Línea robusta (RANSAC)",
    "theilsen": "Línea robusta (Theil-Sen)",
    "huber": "Línea robusta (Huber)",
    "hough": "Transformada de Hough",
    "optimize": "Optimización por touch points",
    "volume_profile": "Volume Profile",
    "vwap_confluence": "Confluencia con VWAP",
    "candle_confirmation": "Confirmación por velas",
    "volume_confirmation": "Confirmación por volumen",
    "multi_timeframe": "Multi-timeframe con jerarquía (mensual/semanal/diario, + 4h/1h si están activados)",
    "channels": "Detección de canales",
}

SR_TIMEFRAME_LABELS = {
    "1h": "1 hora",
    "4h": "4 horas",
    "daily": "Diaria",
    "weekly": "Semanal",
    "monthly": "Mensual",
}

# De más fina a más gruesa — mismo orden que TIMEFRAME_IMPORTANCE en support_resistance.py, solo
# invertido (acá es orden de chip/visualización, allá es peso institucional).
SR_TIMEFRAME_ORDER = {"1h": 0, "4h": 1, "daily": 2, "weekly": 3, "monthly": 4}

# RGB (no hex) porque el gráfico varía el canal alpha según el score de cada nivel — más
# opacidad = más confianza. Deliberadamente NO son los LEVEL_CHART_COLORS de Especulación: esa
# paleta tiene un color fijo por CATEGORÍA conocida (soporte semanal, resistencia anual, …);
# acá la cantidad de niveles es dinámica y no hay una identidad fija por nivel, así que se
# colorea por TIPO (soporte/resistencia/canal) en vez de por nivel individual.

SR_KIND_RGB = {"support": "34,139,34", "resistance": "214,69,65", "channel": "138,43,226"}

# Validación fuera de muestra bajo el score NUEVO ("Market Reaction Zone Engine", ver
# src/support_resistance.py) — re-corrida el mismo día del rediseño porque la fórmula vieja
# (touch_points con peso 30/100, sin reaction_magnitude/timeframe_weight) ya no existe y el
# resultado anterior (BTC soporte, TSLA soporte+resistencia) no se podía asumir vigente, mismo
# principio que ya aplicó este proyecto para Fibonacci/ADX/OBV. Mismo split cronológico 60/40,
# mismos 4 horizontes (5/10/20/30 días), mismos 3 umbrales de score (40/50/60) — script
# descartable, no en el repo. Universo: BTC/ETH/SOL (único universo de esta pestaña ahora que
# stocks salieron de acá; AAPL/TSLA no se re-testearon porque ya no son seleccionables en Cripto).
#
# Resultado: SOPORTE se validó en las 3 monedas — mismo signo en los 3 umbrales, los 4
# horizontes, train Y test (BTC, ETH y SOL, todas positivas: estar cerca de un soporte con
# score alto predijo retorno futuro MAYOR al promedio, consistente con el patrón "momentum, no
# reversión" ya documentado varias veces en este proyecto). RESISTENCIA no se validó en
# ninguna — el signo se invirtió train→test en las 3 monedas (para BTC/SOL incluso se invirtió
# en los 4 horizontes a la vez). Mejor cobertura que bajo el score viejo (que solo validaba BTC-
# soporte) — consistente con que el nuevo score pesa fuerte la calidad de la reacción (tamaño de
# rebote + volumen), no solo la cantidad de toques.

SR_VALIDATED_MIN_SCORE = 50.0

SR_VALIDATED_HORIZONS_DAYS = [5, 10, 20, 30]

SR_VALIDATED_TICKERS: dict[str, set[str]] = {
    "BTC": {"support"},
    "ETH": {"support"},
    "SOL": {"support"},
}


def render_advanced_levels_chart(historical_prices: list[dict], levels: list, ticker: str, window_days: int = 365) -> None:
    dated = sorted(historical_prices, key=lambda p: p["date"])
    if not dated:
        return
    cutoff = datetime.strptime(dated[-1]["date"], "%Y-%m-%d") - timedelta(days=window_days)
    window = [p for p in dated if datetime.strptime(p["date"], "%Y-%m-%d") >= cutoff]
    if len(window) < 2:
        return
    # offset = índice diario (en la serie COMPLETA que vio detect_levels) del primer día visible
    # acá — necesario porque slope/intercept de cada nivel están expresados en ese índice
    # completo, no en el recorte de la ventana visible.
    offset = len(dated) - len(window)
    day_indices = list(range(offset, len(dated)))
    x = [p["date"] for p in window]

    fig = go.Figure()
    fig.add_trace(
        go.Scatter(x=x, y=[p["close"] for p in window], mode="lines", name=f"Precio ({ticker})", line=dict(color="#2a78d6", width=3))
    )

    for lv in levels:
        if lv.kind == "channel":
            continue
        color = SR_KIND_RGB[lv.kind]
        alpha = 0.4 + 0.5 * (lv.confidence_score / 100)
        line_y = [lv.value_at(i) for i in day_indices]
        label = f"{'Soporte' if lv.kind == 'support' else 'Resistencia'} (score {lv.confidence_score:.0f})"
        fig.add_trace(
            go.Scatter(
                x=x, y=line_y, mode="lines", name=label,
                line=dict(color=f"rgba({color},{alpha:.2f})", width=2, dash="dash" if lv.kind == "support" else "dot"),
                hovertemplate=f"{label}: $%{{y:,.2f}}<extra></extra>",
            )
        )
        if lv.zone_low is not None and lv.zone_high is not None:
            zone_half = (lv.zone_high - lv.zone_low) / 2
            upper = [v + zone_half for v in line_y]
            lower = [v - zone_half for v in line_y]
            fig.add_trace(
                go.Scatter(
                    x=x + x[::-1], y=upper + lower[::-1], fill="toself",
                    fillcolor=f"rgba({color},0.08)", line=dict(width=0), showlegend=False, hoverinfo="skip",
                )
            )

    for ch in [lv for lv in levels if lv.kind == "channel"]:
        for side_lv, dash in ((ch.channel_support, "dash"), (ch.channel_resistance, "dot")):
            if side_lv is None:
                continue
            line_y = [side_lv.value_at(i) for i in day_indices]
            fig.add_trace(
                go.Scatter(
                    x=x, y=line_y, mode="lines", name=f"Canal {ch.channel_direction} (score {ch.confidence_score:.0f})",
                    line=dict(color=f"rgba({SR_KIND_RGB['channel']},0.7)", width=2, dash=dash),
                    hovertemplate=f"Canal {ch.channel_direction}: $%{{y:,.2f}}<extra></extra>",
                )
            )

    fig.update_layout(
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        font=dict(color="#898781"),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
        margin=dict(l=10, r=10, t=10, b=10),
        hovermode="x unified",
        height=450,
        xaxis=dict(showgrid=False),
        yaxis=dict(gridcolor="rgba(128,128,128,0.2)", tickprefix="$"),
    )
    st.plotly_chart(fig, use_container_width=True)


def render_crypto():
    """Pestaña dedicada a BTC/ETH/SOL — antes eran dos cosas separadas: estos 3 tickers vivían
    en Especulación (indicadores yfinance) y esta pestaña se llamaba "Niveles" (solo el motor
    multi-metodología, acciones + cripto, también yfinance). Ahora se unificaron: cripto salió
    de Especulación (que quedó solo-acciones, ver `render_speculation()`) y esta pestaña absorbió
    sus indicadores — reusando `render_speculation_indicators()`, la misma función que
    Especulación llama — más el motor de soportes/resistencias, todo sobre datos de Binance
    (`CRYPTO_BINANCE_SYMBOLS`, más historia real y velas de 4h nativas que yfinance). Ya no hace
    falta bifurcar por fuente de datos acá: TODO ticker en esta pestaña es cripto y va a
    Binance."""
    st.title("🪙 Cripto")
    st.caption(
        f"Datos de Binance (no yfinance) para BTC/ETH/SOL — más historia real (~5 años) y velas "
        "de 4h nativas. Especulación (acciones) sigue usando yfinance, sin cambios."
    )

    crypto_tickers = list(CRYPTO_BINANCE_SYMBOLS.keys())
    ticker = st.selectbox("Ticker", crypto_tickers, key="sr_ticker")

    try:
        historical_prices, _ = _cached_binance_historical_prices(CRYPTO_BINANCE_SYMBOLS[ticker])
    except DataError:
        st.error(f"No pudimos consultar {ticker} ahora mismo.")
        return

    closes = [p["close"] for p in historical_prices]
    if not closes:
        st.caption("No hay historial de precios disponible para este ticker.")
        return
    current_price = closes[-1]
    render_sticky_price("niveles", f"Precio actual — {ticker}", current_price, ticker)
    render_speculation_indicators("crypto", ticker, historical_prices, closes, current_price, is_crypto=True)

    st.divider()
    st.subheader("🧭 Market Reaction Zone Engine")
    st.caption(
        "Identifica zonas de soporte/resistencia con evidencia estadística suficiente, priorizando "
        "la CALIDAD de la reacción del mercado por sobre la cantidad de toques — tres rebotes "
        "fuertes con volumen alto valen más que diez toques sin reacción relevante. Combina "
        "clustering (DBSCAN), densidad (KDE), líneas de tendencia robustas (RANSAC/Theil-Sen/"
        "Huber), Transformada de Hough y multi-timeframe con jerarquía institucional→operativa "
        "(mensual/semanal/diario, + 4h/1h si los activás abajo) en un único score 0-100 por zona, "
        "con un paso de optimización que ajusta cada línea para maximizar la calidad de reacción "
        "mientras penaliza rupturas y distancia."
    )
    st.warning(
        "⚠️ **Es descriptivo, no una señal de trading**: identifica y puntúa zonas, no dice si "
        "conviene comprar o vender cerca de ellas. La cercanía a un soporte/resistencia muy "
        "tocado ya se probó como señal de entrada en una investigación anterior (clustering de "
        "múltiples toques, fuera de muestra) y no se sostuvo — para SOL incluso apuntó al "
        "revés (cerca de un soporte muy tocado predijo retorno MENOR, no mayor). Este motor es "
        "más sofisticado que aquel intento, pero esa misma pregunta de fondo no fue re-validada "
        "todavía con esta versión del score (ver más abajo)."
    )

    st.divider()
    with st.expander("⚙️ Configuración avanzada"):
        selected_methods = st.multiselect(
            "Metodologías activas",
            list(SR_METHOD_LABELS.keys()),
            default=list(SR_METHOD_LABELS.keys()),
            format_func=lambda k: SR_METHOD_LABELS[k],
            key="sr_enabled_methods",
        )
        sr_col1, sr_col2 = st.columns(2)
        top_n = sr_col1.slider("Cantidad de niveles a mostrar", 3, 15, 8, key="sr_top_n")
        min_touch_points = sr_col2.slider("Mínimo de touch points", 1, 5, 3, key="sr_min_touches")
        sr_tf_col1, sr_tf_col2 = st.columns(2)
        include_4h = sr_tf_col1.checkbox(
            "Incluir temporalidad 4h (velas nativas de Binance)",
            value=False,
            key="sr_include_4h",
            help=(
                "Binance ofrece velas de 4h nativas (a diferencia de yfinance, que no tiene ese "
                "intervalo) con hasta ~5 años de historia — pero sigue siendo una consulta de "
                "red nueva (no reusa datos ya en memoria como semanal/mensual), así que tarda un "
                "poco más en calcular."
            ),
        )
        include_1h = sr_tf_col2.checkbox(
            "Incluir temporalidad 1h (velas nativas de Binance)",
            value=False,
            key="sr_include_1h",
            help=(
                "Temporalidad 'operativa' del Market Reaction Zone Engine — pesa menos que "
                "diario/semanal/mensual en el score (ver jerarquía de temporalidad), pero suma "
                "confluencia y detecta niveles de más corto plazo. También implica una consulta "
                "de red nueva."
            ),
        )

    result_key = f"sr_levels_result_{ticker}"
    if st.button("🔍 Calcular niveles multi-metodología", key="sr_compute_button"):
        with st.spinner("Corriendo clustering, KDE, líneas robustas, Hough y más — puede tardar unos segundos..."):
            st.session_state[result_key] = _cached_sr_levels(
                ticker, tuple(sorted(selected_methods)), top_n, min_touch_points, include_4h, include_1h
            )

    sr_levels = st.session_state.get(result_key)
    if sr_levels is None:
        st.caption(
            "Sin calcular todavía — apretá el botón de arriba. No se corre automáticamente "
            "porque implica varios algoritmos de clustering/regresión y puede tardar unos "
            "segundos."
        )
    elif not sr_levels:
        st.caption("No se detectaron niveles con suficiente evidencia para este ticker con la configuración actual.")
    else:
        # Filtro puramente de VISUALIZACIÓN — no toca sr_levels ni SRConfig, solo decide cuáles
        # de los niveles ya calculados se muestran en la tabla/gráfico. Bajarlo acerca la vista a
        # planes de corto plazo (niveles cerca del precio de hoy); subirlo la abre a planes de
        # largo plazo. La sección "Lectura validada" de abajo sigue mirando sr_levels COMPLETO,
        # sin filtrar — el hallazgo validado no debe desaparecer solo porque el filtro de
        # visualización quedó angosto.
        max_distance_pct = st.slider(
            "Mostrar niveles a menos de X% del precio actual",
            min_value=5,
            max_value=100,
            value=20,
            step=5,
            key="sr_max_distance_pct",
            help=(
                "Solo filtra qué se muestra acá abajo — no recalcula ni cambia los niveles "
                "detectados. Un % chico (ej. 20%) sirve para planes de corto plazo, niveles muy "
                "cerca del precio actual; uno grande (ej. 50-100%) muestra también niveles más "
                "lejanos, útiles para planificar a más largo plazo."
            ),
        )

        # Igual que el filtro de %: puramente de visualización. Las opciones salen de lo que
        # realmente aparece en sr_levels (no de una lista fija) — así, si "4h" no se incluyó al
        # calcular, ese chip ni aparece, en vez de mostrar una opción vacía/engañosa.
        available_timeframes = sorted(
            {tf for lv in sr_levels for tf in lv.timeframes}, key=lambda tf: SR_TIMEFRAME_ORDER.get(tf, 99)
        )
        selected_timeframes = st.multiselect(
            "Temporalidades a mostrar",
            available_timeframes,
            default=available_timeframes,
            format_func=lambda tf: SR_TIMEFRAME_LABELS.get(tf, tf),
            key="sr_timeframe_filter",
            help="También es solo de visualización — muestra un nivel si apareció en CUALQUIERA de las temporalidades elegidas.",
        )

        def _level_distance_pct(lv: SRLevel) -> float:
            if lv.kind == "channel":
                sides = [abs(s.distance_to_price_pct) for s in (lv.channel_support, lv.channel_resistance) if s is not None]
                return min(sides) if sides else float("inf")
            return abs(lv.distance_to_price_pct)

        filtered_levels = [
            lv
            for lv in sr_levels
            if _level_distance_pct(lv) <= max_distance_pct / 100 and set(lv.timeframes) & set(selected_timeframes)
        ]

        if not filtered_levels:
            st.caption(
                f"Ningún nivel pasa los filtros actuales (menos de {max_distance_pct}% del precio "
                "actual y alguna de las temporalidades elegidas) — ampliá el % o sumá más "
                "temporalidades para ver más."
            )
        else:
            rows = []
            for lv in filtered_levels:
                tipo = {"support": "🟢 Soporte", "resistance": "🔴 Resistencia"}.get(
                    lv.kind, f"🟣 Canal ({lv.channel_direction})"
                )
                rows.append(
                    {
                        "Tipo": tipo,
                        "Precio central": f"${lv.price:,.2f}" if lv.price is not None else "—",
                        "Zona": f"${lv.zone_low:,.2f} – ${lv.zone_high:,.2f}" if lv.zone_low is not None else "—",
                        "Score": round(lv.confidence_score, 1),
                        "Touches": lv.touches,
                        "Rebotes": lv.rebounds,
                        "Magnitud rebote (ATR)": round(lv.avg_rebound_magnitude_atr, 2),
                        "Rupturas": lv.breaks,
                        "Re-test": "Sí" if lv.retested else "—",
                        "Antigüedad (barras)": lv.age_bars,
                        "Temporalidades": ", ".join(lv.timeframes),
                        "Métodos": ", ".join(lv.methods),
                        "Dist. al precio actual": f"{lv.distance_to_price_pct:+.1%}" if lv.kind != "channel" else "—",
                    }
                )
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
            render_advanced_levels_chart(historical_prices, filtered_levels, ticker)

        st.divider()
        st.subheader("📋 Lectura validada fuera de muestra")
        validated_kinds = SR_VALIDATED_TICKERS.get(ticker, set())
        current_price = closes[-1]
        if not validated_kinds:
            st.caption(
                "No hay evidencia validada fuera de muestra para este ticker todavía (se probó "
                "BTC, ETH, SOL, AAPL y TSLA con el mismo split cronológico 60/40 que el resto de "
                "las señales de esta app; solo BTC —soporte— y TSLA —soporte y resistencia— "
                "mostraron el mismo signo de efecto en los 4 horizontes probados). Esto no es "
                "evidencia de que NO haya señal para este ticker — solo que todavía no se "
                "confirmó, y el resto de esta tabla se queda puramente descriptivo."
            )
        else:
            any_hit = False
            for kind in ("support", "resistance"):
                if kind not in validated_kinds:
                    continue
                qualifying = [
                    lv for lv in sr_levels
                    if lv.kind == kind and lv.confidence_score >= SR_VALIDATED_MIN_SCORE and lv.zone_low is not None
                ]
                hit = any(lv.zone_low <= current_price <= lv.zone_high for lv in qualifying)
                if not hit:
                    continue
                any_hit = True
                reactions = compute_level_zone_reactions(
                    historical_prices, sr_levels, kind, SR_VALIDATED_MIN_SCORE, SR_VALIDATED_HORIZONS_DAYS
                )
                phrases = [
                    f"{r.mean_return:+.1%} a {r.horizon_days} días (win rate {r.win_rate:.0%}, {r.observations} casos)"
                    for r in reactions
                    if r.mean_return is not None
                ]
                detail = " · ".join(phrases) if phrases else "sin suficientes observaciones recientes"
                kind_label = "soporte" if kind == "support" else "resistencia"
                validated_for = "/".join(sorted(t for t, ks in SR_VALIDATED_TICKERS.items() if kind in ks))
                st.success(
                    f"**{ticker} está hoy dentro de la zona de un {kind_label} con score ≥ "
                    f"{SR_VALIDATED_MIN_SCORE:.0f}.** Para este ticker y este tipo de nivel, la "
                    "cercanía a una zona así mostró un retorno futuro distinto al promedio "
                    "general, con el mismo signo en el 60% más viejo del historial y en el 40% "
                    f"más nuevo, en los 4 horizontes probados: {detail}. Validado únicamente "
                    f"para {validated_for} ({kind_label}) — no generalizar a otros tickers ni a "
                    "otros tipos de nivel sin repetir la misma prueba fuera de muestra."
                )
            if not any_hit:
                st.info(
                    f"{ticker} tiene evidencia validada fuera de muestra para este tipo de "
                    f"análisis, pero el precio actual no está dentro de la zona de ningún nivel "
                    f"calificado (score ≥ {SR_VALIDATED_MIN_SCORE:.0f}) en este momento — el "
                    "resto de esta tabla se queda puramente descriptivo hasta que eso cambie."
                )
