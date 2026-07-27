# Precio Justo — Acciones Americanas

Dashboard en Streamlit que evalúa si un conjunto fijo de acciones americanas de gran
capitalización está barato, justo o caro en este momento, usando 6 fórmulas de valoración
independientes agrupadas en 3 familias (flujo de caja, valor libro y múltiplos de ganancias).
Todo el texto de la interfaz está en español rioplatense.

Tickers cubiertos (`src/config.py`): AAPL, MSFT, AMZN, META, NVDA, TSLA, UBER, GOOGL.

## Pestañas

- **Acciones**: evalúa cada ticker con las 6 fórmulas de valoración, filtros de calidad
  (ROIC vs. WACC) y solvencia, tendencia (EMA-55) y métricas de riesgo/retorno.
- **ETFs**: análisis de riesgo/retorno para un conjunto de ETFs.
- **Portafolio**: seguimiento de compras propias en pesos colombianos (COP), sobre CDIs que
  trackean los tickers/ETFs de arriba y cotizan en la BVC.
- **Especulación**: indicadores técnicos de corto plazo (RSI, soportes/resistencias, MACD,
  Bandas de Bollinger) sobre un ticker a la vez, incluyendo BTC/ETH/SOL — la única sección del
  proyecto donde se permite lenguaje de timing de mercado.

## Cómo correrlo

```
pip install -r requirements.txt
streamlit run app.py
```

Por defecto usa el proveedor de datos `yfinance`, que no requiere API key. Si querés usar
`fmp` en su lugar, copiá `.env.example` a `.env` y completá `FMP_API_KEY`.

No hay suite de tests ni herramientas de lint/build configuradas en este repo.

### Backtest

Para chequear el veredicto de triangulación contra retornos históricos reales (no forma parte
de la UI):

```
python -c "from src.backtest import run_backtest; print(run_backtest())"
```

## Arquitectura (resumen)

- `src/data/`: abstracción de proveedor de datos (`fmp_client.py` / `yfinance_client.py`),
  ambos exponiendo las mismas funciones, con cacheo en disco (`.cache/`) y fallback a la
  última respuesta cacheada si falla una llamada en vivo.
- `src/valuation/`: las 6 fórmulas de valoración (`dcf.py`, `multiples.py`, `book_value.py`,
  `growth.py`, `graham.py`, `graham_growth.py`), filtros de calidad/solvencia, tendencia y
  riesgo/retorno, orquestados desde `fair_value.py`.
- `src/portfolio.py`: seguimiento de compras (COP), persistido fuera de `.cache/` en
  `portfolio_data/` (no versionado).
- `src/speculation.py`: indicadores técnicos de corto plazo, deliberadamente separado del
  código de valoración no especulativa.

Más detalle de decisiones de diseño en `CLAUDE.md`.
