# Precio Justo — Acciones Americanas

Dashboard en Streamlit que evalúa si un conjunto fijo de acciones americanas de gran
capitalización está barato, justo o caro en este momento, usando 6 fórmulas de valoración
independientes agrupadas en 3 familias (flujo de caja, valor libro y múltiplos de ganancias).
Todo el texto de la interfaz está en español rioplatense.

Tickers cubiertos (`src/config.py`): AAPL, MSFT, AMZN, META, NVDA, TSLA, UBER, GOOGL.

## Pestañas

Orden visual y de ejecución (`st.tabs()` no es lazy — las 6 corren en cada rerun, en este orden):

- **📈 Acciones**: las 6 fórmulas de valoración, filtros de calidad (ROIC vs. WACC) y
  solvencia, tendencia (EMA-55) y métricas de riesgo/retorno, por ticker.
- **📊 Validación**: no evalúa precio — mide qué tan bien funcionaron las señales que la app ya
  usa (backtest direccional a 1 año + historial de veredictos).
- **🧺 ETFs**: análisis de riesgo/retorno para un conjunto de ETFs (no tienen los 6 métodos de
  acciones — no tienen estados financieros propios).
- **🎲 Especulación**: indicadores técnicos de corto plazo (RSI, soportes/resistencias, MACD,
  Bandas de Bollinger, ADX, OBV) + el Market Reaction Zone Engine, sobre acciones (`TICKERS`) —
  la única sección del proyecto donde se permite lenguaje de timing de mercado (junto con
  Cripto y una excepción puntual en Portafolio).
- **🪙 Cripto**: BTC/ETH/SOL, mismo cuerpo de indicadores que Especulación pero sobre datos de
  Binance (más historia, velas de 4h nativas), más el Market Reaction Zone Engine multi-
  metodología.
- **💰 Portafolio**: seguimiento de compras y ventas propias en pesos colombianos (COP), sobre
  CDIs que trackean los tickers/ETFs de arriba y cotizan en la BVC — rentabilidad no realizada
  (lo que se tiene) y realizada (lo que se vendió, neto de comisión). Siempre corre último (reusa
  evaluaciones ya calculadas por Acciones/ETFs en el mismo run).

## Cómo correrlo

```
pip install -r requirements.txt
streamlit run app.py
```

O, equivalente y con manejo de puerto/health-check/reuso de instancia ya resuelto:
`./scripts/run_app.sh` para arrancar, `./scripts/stop_app.sh` para parar (ver
`.claude/skills/financial-advisor-run-app/`).

Por defecto usa el proveedor de datos `yfinance`, que no requiere API key. Si querés usar
`fmp` en su lugar, copiá `.env.example` a `.env` y completá `FMP_API_KEY`.

No hay suite de tests formal; `scripts/verify_app.py` corre un smoke test de las 6 pestañas vía
`streamlit.testing.v1.AppTest` (sin navegador). No hay herramientas de lint/build configuradas.

### Backtest

Para chequear el veredicto de triangulación contra retornos históricos reales (no forma parte
de la UI):

```
python -c "from src.backtest import run_backtest; print(run_backtest())"
```

## Archivos — qué hace cada uno

Mapa de responsabilidad de cada módulo `.py` "importante" (con lógica real — se excluyen los
`__init__.py` vacíos, que son solo marcadores de paquete). Una línea por archivo a propósito:
el racional completo de cada decisión vive en `CLAUDE.md` (arquitectura transversal) y en
`.claude/skills/*/references/design-history.md` (decisiones específicas de una pestaña) — este
mapa es solo para ubicarse rápido, no para reemplazar esa lectura.

### Raíz

| Archivo | Rol |
|---|---|
| `app.py` | Punto de entrada — page config, `session_state` init, wiring de las 6 pestañas (`st.tabs()`). |

### `src/config.py`

Única fuente de verdad de los universos de tickers y tablas estáticas: `TICKERS`, `ETF_TICKERS`,
`PORTFOLIO_CDI_TICKERS`/`PORTFOLIO_CDI_UNDERLYING`/`PORTFOLIO_CDI_SECTOR`,
`CRYPTO_BINANCE_SYMBOLS`, `RISK_FREE_RATE`. Sin lógica, solo datos/constantes.

### `src/data/` — proveedores de datos

