"""Envío de mensajes a Telegram — extraído de `scripts/telegram_alerts.py` cuando
`scripts/telegram_tactical_signals.py` apareció como segundo llamador real (mismo criterio de
este proyecto para extraer a un módulo compartido: recién cuando 2+ llamadores confirmados lo
piden, no antes). Sin dependencia de Streamlit — lo usan solo scripts de terminal.
"""

import os

import requests

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")


def enviar_telegram(mensaje: str) -> None:
    """Defensivo igual que FMP_API_KEY: sin TELEGRAM_TOKEN/TELEGRAM_CHAT_ID no falla, solo avisa
    y sigue de largo — un secret ausente no debe tumbar el script que lo llama."""
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
