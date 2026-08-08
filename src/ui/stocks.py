"""Pestaña "📈 Acciones" — lista + detalle de las valuaciones de TICKERS. Extraído de app.py
(que llegó a 2821 líneas) para modularizar. `render_list()`/`render_detail()` son los dos únicos
puntos donde se computa un resumen de TICKERS y por eso los dos únicos que llaman
`_maybe_record_verdict()` (historial de veredictos, ver `financial-advisor-validation`)."""

import streamlit as st

from src.config import TICKERS
from src.data.errors import DataError
from src.preferences import load_selected_tickers, save_selected_tickers
from src.verdict_history import record_verdict
from src.ui.shared import (
    ZONE_COLOR,
    STOCK_EVAL_CACHE_KEY,
    _cached_evaluation,
    _get_or_fetch,
    classify_trend_state,
    format_as_of,
    render_sticky_price,
    scroll_to_top,
    triangulation_badge,
    zone_badge,
)
from src.valuation.fair_value import (
    PROVIDERS,
    compare_providers,
    multiple_quality_context_note,
    quality_context_note,
    summarize_signals,
    trend_context_note,
)

PROVIDER_LABELS = {"fmp": "Financial Modeling Prep", "yfinance": "yfinance"}

# El Portafolio solo acepta los CDIs colombianos (GOOGLCO, ...), no las acciones en USD de
# TICKERS — las compras reales del usuario se hacen en pesos vía estos CDIs. No participan
# de las 6 fórmulas de valoración (ver comentario en config.py sobre por qué).


def _maybe_record_verdict(ticker: str, summary: dict, price: float) -> None:
    """Graba como máximo una vez por ticker por SESIÓN (no por rerun — Streamlit rerenderiza
    varias veces por sesión) para no tocar disco de más; record_verdict() ya dedupea por fecha
    calendario internamente, así que esto es una optimización, no lo que hace correcto el
    dedupe (dos sesiones el mismo día seguirían resultando en una sola entrada)."""
    recorded = st.session_state.setdefault("_verdict_recorded_today", set())
    if ticker in recorded:
        return
    record_verdict(ticker, summary, price)
    recorded.add(ticker)


TREND_STATE_COLOR = {
    "fuerte": ZONE_COLOR["Acumulación"],
    "mixta": ZONE_COLOR["Precio justo"],
    "debil": ZONE_COLOR["Sobrevalorado"],
}

TREND_STATE_LABEL = {
    "fuerte": "💪 Tendencia fuerte",
    "mixta": "➖ Señal mixta",
    "debil": "⚠️ Tendencia débil",
}

TREND_STATE_TAKEAWAY = {
    "fuerte": (
        "El precio viene subiendo de forma sostenida: está por encima de su propio promedio de "
        "corto, mediano y largo plazo. Si comprás, promediar en el tiempo evita entrar justo "
        "después de una subida fuerte."
    ),
    "mixta": "El precio no tiene una dirección clara: está por encima de algunos de sus propios promedios y por debajo de otros.",
    "debil": (
        "El precio viene cayendo de forma sostenida: está por debajo de su propio promedio de "
        "corto, mediano y largo plazo. Antes de comprar, vale la pena entender si es algo "
        "pasajero o más de fondo."
    ),
}


def trend_state_badge(state: str, small: bool = False) -> str:
    color = TREND_STATE_COLOR[state]
    label = TREND_STATE_LABEL[state]
    font_size = "0.72rem" if small else "0.85rem"
    padding = "1px 8px" if small else "3px 12px"
    return (
        f'<span style="background:{color}22;color:{color};border:1px solid {color};'
        f'padding:{padding};border-radius:12px;font-size:{font_size};font-weight:600;'
        f'white-space:nowrap;">{label}</span>'
    )


def quality_badge(quality, small: bool = False) -> str:
    if quality is None:
        return ""
    if quality.creates_value:
        color, label = ZONE_COLOR["Acumulación"], "✅ Multiplica lo que reinvierte"
    else:
        color, label = ZONE_COLOR["Sobrevalorado"], "⚠️ Pierde valor al reinvertir"
    font_size = "0.72rem" if small else "0.85rem"
    padding = "1px 8px" if small else "3px 12px"
    return (
        f'<span style="background:{color}22;color:{color};border:1px solid {color};'
        f'padding:{padding};border-radius:12px;font-size:{font_size};font-weight:600;'
        f'white-space:nowrap;">{label}</span>'
    )


