"""Punto de entrada de "Precio Justo — Acciones Americanas". Antes este archivo tenía las 2821
líneas de las 6 pestañas juntas; se modularizó a `src/ui/` (un archivo por pestaña + `shared.py`
para lo genuinamente cross-tab) para que trabajar en una sola pestaña no implique cargar/leer
todo el resto. Acá solo queda: page config, inicialización de session_state, y el wiring de las
6 pestañas — la lógica de cada una vive en su propio módulo."""

import streamlit as st

from src.ui.cripto import render_crypto
from src.ui.etfs import render_etf_detail, render_etf_list
from src.ui.portfolio import render_capital
from src.ui.speculation import render_speculation
from src.ui.stocks import render_detail, render_list
from src.ui.validation import render_validation

st.set_page_config(page_title="Precio Justo — Acciones Americanas", layout="wide")

if "selected_ticker" not in st.session_state:
    st.session_state.selected_ticker = None
if "selected_etf" not in st.session_state:
    st.session_state.selected_etf = None

# Orden pedido por el usuario: Acciones, Validación, ETFs, Especulación, Cripto, Portafolio
# SIEMPRE al final. El orden de esta tupla/lista fija tanto el orden visual de las pestañas como
# el orden de ejecución de los bloques `with` de más abajo (st.tabs() no es lazy — ver nota en
# CLAUDE.md), así que Portafolio último acá también preserva que corra después de Acciones y
# ETFs, de donde reusa evaluaciones ya resueltas vía STOCK_EVAL_CACHE_KEY/ETF_EVAL_CACHE_KEY
# (src/ui/shared.py). Cripto (antes "Niveles" — indicadores de especulación + el motor
# multi-metodología de soportes/resistencias para BTC/ETH/SOL, todo sobre datos de Binance) va
# justo después de Especulación (que quedó solo-acciones) y antes de Portafolio porque, como
# Especulación, no hace ningún fetch eager propio (un solo ticker, botón-gated para el motor de
# niveles), así que no retrasa a Portafolio corriendo en el medio.
tab_acciones, tab_validacion, tab_etfs, tab_especulacion, tab_cripto, tab_capital = st.tabs(
    ["📈 Acciones", "📊 Validación", "🧺 ETFs", "🎲 Especulación", "🪙 Cripto", "💰 Portafolio"]
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

with tab_validacion:
    render_validation()

with tab_etfs:
    etf_slot = st.empty()
    with etf_slot.container():
        if st.session_state.selected_etf is None:
            with st.spinner("Cargando ETFs..."):
                render_etf_list()
        else:
            with st.spinner("Cargando..."):
                render_etf_detail(st.session_state.selected_etf)

with tab_especulacion:
    with st.spinner("Cargando..."):
        render_speculation()

with tab_cripto:
    with st.spinner("Cargando..."):
        render_crypto()

with tab_capital:
    with st.spinner("Cargando portafolio..."):
        render_capital()
