"""Orquesta la obtención de datos y calcula las señales de valor justo, independientes entre sí.

`evaluate_ticker` hace I/O (consulta al proveedor) y delega el cálculo puro a
`_evaluate_from_data`, que no toca la red — así el mismo cálculo se puede reusar
para backtesting con datos históricos truncados (ver src/backtest.py).
"""

from dataclasses import dataclass, field

from src.config import EQUITY_RISK_PREMIUM, RISK_FREE_RATE, DCF_PROJECTION_YEARS
from src.data import fmp_client, yfinance_client
from src.valuation.analyst_view import AnalystView, build_analyst_view
from src.valuation.book_value import BookValueResult, evaluate_book_value
from src.valuation.dcf import (
    DCFScenarios,
    DCFSensitivity,
    estimate_wacc,
    run_dcf_scenarios,
    run_dcf_sensitivity,
)
from src.valuation.graham import GrahamResult, evaluate_graham
from src.valuation.graham_growth import GrahamGrowthResult, evaluate_graham_growth
from src.valuation.growth import GrowthResult, evaluate_growth
from src.valuation.lynch_category import LynchCategory, classify_lynch_category
from src.valuation.multiples import MultipleBand, evaluate_multiple
from src.valuation.quality import (
    QualityResult,
    compute_roic_series,
    estimate_effective_tax_rate,
    evaluate_quality,
)
from src.valuation.risk_return import RiskReturnResult, evaluate_risk_return
from src.valuation.solvency import SolvencyResult, evaluate_solvency
from src.valuation.trend import TrendResult, evaluate_trend

# ambos módulos exponen la misma interfaz (get_quote, get_profile, ...) con los
# mismos nombres de campo, así que el resto de esta función es agnóstica al proveedor
PROVIDERS = {"fmp": fmp_client, "yfinance": yfinance_client}

# umbrales de margen (fair_value - price) / price para clasificar cada señal por separado.
# alineados al margen de seguridad clásico de Graham/Buffett para el inversor defensivo
ZONE_THRESHOLDS = {
    "Acumulación fuerte": 0.30,
    "Acumulación": 0.15,
    "Precio justo": -0.05,
    # cualquier valor por debajo del último umbral -> "Sobrevalorado"
}
CHEAP_ZONES = ("Acumulación fuerte", "Acumulación")

# Las 6 fórmulas de precio NO son 6 opiniones independientes: PEG, Número de Graham y la
# fórmula de crecimiento de Graham son variaciones del mismo insumo (EPS × algún múltiplo
# derivado de crecimiento/valor en libros), muy correlacionadas entre sí. Contarlas como 6
# votos separados infla artificialmente el "consenso". Las agrupamos en 3 familias
# genuinamente distintas y cada familia vota una sola vez (con la mediana de sus miembros).
SIGNAL_FAMILIES = {
    "Flujo de caja (DCF)": ["dcf_margin"],
    "Valor patrimonial": ["book_value_margin"],
    "Múltiplos de ganancias (P/E, PEG, Graham x2)": [
        "multiple_margin",
        "growth_margin",
        "graham_margin",
        "graham_growth_margin",
    ],
}


