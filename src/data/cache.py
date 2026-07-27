"""Caché en disco compartida entre proveedores de datos (FMP, yfinance, ...).

Cada proveedor guarda ahí su última respuesta buena por llamada. Si una llamada
en vivo falla, el proveedor puede recurrir a esta caché en vez de romper.
"""

import json
import re
from datetime import datetime, timezone
from pathlib import Path

CACHE_DIR = Path(__file__).resolve().parent.parent.parent / ".cache"
CACHE_DIR.mkdir(exist_ok=True)

_UNSAFE_CHARS = re.compile(r"[^A-Za-z0-9_=-]")


def file_for(namespace: str, path: str, params: dict) -> Path:
    parts = "_".join(f"{k}={v}" for k, v in sorted(params.items()))
    safe = _UNSAFE_CHARS.sub("_", f"{namespace}_{path}_{parts}")
    return CACHE_DIR / f"{safe}.json"


def read(cache_file: Path) -> dict | None:
    if cache_file.exists():
        return json.loads(cache_file.read_text(encoding="utf-8"))
    return None


def write(cache_file: Path, data) -> str:
    fetched_at = datetime.now(timezone.utc).isoformat()
    cache_file.write_text(json.dumps({"fetched_at": fetched_at, "data": data}), encoding="utf-8")
    return fetched_at
