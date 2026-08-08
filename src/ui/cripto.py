"""Pestaña "🪙 Cripto" — BTC/ETH/SOL únicamente, sobre datos de Binance (`src/data/
binance_client.py`), no yfinance. Combina el mismo cuerpo de indicadores que Especulación
(`render_speculation_indicators()`, importado de `src/ui/speculation.py`) con el Market Reaction
Zone Engine (`src/support_resistance.py`) — motor multi-metodología de zonas de soporte/
resistencia rediseñado para priorizar calidad de reacción por sobre cantidad de touches.
Extraído de app.py (que llegó a 2821 líneas) para modularizar — ver `financial-advisor-cripto` skill
para el diseño completo, los bugs reales encontrados construyendo el motor, y el estado de la
validación fuera de muestra (pendiente de re-correr tras el rediseño)."""

from datetime import datetime, timedelta

import pandas as pd
import streamlit as st

from src.config import CRYPTO_BINANCE_SYMBOLS
from src.data import binance_client
from src.data.errors import DataError
from src.support_resistance import (
    SRConfig,
    SRLevel,
    compute_level_zone_reactions,
    detect_levels,
    score_percentile_threshold,
)
from src.ui.shared import (
    SR_KIND_RGB,
    SR_METHOD_LABELS,
    SR_TIMEFRAME_LABELS,
    SR_TIMEFRAME_ORDER,
    render_advanced_levels_chart,
    render_sticky_price,
)
from src.ui.speculation import render_speculation_indicators


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
