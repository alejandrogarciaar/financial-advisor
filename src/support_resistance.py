"""Market Reaction Zone Engine — detección de zonas de soporte/resistencia con evidencia
estadística para Especulación/Cripto. A diferencia de `compute_support_levels`/
`compute_resistance_levels` (src/speculation.py, que siguen siendo el mínimo/máximo simple de
una ventana y NO se tocan acá), este módulo combina clustering (DBSCAN), densidad (KDE), líneas
de tendencia robustas (RANSAC/Theil-Sen/Huber/Hough), volume profile, VWAP y confirmación por
velas/volumen en un único score de confianza 0-100 por nivel.

Filosofía del score (rediseño pedido explícitamente por el usuario): la CANTIDAD de touch
points NO es el criterio principal — es la CALIDAD de la reacción del mercado en cada zona. Tres
rebotes fuertes con volumen alto valen más que diez toques sin reacción relevante. Por eso
`DEFAULT_WEIGHTS` pondera fuerte `reaction_magnitude` (tamaño promedio del rebote, en múltiplos
de ATR) y `volume_during_rebounds`, y degrada `touch_points` a un componente secundario (ver el
dict más abajo). `volume_profile`/`candle_confirmation`/`proximity`/`vwap_confluence` siguen
existiendo como métodos togglables y se siguen calculando — quedan visibles en
`component_scores` para quien quiera inspeccionarlos — pero ya no tienen peso asignado en
`DEFAULT_WEIGHTS`, así que no suman puntos al score final.

Es mayormente DESCRIPTIVO: identifica y puntúa niveles, no reclama en general que predigan
retorno futuro. La investigación ya documentada en CLAUDE.md ("Using more history / multiple
touched levels for support-resistance was investigated and rejected") probó exactamente esa
pregunta con clustering simple y no sobrevivió una validación fuera de muestra (para SOL/TSLA
incluso apuntó al revés). La versión ANTERIOR de este motor (touch_points con peso 30/100, sin
`reaction_magnitude`, sin jerarquía de temporalidad, 3 temporalidades daily/weekly/monthly) SÍ se
había re-validado (mismo split cronológico 60/40, script descartable fuera del repo): estar
dentro de la zona de un nivel con score ≥ 50 mostró el mismo signo de efecto train/test para BTC
(soporte) y TSLA (soporte Y resistencia). **Ese resultado quedó ligado a la fórmula de score
vieja y NO se puede asumir vigente tras este rediseño** — mismo principio que ya aplicó este
proyecto para Fibonacci/ADX/OBV (nunca reusar una validación después de cambiar los pesos sin
volver a correr el mismo test tren/prueba). `SR_VALIDATED_TICKERS` (en `src/ui/cripto.py`) se
vació a `{}` por este motivo — ver el "Design history" de la skill `us-stocks-cripto` para el
detalle y el paso pendiente (re-correr la validación bajo el score nuevo).

Multi-timeframe se resuelve reagregando las velas DIARIAS a semanal/mensual con
pandas.resample (sin fetch intradía) más 4h/1h nativos de Binance cuando el caller los pasa
(`intraday_4h_prices`/`intraday_1h_prices`). Cada línea/nivel encontrado en una temporalidad se
re-expresa en el espacio de índice DIARIO (día 0 = la barra diaria más antigua) para poder medir
touches/rebotes/rupturas siempre contra la serie diaria real — la pendiente se reescala
dividiendo por los días-calendario aproximados de esa unidad (`BARS_PER_UNIT`) y el intercepto
se ancla al valor de la línea en su última barra de esa temporalidad, ubicada en el índice diario
más cercano (`_nearest_daily_index`). `TIMEFRAME_IMPORTANCE` pondera esas temporalidades de más
institucional (mensual/semanal) a más operativa (1h), reflejando que un nivel visible en un
timeframe más largo es intrínsecamente más significativo, no solo más "confirmado por
confluencia" (que sigue sumando como bonus aparte).
"""

from __future__ import annotations

import bisect
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
from scipy.optimize import minimize
from scipy.signal import argrelextrema
from scipy.stats import gaussian_kde
from sklearn.cluster import DBSCAN
from sklearn.linear_model import HuberRegressor, LinearRegression, RANSACRegressor, TheilSenRegressor

ALL_METHODS = {
    "dbscan",
    "kde",
    "ransac",
    "theilsen",
    "huber",
    "hough",
    "optimize",
    "volume_profile",
    "vwap_confluence",
    "candle_confirmation",
    "volume_confirmation",
    "multi_timeframe",
    "channels",
}

# Pesos del score final — suman 100. Rediseño "Market Reaction Zone Engine": la calidad de la
# reacción (tamaño del rebote + volumen durante el rebote) pesa más que la cantidad de touches —
# touch_points baja de 30 a 5, un criterio secundario, no el principal. "volume_profile",
# "candle_confirmation", "proximity" y "vwap_confluence" ya NO tienen entrada acá: siguen
# calculándose si el método está activo (quedan visibles en `component_scores`) pero no suman
# puntos — `_score_level` usa `config.weights.get(k, 0.0)`, no indexing directo, así que faltar
# acá es exactamente "peso cero", no un error.
DEFAULT_WEIGHTS = {
    "reaction_magnitude": 25.0,
    "volume_during_rebounds": 20.0,
    "respect_rate": 20.0,
    "timeframe_weight": 20.0,
    "age": 10.0,
    "touch_points": 5.0,
}

# Jerarquía institucional → operativa (pedida explícitamente): una temporalidad más larga es
# intrínsecamente más significativa, no solo "más confirmada por confluencia" (eso sigue siendo
# un bonus aparte en `timeframe_weight`, ver _score_level). Monthly/weekly quedan por encima de
# daily porque son, en ese mismo sentido, aún más "institucionales" que 1D.
TIMEFRAME_IMPORTANCE = {"monthly": 1.0, "weekly": 0.9, "daily": 0.75, "4h": 0.5, "1h": 0.3}

DEFAULT_PENALTIES = {
    "breakout_penalty_per_break": 8.0,
    "dispersion_threshold_atr_mult": 2.0,
    "dispersion_penalty": 10.0,
    "short_lifespan_bars": 10,
    "short_lifespan_penalty": 10.0,
}


