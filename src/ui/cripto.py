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

from src.config import CRYPTO_BINANCE_SYMBOLS
from src.data import binance_client
from src.data.errors import DataError
from src.speculation import (
    VWAP_WINDOWS_DAYS,
    classify_wyckoff_spring_series,
    compute_wyckoff_spring_reactions,
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
    DESCRIPTIVO, no validado fuera de muestra por este proyecto (ver el caption del final).
    """
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
        "**Descriptivo, no validado por este proyecto.** El VWAP es un indicador clásico y se "
        "muestra acá como tal — igual que el MACD, las Bandas de Bollinger, el ADX o el Índice de "
        "Miedo y Codicia —, pero todavía NO se probó fuera de muestra si la distancia al VWAP "
        "anticipa algo del retorno futuro de BTC/ETH/SOL (split cronológico 60/40 y 4 horizontes, "
        "el mismo criterio con el que se validaron el régimen del Plan de DCA y el Wyckoff "
        "Spring). Hasta que esa prueba se corra y pase, esto no ajusta ninguna recomendación de "
        "la app. Dentro del Market Reaction Zone Engine el VWAP también aparece como componente "
        "de confluencia, pero pesa 0 en el score desde el rediseño — no está inflando el puntaje "
        "de ninguna zona."
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
    render_speculation_indicators(
        "crypto", ticker, historical_prices, closes, current_price, is_crypto=True, render_zone_engine=_render_zone_engine
    )
    render_vwap(ticker, historical_prices, closes, current_price)
    render_wyckoff_spring(ticker, historical_prices, closes)
