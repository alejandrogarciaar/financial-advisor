"""Helpers y estado compartido entre 2+ pestañas — extraído de app.py (que llegó a 2821 líneas)
para modularizar y acotar cuánto hay que leer al trabajar en una sola pestaña. Todo lo de acá
es genuinamente cross-tab (confirmado por uso real, no por intuición, antes de mover nada):
badges/colores usados por Acciones+ETFs+Portafolio, el mecanismo de dedup de fetches entre
Acciones/ETFs/Portafolio (`STOCK_EVAL_CACHE_KEY`/`ETF_EVAL_CACHE_KEY`), `classify_trend_state`
(Acciones y Especulación), `render_sticky_price`/`scroll_to_top` (Acciones/ETFs/Especulación/
Cripto), y `_cached_historical_prices` (Especulación y Portafolio, vía el contexto de
valoración de drawdown)."""

import bisect
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from src.config import ETF_TICKERS, PORTFOLIO_CDI_TICKERS, RISK_FREE_RATE
from src.crecetrader import Level, LevelEngine, Role, nearest_levels, session_envelope
from src.crecetrader_inputs import infer_inputs
from src.data import fear_greed_client
from src.valuation.etf_analysis import evaluate_etf
from src.valuation.fair_value import PROVIDERS, evaluate_ticker

# Bandas convencionales del Índice de Miedo y Codicia (0-100) — el rojo->verde es la paleta que
# este índice específico usa en todas partes donde se lo ve (CNN, alternative.me, cualquier
# widget de cripto), deliberadamente distinta de la paleta diverging azul/rojo default de este
# proyecto — para ESTE índice puntual el rojo/verde ya es la convención reconocida. Vive acá (no
# en cripto.py) porque tanto `render_fear_greed_index()` (Cripto, el gauge) como el "📋 Plan de
# DCA sugerido" (Especulación/speculation.py, el cruce con el régimen) lo necesitan — 2
# llamadores reales, mismo criterio de extracción que el resto de este archivo.
FEAR_GREED_BANDS = [
    (0, 25, "#d32f2f"),
    (25, 45, "#f57c00"),
    (45, 55, "#fbc02d"),
    (55, 75, "#7cb342"),
    (75, 100, "#388e3c"),
]

FEAR_GREED_LABEL_ES = {
    "Extreme Fear": "Miedo extremo",
    "Fear": "Miedo",
    "Neutral": "Neutral",
    "Greed": "Codicia",
    "Extreme Greed": "Codicia extrema",
}


@st.cache_data(ttl=3600, show_spinner=False)
def _cached_fear_greed_index():
    # TTL de 1h, no los 900s de precio — alternative.me actualiza este índice una vez por día,
    # no tiene sentido re-pedirlo tan seguido como el precio.
    return fear_greed_client.get_fear_greed_index()


def fear_greed_badge(color: str, label: str) -> str:
    # Mismo patrón HTML que zone_badge()/quality_badge() más abajo — no una nueva convención
    # visual para este índice.
    return (
        f'<span style="background:{color}22;color:{color};border:1px solid {color};'
        f'padding:3px 12px;border-radius:12px;font-size:0.85rem;font-weight:600;'
        f'white-space:nowrap;">{label}</span>'
    )

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


def _get_or_fetch(cache_key: str, jobs: dict, ttl_seconds: float = 900) -> dict:
    """Como `_parallel_fetch`, pero primero revisa `st.session_state[cache_key]` — la única
    fuente de verdad de lo ya resuelto en esta sesión — y solo arma jobs nuevos para las keys
    que todavía no están ahí o cuya entrada ya venció. Si Acciones ya evaluó GOOGL hace menos
    de `ttl_seconds` y Portafolio también lo necesita, Portafolio lo reusa en vez de volver a
    pedirlo. `ttl_seconds` por defecto coincide con el `ttl=900` de `_cached_evaluation`/
    `_cached_etf_evaluation` — sin esto, una entrada guardada acá nunca vencía y quedaba
    congelada por el resto de la sesión del navegador aunque el cache de abajo ya tuviera datos
    más frescos disponibles. Solo cachea resultados exitosos: un error no se recuerda, para que
    la próxima vez que se necesite ese ticker se reintente."""
    session_cache = st.session_state.setdefault(cache_key, {})
    now = datetime.now()

    def _is_fresh(key: str) -> bool:
        _, fetched_at = session_cache[key]
        return (now - fetched_at).total_seconds() < ttl_seconds

    already_have = {
        key: (session_cache[key][0], None) for key in jobs if key in session_cache and _is_fresh(key)
    }
    missing_jobs = {key: spec for key, spec in jobs.items() if key not in already_have}

    fetched = _parallel_fetch(missing_jobs)
    for key, (result, error) in fetched.items():
        if error is None:
            session_cache[key] = (result, now)

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