def explain_dcf(evaluation) -> str:
    word = "más barata" if evaluation.dcf_margin >= 0 else "más cara"
    d = evaluation.dcf
    return (
        f"Sumamos el dinero que se espera que **{evaluation.ticker}** genere en los próximos años, "
        "traído a valor de hoy, en 3 escenarios (pesimista/base/optimista). Valor esperado: "
        f"**${d.fair_value_per_share:,.2f}**. Hoy cotiza a ${evaluation.current_price:,.2f}, un "
        f"**{abs(evaluation.dcf_margin):.0%} {word}** que ese valor."
    )


def explain_multiple(evaluation) -> str:
    word = "más barata" if evaluation.multiple_margin >= 0 else "más cara"
    return (
        f"**{evaluation.ticker}** se vendió, en promedio, a **{evaluation.multiple.mean_pe:.1f} veces** "
        f"sus ganancias (P/E). Hoy cotiza a **{evaluation.multiple.current_pe:.1f} veces**, un "
        f"**{abs(evaluation.multiple_margin):.0%} {word}** que su propio promedio histórico."
    )


def explain_book_value(evaluation) -> str:
    word = "más barata" if evaluation.book_value_margin >= 0 else "más cara"
    return (
        f"Si **{evaluation.ticker}** vendiera todo y pagara sus deudas, quedarían ~"
        f"**${evaluation.book_value.book_value_per_share:,.2f}** por acción — un "
        f"**{abs(evaluation.book_value_margin):.0%} {word}** que el precio de hoy. El método más "
        "conservador: en tecnológicas, gran parte del valor real (marca, software, patentes) no "
        "entra en esta cuenta."
    )


def explain_graham(evaluation) -> str:
    if evaluation.graham is None:
        return (
            f"No pudimos calcular esta señal para **{evaluation.ticker}**: necesita ganancias y "
            "valor en libros positivos."
        )
    word = "más barata" if evaluation.graham_margin >= 0 else "más cara"
    return (
        "Fórmula de Graham para inversores conservadores: asume un P/E de 15 y P/B de 1.5 como "
        f"'normales'. Con eso, **{evaluation.ticker}** debería valer ~**${evaluation.graham.fair_value:,.2f}**, "
        f"un **{abs(evaluation.graham_margin):.0%} {word}** que el precio de hoy."
    )


def explain_graham_growth(evaluation) -> str:
    if evaluation.graham_growth is None:
        return (
            f"No pudimos calcular esta señal para **{evaluation.ticker}**: necesita ganancias "
            "positivas y crecimiento sostenido."
        )
    word = "más barata" if evaluation.graham_growth_margin >= 0 else "más cara"
    return (
        "Otra fórmula de Graham: múltiplo justo = 8.5 + 2×crecimiento, ajustado por el nivel de "
        f"tasas de interés actual. Con un crecimiento del **{evaluation.graham_growth.growth_rate:.0%}**, "
        f"el múltiplo implícito es **{evaluation.graham_growth.implied_multiple:.1f}x** → valor ~"
        f"**${evaluation.graham_growth.fair_value:,.2f}**, un **{abs(evaluation.graham_growth_margin):.0%} "
        f"{word}** que el precio de hoy."
    )


def explain_quality(evaluation) -> str:
    q = evaluation.quality
    if q is None:
        return "No pudimos estimar este filtro por falta de datos."
    if q.creates_value:
        return (
            f"✅ Sí, **crea valor** — gana un {q.roic:.0%} sobre el capital que invierte, más de "
            f"lo que le cuesta financiarse ({q.wacc:.0%})."
        )
    return (
        f"⚠️ No, **destruye valor** — gana solo un {q.roic:.0%} sobre el capital que invierte, "
        f"menos de lo que le cuesta financiarse ({q.wacc:.0%}), aunque se vea barata por otros métodos."
    )


