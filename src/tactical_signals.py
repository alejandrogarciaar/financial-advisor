"""Registro central de señales TÁCTICAS ya validadas fuera de muestra — la única pieza que
`scripts/telegram_stock_signals.py`/`telegram_crypto_signals.py` conocen es `SIGNAL_REGISTRY` y
`run_ecosystem_signals()`. Ningún script sabe nada de drawdown/golden-cross/S-R/régimen
específicamente: agregar una estrategia nueva a este proyecto (o extender una existente a un
ticker nuevo) es agregar/editar UNA entrada acá, nunca tocar los 2 scripts — eso es lo que hace
que "toda estrategia que se valide" fluya automáticamente a Telegram por ecosistema, pedido
explícito del usuario.

Cada `SignalDefinition.subjects()` deriva su lista de QUÉ chequear directamente del diccionario
`_VALIDATED_*` correspondiente (nunca una tupla hardcodeada de tickers) — mismo principio "los
conteos de trabajo son dinámicos" que ya usa el resto de esta app (`len(selected)`, no
`len(TICKERS)`). Consecuencia real: si mañana se valida soporte de AAPL además de TSLA, o régimen
de SOL, ese ticker aparece solo con editar el diccionario validado — cero cambios de código en
este archivo ni en los scripts.

Las 4 familias de hoy (ver CLAUDE.md / `financial-advisor-speculation`'s design-history para el
detalle y los números de cada validación):

1. Franjas de caída (Portafolio, ecosistema "stocks") — `evaluate_drawdown_zone()`.
2. Golden Cross / Death Cross (ecosistema "stocks") — signo NO uniforme entre tickers.
3. Soporte/resistencia validado (Market Reaction Zone Engine, ecosistema "stocks") — hoy solo
   TSLA-soporte, generalizado a cualquier (ticker, tipo) que `STOCK_SR_VALIDATED_TICKERS` tenga.
4. Régimen "fuerte" (+ refinamiento RSI≥70, ecosistema "crypto") — hoy BTC/ETH, generalizado a
   cualquier ticker con al menos un combo en `REGIME_VALIDATED_COMBOS`.

`SR_VALIDATED_TICKERS` de Cripto (`src/ui/cripto.py`) está vacío hoy — no tiene entrada acá
todavía; el día que valide algo, agregar su propia `SignalDefinition` (ecosistema "crypto").
"""

from dataclasses import dataclass
from typing import Callable

from src.config import CRYPTO_BINANCE_SYMBOLS, PORTFOLIO_CDI_UNDERLYING
from src.data import binance_client, yfinance_client
from src.data.errors import DataError
from src.portfolio import average_buy_price_by_ticker, load_purchases, load_sales
from src.speculation import (
    RSI_OVERBOUGHT_THRESHOLD,
    classify_golden_cross_series,
    classify_regime_series,
    compute_golden_cross_reactions,
    compute_regime_reactions,
    compute_regime_rsi_reactions,
    compute_rsi,
)
from src.support_resistance import (
    compute_level_zone_reactions,
    daily_reference_config,
    detect_levels,
    find_qualifying_zone_hit,
    score_percentile_threshold,
)
from src.tactical_signal_state import record_state
from src.telegram_client import enviar_telegram
from src.ui.portfolio import evaluate_drawdown_zone
from src.ui.shared import SR_METHOD_LABELS
from src.ui.speculation import (
    GOLDEN_CROSS_VALIDATED_TICKERS,
    REGIME_RSI_OVERBOUGHT_VALIDATED_HORIZONS,
    REGIME_VALIDATED_COMBOS,
    STOCK_SR_VALIDATED_HORIZONS_DAYS,
    STOCK_SR_VALIDATED_SCORE_PERCENTILE,
    STOCK_SR_VALIDATED_TICKERS,
)
from src.verdict_history import load_verdict_history

