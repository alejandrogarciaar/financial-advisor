"""Bot de Telegram para registrar compras/ventas de Portafolio sin abrir el navegador.

Agnóstico a la máquina: corre en cualquier Windows que tenga este repo clonado, el paquete
privado `portfolio` instalado (ver README.md — es el mismo requisito que ya tiene
`streamlit run app.py`) y acceso SSH de push al `origin` de este repo. No hace falta nada
adicional: usa `requests`/`python-dotenv`, que ya están en requirements.txt — mismo patrón que
`src/data/*_client.py` (HTTP directo contra la API pública, sin SDK de bots).

Reusa el mismo bot de Telegram que `market-signals-telegram` (que solo envía alertas y nunca
escucha comandos, así que no hay conflicto de dos procesos sobre el mismo token) — configurar
TELEGRAM_TOKEN/TELEGRAM_CHAT_ID en el `.env` de este repo con los mismos valores.

Flujo: /start → botones [Compra]/[Venta] → ticker → cantidad → precio → comisión → fecha →
confirmar. Al confirmar: git pull (para no validar contra datos locales viejos), valida con
validate_purchases()/validate_sales() (las mismas que usa la UI y scripts/add_sale.py), guarda
con save_purchases()/save_sales() (dispara el sync existente hacia el paquete `portfolio`) y,
a diferencia de la UI, hace también commit+push automático de portfolio_data/*.json al origin
de ESTE repo — sin este paso, actualizar desde el celular no evita tener que volver a una PC a
pushear a mano. Solo responde a TELEGRAM_CHAT_ID; ignora en silencio cualquier otro chat.

Uso: ./venv/Scripts/python.exe scripts/telegram_portfolio_bot.py   (se deja corriendo; Ctrl+C
para parar). Solo un proceso puede hacer polling del mismo bot a la vez — si ya está corriendo
en otra máquina, Telegram devuelve 409 y este proceso lo reporta y sigue reintentando.
"""

import os
import subprocess
import sys
import time
from datetime import date as date_cls
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
import requests
from dotenv import load_dotenv

load_dotenv()

# Sin esto, stdout queda bufferizado por completo cuando no hay una TTY real detrás (pipe,
# redirección a archivo, Task Scheduler) — un loop de larga duración que solo imprime de vez en
# cuando podía tardar minutos en mostrar la primera línea.
sys.stdout.reconfigure(line_buffering=True)