@dataclass
class SRConfig:
    # Pivotes: la ventana se adapta por temporalidad (menos barras disponibles en semanal/
    # mensual → ventana más chica, si no casi ningún pivote sobrevive).
    pivot_lookback_daily: int = 5
    pivot_lookback_weekly: int = 3
    pivot_lookback_monthly: int = 2
    pivot_lookback_4h: int = 5  # similar densidad de barras que "daily" (ver BARS_PER_UNIT)
    pivot_lookback_1h: int = 5  # mismo criterio que 4h

    atr_period: int = 14
    atr_tolerance_pct: float = 0.5  # tolerancia de "touch" = 0.5x ATR actual
    breakout_tolerance_pct: float = 1.0  # más allá de esto = candidato a ruptura, no touch
    breakout_confirm_bars: int = 3  # debe sostenerse rota este # de barras para confirmarse
    episode_gap_bars: int = 3  # touches separados por ≤ esto se agrupan en un mismo "episodio"

    # Cluster Tolerance pedida: ATR(14) x 0.15 — comparte este único campo con DBSCAN/KDE
    # (agrupar pivotes) y con la fusión de candidatos en zonas (_merge_candidates), igual que
    # antes (era 1.5, un valor mucho más laxo). Zonas más angostas = "identificar zonas, no
    # líneas exactas, pero con evidencia estadística suficiente", no maximizar cantidad de niveles.
    dbscan_eps_atr_mult: float = 0.15
    dbscan_min_samples: int = 2

    kde_bandwidth: float | None = None
    kde_grid_points: int = 400

    min_pivots_for_trendline: int = 3
    hough_num_slopes: int = 181
    hough_slope_range_atr_mult: float = 3.0

    channel_max_slope_diff_pct: float = 30.0
    max_channels: int = 3

    volume_profile_bins: int = 40
    vwap_windows_days: tuple[int, ...] = (3, 7, 30, 365)
    vwap_confluence_bonus: float = 0.15

    volume_confirmation_mult: float = 1.5
    volume_confirmation_avg_period: int = 20

    optimize_max_slope_shift_atr_mult: float = 1.0  # cuánto puede mover el slope (por día) el refinamiento
    optimize_max_intercept_shift_atr_mult: float = 3.0  # cuánto puede mover el intercepto (en ATRs)

    touch_component_full_credit: int = 6  # touches para el 100% de ese componente (rango ideal pedido: 4-8)
    age_full_credit_bars: int = 180  # barras de vigencia para el 100% de "antigüedad"
    proximity_full_range_atr_mult: float = 5.0  # a esta distancia (en ATRs) la cercanía vale 0
    retest_bonus: float = 8.0
    # Tamaño de rebote (en múltiplos de ATR) que da 100% de crédito en "reaction_magnitude" — el
    # componente NUEVO que mide la calidad de la reacción, no solo si hubo rebote o no.
    reaction_magnitude_full_credit_atr_mult: float = 2.0

    # Rango de precio "sano" para un nivel, como múltiplo del mínimo/máximo REAL observado en la
    # serie — filtra extrapolaciones de líneas diagonales sin sentido (ver _score_level).
    sane_price_min_mult: float = 0.1
    sane_price_max_mult: float = 5.0

    min_touch_points: int = 3  # mínimo pedido; "ideal" es 4-8 (ver touch_component_full_credit)
    top_n: int = 8

    timeframes: tuple[str, ...] = ("daily", "weekly", "monthly")
    enabled_methods: set[str] = field(default_factory=lambda: set(ALL_METHODS))
    weights: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_WEIGHTS))
    penalties: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_PENALTIES))


@dataclass
class SRLevel:
    kind: str  # "support" | "resistance" | "channel"
    price: float | None  # solo si es horizontal (slope ~ 0)
    slope: float | None  # precio por día (índice de la serie diaria)
    intercept: float | None
    zone_low: float | None
    zone_high: float | None
    touches: int
    rebounds: int
    breaks: int
    retested: bool
    age_bars: int
    first_touch_date: str
    last_touch_date: str
    timeframes: list[str]
    methods: list[str]
    volume_at_level: float
    distance_to_price_pct: float
    avg_rebound_magnitude_atr: float
    component_scores: dict[str, float]
    confidence_score: float
    channel_direction: str | None = None
    channel_support: "SRLevel | None" = None
    channel_resistance: "SRLevel | None" = None

    def value_at(self, day_index: int) -> float:
        return _line_value(self.slope or 0.0, self.intercept or self.price or 0.0, day_index)


@dataclass
class _Pivot:
    index: int
    date: str
    price: float
    kind: str  # "high" | "low"


@dataclass
class _Candidate:
    kind: str  # "support" | "resistance"
    slope: float
    intercept: float
    methods: set[str]
    timeframes: set[str]
    contributing_prices: list[float]
    # Dispersión "real" del nivel: para horizontales (DBSCAN/KDE), el spread de precios del
    # propio cluster ya es significativo. Para diagonales (RANSAC/Theil-Sen/Huber/Hough), el
    # spread crudo de precios de los pivotes es enorme por construcción (una línea de 5 años
    # atraviesa un rango de precio grande) y NO mide qué tan ajustada está la línea — lo que
    # importa ahí es el residuo (distancia de cada pivote a la línea ajustada), calculado en el
    # momento del fit, antes de reproyectar a espacio diario. None → cae al fallback de
    # std(contributing_prices), válido solo para las horizontales.
    residual_std: float | None = None


def _line_value(slope: float, intercept: float, x: float) -> float:
    return slope * x + intercept


def _atr_series(highs: list[float], lows: list[float], closes: list[float], period: int) -> pd.Series:
    """Wilder ATR — mismo suavizado (alpha=1/period) que ya usa `compute_adx` en speculation.py,
    factorizado acá porque este módulo lo necesita como serie completa, no solo el último valor."""
    high = pd.Series(highs, dtype=float)
    low = pd.Series(lows, dtype=float)
    close = pd.Series(closes, dtype=float)
    prev_close = close.shift(1)
    true_range = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)
    return true_range.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()


def _resample_ohlcv(dated_prices: list[dict], rule: str) -> list[dict]:
    df = pd.DataFrame(dated_prices)
    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date").sort_index()
    agg = df.resample(rule).agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    )
    agg = agg.dropna(subset=["close"])
    return [
        {
            "date": idx.strftime("%Y-%m-%d"),
            "open": float(row["open"]),
            "high": float(row["high"]),
            "low": float(row["low"]),
            "close": float(row["close"]),
            "volume": float(row["volume"]),
        }
        for idx, row in agg.iterrows()
    ]


def _detect_pivots(dates: list[str], highs: list[float], lows: list[float], lookback: int) -> list[_Pivot]:
    """Pivot high/low (punto 1): un máximo/mínimo local dentro de una ventana ±lookback."""
    n = len(highs)
    pivots: list[_Pivot] = []
    if n < lookback * 2 + 1:
        return pivots
    for i in range(lookback, n - lookback):
        window_high = highs[i - lookback : i + lookback + 1]
        if highs[i] == max(window_high):
            pivots.append(_Pivot(i, dates[i], highs[i], "high"))
        window_low = lows[i - lookback : i + lookback + 1]
        if lows[i] == min(window_low):
            pivots.append(_Pivot(i, dates[i], lows[i], "low"))
    return pivots


def _nearest_daily_index(daily_dates: list[str], target_date: str) -> int | None:
    pos = bisect.bisect_right(daily_dates, target_date) - 1
    return pos if pos >= 0 else None


BARS_PER_UNIT = {"daily": 1.0, "weekly": 7.0, "monthly": 30.44, "4h": 4 / 24, "1h": 1 / 24}


def _cluster_prices_dbscan(prices: list[float], eps: float, min_samples: int) -> list[list[int]]:
    """Punto 2: agrupa pivotes cercanos en un único nivel. Devuelve índices dentro de `prices`."""
    if len(prices) < min_samples or eps <= 0:
        return []
    arr = np.array(prices, dtype=float).reshape(-1, 1)
    labels = DBSCAN(eps=eps, min_samples=min_samples).fit_predict(arr)
    groups: dict[int, list[int]] = {}
    for idx, label in enumerate(labels):
        if label == -1:
            continue
        groups.setdefault(int(label), []).append(idx)
    return list(groups.values())


