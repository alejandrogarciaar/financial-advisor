"""Pestaña "🪙 Cripto" — BTC/ETH/SOL únicamente, sobre datos de Binance (`src/data/
binance_client.py`), no yfinance. Combina el mismo cuerpo de indicadores que Especulación
(`render_speculation_indicators()`, importado de `src/ui/speculation.py`) con el Market Reaction
Zone Engine (`src/support_resistance.py`) — motor multi-metodología de zonas de soporte/
resistencia rediseñado para priorizar calidad de reacción por sobre cantidad de touches.
Extraído de app.py (que llegó a 2821 líneas) para modularizar — ver `financial-advisor-cripto` skill
para el diseño completo, los bugs reales encontrados construyendo el motor, y el estado de la
validación fuera de muestra (re-corrida en dos rondas tras el rediseño del score; `SR_VALIDATED_TICKERS`
sigue vacío — ver el comentario junto a esa constante más abajo)."""

from datetime import datetime, timedelta

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from src.config import CRYPTO_BINANCE_SYMBOLS, SOSOVALUE_API_KEY
from src.crecetrader import Level, LevelEngine, Role, nearest_levels, session_envelope
from src.crecetrader_inputs import infer_inputs
from src.data import binance_client, sosovalue_client
from src.data.errors import DataError
from src.speculation import (
    VWAP_REACTION_ATR_THRESHOLD,
    VWAP_WINDOWS_DAYS,
    classify_wyckoff_spring_series,
    compute_vwap_reactions,
    compute_wyckoff_spring_reactions,
    distance_to_vwap_atr,
    rolling_vwap_series,
)
from src.support_resistance import (
    SRConfig,
    SRLevel,
    compute_level_zone_reactions,
    detect_levels,
    score_percentile_threshold,
)
from src.ui.shared import (
    FEAR_GREED_BANDS,
    FEAR_GREED_LABEL_ES,
    SR_KIND_RGB,
    SR_METHOD_LABELS,
    SR_TIMEFRAME_LABELS,
    SR_TIMEFRAME_ORDER,
    _cached_fear_greed_index,
    fear_greed_badge,
    render_advanced_levels_chart,
    render_sticky_price,
)
from src.ui.speculation import render_speculation_indicators


def render_fear_greed_index() -> None:
    """Índice de Miedo y Codicia (alternative.me) — UN solo valor para todo el mercado cripto, no
    por ticker (ver docstring de `src/data/fear_greed_client.py`) — por eso se renderiza ANTES
    del selector de ticker, como contenido estático que no cambia según qué ticker esté
    seleccionado más abajo. Puramente descriptivo, no una señal validada por este proyecto —
    mismo criterio de disclosure que ADX/OBV en Especulación."""
    try:
        data, meta = _cached_fear_greed_index()
    except DataError as exc:
        st.caption(f"No pudimos consultar el Índice de Miedo y Codicia ahora mismo. Detalle: {exc}")
        return

    value = data["value"]
    label_es = FEAR_GREED_LABEL_ES.get(data["classification"], data["classification"])
    band_color = next(color for lo, hi, color in FEAR_GREED_BANDS if lo <= value < hi or value >= 100 >= hi)

    st.subheader("😨🤑 Índice de Miedo y Codicia (cripto)")
    fig = go.Figure(
        go.Indicator(
            mode="gauge+number",
            value=value,
            number={"font": {"size": 36}},
            gauge={
                "axis": {"range": [0, 100], "tickcolor": "#898781"},
                "bar": {"color": "rgba(11,11,11,0.55)", "thickness": 0.3},
                "bgcolor": "rgba(0,0,0,0)",
                "borderwidth": 0,
                "steps": [{"range": [lo, hi], "color": color} for lo, hi, color in FEAR_GREED_BANDS],
            },
        )
    )
    fig.update_layout(
        height=180,
        margin=dict(l=20, r=20, t=10, b=10),
        paper_bgcolor="rgba(0,0,0,0)",
        font=dict(color="#898781"),
    )
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})
    st.markdown(fear_greed_badge(band_color, label_es), unsafe_allow_html=True)

    cache_note = " (dato en caché — no pudimos actualizarlo recién)" if meta["from_cache"] else ""
    st.caption(
        f"Fuente: alternative.me, actualizado el {data['timestamp']}{cache_note}. Es un solo "
        "valor para todo el mercado cripto, no cambia según el ticker elegido abajo, y es "
        "puramente descriptivo — no es una señal validada por este proyecto."
    )


# Rechazado para las 8 acciones de TICKERS (ver design-history de financial-advisor-speculation) —
# re-testeado para cripto y validado limpio para BTC/ETH en los 3 lookbacks barridos (10/20/30),
# sin fragilidad de parámetro. SOL no validó en ningún lookback. El signo es AL REVÉS de la
# teoría de Wyckoff — ver docstring de render_wyckoff_spring() y el design-history de
# financial-advisor-cripto para el detalle completo y los números.
WYCKOFF_SPRING_VALIDATED_TICKERS = {"BTC", "ETH"}


WYCKOFF_SPRING_HEADLINE_HORIZON = 20  # un solo horizonte para la probabilidad — no una tabla


def render_wyckoff_spring(ticker: str, historical_prices: list[dict], closes: list[float]) -> None:
    """Sección PROPIA, no dentro de render_speculation_indicators() — mismo criterio que Golden
    Cross en speculation.py: esto nunca se testeó para acciones, así que no debe aparecer
    silenciosamente en Especulación. Reducido a lo mínimo por pedido directo del usuario (2 veces:
    primero "no es para nada clara", después "puede ser más sencillo") — solo estado (¿hay un
    spring activo hoy?) + una probabilidad, nada de tabla ni de explicación de la metodología."""
    st.divider()
    st.subheader("🌊 Wyckoff Spring")
    if ticker not in WYCKOFF_SPRING_VALIDATED_TICKERS:
        st.caption(f"Todavía no hay evidencia suficiente para {ticker}.")
        return

    lows = [p["low"] for p in historical_prices]
    if len(lows) != len(closes) or not closes:
        st.caption("No hay suficiente historial todavía para calcular esto.")
        return

    springs = classify_wyckoff_spring_series(lows, closes)
    reactions = {r.horizon_days: r for r in compute_wyckoff_spring_reactions(lows, closes)}

    if springs and springs[-1]:
        st.error(f"🔴 Spring activo hoy en {ticker}.")
    else:
        st.info(f"⚪ Sin spring activo hoy en {ticker}.")

    r = reactions.get(WYCKOFF_SPRING_HEADLINE_HORIZON)
    if r is not None and r.win_rate is not None:
        st.metric(
            f"Probabilidad histórica de que {ticker} suba a los {WYCKOFF_SPRING_HEADLINE_HORIZON} días de un spring",
            f"{r.win_rate:.0%}",
        )
    else:
        st.caption("Sin observaciones suficientes todavía.")


# Primeros 6 slots de la paleta categórica validada por la skill dataviz (mismos hex que
# FAMILY_COLOR en stocks.py: azul/naranja/aqua/amarillo/magenta/verde, en el mismo orden fijo,
# nunca ciclada) — hasta 6 fondos llevan color propio; el resto se agrupa en "Otros" con tinta
# muted en vez de generar un 7mo/8vo/9no hue (BTC tiene 13 fondos listados hoy: distinguir todos
# por color violaría la regla de la skill de nunca generar una hue de más).
ETF_FLOWS_TOP_N = 6
ETF_FLOWS_CATEGORICAL = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300"]
ETF_FLOWS_OTHER_COLOR = "#898781"  # tinta "muted" ya usada en el resto de la app, no un 7mo hue
ETF_FLOWS_AUM_COLOR = "#2a78d6"  # magnitud (ranking de AUM) -> un solo hue secuencial, no identidad
ETF_FLOWS_POSITIVE_COLOR = "#2a78d6"  # par divergente azul/rojo de la skill dataviz: entra plata
ETF_FLOWS_NEGATIVE_COLOR = "#e34948"  # sale plata


