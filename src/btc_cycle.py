"""Variables de "fondo de ciclo" de BTC — cómputo puro, sin I/O ni Streamlit.

Dos familias, medidas contra los 4 pisos de ciclo completos (2011-11-18, 2015-01-14, 2018-12-15,
2022-11-21) en `scripts/btc_bottom_study.py`:
- **Zona de fondo** (valuación extrema): MVRV < 1, Mayer < 0,8, precio bajo la media de 200
  semanas, caída ≥ 75% desde el máximo, RSI semanal < 30, calendario (300–450 días desde el
  máximo), miedo extremo ≤ 15, y la combinada (≥3 de las 5 primeras de valuación).
- **Confirmación** (la recuperación ya arrancó): recuperar la media de 200 semanas, MVRV que
  vuelve sobre 1, hash ribbon (hashrate 30d vuelve sobre 60d tras capitulación), recuperar la
  media de 200 días.

Con solo 4 pisos esto NO es una señal validada fuera de muestra como las del resto de la
pestaña: es descripción histórica de qué tan seguido cada variable acompañó un piso. Los
umbrales son los estándar de la literatura, no optimizados acá.
"""

from dataclasses import dataclass
from datetime import timedelta

import pandas as pd

STUDY_DATE = "2026-11-20"

# Resultado de scripts/btc_bottom_study.py el STUDY_DATE. "pisos" = pisos detectados (señal
# activa a ±180 días del piso) / pisos con datos; "falsas" = episodios a más de 1 año de cualquier
# piso; "precision" = % de los días con señal que cayeron en la zona de fondo (±180 días del piso
# y a menos de +25% de él — solo 4% de todos los días lo están); "gana_365" = % de días con señal
# en que BTC estaba más alto 365 días después; "dolor" = mediana de la peor caída extra en esos
# 365 días; "retraso" (confirmación) = días después del piso y % sobre el mínimo al dispararse.
STUDY_RESULTS = {
    "combined": dict(pisos="4/4", falsas=1, precision=0.44, gana_365=1.00, dolor=-0.10),
    "mvrv": dict(pisos="4/4", falsas=1, precision=0.32, gana_365=0.99, dolor=-0.10),
    "wma200": dict(pisos="2/3", falsas=1, precision=0.32, gana_365=1.00, dolor=-0.06),
    "calendar": dict(pisos="4/4", falsas=0, precision=0.35, gana_365=0.91, dolor=-0.10),
    "drawdown": dict(pisos="4/4", falsas=0, precision=0.24, gana_365=1.00, dolor=-0.06),
    "mayer": dict(pisos="4/4", falsas=2, precision=0.23, gana_365=0.75, dolor=-0.31),
    "rsi_w": dict(pisos="3/4", falsas=1, precision=0.17, gana_365=1.00, dolor=-0.14),
    "fear": dict(pisos="2/2", falsas=4, precision=0.13, gana_365=0.62, dolor=-0.25),
    "reclaim_wma200": dict(pisos="2/3", falsas=1, gana_365=1.00, retraso="1 a 116 días · +21% a +74%"),
    "mvrv_up": dict(pisos="4/4", falsas=1, gana_365=0.93, retraso="44 a 178 días · +26% a +152%"),
    "hash_ribbon": dict(pisos="4/4", falsas=7, gana_365=0.95, retraso="26 a 104 días · +14% a +95%"),
    "reclaim_sma200": dict(pisos="4/4", falsas=5, gana_365=0.78, retraso="53 a 166 días · +26% a +237%"),
}
BASELINE_ZONE_SHARE = 0.04
LIVE_CONFIRM_MIN_DRAWDOWN = 0.50
_MESES = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]


def fecha_es(d) -> str:
    return f"{d.day} {_MESES[d.month - 1]} {d.year}"
BASELINE_GANA_365 = 0.96  # retorno mediano a 365d de cualquier día: +96% (no un %, ver UI)


@dataclass
class CycleIndicator:
    key: str
    family: str            # "zona" | "confirmación"
    label: str
    today: str             # valor de hoy, ya formateado
    threshold: str         # condición de fondo / confirmación, en texto
    active: bool | None    # None = sin dato
    trigger_price: float | None  # precio de BTC que la activaría (o la confirmaría), si aplica


def build_cycle_frame(price_mvrv: list[dict], hashrate: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(price_mvrv).assign(date=lambda d: pd.to_datetime(d["date"])).set_index("date").sort_index()
    df = df[~df.index.duplicated(keep="last")]
    if hashrate:
        hr = pd.DataFrame(hashrate).assign(date=lambda d: pd.to_datetime(d["date"])).set_index("date")["hashrate"]
        df["hashrate"] = hr[~hr.index.duplicated(keep="last")].reindex(df.index).ffill()
    else:
        df["hashrate"] = float("nan")
    df["ath"] = df.price.cummax()
    df["dd"] = df.price / df.ath - 1
    df["sma200"] = df.price.rolling(200).mean()
    df["wma200"] = df.price.rolling(1400).mean()  # 200 semanas en días
    df["mayer"] = df.price / df.sma200
    weekly = df.price.resample("W").last()
    delta = weekly.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / 14).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / 14).mean()
    df["rsi_w"] = (100 - 100 / (1 + gain / loss)).reindex(df.index).ffill()
    df["hr30"] = df.hashrate.rolling(30).mean()
    df["hr60"] = df.hashrate.rolling(60).mean()
    return df


