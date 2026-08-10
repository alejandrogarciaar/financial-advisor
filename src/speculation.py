"""Indicadores para la pestaña Especulación — a diferencia de todo lo demás en
`src/valuation/`, ACÁ sí se permite lenguaje de timing de corto plazo (así lo pidió
explícitamente el usuario: "acá si podemos especular 100%"). No se cruza con las señales de
valoración ni con el Portafolio — es una zona aparte a propósito.
"""

from dataclasses import dataclass

import numpy as np
import pandas as pd

from src.valuation.trend import EMA_PERIOD, SMA_LONG_PERIOD, SMA_SHORT_PERIOD

RSI_PERIOD = 14


def compute_rsi(closes: list[float], period: int = RSI_PERIOD) -> float | None:
    """RSI de Wilder (el estándar clásico: promedio de ganancias/pérdidas suavizado, no un
    promedio simple de los últimos N cambios)."""
    if len(closes) < period + 1:
        return None
    changes = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains = [max(c, 0.0) for c in changes]
    losses = [max(-c, 0.0) for c in changes]

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    for i in range(period, len(changes)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def compute_rsi_series(closes: list[float], period: int = RSI_PERIOD) -> list[float | None]:
    """Versión histórica (día por día) de `compute_rsi` — misma suavización de Wilder, pero
    devuelve toda la serie alineada 1:1 con `closes` en vez de solo el último valor. Existe
    para poder cruzar el RSI contra el régimen día por día (`compute_regime_rsi_reactions`),
    el mismo patrón que `classify_regime_series` ya usa para las medias móviles."""
    n = len(closes)
    rsi_series: list[float | None] = [None] * n
    if n < period + 1:
        return rsi_series
    changes = [closes[i] - closes[i - 1] for i in range(1, n)]
    gains = [max(c, 0.0) for c in changes]
    losses = [max(-c, 0.0) for c in changes]
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    def _rsi(avg_gain: float, avg_loss: float) -> float:
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100 - (100 / (1 + rs))

    rsi_series[period] = _rsi(avg_gain, avg_loss)
    for i in range(period, len(changes)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        rsi_series[i + 1] = _rsi(avg_gain, avg_loss)
    return rsi_series


def _ema_series(closes: list[float], period: int) -> list[float]:
    """A diferencia de la EMA de `trend.py` (que solo devuelve el último valor), el MACD
    necesita la serie completa para calcular el histórico de la línea de señal."""
    if len(closes) < period:
        return []
    ema = sum(closes[:period]) / period
    multiplier = 2 / (period + 1)
    series = [ema]
    for close in closes[period:]:
        ema = (close - ema) * multiplier + ema
        series.append(ema)
    return series


MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL = 9


@dataclass
class MACDResult:
    macd: float
    signal: float
    histogram: float


def compute_macd(
    closes: list[float], fast: int = MACD_FAST, slow: int = MACD_SLOW, signal_period: int = MACD_SIGNAL
) -> MACDResult | None:
    """Línea MACD = EMA rápida - EMA lenta. Señal = EMA de la línea MACD. Histograma = MACD -
    Señal — mide si el momentum de corto plazo viene a favor (positivo) o en contra
    (negativo) de la tendencia."""
    if len(closes) < slow + signal_period:
        return None
    ema_fast = _ema_series(closes, fast)
    ema_slow = _ema_series(closes, slow)
    # ema_fast arranca antes que ema_slow (período más corto) — alinear por el final, que es
    # donde ambas series representan la misma fecha (la más reciente).
    min_len = min(len(ema_fast), len(ema_slow))
    macd_line = [ema_fast[-min_len:][i] - ema_slow[-min_len:][i] for i in range(min_len)]
    if len(macd_line) < signal_period:
        return None
    signal_series = _ema_series(macd_line, signal_period)
    if not signal_series:
        return None
    macd_value = macd_line[-1]
    signal_value = signal_series[-1]
    return MACDResult(macd=macd_value, signal=signal_value, histogram=macd_value - signal_value)


BOLLINGER_PERIOD = 20
BOLLINGER_NUM_STD = 2.0


@dataclass
class BollingerBands:
    middle: float
    upper: float
    lower: float


def compute_bollinger_bands(
    closes: list[float], period: int = BOLLINGER_PERIOD, num_std: float = BOLLINGER_NUM_STD
) -> BollingerBands | None:
    """Banda media = SMA del período. Bandas superior/inferior = media ± N desvíos estándar
    del mismo período — se ensanchan con más volatilidad, se angostan con menos."""
    if len(closes) < period:
        return None
    window = closes[-period:]
    middle = sum(window) / period
    variance = sum((c - middle) ** 2 for c in window) / period
    std = variance**0.5
    return BollingerBands(middle=middle, upper=middle + num_std * std, lower=middle - num_std * std)


ADX_PERIOD = 14


@dataclass
class ADXResult:
    adx: float
    plus_di: float
    minus_di: float


def compute_adx(
    highs: list[float | None], lows: list[float | None], closes: list[float], period: int = ADX_PERIOD
) -> ADXResult | None:
    """ADX de Wilder (14) — a diferencia de RSI/MACD/Bollinger (que solo miran el cierre), acá
    hace falta el máximo y el mínimo de cada día para medir el "true range" y el movimiento
    direccional (+DM/-DM). Mide la FUERZA de la tendencia, no su dirección — un ADX alto puede
    acompañar tanto una suba como una baja sostenida; +DI vs. -DI es lo que da la dirección.

    Se investigó como posible refuerzo del régimen "fuerte" del "Plan de DCA sugerido" (mismo
    split cronológico 60/40 que validó el refuerzo de RSI para BTC) y NO se sostuvo: el signo
    del efecto se invertía entre entrenamiento y prueba, y cambiaba según el umbral de ADX
    elegido (20/25/30) — la misma fragilidad que hundió los niveles de Fibonacci (ver
    CLAUDE.md). Por eso se muestra solo como indicador descriptivo clásico (igual jerarquía que
    MACD/Bollinger), no como parte de esa recomendación.

    Suavizado de Wilder = EMA con alpha=1/period — equivalente matemático de la fórmula
    recursiva clásica ("resta 1/N, suma el valor nuevo"), expresado con `ewm` de pandas en vez
    de un loop manual como `compute_rsi_series`, porque acá hace falta encadenar 3 suavizados
    distintos (TR, +DM, -DM) antes de llegar al ADX."""
    n = len(closes)
    if n < period * 2 or len(highs) != n or len(lows) != n:
        return None

    high = pd.Series(highs, dtype=float)
    low = pd.Series(lows, dtype=float)
    close = pd.Series(closes, dtype=float)
    prev_close = close.shift(1)
    prev_high = high.shift(1)
    prev_low = low.shift(1)

    true_range = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)

    up_move = high - prev_high
    down_move = prev_low - low
    plus_dm = pd.Series(np.where((up_move > down_move) & (up_move > 0), up_move, 0.0))
    minus_dm = pd.Series(np.where((down_move > up_move) & (down_move > 0), down_move, 0.0))

    atr = true_range.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    plus_di = 100 * (plus_dm.ewm(alpha=1 / period, adjust=False, min_periods=period).mean() / atr)
    minus_di = 100 * (minus_dm.ewm(alpha=1 / period, adjust=False, min_periods=period).mean() / atr)

    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di)
    adx_series = dx.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()

    if adx_series.empty or pd.isna(adx_series.iloc[-1]):
        return None
    return ADXResult(
        adx=float(adx_series.iloc[-1]),
        plus_di=float(plus_di.iloc[-1]),
        minus_di=float(minus_di.iloc[-1]),
    )