def render_etf_flows(ticker: str) -> None:
    """Sección PROPIA, al final de la pestaña — flujos institucionales de los ETFs spot de
    `ticker` vía `src/data/sosovalue_client.py`. Descriptivo, mismo criterio que el Índice de
    Miedo y Codicia: no es una señal de precio validada por este proyecto, solo contexto sobre
    cuánto dinero institucional entra/sale por este canal y cómo se reparte entre emisores.

    Button-gated (mismo patrón que el Zone Engine y el backtest de Validación), a propósito NO
    eager-fetch: listar + snapshot + historial de cada fondo son hasta ~2 llamadas por fondo, y
    BTC tiene 13 fondos listados hoy — más llamadas de las que el tier gratuito de SoSoValue
    permite en una ráfaga (20/min). `sosovalue_client._get()` espacia cada request a >=3.1s de la
    anterior para no depender de reaccionar recién después de un 429 (ver ese módulo) — igual
    puede tardar más de un minuto para BTC (~26 llamadas pausadas), por eso el `st.spinner` de
    abajo. Cacheado con TTL de 24h (`_cached_etf_*` más abajo) — la propia API solo actualiza
    estos datos una vez por día (el snapshot trae la fecha de la última sesión liquidada, no
    "ahora mismo"), así que pedirlo más seguido no trae nada nuevo ni evita la espera la próxima
    vez dentro de esas 24h."""
    st.divider()
    st.subheader("🏦 ETFs spot — flujos institucionales")
    st.caption(
        f"De dónde entra y sale la plata institucional en {ticker} a través de sus ETFs spot "
        "listados en EE. UU. (SoSoValue) — AUM y flujo neto por emisor, últimos ~30 días. "
        "Descriptivo, mismo criterio que el Índice de Miedo y Codicia: no es una señal de precio "
        "validada por este proyecto."
    )

    if not SOSOVALUE_API_KEY:
        st.caption("Sección no disponible: falta configurar `SOSOVALUE_API_KEY` (ver `.env.example`).")
        return

    if not st.button(f"Consultar ETFs de {ticker}", key="etf_flows_button"):
        st.caption(
            "No se pide automáticamente: son hasta ~2 llamadas por fondo listado (AUM + "
            "historial) espaciadas para respetar el límite gratuito de SoSoValue (20/min) — "
            "para BTC (13 fondos) puede tardar más de un minuto en cargar."
        )
        return

    try:
        fund_list, _ = _cached_etf_list(ticker)
    except DataError as exc:
        st.error(f"No pudimos consultar los ETFs de {ticker} ahora mismo.")
        st.caption(f"Detalle: {exc}")
        return

    if not fund_list:
        st.caption(f"SoSoValue no tiene ETFs spot listados para {ticker} todavía.")
        return

    rows = []
    with st.spinner(f"Consultando {len(fund_list)} fondos en SoSoValue..."):
        for fund in fund_list:
            fund_ticker = fund.get("ticker")
            if not fund_ticker:
                continue
            try:
                snapshot, _ = _cached_etf_snapshot(fund_ticker)
                history, _ = _cached_etf_history(fund_ticker)
            except DataError:
                continue  # un fondo puntual que falla no tira abajo el resto de la sección
            rows.append(
                {
                    "ticker": fund_ticker,
                    "name": fund.get("name", fund_ticker),
                    "aum": snapshot.get("net_assets") or 0.0,
                    "history": history,
                }
            )

    if not rows:
        st.caption("No se pudo traer información de ningún fondo ahora mismo.")
        return

    rows.sort(key=lambda r: r["aum"], reverse=True)
    total_aum = sum(r["aum"] for r in rows)
    st.metric(f"AUM total — ETFs de {ticker}", f"${total_aum / 1e6:,.1f}M", f"{len(rows)} fondos")

    # --- AUM por fondo: magnitud -> un solo hue secuencial, sin necesidad de identidad ---
    st.markdown("**AUM por fondo**")
    fig_aum = go.Figure(
        go.Bar(
            x=[r["aum"] / 1e6 for r in rows],
            y=[r["ticker"] for r in rows],
            orientation="h",
            marker=dict(color=ETF_FLOWS_AUM_COLOR),
            text=[f"${r['aum'] / 1e6:,.0f}M" for r in rows],
            textposition="outside",
            hovertemplate="%{y}: $%{x:,.1f}M<extra></extra>",
        )
    )
    fig_aum.update_layout(
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        font=dict(color="#898781"),
        margin=dict(l=10, r=10, t=10, b=10),
        height=max(220, 32 * len(rows)),
        xaxis=dict(gridcolor="rgba(128,128,128,0.2)", tickprefix="$", ticksuffix="M"),
        yaxis=dict(autorange="reversed", showgrid=False),
        showlegend=False,
    )
    st.plotly_chart(fig_aum, use_container_width=True)

    all_dates = sorted({h["date"] for r in rows for h in r["history"]})

    # --- Flujo neto diario agregado: polaridad -> par divergente azul (entra)/rojo (sale) ---
    if all_dates:
        st.markdown(f"**Flujo neto diario agregado — {ticker}** (todos los emisores)")
        daily_total = {
            d: sum((h.get("net_inflow") or 0.0) for r in rows for h in r["history"] if h["date"] == d)
            for d in all_dates
        }
        values = [daily_total[d] / 1e6 for d in all_dates]
        colors = [ETF_FLOWS_POSITIVE_COLOR if v >= 0 else ETF_FLOWS_NEGATIVE_COLOR for v in values]
        fig_flow = go.Figure(
            go.Bar(
                x=all_dates, y=values, marker=dict(color=colors),
                hovertemplate="%{x}: $%{y:,.1f}M<extra></extra>",
            )
        )
        fig_flow.update_layout(
            plot_bgcolor="rgba(0,0,0,0)",
            paper_bgcolor="rgba(0,0,0,0)",
            font=dict(color="#898781"),
            margin=dict(l=10, r=10, t=10, b=10),
            height=320,
            xaxis=dict(showgrid=False),
            yaxis=dict(
                gridcolor="rgba(128,128,128,0.2)", tickprefix="$", ticksuffix="M", zerolinecolor="#c3c2b7"
            ),
            showlegend=False,
        )
        st.plotly_chart(fig_flow, use_container_width=True)

    # --- Flujo acumulado por fondo: identidad -> categórico fijo, top N + "Otros" agrupado ---
    top_rows = rows[:ETF_FLOWS_TOP_N]
    other_rows = rows[ETF_FLOWS_TOP_N:]
    if all_dates:
        titulo = f"**Flujo acumulado por fondo** (top {len(top_rows)} por AUM"
        titulo += f" + Otros [{len(other_rows)}])" if other_rows else ")"
        st.markdown(titulo)
        fig_cum = go.Figure()
        for idx, r in enumerate(top_rows):
            by_date = {h["date"]: h.get("net_inflow") or 0.0 for h in r["history"]}
            running, ys = 0.0, []
            for d in all_dates:
                running += by_date.get(d, 0.0)
                ys.append(running / 1e6)
            fig_cum.add_trace(
                go.Scatter(
                    x=all_dates, y=ys, mode="lines", name=r["ticker"],
                    line=dict(color=ETF_FLOWS_CATEGORICAL[idx % len(ETF_FLOWS_CATEGORICAL)], width=2),
                    hovertemplate=f"{r['ticker']}: $%{{y:,.1f}}M<extra></extra>",
                )
            )
        if other_rows:
            running, ys = 0.0, []
            for d in all_dates:
                running += sum(
                    (h.get("net_inflow") or 0.0) for r in other_rows for h in r["history"] if h["date"] == d
                )
                ys.append(running / 1e6)
            fig_cum.add_trace(
                go.Scatter(
                    x=all_dates, y=ys, mode="lines", name=f"Otros ({len(other_rows)})",
                    line=dict(color=ETF_FLOWS_OTHER_COLOR, width=2, dash="dot"),
                    hovertemplate="Otros: $%{y:,.1f}M<extra></extra>",
                )
            )
        fig_cum.update_layout(
            plot_bgcolor="rgba(0,0,0,0)",
            paper_bgcolor="rgba(0,0,0,0)",
            font=dict(color="#898781"),
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
            margin=dict(l=10, r=10, t=10, b=10),
            hovermode="x unified",
            height=380,
            xaxis=dict(showgrid=False),
            yaxis=dict(
                gridcolor="rgba(128,128,128,0.2)", tickprefix="$", ticksuffix="M", zerolinecolor="#c3c2b7"
            ),
        )
        st.plotly_chart(fig_cum, use_container_width=True)

    # Tabla — companion de los 3 gráficos de arriba (la skill dataviz pide una vista de tabla
    # siempre que el color cargue significado, mismo patrón que el resto de esta pestaña).
    with st.expander("Ver los fondos en tabla"):
        table = pd.DataFrame(
            [
                {
                    "Ticker": r["ticker"],
                    "Nombre": r["name"],
                    "AUM (USD)": r["aum"],
                    "Flujo histórico API (~30d, USD)": sum(h.get("net_inflow") or 0.0 for h in r["history"]),
                }
                for r in rows
            ]
        )
        st.dataframe(table, use_container_width=True, hide_index=True)


# Identidad de cada ventana en el gráfico. Las 3 ventanas tienen un orden natural (corta →
# larga), así que además del color va un `dash` distinto por ventana: identidad por color, orden
# por trazo — la línea más "sólida" es el ancla más larga. El precio se queda con el mismo azul
# que usa en render_advanced_levels_chart(), para que "azul = precio" siga siendo cierto en toda
# la pestaña. Los 3 hex son los mismos de FAMILY_COLOR en stocks.py (paleta categórica ya usada
# en el proyecto) + el violeta de SR_KIND_RGB, no una paleta nueva; validados con el script de la
# skill dataviz (separación CVD OK sobre fondo claro), y el valor exacto de cada línea queda
# igual visible como texto en las métricas y en la tabla de abajo, no solo por color.
VWAP_PRICE_COLOR = "#2a78d6"
VWAP_COLOR = {7: "#eb6834", 30: "#1baf7a", 365: "#8a2be2"}
VWAP_DASH = {7: "dot", 30: "dash", 365: "solid"}
VWAP_WINDOW_LABEL = {7: "7 días", 30: "30 días", 365: "1 año"}
VWAP_CHART_WINDOW_DAYS = 365