# Reverso de PORTFOLIO_CDI_UNDERLYING (CDI -> (kind, underlying)): de un ticker subyacente
# (ej. "AAPL") al CDI que lo trackea en Portafolio (ej. "AAPLCO"), si existe. TSLA/UBER/BTC/ETH
# no tienen CDI — no hay holdings que mostrar para esos.
UNDERLYING_TO_CDI = {u: cdi for cdi, (_kind, u) in PORTFOLIO_CDI_UNDERLYING.items()}

ZONE_LABEL_ES = {
    "acumulacion": "zona de acumulación",
    "distribucion": "zona de distribución/venta",
    "rango": "una caída sin evidencia confirmada (en rango)",
}
ZONE_EMOJI = {"acumulacion": "🟢", "distribucion": "🔴", "rango": "🟡"}
REGIME_LABEL_ES = {"fuerte": "fuerte", "debil": "débil", "mixta": "mixta"}
REGIME_EMOJI = {"fuerte": "🟢", "debil": "🔴", "mixta": "🟡"}
VERDICT_LABEL_ES = {"cheap": "barata", "expensive": "cara", "mixed": "mixta"}

DISCLAIMER = "<i>Señal táctica validada fuera de muestra, no una recomendación.</i>"


# ──────────────────────────────────────────────────────────────────────────────
# Enriquecimientos (los 3 confirmados con el usuario) — reuso puro, nunca cambian qué dispara
# una alerta, solo qué texto acompaña al mensaje.
# ──────────────────────────────────────────────────────────────────────────────


def _verdict_context(ticker: str) -> str:
    """Último veredicto de valor justo ya registrado — sin volver a evaluar nada, lee
    `verdict_history.json` (mantenido por telegram_alerts.py o por el dashboard)."""
    historial = load_verdict_history(ticker)
    if not historial:
        return ""
    last = historial[-1]
    return (
        f"\n📊 Veredicto de valor justo ({last['date']}): "
        f"{VERDICT_LABEL_ES.get(last['verdict'], last['verdict'])} — {last['headline']}"
    )


def _portfolio_context(ticker: str) -> str:
    """Holdings reales si el usuario tiene compras del CDI de este ticker. Solo shares + costo
    promedio — no pide precio actual, no hace falta para esto."""
    cdi = UNDERLYING_TO_CDI.get(ticker)
    if cdi is None:
        return ""
    purchases = load_purchases()
    if purchases.empty:
        return ""
    sales = load_sales()
    purchased = int(purchases.loc[purchases["ticker"] == cdi, "shares"].sum())
    sold = int(sales.loc[sales["ticker"] == cdi, "shares"].sum()) if not sales.empty else 0
    shares = purchased - sold
    if shares <= 0:
        return ""
    avg = average_buy_price_by_ticker(purchases).get(cdi)
    avg_txt = f" a un costo promedio de ${avg:,.0f} COP" if avg is not None else ""
    unidad = "acción" if shares == 1 else "acciones"
    return f"\n💼 Tenés {shares} {unidad} de {cdi}{avg_txt}."


def _technical_context(closes: list[float], trend: str | None = "_compute") -> str:
    """RSI + tendencia, siempre 'para referencia' — descriptivo, no una señal en sí (mismo
    criterio que ADX/OBV en la UI). `trend` se puede pasar precalculado (régimen cripto ya lo
    tiene) para no recomputarlo."""
    rsi = compute_rsi(closes)
    rsi_txt = f"RSI {rsi:.0f}" if rsi is not None else "RSI no disponible"
    if trend == "_compute":
        regimes = classify_regime_series(closes)
        trend = regimes[-1] if regimes else None
    trend_txt = REGIME_LABEL_ES.get(trend, "no disponible") if trend else "no disponible"
    return f"\nPara referencia: {rsi_txt}, tendencia {trend_txt}."


# ──────────────────────────────────────────────────────────────────────────────
# El registro
# ──────────────────────────────────────────────────────────────────────────────