def _kde_peaks(prices: list[float], bandwidth: float | None, grid_points: int) -> list[float]:
    """Punto 3: máximos de densidad sobre los precios de los pivotes."""
    if len(prices) < 3:
        return []
    arr = np.array(prices, dtype=float)
    if np.allclose(arr, arr[0]):
        return [float(arr[0])]
    try:
        kde = gaussian_kde(arr, bw_method=bandwidth)
    except Exception:
        return []
    grid = np.linspace(arr.min(), arr.max(), grid_points)
    density = kde(grid)
    peak_idx = argrelextrema(density, np.greater)[0]
    return [float(grid[i]) for i in peak_idx]


def _fit_ransac(xs: list[float], ys: list[float]) -> tuple[float, float] | None:
    """Punto 4: línea robusta que ignora outliers automáticamente."""
    if len(xs) < 3:
        return None
    X = np.array(xs, dtype=float).reshape(-1, 1)
    y = np.array(ys, dtype=float)
    try:
        model = RANSACRegressor(estimator=LinearRegression(), random_state=42)
        model.fit(X, y)
        return float(model.estimator_.coef_[0]), float(model.estimator_.intercept_)
    except Exception:
        return None


def _fit_theilsen(xs: list[float], ys: list[float]) -> tuple[float, float] | None:
    """Punto 5 (parte 1): regresión robusta menos sensible al ruido que OLS."""
    if len(xs) < 3:
        return None
    X = np.array(xs, dtype=float).reshape(-1, 1)
    y = np.array(ys, dtype=float)
    try:
        model = TheilSenRegressor(random_state=42)
        model.fit(X, y)
        return float(model.coef_[0]), float(model.intercept_)
    except Exception:
        return None


def _fit_huber(xs: list[float], ys: list[float]) -> tuple[float, float] | None:
    """Punto 5 (parte 2): alternativa robusta con función de pérdida distinta a Theil-Sen."""
    if len(xs) < 3:
        return None
    X = np.array(xs, dtype=float).reshape(-1, 1)
    y = np.array(ys, dtype=float)
    try:
        model = HuberRegressor()
        model.fit(X, y)
        return float(model.coef_[0]), float(model.intercept_)
    except Exception:
        return None


def _hough_line(xs: list[float], ys: list[float], atr_estimate: float, num_slopes: int, slope_range_mult: float) -> tuple[float, float] | None:
    """Punto 6: Hough transform expresado directo en espacio (pendiente, intercepto) — vota,
    para cada pendiente candidata, qué intercepto (redondeado a un bucket ~ATR/2) junta más
    pivotes, sin necesitar OpenCV ni coordenadas polares."""
    if len(xs) < 3:
        return None
    xs_arr = np.array(xs, dtype=float)
    ys_arr = np.array(ys, dtype=float)
    max_slope = slope_range_mult * atr_estimate if atr_estimate > 0 else 1.0
    slopes = np.linspace(-max_slope, max_slope, num_slopes)
    bucket = max(atr_estimate * 0.5, 1e-6)
    best_count = -1
    best: tuple[float, float] | None = None
    for slope in slopes:
        intercepts = ys_arr - slope * xs_arr
        buckets = np.round(intercepts / bucket).astype(int)
        values, counts = np.unique(buckets, return_counts=True)
        top = int(np.argmax(counts))
        if counts[top] > best_count:
            best_count = int(counts[top])
            best = (float(slope), float(values[top] * bucket))
    return best


def _volume_profile(prices: list[float], volumes: list[float], num_bins: int) -> tuple[np.ndarray, np.ndarray]:
    """Punto 8: distribución del volumen por nivel de precio, sobre TODA la serie diaria (no
    solo los pivotes) — el objetivo es encontrar dónde se concentró la actividad histórica."""
    arr_p = np.array(prices, dtype=float)
    arr_v = np.array([v if v is not None else 0.0 for v in volumes], dtype=float)
    lo, hi = float(arr_p.min()), float(arr_p.max())
    if hi <= lo:
        return np.array([lo]), np.array([arr_v.sum()])
    bins = np.linspace(lo, hi, num_bins + 1)
    idx = np.clip(np.digitize(arr_p, bins) - 1, 0, num_bins - 1)
    vol_by_bin = np.zeros(num_bins)
    for i, v in zip(idx, arr_v):
        vol_by_bin[i] += v
    centers = (bins[:-1] + bins[1:]) / 2
    return centers, vol_by_bin


def _rolling_vwap(dates: list[str], highs: list[float], lows: list[float], closes: list[float], volumes: list[float], window_days: int) -> float | None:
    """Punto 9, adaptado a solo-diario: VWAP "ancla móvil" sobre los últimos `window_days`
    días de calendario, usando precio típico (H+L+C)/3 por barra diaria en vez de trades
    intradía reales (que esta app nunca fetchea) — mismo patrón de ventana por días de
    `_extreme_since` en speculation.py, no buckets calendario (semana ISO, etc.)."""
    if not dates:
        return None
    last_date = datetime.strptime(dates[-1], "%Y-%m-%d")
    cutoff = last_date - timedelta(days=window_days)
    typical, vols = [], []
    for d, h, l, c, v in zip(dates, highs, lows, closes, volumes):
        if datetime.strptime(d, "%Y-%m-%d") >= cutoff and v is not None:
            typical.append((h + l + c) / 3)
            vols.append(v)
    total_vol = sum(vols)
    if total_vol <= 0:
        return None
    return sum(t * v for t, v in zip(typical, vols)) / total_vol


def _classify_candle(o: float, h: float, l: float, c: float, prev_o: float | None, prev_c: float | None) -> str | None:
    """Punto 12: clasificador heurístico simple (no pretende ser un motor de patrones
    exhaustivo) — Hammer/Pin bar, Engulfing, Doji, lo suficiente para confirmar/no un rebote."""
    body = abs(c - o)
    rng = h - l
    if rng <= 0:
        return None
    upper_wick = h - max(o, c)
    lower_wick = min(o, c) - l
    if body <= 0.1 * rng:
        return "doji"
    if lower_wick >= 2 * body and upper_wick <= 0.3 * max(body, 1e-9):
        return "hammer" if c >= o else "hanging_man"
    if upper_wick >= 2 * body and lower_wick <= 0.3 * max(body, 1e-9):
        return "shooting_star" if c <= o else "inverted_hammer"
    if prev_o is not None and prev_c is not None:
        prev_lo, prev_hi = min(prev_o, prev_c), max(prev_o, prev_c)
        cur_lo, cur_hi = min(o, c), max(o, c)
        if c > o and prev_c < prev_o and cur_lo <= prev_lo and cur_hi >= prev_hi:
            return "bullish_engulfing"
        if c < o and prev_c > prev_o and cur_lo <= prev_lo and cur_hi >= prev_hi:
            return "bearish_engulfing"
    return None