# Validación fuera de muestra corrida localmente (`scripts/vwap_oos_validate.py`, Binance,
# 2021-08-18 a 2026-08-16, ~1825 días por ticker) — mismo criterio estricto que el resto del
# proyecto: split cronológico 60/40, los 4 horizontes (5/10/20/30 días) con el mismo signo,
# barrido de 3 umbrales (0.5/1.0/1.5 ATR) sin fragilidad, y etapa 2 de redundancia contra el
# régimen de tendencia que ya usa el "Plan de DCA sugerido" (`classify_regime_series`).
#   - BTC, VWAP de 365 días, arriba Y abajo: VALIDADO, y agrega información sobre el régimen
#     (fuerte y/o débil según el lado) — no es el régimen restado con otro nombre.
#   - SOL, VWAP de 30 días, abajo: VALIDADO, agrega información sobre el régimen débil (el
#     régimen fuerte no tuvo suficientes días en la intersección para decidir esa parte).
#   - SOL, VWAP de 365 días, arriba: pasó la etapa 1 (barrido de umbrales) pero FALLÓ la etapa 2
#     en los dos regímenes — es el régimen de tendencia restado con otro nombre, no señal nueva.
#     El script lo incluye en su impresión final igual (su propio docstring deja el filtro de
#     etapa 2 para hacerse a mano) — deliberadamente NO se pega acá.
#   - ETH: nada validó, en ninguna ventana ni umbral — mismo resultado que Fibonacci/ADX/OBV.
VWAP_VALIDATED_COMBOS: dict[str, set[tuple[int, str]]] = {
    "BTC": {(365, "abajo"), (365, "arriba")},
    "ETH": set(),
    "SOL": {(30, "abajo")},
}

VWAP_REACTION_HEADLINE_HORIZON = 20  # mismo criterio que WYCKOFF_SPRING_HEADLINE_HORIZON: un solo
# horizonte para la lectura en vivo, no una tabla — la tabla completa (4 horizontes) ya la imprime
# scripts/vwap_oos_validate.py para quien quiera el detalle.


def render_vwap(ticker: str, historical_prices: list[dict], closes: list[float], current_price: float) -> None:
    """Sección PROPIA de esta pestaña, no dentro de `render_speculation_indicators()` — mismo
    criterio que Wyckoff Spring y Golden Cross: nunca se probó nada de esto para acciones, así que
    no debe aparecer solo en Especulación por compartir el cuerpo de indicadores.

    El VWAP ya existía en el proyecto pero era invisible y no hacía nada: `_rolling_vwap()` se
    calculaba dentro del Market Reaction Zone Engine solo como un booleano ("¿pasa algún VWAP a
    menos de 0.5 ATR de este nivel?"), ese componente (`vwap_confluence`) pesa 0 en
    `DEFAULT_WEIGHTS` desde el rediseño del score, y `component_scores` no se renderiza en ningún
    lado — o sea que prender/apagar "Confluencia con VWAP" en las metodologías activas no cambiaba
    nada observable. Esta sección lo saca a la superficie como lo que es: un indicador clásico,
    descriptivo por defecto, con un estado VALIDADO fuera de muestra para combos puntuales
    (`VWAP_VALIDATED_COMBOS` más abajo — ver el caption del final para el detalle y los combos
    que quedaron afuera a propósito)."""
    st.divider()
    st.subheader("🎯 VWAP — precio promedio ponderado por volumen")
    st.caption(
        "El VWAP es el precio promedio al que realmente se operó en una ventana, ponderado por "
        "volumen — no un promedio de cierres como una media móvil. Pensalo como el **costo "
        "promedio del mercado**: si el precio de hoy está por encima del VWAP de 30 días, el "
        "comprador promedio del último mes está en ganancia; si está por debajo, está en pérdida. "
        "Por eso se lo suele mirar como referencia de \"caro/barato respecto de lo que pagó el "
        "resto\", y no como una señal de dirección."
    )

    dates = [p["date"] for p in historical_prices]
    highs = [p.get("high") for p in historical_prices]
    lows = [p.get("low") for p in historical_prices]
    volumes = [p.get("volume") for p in historical_prices]
    if len(dates) != len(closes) or any(h is None or l is None for h, l in zip(highs, lows)):
        st.caption("No hay suficiente historial (o datos de máximos/mínimos) para calcular el VWAP.")
        return

    # Solo se muestra una ventana si el historial la cubre de verdad: con 3 días de datos, el
    # "VWAP de 1 año" da exactamente el mismo número que el de 7 días (misma ventana efectiva) y
    # la etiqueta pasa a mentir. Para BTC/ETH/SOL en Binance esto nunca se activa (hay años de
    # historia); es la misma defensa que el resto de los indicadores hacen con `period`.
    history_days = (
        datetime.strptime(dates[-1][:10], "%Y-%m-%d") - datetime.strptime(dates[0][:10], "%Y-%m-%d")
    ).days
    windows = [w for w in VWAP_WINDOWS_DAYS if history_days >= w]
    if not windows:
        st.caption(
            f"No hay suficiente historial para calcular el VWAP: hacen falta al menos "
            f"{min(VWAP_WINDOWS_DAYS)} días y hay {history_days}."
        )
        return

    series_by_window = {w: rolling_vwap_series(dates, highs, lows, closes, volumes, w) for w in windows}
    latest = {w: s[-1] for w, s in series_by_window.items() if s and s[-1] is not None}
    if not latest:
        st.caption("No hay datos de volumen suficientes para calcular el VWAP de este ticker.")
        return

    cols = st.columns(len(windows))
    for col, window in zip(cols, windows):
        value = latest.get(window)
        if value is None:
            col.metric(f"VWAP {VWAP_WINDOW_LABEL[window]}", "—")
        else:
            col.metric(f"VWAP {VWAP_WINDOW_LABEL[window]}", f"${value:,.2f}", f"{current_price / value - 1:+.1%}")

    # Estado validado (si corresponde) — mismo patrón que render_wyckoff_spring(): el combo
    # (ventana, lado) tiene que estar en VWAP_VALIDATED_COMBOS[ticker] Y la distancia de HOY tiene
    # que cruzar el mismo umbral (VWAP_REACTION_ATR_THRESHOLD) que se usó para validarlo — estar
    # apenas del lado correcto del VWAP no es la condición que se probó, estar a >= 1.0 ATR sí.
    for window in windows:
        distances = distance_to_vwap_atr(dates, highs, lows, closes, volumes, window)
        d_today = distances[-1] if distances else None
        if d_today is None:
            continue
        if d_today >= VWAP_REACTION_ATR_THRESHOLD:
            side = "arriba"
        elif d_today <= -VWAP_REACTION_ATR_THRESHOLD:
            side = "abajo"
        else:
            continue
        if (window, side) not in VWAP_VALIDATED_COMBOS.get(ticker, set()):
            continue
        reactions = {
            r.horizon_days: r
            for r in compute_vwap_reactions(dates, highs, lows, closes, volumes, window, side)
        }
        r = reactions.get(VWAP_REACTION_HEADLINE_HORIZON)
        if r is None or r.win_rate is None:
            continue
        lado_txt = "por encima" if side == "arriba" else "por debajo"
        st.success(
            f"✅ **{ticker} está a {abs(d_today):.1f} ATR {lado_txt} del VWAP de "
            f"{VWAP_WINDOW_LABEL[window]}** — condición validada fuera de muestra (split "
            f"cronológico 60/40, 4 horizontes, sin fragilidad de umbral, y sin ser redundante "
            f"con el régimen de tendencia — ver `scripts/vwap_oos_validate.py`). Históricamente, "
            f"en los días que cumplieron esta misma condición, {ticker} subió a los "
            f"{VWAP_REACTION_HEADLINE_HORIZON} días el {r.win_rate:.0%} de las veces (retorno "
            f"promedio {r.mean_return:+.1%}, n={r.observations})."
        )

    # Lectura de 3 vías (arriba de todos / abajo de todos / mixto), mismo patrón que
    # classify_trend_state() para las 3 medias móviles — no inventa un umbral propio: la pregunta
    # es simplemente de qué lado del costo promedio está el precio en cada horizonte.
    above = [w for w, v in latest.items() if current_price > v]
    below = [w for w, v in latest.items() if current_price < v]
    if len(above) == len(latest):
        st.info(
            "🟢 El precio está **por encima del VWAP en todas las ventanas**: el comprador "
            "promedio de la última semana, del último mes y del último año está en ganancia. "
            "Clásicamente se lee como fortaleza — el VWAP largo suele oficiar de referencia de "
            "soporte mientras el precio se mantenga arriba —, aunque cuanto más estirado quede "
            "sobre su propio costo promedio, más caro está pagando el que entra hoy."
        )
    elif len(below) == len(latest):
        st.info(
            "🔴 El precio está **por debajo del VWAP en todas las ventanas**: el comprador "
            "promedio de la última semana, del último mes y del último año está en pérdida. "
            "Clásicamente se lee como debilidad — el VWAP largo suele oficiar de referencia de "
            "resistencia mientras el precio siga abajo —, aunque también es la situación en la "
            "que estás comprando por debajo de lo que pagó el mercado en ese período."
        )
    else:

        def _enumerar(ventanas: list[int]) -> str:
            """"7 días y 30 días", no "7 días, 30 días" — la enumeración con "y" al final es lo
            único que hace legible la frase cuando caen 2 o 3 ventanas del mismo lado."""
            etiquetas = [VWAP_WINDOW_LABEL[w] for w in sorted(ventanas)]
            if len(etiquetas) <= 1:
                return "".join(etiquetas)
            return f"{', '.join(etiquetas[:-1])} y {etiquetas[-1]}"

        st.info(
            f"➖ Lectura mixta: el precio está por encima del VWAP de {_enumerar(above)} y por "
            f"debajo del de {_enumerar(below)}. No hay una sola referencia de costo promedio "
            "mandando — según el horizonte que mires, el comprador promedio está en ganancia o "
            "en pérdida."
        )

    window_start = (datetime.strptime(dates[-1][:10], "%Y-%m-%d") - timedelta(days=VWAP_CHART_WINDOW_DAYS)).strftime("%Y-%m-%d")
    # El VWAP se calcula sobre TODO el historial y recién después se recorta la vista: si se
    # calculara solo sobre la ventana visible, el de 1 año arrancaría "desde cero" en el borde
    # izquierdo del gráfico y mostraría un valor que nunca existió.
    visible = [i for i, d in enumerate(dates) if d[:10] >= window_start]
    if len(visible) >= 2:
        x = [dates[i] for i in visible]
        fig = go.Figure()
        fig.add_trace(
            go.Scatter(
                x=x, y=[closes[i] for i in visible], mode="lines", name=f"Precio ({ticker})",
                line=dict(color=VWAP_PRICE_COLOR, width=3),
                hovertemplate="Precio: $%{y:,.2f}<extra></extra>",
            )
        )
        for window in windows:
            serie = series_by_window[window]
            label = f"VWAP {VWAP_WINDOW_LABEL[window]}"
            fig.add_trace(
                go.Scatter(
                    x=x, y=[serie[i] for i in visible], mode="lines", name=label,
                    line=dict(color=VWAP_COLOR[window], width=2, dash=VWAP_DASH[window]),
                    hovertemplate=f"{label}: $%{{y:,.2f}}<extra></extra>",
                )
            )
        fig.update_layout(
            plot_bgcolor="rgba(0,0,0,0)",
            paper_bgcolor="rgba(0,0,0,0)",
            font=dict(color="#898781"),
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
            margin=dict(l=10, r=10, t=10, b=10),
            hovermode="x unified",
            height=400,
            xaxis=dict(showgrid=False),
            yaxis=dict(gridcolor="rgba(128,128,128,0.2)", tickprefix="$"),
        )
        st.plotly_chart(fig, use_container_width=True)

        # Tabla de lo mismo que muestra el gráfico (más reciente primero) — el gráfico distingue
        # las 3 ventanas por color, así que la versión en texto no es opcional (mismo criterio que
        # el gráfico de veredictos en Validación y el de familias en Acciones).
        with st.expander("Ver los datos del gráfico en tabla"):
            table = pd.DataFrame(
                {
                    "Fecha": x,
                    "Precio": [closes[i] for i in visible],
                    **{
                        f"VWAP {VWAP_WINDOW_LABEL[w]}": [series_by_window[w][i] for i in visible]
                        for w in windows
                    },
                }
            ).iloc[::-1]
            st.dataframe(table, use_container_width=True, hide_index=True)

    st.caption(
        "El VWAP es un indicador clásico y se muestra acá como tal — igual que el MACD, las "
        "Bandas de Bollinger, el ADX o el Índice de Miedo y Codicia. La distancia al VWAP en "
        "múltiplos de ATR SÍ se probó fuera de muestra (split cronológico 60/40, 4 horizontes, "
        "barrido de 3 umbrales, y un chequeo de que no sea el régimen de tendencia del Plan de "
        "DCA restado con otro nombre — ver `scripts/vwap_oos_validate.py`): validó para BTC (VWAP "
        "de 1 año, ambos lados) y SOL (VWAP de 30 días, por debajo) — el recuadro verde de arriba "
        "aparece solo cuando el precio de hoy cumple exactamente esa condición para ese ticker. "
        "Para ETH, y para cualquier otro combo de ventana/lado no listado acá, sigue siendo "
        "puramente descriptivo — no validado. Dentro del Market Reaction Zone Engine el VWAP "
        "también aparece como componente de confluencia, pero pesa 0 en el score desde el "
        "rediseño — no está inflando el puntaje de ninguna zona."
    )


