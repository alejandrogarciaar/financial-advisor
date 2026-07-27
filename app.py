from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from src.config import ETF_TICKERS, PORTFOLIO_CDI_TICKERS, PORTFOLIO_CDI_UNDERLYING, RISK_FREE_RATE, SPECULATION_CRYPTO_TICKERS, TICKERS
from src.data.errors import DataError
from src.portfolio import (
    DEFAULT_COMMISSION_COP,
    commission_summary,
    load_purchases,
    save_purchases,
    simulate_additional_purchase,
    summarize_by_ticker,
    validate_purchases,
)
from src.preferences import load_selected_tickers, save_selected_tickers
from src.speculation import (
    compute_bollinger_bands,
    compute_macd,
    compute_regime_reactions,
    compute_regime_rsi_reactions,
    compute_resistance_levels,
    compute_rsi,
    compute_support_levels,
)
from src.valuation.etf_analysis import REFERENCE_PE, evaluate_etf
from src.valuation.trend import evaluate_trend
from src.valuation.fair_value import (
    PROVIDERS,
    compare_providers,
    evaluate_ticker,
    multiple_quality_context_note,
    quality_context_note,
    summarize_signals,
    trend_context_note,
)

PROVIDER_LABELS = {"fmp": "Financial Modeling Prep", "yfinance": "yfinance"}

# El Portafolio solo acepta los CDIs colombianos (GOOGLCO, ...), no las acciones en USD de
# TICKERS — las compras reales del usuario se hacen en pesos vía estos CDIs. No participan
# de las 6 fórmulas de valoración (ver comentario en config.py sobre por qué).
PORTFOLIO_TICKERS = list(PORTFOLIO_CDI_TICKERS.keys())

# colores de estado reservados: verde=atractivo, ámbar=razonable, rojo=caro
ZONE_COLOR = {
    "Acumulación fuerte": "#1E8E3E",
    "Acumulación": "#1E8E3E",
    "Precio justo": "#B8860B",
    "Sobrevalorado": "#D93025",
}
VERDICT_COLOR = {"cheap": ZONE_COLOR["Acumulación"], "expensive": ZONE_COLOR["Sobrevalorado"], "mixed": ZONE_COLOR["Precio justo"]}

# los mismos nombres internos de zona (usados por la lógica de valoración),
# traducidos a lenguaje simple para quien no maneja los tecnicismos.
# Lenguaje descriptivo, no de acción ("atractivo"/"cómpralo") — el backtest no respalda
# que esto tenga poder de timing, así que evitamos sugerir una recomendación de compra.
FRIENDLY_ZONE = {
    "Acumulación fuerte": "Muy por debajo de su valor histórico",
    "Acumulación": "Por debajo de su valor histórico",
    "Precio justo": "En línea con su valor histórico",
    "Sobrevalorado": "Por encima de su valor histórico",
}

st.set_page_config(page_title="Precio Justo — Acciones Americanas", layout="wide")

if "selected_ticker" not in st.session_state:
    st.session_state.selected_ticker = None
if "selected_etf" not in st.session_state:
    st.session_state.selected_etf = None


def scroll_to_top() -> None:
    """Streamlit corre adentro de un iframe, así que un <script> en st.markdown no alcanza
    (se sanitiza) — hace falta st.iframe, que sí ejecuta JS, apuntando a window.parent para
    llegar al documento de verdad. Prueba varios contenedores posibles a propósito: el nombre
    exacto del div scrolleable cambió entre versiones de Streamlit, y llamar .scrollTo() sobre
    algo que no existe o no scrollea es inofensivo."""
    st.iframe(
        """
        <script>
        (function () {
            var doc = window.parent.document;
            var candidates = [
                doc.querySelector('[data-testid="stAppViewContainer"]'),
                doc.querySelector('[data-testid="stMain"]'),
                doc.querySelector('section.main'),
                doc.documentElement,
                doc.body,
            ];
            candidates.forEach(function (el) {
                if (el) {
                    el.scrollTo(0, 0);
                    el.scrollTop = 0;
                }
            });
            window.parent.scrollTo(0, 0);
        })();
        </script>
        """,
        height=1,  # st.iframe exige un entero positivo — 0 no es válido, a diferencia de components.html
    )