OBV_SMA_PERIOD = 20


@dataclass
class OBVResult:
    obv: float
    obv_sma: float
    rising: bool  # obv >= su propia media móvil — "el volumen viene acompañando"


def compute_obv(
    closes: list[float], volumes: list[float | None], sma_period: int = OBV_SMA_PERIOD
) -> OBVResult | None:
    """OBV (On-Balance Volume) — acumula el volumen sumándolo en los días que el cierre sube y
    restándolo en los que baja. El nivel absoluto no significa nada por sí solo (depende de
    dónde arranca el historial disponible, no es comparable entre tickers ni entre corridas) —
    lo único que importa es su TENDENCIA, por eso se compara acá contra su propia media móvil
    de `sma_period` días, el mismo patrón que ya usa el precio contra su SMA.

    Se investigó como posible refuerzo del régimen "fuerte" del "Plan de DCA sugerido" (mismo
    método de split cronológico 60/40 que validó el refuerzo de RSI) y el resultado fue
    demasiado sensible al período de la media elegida: con 20 días daba una señal limpia para
    ETH (mismo signo en los 4 horizontes, train y test), pero con 10 o 30 días esa limpieza
    desaparecía — la misma fragilidad (resultado que cambia con parámetros cercanos e
    igualmente defendibles) que ya descartó Fibonacci y el refuerzo de ADX. Por eso se muestra
    solo como cruce descriptivo contra la tendencia de precio (confirmación/divergencia, mismo
    patrón que `trend_context_note`/`quality_context_note` en `fair_value.py`/`trend.py`), no
    como parte de esa recomendación."""
    n = len(closes)
    if n < sma_period + 1 or len(volumes) != n:
        return None
    volumes_clean = [v if v is not None else 0.0 for v in volumes]
    closes_series = pd.Series(closes, dtype=float)
    volume_series = pd.Series(volumes_clean, dtype=float)
    direction = np.sign(closes_series.diff().fillna(0.0))
    obv_series = (direction * volume_series).cumsum()
    obv_sma_series = obv_series.rolling(sma_period).mean()

    if pd.isna(obv_sma_series.iloc[-1]):
        return None
    obv_val = float(obv_series.iloc[-1])
    obv_sma_val = float(obv_sma_series.iloc[-1])
    return OBVResult(obv=obv_val, obv_sma=obv_sma_val, rising=obv_val >= obv_sma_val)


