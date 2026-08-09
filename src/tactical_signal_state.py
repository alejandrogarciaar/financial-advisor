"""Último estado conocido de cada señal táctica (drawdown/golden-cross/S-R/régimen), para que
`scripts/telegram_tactical_signals.py` sepa si algo CAMBIÓ desde la corrida anterior antes de
avisar por Telegram. A diferencia de `verdict_history.py` (un log que crece, una entrada por
día), esto solo necesita "¿cuál fue el último estado?" — no hay ningún gráfico ni tabla que
necesite el historial completo, así que no vale la pena guardar más que el último valor por
clave. Vive en `app_data/` por el mismo motivo que `verdict_history.json`/`preferences.json`:
no es una respuesta de API reconstruible, es lo que este script efectivamente ya vio.
"""

import json
from pathlib import Path

STATE_FILE = Path(__file__).resolve().parent.parent / "app_data" / "tactical_signal_state.json"


def load_state() -> dict:
    if not STATE_FILE.exists():
        return {}
    return json.loads(STATE_FILE.read_text(encoding="utf-8"))


def record_state(key: str, state: str) -> tuple[str | None, bool]:
    """Devuelve (estado_anterior, cambió) y persiste `state` como el nuevo actual para `key`.
    `estado_anterior=None` en el primer registro de esa key — igual que telegram_alerts.py con
    verdict_history, nunca se notifica la primera vez porque no hay nada contra qué comparar."""
    data = load_state()
    anterior = data.get(key)
    cambio = anterior is not None and anterior != state
    data[key] = state
    STATE_FILE.parent.mkdir(exist_ok=True)
    STATE_FILE.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    return anterior, cambio
