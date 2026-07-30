import os
from dotenv import load_dotenv

load_dotenv()

FMP_API_KEY = os.environ["FMP_API_KEY"]
FMP_BASE_URL = "https://financialmodelingprep.com/stable"

# Excluidos del universo:
# - CSPXCO (iShares Core S&P 500 ETF): es un ETF, no una empresa individual,
#   y no tiene estados financieros propios sobre los que calcular un DCF.
# - NU (Nubank): bloqueado en el plan free de Financial Modeling Prep (quote,
#   income-statement e historical-price devuelven 402 "no disponible en tu
#   suscripción actual"), probablemente por reportar como emisor privado
#   extranjero (20-F). Solo el endpoint de perfil responde. Re-agregar si se
#   sube de plan en FMP.
TICKERS = [
    "AAPL",   # Apple
    "MSFT",   # Microsoft
    "AMZN",   # Amazon
    "META",   # Meta Platforms
    "NVDA",   # Nvidia
    "TSLA",   # Tesla
    "UBER",   # Uber
    "GOOGL",  # Alphabet
]

# Mapeo símbolo-que-usa-el-usuario -> símbolo real de yfinance. yfinance exige sufijo de
# exchange y "CSPXCO"/"CSPX" pelados no tienen feed de precio; CSPX.L (Londres, USD) sí,
# y cotiza en la misma moneda que el resto del dashboard.
ETF_TICKERS = {"CSPXCO": "CSPX.L"}

# CDIs (certificados de depósito) colombianos: mismo activo subyacente que ya cubrimos en
# TICKERS o ETF_TICKERS, pero cotizan directo en pesos en la BVC. NO van en TICKERS ni tienen
# las 6 fórmulas de valoración propias: para las acciones, sus estados financieros en
# yfinance vienen en USD (son los mismos de la empresa matriz) pero su precio viene en COP,
# así que correr el DCF/múltiplos/Graham ahí compararía un valor justo en dólares contra un
# precio en pesos — resultado sin sentido, además de redundante con la tarjeta que ya existe
# para el ticker original. Solo están habilitados para registrar compras reales en el
# Portafolio, donde solo hace falta el precio (no los estados financieros).
# Nota: "CSPXCO" también es clave en ETF_TICKERS pero apunta a un símbolo distinto
# (CSPX.L, Londres, USD) — ese feed es para la pestaña ETFs, que necesita una moneda
# consistente para su análisis; este (CSPXCO.CL, COP) es para lo que realmente pagaste acá.
PORTFOLIO_CDI_TICKERS = {
    "GOOGLCO": "GOOGLCO.CL",
    "AMZNCO": "AMZNCO.CL",
    "CSPXCO": "CSPXCO.CL",
    "AAPLCO": "AAPLCO.CL",
    "MSFTCO": "MSFTCO.CL",
    "METACO": "METACO.CL",
}

# Para el Portafolio: qué evaluación de la pestaña Acciones/ETFs reusar como contexto de cada
# CDI ("stock", clave de TICKERS) o ("etf", clave de ETF_TICKERS) — así se puede mostrar el
# veredicto de valoración de la acción/fondo matriz junto a la tenencia, sin recalcular nada.
PORTFOLIO_CDI_UNDERLYING = {
    "GOOGLCO": ("stock", "GOOGL"),
    "AMZNCO": ("stock", "AMZN"),
    "AAPLCO": ("stock", "AAPL"),
    "MSFTCO": ("stock", "MSFT"),
    "CSPXCO": ("etf", "CSPXCO"),
    "METACO": ("stock", "META"),
}

# Clasificación GICS aproximada de cada subyacente, para la sección de diversificación del
# Portafolio. Estática (no viene de ninguna API — yfinance/FMP no la exponen de forma
# confiable en este proyecto) porque el universo es chico y fijo y estas clasificaciones no
# cambian seguido: Alphabet y Meta son Comunicación desde la reclasificación GICS de 2018,
# Amazon Consumo discrecional, Apple/Microsoft Tecnología — no ambiguas. CSPX es un fondo
# diversificado (S&P 500 completo), no un sector puntual.
PORTFOLIO_CDI_SECTOR = {
    "GOOGLCO": "Comunicación",
    "AMZNCO": "Consumo discrecional",
    "AAPLCO": "Tecnología",
    "MSFTCO": "Tecnología",
    "CSPXCO": "Diversificado (ETF S&P 500)",
    "METACO": "Comunicación",
}

# Cripto para la pestaña "🪙 Cripto" (no participa en Acciones/Portafolio: no tiene estados
# financieros, ninguna de las 6 fórmulas de valoración aplicaría) — Binance en vez de yfinance
# (klines de 4h nativos, sin el tope de ~730 días que yfinance impone al reagregar barras de
# 60m, y hasta el historial completo del par listado: BTC/ETH desde 2017, SOL desde ~2020, muy
# por encima del tope de "5y" de yfinance). Especulación (acciones) no usa esto — quedó
# solo-acciones cuando estas 3 monedas se movieron a su propia pestaña.
CRYPTO_BINANCE_SYMBOLS = {
    "BTC": "BTCUSDT",
    "ETH": "ETHUSDT",
    "SOL": "SOLUSDT",
}

RISK_FREE_RATE = 0.042          # tasa libre de riesgo aprox. (bono 10Y US)
EQUITY_RISK_PREMIUM = 0.045     # prima de riesgo de mercado histórica
TERMINAL_GROWTH_RATE = 0.025    # crecimiento perpetuo (~ nominal GDP)
DCF_PROJECTION_YEARS = 5