# Las 3 capas del método Crecetrader son IDENTIDAD (tres cosas distintas que hay que poder
# distinguir), no magnitud ni polaridad — color categórico, en los mismos slots de orden fijo que
# ya usan FAMILY_COLOR/VWAP_COLOR/ETF_FLOWS_CATEGORICAL en el resto de la app, no una paleta nueva.
# El precio reusa el mismo azul que en la sección de VWAP, por consistencia dentro de esta pestaña.
# Validado con el script de la skill dataviz en modo claro (todos PASS; el WARN de contraste de
# #1baf7a se cubre con etiquetas visibles + la tabla de abajo, que es exactamente el "relief" que
# pide). En modo oscuro el naranja queda fuera de la banda de luminosidad del validador — es el
# mismo trade-off que ya arrastra toda la app con este set de hues, no algo nuevo de esta sección;
# cambiarlo solo acá rompería la consistencia con las otras secciones de la pestaña.
CRECETRADER_PRICE_COLOR = "#2a78d6"
CRECETRADER_LAYER_COLOR = {"envelope": "#eb6834", "daily": "#1baf7a", "macro": "#8a2be2"}
CRECETRADER_LAYER_LABEL = {
    "envelope": "Envolvente de sesión",
    "daily": "Rejilla diaria",
    "macro": "Fracciones macro",
}
CRECETRADER_ROLE_LABEL = {
    "zona_compra": "🟢 Zona de compra",
    "zona_venta": "🔴 Zona de venta",
    "neutro": "⚪ Neutro",
}
CRECETRADER_CHART_WINDOW_DAYS = 180


# Panel "consola" del método Crecetrader. Es la única parte de la app con su propia paleta
# oscura fija en vez de los componentes nativos de Streamlit: fue un pedido explícito del usuario
# (mandó el diseño completo, en React, y pidió replicarlo acá) — el resto de la pestaña sigue
# usando los colores de siempre. Los hex son los del diseño que mandó, sin reinterpretar.
CRECE_C = {
    "bg": "#0C1118",
    "panel": "#121926",
    "panel_soft": "#171F2E",
    "line": "#243044",
    "text": "#E8EDF5",
    "dim": "#8A96A8",
    "faint": "#5A6578",
    "btc": "#F7931A",
    "btc_soft": "rgba(247,147,26,0.12)",
    "green": "#3DD68C",
    "cyan": "#39C3D6",
    "red": "#F0616D",
    "purple": "#B48CF2",
}

CRECE_LAYER_TABS = {
    "Intradía H1": (
        "envelope",
        "Envolvente de sesión: apertura diaria (00:00 UTC) con anillos a ±0.382, 1, 1.5 y 2%. "
        "Regla confirmada en dos jornadas (27 y 28-ago) con 15 niveles exactos a ±$1, idéntica "
        "en H1 y 5 minutos.",
    ),
    "Diario": (
        "daily",
        "Rejilla anclada al mínimo anual con pasos de 25% del rango base. 7 niveles verificados "
        "contra sus gráficos, incluida la predicción algebraica del 125%.",
    ),
    "Semanal": (
        "macro",
        "Fracciones de 12.5% de la caída macro (techo de ciclo → mínimo anual). El 37.5% "
        "verificado; otros niveles semanales del canal son pivots manuales, no algoritmizables.",
    ),
}

CRECE_ROW_HEIGHT_PX = 44