@dataclass
class TickerEvaluation:
    ticker: str
    current_price: float
    dcf: DCFScenarios
    multiple: MultipleBand
    book_value: BookValueResult
    growth: GrowthResult | None       # None si la empresa no tiene ganancias/crecimiento positivo (PEG no aplica)
    graham: GrahamResult | None       # None si EPS o BVPS no son positivos
    graham_growth: GrahamGrowthResult | None  # None si no hay EPS/crecimiento positivo (fórmula 8.5+2g de Graham)
    quality: QualityResult | None     # None si no se pudo estimar capital invertido (filtro, no señal de precio)
    lynch_category: LynchCategory     # heurística de categoría (fast grower / stalwart / cíclica / ...)
    analyst_view: AnalystView | None  # consenso de Wall Street (contexto, no señal propia) — solo yfinance
    solvency: SolvencyResult | None   # filtro de apalancamiento/riesgo de crédito (no es señal de precio)
    trend: TrendResult | None         # EMA de 55 períodos — filtro técnico/momentum, no señal de precio
    risk_return: RiskReturnResult     # CAGR/volatilidad/Sharpe/máxima caída — 100% histórico, no es señal de precio
    dcf_sensitivity: DCFSensitivity   # qué mueve más el DCF: ¿crecimiento o WACC?
    avg_reported_fcf: float | None       # FCF promedio (3 años) tal como lo reporta la empresa
    avg_stock_comp: float | None         # stock-based compensation promedio (3 años)
    sbc_adjusted_fcf: float | None       # FCF promedio menos SBC — más conservador, ajusta por dilución
    dcf_margin: float                 # (fair_value - price) / price
    multiple_margin: float
    book_value_margin: float
    growth_margin: float | None
    graham_margin: float | None
    graham_growth_margin: float | None
    dcf_zone: str
    multiple_zone: str
    book_value_zone: str
    growth_zone: str | None
    graham_zone: str | None
    graham_growth_zone: str | None
    data_as_of: str             # ISO timestamp del dato más viejo usado en esta evaluación
    is_stale: bool              # True si al menos un endpoint falló y se usó su caché en disco
    stale_reason: str | None    # motivo del último fallo de API que forzó a usar caché
    market_closed: bool = False # True si se usó caché porque el mercado está cerrado (no por una falla)
    historical_prices: list[dict] = field(default_factory=list)


def classify_margin(margin: float) -> str:
    if margin >= ZONE_THRESHOLDS["Acumulación fuerte"]:
        return "Acumulación fuerte"
    if margin >= ZONE_THRESHOLDS["Acumulación"]:
        return "Acumulación"
    if margin >= ZONE_THRESHOLDS["Precio justo"]:
        return "Precio justo"
    return "Sobrevalorado"


def _median(values: list[float]) -> float:
    values = sorted(values)
    n = len(values)
    mid = n // 2
    return values[mid] if n % 2 else (values[mid - 1] + values[mid]) / 2


def summarize_signals(evaluation: TickerEvaluation) -> dict:
    """Triangula por FAMILIA (no por fórmula individual): 3 familias genuinamente distintas
    (flujo de caja, valor patrimonial, múltiplos de ganancias), cada una resumida con la
    mediana de sus miembros disponibles. El filtro de calidad (ROIC vs WACC) NO entra acá —
    es una pregunta distinta ('¿crea valor?') y mezclarla distorsionaría el conteo de precio."""
    family_zones = {}
    for family, fields in SIGNAL_FAMILIES.items():
        margins = [getattr(evaluation, f) for f in fields]
        margins = [m for m in margins if m is not None]
        if margins:
            family_zones[family] = classify_margin(_median(margins))

    zones = list(family_zones.values())
    cheap = sum(1 for z in zones if z in CHEAP_ZONES)
    expensive = sum(1 for z in zones if z == "Sobrevalorado")
    fair = sum(1 for z in zones if z == "Precio justo")
    total = len(zones)

    if cheap == total:
        headline = f"Las {total} familias de métodos coinciden: se ve barata"
        verdict = "cheap"
    elif expensive == total:
        headline = f"Las {total} familias de métodos coinciden: se ve cara"
        verdict = "expensive"
    elif cheap > expensive and cheap > fair:
        headline = f"{cheap} de {total} familias de métodos ven la acción barata"
        verdict = "cheap"
    elif expensive > cheap and expensive > fair:
        headline = f"{expensive} de {total} familias de métodos ven la acción cara"
        verdict = "expensive"
    else:
        headline = "Familias de métodos mixtas — sin conclusión clara"
        verdict = "mixed"

    return {
        "cheap": cheap,
        "fair": fair,
        "expensive": expensive,
        "total": total,
        "headline": headline,
        "verdict": verdict,
        "family_zones": family_zones,
    }


