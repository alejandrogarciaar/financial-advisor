"""Historial del veredicto de triangulación por ticker a lo largo del tiempo. Igual que
`preferences.py`, vive fuera de `.cache/` porque no es una respuesta de API reconstruible: es
un registro de lo que la app efectivamente mostró cada día, y no hay forma de recomputar el
veredicto de un día pasado sin sus estados financieros de esa fecha (eso es justo lo que
`backtest.py` hace, con límites documentados propios, para `years_ago=1` únicamente).
"""

import json
from datetime import date
from pathlib import Path

VERDICT_HISTORY_FILE = Path(__file__).resolve().parent.parent / "app_data" / "verdict_history.json"


def load_verdict_history(ticker: str) -> list[dict]:
    if not VERDICT_HISTORY_FILE.exists():
        return []
    data = json.loads(VERDICT_HISTORY_FILE.read_text(encoding="utf-8"))
    return data.get(ticker, [])


def record_verdict(ticker: str, summary: dict, price: float) -> None:
    """Agrega la entrada de HOY para `ticker`, salvo que ya haya una — sin esto, cada rerun de
    Streamlit (hay varios por sesión) duplicaría el mismo día. El llamador ya suele evitar
    tocar disco más de una vez por sesión (ver `_verdict_recorded_today` en app.py), pero esto
    es lo que hace que sea correcto igual si no fuera así (ej. dos sesiones el mismo día)."""
    today = date.today().isoformat()
    data = {}
    if VERDICT_HISTORY_FILE.exists():
        data = json.loads(VERDICT_HISTORY_FILE.read_text(encoding="utf-8"))
    entries = data.setdefault(ticker, [])
    if entries and entries[-1]["date"] == today:
        return
    entries.append(
        {
            "date": today,
            "verdict": summary["verdict"],
            "headline": summary["headline"],
            "cheap": summary["cheap"],
            "fair": summary["fair"],
            "expensive": summary["expensive"],
            "price": price,
            "family_margins": summary.get("family_margins", {}),
        }
    )
    VERDICT_HISTORY_FILE.parent.mkdir(exist_ok=True)
    VERDICT_HISTORY_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
