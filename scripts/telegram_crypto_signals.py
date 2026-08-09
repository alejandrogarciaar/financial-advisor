"""Alertas de Telegram para señales TÁCTICAS validadas del ecosistema CRYPTO (BTC/ETH/SOL) —
hoy: régimen "fuerte" (+ refinamiento RSI≥70). Toda la lógica vive en `src/tactical_signals.py`
(`SIGNAL_REGISTRY`); este archivo solo filtra por `ecosystem="crypto"` — agregar una estrategia
nueva de este ecosistema (o extender el régimen a SOL el día que valide) es agregar/editar una
`SignalDefinition` allá, nunca tocar este archivo.

Corrida programada (GitHub Actions, ver .github/workflows/telegram_signals.yml, cada hora) o
manual:

    ./venv/Scripts/python.exe scripts/telegram_crypto_signals.py
"""

import sys
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.tactical_signals import run_ecosystem_signals


def main() -> int:
    run_ecosystem_signals("crypto")
    return 0


if __name__ == "__main__":
    sys.exit(main())