| Archivo | Rol |
|---|---|
| `fmp_client.py` | Wrapper sobre la API de Financial Modeling Prep — 7 funciones (`get_quote`, `get_profile`, estados financieros, `get_historical_prices`, `get_analyst_view`), cacheadas en disco con fallback a la última respuesta buena. |
| `yfinance_client.py` | Mismas 7 funciones que `fmp_client.py` (misma forma de dict, para que `fair_value.py` sea agnóstico al proveedor activo); sin API key, sin límite de años de estados financieros. |
| `binance_client.py` | Klines públicas de Binance (sin API key) — única fuente de datos de BTC/ETH/SOL (diario, 4h y 1h nativos). |
| `cache.py` | Caché en disco compartida entre proveedores (`.cache/`) — última respuesta buena por llamada, sin TTL propio (el TTL vive en `@st.cache_data` del lado de `src/ui/shared.py`). |
| `errors.py` | `DataError` — excepción común a los 3 proveedores. |
| `fx.py` | TRM USD/COP vía yfinance — existe pero no está en uso hoy (los CDIs de Portafolio ya cotizan en COP). |
| `market_hours.py` | Chequeo aproximado de horario de mercado NYSE/NASDAQ (sin feriados) para no gastar cuota de FMP fuera de horario. |

### `src/valuation/` — las 6 fórmulas + filtros + orquestación

| Archivo | Rol |
|---|---|
| `fair_value.py` | Orquesta: `evaluate_ticker()` (I/O) + `_evaluate_from_data()` (puro, reusado por `backtest.py`); `summarize_signals()` agrupa las 6 fórmulas en 3 familias y vota el veredicto. |
| `dcf.py` | DCF de 2 etapas sobre FCFF, 3 escenarios (pesimista/base/optimista) ponderados. |
| `multiples.py` | Bandas históricas de P/E propio, ponderadas por recencia (mean-reversion). |
| `book_value.py` | Valor Patrimonial (Book Value) — el método más conservador de Graham. |
| `growth.py` | PEG/PEGY de Peter Lynch — P/E ajustado por crecimiento de EPS (+ dividendo). |
| `graham.py` | Número de Graham: `√(22.5 × EPS × BVPS)`. |
| `graham_growth.py` | Fórmula de crecimiento de Graham: `EPS × (8.5 + 2g) × 4.4 / Y`. |
| `quality.py` | Filtro (no señal de precio): ROIC vs. WACC — ¿la empresa crea valor al reinvertir? |
| `solvency.py` | Filtro: cobertura de intereses y deuda/EBITDA — riesgo de apalancamiento. |
| `analyst_view.py` | Consenso de Wall Street (solo yfinance) — contexto, no un método propio. |
| `trend.py` | EMA-55 / SMA 50-200 — momentum, no participa del voto de `summarize_signals()`. |
| `risk_return.py` | CAGR (1/3/5y), volatilidad, Sharpe, máxima caída — 100% retrospectivo; compartido entre Acciones y ETFs. |
| `etf_analysis.py` | Valoración de ETFs (P/E histórico del S&P 500 como referencia) + riesgo/retorno — no usa las 6 fórmulas de acciones. |
| `lynch_category.py` | Heurística que marca cuándo PEG/Graham-growth se aplican fuera de su dominio (cíclicas, ganancias erráticas). |

### `src/ui/` — una pestaña por archivo + plumbing cross-tab

| Archivo | Rol |
|---|---|
| `shared.py` | Cross-tab: caché (`_cached_evaluation`, `_get_or_fetch`, `_parallel_fetch`), badges, `render_sticky_price`, `render_advanced_levels_chart`, labels del Market Reaction Zone Engine. |
| `stocks.py` | Pestaña Acciones — lista + detalle de `TICKERS`. |
| `etfs.py` | Pestaña ETFs — lista + detalle. |
| `validation.py` | Pestaña Validación — backtest en UI + historial de veredictos. |
| `speculation.py` | Pestaña Especulación (solo acciones) + `render_speculation_indicators()` (compartida con Cripto) + sección del Market Reaction Zone Engine sobre datos diarios. |
| `cripto.py` | Pestaña Cripto (BTC/ETH/SOL, Binance) — mismo cuerpo de indicadores + Market Reaction Zone Engine sobre 4h. |
| `portfolio.py` | Pestaña Portafolio — alta de compras y ventas, resumen de holdings, "Ganancias realizadas", "Plan de compra escalonada", auto-refresh de precios (`st.fragment`). |

