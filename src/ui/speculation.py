"""Pestaña "🎲 Especulación" (solo-acciones) + `render_speculation_indicators()`, el cuerpo de
indicadores COMPARTIDO con la pestaña "🪙 Cripto" (`src/ui/cripto.py`). Extraído de app.py (que
llegó a 2821 líneas) para modularizar. A diferencia de todo el resto del dashboard, acá el
lenguaje de timing es a propósito — ver `us-stocks-speculation` skill para el detalle completo
(por qué existe esta excepción, qué se probó y se descartó, etc.)."""

from datetime import datetime, timedelta

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from src.config import TICKERS
from src.data.errors import DataError
from src.speculation import (
    OBV_SMA_PERIOD,
    compute_adx,
    compute_bollinger_bands,
    compute_macd,
    compute_obv,
    compute_regime_reactions,
    compute_regime_rsi_reactions,
    compute_resistance_levels,
    compute_rsi,
    compute_support_levels,
)
from src.support_resistance import (
    SRLevel,
    compute_level_zone_reactions,
    daily_reference_config,
    detect_levels,
    score_percentile_threshold,
)
from src.ui.shared import (
    SR_KIND_RGB,
    SR_METHOD_LABELS,
    SR_TIMEFRAME_LABELS,
    SR_TIMEFRAME_ORDER,
    _cached_historical_prices,
    classify_trend_state,
    render_advanced_levels_chart,
    render_sticky_price,
)
from src.valuation.trend import evaluate_trend

LEVEL_CHART_COLORS = {
    "price": "#2a78d6",
    "support_daily": "#e34948",
    "support_weekly": "#eb6834",
    "support_monthly": "#1baf7a",
    "support_yearly": "#eda100",
    "resistance_daily": "#8a5a2b",
    "resistance_weekly": "#e87ba4",
    "resistance_monthly": "#008300",
    "resistance_yearly": "#4a3aa7",
}