_PATTERN_POLARITY = {
    "hammer": "bullish",
    "inverted_hammer": "bullish",
    "bullish_engulfing": "bullish",
    "shooting_star": "bearish",
    "hanging_man": "bearish",
    "bearish_engulfing": "bearish",
    "doji": None,
}


def _group_episodes(indices: list[int], gap: int) -> list[list[int]]:
    if not indices:
        return []
    episodes = [[indices[0]]]
    for idx in indices[1:]:
        if idx - episodes[-1][-1] <= gap:
            episodes[-1].append(idx)
        else:
            episodes.append([idx])
    return episodes


def _walk_touches(
    slope: float,
    intercept: float,
    dates: list[str],
    opens: list[float],
    highs: list[float],
    lows: list[float],
    closes: list[float],
    volumes: list[float],
    atr_arr: np.ndarray,
    avg_vol_arr: np.ndarray,
    config: SRConfig,
) -> dict | None:
    """Recorre la serie diaria completa contra la línea (slope, intercept) y arma, punto por
    punto: touches (punto 1/10), rebotes y rupturas (puntos 17/18), re-test (punto 19),
    confirmación por velas (12) y por volumen (13). La tolerancia de touch/ruptura es dinámica
    según el ATR de cada día (punto 10), no un % fijo.

    `atr_arr`/`avg_vol_arr` llegan como numpy arrays YA calculados (no `pd.Series`) a propósito:
    esta función se llama cientos de veces por candidato durante la optimización (punto 7,
    Nelder-Mead), y `pd.Series.iloc[i]` en un loop resultó ser, medido con cProfile, el cuello
    de botella real de todo el motor (cada acceso escalar pasa por la maquinaria completa de
    indexing de pandas) — más aún la media móvil de volumen, que antes se recalculaba desde
    cero en cada llamada aunque no dependa de (slope, intercept) en absoluto. Ambos se calculan
    UNA sola vez en detect_levels() y se pasan ya resueltos."""
    n = len(closes)
    day_indices = np.arange(n)
    line_values = slope * day_indices + intercept
    closes_arr = np.asarray(closes, dtype=float)
    valid_atr = ~np.isnan(atr_arr) & (atr_arr > 0)
    touch_mask = valid_atr & (np.abs(closes_arr - line_values) <= config.atr_tolerance_pct * atr_arr)
    raw_touch_idx = np.nonzero(touch_mask)[0].tolist()
    episodes = _group_episodes(raw_touch_idx, config.episode_gap_bars)
    if not episodes:
        return None

    rebounds = 0
    breaks = 0
    retested = False
    touch_volumes: list[float] = []
    candle_confirmations = 0
    volume_confirmations = 0
    rebound_count_for_volume = 0
    rebound_magnitudes_atr: list[float] = []
    last_break_side: int | None = None

    for ep in episodes:
        start, end = ep[0], ep[-1]
        touch_volumes.extend(volumes[i] or 0.0 for i in ep)
        if start == 0:
            continue  # no hay "antes" para saber de qué lado venía — no se puede resolver
        pre_value = _line_value(slope, intercept, start - 1)
        side_before = 1 if closes[start - 1] > pre_value else -1

        lookahead_idx = list(range(end + 1, min(n, end + 1 + config.breakout_confirm_bars)))
        if len(lookahead_idx) < config.breakout_confirm_bars:
            continue  # muy cerca del final del historial — no se puede confirmar, se descarta

        confirmed_break = True
        for j in lookahead_idx:
            atr_j = atr_arr[j]
            if np.isnan(atr_j) or atr_j <= 0:
                confirmed_break = False
                break
            dist_j = closes[j] - _line_value(slope, intercept, j)
            side_j = 1 if dist_j > 0 else -1
            if abs(dist_j) <= config.breakout_tolerance_pct * atr_j or side_j == side_before:
                confirmed_break = False
                break

        if confirmed_break:
            breaks += 1
            if last_break_side is not None and last_break_side != -side_before:
                retested = True
            last_break_side = -side_before
        else:
            rebounds += 1
            rebound_count_for_volume += 1
            # Tamaño del rebote (criterio nuevo, "reaction_magnitude"): qué tan lejos de la
            # línea llegó el precio en la misma ventana que ya se usa para confirmar/descartar
            # ruptura (lookahead_idx), en múltiplos de ATR — un rebote "fuerte" se aleja mucho
            # de la línea, uno "débil" apenas la toca y vuelve.
            mags = []
            for j in lookahead_idx:
                atr_j = atr_arr[j]
                if not np.isnan(atr_j) and atr_j > 0:
                    mags.append(abs(closes[j] - _line_value(slope, intercept, j)) / atr_j)
            if mags:
                rebound_magnitudes_atr.append(max(mags))
            polarity_needed = "bullish" if side_before < 0 else "bearish"
            if "candle_confirmation" in config.enabled_methods:
                for i in ep:
                    prev_o = opens[i - 1] if i > 0 else None
                    prev_c = closes[i - 1] if i > 0 else None
                    pattern = _classify_candle(opens[i], highs[i], lows[i], closes[i], prev_o, prev_c)
                    if pattern and _PATTERN_POLARITY.get(pattern) == polarity_needed:
                        candle_confirmations += 1
                        break
            if "volume_confirmation" in config.enabled_methods:
                ep_vol = float(np.mean([volumes[i] or 0.0 for i in ep]))
                baseline = avg_vol_arr[start - 1] if start > 0 else np.nan
                if not np.isnan(baseline) and baseline > 0 and ep_vol >= config.volume_confirmation_mult * baseline:
                    volume_confirmations += 1

    return dict(
        touches=len(episodes),
        rebounds=rebounds,
        breaks=breaks,
        retested=retested,
        touch_episodes=episodes,
        touch_volumes=touch_volumes,
        candle_confirmations=candle_confirmations,
        volume_confirmations=volume_confirmations,
        rebound_count_for_volume=rebound_count_for_volume,
        rebound_magnitudes_atr=rebound_magnitudes_atr,
    )


def _touch_optimization_objective(
    params: np.ndarray,
    dates: list[str],
    opens: list[float],
    highs: list[float],
    lows: list[float],
    closes: list[float],
    volumes: list[float],
    atr_arr: np.ndarray,
    avg_vol_arr: np.ndarray,
    all_vol_mean: float,
    config: SRConfig,
) -> float:
    """Punto 7 — la función objetivo EXACTA del spec del usuario (touches/rebotes/volumen/
    antigüedad a favor, rupturas/distancia promedio en contra). Distinta a propósito del score
    final 0-100 (punto 20, que usa sus propios pesos para RANKEAR niveles ya detectados) — esta
    solo sirve para REFINAR la pendiente/intercepto de una línea candidata."""
    slope, intercept = params
    walk = _walk_touches(slope, intercept, dates, opens, highs, lows, closes, volumes, atr_arr, avg_vol_arr, config)
    if walk is None or walk["touches"] == 0:
        return 1e6  # sin touches, la línea no es candidata — objetivo pésimo
    touches = walk["touches"]
    rebounds = walk["rebounds"]
    breaks = walk["breaks"]
    mean_vol = float(np.mean(walk["touch_volumes"])) if walk["touch_volumes"] else 0.0
    norm_volume = mean_vol / all_vol_mean if all_vol_mean else 0.0

    episodes = walk["touch_episodes"]
    age_bars = episodes[-1][-1] - episodes[0][0]
    distances = []
    for ep in episodes:
        for i in ep:
            atr_i = atr_arr[i]
            if not np.isnan(atr_i) and atr_i > 0:
                distances.append(abs(closes[i] - _line_value(slope, intercept, i)) / atr_i)
    avg_distance_atr = float(np.mean(distances)) if distances else 0.0

    proxy = (
        touches * 3.0
        + rebounds * 2.0
        + norm_volume * 1.0
        + age_bars * 0.01
        - breaks * 4.0
        - avg_distance_atr * 2.0
    )
    return -proxy