def render_sticky_price(key_prefix: str, label: str, price: float, nonce_id: str) -> None:
    """Precio normal en el punto de la página donde se llama + un clon flotante que arranca
    OCULTO y solo aparece cuando ese primero sale de la vista al hacer scroll (y se vuelve a
    ocultar si volvés a subir) — lo detecta el IntersectionObserver de más abajo, no hay forma
    de hacerlo con CSS solo. position: fixed en vez de sticky porque sticky depende de que TODA
    la cadena de contenedores padre tenga overflow visible/scroll, y algún div interno de
    Streamlit casi seguro tiene overflow:hidden en algún punto — fixed ancla directo al
    viewport, sin esa dependencia. Los !important y el ancho explícito son porque el CSS propio
    de Streamlit para ese contenedor (pensado para una columna de ancho completo) le ganaba en
    especificidad al nuestro — así se veía estirado de borde a borde y entrecortado.

    key_prefix debe ser único por cada llamada activa en el mismo rerun (varias pestañas de
    Streamlit corren en el mismo script run — ver nota sobre st.tabs() en CLAUDE.md — así que
    dos llamadas con el mismo prefix pisarían el CSS/selector de la otra)."""
    top_key = f"{key_prefix}_top_price"
    sticky_key = f"{key_prefix}_sticky_price"
    st.markdown(
        f"""
        <style>
        .st-key-{sticky_key} {{
            display: none;
            position: fixed !important;
            top: 4.5rem !important;
            right: 1.5rem !important;
            left: auto !important;
            bottom: auto !important;
            width: auto !important;
            min-width: 200px !important;
            max-width: 260px !important;
            z-index: 9999;
            padding: 0.4rem 1rem;
            border-radius: 12px;
            box-shadow: 0 2px 10px rgba(0, 0, 0, 0.25);
        }}
        @media (prefers-color-scheme: light) {{
            .st-key-{sticky_key} {{
                background-color: rgba(255, 255, 255, 0.97);
                border: 1px solid rgba(0, 0, 0, 0.1);
            }}
        }}
        @media (prefers-color-scheme: dark) {{
            .st-key-{sticky_key} {{
                background-color: rgba(14, 17, 23, 0.97);
                border: 1px solid rgba(255, 255, 255, 0.15);
            }}
        }}
        </style>
        """,
        unsafe_allow_html=True,
    )
    with st.container(key=top_key):
        st.metric(label, f"${price:,.2f}")
    with st.container(key=sticky_key):
        st.metric(label, f"${price:,.2f}")

    # nonce en el comentario: fuerza a que este iframe se considere "distinto" en cada rerun
    # (cambio de ticker o de precio), así el navegador lo vuelve a montar y el script corre de
    # nuevo apuntando a los elementos recién dibujados — si el contenido fuera idéntico al de la
    # corrida anterior, Streamlit podría no re-ejecutar el <script> de adentro. El registro de
    # observers vive en window.__stickyPriceObservers, indexado por key_prefix, para que cada
    # instancia (Acciones, Especulación, ...) pueda remontar la suya sin desconectar la de otra.
    st.iframe(
        f"""
        <!-- nonce: {nonce_id}-{price} -->
        <script>
        (function () {{
            var doc = window.parent.document;
            function setup() {{
                var topEl = doc.querySelector('.st-key-{top_key}');
                var stickyEl = doc.querySelector('.st-key-{sticky_key}');
                if (!topEl || !stickyEl) {{
                    return false;
                }}
                window.__stickyPriceObservers = window.__stickyPriceObservers || {{}};
                if (window.__stickyPriceObservers['{key_prefix}']) {{
                    window.__stickyPriceObservers['{key_prefix}'].disconnect();
                }}
                var observer = new IntersectionObserver(function (entries) {{
                    entries.forEach(function (entry) {{
                        stickyEl.style.display = entry.isIntersecting ? 'none' : 'block';
                    }});
                }}, {{ root: null, threshold: 0 }});
                observer.observe(topEl);
                window.__stickyPriceObservers['{key_prefix}'] = observer;
                return true;
            }}
            var attempts = 0;
            var interval = setInterval(function () {{
                attempts += 1;
                if (setup() || attempts > 20) {{
                    clearInterval(interval);
                }}
            }}, 200);
        }})();
        </script>
        """,
        height=1,
    )