CRECE_CSS = """<style>
@import url('https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;500;700&family=IBM+Plex+Mono:wght@400;500;600&display=swap');
.crece-wrap{background:%(bg)s;border:1px solid %(line)s;border-radius:14px;padding:16px;font-family:'Space Grotesk',system-ui,sans-serif;color:%(text)s;}
.crece-mono{font-family:'IBM Plex Mono',ui-monospace,monospace;}
.crece-kicker{font-size:10px;letter-spacing:.22em;color:%(btc)s;text-transform:uppercase;margin-bottom:6px;}
.crece-h1{font-size:24px;font-weight:700;line-height:1.15;margin:0 0 14px;}
.crece-h1 span{color:%(btc)s;}
.crece-cards{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:14px;}
.crece-card{flex:1;min-width:150px;background:%(panel)s;border:1px solid %(line)s;border-radius:10px;padding:10px 14px;}
.crece-card .t{font-size:10px;letter-spacing:.14em;text-transform:uppercase;color:%(dim)s;}
.crece-card .v{font-family:'IBM Plex Mono',ui-monospace,monospace;font-size:18px;font-weight:600;margin-top:4px;}
.crece-card .s{font-size:11px;color:%(faint)s;margin-top:2px;}
.crece-ladder{display:flex;background:%(panel)s;border:1px solid %(line)s;border-radius:14px;overflow:hidden;}
.crece-rail{position:relative;width:74px;flex-shrink:0;border-right:1px solid %(line)s;background:%(panel_soft)s;}
.crece-rail i{position:absolute;left:0;right:0;height:0;display:block;}
.crece-mark{position:absolute;left:6px;right:6px;text-align:center;background:%(btc)s;color:#141414;font-size:9px;font-weight:600;border-radius:4px;padding:2px 0;font-family:'IBM Plex Mono',ui-monospace,monospace;}
.crece-rows{flex:1;min-width:0;}
.crece-row{display:flex;align-items:baseline;gap:10px;padding:0 14px;height:%(row)dpx;box-sizing:border-box;border-bottom:1px solid %(line)s;overflow:hidden;}
.crece-row:last-child{border-bottom:none;}
.crece-row.near{background:%(btc_soft)s;}
.crece-lbl{font-family:'IBM Plex Mono',ui-monospace,monospace;font-size:12px;width:58px;flex-shrink:0;}
.crece-val{font-family:'IBM Plex Mono',ui-monospace,monospace;font-size:16px;min-width:96px;}
.crece-role{font-size:9px;font-weight:700;letter-spacing:.08em;border-radius:4px;padding:2px 6px;flex-shrink:0;opacity:.9;white-space:nowrap;}
.crece-note{font-size:11px;color:%(faint)s;margin-left:auto;text-align:right;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
.crece-foot{margin-top:14px;font-size:11px;color:%(faint)s;line-height:1.6;}
</style>""" % {**CRECE_C, "row": CRECE_ROW_HEIGHT_PX}


def _crece_level_color(layer: str, lv: Level) -> str:
    """Color de cada nivel dentro de su capa — el mismo mapeo del diseño que mandó el usuario.

    No es color categórico por serie (no hay series acá): distingue el papel del nivel dentro de
    la capa, y siempre viaja junto al texto del nivel, nunca solo."""
    if layer == "envelope":
        return CRECE_C["purple"] if lv.label == "centro" else CRECE_C["dim"]
    if layer == "macro":
        return CRECE_C["cyan"] if lv.verified else CRECE_C["dim"]
    if lv.pct in (175.0, 225.0):
        return CRECE_C["cyan"]
    if lv.pct >= 250.0:
        return CRECE_C["red"]
    if lv.pct == 125.0:
        return CRECE_C["btc"]
    if lv.pct <= 75.0 and (lv.verified or lv.pct == 0.0):
        return CRECE_C["green"]
    return CRECE_C["faint"]


def _crece_is_key(layer: str, lv: Level) -> bool:
    if layer == "envelope":
        return lv.label == "centro" or abs(lv.pct) == 0.382
    if layer == "daily":
        return lv.verified or lv.pct == 0.0
    return lv.verified


def _crece_role_chip(lv: Level) -> str:
    """`Role` ya viene resuelto por `src/crecetrader.py` (zona de compra / venta / neutro) — acá
    solo se pinta. Es la etiqueta que el método le da a cada nivel, no una recomendación, y el
    pie del panel lo dice explícitamente."""
    if lv.role is Role.BUY_ZONE:
        txt, col = "ZONA COMPRA", CRECE_C["green"]
    elif lv.role is Role.SELL_ZONE:
        txt, col = "ZONA VENTA", CRECE_C["red"]
    else:
        return ""
    return f'<span class="crece-role" style="color:{col};border:1px solid {col};">{txt}</span>'


def _crece_ladder_html(layer: str, levels: list, price: float, near: set) -> str:
    """Escalera: riel con las líneas a escala real de precio + una fila por nivel.

    El riel y las filas se alinean porque ambos miden `CRECE_ROW_HEIGHT_PX` × cantidad de
    niveles: la posición de cada línea es proporcional al precio, la de su fila es secuencial —
    a propósito, es lo que hace visible que los niveles no están equiespaciados."""
    values = [lv.price for lv in levels] + [price]
    top_v, bottom_v = max(values), min(values)
    span = (top_v - bottom_v) or 1.0
    height = CRECE_ROW_HEIGHT_PX * len(levels)

    def y(v: float) -> float:
        return (top_v - v) / span * 100.0

    rail = "".join(
        f'<i style="top:{y(lv.price):.3f}%;border-top:{"2px solid" if _crece_is_key(layer, lv) else "1px dashed"} '
        f'{_crece_level_color(layer, lv)};opacity:{1 if _crece_is_key(layer, lv) else 0.5};"></i>'
        for lv in levels
    )
    rail += f'<div class="crece-mark" style="top:calc({y(price):.3f}% - 8px);">{price:,.0f}</div>'

    rows = ""
    for lv in levels:
        key = _crece_is_key(layer, lv)
        rows += (
            f'<div class="crece-row{" near" if lv in near else ""}">'
            f'<span class="crece-lbl" style="color:{_crece_level_color(layer, lv)};'
            f'font-weight:{600 if key else 400};">{lv.label}</span>'
            f'<span class="crece-val" style="color:{CRECE_C["text"] if key else CRECE_C["dim"]};'
            f'font-weight:{600 if key else 500};">{lv.price:,.2f}</span>'
            f"{_crece_role_chip(lv)}"
            f'<span class="crece-note">{lv.note}</span>'
            "</div>"
        )

    return (
        f'<div class="crece-ladder" style="min-height:{height}px;">'
        f'<div class="crece-rail" style="height:{height}px;">{rail}</div>'
        f'<div class="crece-rows">{rows}</div>'
        "</div>"
    )