@dataclass
class TacticalSignalResult:
    state: str  # lo que se compara contra la corrida anterior para decidir si avisar
    message: str  # mensaje de Telegram ya armado (con los 3 enriquecimientos incluidos)


@dataclass
class SignalDefinition:
    key: str  # parte de la clave de dedupe en tactical_signal_state.json
    ecosystem: str  # "stocks" | "crypto" — lo único que filtran los 2 scripts
    label: str  # para el log de consola
    subjects: Callable[[], list[str]]
    # Normalmente un ticker ("AAPL"), pero puede ser un id compuesto ("TSLA:support") cuando una
    # familia necesita más granularidad que "uno por ticker" (ver _sr_subjects) — evaluate() es
    # lo único que necesita conocer la forma de sus propios subjects.
    evaluate: Callable[[str], "TacticalSignalResult | None"]


# --- Familia 1: franjas de caída (Portafolio) ---


def _drawdown_subjects() -> list[str]:
    return list(PORTFOLIO_CDI_UNDERLYING)


def _evaluate_drawdown(ticker: str) -> TacticalSignalResult | None:
    _kind, underlying = PORTFOLIO_CDI_UNDERLYING[ticker]
    evaluation = evaluate_drawdown_zone(ticker)
    if evaluation is None:
        return None

    r = evaluation.reaction
    evidencia = (
        f"Esta franja ({evaluation.bucket}) rindió, en promedio y confirmado fuera de "
        f"muestra, {r.mean_return:+.0%} a {r.horizon_days} días (tasa de acierto "
        f"{r.win_rate:.0%}, n={r.observations})."
        if r is not None and r.mean_return is not None
        else "Sin confirmación histórica suficiente para esta franja específica."
    )
    mensaje = (
        f"{ZONE_EMOJI[evaluation.zone]} <b>{underlying}</b> (CDI {ticker}) entró en "
        f"{ZONE_LABEL_ES[evaluation.zone]}\n"
        f"💰 Precio ({evaluation.basis_currency}): ${evaluation.snapshot.current_price:,.2f}\n"
        f"{evidencia}"
        f"{_verdict_context(underlying)}"
        f"{_portfolio_context(underlying)}"
        f"{_technical_context(evaluation.basis_closes)}\n"
        f"{DISCLAIMER}"
    )
    return TacticalSignalResult(state=evaluation.zone, message=mensaje)


# --- Familia 2: Golden Cross / Death Cross ---


def _golden_cross_subjects() -> list[str]:
    return list(GOLDEN_CROSS_VALIDATED_TICKERS)


def _evaluate_golden_cross(ticker: str) -> TacticalSignalResult | None:
    try:
        historical_prices, _meta = yfinance_client.get_historical_prices(ticker)
    except DataError:
        return None
    closes = [p["close"] for p in historical_prices]
    states = classify_golden_cross_series(closes)
    current_state = states[-1] if states else None
    if current_state is None:
        return None

    reactions = [
        r for r in compute_golden_cross_reactions(closes)
        if r.in_golden_cross == current_state and r.mean_return is not None
    ]
    detalle = " · ".join(
        f"{r.mean_return:+.1%} a {r.horizon_days}d (acierto {r.win_rate:.0%}, n={r.observations})"
        for r in reactions
    ) or "sin observaciones recientes suficientes"

    mensaje = (
        f"🟡 <b>{ticker}</b> pasó a {'golden cross' if current_state else 'death cross'} "
        "(SMA50 vs SMA200)\n"
        f"💰 Precio: ${closes[-1]:,.2f}\n"
        f"Retorno histórico validado en este estado: {detalle}.\n"
        "⚠️ El signo NO es uniforme entre tickers — este es el número medido para "
        f"{ticker} puntualmente, no una regla general de mercado."
        f"{_verdict_context(ticker)}"
        f"{_portfolio_context(ticker)}"
        f"{_technical_context(closes)}\n"
        f"{DISCLAIMER}"
    )
    return TacticalSignalResult(
        state="golden_cross" if current_state else "death_cross", message=mensaje
    )