@st.cache_data(ttl=900, show_spinner=False)
def _cached_evaluation(ticker: str, provider: str):
    return evaluate_ticker(ticker, provider=provider)


@st.cache_data(ttl=900, show_spinner=False)
def _cached_etf_evaluation(display_ticker: str):
    return evaluate_etf(display_ticker, ETF_TICKERS[display_ticker], RISK_FREE_RATE)


@st.cache_data(ttl=900, show_spinner=False)
def _cached_portfolio_price(ticker: str) -> float | None:
    """Precio actual en COP de un ticker del Portafolio. Los CDIs colombianos (GOOGLCO, ...)
    cotizan nativos en pesos en la BVC, así que no hace falta ninguna TRM acá."""
    quote, _ = PROVIDERS["yfinance"].get_quote(PORTFOLIO_CDI_TICKERS[ticker])
    return quote["price"]


# Techo de hilos simultáneos — un límite de seguridad de la máquina, no una suposición sobre
# cuántos tickers existen (eso vive únicamente en TICKERS/PORTFOLIO_CDI_TICKERS en config.py).
MAX_PARALLEL_WORKERS = 16

# Claves de st.session_state donde se guardan, por esta corrida del script, las evaluaciones ya
# resueltas — única fuente de verdad de "qué ya trajimos" que comparten Acciones/ETFs/Portafolio,
# para que ninguna pestaña vuelva a pedir algo que otra ya consultó en este mismo run.
STOCK_EVAL_CACHE_KEY = "_run_stock_evaluations"
ETF_EVAL_CACHE_KEY = "_run_etf_evaluations"


def _parallel_fetch(jobs: dict) -> dict:
    """jobs: {key: (func, args_tuple)}. Trae todo en paralelo (son llamadas de red, no
    cómputo) y devuelve {key: (resultado, error)} — nunca lanza, cada entrada lleva su propio
    resultado o excepción para que el caller decida qué hacer con cada una. El número de jobs
    lo decide siempre el caller (cuántos tickers tiene filtrados/en cartera en ese momento) —
    acá no se asume ninguna cantidad fija.

    Solo es seguro llamar acá funciones que no usan ningún st.* internamente: los hilos no
    tienen el ScriptRunContext de Streamlit. Por eso todos los _cached_* de este archivo usan
    show_spinner=False — no dependen de ese contexto para nada."""
    if not jobs:
        return {}
    results = {}
    with ThreadPoolExecutor(max_workers=min(MAX_PARALLEL_WORKERS, len(jobs))) as executor:
        future_to_key = {executor.submit(func, *args): key for key, (func, args) in jobs.items()}
        for future, key in future_to_key.items():
            try:
                results[key] = (future.result(), None)
            except Exception as exc:
                results[key] = (None, exc)
    return results


def _get_or_fetch(cache_key: str, jobs: dict) -> dict:
    """Como `_parallel_fetch`, pero primero revisa `st.session_state[cache_key]` — la única
    fuente de verdad de lo ya resuelto en esta corrida — y solo arma jobs nuevos para las keys
    que todavía no están ahí. Si Acciones ya evaluó GOOGL en este run y Portafolio también lo
    necesita, Portafolio lo reusa en vez de volver a pedirlo. Solo cachea resultados exitosos:
    un error no se recuerda, para que la próxima vez que se necesite ese ticker se reintente."""
    session_cache = st.session_state.setdefault(cache_key, {})
    already_have = {key: (session_cache[key], None) for key in jobs if key in session_cache}
    missing_jobs = {key: spec for key, spec in jobs.items() if key not in session_cache}

    fetched = _parallel_fetch(missing_jobs)
    for key, (result, error) in fetched.items():
        if error is None:
            session_cache[key] = result

    return {**already_have, **fetched}