### `src/` — módulos de cómputo top-level (no UI)

| Archivo | Rol |
|---|---|
| `portfolio.py` | Cómputo de Portafolio: `load_purchases`/`save_purchases`, `load_sales`/`save_sales`, `summarize_by_ticker` (holdings netos), `realized_gains_summary`, comisiones, `build_synthetic_portfolio_series`, `project_future_value`. |
| `speculation.py` | RSI, MACD, Bollinger, ADX, OBV, soportes/resistencias simples, reacciones por régimen — computación técnica, separada de la valoración. |
| `support_resistance.py` | "Market Reaction Zone Engine" — motor multi-metodología de soporte/resistencia (DBSCAN, KDE, RANSAC/Theil-Sen/Huber, Hough, Volume Profile, VWAP), compartido por Especulación y Cripto vía `daily_reference_config()`/`SRConfig()`. |
| `drawdown_dca.py` | Zona de acumulación por caída desde máximo de 1 año, usado en Portafolio. |
| `backtest.py` | ¿El veredicto de hace N años habría anticipado el retorno real? Limitaciones documentadas en su propio docstring. |
| `preferences.py` | Persiste el filtro de tickers de Acciones entre reinicios (`app_data/preferences.json`). |
| `verdict_history.py` | Historial diario de veredictos por ticker (`app_data/verdict_history.json`). |

### `scripts/` — procesos delegados a CPU (no LLM)

| Archivo | Rol |
|---|---|
| `oos_validate.py` | Validador fuera de muestra reusable (split cronológico 60/40, consistencia de signo, barrido de umbrales) — reemplaza re-derivar esta metodología a mano en cada investigación. |
| `verify_app.py` | Smoke test de las 6 pestañas vía `AppTest`, sin navegador. |
| `run_app.sh` / `stop_app.sh` | Arrancar/parar el servidor Streamlit local (puerto libre, health check, kill confiable por línea de comando). |
| `add_sale.py` | Agrega una venta a `portfolio_data/sales.json` desde la terminal, validada igual que la tabla "Tus ventas" de la UI — para registrar una venta dictada por chat sin abrir el navegador. |
| `telegram_alerts.py` | Manda una alerta de Telegram por ticker cuyo veredicto cambió desde el último registro — corrida manual, no programada. |

## Skills (`.claude/skills/`)

Guían a Claude Code a trabajar en este repo — cada una acota el alcance a los archivos de una
pestaña/tema y documenta su historial de diseño en `references/design-history.md` cuando
aplica.

| Skill | Qué cubre |
|---|---|
| `financial-advisor-stocks` | Pestaña Acciones — tarjetas de valoración, lista/filtro de tickers, detalle. |
| `financial-advisor-etfs` | Pestaña ETFs — lista o detalle de un ETF. |
| `financial-advisor-speculation` | Pestaña Especulación (solo acciones) — RSI, soporte/resistencia, MACD, Bollinger, ADX, OBV, Market Reaction Zone Engine. |
| `financial-advisor-cripto` | Pestaña Cripto (BTC/ETH/SOL) — mismos indicadores + motor de soporte/resistencia multi-metodología sobre Binance. |
| `financial-advisor-validation` | Pestaña Validación — backtest en UI o historial de veredictos. |
| `financial-advisor-portfolio` | Pestaña Portafolio — compras/ventas COP, resumen de holdings, ganancias realizadas, contexto de valoración. |
| `financial-advisor-add-ticker-or-formula` | Agregar un ticker nuevo o una fórmula/señal de valoración nueva. |
| `financial-advisor-run-app` | Levantar, chequear o parar el dashboard local. |
| `token-audit` | Auditar el consumo de tokens del proyecto (`CLAUDE.md`, skills, memoria) y qué procesos delegar a `scripts/`. |

## Manteniendo este README actualizado

**Todo `.py` nuevo con lógica real (no un `__init__.py` vacío) y toda skill nueva en
`.claude/skills/` se agregan a las tablas de arriba en el mismo cambio que los crea** — una
línea de rol/responsabilidad alcanza, el detalle va en el docstring del archivo o en
`references/design-history.md` de la skill correspondiente. Esta regla está también en
`CLAUDE.md` (se carga en cada conversación) para que no dependa de que esta página se lea.
