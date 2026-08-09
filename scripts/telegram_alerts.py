"""Manda una alerta de Telegram por cada ticker cuyo veredicto de valoración cambió desde la
última vez que se registró — corrida MANUAL (a propósito, ver decisión del usuario), no
programada. No es un cálculo nuevo: reusa evaluate_ticker()/summarize_signals() (los mismos que
Acciones ya llama) y record_verdict() (los mismos que Validación ya lee), así que correr este
script también llena `app_data/verdict_history.json` en días que nadie abrió el dashboard — no
dos fuentes de verdad del veredicto, una sola.

Uso:
    ./venv/Scripts/python.exe scripts/telegram_alerts.py

Requiere TELEGRAM_TOKEN y TELEGRAM_CHAT_ID en .env (ver .env.example) — creados gratis hablándole
a @BotFather en Telegram. Sin esto, igual que FMP_API_KEY, el script no falla: imprime un aviso y
sigue de largo (mismo patrón defensivo del resto de la app — un secret ausente no debe tumbar
nada). El primer registro de un ticker nunca notifica (no hay "antes" contra qué comparar) —
mismo criterio que el gráfico de historial de veredictos, que tampoco muestra nada con <2 puntos.
"""

import os
import sys
from pathlib import Path

import requests

# La consola de Windows suele quedar en cp1252, que no puede codificar los emoji de los prints
# de abajo (crashea con UnicodeEncodeError apenas el primer ⚠️/✅ llega a stdout) — a diferencia
# de Streamlit (corre en el navegador, UTF-8 siempre), este es un script de terminal.
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import TICKERS
from src.data.errors import DataError
from src.valuation.fair_value import evaluate_ticker, summarize_signals
from src.verdict_history import load_verdict_history, record_verdict

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
PROVIDER = os.environ.get("TELEGRAM_ALERTS_PROVIDER", "yfinance")

VERDICT_EMOJI = {"cheap": "🟢", "expensive": "🔴", "mixed": "🟡"}
VERDICT_LABEL_ES = {"cheap": "barata", "expensive": "cara", "mixed": "mixta"}


def enviar_telegram(mensaje: str) -> None:
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("  ⚠️ Faltan TELEGRAM_TOKEN o TELEGRAM_CHAT_ID en .env — no se mandó nada.")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        r = requests.post(
            url, data={"chat_id": TELEGRAM_CHAT_ID, "text": mensaje, "parse_mode": "HTML"}, timeout=10
        )
        if r.status_code != 200:
            print(f"  ⚠️ Error Telegram: {r.status_code} | {r.text[:200]}")
        else:
            print("  ✅ Telegram enviado")
    except Exception as exc:
        print(f"  ⚠️ Error Telegram: {exc}")


def formato_mensaje(ticker: str, summary: dict, price: float, verdicto_anterior: str) -> str:
    emoji = VERDICT_EMOJI[summary["verdict"]]
    antes = VERDICT_LABEL_ES[verdicto_anterior]
    return (
        f"{emoji} <b>{ticker}</b> cambió de veredicto: {antes} → <b>{VERDICT_LABEL_ES[summary['verdict']]}</b>\n"
        f"💰 Precio: <b>${price:,.2f}</b>\n"
        f"📊 {summary['headline']}\n"
        f"<i>Señal de valoración a largo plazo, no de timing — mirá el detalle por familia en el dashboard.</i>"
    )


def main() -> int:
    for ticker in TICKERS:
        historial = load_verdict_history(ticker)
        verdicto_anterior = historial[-1]["verdict"] if historial else None

        try:
            evaluation = evaluate_ticker(ticker, provider=PROVIDER)
        except (DataError, ValueError) as exc:
            print(f"  ⚠️ {ticker}: no se pudo evaluar ({exc})")
            continue

        summary = summarize_signals(evaluation)
        record_verdict(ticker, summary, evaluation.current_price)

        cambio = verdicto_anterior is not None and verdicto_anterior != summary["verdict"]
        print(f"  {ticker}: {summary['verdict']} ({'CAMBIÓ' if cambio else 'sin cambio'})")

        if cambio:
            enviar_telegram(formato_mensaje(ticker, summary, evaluation.current_price, verdicto_anterior))

    return 0


if __name__ == "__main__":
    sys.exit(main())