@st.cache_data(ttl=900, show_spinner=False)
def _cached_historical_prices(ticker: str):
    return PROVIDERS["yfinance"].get_historical_prices(ticker)


def zone_badge(zone: str, small: bool = False) -> str:
    color = ZONE_COLOR[zone]
    label = FRIENDLY_ZONE[zone]
    font_size = "0.72rem" if small else "0.85rem"
    padding = "1px 8px" if small else "3px 12px"
    return (
        f'<span style="background:{color}22;color:{color};border:1px solid {color};'
        f'padding:{padding};border-radius:12px;font-size:{font_size};font-weight:600;'
        f'white-space:nowrap;">{label}</span>'
    )


# EMA vs. SMA 50 vs. SMA 200 combinados en un solo estado: mismo color de estado que el resto
# de la app (verde/ámbar/rojo), para no inventar una paleta nueva.
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


def classify_trend_state(tr) -> str:
    above_50 = tr.sma_50 is None or tr.price_vs_sma_50 >= 0
    above_200 = tr.sma_200 is None or tr.price_vs_sma_200 >= 0
    if tr.price_vs_ema >= 0 and above_50 and above_200:
        return "fuerte"
    if tr.price_vs_ema < 0 and not above_50 and not above_200:
        return "debil"
    return "mixta"


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


def triangulation_badge(summary: dict, small: bool = False) -> str:
    color = VERDICT_COLOR[summary["verdict"]]
    font_size = "0.72rem" if small else "0.9rem"
    padding = "1px 8px" if small else "4px 14px"
    return (
        f'<span style="background:{color}22;color:{color};border:1px solid {color};'
        f'padding:{padding};border-radius:12px;font-size:{font_size};font-weight:600;'
        f'white-space:nowrap;">{summary["headline"]}</span>'
    )


def format_as_of(iso_timestamp: str) -> str:
    return datetime.fromisoformat(iso_timestamp).strftime("%Y-%m-%d %H:%M UTC")


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