def render_crecetrader(ticker: str, historical_prices: list[dict], current_price: float) -> None:
    """Sección "📐 Niveles Crecetrader" — pestaña interna de cada cripto.

    Reproduce las 3 capas de `src/crecetrader.py` (envolvente de sesión, rejilla diaria anclada
    al mínimo anual, fracciones de 1/8 de la caída macro) sobre los datos de Binance que esta
    pestaña ya tiene en mano. Las entradas del motor se derivan de la serie diaria con
    `src/crecetrader_inputs.py` y se pueden sobrescribir a mano — dónde termina el "primer
    impulso" es justamente la parte que el método original traza a ojo.

    El panel replica el diseño (React) que mandó el usuario: una capa por vez, riel de niveles a
    escala de precio y filas con rol y nota. Todo se calcula acá, en la máquina que abre la app,
    sobre la serie de Binance — el diseño original pedía los datos a una API de LLM con búsqueda
    web; eso se reemplazó por el mismo `binance_client` que usa el resto de la pestaña.

    Descriptivo, NO validado fuera de muestra: es la reconstrucción de cómo se generan esos
    niveles, no evidencia de que predigan algo. Una rejilla densa "acierta" toques por
    construcción — el propio módulo lo dice en su aviso y acá se repite en pantalla.
    """
    st.subheader("📐 Niveles calculados (método Crecetrader)")
    st.warning(
        "⚠️ **Es descriptivo, no una señal de trading, y no está validado fuera de muestra.** "
        "Reproduce CÓMO se generan los niveles; no implica que tengan poder predictivo. Una "
        "rejilla densa acierta toques por construcción: con 31 niveles repartidos en el mapa, que "
        "el precio reaccione cerca de alguno no es evidencia de nada. A diferencia del VWAP o del "
        "régimen del Plan de DCA, acá no se corrió ningún estudio fuera de muestra."
    )

    with st.expander("⚙️ Entradas del cálculo"):
        st.caption(
            "El motor necesita 5 entradas: precio, apertura diaria, mínimo anual (el ancla de las "
            "capas 2 y 3), amplitud del primer impulso desde ese mínimo y caída macro (techo de "
            "ciclo − ancla). Todas salen de la serie diaria de Binance, calculadas en esta "
            "máquina. La única con margen de interpretación es dónde termina el primer impulso — "
            "estos dos controles la definen, y abajo se puede sobrescribir todo a mano."
        )
        crece_col1, crece_col2 = st.columns(2)
        retracement_pct = crece_col1.slider(
            "Retroceso que cierra el impulso (% del avance)",
            min_value=25.0,
            max_value=75.0,
            value=50.0,
            step=5.0,
            key="crece_retracement_pct",
            help=(
                "Un cierre diario que devuelve este % del avance acumulado desde el mínimo anual "
                "da por terminado el primer impulso."
            ),
        )
        min_reversal_pct = crece_col2.slider(
            "Giro mínimo para que ese retroceso cuente (% del techo)",
            min_value=5.0,
            max_value=30.0,
            value=15.0,
            step=1.0,
            key="crece_min_reversal_pct",
            help=(
                "Segunda condición, simultánea con la anterior: sin esto, en cripto el 50% de un "
                "avance del 13% son 6.5% de precio y el impulso se cerraría a los dos días del "
                "mínimo, con una amplitud que no representa la estructura del gráfico."
            ),
        )

        try:
            inferred = infer_inputs(
                historical_prices,
                impulse_retracement_pct=retracement_pct,
                min_reversal_pct=min_reversal_pct,
            )
        except ValueError as exc:
            st.caption(f"No se pudieron derivar las entradas para {ticker}: {exc}")
            return

        st.caption(
            f"**Ancla (mínimo anual):** ${inferred.year_low:,.2f} ({inferred.year_low_date}) · "
            f"**Techo del primer impulso:** ${inferred.base_range_top:,.2f} "
            f"({inferred.base_range_top_date}"
            + (", impulso todavía abierto" if inferred.impulse_open else "")
            + f") · **Techo de ciclo:** ${inferred.cycle_high:,.2f} ({inferred.cycle_high_date}) · "
            f"{inferred.history_days} velas diarias."
        )

        manual = st.checkbox(
            "Ajustar las entradas a mano",
            value=False,
            key="crece_manual_inputs",
            help=(
                "Para replicar exactamente un gráfico del canal: pegá los valores que ves ahí. "
                "Los campos arrancan en lo que derivó el cálculo automático y se vuelven a "
                "sembrar si movés los controles de arriba."
            ),
        )
        daily_open = inferred.daily_open
        year_low = inferred.year_low
        base_range = inferred.base_range
        macro_range = inferred.macro_range
        if manual:
            # Sin `key=` a propósito: así el número vuelve a sembrarse desde `inferred` cuando el
            # usuario mueve los sliders de arriba (con una key fija, Streamlit ignoraría el nuevo
            # `value` y el campo quedaría clavado en el valor viejo, que ya no corresponde a los
            # umbrales elegidos).
            man_col1, man_col2 = st.columns(2)
            daily_open = man_col1.number_input(
                "Apertura diaria (00:00 UTC)", value=float(inferred.daily_open), min_value=0.0, format="%.2f"
            )
            year_low = man_col2.number_input(
                "Mínimo anual (ancla)", value=float(inferred.year_low), min_value=0.0, format="%.2f"
            )
            base_range = man_col1.number_input(
                "Rango base (primer impulso)", value=float(inferred.base_range), min_value=0.0, format="%.2f"
            )
            macro_range = man_col2.number_input(
                "Caída macro (techo de ciclo − ancla)", value=float(inferred.macro_range), min_value=0.0, format="%.2f"
            )

    if year_low <= 0 or base_range <= 0 or macro_range <= 0 or daily_open <= 0:
        st.caption(
            "Alguna de las entradas quedó en cero o negativa — no se puede armar la rejilla con "
            "esos valores. Revisá los campos manuales."
        )
        return

    engine = LevelEngine(
        price=current_price,
        daily_open=daily_open,
        year_low=year_low,
        base_range=base_range,
        macro_range=macro_range,
    )

    tab_label = st.segmented_control(
        "Capa",
        list(CRECE_LAYER_TABS.keys()),
        default="Diario",
        key="crece_layer",
        label_visibility="collapsed",
    )
    if tab_label is None:  # segmented_control permite deseleccionar
        tab_label = "Diario"
    layer, layer_desc = CRECE_LAYER_TABS[tab_label]
    st.caption(layer_desc)

    if layer == "envelope":
        center_choice = st.radio(
            "Centro de la envolvente",
            ["Apertura diaria (00:00 UTC)", "Precio actual"],
            horizontal=True,
            key="crece_envelope_center",
            label_visibility="collapsed",
        )
        use_open = center_choice.startswith("Apertura")
        center = daily_open if use_open else current_price
        levels = session_envelope(center, reference=current_price)
        params = [
            (
                "Centro de la envolvente",
                f"${center:,.2f}",
                "apertura diaria — regla confirmada" if use_open else "precio en vivo (elegido a mano)",
                CRECE_C["purple"],
            ),
            ("Anillos", "± 0.382 / 1 / 1.5 / 2%", "verificados al $1 — 27 y 28-ago", CRECE_C["btc"]),
        ]
    elif layer == "daily":
        levels = engine.grid()
        params = [
            ("Ancla (mínimo anual)", f"${year_low:,.2f}", f"onda V — {inferred.year_low_date}", CRECE_C["green"]),
            (
                "Rango base (Fase 1)",
                f"${base_range:,.2f}",
                ("impulso abierto — techo de hoy" if inferred.impulse_open else f"impulso hasta {inferred.base_range_top_date}")
                if not manual
                else "valor cargado a mano",
                CRECE_C["btc"],
            ),
        ]
    else:
        levels = engine.macro()
        params = [
            ("Ancla (mínimo anual)", f"${year_low:,.2f}", f"onda V — {inferred.year_low_date}", CRECE_C["green"]),
            (
                "Caída macro (rango)",
                f"${macro_range:,.2f}",
                f"techo de ciclo {inferred.cycle_high_date}" if not manual else "valor cargado a mano",
                CRECE_C["cyan"],
            ),
        ]

    below, above = nearest_levels(levels, current_price)
    near = {lv for lv in (below, above) if lv is not None}

    def _card(title: str, value: str, sub: str, color: str) -> str:
        return (
            f'<div class="crece-card" style="border-left:3px solid {color};">'
            f'<div class="t">{title}</div><div class="v">{value}</div><div class="s">{sub}</div></div>'
        )

    def _near_card(title: str, lv, color: str) -> str:
        if lv is None:
            return _card(title, "—", "fuera de la escalera", color)
        return _card(
            title,
            f"${lv.price:,.2f}",
            f"nivel {lv.label} — {lv.distance_pct(current_price):+.2f}%",
            color,
        )

    html = (
        CRECE_CSS
        + '<div class="crece-wrap">'
        + '<div class="crece-kicker">Ingeniería inversa Crecetrader — 3 capas</div>'
        + f'<div class="crece-h1">Niveles calculados <span>{ticker}</span> '
        + f'<span class="crece-mono" style="font-size:20px;color:{CRECE_C["text"]};">${current_price:,.2f}</span></div>'
        + '<div class="crece-cards">'
        + "".join(_card(*p) for p in params)
        + "</div>"
        + '<div class="crece-cards">'
        + _near_card("Resistencia próxima", above, CRECE_C["red"])
        + _near_card("Soporte próximo", below, CRECE_C["green"])
        + "</div>"
        + _crece_ladder_html(layer, levels, current_price, near)
        + '<div class="crece-foot">Tres capas reconstruidas de gráficos públicos (27/28-ago-2026): '
        "intradía (envolvente sobre la apertura diaria, 15 niveles verificados), diaria (pasos de "
        "25% del rango base sobre el mínimo anual, 7 verificados) y semanal (fracciones de 12.5% "
        "de la caída macro, 1 verificado). Algunos niveles semanales del canal son pivots "
        "discrecionales, no automatizables. Si el mínimo anual cambia, hay que recalibrar. Las "
        "etiquetas ZONA COMPRA / ZONA VENTA describen el rol que cada nivel tiene dentro del "
        "método replicado (refugios donde busca rebotes, objetivos donde toma beneficios); no son "
        "señales ni recomendaciones, y no se validaron fuera de muestra. La entrada real en su "
        "sistema es discrecional: exige confirmación de la acción del precio sobre el nivel. "
        "Reconstrucción educativa — no es asesoramiento de inversión.</div>"
        + "</div>"
    )
    st.markdown(html, unsafe_allow_html=True)

    with st.expander("📈 Ver esta capa sobre el precio (y la tabla completa)"):
        fig = go.Figure()
        window = historical_prices[-CRECETRADER_CHART_WINDOW_DAYS:]
        fig.add_trace(
            go.Scatter(
                x=[p["date"] for p in window],
                y=[p["close"] for p in window],
                mode="lines",
                name="Precio",
                line=dict(color=CRECETRADER_PRICE_COLOR, width=2),
            )
        )
        x0, x1 = window[0]["date"], window[-1]["date"]
        first = True
        for lv in levels:
            fig.add_trace(
                go.Scatter(
                    x=[x0, x1],
                    y=[lv.price, lv.price],
                    mode="lines",
                    line=dict(color=CRECETRADER_LAYER_COLOR[layer], width=2, dash="dot"),
                    name=CRECETRADER_LAYER_LABEL[layer],
                    legendgroup=layer,
                    showlegend=first,
                    hovertemplate=f"{lv.label}<br>$%{{y:,.2f}}<br>{lv.note}<extra></extra>",
                )
            )
            first = False
        # Etiqueta directa SOLO sobre los dos niveles que bracketean el precio — anotar los 13 es
        # exactamente el anti-patrón de "un número en cada punto".
        for lv in (below, above):
            if lv is not None:
                fig.add_annotation(
                    x=x1,
                    y=lv.price,
                    text=f"{lv.label} · ${lv.price:,.0f}",
                    showarrow=False,
                    xanchor="right",
                    yanchor="bottom",
                    font=dict(size=11, color=CRECETRADER_LAYER_COLOR[layer]),
                )
        fig.update_layout(
            title=f"{ticker} — {CRECETRADER_LAYER_LABEL[layer].lower()} (últimos {CRECETRADER_CHART_WINDOW_DAYS} días)",
            xaxis_title="Fecha",
            yaxis_title="Precio (USD)",
            hovermode="x unified",
            height=420,
            margin=dict(l=10, r=10, t=50, b=10),
        )
        st.plotly_chart(fig, use_container_width=True)
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "Nivel": lv.label,
                        "Precio": f"${lv.price:,.2f}",
                        "Distancia": f"{lv.distance_pct(current_price):+.2f}%",
                        "Rol": CRECETRADER_ROLE_LABEL.get(lv.role.value, lv.role.value),
                        "Verificado": "✅" if lv.verified else "—",
                        "Nota": lv.note,
                    }
                    for lv in levels
                ]
            ),
            use_container_width=True,
            hide_index=True,
        )
        st.caption(
            "«Verificado» significa que ese nivel fue confirmado contra los gráficos públicos del "
            "canal (los 8 anillos de la envolvente al dólar, y 6 pasos de la rejilla diaria, "
            "incluida una predicción algebraica del 125%) — es una verificación de que la fórmula "
            "reproduce sus números, NO de que el nivel funcione."
        )

    confluences = engine.confluences()
    if confluences:
        with st.expander(f"🔗 Confluencias entre capas ({len(confluences)})"):
            st.caption(
                "Precios donde dos capas distintas caen a menos de 0.15% una de otra. En el "
                "método original eso se trata como un nivel crítico; acá se muestra por "
                "completitud, sin evidencia de que cambie nada."
            )
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            "Precio": f"${a.price:,.2f}",
                            "Distancia": f"{a.distance_pct(current_price):+.2f}%",
                            "Capa A": f"{CRECETRADER_LAYER_LABEL[a.layer]} {a.label}",
                            "Capa B": f"{CRECETRADER_LAYER_LABEL[b.layer]} {b.label}",
                        }
                        for a, b in confluences
                    ]
                ),
                use_container_width=True,
                hide_index=True,
            )


