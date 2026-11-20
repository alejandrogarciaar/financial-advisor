"""¿Qué variables marcan / confirman un fondo de ciclo de BTC?

Pisos de ciclo completos (verdad de referencia): 2011-11-18, 2015-01-14, 2018-12-15, 2022-11-21.
Familia A "zona de fondo" (valuación extrema) y familia B "confirmación" (la recuperación arrancó).
Para cada condición: episodios, cuántos pisos detecta (recall), qué fracción de sus días cae en la
"zona de fondo" (precisión), retorno a 365d y dolor adicional (peor caída en los 365d siguientes).
Consistencia: ciclos 2011/2015 (train) vs 2018/2022 (test).

Sus números alimentan `STUDY_RESULTS` en `src/btc_cycle.py` (panel "🧱 ¿Fondo de ciclo?" de la
pestaña Cripto). Al re-correrlo, actualizar esa tabla y `STUDY_DATE` en el mismo cambio.
Correr local: `./venv/bin/python scripts/btc_bottom_study.py` (Binance bloquea entornos remotos).
"""
import numpy as np, pandas as pd, requests

cm = requests.get("https://community-api.coinmetrics.io/v4/timeseries/asset-metrics",
                  params=dict(assets="btc", metrics="CapMVRVCur,PriceUSD", frequency="1d",
                              page_size=10000, start_time="2010-07-18"), timeout=60).json()["data"]
d = pd.DataFrame(cm); d.index = pd.to_datetime(d.time).dt.tz_localize(None).dt.normalize()
px = d.PriceUSD.astype(float); mvrv = d.CapMVRVCur.astype(float)

hr = requests.get("https://api.blockchain.info/charts/hash-rate?timespan=all&format=json&sampled=false", timeout=60).json()["values"]
hr = pd.Series({pd.Timestamp(v["x"], unit="s").normalize(): v["y"] for v in hr}).sort_index()
hr = hr[~hr.index.duplicated()].reindex(px.index).ffill()

fg = requests.get("https://api.alternative.me/fng/?limit=0", timeout=30).json()["data"]
fg = pd.Series({pd.Timestamp(int(x["timestamp"]), unit="s").normalize(): int(x["value"]) for x in fg}).sort_index()
fg = fg[~fg.index.duplicated()].reindex(px.index)

rows, start = [], 1567296000000
while True:
    j = requests.get("https://fapi.binance.com/fapi/v1/fundingRate",
                     params=dict(symbol="BTCUSDT", startTime=start, limit=1000), timeout=30).json()
    if not j: break
    rows += j; start = j[-1]["fundingTime"] + 1
    if len(j) < 1000: break
fund = pd.Series([float(x["fundingRate"]) for x in rows],
                 index=[pd.Timestamp(x["fundingTime"], unit="ms").normalize() for x in rows]).groupby(level=0).mean()
fund7 = fund.rolling(7).mean().reindex(px.index)

df = pd.DataFrame({"px": px, "mvrv": mvrv, "hr": hr, "fg": fg, "fund7": fund7})
df["ath"] = df.px.cummax(); df["dd"] = df.px / df.ath - 1
df["sma200"] = df.px.rolling(200).mean(); df["mayer"] = df.px / df.sma200
df["wma200"] = df.px.rolling(1400).mean()
w = df.px.resample("W").last(); dlt = w.diff()
rsi_w = 100 - 100 / (1 + dlt.clip(lower=0).ewm(alpha=1/14).mean() / (-dlt.clip(upper=0)).ewm(alpha=1/14).mean())
df["rsi_w"] = rsi_w.reindex(df.index).ffill()
df["hr30"] = df.hr.rolling(30).mean(); df["hr60"] = df.hr.rolling(60).mean()
# días desde el último ATH
last_ath = pd.Series(df.index.where(df.px.eq(df.ath)), index=df.index).ffill()
df["days_since_ath"] = (pd.Series(df.index, index=df.index) - last_ath).dt.days

fwd = df.px.shift(-365)
df["r365"] = fwd / df.px - 1
df["r180"] = df.px.shift(-180) / df.px - 1
fmin = df.px[::-1].rolling(365, min_periods=1).min()[::-1].shift(-1)
df["pain365"] = fmin / df.px - 1

LOWS = [pd.Timestamp(x) for x in ["2011-11-18", "2015-01-14", "2018-12-15", "2022-11-21"]]
LOWS = [df.loc[l - pd.Timedelta(days=10): l + pd.Timedelta(days=10), "px"].idxmin() for l in LOWS]
zone = pd.Series(False, index=df.index)
for l in LOWS:
    lowp = df.px[l]
    win = (abs((df.index - l).days) <= 180) & (df.px <= 1.25 * lowp)
    zone |= win
EVAL_END = pd.Timestamp("2025-10-06")  # pico del ciclo actual: después de esto la zona no se conoce