def colored_metric(container, label: str, value: str, color: str) -> None:
    """st.metric no acepta un color de texto custom — esto lo imita a mano, para que cada
    nivel se vea en el mismo color que su línea en el gráfico de abajo (LEVEL_CHART_COLORS),
    sin tocar el gráfico ni su leyenda."""
    container.markdown(
        f"""
        <div style="padding: 0.25rem 0;">
            <div style="font-size:0.85rem;color:{color};opacity:0.9;">{label}</div>
            <div style="font-size:1.6rem;font-weight:600;color:{color};line-height:1.2;">{value}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


# Cada estado del selector zoom-ea el gráfico a una ventana distinta Y muestra solo el par
# soporte/resistencia que corresponde a esa escala — mostrar los 6 niveles juntos era demasiada
# info en un solo vistazo. Las etiquetas coinciden 1:1 con la ventana real de cada nivel
# (diario=3 sesiones, semanal=7 días, mensual=30, anual=365) a propósito: antes el estado
# "Diaria" mostraba el nivel "semanal" y la leyenda no coincidía con lo que el usuario acababa
# de elegir — confuso en la práctica, aunque internamente fuera coherente. Nunca renombrar un
# estado sin que su `support`/`resistance` apunte al campo de SupportLevels/ResistanceLevels con
# el mismo nombre.

SPECULATION_CHART_VIEWS = {
    "Diaria": {"window_days": 14, "support": "daily", "resistance": "daily", "support_label": "Soporte diario", "resistance_label": "Resistencia diaria"},
    "Semanal": {"window_days": 30, "support": "weekly", "resistance": "weekly", "support_label": "Soporte semanal", "resistance_label": "Resistencia semanal"},
    "Mensual": {"window_days": 90, "support": "monthly", "resistance": "monthly", "support_label": "Soporte mensual", "resistance_label": "Resistencia mensual"},
    "Anual": {"window_days": 365, "support": "yearly", "resistance": "yearly", "support_label": "Soporte anual", "resistance_label": "Resistencia anual"},
}

REGIME_LABEL = {"fuerte": "Fuerte (alcista)", "debil": "Débil (bajista)", "mixta": "Mixta"}

# Resultado de la investigación de esta sesión (train=60% más viejo del historial, test=40% más
# nuevo): la única combinación régimen/horizonte cuyo signo se mantuvo en las 4 pruebas para
# BTC y ETH fue "fuerte" a 20 y 30 días — SOL no confirmó (solo 2 de 4, y con magnitud ínfima).
# No recalculado en vivo: es un hallazgo fijo de esa investigación, documentado en CLAUDE.md —
# si se repite el backtest con más historial en el futuro, actualizar acá.

REGIME_VALIDATED_COMBOS = {
    "BTC": {("fuerte", 20), ("fuerte", 30)},
    "ETH": {("fuerte", 20), ("fuerte", 30)},
    "SOL": set(),
}

# Refinamiento probado en una sesión posterior sobre el mismo split 60/40: dentro de los días ya
# "fuerte", separar además por RSI ≥ 70 (sobrecompra) contra RSI < 70. El diferencial de retorno
# entre ambos subgrupos mantuvo signo positivo en los 4 horizontes, train y test, únicamente para
# BTC — para ETH el signo se invirtió entre train y test (no persistió), y por eso no está acá.
# No confundir con REGIME_VALIDATED_COMBOS: ese ya valida "fuerte" solo; esto es el paso extra de
# "fuerte + sobrecompra" superando a "fuerte sin sobrecompra", no una condición independiente.

REGIME_RSI_OVERBOUGHT_VALIDATED_HORIZONS = {
    "BTC": {20, 30},
    "ETH": set(),
    "SOL": set(),
}

# Igual que SR_VALIDATED_SCORE_PERCENTILE/HORIZONS_DAYS de Cripto (src/ui/cripto.py) — no se
# comparte cross-módulo porque son 2 constantes triviales, no vale acoplar los dos archivos por
# esto. STOCK_SR_VALIDATED_TICKERS queda vacío a propósito: la única validación fuera de muestra
# que existió para acciones (TSLA soporte+resistencia, AAPL resistencia con fragilidad de
# umbral) corrió bajo la fórmula de score ANTERIOR al rediseño de "calidad de reacción" del
# motor (ver src/support_resistance.py) — no se puede asumir vigente sin repetir el mismo test
# bajo la fórmula/serie de referencia actuales, mismo principio que ya aplicó este proyecto para
# Fibonacci/ADX/OBV y para la propia re-validación de Cripto (que también terminó en {}).
STOCK_SR_VALIDATED_SCORE_PERCENTILE = 55.0
STOCK_SR_VALIDATED_HORIZONS_DAYS = [5, 10, 20, 30]
STOCK_SR_VALIDATED_TICKERS: dict[str, set[str]] = {}


# TTL largo (6h), no los 900s de _cached_historical_prices — mismo criterio que
# _cached_sr_levels en cripto.py: el costo acá es de CPU (clustering/regresión/optimización),
# no de red, así que no hace falta recomputar esto cada vez que el precio se refresca.
@st.cache_data(ttl=21600, show_spinner=False)
def _cached_stock_sr_levels(
    ticker: str,
    enabled_methods: tuple[str, ...],
    top_n: int,
    min_touch_points: int,
):
    """A diferencia de Cripto, acá no hay una serie de 4h nativa (yfinance no la tiene) — la
    serie de referencia del motor ES la diaria (daily_reference_config(), ver
    support_resistance.py), pasada dos veces: como `historical_prices` (referencia/caminata de
    touches) y como `daily_prices` (candidatos "daily" nativos + reagregado semanal/mensual) —
    es el mismo array a propósito, no un bug de copy-paste."""
    historical_prices, _ = _cached_historical_prices(ticker)
    config = daily_reference_config(
        enabled_methods=set(enabled_methods), top_n=top_n, min_touch_points=min_touch_points
    )
    return detect_levels(historical_prices, config, daily_prices=historical_prices)


def render_levels_chart(historical_prices: list[dict], supports, resistances, ticker: str, view: str) -> None:
    spec = SPECULATION_CHART_VIEWS.get(view, SPECULATION_CHART_VIEWS["Semanal"])
    dated = sorted(historical_prices, key=lambda p: p["date"])
    if not dated:
        return
    cutoff = datetime.strptime(dated[-1]["date"], "%Y-%m-%d") - timedelta(days=spec["window_days"])
    window = [p for p in dated if datetime.strptime(p["date"], "%Y-%m-%d") >= cutoff]
    if len(window) < 2:
        return

    x = [p["date"] for p in window]
    y = [p["close"] for p in window]

    fig = go.Figure()
    fig.add_trace(
        go.Scatter(x=x, y=y, mode="lines", name=f"Precio ({ticker})", line=dict(color=LEVEL_CHART_COLORS["price"], width=3))
    )

    support_value = getattr(supports, spec["support"])
    resistance_value = getattr(resistances, spec["resistance"])
    level_specs = [
        (support_value, spec["support_label"], LEVEL_CHART_COLORS[f"support_{spec['support']}"], "dash"),
        (resistance_value, spec["resistance_label"], LEVEL_CHART_COLORS[f"resistance_{spec['resistance']}"], "dot"),
    ]
    for value, label, color, dash in level_specs:
        if value is None:
            continue
        fig.add_trace(
            go.Scatter(
                x=[x[0], x[-1]],
                y=[value, value],
                mode="lines",
                name=label,
                line=dict(color=color, width=2, dash=dash),
                hovertemplate=f"{label}: ${value:,.2f}<extra></extra>",
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


def render_speculation_indicators(
    key_prefix: str,
    ticker: str,
    historical_prices: list[dict],
    closes: list[float],
    current_price: float,
    is_crypto: bool,
) -> None:
    """El cuerpo compartido de indicadores técnicos — RSI, EMA/SMA, Soportes/Resistencias
    simples, Plan de DCA sugerido (solo `is_crypto`), MACD, Bandas de Bollinger, ADX, OBV.
    Antes vivía inline en `render_speculation()` y se aplicaba tanto a acciones como a BTC/ETH/
    SOL; ahora Especulación es solo-acciones y la pestaña "🪙 Cripto" (`render_crypto()`) es la
    única que pasa `is_crypto=True` — se extrajo a una función propia para no duplicar ~350
    líneas de RSI/MACD/Bollinger/ADX/OBV entre ambas. El único comportamiento condicionado por
    `is_crypto` es el "📋 Plan de DCA sugerido" (no tiene sentido para acciones, que no se
    acumulan en DCA en este proyecto) — todo lo demás corre igual para cualquier ticker.

    `key_prefix` (único por caller: "speculation" / "crypto") evita colisión de keys de widget:
    `st.tabs()` no es lazy (ver CLAUDE.md), así que Especulación Y Cripto ejecutan esta función
    en el mismo rerun — un `key="speculation_chart_view"` fijo, compartido entre ambas, rompía
    con `StreamlitDuplicateElementKey` apenas se armó la pestaña Cripto (encontrado al probar
    este refactor, mismo tipo de bug ya documentado para `render_sticky_price()`)."""
    st.divider()
    st.subheader("RSI (14)")
    rsi = compute_rsi(closes)
    if rsi is None:
        st.caption("No hay suficiente historial para calcular el RSI.")
    else:
        st.metric("RSI", f"{rsi:.1f}")
        if rsi >= 70:
            st.info(
                "🔴 Sobrecompra clásica (RSI ≥ 70). Históricamente, niveles así suelen ir "
                "seguidos de una pausa o corrección de corto plazo — podría enfriarse en los "
                "próximos días, aunque en tendencias muy fuertes el RSI puede quedarse "
                "'pegado' arriba de 70 por un buen tramo antes de corregir."
            )
        elif rsi <= 30:
            st.info(
                "🟢 Sobreventa clásica (RSI ≤ 30). Históricamente, niveles así suelen ir "
                "seguidos de un rebote de corto plazo — podría recuperarse en los próximos "
                "días, aunque en caídas muy fuertes el RSI puede quedarse 'pegado' abajo de "
                "30 por un buen tramo antes de rebotar."
            )
        else:
            st.info("Zona neutral (entre 30 y 70) — sin lectura histórica fuerte de rebote ni de corrección inminente.")

    st.divider()
    st.subheader("Medias móviles (EMA 55 / SMA 50-200)")
    tr = evaluate_trend(historical_prices, current_price)
    if tr is None:
        st.caption("No hay suficiente historial para calcular estos indicadores.")
    else:
        st.metric("EMA de 55 días", f"${tr.ema:,.2f}", f"{tr.price_vs_ema:+.1%}")
        ema_col1, ema_col2 = st.columns(2)
        if tr.sma_50 is not None:
            ema_col1.metric("SMA de 50 días", f"${tr.sma_50:,.2f}", f"{tr.price_vs_sma_50:+.1%}")
        if tr.sma_200 is not None:
            ema_col2.metric("SMA de 200 días", f"${tr.sma_200:,.2f}", f"{tr.price_vs_sma_200:+.1%}")

        trend_state = classify_trend_state(tr)
        if trend_state == "fuerte":
            st.info(
                "💪 Sostenido arriba en las 3 medias. Históricamente, una tendencia así de "
                "sostenida tiende a seguir de largo antes de cortar — podría seguir subiendo "
                "en el corto plazo, aunque cuanto más estirada quede sobre sus propias medias, "
                "más aumenta el riesgo de una corrección brusca cuando se agote."
            )
        elif trend_state == "debil":
            st.info(
                "⚠️ Sostenido abajo en las 3 medias. Históricamente, una tendencia así de "
                "sostenida a la baja tiende a seguir de largo antes de cortar — podría seguir "
                "cayendo en el corto plazo, aunque cuanto más estirada quede por debajo, más "
                "aumenta la chance de un rebote técnico cuando se agote."
            )
        else:
            st.info("➖ Señal mixta entre las 3 medias — históricamente esto no anticipa una dirección clara, el precio podría irse para cualquier lado en el corto plazo.")

    st.divider()
    st.subheader("Soportes y Resistencias")
    st.caption(
        "Soporte = el cierre más bajo de la ventana elegida; resistencia = el más alto — el "
        "mismo nivel, mirado desde el otro lado."
    )
    supports = compute_support_levels(historical_prices)
    resistances = compute_resistance_levels(historical_prices)

    chart_view = st.segmented_control(
        "Ventana",
        list(SPECULATION_CHART_VIEWS.keys()),
        default="Semanal",
        key=f"{key_prefix}_chart_view",
    )
    view = chart_view or "Semanal"
    view_spec = SPECULATION_CHART_VIEWS[view]

    # Las métricas y el gráfico muestran el mismo par soporte/resistencia — el que corresponde
    # a la ventana elegida arriba, no los 6 niveles juntos (era demasiada info de una).
    support_value = getattr(supports, view_spec["support"])
    resistance_value = getattr(resistances, view_spec["resistance"])
    level_col1, level_col2 = st.columns(2)
    if support_value is not None:
        colored_metric(
            level_col1, view_spec["support_label"], f"${support_value:,.2f}", LEVEL_CHART_COLORS[f"support_{view_spec['support']}"]
        )
    if resistance_value is not None:
        colored_metric(
            level_col2,
            view_spec["resistance_label"],
            f"${resistance_value:,.2f}",
            LEVEL_CHART_COLORS[f"resistance_{view_spec['resistance']}"],
        )

    st.info(
        "Si el precio se acerca a un **soporte**, históricamente suele encontrar compradores "
        "ahí y podría rebotar; si lo perfora hacia abajo, históricamente eso tiende a acelerar "
        "la caída — el soporte roto suele pasar a actuar como resistencia. Si se acerca a una "
        "**resistencia**, históricamente suele encontrar vendedores y podría rechazar; si la "
        "rompe hacia arriba, históricamente acelera la subida — la resistencia rota suele "
        "pasar a actuar como soporte."
    )
    render_levels_chart(historical_prices, supports, resistances, ticker, view)

    if is_crypto:
        st.divider()
        st.subheader("📋 Plan de DCA sugerido")
        current_regime = classify_trend_state(tr) if tr is not None else None
        validated_combos = REGIME_VALIDATED_COMBOS.get(ticker, set())
        regime_has_validated_edge = any(regime == current_regime for regime, _horizon in validated_combos)

        rsi_overbought_now = rsi is not None and rsi >= 70
        rsi_reinforced_horizons = REGIME_RSI_OVERBOUGHT_VALIDATED_HORIZONS.get(ticker, set())
        regime_rsi_edge = current_regime == "fuerte" and rsi_overbought_now and bool(rsi_reinforced_horizons)

        # Números reales de ESTE ticker (no una frase genérica con el nombre insertado) — usa
        # compute_regime_reactions() sobre el propio historial de precio del ticker elegido, no
        # una tabla compartida entre todos.
        current_regime_stats = {}
        if current_regime is not None:
            current_regime_stats = {
                r.horizon_days: r for r in compute_regime_reactions(closes) if r.regime == current_regime
            }

        def _stat_phrase(horizon_days: int) -> str | None:
            stat = current_regime_stats.get(horizon_days)
            if stat is None or stat.mean_return is None:
                return None
            return f"{stat.mean_return:+.1%} a {horizon_days} días (win rate {stat.win_rate:.0%}, {stat.observations} casos)"

        if current_regime is None:
            st.caption("No hay suficiente historial para determinar el régimen actual.")
        elif regime_rsi_edge:
            rsi_stats = {r.horizon_days: r for r in compute_regime_rsi_reactions(closes)}

            def _rsi_stat_phrase(horizon_days: int) -> str | None:
                stat = rsi_stats.get(horizon_days)
                if stat is None or stat.mean_return is None:
                    return None
                return f"{stat.mean_return:+.1%} a {horizon_days} días (win rate {stat.win_rate:.0%}, {stat.observations} casos)"

            phrases = [p for h in sorted(rsi_reinforced_horizons) if (p := _rsi_stat_phrase(h))]
            detail = " y ".join(phrases) if phrases else "sesgo alcista reforzado"
            st.success(
                f"**Régimen actual de {ticker}: {REGIME_LABEL[current_regime]}, con RSI en "
                "sobrecompra (≥ 70).** Esta combinación específica tiene evidencia validada fuera "
                "de muestra propia (más allá de 'Fuerte' por sí solo): dentro de los días "
                "'Fuerte', separar además por RSI sobrecomprado mostró un retorno futuro "
                "consistentemente mayor que 'Fuerte' sin sobrecompra, con el mismo signo en el 60% "
                f"más viejo del historial y en el 40% más nuevo — para {ticker}, el propio "
                f"historial de esa combinación dio {detail}. Validado únicamente para BTC (no para "
                "ETH: ahí el diferencial cambió de signo entre entrenamiento y prueba). Sugerencia: "
                f"mantené o aumentá tu aporte periódico en {ticker} — esto ajusta *cuánto/cuándo* "
                "aportar, no te dice a qué precio comprar."
            )
        elif regime_has_validated_edge:
            phrases = [p for p in (_stat_phrase(20), _stat_phrase(30)) if p]
            detail = " y ".join(phrases) if phrases else "sesgo alcista histórico a 20-30 días"
            st.success(
                f"**Régimen actual de {ticker}: {REGIME_LABEL[current_regime]}.** Es la única "
                f"condición con evidencia validada fuera de muestra para {ticker} (entrenando "
                "con el 60% más viejo de su historial y confirmando con el 40% más nuevo): "
                f"retorno promedio histórico de {detail}. Sugerencia: mantené o aumentá tu "
                f"aporte periódico habitual en {ticker} — esto ajusta *cuánto/cuándo* aportar, "
                "no te dice a qué precio comprar."
            )
        else:
            phrases = [p for p in (_stat_phrase(20), _stat_phrase(30)) if p]
            context = (
                f" (a título informativo, sin confirmar fuera de muestra: {' y '.join(phrases)})"
                if phrases
                else ""
            )
            st.info(
                f"**Régimen actual de {ticker}: {REGIME_LABEL[current_regime]}.** No hay "
                f"evidencia validada que respalde ajustar tu aporte en {ticker} bajo esta "
                f"condición (ni para aumentarlo ni para reducirlo){context} — para {ticker}, "
                + (
                    "solo 'Fuerte' a 20-30 días pasó la validación fuera de muestra; el resto "
                    "de los regímenes/horizontes no se sostuvo en esa prueba."
                    if ticker != "SOL"
                    else "ningún régimen ni horizonte se sostuvo en la validación fuera de "
                    "muestra (a diferencia de BTC/ETH, que sí confirmaron 'Fuerte' a 20-30 días)."
                )
                + f" Sugerencia: mantené tu plan de DCA habitual en {ticker} sin cambios — no "
                "hay base para pausar ni para acelerar compras."
            )

    st.divider()
    st.subheader("MACD (12/26/9)")
    macd = compute_macd(closes)
    if macd is None:
        st.caption("No hay suficiente historial para calcular el MACD.")
    else:
        macd_col1, macd_col2, macd_col3 = st.columns(3)
        macd_col1.metric("Línea MACD", f"{macd.macd:+.2f}")
        macd_col2.metric("Línea de señal", f"{macd.signal:+.2f}")
        macd_col3.metric("Histograma", f"{macd.histogram:+.2f}")
        if macd.histogram > 0:
            st.info(
                "🟢 MACD por encima de la señal — momentum de corto plazo a favor. "
                "Históricamente esto suele seguir empujando en la misma dirección por un "
                "tramo más — podría continuar la suba, aunque un cruce del histograma hacia "
                "abajo suele anticipar que el momentum se está agotando."
            )
        elif macd.histogram < 0:
            st.info(
                "🔴 MACD por debajo de la señal — momentum de corto plazo en contra. "
                "Históricamente esto suele seguir empujando en la misma dirección por un "
                "tramo más — podría continuar la baja, aunque un cruce del histograma hacia "
                "arriba suele anticipar un posible rebote."
            )
        else:
            st.info("MACD y señal coinciden — sin momentum claro, históricamente esto no anticipa una dirección fuerte de corto plazo.")

    st.divider()
    st.subheader("Bandas de Bollinger (20, 2σ)")
    bollinger = compute_bollinger_bands(closes)
    if bollinger is None:
        st.caption("No hay suficiente historial para calcular las bandas de Bollinger.")
    else:
        boll_col1, boll_col2, boll_col3 = st.columns(3)
        boll_col1.metric("Banda inferior", f"${bollinger.lower:,.2f}")
        boll_col2.metric("Banda media (SMA 20)", f"${bollinger.middle:,.2f}")
        boll_col3.metric("Banda superior", f"${bollinger.upper:,.2f}")
        if current_price >= bollinger.upper:
            st.info(
                "🔴 El precio está en o por encima de la banda superior — sobrecompra "
                "clásica. Tocar esta banda históricamente suele preceder una pausa o vuelta "
                "hacia la media — podría corregir en el corto plazo, aunque en tendencias muy "
                "fuertes el precio puede 'caminar' pegado a la banda por un tiempo antes de "
                "aflojar."
            )
        elif current_price <= bollinger.lower:
            st.info(
                "🟢 El precio está en o por debajo de la banda inferior — sobreventa "
                "clásica. Tocar esta banda históricamente suele preceder un rebote hacia la "
                "media — podría recuperarse en el corto plazo, aunque en caídas muy fuertes el "
                "precio puede seguir pegado a la banda por un tiempo antes de rebotar."
            )
        else:
            st.info(
                "El precio está dentro de las bandas, sin extremo de corto plazo — el rango "
                "que marcan la banda superior e inferior es el movimiento esperable mientras "
                "no se acerque a ninguna de las dos."
            )

    st.divider()
    st.subheader("ADX (14) — Fuerza de la tendencia")
    st.caption(
        "El ADX no dice si el precio va a subir o bajar — mide qué tan fuerte y sostenida es "
        "la tendencia actual, sea cual sea. Pensalo como un medidor de intensidad de viento: no "
        "importa de qué lado sopla, importa si sopla fuerte y parejo o si apenas hay brisa "
        "(precio moviéndose de costado, sin rumbo claro)."
    )
    highs = [p.get("high") for p in historical_prices]
    lows = [p.get("low") for p in historical_prices]
    adx = compute_adx(highs, lows, closes)
    if adx is None:
        st.caption("No hay suficiente historial (o datos de máximos/mínimos) para calcular el ADX.")
    else:
        adx_col1, adx_col2, adx_col3 = st.columns(3)
        adx_col1.metric("ADX", f"{adx.adx:.1f}")
        adx_col2.metric("+DI (empuje alcista)", f"{adx.plus_di:.1f}")
        adx_col3.metric("−DI (empuje bajista)", f"{adx.minus_di:.1f}")

        direction = "alcista 🟢" if adx.plus_di > adx.minus_di else "bajista 🔴"
        if adx.adx < 20:
            st.info(
                "➖ ADX por debajo de 20 — sin tendencia clara, el precio se mueve de costado. "
                "Clásicamente, en esta zona las señales de tendencia (medias móviles, MACD) son "
                "menos confiables: hay más chance de que sean señales falsas."
            )
        elif adx.adx < 25:
            st.info(
                f"🟡 ADX entre 20 y 25 — zona gris. Hay un empuje {direction} recién formándose, "
                "pero todavía no está confirmado con fuerza — clásicamente conviene esperar más "
                "confirmación antes de darle mucho peso a esta lectura."
            )
        else:
            st.info(
                f"💪 ADX por encima de 25 — tendencia {direction} fuerte y confirmada (cuanto "
                "más alto el número, más fuerte). Clásicamente, tendencias así de marcadas "
                "tienden a sostenerse un tramo más antes de perder fuerza — pero el ADX no "
                "avisa el techo/piso exacto, solo que la tendencia actual tiene 'motor'."
            )
        st.caption(
            "Se probó como posible refuerzo del régimen del '📋 Plan de DCA sugerido' (misma "
            "prueba fuera de muestra que validó el refuerzo de RSI para BTC) y no se sostuvo: "
            "el resultado cambiaba de signo entre entrenamiento y prueba, y variaba según el "
            "umbral de ADX elegido. Por eso queda acá como indicador descriptivo clásico, igual "
            "que el MACD y las Bandas de Bollinger — no ajusta esa sugerencia."
        )

    st.divider()
    st.subheader("Volumen (OBV — On-Balance Volume)")
    st.caption(
        "El OBV suma el volumen operado los días que el precio sube y lo resta los días que "
        "baja — es un acumulado, así que el número en sí no significa nada (no se compara entre "
        "tickers ni tiene una 'zona buena'). Lo que importa es si viene para arriba o para abajo: "
        "pensalo como un termómetro de convicción — ¿hay mucha gente comprando/vendiendo detrás "
        "del movimiento del precio, o el precio se mueve con poco respaldo?"
    )
    volumes = [p.get("volume") for p in historical_prices]
    obv = compute_obv(closes, volumes)
    if obv is None:
        st.caption("No hay suficiente historial (o datos de volumen) para calcular el OBV.")
    else:
        obv_col1, obv_col2 = st.columns(2)
        obv_col1.metric("OBV", f"{obv.obv:,.0f}")
        obv_col2.metric(f"OBV — media {OBV_SMA_PERIOD} días", f"{obv.obv_sma:,.0f}")

        price_trend = classify_trend_state(tr) if tr is not None else None
        if price_trend is None:
            st.caption("No hay suficiente historial de precio para cruzar el volumen contra la tendencia.")
        elif price_trend == "fuerte":
            if obv.rising:
                st.info(
                    "🟢 **Confirmación**: el precio viene subiendo Y el volumen lo acompaña "
                    "(OBV arriba de su propia media). Una suba con este respaldo de volumen "
                    "detrás suele ser más sólida que una que sube con poco volumen."
                )
            else:
                st.info(
                    "🟡 **Posible divergencia**: el precio viene subiendo pero el volumen NO "
                    "lo acompaña (OBV abajo de su propia media) — subas con este patrón a "
                    "veces pierden fuerza más rápido que las que sí tienen respaldo de volumen."
                )
        elif price_trend == "debil":
            if not obv.rising:
                st.info(
                    "🔴 **Confirmación bajista**: el precio viene cayendo Y el volumen lo "
                    "acompaña (OBV abajo de su propia media) — presión vendedora sostenida "
                    "detrás de la baja."
                )
            else:
                st.info(
                    "🟡 **Posible divergencia**: el precio viene cayendo pero el volumen NO "
                    "confirma la baja (OBV arriba de su propia media) — a veces anticipa que "
                    "la presión vendedora se está agotando, aunque no es una señal fuerte por "
                    "sí sola."
                )
        else:
            st.info("➖ Tendencia de precio mixta — sin una dirección clara contra la cual cruzar el volumen.")

        st.caption(
            "Se probó como posible refuerzo del régimen del '📋 Plan de DCA sugerido' (misma "
            "prueba fuera de muestra que las anteriores) y el resultado fue demasiado sensible "
            "al período de la media elegida (10/20/30 días daban conclusiones distintas) para "
            "confiar en él. Por eso queda acá como cruce descriptivo contra la tendencia, no "
            "como parte de esa recomendación."
        )


def render_speculation():
    """A diferencia de toda otra pestaña: acá el lenguaje de timing es a propósito, no hay
    cruce con fundamentales, y no tiene nada que ver con el Portafolio. Solo carga datos del
    ticker elegido (no los 8 de una, como Acciones) — no hay "carga inicial" de todo el
    universo. Usa TICKERS (el mismo universo de Acciones) — BTC/ETH/SOL se movieron a la
    pestaña "🪙 Cripto" (ver `render_crypto()`), que corre la misma
    `render_speculation_indicators()` pero sobre datos de Binance en vez de yfinance."""
    st.title("🎲 Especulación")
    st.warning(
        "⚠️ Esta pestaña es distinta a todo el resto del dashboard: son indicadores técnicos "
        "de corto plazo pensados para timing, sin cruzarlos con fundamentales ni con tu "
        "Portafolio. El resto de la app evita a propósito este tipo de señal — acá sí se "
        "permite."
    )

    ticker = st.selectbox("Ticker", TICKERS, key="speculation_ticker")

    try:
        historical_prices, _ = _cached_historical_prices(ticker)
    except DataError:
        st.error(f"No pudimos consultar {ticker} ahora mismo.")
        return

    closes = [p["close"] for p in historical_prices]
    if not closes:
        st.caption("No hay historial de precios disponible para este ticker.")
        return
    current_price = closes[-1]

    render_sticky_price("speculation", f"Precio actual — {ticker}", current_price, ticker)
    render_speculation_indicators("speculation", ticker, historical_prices, closes, current_price, is_crypto=False)

    st.divider()
    st.subheader("🧭 Market Reaction Zone Engine")
    st.caption(
        "Identifica zonas de soporte/resistencia con evidencia estadística suficiente, "
        "priorizando la CALIDAD de la reacción del mercado por sobre la cantidad de toques — "
        "tres rebotes fuertes con volumen alto valen más que diez toques sin reacción "
        "relevante. Mismo motor que la pestaña Cripto (clustering DBSCAN, densidad KDE, líneas "
        "de tendencia robustas RANSAC/Theil-Sen/Huber, Transformada de Hough, multi-timeframe "
        "diario/semanal/mensual con jerarquía institucional, optimización de cada línea), pero "
        "sobre datos diarios de yfinance en vez de velas de 4h de Binance — yfinance no tiene "
        "un intervalo de 4h nativo para acciones."
    )
    st.warning(
        "⚠️ **Es descriptivo, no una señal de trading**: identifica y puntúa zonas, no dice si "
        "conviene comprar o vender cerca de ellas. La cercanía a un soporte/resistencia muy "
        "tocado ya se probó como señal de entrada en una investigación anterior (clustering de "
        "múltiples toques, fuera de muestra, incluyendo AAPL/TSLA) y no se sostuvo — para SOL/"
        "TSLA incluso apuntó al revés en algún caso. Este motor es más sofisticado que aquel "
        "intento, pero esa misma pregunta de fondo no está validada todavía para acciones bajo "
        "esta versión del score (ver 'Lectura validada' más abajo)."
    )

    with st.expander("⚙️ Configuración avanzada"):
        selected_methods = st.multiselect(
            "Metodologías activas",
            list(SR_METHOD_LABELS.keys()),
            default=list(SR_METHOD_LABELS.keys()),
            format_func=lambda k: SR_METHOD_LABELS[k],
            key="stock_sr_enabled_methods",
        )
        stock_sr_col1, stock_sr_col2 = st.columns(2)
        stock_top_n = stock_sr_col1.slider("Cantidad de niveles a mostrar", 3, 15, 8, key="stock_sr_top_n")
        stock_min_touch_points = stock_sr_col2.slider("Mínimo de touch points", 1, 5, 3, key="stock_sr_min_touches")

    stock_result_key = f"stock_sr_levels_result_{ticker}"
    if st.button("🔍 Calcular niveles multi-metodología", key="stock_sr_compute_button"):
        with st.spinner("Corriendo clustering, KDE, líneas robustas, Hough y más — puede tardar unos segundos..."):
            st.session_state[stock_result_key] = _cached_stock_sr_levels(
                ticker, tuple(sorted(selected_methods)), stock_top_n, stock_min_touch_points
            )

    stock_sr_levels = st.session_state.get(stock_result_key)
    if stock_sr_levels is None:
        st.caption(
            "Sin calcular todavía — apretá el botón de arriba. No se corre automáticamente "
            "porque implica varios algoritmos de clustering/regresión y puede tardar unos "
            "segundos."
        )
    elif not stock_sr_levels:
        st.caption("No se detectaron niveles con suficiente evidencia para este ticker con la configuración actual.")
    else:
        # Filtro puramente de VISUALIZACIÓN — no toca stock_sr_levels ni SRConfig (ver
        # daily_reference_config en support_resistance.py), mismo patrón que Cripto.
        stock_max_distance_pct = st.slider(
            "Mostrar niveles a menos de X% del precio actual",
            min_value=5,
            max_value=100,
            value=20,
            step=5,
            key="stock_sr_max_distance_pct",
            help=(
                "Solo filtra qué se muestra acá abajo — no recalcula ni cambia los niveles "
                "detectados."
            ),
        )

        stock_available_timeframes = sorted(
            {tf for lv in stock_sr_levels for tf in lv.timeframes}, key=lambda tf: SR_TIMEFRAME_ORDER.get(tf, 99)
        )
        stock_selected_timeframes = st.multiselect(
            "Temporalidades a mostrar",
            stock_available_timeframes,
            default=stock_available_timeframes,
            format_func=lambda tf: SR_TIMEFRAME_LABELS.get(tf, tf),
            key="stock_sr_timeframe_filter",
            help="También es solo de visualización — muestra un nivel si apareció en CUALQUIERA de las temporalidades elegidas.",
        )

        def _stock_level_distance_pct(lv: SRLevel) -> float:
            if lv.kind == "channel":
                sides = [abs(s.distance_to_price_pct) for s in (lv.channel_support, lv.channel_resistance) if s is not None]
                return min(sides) if sides else float("inf")
            return abs(lv.distance_to_price_pct)

        stock_filtered_levels = [
            lv
            for lv in stock_sr_levels
            if _stock_level_distance_pct(lv) <= stock_max_distance_pct / 100
            and set(lv.timeframes) & set(stock_selected_timeframes)
        ]

        if not stock_filtered_levels:
            st.caption(
                f"Ningún nivel pasa los filtros actuales (menos de {stock_max_distance_pct}% del "
                "precio actual y alguna de las temporalidades elegidas) — ampliá el % o sumá más "
                "temporalidades para ver más."
            )
        else:
            stock_rows = []
            for lv in stock_filtered_levels:
                tipo = {"support": "🟢 Soporte", "resistance": "🔴 Resistencia"}.get(
                    lv.kind, f"🟣 Canal ({lv.channel_direction})"
                )
                stock_rows.append(
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
                        # A diferencia de Cripto (age_bars en barras de 4h, ÷6 para mostrar
                        # días): acá la referencia YA es diaria (daily_reference_config), así
                        # que age_bars ya está en días — sin conversión.
                        "Antigüedad (días)": lv.age_bars,
                        "Temporalidades": ", ".join(lv.timeframes),
                        "Métodos": ", ".join(lv.methods),
                        "Dist. al precio actual": f"{lv.distance_to_price_pct:+.1%}" if lv.kind != "channel" else "—",
                    }
                )
            st.dataframe(pd.DataFrame(stock_rows), use_container_width=True, hide_index=True)
            render_advanced_levels_chart(historical_prices, historical_prices, stock_filtered_levels, ticker)

        st.divider()
        st.subheader("📋 Lectura validada fuera de muestra")
        stock_validated_kinds = STOCK_SR_VALIDATED_TICKERS.get(ticker, set())
        if not stock_validated_kinds:
            st.caption(
                "No hay evidencia validada fuera de muestra para ningún ticker todavía bajo "
                "esta versión del motor (calidad de reacción + ajuste estadístico de "
                "consistencia). La única validación que existió para acciones (TSLA soporte y "
                "resistencia, AAPL resistencia con fragilidad de umbral) corrió bajo la fórmula "
                "de score ANTERIOR al rediseño — no se puede asumir vigente sin repetir el mismo "
                "test bajo la fórmula actual, mismo criterio ya aplicado a Fibonacci/ADX/OBV y a "
                "la propia re-validación de Cripto (que también terminó sin ningún ticker "
                "validado). Esto no es evidencia de que NO haya señal para este ticker — solo "
                "que todavía no se confirmó, y el resto de esta sección se queda puramente "
                "descriptivo."
            )
        else:
            stock_any_hit = False
            for kind in ("support", "resistance"):
                if kind not in stock_validated_kinds:
                    continue
                stock_score_threshold = score_percentile_threshold(stock_sr_levels, kind, STOCK_SR_VALIDATED_SCORE_PERCENTILE)
                if stock_score_threshold is None:
                    continue
                stock_qualifying = [
                    lv for lv in stock_sr_levels
                    if lv.kind == kind and lv.confidence_score >= stock_score_threshold and lv.zone_low is not None
                ]
                stock_hit = any(lv.zone_low <= current_price <= lv.zone_high for lv in stock_qualifying)
                if not stock_hit:
                    continue
                stock_any_hit = True
                stock_reactions = compute_level_zone_reactions(
                    historical_prices, stock_sr_levels, kind, stock_score_threshold, STOCK_SR_VALIDATED_HORIZONS_DAYS
                )
                stock_phrases = [
                    f"{r.mean_return:+.1%} a {r.horizon_days} días (win rate {r.win_rate:.0%}, {r.observations} casos)"
                    for r in stock_reactions
                    if r.mean_return is not None
                ]
                stock_detail = " · ".join(stock_phrases) if stock_phrases else "sin suficientes observaciones recientes"
                stock_kind_label = "soporte" if kind == "support" else "resistencia"
                stock_validated_for = "/".join(sorted(t for t, ks in STOCK_SR_VALIDATED_TICKERS.items() if kind in ks))
                st.success(
                    f"**{ticker} está hoy dentro de la zona de un {stock_kind_label} en el "
                    f"percentil {STOCK_SR_VALIDATED_SCORE_PERCENTILE:.0f} de sus propios "
                    f"niveles (score ≥ {stock_score_threshold:.0f} hoy).** Para este ticker y "
                    "este tipo de nivel, la cercanía a una zona así mostró un retorno futuro "
                    "distinto al promedio general, con el mismo signo en el 60% más viejo del "
                    "historial y en el 40% más nuevo, en los 4 horizontes probados: "
                    f"{stock_detail}. Validado únicamente para {stock_validated_for} "
                    f"({stock_kind_label}) — no generalizar a otros tickers ni a otros tipos de "
                    "nivel sin repetir la misma prueba fuera de muestra."
                )
            if not stock_any_hit:
                st.info(
                    f"{ticker} tiene evidencia validada fuera de muestra para este tipo de "
                    f"análisis, pero el precio actual no está dentro de la zona de ningún nivel "
                    f"en el percentil {STOCK_SR_VALIDATED_SCORE_PERCENTILE:.0f} de sus propios "
                    "niveles en este momento — el resto de esta sección se queda puramente "
                    "descriptiva hasta que eso cambie."
                )
