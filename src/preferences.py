"""Preferencias del usuario que sobreviven a un reinicio de la app. A diferencia de
`.cache/` (respuestas de API, reconstruibles, gitignoreadas y descartables), esto es una
elección explícita que el usuario hizo a mano y que no se puede reconstruir si se pierde —
por eso vive en su propio archivo fuera de la caché, igual que `portfolio_data/`.
"""

import json
from pathlib import Path

PREFERENCES_FILE = Path(__file__).resolve().parent.parent / "app_data" / "preferences.json"


def load_selected_tickers(available: list[str]) -> list[str]:
    """Tickers que el usuario dejó filtrados la última vez en Acciones. Si nunca guardó nada
    (primera vez, o se borró el archivo) devuelve `available` completo — cargar todo por
    defecto cuando no hay preferencia guardada."""
    if not PREFERENCES_FILE.exists():
        return available
    data = json.loads(PREFERENCES_FILE.read_text(encoding="utf-8"))
    saved = data.get("selected_tickers") or []
    # si el universo de tickers cambió desde que se guardó (uno se sacó de config.py), filtra
    # los que ya no existen; si eso deja la lista vacía, mejor volver a mostrar todo que nada.
    valid = [t for t in saved if t in available]
    return valid or available


def save_selected_tickers(tickers: list[str]) -> None:
    PREFERENCES_FILE.parent.mkdir(exist_ok=True)
    PREFERENCES_FILE.write_text(json.dumps({"selected_tickers": tickers}, indent=2), encoding="utf-8")