# Reemplaza a los niveles de Fibonacci (removidos): probamos esos niveles a fondo — incluso
# condicionados por régimen de tendencia y por volumen — y ninguna versión sobrevivió una
# validación fuera de muestra (train/test), el resultado cambiaba de signo con cambios chicos
# de parámetros, la firma clásica de ruido estadístico. Esto en cambio SÍ sobrevivió esa misma
# prueba para BTC/ETH: no es un nivel de precio puntual, es una pregunta más simple y con mucho
# más poder estadístico — "¿el retorno futuro es distinto según si el precio viene sostenido
# arriba (o abajo) de sus 3 medias?" — el efecto "momentum/trend-following" documentado en la
# literatura académica desde hace décadas, no algo inventado para este ticker.
REGIME_STRONG = "fuerte"
REGIME_WEAK = "debil"
REGIME_MIXED = "mixta"

# Mismos horizontes probados en la investigación: 5/10 días no mostraron nada que se sostuviera
# fuera de muestra, 20/30 sí (para BTC/ETH, no para SOL) — se muestran los 4 igual, por
# transparencia, en vez de ocultar los que no funcionaron.
REGIME_REACTION_HORIZONS_DAYS = [5, 10, 20, 30]
REGIME_MIN_OBSERVATIONS = 15


def classify_regime_series(closes: list[float]) -> list[str | None]:
    """Versión histórica (día por día) de la misma regla que ya usa `classify_trend_state`
    (app.py) para "hoy": "fuerte" = precio sostenido arriba de su EMA de EMA_PERIOD Y su SMA de
    SMA_SHORT_PERIOD Y su SMA de SMA_LONG_PERIOD a la vez; "debil" = sostenido abajo de las 3;
    "mixta" = cualquier otra combinación. None mientras no haya suficiente historia para las 3
    medias (los primeros SMA_LONG_PERIOD-1 días, ya que esa es la más lenta de las tres)."""
    n = len(closes)
    if n < SMA_LONG_PERIOD:
        return [None] * n

    ema_series = _ema_series(closes, EMA_PERIOD)  # ema_series[0] == día EMA_PERIOD-1
    ema_offset = EMA_PERIOD - 1
    closes_series = pd.Series(closes, dtype=float)
    sma_short = closes_series.rolling(SMA_SHORT_PERIOD).mean()
    sma_long = closes_series.rolling(SMA_LONG_PERIOD).mean()

    regimes: list[str | None] = [None] * n
    for i in range(SMA_LONG_PERIOD - 1, n):
        sma_s, sma_l = sma_short.iloc[i], sma_long.iloc[i]
        if pd.isna(sma_s) or pd.isna(sma_l):
            continue
        ema_val = ema_series[i - ema_offset]
        price = closes[i]
        above_ema, above_s, above_l = price >= ema_val, price >= sma_s, price >= sma_l
        if above_ema and above_s and above_l:
            regimes[i] = REGIME_STRONG
        elif not above_ema and not above_s and not above_l:
            regimes[i] = REGIME_WEAK
        else:
            regimes[i] = REGIME_MIXED
    return regimes