# --- Familia 3: soporte/resistencia validado (Market Reaction Zone Engine) ---


def _sr_subjects() -> list[str]:
    """Un subject por (ticker, tipo) validado — hoy solo "TSLA:support", pero generalizado: si
    algún día se valida otro ticker o el otro tipo (resistance), aparece solo."""
    return [
        f"{ticker}:{kind}"
        for ticker, kinds in STOCK_SR_VALIDATED_TICKERS.items()
        for kind in sorted(kinds)
    ]


def _evaluate_sr(subject: str) -> TacticalSignalResult | None:
    ticker, kind = subject.split(":")
    try:
        historical_prices, _meta = yfinance_client.get_historical_prices(ticker)
    except DataError:
        return None
    closes = [p["close"] for p in historical_prices]
    if not closes:
        return None
    current_price = closes[-1]

    config = daily_reference_config(enabled_methods=set(SR_METHOD_LABELS.keys()), top_n=8, min_touch_points=3)
    levels = detect_levels(historical_prices, config, daily_prices=historical_prices)
    threshold = score_percentile_threshold(levels, kind, STOCK_SR_VALIDATED_SCORE_PERCENTILE)
    if threshold is None:
        return None

    hit_level = find_qualifying_zone_hit(levels, kind, current_price, threshold)
    if hit_level is None:
        return TacticalSignalResult(state="fuera_de_zona", message="")

    reactions = [
        r for r in compute_level_zone_reactions(historical_prices, levels, kind, threshold, STOCK_SR_VALIDATED_HORIZONS_DAYS)
        if r.mean_return is not None
    ]
    detalle = " · ".join(
        f"{r.mean_return:+.1%} a {r.horizon_days}d (win rate {r.win_rate:.0%}, {r.observations} casos)"
        for r in reactions
    ) or "sin observaciones recientes suficientes"
    kind_es = "soporte" if kind == "support" else "resistencia"

    mensaje = (
        f"{'🟢' if kind == 'support' else '🔴'} <b>{ticker}</b> entró en zona de {kind_es} "
        f"validada (score ≥ {threshold:.0f}, percentil {STOCK_SR_VALIDATED_SCORE_PERCENTILE:.0f})\n"
        f"💰 Precio: ${current_price:,.2f}\n"
        f"Retorno histórico validado: {detalle}."
        f"{_verdict_context(ticker)}"
        f"{_portfolio_context(ticker)}"
        f"{_technical_context(closes)}\n"
        f"{DISCLAIMER}"
    )
    return TacticalSignalResult(state="en_zona", message=mensaje)


# --- Familia 4: régimen "fuerte" cripto (+ refinamiento RSI≥70) ---


def _regime_subjects() -> list[str]:
    return [ticker for ticker, combos in REGIME_VALIDATED_COMBOS.items() if combos]