def _evaluate_from_data(
    ticker: str,
    current_price: float,
    quote: dict,
    profile: dict,
    income_statements: list[dict],
    cash_flows: list[dict],
    balance_sheets: list[dict],
    key_metrics: list[dict],
    historical_prices: list[dict],
    data_as_of: str,
    is_stale: bool,
    stale_reason: str | None,
    analyst_view: AnalystView | None = None,
    market_closed: bool = False,
) -> TickerEvaluation:
    fcf_series = [cf.get("freeCashFlow") for cf in cash_flows]

    total_debt = balance_sheets[0].get("totalDebt")
    cash = balance_sheets[0].get("cashAndCashEquivalents")
    # algunos proveedores no calculan netDebt para empresas con posición de caja neta
    # (ej. yfinance en AMZN/NVDA/TSLA); en vez de asumir deuda neta = 0 en silencio,
    # lo derivamos de deuda bruta y caja cuando ambos datos existen
    net_debt = balance_sheets[0].get("netDebt")
    if net_debt is None:
        net_debt = (total_debt or 0) - (cash or 0) if (total_debt is not None or cash is not None) else 0

    shares_outstanding = income_statements[0].get("weightedAverageShsOut") or (
        quote.get("marketCap", 0) / current_price
    )

    # Método 1: DCF por escenarios (pesimista/base/optimista + valor esperado ponderado)
    wacc = estimate_wacc(profile.get("beta"), RISK_FREE_RATE, EQUITY_RISK_PREMIUM)
    dcf_result = run_dcf_scenarios(
        historical_fcf=fcf_series,
        shares_outstanding=shares_outstanding,
        net_debt=net_debt,
        wacc=wacc,
        projection_years=DCF_PROJECTION_YEARS,
    )

    # Análisis de sensibilidad: ¿el DCF se mueve más por el crecimiento o por el WACC?
    dcf_sensitivity = run_dcf_sensitivity(
        historical_fcf=fcf_series,
        shares_outstanding=shares_outstanding,
        net_debt=net_debt,
        wacc=wacc,
        projection_years=DCF_PROJECTION_YEARS,
    )

    # Disclosure: FCF reportado vs. ajustado por stock-based compensation (dilución).
    # NO reemplaza el DCF de arriba — el SBC-adjusted suele dar un valor MÁS bajo, no corrige
    # el sesgo conservador que mostró el backtest, es una segunda mirada más estricta.
    recent_fcf = [cf.get("freeCashFlow") for cf in cash_flows[:3] if cf.get("freeCashFlow") is not None]
    recent_sbc = [cf.get("stockBasedCompensation") for cf in cash_flows[:3] if cf.get("stockBasedCompensation") is not None]
    avg_reported_fcf = sum(recent_fcf) / len(recent_fcf) if recent_fcf else None
    avg_stock_comp = sum(recent_sbc) / len(recent_sbc) if recent_sbc else None
    sbc_adjusted_fcf = (
        avg_reported_fcf - avg_stock_comp if avg_reported_fcf is not None and avg_stock_comp is not None else None
    )

    current_eps = income_statements[0].get("eps") or 0
    current_pe = current_price / current_eps if current_eps else 0

    # Método 2: múltiplos (P/E propio histórico)
    historical_pe = [
        (1 / m["earningsYield"]) if m.get("earningsYield") else None for m in key_metrics
    ]
    multiple_result = evaluate_multiple(
        current_eps=current_eps,
        current_pe=current_pe,
        historical_pe=historical_pe,
    )

    # Método 3: valor patrimonial (book value)
    total_equity = balance_sheets[0].get("totalStockholdersEquity") or 0
    book_value_result = evaluate_book_value(total_equity, shares_outstanding)

    # Método 4: crecimiento (PEG / PEGY de Lynch) — no siempre aplica (sin ganancias/crecimiento positivo)
    eps_series = [inc.get("eps") for inc in income_statements]
    dividend_yield = (profile.get("lastDividend") or 0) / current_price if current_price else 0.0
    try:
        growth_result = evaluate_growth(current_eps, current_price, eps_series, dividend_yield=dividend_yield)
    except ValueError:
        growth_result = None

    # Método 5: número de Graham — no siempre aplica (requiere EPS y BVPS positivos)
    try:
        graham_result = evaluate_graham(current_eps, book_value_result.book_value_per_share)
    except ValueError:
        graham_result = None

    # Método 6: fórmula de crecimiento de Graham (8.5 + 2g, ajustada por tasa de interés)
    try:
        graham_growth_result = evaluate_graham_growth(current_eps, eps_series, RISK_FREE_RATE)
    except ValueError:
        graham_growth_result = None

    # Filtro de calidad (no es señal de precio): ROIC vs WACC, + tendencia de los últimos años
    ebit = income_statements[0].get("ebit")
    tax_rate = estimate_effective_tax_rate(
        income_statements[0].get("incomeTaxExpense"), income_statements[0].get("incomeBeforeTax")
    )
    invested_capital = (total_debt or 0) + total_equity - (cash or 0)
    roic_series = compute_roic_series(income_statements, balance_sheets)
    try:
        quality_result = evaluate_quality(ebit, tax_rate, invested_capital, wacc, roic_series=roic_series)
    except (ValueError, TypeError):
        quality_result = None

    # Filtro de solvencia (no es señal de precio): ¿puede pagar su deuda?
    solvency_result = evaluate_solvency(
        ebit=ebit,
        interest_expense=income_statements[0].get("interestExpense"),
        total_debt=total_debt,
        ebitda=income_statements[0].get("ebitda"),
    )

    # Filtro de tendencia (no es señal de precio): EMA de 55 períodos
    trend_result = evaluate_trend(historical_prices, current_price)

    # Riesgo/retorno histórico (no es señal de precio): CAGR/volatilidad/Sharpe/máxima caída
    risk_return_result = evaluate_risk_return(historical_prices, RISK_FREE_RATE)

    # Categoría de Lynch: avisa cuándo PEG/Graham-growth se están aplicando fuera de su dominio
    lynch_category = classify_lynch_category(
        growth_result.eps_growth_rate if growth_result else None, profile.get("beta")
    )

    dcf_margin = (dcf_result.fair_value_per_share - current_price) / current_price
    multiple_margin = (
        (multiple_result.fair_value - current_price) / current_price
        if multiple_result.fair_value
        else 0.0
    )
    book_value_margin = (book_value_result.book_value_per_share - current_price) / current_price
    growth_margin = (
        (growth_result.fair_value - current_price) / current_price if growth_result else None
    )
    graham_margin = (
        (graham_result.fair_value - current_price) / current_price if graham_result else None
    )
    graham_growth_margin = (
        (graham_growth_result.fair_value - current_price) / current_price if graham_growth_result else None
    )

    return TickerEvaluation(
        ticker=ticker,
        current_price=current_price,
        dcf=dcf_result,
        multiple=multiple_result,
        book_value=book_value_result,
        growth=growth_result,
        graham=graham_result,
        graham_growth=graham_growth_result,
        quality=quality_result,
        lynch_category=lynch_category,
        analyst_view=analyst_view,
        solvency=solvency_result,
        trend=trend_result,
        risk_return=risk_return_result,
        dcf_sensitivity=dcf_sensitivity,
        avg_reported_fcf=avg_reported_fcf,
        avg_stock_comp=avg_stock_comp,
        sbc_adjusted_fcf=sbc_adjusted_fcf,
        dcf_margin=dcf_margin,
        multiple_margin=multiple_margin,
        book_value_margin=book_value_margin,
        growth_margin=growth_margin,
        graham_margin=graham_margin,
        graham_growth_margin=graham_growth_margin,
        dcf_zone=classify_margin(dcf_margin),
        multiple_zone=classify_margin(multiple_margin),
        book_value_zone=classify_margin(book_value_margin),
        growth_zone=classify_margin(growth_margin) if growth_margin is not None else None,
        graham_zone=classify_margin(graham_margin) if graham_margin is not None else None,
        graham_growth_zone=classify_margin(graham_growth_margin) if graham_growth_margin is not None else None,
        data_as_of=data_as_of,
        is_stale=is_stale,
        stale_reason=stale_reason,
        market_closed=market_closed,
        historical_prices=historical_prices,
    )