# A partir de acá: helpers del Market Reaction Zone Engine (src/support_resistance.py) genuinamente
# cross-tab — usados por Cripto (src/ui/cripto.py) y Especulación (src/ui/speculation.py, motor
# sobre referencia diaria vía daily_reference_config()). Vivían solo en cripto.py hasta que
# Especulación necesitó exactamente lo mismo; mismo criterio que el resto de este archivo
# ("confirmado por uso real, no por intuición, antes de mover nada").

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


def render_advanced_levels_chart(
    historical_prices: list[dict], reference_prices: list[dict], levels: list, ticker: str, window_days: int = 365
) -> None:
    """`historical_prices` (diaria) maneja el eje X del gráfico — se ve mejor con una barra por
    día que por 4h/lo que sea la referencia. `reference_prices` (la serie que realmente vio
    `detect_levels()` — 4h para Cripto, la misma diaria para Especulación) es necesaria aparte
    porque `lv.value_at(bar_index)` espera un índice en ESA serie (ver `SRLevel.value_at` en
    support_resistance.py) — cada fecha diaria de la ventana visible se convierte a su barra de
    referencia más cercana antes de evaluar la línea. Para Especulación, `historical_prices` y
    `reference_prices` son literalmente el mismo array — la conversión es una identidad trivial
    en ese caso, sin código especial."""
    dated = sorted(historical_prices, key=lambda p: p["date"])
    if not dated:
        return
    cutoff = datetime.strptime(dated[-1]["date"], "%Y-%m-%d") - timedelta(days=window_days)
    window = [p for p in dated if datetime.strptime(p["date"], "%Y-%m-%d") >= cutoff]
    if len(window) < 2:
        return
    reference_dates = [p["date"] for p in sorted(reference_prices, key=lambda p: p["date"])]

    def _nearest_reference_index(target_date: str) -> int:
        pos = bisect.bisect_right(reference_dates, target_date) - 1
        return max(pos, 0)

    bar_indices = [_nearest_reference_index(p["date"]) for p in window]
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
        line_y = [lv.value_at(i) for i in bar_indices]
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
            line_y = [side_lv.value_at(i) for i in bar_indices]
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


def render_crecetrader(
    key_prefix: str,
    ticker: str,
    historical_prices: list[dict],
    current_price: float,
    *,
    is_crypto: bool,
) -> None:
    """Sección "📐 Niveles Crecetrader" — pestaña interna de cada cripto Y de cada acción.

    Reproduce las 3 capas de `src/crecetrader.py` (envolvente de sesión, rejilla diaria anclada
    al mínimo anual, fracciones de 1/8 de la caída macro) sobre la serie diaria que la pestaña
    llamadora ya tiene en mano: Binance para Cripto (`key_prefix="crypto"`), yfinance para
    Especulación (`key_prefix="speculation"`). Las entradas del motor se derivan de esa serie con
    `src/crecetrader_inputs.py` y se pueden sobrescribir a mano — dónde termina el "primer
    impulso" es justamente la parte que el método original traza a ojo.

    `key_prefix` existe solo para que las keys de los widgets no choquen entre las dos pestañas
    (`st.tabs()` no es lazy: los dos cuerpos se ejecutan en cada rerun — mismo motivo y mismo
    patrón que `render_speculation_indicators()`). `is_crypto` cambia únicamente los avisos: el
    método fue calibrado sobre BTC/ETH, así que para acciones hay que decirlo.

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
    if not is_crypto:
        st.caption(
            "**Calibrado sobre BTC y ETH, no sobre acciones.** Las tres capas son fórmulas "
            "genéricas y corren igual con cualquier serie diaria, pero dos cosas cambian de "
            "significado acá: (1) los anillos de ±0.382/1/1.5/2% salen de la volatilidad "
            "intradía de una cripto — en una acción típica ±2% cubre casi todo el rango del día, "
            "así que la envolvente queda más ancha en términos relativos; (2) la «apertura "
            "diaria» de una acción llega después de 17 horas de mercado cerrado (gap overnight), "
            "mientras que en cripto es un corte arbitrario de un mercado que nunca cerró. El "
            "canal aplica esto a cripto; replicarlo acá es una extensión, no algo que ellos "
            "hayan verificado."
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
            key=f"{key_prefix}_crece_retracement_pct",
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
            key=f"{key_prefix}_crece_min_reversal_pct",
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
            key=f"{key_prefix}_crece_manual_inputs",
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
        key=f"{key_prefix}_crece_layer",
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
            key=f"{key_prefix}_crece_envelope_center",
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
        # Caso real, frecuente en acciones y casi inexistente en cripto: si el techo de ciclo ES
        # el techo del primer impulso (el activo está en máximos de la ventana de 5 años, p. ej.
        # AAPL hoy), esta capa reproduce exactamente la rejilla diaria escalada y no aporta
        # información nueva. Se dice, en vez de mostrar dos capas que parecen independientes.
        if abs(macro_range - base_range) / macro_range < 0.01:
            st.caption(
                "⚠️ Para este activo el techo de ciclo coincide con el techo del primer impulso "
                "(está en máximos de la historia disponible), así que esta capa es la rejilla "
                "diaria reescalada, no una lectura independiente."
            )
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