def _optimize_line(
    slope0: float,
    intercept0: float,
    dates: list[str],
    opens: list[float],
    highs: list[float],
    lows: list[float],
    closes: list[float],
    volumes: list[float],
    atr_arr: np.ndarray,
    avg_vol_arr: np.ndarray,
    all_vol_mean: float,
    config: SRConfig,
) -> tuple[float, float]:
    args = (dates, opens, highs, lows, closes, volumes, atr_arr, avg_vol_arr, all_vol_mean, config)
    initial_obj = _touch_optimization_objective(np.array([slope0, intercept0]), *args)

    # Sin límites, Nelder-Mead puede alejarse del candidato inicial hasta la línea "más fuerte"
    # de TODA la serie, sin importar si arrancó como soporte o resistencia — se vio en la
    # práctica: un soporte y una resistencia detectados por métodos distintos convergían al
    # mismo precio exacto, porque el objetivo no distingue de qué lado vino la línea. Acotar el
    # slope/intercept a un entorno del candidato inicial mantiene la optimización como un
    # REFINAMIENTO local (punto 7: "optimizar automáticamente la pendiente e intersección" de
    # ESA línea), no una búsqueda global de la mejor línea posible.
    atr_valid = atr_arr[~np.isnan(atr_arr)]
    atr_current = float(atr_valid[-1]) if atr_valid.size else float(np.std(closes) or 1.0)
    slope_bound = config.optimize_max_slope_shift_atr_mult * atr_current
    intercept_bound = config.optimize_max_intercept_shift_atr_mult * atr_current
    bounds = [
        (slope0 - slope_bound, slope0 + slope_bound),
        (intercept0 - intercept_bound, intercept0 + intercept_bound),
    ]
    try:
        res = minimize(
            _touch_optimization_objective,
            x0=np.array([slope0, intercept0]),
            args=args,
            method="Nelder-Mead",
            bounds=bounds,
            options={"maxiter": 60, "xatol": 1e-4, "fatol": 1e-3},
        )
    except Exception:
        return slope0, intercept0
    if res.success and res.fun < initial_obj:
        return float(res.x[0]), float(res.x[1])
    return slope0, intercept0


def _build_candidates_for_timeframe(
    tf: str,
    tf_prices: list[dict],
    daily_dates: list[str],
    config: SRConfig,
) -> list[_Candidate]:
    tf_dates = [p["date"] for p in tf_prices]
    tf_opens = [p["open"] for p in tf_prices]
    tf_highs = [p["high"] for p in tf_prices]
    tf_lows = [p["low"] for p in tf_prices]
    tf_closes = [p["close"] for p in tf_prices]

    lookback = {
        "daily": config.pivot_lookback_daily,
        "weekly": config.pivot_lookback_weekly,
        "monthly": config.pivot_lookback_monthly,
        "4h": config.pivot_lookback_4h,
        "1h": config.pivot_lookback_1h,
    }[tf]
    pivots = _detect_pivots(tf_dates, tf_highs, tf_lows, lookback)
    highs_p = [pv for pv in pivots if pv.kind == "high"]
    lows_p = [pv for pv in pivots if pv.kind == "low"]

    tf_atr = _atr_series(tf_highs, tf_lows, tf_closes, min(config.atr_period, max(2, len(tf_closes) - 1)))
    atr_est = float(tf_atr.dropna().iloc[-1]) if tf_atr.notna().any() else float(np.std(tf_closes) or 1.0)

    bars_per_unit = BARS_PER_UNIT[tf]

    def to_daily(slope_tf: float, intercept_tf: float, anchor_j: int) -> tuple[float, float] | None:
        anchor_date = tf_dates[anchor_j]
        daily_idx = _nearest_daily_index(daily_dates, anchor_date)
        if daily_idx is None:
            return None
        anchor_value = _line_value(slope_tf, intercept_tf, anchor_j)
        slope_d = slope_tf / bars_per_unit
        intercept_d = anchor_value - slope_d * daily_idx
        return slope_d, intercept_d

    candidates: list[_Candidate] = []
    last_j = len(tf_closes) - 1

    # --- horizontales: DBSCAN (punto 2) + KDE (punto 3) ---
    for side, side_pivots, kind in (("low", lows_p, "support"), ("high", highs_p, "resistance")):
        prices = [pv.price for pv in side_pivots]
        if "dbscan" in config.enabled_methods:
            for group_idx in _cluster_prices_dbscan(prices, config.dbscan_eps_atr_mult * atr_est, config.dbscan_min_samples):
                group_prices = [prices[i] for i in group_idx]
                level_price = float(np.mean(group_prices))
                candidates.append(
                    _Candidate(kind, 0.0, level_price, {"dbscan"}, {tf}, group_prices)
                )
        if "kde" in config.enabled_methods:
            for peak in _kde_peaks(prices, config.kde_bandwidth, config.kde_grid_points):
                nearby = [p for p in prices if abs(p - peak) <= config.dbscan_eps_atr_mult * atr_est]
                candidates.append(_Candidate(kind, 0.0, peak, {"kde"}, {tf}, nearby or [peak]))

    # --- diagonales: RANSAC/Theil-Sen/Huber (puntos 4/5) + Hough (punto 6) ---
    for side_pivots, kind in ((lows_p, "support"), (highs_p, "resistance")):
        if len(side_pivots) < config.min_pivots_for_trendline:
            continue
        xs = [float(pv.index) for pv in side_pivots]
        ys = [pv.price for pv in side_pivots]
        prices = ys
        fitters = {
            "ransac": _fit_ransac,
            "theilsen": _fit_theilsen,
            "huber": _fit_huber,
        }
        for method, fitter in fitters.items():
            if method not in config.enabled_methods:
                continue
            fit = fitter(xs, ys)
            if fit is None:
                continue
            slope_tf, intercept_tf = fit
            residual_std = float(np.std([y - _line_value(slope_tf, intercept_tf, x) for x, y in zip(xs, ys)]))
            daily_line = to_daily(slope_tf, intercept_tf, last_j)
            if daily_line is None:
                continue
            candidates.append(_Candidate(kind, daily_line[0], daily_line[1], {method}, {tf}, prices, residual_std))
        if "hough" in config.enabled_methods:
            fit = _hough_line(xs, ys, atr_est, config.hough_num_slopes, config.hough_slope_range_atr_mult)
            if fit is not None:
                residual_std = float(np.std([y - _line_value(fit[0], fit[1], x) for x, y in zip(xs, ys)]))
                daily_line = to_daily(fit[0], fit[1], last_j)
                if daily_line is not None:
                    candidates.append(_Candidate(kind, daily_line[0], daily_line[1], {"hough"}, {tf}, prices, residual_std))

    return candidates