def explain_growth(evaluation) -> str:
    if evaluation.growth is None:
        return (
            f"No pudimos calcular esta señal para **{evaluation.ticker}**: necesita ganancias "
            "positivas y crecimiento sostenido."
        )
    word = "más barata" if evaluation.growth_margin >= 0 else "más cara"
    dividend_note = (
        f" (+ dividendo de **{evaluation.growth.dividend_yield:.1%}**, variante PEGY de Lynch para "
        "empresas que también reparten capital)"
        if evaluation.growth.dividend_yield > 0
        else ""
    )
    return (
        f"**{evaluation.ticker}** creció su EPS ~**{evaluation.growth.eps_growth_rate:.0%}/año**. "
        f"Regla de Peter Lynch: el P/E 'justo' debería parecerse a ese crecimiento{dividend_note}. "
        f"Con eso, valdría ~**${evaluation.growth.fair_value:,.2f}**, un "
        f"**{abs(evaluation.growth_margin):.0%} {word}** que el precio de hoy."
    )


EMA_HELP = "Media móvil exponencial: pesa más los precios recientes, así que es la más rápida en reflejar un cambio de ritmo (no mide si está barata o cara)."

SMA_HELP = "Media móvil simple: promedia sin ponderar por recencia, más lenta que la EMA — confirma si algo es tendencia sostenida o solo ruido de corto plazo."

DIRECTION_EXPLAIN = {
    "al alza": "los analistas subieron sus expectativas de ganancias en 90 días — vieron algo que los volvió más optimistas.",
    "a la baja": "los analistas bajaron sus expectativas de ganancias en 90 días — vieron algo que los volvió menos optimistas.",
    "estable": "los analistas casi no cambiaron sus expectativas de ganancias en 90 días — sin novedades relevantes.",
}


def explain_analyst_view(evaluation) -> str:
    av = evaluation.analyst_view
    parts = []

    if av.price_target_mean:
        target_margin = (av.price_target_mean - evaluation.current_price) / evaluation.current_price
        word = "por encima" if target_margin >= 0 else "por debajo"
        parts.append(
            "El **precio objetivo** es el promedio de lo que estiman los analistas que va a valer la "
            f"acción en ~12 meses — no es garantía, es una opinión profesional. Hoy es "
            f"**${av.price_target_mean:,.2f}**, un **{abs(target_margin):.0%} {word}** del precio actual."
        )

    if av.estimate_revision_direction:
        direction_note = DIRECTION_EXPLAIN.get(av.estimate_revision_direction, "")
        parts.append(
            "La **tendencia de revisión** mide si los analistas suben o bajan cuánto esperan que la "
            f"empresa **gane** (no el precio): {direction_note}"
        )

    return " ".join(parts) if parts else "No hay suficiente información de analistas para explicar esta sección."


def render_method_card(method: dict):
    st.markdown(f"#### {method['title']}")
    if method["value"] is not None:
        st.metric(method["metric_label"], method["value"], f"{method['margin']:+.1%}")
        if method.get("extra_caption"):
            st.caption(method["extra_caption"])
        st.markdown(zone_badge(method["zone"]), unsafe_allow_html=True)
    else:
        st.metric(method["metric_label"], "No aplica")
    st.write("")
    st.write(method["explain"])
    if method.get("context_note"):
        st.info(f"💡 {method['context_note']}")


def render_method_grid(methods: list[dict]):
    for i in range(0, len(methods), 2):
        row = st.columns(2)
        for col, method in zip(row, methods[i : i + 2]):
            with col:
                render_method_card(method)


def _persist_ticker_filter():
    save_selected_tickers(st.session_state.ticker_filter)


def render_options_bar():
    col_tickers, col_provider = st.columns([3, 1])
    # semilla el estado del widget UNA sola vez por sesión desde el archivo guardado — nunca
    # con `default=` recalculado en cada rerun, que es justo lo que causaba el bug ("a veces
    # se elimina y se vuelve a crear"): si `default` cambia de una corrida a la siguiente,
    # Streamlit trata al multiselect como un widget distinto y pierde la interacción que el
    # usuario acababa de hacer. Con `key=` el widget maneja su propio estado siempre; on_change
    # guarda a disco solo cuando el valor realmente cambió por una interacción real.
    if "ticker_filter" not in st.session_state:
        st.session_state.ticker_filter = load_selected_tickers(TICKERS)
    selected = col_tickers.multiselect(
        "Acciones a mostrar", TICKERS, key="ticker_filter", on_change=_persist_ticker_filter
    )
    provider_keys = list(PROVIDERS.keys())
    provider = col_provider.selectbox(
        "Fuente de datos",
        provider_keys,
        index=provider_keys.index("yfinance"),
        format_func=lambda p: PROVIDER_LABELS[p],
    )
    if st.button("🔄 Actualizar datos"):
        _cached_evaluation.clear()
    return selected, provider