@st.cache_data(ttl=900, show_spinner=False)
def _cached_binance_historical_prices(binance_symbol: str):
    return binance_client.get_historical_prices(binance_symbol)


@st.cache_data(ttl=900, show_spinner=False)
def _cached_binance_historical_prices_4h(binance_symbol: str):
    # years_back=2.0 (no el default de 5.0 de binance_client): esta es ahora la serie de
    # REFERENCIA del Market Reaction Zone Engine (ver src/support_resistance.py) — cada touch se
    # camina contra esta serie, y ese recorrido se llama cientos de veces por candidato durante
    # la optimización, así que su longitud domina el costo del pipeline. 2 años ≈ 4380 velas de
    # 4h es el mismo criterio ya usado para 1h más abajo. Es el único caller de este wrapper, así
    # que el cambio de default es seguro.
    return binance_client.get_historical_prices_intraday_4h(binance_symbol, years_back=2.0)


@st.cache_data(ttl=900, show_spinner=False)
def _cached_binance_historical_prices_1h(binance_symbol: str):
    return binance_client.get_historical_prices_intraday_1h(binance_symbol)


# TTL de 24h, no 900s como el precio: SoSoValue solo actualiza AUM/flujos una vez por día (el
# snapshot trae la fecha de la última sesión liquidada), y un TTL corto solo gastaría cuota del
# free tier (20 req/min, 100k/mes) sin traer nada distinto. Mismo criterio que el TTL de 86400s
# de _cached_backtest_ticker en Validación.
@st.cache_data(ttl=86400, show_spinner=False)
def _cached_etf_list(symbol: str):
    return sosovalue_client.get_etf_list(symbol)


@st.cache_data(ttl=86400, show_spinner=False)
def _cached_etf_snapshot(fund_ticker: str):
    return sosovalue_client.get_etf_market_snapshot(fund_ticker)


@st.cache_data(ttl=86400, show_spinner=False)
def _cached_etf_history(fund_ticker: str):
    return sosovalue_client.get_etf_history(fund_ticker)


# TTL largo (6h) a propósito, a diferencia de _cached_evaluation (900s, sigue el precio en
# vivo) — los niveles de soporte/resistencia no se mueven al ritmo del precio intradía, así que
# no hay necesidad de recomputar este pipeline pesado (~9-15s según ticker, caminando contra la
# serie de 4h — ver src/support_resistance.py — más DBSCAN/KDE/RANSAC/Theil-Sen/Huber/Hough x
# temporalidad x soporte y resistencia, más el refinamiento por optimización) cada 15 minutos.
# Mismo criterio que el TTL de 86400s de _cached_backtest_ticker (Validación): el costo real acá
# es de CÓMPUTO, no de red, así que el botón en render_crypto() (pestaña "🪙 Cripto") no lo
# dispara automáticamente en cada carga.
@st.cache_data(ttl=21600, show_spinner=False)
def _cached_sr_levels(
    ticker: str,
    enabled_methods: tuple[str, ...],
    top_n: int,
    min_touch_points: int,
    include_1h: bool,
):
    binance_symbol = CRYPTO_BINANCE_SYMBOLS[ticker]
    # La serie de 4h es ahora la referencia obligatoria del motor (ver detect_levels()) — ya no
    # es opcional/togglable como antes. La diaria se sigue usando, pero solo para reagregar
    # weekly/monthly y generar candidatos "daily" nativos (daily_prices=), no como referencia.
    daily_prices, _ = _cached_binance_historical_prices(binance_symbol)
    intraday_4h, _ = _cached_binance_historical_prices_4h(binance_symbol)
    timeframes = ("4h", "daily", "weekly", "monthly")
    if include_1h:
        timeframes += ("1h",)
    intraday_1h = None
    if include_1h:
        intraday_1h, _ = _cached_binance_historical_prices_1h(binance_symbol)
    config = SRConfig(
        enabled_methods=set(enabled_methods), top_n=top_n, min_touch_points=min_touch_points, timeframes=timeframes
    )
    return detect_levels(intraday_4h, config, daily_prices=daily_prices, intraday_1h_prices=intraday_1h)


# SR_METHOD_LABELS, SR_TIMEFRAME_LABELS, SR_TIMEFRAME_ORDER, SR_KIND_RGB y
# render_advanced_levels_chart() se movieron a src/ui/shared.py — genuinamente cross-tab desde
# que Especulación (acciones) también los necesita, ver ese archivo.

# Validación fuera de muestra bajo el Market Reaction Zone Engine — DOS rondas el mismo día,
# porque la segunda ronda (ajuste de consistencia estadística: Wilson lower bound en
# respect_rate/volume_during_rebounds, límite inferior de confianza en reaction_magnitude, ver
# src/support_resistance.py) cambió lo suficiente el ranking de niveles como para invalidar el
# resultado de la primera ronda. Mismo split cronológico 60/40, mismos 4 horizontes (5/10/20/30
# días), universo BTC/ETH/SOL — script descartable, no en el repo.
#
# Ronda 1 (score recién rediseñado, sin ajuste de consistencia todavía): SOPORTE validó en las 3
# monedas a umbral fijo (score ≥ 50/40/60) — mismo signo en los 4 horizontes, train y test.
#
# Ronda 2 (con el ajuste de consistencia): el mismo chequeo, incluso a umbral fijo ≥ 50, ya
# mostraba signo invertido para BTC-soporte en 2 de 4 horizontes (20d/30d) — el ajuste de Wilson
# cambia CUÁLES niveles quedan arriba del ranking (penaliza los de pocos touches, que dominan en
# un dataset de solo ~1825 velas diarias), y ese cambio de conjunto fue suficiente para romper la
# validación. Se probó además con umbrales por percentil (50/70/90 y luego 40/55/70, para que el
# corte se adapte a la escala del score en vez de un número fijo) — mismo resultado: ninguna
# combinación (ticker, tipo) sostuvo el mismo signo en los 4 horizontes, train y test.
#
# Decisión: confiar en el ajuste de consistencia (es estadísticamente correcto — un nivel con 3
# touches no debería pesar igual que uno con 20) y aceptar el resultado más honesto, aunque menos
# alentador, en vez de diluir o apagar el ajuste para forzar que algo vuelva a validar. Es
# plausible que la validación de Ronda 1 dependiera en parte de ruido de muestra chica que Ronda 2
# expone correctamente. `SR_VALIDATED_TICKERS` queda vacío — no hay ningún combo (ticker, tipo)
# validado hoy bajo el score con ajuste de consistencia. Re-probar es válido más adelante si se
# acumula más historial (más touches por nivel = el ajuste de Wilson pesa menos), pero no antes.