from src.config import PORTFOLIO_CDI_TICKERS
from src.portfolio import (
    DEFAULT_COMMISSION_COP,
    load_purchases,
    load_sales,
    save_purchases,
    save_sales,
    validate_purchases,
    validate_sales,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
TICKERS = list(PORTFOLIO_CDI_TICKERS.keys())

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
API_BASE = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"

ACTION_LABEL = {"compra": "COMPRA", "venta": "VENTA"}

# Estado de la conversación por chat_id — en memoria; un reinicio del proceso simplemente hace
# que el usuario tenga que volver a empezar (bot de un solo usuario autorizado, no hay nada que
# perder salvo una carga a medio completar que todavía no se guardó).
_sessions: dict[int, dict] = {}


# --- Bot API (HTTP directo, sin SDK) -----------------------------------------------------------


def _api(method: str, **params) -> dict:
    resp = requests.post(f"{API_BASE}/{method}", json=params, timeout=40)
    resp.raise_for_status()
    return resp.json()


def send_message(chat_id: int, text: str, buttons: Optional[list[list[tuple[str, str]]]] = None) -> None:
    kwargs = {"chat_id": chat_id, "text": text}
    if buttons:
        kwargs["reply_markup"] = {
            "inline_keyboard": [
                [{"text": label, "callback_data": data} for label, data in row] for row in buttons
            ]
        }
    _api("sendMessage", **kwargs)


def answer_callback(callback_query_id: str) -> None:
    _api("answerCallbackQuery", callback_query_id=callback_query_id)


# --- Git helpers (solo sobre ESTE repo) --------------------------------------------------------


def _pull_latest() -> Optional[str]:
    """Devuelve None si el pull salió bien, o un mensaje de error para mostrar por Telegram."""
    result = subprocess.run(
        ["git", "pull", "--ff-only"], cwd=REPO_ROOT, capture_output=True, text=True
    )
    if result.returncode != 0:
        return result.stderr.strip() or result.stdout.strip() or "git pull falló"
    return None


def _push_portfolio_data(action: str, ticker: str) -> Optional[str]:
    """Commit+push de portfolio_data/*.json al origin de este repo. Devuelve un mensaje de error
    para mostrar por Telegram si algo falla, o None si salió bien (o no había nada que commitear).
    """
    try:
        subprocess.run(
            ["git", "add", "portfolio_data/purchases.json", "portfolio_data/sales.json"],
            cwd=REPO_ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        staged = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=REPO_ROOT)
        if staged.returncode == 0:
            return None
        subprocess.run(
            ["git", "commit", "-m", f"Telegram: registra {action} de {ticker}"],
            cwd=REPO_ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        subprocess.run(["git", "push"], cwd=REPO_ROOT, check=True, capture_output=True, text=True)
        return None
    except subprocess.CalledProcessError as exc:
        return (exc.stderr or exc.stdout or str(exc)).strip()


# --- Flujo del menú ------------------------------------------------------------------------------


def _reset(chat_id: int) -> None:
    _sessions[chat_id] = {"step": "idle"}


def _fmt_cop(value: float) -> str:
    return f"${value:,.0f} COP".replace(",", ".")


def _parse_cop_amount(text: str) -> Optional[float]:
    """Interpreta un monto en COP escrito con o sin separador de miles (`.`/`,`) — los montos de
    este proyecto son siempre enteros (ver commits de compras/ventas), así que no hace falta
    distinguir separador decimal de separador de miles: se descartan ambos.
    """
    cleaned = text.strip().replace(".", "").replace(",", "")
    try:
        value = float(cleaned)
    except ValueError:
        return None
    return value


def start_menu(chat_id: int) -> None:
    _reset(chat_id)
    send_message(
        chat_id,
        "¿Qué querés registrar?",
        buttons=[[("➕ Compra", "action:compra"), ("➖ Venta", "action:venta")]],
    )


def handle_text(chat_id: int, text: str) -> None:
    text = text.strip()
    session = _sessions.get(chat_id, {"step": "idle"})
    step = session.get("step", "idle")

    if text in ("/start", "/ayuda", "/help"):
        if text == "/start":
            start_menu(chat_id)
        else:
            send_message(
                chat_id,
                "/start — registrar una compra o venta\n/cancelar — cancelar la carga en curso",
            )
        return

    if text == "/cancelar":
        _reset(chat_id)
        send_message(chat_id, "Operación cancelada.")
        return

    if step == "shares":
        if not text.isdigit() or int(text) < 1:
            send_message(chat_id, "Ingresá un número entero de acciones (mínimo 1).")
            return
        session["shares"] = int(text)
        session["step"] = "price"
        send_message(chat_id, "¿Precio por acción, en COP?")
        return

    if step == "price":
        price = _parse_cop_amount(text)
        if price is None:
            send_message(chat_id, "Ingresá un precio válido (solo números).")
            return
        if price <= 0:
            send_message(chat_id, "El precio tiene que ser mayor a 0.")
            return
        session["price_cop"] = price
        session["step"] = "commission"
        send_message(
            chat_id,
            f"¿Comisión, en COP?",
            buttons=[
                [(f"Usar por defecto ({_fmt_cop(DEFAULT_COMMISSION_COP)})", "commission:default")],
                [("Ingresar otra", "commission:custom")],
                [("❌ Cancelar", "cancel")],
            ],
        )
        return

    if step == "commission_text":
        commission = _parse_cop_amount(text)
        if commission is None:
            send_message(chat_id, "Ingresá una comisión válida (solo números).")
            return
        if commission < 0:
            send_message(chat_id, "La comisión no puede ser negativa.")
            return
        session["commission_cop"] = commission
        _ask_date(chat_id, session)
        return

    if step == "date_text":
        try:
            parsed = date_cls.fromisoformat(text)
        except ValueError:
            send_message(chat_id, "Ingresá la fecha en formato YYYY-MM-DD (ej. 2026-09-22).")
            return
        session["date"] = parsed.isoformat()
        _ask_confirm(chat_id, session)
        return

    # Estando idle o en cualquier otro paso, un texto suelto reinicia el menú.
    start_menu(chat_id)


def _ask_date(chat_id: int, session: dict) -> None:
    session["step"] = "date"
    today = date_cls.today().isoformat()
    send_message(
        chat_id,
        "¿Fecha de la operación?",
        buttons=[
            [(f"Hoy ({today})", "date:today")],
            [("Ingresar otra fecha", "date:custom")],
            [("❌ Cancelar", "cancel")],
        ],
    )


def _ask_confirm(chat_id: int, session: dict) -> None:
    session["step"] = "confirm"
    total = session["shares"] * session["price_cop"] + session["commission_cop"]
    resumen = (
        f"Vas a registrar una {ACTION_LABEL[session['action']]}:\n"
        f"{session['shares']} {session['ticker']} a {_fmt_cop(session['price_cop'])} c/u\n"
        f"Comisión: {_fmt_cop(session['commission_cop'])}\n"
        f"Fecha: {session['date']}\n"
        f"Total: {_fmt_cop(total)}"
    )
    send_message(
        chat_id,
        resumen,
        buttons=[[("✅ Confirmar", "confirm"), ("❌ Cancelar", "cancel")]],
    )


def _save_movement(chat_id: int, session: dict) -> None:
    action = session["action"]
    ticker = session["ticker"]

    pull_error = _pull_latest()
    if pull_error:
        send_message(
            chat_id,
            f"No se guardó nada — no se pudo actualizar el repo antes de validar:\n{pull_error}",
        )
        _reset(chat_id)
        return

    new_row = pd.DataFrame(
        [
            {
                "ticker": ticker,
                "shares": session["shares"],
                "price_cop": session["price_cop"],
                "commission_cop": session["commission_cop"],
                "date": date_cls.fromisoformat(session["date"]),
            }
        ]
    )

    purchases = load_purchases()
    sales = load_sales()

    if action == "compra":
        candidate = pd.concat([purchases, new_row], ignore_index=True)
        errors = validate_purchases(candidate, TICKERS)
    else:
        candidate = pd.concat([sales, new_row], ignore_index=True)
        errors = validate_sales(candidate, TICKERS, purchases)

    if errors:
        send_message(chat_id, "No se guardó nada — hay errores:\n" + "\n".join(f"- {e}" for e in errors))
        _reset(chat_id)
        return

    if action == "compra":
        save_purchases(candidate)
    else:
        save_sales(candidate)

    push_error = _push_portfolio_data(action, ticker)
    if push_error:
        send_message(
            chat_id,
            "Se guardó localmente, pero no se pudo sincronizar con git — hacelo a mano:\n"
            f"{push_error}",
        )
    else:
        send_message(chat_id, f"Listo — {ACTION_LABEL[action]} de {ticker} registrada y sincronizada.")

    _reset(chat_id)


def handle_callback(chat_id: int, callback_id: str, data: str) -> None:
    answer_callback(callback_id)
    session = _sessions.get(chat_id, {"step": "idle"})

    if data == "cancel":
        _reset(chat_id)
        send_message(chat_id, "Operación cancelada.")
        return

    if data.startswith("action:"):
        session = {"step": "ticker", "action": data.split(":", 1)[1]}
        _sessions[chat_id] = session
        send_message(
            chat_id,
            "¿Qué ticker?",
            buttons=[[(t, f"ticker:{t}")] for t in TICKERS] + [[("❌ Cancelar", "cancel")]],
        )
        return

    if data.startswith("ticker:") and session.get("step") == "ticker":
        session["ticker"] = data.split(":", 1)[1]
        session["step"] = "shares"
        send_message(chat_id, f"¿Cuántas acciones de {session['ticker']}?")
        return

    if data == "commission:default" and session.get("step") == "commission":
        session["commission_cop"] = DEFAULT_COMMISSION_COP
        _ask_date(chat_id, session)
        return

    if data == "commission:custom" and session.get("step") == "commission":
        session["step"] = "commission_text"
        send_message(chat_id, "Ingresá la comisión en COP.")
        return

    if data == "date:today" and session.get("step") == "date":
        session["date"] = date_cls.today().isoformat()
        _ask_confirm(chat_id, session)
        return

    if data == "date:custom" and session.get("step") == "date":
        session["step"] = "date_text"
        send_message(chat_id, "Ingresá la fecha en formato YYYY-MM-DD.")
        return

    if data == "confirm" and session.get("step") == "confirm":
        _save_movement(chat_id, session)
        return


# --- Loop principal --------------------------------------------------------------------------


def main() -> int:
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("Faltan TELEGRAM_TOKEN / TELEGRAM_CHAT_ID en .env — ver README.md.")
        return 1

    allowed_chat_id = int(TELEGRAM_CHAT_ID)
    print("Bot de Portafolio escuchando. Ctrl+C para parar.")
    offset = None

    while True:
        try:
            params = {"timeout": 30}
            if offset is not None:
                params["offset"] = offset
            resp = requests.get(f"{API_BASE}/getUpdates", params=params, timeout=40)
            if resp.status_code == 409:
                print("Otro proceso ya está haciendo polling de este bot (¿corriendo en otra máquina?). Reintentando en 10s...")
                time.sleep(10)
                continue
            resp.raise_for_status()
            updates = resp.json().get("result", [])
        except requests.RequestException as exc:
            print(f"Error de red consultando Telegram: {exc}. Reintentando en 5s...")
            time.sleep(5)
            continue

        for update in updates:
            offset = update["update_id"] + 1
            try:
                if "callback_query" in update:
                    cq = update["callback_query"]
                    chat_id = cq["message"]["chat"]["id"]
                    if chat_id != allowed_chat_id:
                        print(f"Ignorado: callback de chat_id no autorizado {chat_id}")
                        continue
                    handle_callback(chat_id, cq["id"], cq.get("data", ""))
                elif "message" in update and "text" in update["message"]:
                    msg = update["message"]
                    chat_id = msg["chat"]["id"]
                    if chat_id != allowed_chat_id:
                        print(f"Ignorado: mensaje de chat_id no autorizado {chat_id} ({msg['chat'].get('type')}): {msg['text']!r}")
                        continue
                    handle_text(chat_id, msg["text"])
            except Exception as exc:  # nunca tirar abajo el loop por un update puntual
                print(f"Error procesando update: {exc}")


if __name__ == "__main__":
    sys.exit(main())