def evaluate_ticker(ticker: str, provider: str = "fmp") -> TickerEvaluation:
    client = PROVIDERS[provider]
    quote, quote_meta = client.get_quote(ticker)
    profile, profile_meta = client.get_profile(ticker)
    income_statements, income_meta = client.get_income_statement(ticker)
    cash_flows, cf_meta = client.get_cash_flow_statement(ticker)
    balance_sheets, bs_meta = client.get_balance_sheet(ticker)
    key_metrics, km_meta = client.get_key_metrics(ticker)
    historical_prices, hist_meta = client.get_historical_prices(ticker)

    metas = [quote_meta, profile_meta, income_meta, cf_meta, bs_meta, km_meta, hist_meta]
    is_stale = any(m["from_cache"] for m in metas)
    data_as_of = min(m["fetched_at"] for m in metas)
    stale_reason = next((m["error"] for m in metas if m["from_cache"]), None)
    # yfinance no tiene el concepto (no es de cuota limitada), así que su meta no trae esta key
    market_closed = is_stale and all(m.get("market_closed", False) for m in metas if m["from_cache"])

    # contexto opcional: no todos los proveedores lo tienen (FMP lo bloquea en el plan free),
    # y su ausencia no debe tumbar el resto de la evaluación
    analyst_view = None
    try:
        raw_view, _ = client.get_analyst_view(ticker)
        analyst_view = build_analyst_view(
            price_target_mean=raw_view.get("priceTargetMean"),
            price_target_median=raw_view.get("priceTargetMedian"),
            forward_growth_rate=raw_view.get("forwardGrowth"),
            eps_estimate_now=raw_view.get("epsEstimateNow"),
            eps_estimate_90d_ago=raw_view.get("epsEstimate90dAgo"),
        )
    except Exception:
        pass

    return _evaluate_from_data(
        ticker=ticker,
        current_price=quote["price"],
        quote=quote,
        profile=profile,
        income_statements=income_statements,
        cash_flows=cash_flows,
        balance_sheets=balance_sheets,
        key_metrics=key_metrics,
        historical_prices=historical_prices,
        data_as_of=data_as_of,
        is_stale=is_stale,
        stale_reason=stale_reason,
        analyst_view=analyst_view,
        market_closed=market_closed,
    )