def _merge_candidates(candidates: list[_Candidate], current_day_idx: int, atr_current: float, config: SRConfig) -> list[_Candidate]:
    """Fusiona candidatos del mismo tipo cuyo valor (hoy) y pendiente son parecidos — vienen de
    metodologías/temporalidades distintas mirando el mismo nivel real. El resultado promedia
    pendiente/intercepto y UNE los sets de métodos/temporalidades (así "multi_timeframe" cuenta
    cuántas temporalidades distintas realmente coincidieron acá, punto 11)."""
    merged: list[_Candidate] = []
    for kind in ("support", "resistance"):
        group = [c for c in candidates if c.kind == kind]
        group.sort(key=lambda c: _line_value(c.slope, c.intercept, current_day_idx))
        used = [False] * len(group)
        for i, c in enumerate(group):
            if used[i]:
                continue
            bucket = [c]
            used[i] = True
            value_i = _line_value(c.slope, c.intercept, current_day_idx)
            for j in range(i + 1, len(group)):
                if used[j]:
                    continue
                c2 = group[j]
                value_j = _line_value(c2.slope, c2.intercept, current_day_idx)
                slope_scale = max(abs(c.slope), abs(c2.slope), 1e-9)
                slope_close = abs(c.slope - c2.slope) / slope_scale <= 0.5 or (abs(c.slope) < 1e-6 and abs(c2.slope) < 1e-6)
                if abs(value_i - value_j) <= config.dbscan_eps_atr_mult * atr_current and slope_close:
                    bucket.append(c2)
                    used[j] = True
            avg_slope = float(np.mean([b.slope for b in bucket]))
            avg_intercept = float(np.mean([b.intercept for b in bucket]))
            methods: set[str] = set()
            timeframes: set[str] = set()
            prices: list[float] = []
            residuals = [b.residual_std for b in bucket if b.residual_std is not None]
            for b in bucket:
                methods |= b.methods
                timeframes |= b.timeframes
                prices.extend(b.contributing_prices)
            # max (no promedio) a propósito: fusionar un cluster ajustado con una diagonal más
            # ruidosa debe heredar la dispersión del miembro MENOS ajustado, no diluirla.
            merged_residual = max(residuals) if residuals else None
            merged.append(_Candidate(kind, avg_slope, avg_intercept, methods, timeframes, prices, merged_residual))
    return merged


def _score_level(
    cand: _Candidate,
    slope: float,
    intercept: float,
    dates: list[str],
    opens: list[float],
    highs: list[float],
    lows: list[float],
    closes: list[float],
    volumes: list[float],
    atr_arr: np.ndarray,
    avg_vol_arr: np.ndarray,
    volume_bins: np.ndarray | None,
    volume_hist: np.ndarray | None,
    vwaps: list[float],
    config: SRConfig,
) -> SRLevel | None:
    walk = _walk_touches(slope, intercept, dates, opens, highs, lows, closes, volumes, atr_arr, avg_vol_arr, config)
    if walk is None or walk["touches"] < config.min_touch_points:
        return None

    n = len(closes)
    current_idx = n - 1
    current_price = closes[-1]
    atr_valid = atr_arr[~np.isnan(atr_arr)]
    atr_current = float(atr_valid[-1]) if atr_valid.size else 0.0
    line_value_now = _line_value(slope, intercept, current_idx)

    # Sanity check: una línea diagonal (RANSAC/Theil-Sen/Huber/Hough) ajustada sobre varios AÑOS
    # de historia puede tener una pendiente que se ve chica día a día pero, extrapolada miles de
    # barras hasta hoy, da un precio sin sentido (negativo, o un múltiplo absurdo del rango
    # histórico real) — encontrado con datos de BTC de 5 años vía Binance: Hough eligió una
    # pendiente de -55.7/día que, sobre 1825 barras, aterrizaba en -$59,013. Nada en el fitting
    # en sí mismo lo evita (el bound del optimizador es relativo al candidato inicial, no
    # absoluto), así que se descarta acá, contra el rango de precios REALMENTE observado en los
    # datos — con margen generoso (5x) para no tirar niveles legítimos que se proyectan más allá
    # del historial.
    price_floor = min(closes) * config.sane_price_min_mult
    price_ceiling = max(closes) * config.sane_price_max_mult
    if line_value_now <= 0 or line_value_now < price_floor or line_value_now > price_ceiling:
        return None

    if cand.residual_std is not None:
        dispersion = cand.residual_std
    else:
        dispersion = float(np.std(cand.contributing_prices)) if len(cand.contributing_prices) > 1 else 0.0
    zone_half = max(config.atr_tolerance_pct * atr_current, dispersion)

    episodes = walk["touch_episodes"]
    first_touch_idx, last_touch_idx = episodes[0][0], episodes[-1][-1]
    age_bars = last_touch_idx - first_touch_idx
    touches, rebounds, breaks = walk["touches"], walk["rebounds"], walk["breaks"]

    # --- componentes puntuados (DEFAULT_WEIGHTS) ---
    touch_component = min(1.0, touches / config.touch_component_full_credit)
    # "Respect Rate": fracción de touches que el nivel sostuvo sin romperse — la misma cuenta que
    # "número de rupturas fallidas" del spec, expresada como ratio en vez de conteo crudo (el
    # conteo crudo ya vive en `rebounds`/SRLevel, no hace falta un componente aparte para eso).
    respect_rate_component = min(1.0, rebounds / max(1, touches))
    age_component = min(1.0, age_bars / config.age_full_credit_bars)
    # Tamaño promedio del rebote (NUEVO) — la pieza central del rediseño: mide qué tan lejos se
    # alejó el precio tras cada rebote, no solo si rebotó o no.
    reaction_magnitude_avg = float(np.mean(walk["rebound_magnitudes_atr"])) if walk["rebound_magnitudes_atr"] else 0.0
    reaction_magnitude_component = min(1.0, reaction_magnitude_avg / config.reaction_magnitude_full_credit_atr_mult)
    volume_during_rebounds_component = (
        walk["volume_confirmations"] / walk["rebound_count_for_volume"]
        if "volume_confirmation" in config.enabled_methods and walk["rebound_count_for_volume"] > 0
        else 0.0
    )
    # Timeframe donde fue detectado: jerarquía institucional→operativa (TIMEFRAME_IMPORTANCE) da
    # la base, más un bonus de confluencia (+10% por cada temporalidad adicional que coincide en
    # este mismo nivel) — reemplaza al viejo "multi_timeframe" (que solo miraba cantidad de
    # temporalidades, sin distinguir cuál).
    timeframe_weight_component = 0.0
    if "multi_timeframe" in config.enabled_methods and cand.timeframes:
        base_importance = max(TIMEFRAME_IMPORTANCE.get(tf, 0.5) for tf in cand.timeframes)
        confluence_bonus = 0.1 * (len(cand.timeframes) - 1)
        timeframe_weight_component = min(1.0, base_importance + confluence_bonus)

    # --- componentes informativos (peso 0 en DEFAULT_WEIGHTS, no suman al score) ---
    proximity_range = config.proximity_full_range_atr_mult * atr_current
    proximity_component = (
        max(0.0, 1.0 - abs(current_price - line_value_now) / proximity_range) if proximity_range > 0 else 0.0
    )
    candle_component = walk["candle_confirmations"] / touches if "candle_confirmation" in config.enabled_methods else 0.0
    volume_profile_component = 0.0
    if volume_bins is not None and volume_hist is not None and "volume_profile" in config.enabled_methods:
        nearest_bin = int(np.argmin(np.abs(volume_bins - line_value_now)))
        vmax = float(volume_hist.max())
        volume_profile_component = float(volume_hist[nearest_bin]) / vmax if vmax > 0 else 0.0
    vwap_confluence_component = 0.0
    if vwaps and "vwap_confluence" in config.enabled_methods and atr_current > 0:
        vwap_confluence_component = float(
            any(abs(v - line_value_now) <= config.atr_tolerance_pct * atr_current for v in vwaps)
        )

    components = {
        "reaction_magnitude": reaction_magnitude_component,
        "volume_during_rebounds": volume_during_rebounds_component,
        "respect_rate": respect_rate_component,
        "timeframe_weight": timeframe_weight_component,
        "age": age_component,
        "touch_points": touch_component,
        # informativos — no están en DEFAULT_WEIGHTS, .get(k, 0.0) los deja en peso 0
        "volume_profile": volume_profile_component,
        "candle_confirmation": candle_component,
        "proximity": proximity_component,
        "vwap_confluence": vwap_confluence_component,
    }
    raw_score = sum(components[k] * config.weights.get(k, 0.0) for k in components)
    if walk["retested"]:
        raw_score += config.retest_bonus

    penalty = breaks * config.penalties["breakout_penalty_per_break"]
    dispersion_ratio = dispersion / atr_current if atr_current > 0 else 0.0
    if dispersion_ratio > config.penalties["dispersion_threshold_atr_mult"]:
        penalty += config.penalties["dispersion_penalty"]
    if age_bars < config.penalties["short_lifespan_bars"]:
        penalty += config.penalties["short_lifespan_penalty"]

    confidence_score = max(0.0, min(100.0, raw_score - penalty))

    return SRLevel(
        kind=cand.kind,
        price=line_value_now,  # valor de la línea/nivel HOY, aunque sea diagonal — el "precio
        # central" que pide el spec (punto "Resultado esperado"); `slope`/`intercept` quedan
        # disponibles aparte para quien necesite la ecuación completa (ej. dibujar el gráfico).
        slope=slope,
        intercept=intercept,
        zone_low=line_value_now - zone_half,
        zone_high=line_value_now + zone_half,
        touches=touches,
        rebounds=rebounds,
        breaks=breaks,
        retested=walk["retested"],
        age_bars=age_bars,
        first_touch_date=dates[first_touch_idx],
        last_touch_date=dates[last_touch_idx],
        timeframes=sorted(cand.timeframes),
        methods=sorted(cand.methods),
        volume_at_level=float(np.mean(walk["touch_volumes"])) if walk["touch_volumes"] else 0.0,
        distance_to_price_pct=(current_price - line_value_now) / current_price if current_price else 0.0,
        avg_rebound_magnitude_atr=reaction_magnitude_avg,
        component_scores=components,
        confidence_score=confidence_score,
    )