def render_list():
    st.title("¿A qué precio están estas acciones hoy?")
    st.caption(
        "Elegí una acción para ver si hoy está cara, barata, o a un precio razonable — explicado en simple."
    )

    with st.expander("¿Cómo lo calculamos?"):
        st.markdown(
            "Miramos cada acción de **6 formas distintas**. Pero 4 de esas 6 parten del mismo dato "
            "base (las ganancias por acción) y suelen moverse juntas, así que agruparlas como 6 votos "
            "separados infla artificialmente el 'consenso'. Por eso el resumen que ves arriba de cada "
            "acción agrupa todo en **3 familias genuinamente distintas** y compara esas 3, no las 6 "
            "fórmulas sueltas:\n\n"
            "- 🔮 **Flujo de caja (DCF)**: cuánto dinero se espera que la empresa genere en los "
            "próximos años, traído a valor de hoy — en 3 escenarios (pesimista/base/optimista).\n"
            "- 🏦 **Valor patrimonial**: cuánto quedaría por acción si la empresa vendiera todos sus "
            "activos y pagara todas sus deudas hoy. El método más conservador.\n"
            "- 📊 **Múltiplos de ganancias**: agrupa 4 fórmulas que parten del mismo EPS — el P/E "
            "propio histórico, el PEG/PEGY de Lynch, el Número de Graham, y la fórmula de crecimiento "
            "de Graham (8.5 + 2×crecimiento, ajustada por tasas). Te las mostramos por separado más "
            "abajo, pero para el resumen cuentan como una sola opinión, no cuatro.\n\n"
            "Ninguna familia es 'la verdad absoluta' — son 3 ángulos distintos para ayudarte a formar "
            "tu propio criterio.\n\n"
            "Además, mostramos un **filtro de calidad aparte** (ROIC vs. costo de capital): no dice si "
            "la acción está barata o cara, sino si la empresa gana más de lo que le cuesta financiarse "
            "cuando reinvierte. Es el filtro que Buffett aplica antes de mirar el precio."
        )

    selected, provider = render_options_bar()
    st.session_state.last_provider = provider
    st.divider()

    # trae las evaluaciones de los tickers filtrados en paralelo primero (I/O de red, la parte
    # lenta) y recién después dibuja las tarjetas en orden — así el tiempo total es el de la
    # llamada más lenta, no la suma de todas. El número de jobs es len(selected): si el usuario
    # filtró a 3 tickers, son 3 jobs, no 8. Guarda cada resultado en STOCK_EVAL_CACHE_KEY para
    # que Portafolio (que corre después en el mismo script) no vuelva a pedir lo mismo.
    stock_results = _get_or_fetch(
        STOCK_EVAL_CACHE_KEY, {(ticker, provider): (_cached_evaluation, (ticker, provider)) for ticker in selected}
    )
    evaluations = {ticker: stock_results[(ticker, provider)] for ticker in selected}

    columns = st.columns(3)
    for i, ticker in enumerate(selected):
        with columns[i % 3]:
            with st.container(border=True):
                evaluation, error = evaluations[ticker]
                if isinstance(error, DataError):
                    st.markdown(f"**{ticker}**")
                    st.caption("No pudimos consultar esta acción ahora mismo.")
                    continue
                if isinstance(error, ValueError):
                    st.markdown(f"**{ticker}**")
                    st.caption("No hay suficiente historial para evaluarla todavía.")
                    continue

                summary = summarize_signals(evaluation)
                _maybe_record_verdict(ticker, summary, evaluation.current_price)

                st.markdown(f"### {ticker}")
                st.caption("Precio actual")
                st.markdown(f"#### ${evaluation.current_price:,.2f}")
                st.markdown(triangulation_badge(summary, small=True), unsafe_allow_html=True)
                st.markdown(quality_badge(evaluation.quality, small=True), unsafe_allow_html=True)

                st.write("")
                if st.button("Ver detalle →", key=f"detail_{ticker}", use_container_width=True):
                    st.session_state.selected_ticker = ticker
                    st.rerun()