def _evaluate_crypto_regime(ticker: str) -> TacticalSignalResult | None:
    symbol = CRYPTO_BINANCE_SYMBOLS[ticker]
    try:
        historical_prices, _meta = binance_client.get_historical_prices(symbol)
    except DataError:
        return None
    closes = [p["close"] for p in historical_prices]
    regimes = classify_regime_series(closes)
    current_regime = regimes[-1] if regimes else None
    if current_regime is None:
        return None

    validated_horizons = {
        h for (regime, h) in REGIME_VALIDATED_COMBOS.get(ticker, set()) if regime == current_regime
    }
    if not validated_horizons:
        # Régimen real, pero sin evidencia validada PARA ESTE régimen puntual (ej. "débil"/"mixta"
        # cuando solo "fuerte" tiene combos) — se registra el estado igual (para detectar el
        # próximo cambio real) pero no hay nada que avisar.
        return TacticalSignalResult(state=current_regime, message="")

    reactions = [
        r for r in compute_regime_reactions(closes)
        if r.regime == current_regime and r.horizon_days in validated_horizons and r.mean_return is not None
    ]
    detalle = " · ".join(
        f"{r.mean_return:+.1%} a {r.horizon_days}d (acierto {r.win_rate:.0%}, n={r.observations})"
        for r in reactions
    ) or "sin observaciones recientes suficientes"

    refuerzo = ""
    rsi_validated = REGIME_RSI_OVERBOUGHT_VALIDATED_HORIZONS.get(ticker, set())
    if rsi_validated and current_regime == "fuerte":
        rsi = compute_rsi(closes)
        if rsi is not None and rsi >= RSI_OVERBOUGHT_THRESHOLD:
            rsi_reactions = [
                r for r in compute_regime_rsi_reactions(closes)
                if r.horizon_days in rsi_validated and r.mean_return is not None
            ]
            if rsi_reactions:
                rsi_detalle = " · ".join(
                    f"{r.mean_return:+.1%} a {r.horizon_days}d (acierto {r.win_rate:.0%}, n={r.observations})"
                    for r in rsi_reactions
                )
                refuerzo = (
                    f"\n🔥 Además sobrecomprado (RSI {rsi:.0f} ≥ {RSI_OVERBOUGHT_THRESHOLD:.0f}) — "
                    f"refinamiento validado solo para {ticker}: {rsi_detalle}."
                )

    horizontes_txt = ", ".join(str(h) for h in sorted(validated_horizons))
    mensaje = (
        f"{REGIME_EMOJI[current_regime]} <b>{ticker}</b> pasó a régimen "
        f"{REGIME_LABEL_ES[current_regime]}\n"
        f"💰 Precio: ${closes[-1]:,.2f}\n"
        f"Retorno histórico validado ({horizontes_txt} días): {detalle}."
        f"{refuerzo}"
        f"{_technical_context(closes, trend=current_regime)}\n"
        "<i>Sin veredicto de valor justo ni holdings trackeados para cripto en esta app.</i>\n"
        f"{DISCLAIMER}"
    )
    return TacticalSignalResult(state=current_regime, message=mensaje)


SIGNAL_REGISTRY: list[SignalDefinition] = [
    SignalDefinition("drawdown", "stocks", "Franjas de caída (Portafolio)", _drawdown_subjects, _evaluate_drawdown),
    SignalDefinition("golden_cross", "stocks", "Golden Cross / Death Cross", _golden_cross_subjects, _evaluate_golden_cross),
    SignalDefinition("sr", "stocks", "Soporte/resistencia validado", _sr_subjects, _evaluate_sr),
    SignalDefinition("regime", "crypto", "Régimen fuerte/débil/mixta", _regime_subjects, _evaluate_crypto_regime),
]


def run_ecosystem_signals(ecosystem: str) -> None:
    """Corre TODAS las definiciones de `SIGNAL_REGISTRY` cuyo `ecosystem` coincide — agregar una
    definición nueva a la lista de arriba (una estrategia nueva, o una extensión de una
    existente a otro ticker vía su propio diccionario `_VALIDATED_*`) es lo único que hace falta
    para que aparezca acá; esta función no cambia."""
    for definition in SIGNAL_REGISTRY:
        if definition.ecosystem != ecosystem:
            continue
        print(f"{definition.label}:")
        for subject in definition.subjects():
            result = definition.evaluate(subject)
            if result is None:
                print(f"  {subject}: sin datos/historial suficiente")
                continue

            _anterior, cambio = record_state(f"{subject}:{definition.key}", result.state)
            print(f"  {subject}: {result.state} ({'CAMBIÓ' if cambio else 'sin cambio'})")
            if cambio and result.message:
                enviar_telegram(result.message)