SR_VALIDATED_SCORE_PERCENTILE = 55.0  # mecanismo listo para cuando algo vuelva a validar

SR_VALIDATED_HORIZONS_DAYS = [5, 10, 20, 30]

SR_VALIDATED_TICKERS: dict[str, set[str]] = {}


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

    # Contenido ESTÁTICO — no depende del selector de ticker de abajo (ver docstring de
    # render_fear_greed_index()). Va primero y con su propio divider para que quede claro que no
    # es parte de lo que cambia al elegir BTC/ETH/SOL más abajo.
    render_fear_greed_index()
    st.divider()

    crypto_tickers = list(CRYPTO_BINANCE_SYMBOLS.keys())
    ticker = st.selectbox("Ticker", crypto_tickers, key="sr_ticker")

    try:
        historical_prices, _ = _cached_binance_historical_prices(CRYPTO_BINANCE_SYMBOLS[ticker])
    except DataError as exc:
        st.error(f"No pudimos consultar {ticker} ahora mismo.")
        # Detalle real del error (código HTTP de Binance, texto de respuesta) en un caption
        # aparte — no reemplaza el mensaje de arriba, pero es lo único que permite diagnosticar
        # remotamente sin ir a buscar los logs del deploy (p. ej. Binance devuelve 451 a pedidos
        # desde IPs de datacenters en EE. UU., que es donde corre Streamlit Community Cloud —
        # geo-bloqueo, no un bug de este código).
        st.caption(f"Detalle: {exc}")
        return

    closes = [p["close"] for p in historical_prices]
    if not closes:
        st.caption("No hay historial de precios disponible para este ticker.")
        return
    current_price = closes[-1]

    def _render_zone_engine() -> None:
        """Closure — captura ticker/historical_prices/closes/current_price del scope de
        render_crypto(). Reemplaza, en esta misma posición, lo que antes era el gráfico simple
        de "Soportes y Resistencias" (quitado por pedido explícito — ver
        `financial-advisor-speculation`'s references/design-history.md)."""
        st.subheader("🧭 Market Reaction Zone Engine")
        st.caption(
            "Identifica zonas de soporte/resistencia con evidencia estadística suficiente, priorizando "
            "la CALIDAD de la reacción del mercado por sobre la cantidad de toques — tres rebotes "
            "fuertes con volumen alto valen más que diez toques sin reacción relevante. Cada touch se "
            "camina contra velas de 4h (2 años de historia) en vez de diarias, dándole a cada nivel "
            "muchas más oportunidades reales de tocar/rebotar. Combina clustering (DBSCAN), densidad "
            "(KDE), líneas de tendencia robustas (RANSAC/Theil-Sen/Huber), Transformada de Hough y "
            "multi-timeframe con jerarquía institucional→operativa (mensual/semanal/diario/4h, + 1h "
            "si lo activás abajo) en un único score 0-100 por zona, con un paso de optimización que "
            "ajusta cada línea para maximizar la calidad de reacción mientras penaliza rupturas y "
            "distancia."
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
            include_1h = st.checkbox(
                "Incluir temporalidad 1h (velas nativas de Binance)",
                value=False,
                key="sr_include_1h",
                help=(
                    "Temporalidad 'operativa' del Market Reaction Zone Engine — pesa menos que "
                    "diario/semanal/mensual/4h en el score (ver jerarquía de temporalidad), pero suma "
                    "confluencia y detecta niveles de más corto plazo. Implica una consulta de red "
                    "adicional (4h, la referencia del motor, siempre se consulta — ya no es opcional)."
                ),
            )

        result_key = f"sr_levels_result_{ticker}"
        if st.button("🔍 Calcular niveles multi-metodología", key="sr_compute_button"):
            with st.spinner("Corriendo clustering, KDE, líneas robustas, Hough y más — puede tardar unos segundos..."):
                st.session_state[result_key] = _cached_sr_levels(
                    ticker, tuple(sorted(selected_methods)), top_n, min_touch_points, include_1h
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
                            # age_bars está en barras de 4h desde el rediseño (antes eran barras
                            # diarias) — se muestra en días (÷6, 1 día = 6 barras de 4h) para que
                            # siga siendo legible sin tener que saber la conversión interna.
                            "Antigüedad (días)": round(lv.age_bars / 6),
                            "Temporalidades": ", ".join(lv.timeframes),
                            "Métodos": ", ".join(lv.methods),
                            "Dist. al precio actual": f"{lv.distance_to_price_pct:+.1%}" if lv.kind != "channel" else "—",
                        }
                    )
                st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
                intraday_4h_for_chart, _ = _cached_binance_historical_prices_4h(CRYPTO_BINANCE_SYMBOLS[ticker])
                render_advanced_levels_chart(historical_prices, intraday_4h_for_chart, filtered_levels, ticker)

            st.divider()
            st.subheader("📋 Lectura validada fuera de muestra")
            validated_kinds = SR_VALIDATED_TICKERS.get(ticker, set())
            if not validated_kinds:
                st.caption(
                    "No hay evidencia validada fuera de muestra para ningún ticker con la versión "
                    "actual del score (Market Reaction Zone Engine, con ajuste estadístico de "
                    "Wilson). Se probó BTC, ETH y SOL con el mismo split cronológico 60/40 y los "
                    "mismos 4 horizontes que el resto de las señales de esta app: hubo una primera "
                    "ronda donde el soporte validó en las 3 monedas, pero se rompió al agregar un "
                    "ajuste estadístico que evita que niveles con pocos touches pesen igual que uno "
                    "con muchos — resultado más honesto, no un error de cálculo. Esto no es "
                    "evidencia de que NO haya señal — solo que todavía no se confirmó bajo el "
                    "criterio estadístico correcto, y el resto de esta tabla se queda puramente "
                    "descriptivo."
                )
            else:
                any_hit = False
                for kind in ("support", "resistance"):
                    if kind not in validated_kinds:
                        continue
                    score_threshold = score_percentile_threshold(sr_levels, kind, SR_VALIDATED_SCORE_PERCENTILE)
                    if score_threshold is None:
                        continue
                    qualifying = [
                        lv for lv in sr_levels
                        if lv.kind == kind and lv.confidence_score >= score_threshold and lv.zone_low is not None
                    ]
                    hit = any(lv.zone_low <= current_price <= lv.zone_high for lv in qualifying)
                    if not hit:
                        continue
                    any_hit = True
                    reactions = compute_level_zone_reactions(
                        historical_prices, sr_levels, kind, score_threshold, SR_VALIDATED_HORIZONS_DAYS
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
                        f"**{ticker} está hoy dentro de la zona de un {kind_label} en el percentil "
                        f"{SR_VALIDATED_SCORE_PERCENTILE:.0f} de sus propios niveles (score ≥ "
                        f"{score_threshold:.0f} hoy).** Para este ticker y este tipo de nivel, la "
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
                        f"en el percentil {SR_VALIDATED_SCORE_PERCENTILE:.0f} de sus propios niveles "
                        "en este momento — el resto de esta tabla se queda puramente descriptivo "
                        "hasta que eso cambie."
                    )

    render_sticky_price("niveles", f"Precio actual — {ticker}", current_price, ticker)

    # Pestañas INTERNAS del ticker elegido (pedido explícito): el análisis de siempre por un lado
    # y el método Crecetrader por el otro, para que una reconstrucción descriptiva y no validada
    # fuera de muestra no quede intercalada entre las secciones que sí pasaron ese filtro. Igual
    # que las pestañas de arriba, st.tabs no es lazy: los dos cuerpos se ejecutan en cada rerun —
    # acá no importa, porque lo de Crecetrader es cálculo local sobre la serie diaria que esta
    # función ya tiene en mano, sin ninguna consulta de red propia.
    tab_analisis, tab_crecetrader = st.tabs(["📊 Análisis", "📐 Niveles Crecetrader"])

    with tab_analisis:
        render_speculation_indicators(
            "crypto", ticker, historical_prices, closes, current_price, is_crypto=True, render_zone_engine=_render_zone_engine
        )
        render_vwap(ticker, historical_prices, closes, current_price)
        render_wyckoff_spring(ticker, historical_prices, closes)
        render_etf_flows(ticker)

    with tab_crecetrader:
        render_crecetrader(ticker, historical_prices, current_price)