def current_indicators(df: pd.DataFrame, fear_greed: int | None) -> list[CycleIndicator]:
    now = df.iloc[-1]
    price, mvrv = float(now.price), float(now.mvrv)
    realized = price / mvrv
    ath_date = df.index[df.price.eq(df.ath)][-1]
    days_since_ath = (df.index[-1] - ath_date).days
    window_start, window_end = (ath_date + timedelta(days=300)).date(), (ath_date + timedelta(days=450)).date()
    # Las confirmaciones solo cuentan tras una caída de ciclo. El estudio usó ≥70% (los 4 pisos
    # fueron de −77% a −85%); en vivo se usa ≥50% porque el ciclo actual tocó −53% y con 70% las
    # confirmaciones no podrían activarse nunca. El panel lo aclara.
    deep_recently = df.dd.iloc[-400:].min() <= -LIVE_CONFIRM_MIN_DRAWDOWN

    zone = [
        CycleIndicator("mvrv", "zona", "MVRV (precio vs. precio realizado)", f"{mvrv:.2f}", "< 1",
                       mvrv < 1, realized),
        CycleIndicator("wma200", "zona", "Precio vs. media de 200 semanas", f"{price / now.wma200 - 1:+.0%}",
                       "por debajo", price < now.wma200, float(now.wma200)),
        CycleIndicator("calendar", "zona", "Días desde el máximo del ciclo", f"{days_since_ath}",
                       f"300–450 ({fecha_es(window_start)} → {fecha_es(window_end)})",
                       300 <= days_since_ath <= 450 and now.dd <= -0.5, None),
        CycleIndicator("mayer", "zona", "Mayer (precio / media de 200 días)", f"{now.mayer:.2f}", "< 0,8",
                       now.mayer < 0.8, 0.8 * float(now.sma200)),
        CycleIndicator("drawdown", "zona", "Caída desde el máximo", f"{now.dd:.0%}", "≤ −75%",
                       now.dd <= -0.75, 0.25 * float(now.ath)),
        CycleIndicator("rsi_w", "zona", "RSI semanal", f"{now.rsi_w:.0f}", "< 30", now.rsi_w < 30, None),
        CycleIndicator("fear", "zona", "Miedo y Codicia", "—" if fear_greed is None else str(fear_greed), "≤ 15",
                       None if fear_greed is None else fear_greed <= 15, None),
    ]
    valuation_keys = {"mvrv", "wma200", "mayer", "drawdown", "rsi_w"}
    combined_n = sum(1 for i in zone if i.key in valuation_keys and i.active)
    zone.insert(0, CycleIndicator("combined", "zona", "Combinada (MVRV, Mayer, 200 semanas, caída, RSI)",
                                  f"{combined_n}/5", "≥ 3 de 5", combined_n >= 3, None))

    hr_ratio = now.hr30 / now.hr60 - 1 if pd.notna(now.hr30) and pd.notna(now.hr60) else None
    capitulated = bool((df.hr30.iloc[-120:] < df.hr60.iloc[-120:]).any())
    confirm = [
        CycleIndicator("reclaim_wma200", "confirmación", "Recuperar la media de 200 semanas",
                       f"{price / now.wma200 - 1:+.0%}", "cruza hacia arriba",
                       bool(deep_recently and price > now.wma200 and (df.price.iloc[-120:] < df.wma200.iloc[-120:]).any()),
                       float(now.wma200)),
        CycleIndicator("mvrv_up", "confirmación", "MVRV vuelve sobre 1", f"{mvrv:.2f}",
                       "cruza 1 hacia arriba (tras estar abajo)",
                       bool(deep_recently and mvrv > 1 and (df.mvrv.iloc[-120:] < 1).any()), realized),
        CycleIndicator("hash_ribbon", "confirmación", "Hash ribbon (hashrate 30d vs. 60d)",
                       "—" if hr_ratio is None else f"{hr_ratio:+.1%}" + (" · hubo capitulación" if capitulated else " · sin capitulación"),
                       "30d vuelve sobre 60d tras capitulación",
                       None if hr_ratio is None else bool(deep_recently and capitulated and hr_ratio > 0), None),
        CycleIndicator("reclaim_sma200", "confirmación", "Recuperar la media de 200 días",
                       f"{now.mayer - 1:+.0%}", "cruza hacia arriba",
                       bool(deep_recently and price > now.sma200 and (df.price.iloc[-120:] < df.sma200.iloc[-120:]).any()),
                       float(now.sma200)),
    ]
    return zone + confirm


def cycle_context(df: pd.DataFrame) -> dict:
    now = df.iloc[-1]
    ath_date = df.index[df.price.eq(df.ath)][-1]
    return {
        "price": float(now.price), "date": df.index[-1].date(), "ath": float(now.ath), "ath_date": ath_date.date(),
        "realized_price": float(now.price / now.mvrv), "drawdown": float(now.dd),
    }