@dataclass
class RegimeReaction:
    regime: str
    horizon_days: int
    observations: int
    mean_return: float | None  # retorno promedio histórico a horizon_days, dado ese régimen
    win_rate: float | None  # fracción de esas veces que el retorno fue positivo


def compute_regime_reactions(closes: list[float]) -> list[RegimeReaction]:
    """Para cada (régimen, horizonte), junta todos los días históricos que estuvieron en ese
    régimen y mide qué retorno tuvieron horizon_days después — el promedio y qué fracción fue
    positiva. Descarta con muestra chica (< REGIME_MIN_OBSERVATIONS) en vez de mostrar un
    número poco confiable. Esto es estadística descriptiva sobre el propio historial del
    ticker: valida (o no) según lo que ya viste en régimen "fuerte" en el propio backtest de
    esta conversación, no algo que este código re-verifique en cada corrida."""
    n = len(closes)
    regimes = classify_regime_series(closes)
    closes_series = pd.Series(closes, dtype=float)
    regime_series = pd.Series(regimes)

    reactions = []
    for horizon in REGIME_REACTION_HORIZONS_DAYS:
        forward_return = (closes_series.shift(-horizon) - closes_series) / closes_series
        valid = forward_return.notna() & regime_series.notna()
        for regime in [REGIME_STRONG, REGIME_WEAK, REGIME_MIXED]:
            mask = valid & (regime_series == regime)
            n_obs = int(mask.sum())
            if n_obs < REGIME_MIN_OBSERVATIONS:
                reactions.append(RegimeReaction(regime, horizon, n_obs, None, None))
                continue
            vals = forward_return[mask]
            reactions.append(
                RegimeReaction(regime, horizon, n_obs, float(vals.mean()), float((vals > 0).mean()))
            )
    return reactions


# "Golden cross"/"death cross" (SMA50 vs SMA200) probado como RÉGIMEN (el estado sostenido, no
# solo el día puntual del cruce — el cruce en sí es demasiado raro en ~5 años de historial
# diario para juntar observaciones suficientes). 50/200 es la definición canónica de esta señal,
# no un valor descubierto barriendo umbrales cercanos — a diferencia del lookback inventado del
# Spring de Wyckoff (ver design-history), acá no hace falta un chequeo de fragilidad de
# parámetros porque no hay ningún parámetro que ajustar.
GOLDEN_CROSS_HORIZONS_DAYS = [5, 10, 20, 30]
GOLDEN_CROSS_MIN_OBSERVATIONS = 15


def classify_golden_cross_series(closes: list[float]) -> list[bool | None]:
    """True = SMA50 > SMA200 hoy ("golden cross" en curso), False = SMA50 <= SMA200 ("death
    cross" en curso). None mientras no haya suficiente historial para la SMA_LONG_PERIOD (los
    primeros SMA_LONG_PERIOD-1 días)."""
    n = len(closes)
    if n < SMA_LONG_PERIOD:
        return [None] * n
    closes_series = pd.Series(closes, dtype=float)
    sma_short = closes_series.rolling(SMA_SHORT_PERIOD).mean()
    sma_long = closes_series.rolling(SMA_LONG_PERIOD).mean()
    states: list[bool | None] = [None] * n
    for i in range(SMA_LONG_PERIOD - 1, n):
        sma_s, sma_l = sma_short.iloc[i], sma_long.iloc[i]
        if pd.isna(sma_s) or pd.isna(sma_l):
            continue
        states[i] = bool(sma_s > sma_l)
    return states