QUALITY_PREMIUM_SPREAD = 0.05  # 5 puntos porcentuales de ROIC por encima del WACC


def quality_context_note(evaluation: TickerEvaluation, summary: dict) -> str | None:
    """Cruza el veredicto de precio con el filtro de calidad: si la triangulación dice 'cara'
    pero la empresa reinvierte muy por encima de su costo de capital, parte de esa prima puede
    estar justificada — el backtest mostró que ignorar esto nos hizo llamar 'caras' a acciones
    que siguieron subiendo con fuerza."""
    q = evaluation.quality
    if q is None:
        return None
    spread = q.roic - q.wacc
    if summary["verdict"] == "expensive" and spread >= QUALITY_PREMIUM_SPREAD:
        return (
            f"Aunque se ve cara, {evaluation.ticker} reinvierte con un ROIC ({q.roic:.0%}) muy por "
            f"encima de su costo de capital ({q.wacc:.0%}) — parte de esa prima puede estar justificada "
            "por la calidad del negocio."
        )
    if summary["verdict"] == "cheap" and spread < 0:
        return (
            f"{evaluation.ticker} se ve barata, pero destruye valor al reinvertir (ROIC {q.roic:.0%} < "
            f"costo de capital {q.wacc:.0%}) — vale la pena revisar por qué antes de asumir que es una "
            "oportunidad."
        )
    return None