def _detect_channels(levels: list[SRLevel], config: SRConfig) -> list[SRLevel]:
    """Punto 14: pares de líneas soporte/resistencia aproximadamente paralelas."""
    channels: list[SRLevel] = []
    supports = [lv for lv in levels if lv.kind == "support" and lv.slope is not None]
    resistances = [lv for lv in levels if lv.kind == "resistance" and lv.slope is not None]
    for s in supports:
        for r in resistances:
            if abs(s.slope) < 1e-9 and abs(r.slope) < 1e-9:
                continue  # dos horizontales no son un "canal" diagonal — ya están como niveles
            denom = max(abs(s.slope), abs(r.slope), 1e-9)
            if abs(s.slope - r.slope) / denom > config.channel_max_slope_diff_pct / 100:
                continue
            avg_slope = (s.slope + r.slope) / 2
            direction = "alcista" if avg_slope > 1e-9 else ("bajista" if avg_slope < -1e-9 else "lateral")
            channels.append(
                SRLevel(
                    kind="channel",
                    price=None,
                    slope=avg_slope,
                    intercept=None,
                    zone_low=None,
                    zone_high=None,
                    touches=s.touches + r.touches,
                    rebounds=s.rebounds + r.rebounds,
                    breaks=s.breaks + r.breaks,
                    retested=s.retested or r.retested,
                    age_bars=min(s.age_bars, r.age_bars),
                    first_touch_date=min(s.first_touch_date, r.first_touch_date),
                    last_touch_date=max(s.last_touch_date, r.last_touch_date),
                    timeframes=sorted(set(s.timeframes) | set(r.timeframes)),
                    methods=sorted(set(s.methods) | set(r.methods) | {"channel"}),
                    volume_at_level=(s.volume_at_level + r.volume_at_level) / 2,
                    distance_to_price_pct=0.0,
                    avg_rebound_magnitude_atr=(s.avg_rebound_magnitude_atr + r.avg_rebound_magnitude_atr) / 2,
                    component_scores={},
                    confidence_score=(s.confidence_score + r.confidence_score) / 2,
                    channel_direction=direction,
                    channel_support=s,
                    channel_resistance=r,
                )
            )
    channels.sort(key=lambda c: c.confidence_score, reverse=True)
    return channels[: config.max_channels]