@dataclass
class GoldenCrossReaction:
    in_golden_cross: bool  # True = franja "golden cross", False = franja "death cross"
    horizon_days: int
    observations: int
    mean_return: float | None
    win_rate: float | None


def compute_golden_cross_reactions(closes: list[float]) -> list[GoldenCrossReaction]:
    """Para cada (estado, horizonte), junta todos los días históricos que estuvieron en ese
    estado y mide qué retorno tuvieron horizon_days después — mismo patrón que
    `compute_regime_reactions`. IMPORTANTE: el signo del retorno NO es el mismo para todos los
    tickers validados (ver GOLDEN_CROSS_VALIDATED_TICKERS en src/ui/speculation.py) — para
    AAPL/TSLA "golden cross" rindió, en promedio, PEOR que "death cross" (el cruce es un
    indicador rezagado: para cuando confirma, ya pasó buena parte del rebote), mientras que para
    UBER salió con el signo "tradicional" (positivo). Por eso la UI muestra el número real de
    cada ticker en vez de una etiqueta genérica "alcista"/"bajista"."""
    states = classify_golden_cross_series(closes)
    closes_series = pd.Series(closes, dtype=float)
    state_series = pd.Series(states)

    reactions = []
    for horizon in GOLDEN_CROSS_HORIZONS_DAYS:
        forward_return = (closes_series.shift(-horizon) - closes_series) / closes_series
        valid = forward_return.notna() & state_series.notna()
        for state in (True, False):
            mask = valid & (state_series == state)
            n_obs = int(mask.sum())
            if n_obs < GOLDEN_CROSS_MIN_OBSERVATIONS:
                reactions.append(GoldenCrossReaction(state, horizon, n_obs, None, None))
                continue
            vals = forward_return[mask]
            reactions.append(
                GoldenCrossReaction(state, horizon, n_obs, float(vals.mean()), float((vals > 0).mean()))
            )
    return reactions


# Refinamiento sobre el régimen "fuerte": la validación fuera de muestra de esta sesión (mismo
# split 60/40) probó si el RSI agrega información DENTRO de los días ya "fuerte" — separando
# "fuerte + RSI ≥ 70" contra "fuerte sin sobrecompra" y comparando el retorno futuro entre
# ambos. El resultado fue específico de BTC: el retorno de "fuerte + sobrecompra" superó a
# "fuerte sin sobrecompra" con signo consistente en los 4 horizontes, train y test. Para ETH el
# signo del diferencial se invirtió entre train y test (no persistió) — no validó. Ver
# REGIME_RSI_OVERBOUGHT_VALIDATED_HORIZONS en app.py para el scope exacto por ticker.
RSI_OVERBOUGHT_THRESHOLD = 70.0


@dataclass
class RegimeRsiReaction:
    horizon_days: int
    observations: int
    mean_return: float | None
    win_rate: float | None


def compute_regime_rsi_reactions(closes: list[float]) -> list[RegimeRsiReaction]:
    """Como `compute_regime_reactions`, pero acotado al subconjunto 'fuerte + RSI sobrecomprado'
    — el refinamiento que sí agregó información nueva sobre el régimen solo, validado fuera de
    muestra únicamente para BTC (ver comentario arriba)."""
    n = len(closes)
    regimes = classify_regime_series(closes)
    rsi_series = compute_rsi_series(closes)
    closes_series = pd.Series(closes, dtype=float)

    reactions = []
    for horizon in REGIME_REACTION_HORIZONS_DAYS:
        forward_return = (closes_series.shift(-horizon) - closes_series) / closes_series
        mask = pd.Series(
            [
                forward_return.notna().iloc[i]
                and regimes[i] == REGIME_STRONG
                and rsi_series[i] is not None
                and rsi_series[i] >= RSI_OVERBOUGHT_THRESHOLD
                for i in range(n)
            ]
        )
        n_obs = int(mask.sum())
        if n_obs < REGIME_MIN_OBSERVATIONS:
            reactions.append(RegimeRsiReaction(horizon, n_obs, None, None))
            continue
        vals = forward_return[mask]
        reactions.append(
            RegimeRsiReaction(horizon, n_obs, float(vals.mean()), float((vals > 0).mean()))
        )
    return reactions