# --- condiciones ---
was_deep = (df.dd.rolling(400, min_periods=1).min() <= -0.70)      # venimos de una caída de ciclo
cross_up = lambda a, b: (a > b) & (a.shift(1) <= b.shift(1))
A = {
    "MVRV < 1 (precio bajo el precio realizado)": df.mvrv < 1,
    "Mayer < 0,8 (precio < 80% de la media 200d)": df.mayer < 0.8,
    "Precio < media de 200 semanas": df.px < df.wma200,
    "Caída desde máximo ≤ −75%": df.dd <= -0.75,
    "RSI semanal < 30": df.rsi_w < 30,
    "Miedo extremo ≤ 15 (desde 2018)": df.fg <= 15,
    "Funding 7d negativo (desde 2019)": df.fund7 < 0,
    "Calendario: 300–450 días desde el máximo": df.days_since_ath.between(300, 450) & (df.dd <= -0.5),
}
B = {
    "Hash ribbon: hashrate 30d vuelve sobre 60d (tras caída ≥70%)": cross_up(df.hr30, df.hr60) & was_deep,
    "MVRV vuelve a cruzar sobre 1": cross_up(df.mvrv, pd.Series(1.0, index=df.index)) & was_deep,
    "Precio recupera la media de 200d (tras caída ≥70%)": cross_up(df.px, df.sma200) & was_deep,
    "Precio recupera la media de 200 semanas": cross_up(df.px, df.wma200) & was_deep,
}
score = sum(c.fillna(False).astype(int) for c in [df.mvrv < 1, df.mayer < 0.8, df.px < df.wma200, df.dd <= -0.75, df.rsi_w < 30])
A["Combinado: ≥3 de las 5 de valuación (MVRV, Mayer, 200s, caída, RSI)"] = score >= 3

def episodes(m):
    idx = np.where(m.values)[0]
    if len(idx) == 0: return []
    starts = [idx[0]] + [idx[i] for i in range(1, len(idx)) if idx[i] - idx[i-1] > 30]
    return [df.index[s] for s in starts]

def evaluate(name, m, kind):
    m = m.fillna(False)
    ev = m & (df.index <= EVAL_END)
    eps = episodes(ev)
    hit = [any(abs((e - l).days) <= 180 for e in df.index[ev]) for l in LOWS]
    avail = [l for l in LOWS if df.index[ev].min() is not pd.NaT and len(df.index[ev]) and df.index[ev].min() - pd.Timedelta(days=400) <= l] if ev.any() else []
    far = [e for e in eps if min(abs((e - l).days) for l in LOWS) > 365]
    sub = df[ev]
    tr = sub[sub.index < "2017-01-01"]; te = sub[sub.index >= "2017-01-01"]
    r = dict(señal=name, días=int(ev.sum()), episodios=len(eps),
             pisos=f"{sum(h for h, l in zip(hit, LOWS) if l in avail)}/{len(avail)}",
             falsas=len(far),
             precisión=(zone[ev]).mean() if ev.any() else np.nan,
             r365_med=sub.r365.median(), r365_gana=(sub.r365.dropna() > 0).mean() if sub.r365.notna().any() else np.nan,
             dolor_med=sub.pain365.median(), dolor_peor=sub.pain365.min(),
             r365_train=tr.r365.median() if len(tr) else np.nan, r365_test=te.r365.median() if len(te) else np.nan,
             hoy="SÍ" if bool(m.iloc[-1]) or (kind == "B" and m.iloc[-60:].any()) else "no")
    if kind == "B":
        lags = []
        for l in LOWS:
            after = df.index[ev & (df.index > l) & (df.index <= l + pd.Timedelta(days=400))]
            if len(after): lags.append(f"{(after[0]-l).days}d/+{df.px[after[0]]/df.px[l]-1:.0%}")
        r["retraso tras el piso"] = " · ".join(lags)
    return r

base = dict(r365=df.loc[df.index <= EVAL_END, "r365"].median())
print(f"Datos {df.index[0].date()} → {df.index[-1].date()} | BTC hoy {df.px.iloc[-1]:,.0f} | pisos: {[l.date().isoformat() for l in LOWS]}")
print(f"Referencia: retorno 365d mediano de CUALQUIER día = {base['r365']:+.0%}; días en 'zona de fondo' = {zone[df.index<=EVAL_END].mean():.0%}\n")
pd.set_option("display.width", 320); pd.set_option("display.max_columns", 30); pd.set_option("display.max_colwidth", 70)
for kind, fam in [("A", A), ("B", B)]:
    t = pd.DataFrame([evaluate(n, m, kind) for n, m in fam.items()])
    f = t.copy()
    for c in ["precisión", "r365_gana"]: f[c] = t[c].map(lambda v: "—" if pd.isna(v) else f"{v:.0%}")
    for c in ["r365_med", "dolor_med", "dolor_peor", "r365_train", "r365_test"]: f[c] = t[c].map(lambda v: "—" if pd.isna(v) else f"{v:+.0%}")
    print("ZONA DE FONDO" if kind == "A" else "CONFIRMACIÓN"); print(f.to_string(index=False)); print()

now = df.iloc[-1]
print("ESTADO HOY:")
print(f"  MVRV {now.mvrv:.2f} | Mayer {now.mayer:.2f} | precio vs media 200 semanas {now.px/now.wma200-1:+.0%} ({now.wma200:,.0f}) | caída desde máximo {now.dd:.0%}")
print(f"  RSI semanal {now.rsi_w:.0f} | miedo {now.fg:.0f} | funding 7d {now.fund7*100:+.4f}% | días desde máximo {now.days_since_ath:.0f}")
print(f"  hashrate 30d vs 60d {now.hr30/now.hr60-1:+.1%} | media 200d {now.sma200:,.0f} | precio realizado ≈ {now.px/now.mvrv:,.0f}")
print(f"  Combinado valuación: {int(score.iloc[-1])}/5")
