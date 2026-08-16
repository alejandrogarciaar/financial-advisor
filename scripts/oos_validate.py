"""Reusable chronological out-of-sample validator.

This project has re-derived the SAME methodology from scratch, as a "throwaway scratchpad
script, not committed to the repo," at least 6-8 times: Fibonacci levels, the regime/RSI DCA
signal, ADX, OBV, the drawdown-bucket accumulation zone, and two rounds of the Market Reaction
Zone Engine's score. Every one of those investigations is, underneath, the same shape: split a
price history chronologically (train = older 60%, test = newer 40% — never a random split,
which would leak future information into "training"), compute forward returns at a few horizons
for days matching some condition vs. a baseline, and check whether the SIGN of the gap holds in
both halves. A result that only holds at one specific threshold/parameter and not at its
neighbors is exactly the multiple-comparisons fragility that has sunk every rejected signal in
this project (see CLAUDE.md / the `financial-advisor-*` skills' "Design history" for the full list) — so
this module also provides a threshold/parameter sweep helper to catch that automatically.

This is deliberately NOT a CLI that re-fetches data or re-implements each investigation's
condition logic — every investigation's "is this day interesting" rule is different (regime
classification, RSI level, drawdown bucket, S/R score percentile), so that part stays
project-specific. What's reusable is everything AROUND that rule: the split, the forward-return
math, the train/test sign-consistency check, and the sweep-for-fragility helper. A future
investigation should look like:

    from scripts.oos_validate import run_oos_validation, run_oos_validation_sweep

    condition = [classify_regime(...) == "fuerte" for ... in ...]  # project-specific
    result = run_oos_validation(dates, closes, condition, horizons_days=[5, 10, 20, 30])
    print(result.summary())

instead of a new script re-deriving `chronological_split`/forward-return math/sign-consistency
checks line by line.

`run_timeframe_sweep()` generalizes this one step further: instead of sweeping a strategy
PARAMETER against one fixed price series, it sweeps the same condition/horizon logic across
several TIMEFRAMES of the same ticker (e.g. 1h/4h/daily/weekly), each with its own dates/closes.
Only real for providers without yfinance's severe intraday history caps (Binance today — see
`src/data/binance_client.py`'s `BINANCE_INTERVALS`; yfinance's viable set is much narrower, see
`src/data/yfinance_client.py`'s `YFINANCE_VIABLE_INTERVALS`) — 1m/5m/15m/30m intraday stock data
is capped at 7-60 days by Yahoo, nowhere near enough for a 60/40 split with a real sample.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class HorizonResult:
    horizon_days: int
    train_condition_mean: float | None
    train_baseline_mean: float | None
    train_n: int
    test_condition_mean: float | None
    test_baseline_mean: float | None
    test_n: int

    @property
    def train_gap(self) -> float | None:
        if self.train_condition_mean is None or self.train_baseline_mean is None:
            return None
        return self.train_condition_mean - self.train_baseline_mean

    @property
    def test_gap(self) -> float | None:
        if self.test_condition_mean is None or self.test_baseline_mean is None:
            return None
        return self.test_condition_mean - self.test_baseline_mean

    @property
    def validated(self) -> bool:
        """Same sign in train and test, both with enough observations to have been computed
        at all (see `min_observations` in `run_oos_validation`) — the one rule this project
        always applies before calling anything an out-of-sample-confirmed effect."""
        train_gap, test_gap = self.train_gap, self.test_gap
        if train_gap is None or test_gap is None:
            return False
        return (train_gap > 0) == (test_gap > 0) and train_gap != 0


@dataclass
class OOSResult:
    horizons: list[HorizonResult] = field(default_factory=list)

    @property
    def all_validated(self) -> bool:
        """The project's standing bar: EVERY horizon tested must hold the same sign in train
        and test, not just some of them (a partial hit is exactly the "looks good until you
        check the next horizon" pattern that sank ADX)."""
        return bool(self.horizons) and all(h.validated for h in self.horizons)

    def summary(self) -> str:
        lines = []
        for h in self.horizons:
            # Plain ASCII, not emoji — this prints fine on a default Windows console (cp1252),
            # which raises UnicodeEncodeError on unichar emoji without explicit UTF-8 setup.
            mark = "[OK]" if h.validated else "[FAIL]"
            lines.append(
                f"{mark} {h.horizon_days}d: train_gap={_fmt(h.train_gap)} (n={h.train_n}), "
                f"test_gap={_fmt(h.test_gap)} (n={h.test_n})"
            )
        return "\n".join(lines)


def _fmt(x: float | None) -> str:
    return "n/a" if x is None else f"{x:+.2%}"


def chronological_split(n: int, train_frac: float = 0.6) -> tuple[slice, slice]:
    """Index-based split — never random. A random split would let a "training" fold contain
    days chronologically AFTER a "test" day, leaking future information the signal couldn't
    actually have had at decision time. `train_frac=0.6` is this project's standing convention
    (older 60% / newer 40%), used identically across every OOS check so far."""
    cut = int(n * train_frac)
    return slice(0, cut), slice(cut, n)


def _forward_returns(closes: list[float], horizon_days: int) -> list[float | None]:
    """closes[i + horizon_days] / closes[i] - 1, or None where the horizon runs past the end
    of the series (those days simply can't be scored yet)."""
    n = len(closes)
    out: list[float | None] = [None] * n
    for i in range(n - horizon_days):
        base = closes[i]
        if base:
            out[i] = closes[i + horizon_days] / base - 1.0
    return out


def _mean_and_n(values: list[float | None], mask: list[bool]) -> tuple[float | None, int]:
    selected = [v for v, m in zip(values, mask) if m and v is not None]
    if not selected:
        return None, 0
    return sum(selected) / len(selected), len(selected)


def run_oos_validation(
    dates: list[str],
    closes: list[float],
    condition: list[bool],
    horizons_days: list[int] = (5, 10, 20, 30),
    train_frac: float = 0.6,
    min_observations: int = 15,
    baseline_condition: list[bool] | None = None,
) -> OOSResult:
    """Chronological 60/40 split (or `train_frac`); for each horizon, compares the mean forward
    return on days where `condition[i]` is True against the mean over ALL days in that slice
    (the baseline) — same "excess return vs. unconditional mean" approach this project's RSI
    investigation used (a raw positive mean means nothing when the whole series has strong
    drift; the gap is what matters). A (train, test) cell with fewer than `min_observations`
    condition-matching days is reported as `None` (not silently zero) — same "absence of a
    validated signal is not evidence of a negative one" principle used throughout this project's
    skills.

    `baseline_condition` narrows what "the baseline" means: instead of every day in the slice,
    only the days it marks True. That's what a REFINEMENT check needs — "does this new signal add
    anything ON TOP of one we already trust?" — and it's the part every such investigation so far
    (the RSI-overbought refinement of the DCA regime, the Fear & Greed redundancy check) had to
    hand-roll because the harness only offered the unconditional mean. Pass the already-trusted
    signal here and the intersection as `condition`: a gap near zero then means the new signal is
    redundant, not that it's worthless on its own. `None` (default) keeps the unconditional
    baseline.

    `dates`/`closes`/`condition` must be the same length, in chronological order (oldest first).
    """
    n = len(closes)
    if not (len(dates) == n == len(condition)):
        raise ValueError("dates, closes, and condition must be the same length")
    if baseline_condition is not None and len(baseline_condition) != n:
        raise ValueError("baseline_condition must be the same length as closes")

    train_slice, test_slice = chronological_split(n, train_frac)
    horizons = []
    for h in horizons_days:
        fwd = _forward_returns(closes, h)

        def _cell(sl: slice, want_condition: bool) -> tuple[float | None, int]:
            mask = [
                (condition[i] if want_condition else (baseline_condition is None or baseline_condition[i]))
                for i in range(sl.start, sl.stop)
            ]
            mean, cnt = _mean_and_n(fwd[sl], mask)
            return (mean, cnt) if cnt >= min_observations else (None, cnt)

        train_cond_mean, train_n = _cell(train_slice, True)
        train_base_mean, _ = _cell(train_slice, False)
        test_cond_mean, test_n = _cell(test_slice, True)
        test_base_mean, _ = _cell(test_slice, False)

        horizons.append(
            HorizonResult(
                horizon_days=h,
                train_condition_mean=train_cond_mean,
                train_baseline_mean=train_base_mean,
                train_n=train_n,
                test_condition_mean=test_cond_mean,
                test_baseline_mean=test_base_mean,
                test_n=test_n,
            )
        )
    return OOSResult(horizons=horizons)


def run_oos_validation_sweep(
    dates: list[str],
    closes: list[float],
    condition_variants: dict[str, list[bool]],
    horizons_days: list[int] = (5, 10, 20, 30),
    train_frac: float = 0.6,
    min_observations: int = 15,
    baseline_condition: list[bool] | None = None,
) -> dict[str, OOSResult]:
    """Runs `run_oos_validation` once per named variant (e.g. 3 nearby score percentiles, or 3
    ADX thresholds) — the threshold-fragility check this project always applies before trusting
    a single-parameter result. A variant that validates alone but whose neighbors in this sweep
    don't is the exact multiple-comparisons signature that disqualified Fibonacci/ADX/OBV/the
    Market Reaction Zone Engine's first re-validation round — don't report just the one variant
    that happened to pass without also checking (and disclosing) its neighbors."""
    return {
        label: run_oos_validation(
            dates, closes, condition, horizons_days, train_frac, min_observations, baseline_condition
        )
        for label, condition in condition_variants.items()
    }


def run_timeframe_sweep(
    series_by_timeframe: dict[str, tuple[list[str], list[float]]],
    condition_fn,
    horizons_bars: list[int] = (5, 10, 20, 30),
    train_frac: float = 0.6,
    min_observations: int = 15,
) -> dict[str, OOSResult]:
    """Como `run_oos_validation_sweep`, pero la variable que cambia entre variantes es la
    TEMPORALIDAD, no un parámetro de la estrategia sobre una sola serie — cada temporalidad trae
    su propia serie de `dates`/`closes` (no se puede compartir una sola, a diferencia del sweep
    de parámetros de arriba), así que el llamador entrega un dict `{etiqueta: (dates, closes)}`
    ya armado (ver `src/data/binance_client.py`/`yfinance_client.py`'s funciones
    `get_historical_prices_multi_timeframe()` para construirlo) en vez de una sola serie
    compartida.

    `condition_fn(closes) -> list[bool]` se re-ejecuta una vez por temporalidad, sobre los
    closes de ESA temporalidad — tiene que tener sentido en cualquier granularidad (ej. un
    lookback en BARRAS, no en "días calendario"), y `horizons_bars` significa lo mismo: N barras
    hacia adelante en la temporalidad de esa serie, no N días — 20 barras en 1h son ~20 horas, 20
    barras en diario son ~20 días. Mismo criterio de "no reportar solo la que dio bien" que
    `run_oos_validation_sweep`: una temporalidad que valida sola, mientras las vecinas
    (1h/4h/daily, por ejemplo) no, es la misma fragilidad de múltiples comparaciones aplicada al
    eje de temporalidad en vez de al de parámetro."""
    results = {}
    for label, (dates, closes) in series_by_timeframe.items():
        condition = condition_fn(closes)
        results[label] = run_oos_validation(dates, closes, condition, horizons_bars, train_frac, min_observations)
    return results


if __name__ == "__main__":
    # Self-test with synthetic data (deterministic, no network/provider dependency) — proves the
    # split/statistics logic itself is correct, independent of any real investigation. A real
    # investigation imports run_oos_validation/run_oos_validation_sweep directly (see module
    # docstring) rather than running this file.
    import random

    random.seed(0)
    n = 500
    closes = [100.0]
    for _ in range(n - 1):
        closes.append(closes[-1] * (1 + random.gauss(0.0005, 0.01)))
    dates = [f"2020-01-{(i % 28) + 1:02d}" for i in range(n)]
    # Condition deliberately correlated with a positive future-return bump, so the self-test has
    # something real to detect.
    condition = [False] * n
    for i in range(n):
        if i + 10 < n:
            condition[i] = closes[i + 10] > closes[i] * 1.02

    result = run_oos_validation(dates, closes, condition, horizons_days=[5, 10, 20])
    print(result.summary())
    print("all_validated:", result.all_validated)

    # Mismo self-test, ahora vía run_timeframe_sweep() con 2 "temporalidades" sintéticas idénticas
    # (mismos closes/dates para ambas — alcanza para probar que el plumbing por-serie funciona,
    # no hace falta generar 2 series realmente distintas para esto) — condition_fn recibe los
    # closes y re-deriva la misma condición.
    def _condition_fn(cl):
        out = [False] * len(cl)
        for i in range(len(cl)):
            if i + 10 < len(cl):
                out[i] = cl[i + 10] > cl[i] * 1.02
        return out

    sweep = run_timeframe_sweep(
        {"tf_a": (dates, closes), "tf_b": (dates, closes)}, _condition_fn, horizons_bars=[5, 10, 20]
    )
    for label, r in sweep.items():
        print(f"\n[{label}]\n{r.summary()}\nall_validated: {r.all_validated}")
