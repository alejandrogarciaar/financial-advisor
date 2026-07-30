"""Helpers y estado compartido entre 2+ pestañas — extraído de app.py (que llegó a 2821 líneas)
para modularizar y acotar cuánto hay que leer al trabajar en una sola pestaña. Todo lo de acá
es genuinamente cross-tab (confirmado por uso real, no por intuición, antes de mover nada):
badges/colores usados por Acciones+ETFs+Portafolio, el mecanismo de dedup de fetches entre
Acciones/ETFs/Portafolio (`STOCK_EVAL_CACHE_KEY`/`ETF_EVAL_CACHE_KEY`), `classify_trend_state`
(Acciones y Especulación), `render_sticky_price`/`scroll_to_top` (Acciones/ETFs/Especulación/
Cripto), y `_cached_historical_prices` (Especulación y Portafolio, vía el contexto de
valoración de drawdown)."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import streamlit as st

from src.config import ETF_TICKERS, PORTFOLIO_CDI_TICKERS, RISK_FREE_RATE
from src.valuation.etf_analysis import evaluate_etf
from src.valuation.fair_value import PROVIDERS, evaluate_ticker

# colores de estado reservados: verde=atractivo, ámbar=razonable, rojo=caro
ZONE_COLOR = {
    "Acumulación fuerte": "#1E8E3E",
    "Acumulación": "#1E8E3E",
    "Precio justo": "#B8860B",
    "Sobrevalorado": "#D93025",
}

VERDICT_COLOR = {"cheap": ZONE_COLOR["Acumulación"], "expensive": ZONE_COLOR["Sobrevalorado"], "mixed": ZONE_COLOR["Precio justo"]}

VERDICT_LABEL = {"cheap": "Barata", "expensive": "Cara", "mixed": "Mixta"}

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
    cotizan nativos en pesos en la BVC, así que no hace falta ninguna TRM acá. Usado tanto por
    Portafolio como por ETFs (referencia de precio BVC en la lista/detalle de ETFs) — no es
    exclusivo de una sola pestaña, por eso vive acá y no en portfolio.py."""
    quote, _ = PROVIDERS["yfinance"].get_quote(PORTFOLIO_CDI_TICKERS[ticker])
    return quote["price"]


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


def classify_trend_state(tr) -> str:
    above_50 = tr.sma_50 is None or tr.price_vs_sma_50 >= 0
    above_200 = tr.sma_200 is None or tr.price_vs_sma_200 >= 0
    if tr.price_vs_ema >= 0 and above_50 and above_200:
        return "fuerte"
    if tr.price_vs_ema < 0 and not above_50 and not above_200:
        return "debil"
    return "mixta"


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
