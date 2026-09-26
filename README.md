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

### Requisito previo: el paquete privado `portfolio`

`requirements.txt` **no** alcanza. La lógica de costo promedio / ganancias realizadas vive en el
paquete privado [`portfolio`](https://github.com/alejandrogarciaar/portfolio), que a propósito no
está en `requirements.txt` (rompería el deploy público de Streamlit Cloud, que no tiene llave SSH
ni PAT para instalar una dependencia privada — ver `CLAUDE.md`). Sin él, la app **entera** falla
al importar (`app.py:11` → `src/ui/portfolio.py` → `src/portfolio.py`), no solo la pestaña
Portafolio:

```
ModuleNotFoundError: No module named 'portfolio'
```

En una máquina nueva, antes de `streamlit run app.py`:

```
cd ..                      # a la raíz donde vive este repo
git clone git@github.personal:alejandrogarciaar/portfolio.git
cd financial-advisor
./venv/bin/pip install -e ../portfolio
```

Dos detalles que cuestan un rato si no se saben:

- **El alias SSH `github.personal`, no `github.com`.** `~/.ssh/config` en esta máquina no tiene
  entrada `Host github.com`; las tres llaves están detrás de aliases. Con la URL
  `git@github.com:...` el clone falla con `Permission denied (publickey)`. Es el mismo alias que
  usa el `origin` de este repo.
- **El clone va como checkout hermano (`../portfolio`).** También se acepta `.portfolio_repo/`
  adentro de este repo (la ubicación original) — `_SYNC_REPO_CANDIDATES` en `src/portfolio.py`
  prueba las dos. Si no encuentra ninguna, el auto-sync de compras/ventas hacia ese repo se
  desactiva **en silencio**, sin error.

O, equivalente y con manejo de puerto/health-check/reuso de instancia ya resuelto:
`./scripts/run_app.sh` para arrancar, `./scripts/stop_app.sh` para parar (ver
`.claude/skills/financial-advisor-run-app/`).

Por defecto usa el proveedor de datos `yfinance`, que no requiere API key. Si querés usar
`fmp` en su lugar, copiá `.env.example` a `.env` y completá `FMP_API_KEY`.

No hay suite de tests formal; `scripts/verify_app.py` corre un smoke test de las 6 pestañas vía
`streamlit.testing.v1.AppTest` (sin navegador). No hay herramientas de lint/build configuradas.

### Actualizar el portafolio desde Telegram

`scripts/telegram_portfolio_bot.py` deja registrar una compra o venta de Portafolio con un menú
de botones en Telegram, sin abrir el navegador. Es agnóstico a la máquina: corre en cualquier
Windows que ya cumpla lo mismo que hace falta para `streamlit run app.py` (repo clonado, paquete
`portfolio` instalado — ver arriba —, acceso SSH de push al `origin` de este repo), más dos
variables nuevas en `.env`:

```
TELEGRAM_TOKEN=...
TELEGRAM_CHAT_ID=...
```

`TELEGRAM_TOKEN` se puede copiar tal cual del `.env` de `market-signals-telegram` (ese bot solo
envía alertas y nunca escucha comandos, así que no hay conflicto por reusar el mismo token).
`TELEGRAM_CHAT_ID` en cambio **no** — tiene que ser el chat privado (1 a 1) de este bot con el
usuario, nunca el ID de un grupo (aunque sea el mismo grupo donde ese otro bot ya postea alertas).
Motivo: el "modo privacidad de grupo" de Telegram hace que un bot en un grupo reciba comandos
(`/algo`) y toques de botón, pero **no** mensajes de texto sueltos como "1" o "250000" — el menú
de este bot depende de esas respuestas de texto libre (cantidad, precio, fecha/comisión manual),
así que en un grupo se traba justo ahí, sin ningún error visible (Telegram ni siquiera reenvía
ese mensaje). Se puede evitar desactivando el modo privacidad del bot vía `@BotFather` →
`/setprivacy` → `Disable`, pero lo más simple es directamente hablarle al bot por privado.

Cómo conseguir el `chat_id` correcto la primera vez (o en una máquina nueva): arrancá el bot con
cualquier valor en `TELEGRAM_CHAT_ID` (incluso el placeholder de `.env.example`), mandale `/start`
al bot **por mensaje privado** desde Telegram, y en la consola del bot va a aparecer una línea
`Ignorado: mensaje de chat_id no autorizado <NÚMERO> (private): '/start'` — ese `<NÚMERO>` (sin
signo, a diferencia de los IDs de grupo que empiezan con `-`) es el que va en `TELEGRAM_CHAT_ID`.
Reiniciar el bot después de corregirlo.

Arrancarlo (se deja corriendo; `Ctrl+C` para pararlo):

```
./venv/Scripts/python.exe scripts/telegram_portfolio_bot.py
```

Uso: `/start` **en el chat privado con el bot** → elegir Compra o Venta → ticker → cantidad →
precio → comisión (por defecto o manual) → fecha (hoy o manual) → confirmar. A diferencia de la
UI, cada carga por Telegram hace `git pull` antes de validar y **commit + push automático** de
`portfolio_data/*.json` al `origin` de este repo al confirmar — así el dato llega solo a otras
máquinas y al deploy público, sin volver a la PC a pushear a mano.

Cosas a tener en cuenta:

- Solo responde al chat de `TELEGRAM_CHAT_ID`; cualquier otro se ignora en silencio.
- No correrlo en dos máquinas al mismo tiempo — Telegram solo permite un proceso haciendo
  polling por token a la vez y rechaza al segundo con `409` (el script lo detecta y avisa).

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
| `binance_client.py` | Klines públicas de Binance (sin API key) — única fuente de datos de BTC/ETH/SOL (diario, 4h y 1h nativos, + cualquier otro intervalo nativo vía `get_historical_prices_multi_timeframe()`). |
| `bitstamp_client.py` | OHLC diario público de Bitstamp (sin API key) — fuente SOLO de "📐 Niveles calculados" en Cripto, porque es el feed `BTCUSD` de TradingView sobre el que Crecetrader dibuja sus niveles. |
| `fear_greed_client.py` | Índice de Miedo y Codicia cripto (alternative.me, sin API key) — un solo valor para todo el mercado, no por ticker. |
| `sosovalue_client.py` | ETFs spot cripto (BTC/ETH/SOL, `openapi.sosovalue.com`, requiere `SOSOVALUE_API_KEY`) — lista por símbolo, historia diaria (~1 mes) y snapshot de hoy (AUM, flujos, prima/descuento, expense ratio) por fondo. Empezó Solana-only (reemplazando un scraper exploratorio de solanafloor.com), se generalizó por símbolo y se conectó a la pestaña Cripto (`render_etf_flows()`) el mismo día. |
| `cache.py` | Caché en disco compartida entre proveedores (`.cache/`) — última respuesta buena por llamada, sin TTL propio (el TTL vive en `@st.cache_data` del lado de `src/ui/shared.py`). |
| `errors.py` | `DataError` — excepción común a todos los proveedores. |
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
| `shared.py` | Cross-tab: caché (`_cached_evaluation`, `_get_or_fetch`, `_parallel_fetch`), badges, `render_sticky_price`, `render_advanced_levels_chart`, `render_niveles_calculados` (panel de niveles Crecetrader, usado por Cripto y Especulación), labels del Market Reaction Zone Engine. |
| `stocks.py` | Pestaña Acciones — lista + detalle de `TICKERS`. |
| `etfs.py` | Pestaña ETFs — lista + detalle. |
| `validation.py` | Pestaña Validación — backtest en UI + historial de veredictos. |
| `speculation.py` | Pestaña Especulación (solo acciones) + `render_speculation_indicators()` (compartida con Cripto) + sección del Market Reaction Zone Engine sobre datos diarios + pestaña interna "Niveles calculados". |
| `cripto.py` | Pestaña Cripto (BTC/ETH/SOL, Binance) — mismo cuerpo de indicadores + Market Reaction Zone Engine sobre 4h + VWAP, Wyckoff Spring y flujos de ETFs spot vía SoSoValue (secciones propias, no compartidas con Especulación), más la pestaña interna "Niveles calculados" (`render_niveles_calculados()`, en `shared.py`). |
| `portfolio.py` | Pestaña Portafolio — alta de compras y ventas, resumen de holdings, "Ganancias realizadas", "Plan de compra escalonada", auto-refresh de precios (`st.fragment`). |

### `src/` — módulos de cómputo top-level (no UI)

| Archivo | Rol |
|---|---|
| `portfolio.py` | Cómputo de Portafolio: `load_purchases`/`save_purchases`, `load_sales`/`save_sales`, `summarize_by_ticker` (holdings netos), `realized_gains_summary`, comisiones, `build_synthetic_portfolio_series`, `project_future_value`. |
| `speculation.py` | RSI, MACD, Bollinger, VWAP, ADX, OBV, soportes/resistencias simples, reacciones por régimen — computación técnica, separada de la valoración. |
| `support_resistance.py` | "Market Reaction Zone Engine" — motor multi-metodología de soporte/resistencia (DBSCAN, KDE, RANSAC/Theil-Sen/Huber, Hough, Volume Profile, VWAP), compartido por Especulación y Cripto vía `daily_reference_config()`/`SRConfig()`. |
| `drawdown_dca.py` | Zona de acumulación por caída desde máximo de 1 año, usado en Portafolio. |
| `niveles_calculados.py` | (motor de la app; el homónimo en `scripts/` es el CLI autocontenido) Reconstrucción del método de niveles de Crecetrader (envolvente de sesión, rejilla diaria, fracciones macro, eje semanal) + confirmaciones sobre un nivel (rebote/ruptura/retest, sin uso en la UI) — algoritmo puro, sin I/O ni dependencias externas. |
| `niveles_calculados_inputs.py` | Deriva las 6 entradas de `niveles_calculados.LevelEngine` desde una serie de velas diarias (ancla estructural — ventana anual auto-extendida si su mínimo es un artefacto del borde —, primer impulso, caída macro, apertura semanal). Además, la calibración por ticker de los umbrales del impulso y la escalera diaria con 40/60%. |
| `backtest.py` | ¿El veredicto de hace N años habría anticipado el retorno real? Limitaciones documentadas en su propio docstring. |
| `preferences.py` | Persiste el filtro de tickers de Acciones entre reinicios (`app_data/preferences.json`). |
| `verdict_history.py` | Historial diario de veredictos por ticker (`app_data/verdict_history.json`). |

### `scripts/` — procesos delegados a CPU (no LLM)

| Archivo | Rol |
|---|---|
| `oos_validate.py` | Validador fuera de muestra reusable (split cronológico 60/40, consistencia de signo, barrido de umbrales/temporalidades, baseline acotable a un subconjunto para chequeos de redundancia) — reemplaza re-derivar esta metodología a mano en cada investigación. |
| `vwap_oos_validate.py` | Investigación: ¿la distancia del precio al VWAP (normalizada por ATR) anticipa el retorno futuro de BTC/ETH/SOL? Barre 3 ventanas × 2 lados × 3 umbrales y chequea redundancia contra el régimen de tendencia. Correr local (Binance bloquea los entornos remotos). |
| `niveles_oos_validate.py` | Estudio OOS de los Niveles Calculados (capas 2/3/4, walk-forward sin look-ahead, 60/40, barrido θ en ATR y **placebo de densidad**: 40 rejillas falsas de igual densidad). Resultado 2026-08-30: **0/24 combos validan** en BTC/ETH/SOL — los toques no anticipan el retorno mejor que rejillas arbitrarias; la sección sigue descriptiva. |
| `envolvente_oos_validate.py` | Parte 2 del estudio OOS: la capa 1 (envolvente de sesión) sobre velas 1h (~17.500/moneda, 2 años), con las mismas garantías (walk-forward, 60/40, barrido θ, placebo de densidad). Resultado 2026-08-30: **0/18 combos validan** en BTC/ETH/SOL — gaps horarios de ±0,1%, indistinguibles de envolventes falsas. Las 4 capas quedan medidas: reproducen los gráficos, no predicen. |
| `verify_app.py` | Smoke test de las 6 pestañas vía `AppTest`, sin navegador. |
| `run_app.sh` / `stop_app.sh` | Arrancar/parar el servidor Streamlit local (puerto libre, health check, kill confiable por línea de comando). Corren igual en Windows (git-bash) y macOS/Linux. |
| `_platform.sh` | Lo único que sabe en qué sistema operativo corre (venv `Scripts/` vs `bin/`, sondeo de puerto, match de procesos) — se hace `source` desde los dos scripts de arriba, no se ejecuta solo. |
| `add_sale.py` | Agrega una venta a `portfolio_data/sales.json` desde la terminal, validada igual que la tabla "Tus ventas" de la UI — para registrar una venta dictada por chat sin abrir el navegador. |
| `telegram_portfolio_bot.py` | Bot de Telegram (menú de botones) para registrar compras/ventas de Portafolio sin abrir el navegador — misma validación que la UI, más `git pull`/`push` automático del propio repo al confirmar (a diferencia de `add_sale.py` y la UI, donde ese paso sigue siendo manual). Agnóstico a la máquina: mismos requisitos que `streamlit run app.py`. |
| `niveles_calculados.pine` | El mismo cálculo como indicador de TradingView (Pine v6). Corrige el modo automático de una versión previa: ventana anual en días de calendario (no barras), rango base = primer impulso (no el rango del año) y rol de la envolvente contra el precio. Lógica verificada contra el `.py` en los 11 tickers; la sintaxis hay que compilarla en TradingView. Incluye los dos fixes del 2026-08-30 (capa 4 — eje semanal, con objetivos como heurística no verificada — y ancla estructural desactivable). |
| `niveles_calculados_abanico.pine` | Variante del anterior: abanico simétrico de anillos alrededor del precio actual (paso configurable), filtros de dibujo (rango visible / distancia % / tope de niveles por capa) y atenuación de la rejilla en intradía. **No reemplaza al de arriba**: su modo automático vuelve al rango del año en vez del primer impulso, que es justo lo que el otro corrige, y viene con los modos automáticos apagados por defecto. Sin verificar contra el `.py`. También recibió los dos fixes del 2026-08-30 (eje semanal + ancla estructural, esta última ahora en días de calendario como ya decía su etiqueta). |
| `niveles_calculados_v2.pine` | **v2 del indicador de TradingView: las mismas 4 capas de `niveles_calculados.pine` (copiadas sin tocar una fórmula) + una capa 5 de ondas de Elliott.** La v1 queda congelada; cualquier cambio a las capas 1-4 va acá. La capa 5 arma un zigzag por umbral (ATR×mult o %), valida las 3 reglas duras del impulso (la 2 no se come la 1, la 3 no es la más corta, la 4 no invade la 1), identifica la onda en curso entre 6 casos y proyecta objetivos por proporciones de Fibonacci + el precio exacto que **invalida** el conteo. **Repinta por construcción** (un pivote se confirma recién cuando el precio giró el umbral) y **no está validado fuera de muestra** — mismo estándar descriptivo que las capas 1-4, que ya dieron 0/24 y 0/18 en sus propios estudios. Marca confluencias con las otras capas en la etiqueta. Lógica verificada con `elliott_check.py`; la sintaxis Pine hay que compilarla en TradingView. |
| `niveles_calculados_v3.pine` | **v3 = v2 completa + vigilancia**, creada a pedido; la v2 queda como referencia estable (mismo patrón v1→v2). Agrega: **alertas** vía `alert()` (precio a menos de X ATR de un nivel de la escalera con histéresis, invalidación del conteo cruzada, cambio de conteo al cierre de barra — requieren crear en TV una alerta "Any alert() function call"); **test de estabilidad del conteo** (el mismo conteo con el umbral del zigzag ±17%, vía un espejo condensado `waveCode()` verificado contra el conteo completo en 5.400 comparaciones locales con 0 desacuerdos, más un guardia en la tabla que delata desincronización); **estado del pivote** (hace cuántas barras confirmó el último, y el precio exacto que confirmaría el giro del tramo que repinta); **confluencia generalizada** (cualquier nivel anota con `~` a un vecino de otra capa); y **escalera completa** (incluye capas no dibujadas, marcadas con `*`). Compiló a la primera en TradingView; sigue siendo descriptivo — las alertas avisan que un precio del plan quedó al alcance, no recomiendan nada. |
| `elliott_oos_validate.py` | Estudio OOS de la capa 5 (ondas de Elliott): ¿el conteo anticipa algo? Walk-forward **causal** — `zigzag_states()` devuelve los pivotes vigentes en cada barra, así que el repintado del indicador es justamente lo que impide mirar el futuro —, 60/40, consistencia de signo en 5/10/20/30 días, etapa 2 de redundancia contra el régimen de tendencia y placebo de densidad K=40. El umbral del zigzag queda FIJO en el default (ATR14×3): barrerlo sería fabricar el resultado; 2 y 5 se reportan solo como sensibilidad. Resultado 2026-08-31: **0 variantes robustas**. El titular (dirección) valida solo en SOL; la única que valida en los 3 símbolos ("onda 2 en curso") es frágil al umbral y en ETH cambia de signo. Correr local (Binance bloquea los entornos remotos). |
| `elliott_check.py` | Banco de pruebas de la capa 5 del `.pine` de arriba: reimplementa su algoritmo en Python (Pine solo corre dentro de TradingView) y lo ejercita contra BTC/ETH/SOL (3 símbolos × 9 umbrales de ATR, 3 años diarios) y contra series sintéticas, una por rama del conteo. También expone `zigzag_states()` (los pivotes vigentes en CADA barra), que es lo que hace posible el walk-forward causal de `elliott_oos_validate.py`. Verifica que el zigzag siempre alterna y que ningún conteo se muestra con su propio nivel de invalidación ya rebasado — así se encontró un bug real (BTC ATR14×5 devolvía una "onda 4" con el precio 19.000 USD del lado equivocado de su invalidación). Correr local (Binance bloquea los entornos remotos). |
| `niveles_calculados.py` | (CLI; no confundir con `src/niveles_calculados.py`, el motor que usa la app) Las 4 capas de "Niveles calculados" desde la terminal, para cualquier cripto (Binance) o acción (Yahoo). **Autocontenido**: un archivo, solo stdlib, no importa nada de `src/` — copiable a otra máquina tal cual. Verificado nivel por nivel contra el motor de la app en los 11 tickers. |

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