def multiple_quality_context_note(evaluation: TickerEvaluation) -> str | None:
    """Cruza el veredicto de la familia de Múltiplos ESPECÍFICAMENTE (no el veredicto general)
    con la tendencia de ROIC de los últimos años. La reversión al P/E histórico propio no
    distingue si el múltiplo subió por especulación o porque el negocio genuinamente mejoró —
    el backtest mostró que esa familia, junto con el DCF, marcó 'cara' de forma unánime a
    varios compounders de calidad que siguieron subiendo. El ROIC no predice nada del futuro;
    solo describe si la mejora/deterioro de fundamentales ya ocurrió, dato real que la
    reversión a la media no incorpora."""
    q = evaluation.quality
    if q is None or q.roic_trend == "Sin suficiente historia":
        return None

    if evaluation.multiple_zone == "Sobrevalorado":
        if q.roic_trend == "Mejorando":
            return (
                f"El múltiplo de {evaluation.ticker} está sobre su promedio histórico, pero su ROIC "
                f"viene mejorando ({', '.join(f'{r:.0%}' for r in q.roic_series[:3])} últimos años) — "
                "puede ser una recalificación real del negocio, no solo optimismo del mercado."
            )
        if q.roic_trend == "Deteriorándose":
            return (
                f"El múltiplo de {evaluation.ticker} está sobre su promedio histórico Y su ROIC viene "
                "empeorando — doble alerta: cara por historia, y la calidad del negocio va en la "
                "dirección contraria."
            )
    elif evaluation.multiple_zone in CHEAP_ZONES and q.roic_trend == "Deteriorándose":
        return (
            f"El múltiplo de {evaluation.ticker} está bajo su promedio histórico, pero su ROIC viene "
            "empeorando — antes de asumir oportunidad, vale la pena preguntarse si bajó porque el "
            "negocio se deterioró."
        )
    return None


TREND_STRETCH_THRESHOLD = 0.15  # 15% de distancia a la EMA de 55 para considerarla "estirada"


def trend_context_note(evaluation: TickerEvaluation, summary: dict) -> str | None:
    """Cruza el veredicto de precio (fundamental, lento) con la tendencia de corto plazo
    (técnica, rápida). No predice hacia dónde va el precio — señala cuando comprar/vender HOY
    implicaría hacerlo justo después de un movimiento fuerte en la dirección contraria a lo que
    dicen los fundamentales, dato relevante para decidir SI conviene entrar de una sola vez o
    de a poco, no para decidir SI conviene entrar."""
    tr = evaluation.trend
    if tr is None:
        return None
    if summary["verdict"] == "cheap" and tr.price_vs_ema <= -TREND_STRETCH_THRESHOLD:
        return (
            f"{evaluation.ticker} se ve barata, pero además cae con fuerza ({tr.price_vs_ema:+.0%} vs. "
            "su tendencia de 55 días) — vale la pena entender qué la está castigando antes de asumir "
            "que es una oportunidad."
        )
    if summary["verdict"] == "expensive" and tr.price_vs_ema >= TREND_STRETCH_THRESHOLD:
        return (
            f"{evaluation.ticker} se ve cara, y además sube con fuerza ({tr.price_vs_ema:+.0%} vs. su "
            "tendencia de 55 días) — comprar ahora es después de un rally; promediar la compra en el "
            "tiempo reduce ese riesgo puntual."
        )
    return None


def compare_providers(ticker: str) -> dict:
    """Corre el mismo ticker contra los 2 proveedores y avisa si el veredicto de triangulación
    difiere — para detectar cuándo el 'barata/cara' depende más del origen del dato que de los
    fundamentales reales."""
    evaluations = {}
    errors = {}
    for name in PROVIDERS:
        try:
            evaluations[name] = evaluate_ticker(ticker, provider=name)
        except Exception as exc:
            errors[name] = str(exc)

    summaries = {name: summarize_signals(ev) for name, ev in evaluations.items()}
    verdicts = {name: s["verdict"] for name, s in summaries.items()}
    agree = len(set(verdicts.values())) <= 1 if verdicts else None

    return {
        "evaluations": evaluations,
        "summaries": summaries,
        "errors": errors,
        "agree": agree,
    }