def render_detail(ticker: str):
    if st.session_state.get("_last_rendered_ticker") != ticker:
        scroll_to_top()
        st.session_state._last_rendered_ticker = ticker

    if st.button("← Volver a la lista"):
        st.session_state.selected_ticker = None
        st.session_state._last_rendered_ticker = None
        st.rerun()

    # el proveedor elegido en la lista no persiste al entrar al detalle directo,
    # así que usamos yfinance por defecto (funciona sin API key propia)
    provider = st.session_state.get("last_provider", "yfinance")

    try:
        evaluation = _cached_evaluation(ticker, provider)
    except DataError:
        st.error(
            f"No pudimos obtener información de **{ticker}** ahora mismo. "
            "Probá de nuevo en unos minutos."
        )
        return
    except ValueError:
        st.warning(f"No hay suficiente historial de **{ticker}** para estimar un precio justo todavía.")
        return

    summary = summarize_signals(evaluation)
    _maybe_record_verdict(ticker, summary, evaluation.current_price)

    st.title(ticker)
    render_sticky_price("acciones", "Precio actual", evaluation.current_price, ticker)
    st.markdown(triangulation_badge(summary), unsafe_allow_html=True)

    note = quality_context_note(evaluation, summary)
    if note:
        st.info(f"💡 {note}")

    as_of = format_as_of(evaluation.data_as_of)
    if evaluation.market_closed:
        st.caption(f"ℹ️ Mercado cerrado — mostrando el último cierre, del {as_of}.")
    elif evaluation.is_stale:
        st.warning(
            "⚠️ No pudimos actualizar los datos ahora mismo (la fuente de datos alcanzó su límite). "
            f"Te mostramos la última información que guardamos, del {as_of}."
        )
    else:
        st.caption(f"Información actualizada el {as_of}")

    with st.expander("🔍 ¿FMP y yfinance opinan lo mismo de esta acción?"):
        st.caption(
            "Corre la misma evaluación con la otra fuente de datos. Si el veredicto cambia, "
            "es una señal de que depende más del origen del dato que de los fundamentales reales."
        )
        if st.button("Comparar fuentes ahora"):
            with st.spinner("Consultando ambas fuentes..."):
                comparison = compare_providers(ticker)
            for name, err in comparison["errors"].items():
                st.caption(f"⚠️ {PROVIDER_LABELS[name]} no disponible ahora mismo: {err}")
            if len(comparison["summaries"]) < 2:
                st.info("Solo una fuente respondió — no hay con qué comparar en este momento.")
            elif comparison["agree"]:
                st.success("✅ Ambas fuentes coinciden en el veredicto general.")
                for name, s in comparison["summaries"].items():
                    st.write(f"**{PROVIDER_LABELS[name]}**: {s['headline']}")
            else:
                st.warning("⚠️ Las fuentes NO coinciden — tomá el veredicto con más cautela.")
                for name, s in comparison["summaries"].items():
                    st.write(f"**{PROVIDER_LABELS[name]}**: {s['headline']}")

    st.divider()
    st.subheader("🏆 ¿Esta empresa crea o destruye valor?")
    st.caption("Mide si el negocio es bueno, no si el precio es bueno.")
    if evaluation.quality is not None and evaluation.quality.creates_value:
        st.success(explain_quality(evaluation))
    elif evaluation.quality is not None:
        st.warning(explain_quality(evaluation))
    else:
        st.caption(explain_quality(evaluation))
    if evaluation.quality is not None and evaluation.quality.roic_trend != "Sin suficiente historia":
        trend = evaluation.quality.roic_trend
        icon = {"Mejorando": "📈", "Deteriorándose": "📉", "Estable": "➡️"}.get(trend, "")
        st.caption(f"{icon} Tendencia de los últimos años: **{trend}** (no solo el nivel de hoy, sino hacia dónde va).")

    st.divider()
    st.subheader("🛡️ ¿Puede pagar su deuda?")
    st.caption("Mide riesgo de deuda, no precio.")
    sv = evaluation.solvency
    if sv is None or sv.risk_level == "No aplica":
        st.caption("No pudimos calcular este filtro con los datos disponibles.")
    else:
        s1, s2 = st.columns(2)
        if sv.interest_coverage is not None:
            s1.metric("Cobertura de intereses", f"{sv.interest_coverage:.1f}x")
        if sv.debt_to_ebitda is not None:
            s2.metric("Deuda / EBITDA", f"{sv.debt_to_ebitda:.1f}x")
        risk_msg = (
            f"Riesgo de apalancamiento: **{sv.risk_level}**. "
            + {
                "Bajo": "La deuda no parece un problema para pagar en el corto/mediano plazo.",
                "Moderado": "Vale la pena vigilar la deuda, aunque no es una alerta roja todavía.",
                "Alto": "La deuda es alta relativa a sus ganancias — un riesgo real más allá del precio.",
            }.get(sv.risk_level, "")
        )
        if sv.risk_level == "Alto":
            st.warning(risk_msg)
        elif sv.risk_level == "Moderado":
            st.info(risk_msg)
        else:
            st.caption(risk_msg)

    st.divider()
    st.subheader("📉 Tendencia (EMA 55 / SMA 50-200)")
    tr = evaluation.trend
    if tr is None:
        st.caption("No hay suficiente historial de precios para calcular estos indicadores.")
    else:
        state = classify_trend_state(tr)
        st.markdown(trend_state_badge(state), unsafe_allow_html=True)
        st.caption(TREND_STATE_TAKEAWAY[state])

        st.metric("Precio vs. EMA de 55 días", f"${tr.ema:,.2f}", f"{tr.price_vs_ema:+.1%}", help=EMA_HELP)
        sma_col1, sma_col2 = st.columns(2)
        if tr.sma_50 is not None:
            sma_col1.metric(
                "Precio vs. SMA de 50 días", f"${tr.sma_50:,.2f}", f"{tr.price_vs_sma_50:+.1%}", help=SMA_HELP
            )
        if tr.sma_200 is not None:
            sma_col2.metric(
                "Precio vs. SMA de 200 días", f"${tr.sma_200:,.2f}", f"{tr.price_vs_sma_200:+.1%}", help=SMA_HELP
            )

        trend_note = trend_context_note(evaluation, summary)
        if trend_note:
            st.info(f"💡 {trend_note}")

    st.divider()
    st.subheader("📊 Riesgo y retorno histórico")
    st.caption("Calculado sobre los últimos 5 años de precios disponibles — 100% pasado, no proyecta nada.")
    rr = evaluation.risk_return
    rr1, rr2, rr3 = st.columns(3)
    if rr.cagr_1y is not None:
        rr1.metric("Retorno anualizado (1 año)", f"{rr.cagr_1y:+.1%}")
    if rr.cagr_3y is not None:
        rr2.metric("Retorno anualizado (3 años)", f"{rr.cagr_3y:+.1%}")
    if rr.cagr_5y is not None:
        rr3.metric("Retorno anualizado (5 años)", f"{rr.cagr_5y:+.1%}")
    rr4, rr5, rr6 = st.columns(3)
    if rr.annualized_volatility is not None:
        rr4.metric("Volatilidad anualizada", f"{rr.annualized_volatility:.1%}")
    if rr.sharpe_ratio is not None:
        rr5.metric("Sharpe ratio", f"{rr.sharpe_ratio:.2f}")
    if rr.max_drawdown is not None:
        rr6.metric("Máxima caída (5 años)", f"{rr.max_drawdown:.1%}")
    if all(
        v is None
        for v in [rr.cagr_1y, rr.cagr_3y, rr.cagr_5y, rr.annualized_volatility, rr.sharpe_ratio, rr.max_drawdown]
    ):
        st.caption("No hay suficiente historial de precios para calcular estos indicadores.")

    st.divider()
    st.subheader("👀 ¿Qué opina Wall Street?")
    st.caption("No es un método nuestro — es el consenso de analistas, para comparar contra nuestras señales.")
    av = evaluation.analyst_view
    if av is None:
        st.caption("No disponible con la fuente de datos actual (probá con yfinance).")
    else:
        a1, a2 = st.columns(2)
        if av.price_target_mean:
            target_margin = (av.price_target_mean - evaluation.current_price) / evaluation.current_price
            a1.metric("Precio objetivo promedio (analistas)", f"${av.price_target_mean:,.2f}", f"{target_margin:+.1%}")
        if av.estimate_revision_direction:
            a2.metric("Tendencia de revisión de estimados (90 días)", av.estimate_revision_direction.capitalize())
        if av.forward_growth_rate is not None:
            st.caption(f"Crecimiento de ganancias esperado por el mercado (próx. 12 meses): {av.forward_growth_rate:.0%}")
        st.write("")
        st.write(explain_analyst_view(evaluation))
        if av.estimate_revision_direction == "al alza":
            st.caption(
                "⚠️ Analistas al alza — justo lo que nuestros métodos (basados en historia) no "
                "capturan; un 'cara' nuestro puede convivir con más subida."
            )

    st.divider()
    st.subheader("¿Está barata o cara esta acción?")
    st.caption("6 formas de estimarlo, agrupadas en las 3 familias del resumen de arriba. Pueden no coincidir.")
    lc = evaluation.lynch_category
    if lc.growth_methods_appropriate:
        st.caption(f"📎 Categoría (heurística de Lynch): **{lc.label}**. {lc.note}")
    else:
        st.warning(f"📎 Categoría (heurística de Lynch): **{lc.label}**. {lc.note}")

    methods = [
        {
            "title": "🔮 Según sus ganancias futuras",
            "metric_label": "Valor esperado (pesimista/base/optimista)",
            "value": f"${evaluation.dcf.fair_value_per_share:,.2f}",
            "margin": evaluation.dcf_margin,
            "extra_caption": (
                f"Rango: ${evaluation.dcf.pessimistic.fair_value_per_share:,.2f} — "
                f"${evaluation.dcf.optimistic.fair_value_per_share:,.2f}"
            ),
            "zone": evaluation.dcf_zone,
            "explain": explain_dcf(evaluation),
        },
        {
            "title": "📊 Comparado con su propio historial",
            "metric_label": "Precio 'normal' según su historia",
            "value": f"${evaluation.multiple.fair_value:,.2f}",
            "margin": evaluation.multiple_margin,
            "zone": evaluation.multiple_zone,
            "explain": explain_multiple(evaluation),
            "context_note": multiple_quality_context_note(evaluation),
        },
        {
            "title": "🏦 Valor patrimonial",
            "metric_label": "Valor si vendiera todo hoy",
            "value": f"${evaluation.book_value.book_value_per_share:,.2f}",
            "margin": evaluation.book_value_margin,
            "zone": evaluation.book_value_zone,
            "explain": explain_book_value(evaluation),
        },
        {
            "title": "🚀 Crecimiento (PEG)",
            "metric_label": "Precio 'justo' según su crecimiento",
            "value": f"${evaluation.growth.fair_value:,.2f}" if evaluation.growth is not None else None,
            "margin": evaluation.growth_margin if evaluation.growth is not None else None,
            "zone": evaluation.growth_zone if evaluation.growth is not None else None,
            "explain": explain_growth(evaluation),
        },
        {
            "title": "📐 Número de Graham",
            "metric_label": "Precio 'justo' conservador",
            "value": f"${evaluation.graham.fair_value:,.2f}" if evaluation.graham is not None else None,
            "margin": evaluation.graham_margin if evaluation.graham is not None else None,
            "zone": evaluation.graham_zone if evaluation.graham is not None else None,
            "explain": explain_graham(evaluation),
        },
        {
            "title": "📈 Fórmula de crecimiento de Graham",
            "metric_label": "Precio 'justo' según crecimiento + tasas",
            "value": f"${evaluation.graham_growth.fair_value:,.2f}" if evaluation.graham_growth is not None else None,
            "margin": evaluation.graham_growth_margin if evaluation.graham_growth is not None else None,
            "zone": evaluation.graham_growth_zone if evaluation.graham_growth is not None else None,
            "explain": explain_graham_growth(evaluation),
        },
    ]

    positive_methods = [m for m in methods if m["margin"] is not None and m["margin"] >= 0]
    negative_methods = [m for m in methods if m["margin"] is not None and m["margin"] < 0]
    not_applicable_methods = [m for m in methods if m["margin"] is None]

    if positive_methods:
        st.markdown("##### ✅ La ven barata")
        render_method_grid(positive_methods)
    if negative_methods:
        st.markdown("##### ⚠️ La ven cara")
        render_method_grid(negative_methods)
    if not_applicable_methods:
        st.markdown("##### ➖ No aplican para esta empresa")
        render_method_grid(not_applicable_methods)

    st.divider()

    with st.expander("🔧 Ver el detalle técnico"):
        d = evaluation.dcf
        st.markdown("**Escenarios del DCF** (pesos 20% / 50% / 30%)")
        st.write(
            {
                "Pesimista": f"${d.pessimistic.fair_value_per_share:,.2f} (crecimiento {d.pessimistic.growth_rate_used:.2%}, WACC {d.pessimistic.wacc_used:.2%})",
                "Base": f"${d.base.fair_value_per_share:,.2f} (crecimiento {d.base.growth_rate_used:.2%}, WACC {d.base.wacc_used:.2%})",
                "Optimista": f"${d.optimistic.fair_value_per_share:,.2f} (crecimiento {d.optimistic.growth_rate_used:.2%}, WACC {d.optimistic.wacc_used:.2%})",
                "Valor esperado": f"${d.fair_value_per_share:,.2f}",
            }
        )

        sens = evaluation.dcf_sensitivity
        st.markdown(f"**Sensibilidad del DCF** — lo que más mueve el resultado es **{sens.dominant_driver}**")
        st.write(
            {
                "Rango moviendo solo el crecimiento (WACC fijo)": (
                    f"${sens.fv_growth_low:,.2f} — ${sens.fv_growth_high:,.2f} "
                    f"(swing de ${sens.growth_swing:,.2f})"
                ),
                "Rango moviendo solo el WACC (crecimiento fijo)": (
                    f"${sens.fv_wacc_high:,.2f} — ${sens.fv_wacc_low:,.2f} "
                    f"(swing de ${sens.wacc_swing:,.2f})"
                ),
            }
        )

        if evaluation.avg_reported_fcf is not None:
            st.markdown("**FCF reportado vs. ajustado por dilución (stock-based compensation)**")
            sbc_details = {"FCF reportado (promedio 3 años)": f"${evaluation.avg_reported_fcf:,.0f}"}
            if evaluation.sbc_adjusted_fcf is not None:
                sbc_details["Stock-based compensation (promedio 3 años)"] = f"${evaluation.avg_stock_comp:,.0f}"
                sbc_details["FCF ajustado por dilución"] = f"${evaluation.sbc_adjusted_fcf:,.0f}"
            st.write(sbc_details)
            st.caption(
                "El FCF ajustado NO alimenta el DCF de arriba — es un dato aparte, más estricto, "
                "para que veas cuánto de ese flujo de caja 'reportado' en realidad se diluye entre más "
                "acciones con el tiempo."
            )

        details = {
            "Tasa de descuento (WACC) base usada": f"{evaluation.dcf.wacc_used:.2%}",
            "Crecimiento de flujo de caja base usado": f"{evaluation.dcf.growth_rate_used:.2%}",
            "P/E actual": f"{evaluation.multiple.current_pe:.1f}",
            "P/E histórico promedio": f"{evaluation.multiple.mean_pe:.1f}",
            "P/E histórico (rango típico)": f"{evaluation.multiple.p25_pe:.1f} - {evaluation.multiple.p75_pe:.1f}",
            "Patrimonio total": f"${evaluation.book_value.total_equity:,.0f}",
        }
        if evaluation.growth is not None:
            details["Crecimiento de EPS usado (PEG)"] = f"{evaluation.growth.eps_growth_rate:.2%}"
            details["Dividend yield usado (PEGY)"] = f"{evaluation.growth.dividend_yield:.2%}"
            details["PEG/PEGY ratio"] = f"{evaluation.growth.peg_ratio:.2f}"
        if evaluation.graham_growth is not None:
            details["Múltiplo implícito (Graham 8.5+2g)"] = f"{evaluation.graham_growth.implied_multiple:.1f}x"
            details["Crecimiento usado (Graham 8.5+2g)"] = f"{evaluation.graham_growth.growth_rate:.2%}"
        if evaluation.quality is not None:
            details["ROIC"] = f"{evaluation.quality.roic:.2%}"
            details["WACC (para ROIC)"] = f"{evaluation.quality.wacc:.2%}"
        st.write(details)