def detect_levels(
    historical_prices: list[dict],
    config: SRConfig | None = None,
    intraday_4h_prices: list[dict] | None = None,
    intraday_1h_prices: list[dict] | None = None,
) -> list[SRLevel]:
    """Punto de entrada. `historical_prices` son las velas DIARIAS completas (necesita
    date/open/high/low/close/volume — un caché viejo sin "open" hace que esto devuelva []
    y se autocorrija en el próximo fetch, mismo patrón que ADX/OBV en speculation.py).

    `intraday_4h_prices`/`intraday_1h_prices` son opcionales y, a diferencia de "weekly"/
    "monthly" (que se arman reagregando `historical_prices` acá mismo, sin red), tienen que venir
    YA fetcheados por quien llama — esta función se mantiene pura (sin I/O), mismo patrón que
    `_evaluate_from_data()` en fair_value.py. Si "4h"/"1h" está en `config.timeframes` pero no se
    pasó la lista correspondiente (o vino vacía/corta), esa temporalidad simplemente se salta —
    no rompe el resto del pipeline."""
    config = config or SRConfig()
    dated = sorted(historical_prices, key=lambda p: p["date"])
    if len(dated) < 30:
        return []
    if any(p.get(k) is None for p in dated for k in ("open", "high", "low", "close", "volume")):
        return []

    dates = [p["date"] for p in dated]
    opens = [p["open"] for p in dated]
    highs = [p["high"] for p in dated]
    lows = [p["low"] for p in dated]
    closes = [p["close"] for p in dated]
    volumes = [p["volume"] for p in dated]
    n = len(closes)

    atr_arr = _atr_series(highs, lows, closes, config.atr_period).to_numpy()
    atr_valid = atr_arr[~np.isnan(atr_arr)]
    atr_current = float(atr_valid[-1]) if atr_valid.size else float(np.std(closes) or 1.0)
    # Calculados UNA sola vez acá — el motor de optimización llama a _walk_touches cientos de
    # veces por candidato, y ninguno de estos dos depende de (slope, intercept), así que
    # recalcularlos en cada llamada (como se hacía antes) era puro trabajo redundante que
    # cProfile mostró como el cuello de botella real del pipeline.
    avg_vol_arr = (
        pd.Series([v if v is not None else 0.0 for v in volumes])
        .rolling(config.volume_confirmation_avg_period)
        .mean()
        .to_numpy()
    )
    all_vol_mean = float(np.nanmean([v for v in volumes if v is not None])) or 1.0

    timeframes_to_run = config.timeframes if "multi_timeframe" in config.enabled_methods else ("daily",)

    # Las 3 temporalidades son independientes entre sí (cada una arma sus propios pivotes/
    # clusters/líneas sin leer nada de las otras) — ThreadPoolExecutor las corre en paralelo
    # aprovechando que DBSCAN/KDE/RANSAC/Theil-Sen/Huber (numpy/scipy/sklearn) liberan el GIL
    # durante su trabajo en C, mismo patrón que `_parallel_fetch()` en app.py para tareas
    # independientes por ticker.
    tf_inputs = []
    for tf in timeframes_to_run:
        if tf == "daily":
            tf_prices = dated
        elif tf == "4h":
            tf_prices = sorted(intraday_4h_prices, key=lambda p: p["date"]) if intraday_4h_prices else []
        elif tf == "1h":
            tf_prices = sorted(intraday_1h_prices, key=lambda p: p["date"]) if intraday_1h_prices else []
        else:
            tf_prices = _resample_ohlcv(dated, "W" if tf == "weekly" else "ME")
        if len(tf_prices) >= 10:
            tf_inputs.append((tf, tf_prices))

    all_candidates: list[_Candidate] = []
    if len(tf_inputs) > 1:
        with ThreadPoolExecutor(max_workers=len(tf_inputs)) as executor:
            futures = [executor.submit(_build_candidates_for_timeframe, tf, tf_prices, dates, config) for tf, tf_prices in tf_inputs]
            for future in futures:
                all_candidates.extend(future.result())
    else:
        for tf, tf_prices in tf_inputs:
            all_candidates.extend(_build_candidates_for_timeframe(tf, tf_prices, dates, config))

    if not all_candidates:
        return []

    merged = _merge_candidates(all_candidates, n - 1, atr_current, config)

    volume_bins = volume_hist = None
    if "volume_profile" in config.enabled_methods:
        volume_bins, volume_hist = _volume_profile(closes, volumes, config.volume_profile_bins)

    vwaps: list[float] = []
    if "vwap_confluence" in config.enabled_methods:
        vwaps = [
            v
            for w in config.vwap_windows_days
            if (v := _rolling_vwap(dates, highs, lows, closes, volumes, w)) is not None
        ]

    def _optimize_and_score(cand: _Candidate) -> SRLevel | None:
        if "optimize" in config.enabled_methods:
            slope, intercept = _optimize_line(
                cand.slope, cand.intercept, dates, opens, highs, lows, closes, volumes,
                atr_arr, avg_vol_arr, all_vol_mean, config,
            )
        else:
            slope, intercept = cand.slope, cand.intercept
        return _score_level(
            cand, slope, intercept, dates, opens, highs, lows, closes, volumes,
            atr_arr, avg_vol_arr, volume_bins, volume_hist, vwaps, config,
        )

    # El refinamiento por optimización (Nelder-Mead, ~100-200 evaluaciones de _walk_touches por
    # candidato) es el paso más caro de todo el pipeline — y cada candidato se optimiza de forma
    # completamente independiente de los demás, así que es el punto de mayor beneficio para
    # correr en paralelo (mismo razonamiento que arriba: numpy/scipy liberan el GIL).
    levels: list[SRLevel] = []
    if len(merged) > 1:
        with ThreadPoolExecutor(max_workers=min(len(merged), 8)) as executor:
            for level in executor.map(_optimize_and_score, merged):
                if level is not None:
                    levels.append(level)
    else:
        for cand in merged:
            level = _optimize_and_score(cand)
            if level is not None:
                levels.append(level)

    levels.sort(key=lambda lv: lv.confidence_score, reverse=True)
    top_levels = levels[: config.top_n]

    channels = _detect_channels(top_levels, config) if "channels" in config.enabled_methods else []
    return top_levels + channels


@dataclass
class LevelZoneReaction:
    horizon_days: int
    observations: int
    mean_return: float | None
    win_rate: float | None


def compute_level_zone_reactions(
    historical_prices: list[dict],
    levels: list[SRLevel],
    kind: str,
    score_threshold: float,
    horizons: list[int],
    min_observations: int = 15,
) -> list[LevelZoneReaction]:
    """Recorre TODA la serie diaria y mide el retorno futuro en los días en que el precio
    estuvo dentro de la zona de algún nivel `kind` ("support"/"resistance") con
    confidence_score >= score_threshold. Mismo patrón que `compute_regime_reactions` en
    speculation.py: el SET de tickers/tipos validados es una decisión fija de una investigación
    puntual (ver SR_VALIDATED_TICKERS en app.py), pero el número que devuelve esta función se
    recalcula en vivo contra el historial completo en cada corrida — igual que
    `current_bucket_reaction` en drawdown_dca.py."""
    dated = sorted(historical_prices, key=lambda p: p["date"])
    closes = [p["close"] for p in dated]
    n = len(closes)
    qualifying = [
        lv for lv in levels if lv.kind == kind and lv.confidence_score >= score_threshold and lv.zone_low is not None
    ]
    if not qualifying:
        return [LevelZoneReaction(h, 0, None, None) for h in horizons]

    near = np.zeros(n, dtype=bool)
    for i in range(n):
        price = closes[i]
        for lv in qualifying:
            if lv.zone_low <= price <= lv.zone_high:
                near[i] = True
                break

    closes_series = pd.Series(closes, dtype=float)
    near_series = pd.Series(near)
    reactions = []
    for horizon in horizons:
        forward_return = (closes_series.shift(-horizon) - closes_series) / closes_series
        mask = near_series & forward_return.notna()
        n_obs = int(mask.sum())
        if n_obs < min_observations:
            reactions.append(LevelZoneReaction(horizon, n_obs, None, None))
            continue
        vals = forward_return[mask]
        reactions.append(LevelZoneReaction(horizon, n_obs, float(vals.mean()), float((vals > 0).mean())))
    return reactions