def render_capital():
    st.title("💰 Portafolio")
    st.caption(
        "Registrá tus compras reales de estas acciones y seguí cuánto llevás invertido en "
        "pesos, a qué precio promedio, y cómo viene la rentabilidad hoy."
    )

    st.divider()
    st.subheader("Tus compras")
    st.caption(
        "Editá cualquier celda para corregir una compra, tocá el **+** para agregar una nueva fila, "
        "o el ícono de papelera para borrarla (te vamos a pedir confirmación antes de borrar nada "
        "de verdad). Las acciones son unidades enteras — no se aceptan compras fraccionarias "
        "(1.2, 2.3, etc.). La comisión viene precargada en "
        f"${DEFAULT_COMMISSION_COP:,.0f} COP para compras nuevas, pero se puede ajustar compra a "
        "compra (no cambia las que ya guardaste)."
    )

    EDITOR_KEY = "purchases_editor"
    saved_purchases = load_purchases()

    edited = st.data_editor(
        saved_purchases,
        num_rows="dynamic",
        use_container_width=True,
        hide_index=True,
        key=EDITOR_KEY,
        column_config={
            "ticker": st.column_config.SelectboxColumn("Ticker", options=PORTFOLIO_TICKERS, required=True),
            "shares": st.column_config.NumberColumn(
                "Acciones", min_value=1, step=1, format="%d", required=True
            ),
            "price_cop": st.column_config.NumberColumn(
                "Precio de compra (COP)", min_value=1, step=1000, format="$%.0f", required=True
            ),
            "commission_cop": st.column_config.NumberColumn(
                "Comisión (COP)",
                min_value=0,
                step=100,
                format="$%.0f",
                default=DEFAULT_COMMISSION_COP,
                required=True,
            ),
            "date": st.column_config.DateColumn(
                "Fecha de compra", format="DD/MM/YYYY", max_value=date.today(), required=True
            ),
        },
    )

    errors = validate_purchases(edited, PORTFOLIO_TICKERS)
    # las filas eliminadas con el ícono de papelera desaparecen del `edited` que devuelve el
    # editor ANTES de que nosotros veamos nada — no hay forma de interceptarlo ahí. Por eso la
    # confirmación funciona al revés: detectamos qué índices de `saved_purchases` (el último
    # estado guardado en disco) ya no están en `edited`, y no llamamos a save_purchases() hasta
    # que el usuario confirme. Si cancela, reseteamos el editor para que la fila "vuelva".
    deleted_rows = (
        saved_purchases.loc[saved_purchases.index.difference(edited.index)] if not errors else pd.DataFrame()
    )

    if errors:
        for err in errors:
            st.error(err)
        st.caption("Corregí las filas marcadas para que se guarden los cambios.")
        purchases = saved_purchases
    elif not deleted_rows.empty:
        st.warning(f"⚠️ Vas a eliminar {len(deleted_rows)} compra(s) — esto no se puede deshacer:")
        st.dataframe(deleted_rows, hide_index=True, use_container_width=True)
        confirm_col, cancel_col = st.columns(2)
        if confirm_col.button("🗑️ Confirmar eliminación", type="primary", use_container_width=True):
            save_purchases(edited)
            del st.session_state[EDITOR_KEY]
            st.rerun()
        if cancel_col.button("Cancelar", use_container_width=True):
            del st.session_state[EDITOR_KEY]
            st.rerun()
        purchases = saved_purchases
    else:
        save_purchases(edited)
        purchases = edited

    st.divider()
    st.subheader("Resumen por acción")

    if purchases.empty:
        st.caption("Todavía no registraste ninguna compra.")
    else:
        held_tickers = sorted(purchases["ticker"].unique())
        price_results = _parallel_fetch({t: (_cached_portfolio_price, (t,)) for t in held_tickers})
        current_prices_cop = {t: result for t, (result, error) in price_results.items()}

        summary = summarize_by_ticker(purchases, current_prices_cop)

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

        total_invested_cop = float(summary["invested_cop"].sum())
        valued_rows = summary["current_value_cop"].dropna()
        total_value_cop = float(valued_rows.sum()) if not valued_rows.empty else None

        render_portfolio_total_hero(total_invested_cop, total_value_cop)

        if len(valued_rows) < len(summary):
            st.caption("⚠️ El precio actual de algún ticker no está disponible ahora — el total no lo incluye.")

        st.divider()
        st.subheader("💸 Costo de comisiones")
        st.caption("Lo que pagaste en comisiones no proyecta nada — es plata real que ya salió de tu bolsillo y le resta a la rentabilidad.")

        commissions = commission_summary(purchases)
        cm1, cm2, cm3 = st.columns(3)
        cm1.metric("Comisiones pagadas (COP)", f"${commissions['total_commission_cop']:,.0f}")
        cm2.metric("Comisión promedio por compra", f"${commissions['avg_commission_cop']:,.0f}")
        if commissions["pct_of_invested"] is not None:
            cm3.metric("% del capital invertido", f"{commissions['pct_of_invested']:.2%}")
        st.caption(f"Sobre {commissions['num_purchases']} compra(s) registrada(s).")

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

    sim = simulate_additional_purchase(purchases, sim_ticker, int(sim_shares), float(sim_price), float(sim_commission))

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


# Paleta categórica validada (identidad, no magnitud — cada línea es una entidad distinta, no
# un punto en una escala), orden fijo blue/orange/aqua/yellow/magenta/green/violet/red: es el
# orden que pasa el chequeo de contraste entre colores ADYACENTES en una leyenda (por eso no se
# reordena a mano). Deliberadamente no son los verdes/rojos de estado (ZONE_COLOR) — esto es
# identidad de serie, no un juicio de bueno/malo. Fondo transparente en el gráfico para que se
# mezcle con el tema de Streamlit (claro u oscuro) sin tener que detectarlo.
# support_daily/resistance_daily quedan fuera de los 8 slots validados (price ocupa 1, los 3
# soportes semanal/mensual/anual ocupan 3, las 3 resistencias ocupan 3 más — el slot 8 ("red")
# ya estaba libre pero solo alcanza para uno de los dos nuevos). Elegidos a ojo (sin el
# validador — este entorno no tiene Node) para que se distingan claramente de "price" (azul) y
# entre sí, ya que son las únicas 3 líneas que comparten pantalla en la vista "Diaria".
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