# Wyckoff Spring: rechazado para las 8 acciones de TICKERS (ver design-history de
# financial-advisor-speculation) — re-testeado para cripto (BTC/ETH/SOL, misma sesión que agregó
# el Índice de Miedo y Codicia) y validó limpio para BTC/ETH en los 3 lookbacks (10/20/30), sin
# fragilidad de parámetro. El signo es AL REVÉS de lo que dice la teoría de Wyckoff: un spring
# se supone que anticipa un rebote alcista (falsa ruptura antes de acumulación), pero acá
# anticipó peor retorno que el promedio del propio ticker (ETH: retorno absoluto negativo
# mientras el mercado en general subía; BTC: por debajo del promedio, sin signo absoluto estable
# entre train/test). SOL no validó en ningún lookback. Ver
# WYCKOFF_SPRING_VALIDATED_TICKERS en src/ui/cripto.py para el scope exacto.
WYCKOFF_SPRING_LOOKBACK = 20  # el del medio de los 3 (10/20/30) que se barrieron — los 3 validaron
WYCKOFF_SPRING_HORIZONS_DAYS = REGIME_REACTION_HORIZONS_DAYS
WYCKOFF_SPRING_MIN_OBSERVATIONS = REGIME_MIN_OBSERVATIONS


def classify_wyckoff_spring_series(lows: list[float], closes: list[float], lookback: int = WYCKOFF_SPRING_LOOKBACK) -> list[bool]:
    """Un spring dispara en el día i si `lows[i]` perfora el mínimo de los `lookback` días
    anteriores (trailing, sin mirar el propio día i — sin lookahead) Y `closes[i]` cierra de
    vuelta en o por encima de ese mínimo el mismo día (la "recuperación" que distingue un spring
    de una ruptura común). Definición idéntica a la usada en la investigación de acciones
    (rechazada ahí, ver design-history de financial-advisor-speculation)."""
    n = len(closes)
    out = [False] * n
    for i in range(lookback, n):
        support = min(lows[i - lookback:i])
        if lows[i] < support and closes[i] >= support:
            out[i] = True
    return out


@dataclass
class WyckoffSpringReaction:
    horizon_days: int
    observations: int
    mean_return: float | None
    win_rate: float | None


def compute_wyckoff_spring_reactions(
    lows: list[float], closes: list[float], lookback: int = WYCKOFF_SPRING_LOOKBACK
) -> list[WyckoffSpringReaction]:
    """Retorno futuro promedio de los días donde disparó un spring (mismo patrón que
    `compute_golden_cross_reactions`, pero sin el estado complementario — un spring es un evento
    raro, no un régimen sostenido, así que no hay un "no-spring" simétrico interesante para
    mostrar). El número relevante es el gap contra el promedio SIN condicionar del propio ticker,
    no contra cero — mismo criterio que el resto de este proyecto con BTC/ETH/SOL (drift fuerte),
    la UI es la que calcula y muestra ese promedio de referencia."""
    springs = classify_wyckoff_spring_series(lows, closes, lookback)
    closes_series = pd.Series(closes, dtype=float)
    spring_series = pd.Series(springs)

    reactions = []
    for horizon in WYCKOFF_SPRING_HORIZONS_DAYS:
        forward_return = (closes_series.shift(-horizon) - closes_series) / closes_series
        mask = forward_return.notna() & spring_series
        n_obs = int(mask.sum())
        if n_obs < WYCKOFF_SPRING_MIN_OBSERVATIONS:
            reactions.append(WyckoffSpringReaction(horizon, n_obs, None, None))
            continue
        vals = forward_return[mask]
        reactions.append(
            WyckoffSpringReaction(horizon, n_obs, float(vals.mean()), float((vals > 0).mean()))
        )
    return reactions