def render_speculation():
    """A diferencia de toda otra pestaña: acá el lenguaje de timing es a propósito, no hay
    cruce con fundamentales, y no tiene nada que ver con el Portafolio. Solo carga datos del
    ticker elegido (no los 8 de una, como Acciones) — no hay "carga inicial" de todo el
    universo. Usa TICKERS (el mismo universo de Acciones) más SPECULATION_CRYPTO_TICKERS
    (BTC/ETH/SOL) — cripto no tiene estados financieros, así que ninguna de las 6 fórmulas de
    valoración le aplicaría, pero técnicos (RSI, MACD, soportes, etc.) sí funcionan igual sobre
    su historial de precio."""
    st.title("🎲 Especulación")
    st.warning(
        "⚠️ Esta pestaña es distinta a todo el resto del dashboard: son indicadores técnicos "
        "de corto plazo pensados para timing, sin cruzarlos con fundamentales ni con tu "
        "Portafolio. El resto de la app evita a propósito este tipo de señal — acá sí se "
        "permite."
    )

    speculation_tickers = TICKERS + list(SPECULATION_CRYPTO_TICKERS.keys())
    ticker = st.selectbox("Ticker", speculation_tickers, key="speculation_ticker")
    real_ticker = SPECULATION_CRYPTO_TICKERS.get(ticker, ticker)

    try:
        historical_prices, _ = _cached_historical_prices(real_ticker)
    except DataError:
        st.error(f"No pudimos consultar {ticker} ahora mismo.")
        return

    closes = [p["close"] for p in historical_prices]
    if not closes:
        st.caption("No hay historial de precios disponible para este ticker.")
        return
    current_price = closes[-1]

    render_sticky_price("speculation", f"Precio actual — {ticker}", current_price, ticker)

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
        key="speculation_chart_view",
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

    if ticker in SPECULATION_CRYPTO_TICKERS:
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


tab_acciones, tab_etfs, tab_capital, tab_especulacion = st.tabs(
    ["📈 Acciones", "🧺 ETFs", "💰 Portafolio", "🎲 Especulación"]
)

with tab_acciones:
    # st.empty() fuerza a Streamlit a limpiar TODO el contenido anterior de este slot antes de
    # dibujar el nuevo — a diferencia de un st.container() con key, que no garantiza reemplazo
    # completo cuando el layout cambia drásticamente (lista de tarjetas <-> detalle largo). Pero
    # eso solo resuelve el reemplazo INSTANTÁNEO: si lo nuevo tarda unos segundos en llegar (8
    # llamadas de red seguidas en render_list, o la evaluación de un ticker en render_detail),
    # Streamlit no tiene nada nuevo que mostrar todavía y deja ver la pantalla anterior mientras
    # tanto. El st.spinner() de adentro es lo que realmente tapa eso — es el primer elemento
    # nuevo que se manda, así que reemplaza lo viejo de inmediato aunque el resto tarde.
    stock_slot = st.empty()
    with stock_slot.container():
        if st.session_state.selected_ticker is None:
            with st.spinner("Cargando acciones..."):
                render_list()
        else:
            with st.spinner("Cargando..."):
                render_detail(st.session_state.selected_ticker)

with tab_etfs:
    etf_slot = st.empty()
    with etf_slot.container():
        if st.session_state.selected_etf is None:
            with st.spinner("Cargando ETFs..."):
                render_etf_list()
        else:
            with st.spinner("Cargando..."):
                render_etf_detail(st.session_state.selected_etf)

with tab_capital:
    with st.spinner("Cargando portafolio..."):
        render_capital()

with tab_especulacion:
    with st.spinner("Cargando..."):
        render_speculation()
