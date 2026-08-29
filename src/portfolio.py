"""Gestión de capital: persistencia de las compras y ventas reales del usuario. El cálculo
(costo promedio cronológico, ganancias realizadas, comisiones, proyecciones, etc.) vive ahora en
el paquete privado `portfolio` (github.com/alejandrogarciaar/portfolio, extraído de este mismo
archivo el 2026-08-16, instalado en modo editable desde el clone local — ver
`financial-advisor-portfolio`'s design history) — este módulo quedó como un wrapper delgado:
sigue siendo el único dueño de `portfolio_data/` (la fuente de verdad real, nunca el snapshot
embebido en el paquete) y reexporta el cálculo tal cual.

A diferencia de `.cache/` (respuestas de APIs, reconstruibles y por eso gitignoreadas),
`portfolio_data/` guarda datos que el usuario ingresó a mano y que no se pueden reconstruir si
se borran, así que vive en su propio archivo fuera de la caché.

Cada `save_purchases()`/`save_sales()` además sincroniza el archivo guardado hacia el clone
local del repo `portfolio` con commit+push automático — así el snapshot que ese paquete embebe
nunca queda desactualizado respecto a lo que el usuario acaba de cargar. Un fallo de sync (red
caída, remoto rechazado) no debe tirar abajo el guardado real, que ya ocurrió en
`portfolio_data/`; solo se avisa por consola. Dónde vive ese clone: ver
`_SYNC_REPO_CANDIDATES` abajo.
"""

import subprocess
from pathlib import Path
from typing import Optional

import pandas as pd
import portfolio as _lib

DATA_DIR = Path(__file__).resolve().parent.parent / "portfolio_data"

#: Ubicaciones posibles del clone local del repo `portfolio`, en orden de preferencia:
#: `.portfolio_repo/` adentro de este repo (la original, gitignored acá) y el checkout hermano
#: `../portfolio` (el que existe en esta máquina desde el 2026-08-29 — mismo patrón de checkout
#: hermano que ya usa `market-signals-telegram`). Se aceptan las dos para que mover el clone de
#: una a la otra no rompa el sync en silencio, que es exactamente lo que pasó al clonarlo afuera.
_SYNC_REPO_CANDIDATES = (
    Path(__file__).resolve().parent.parent / ".portfolio_repo",
    Path(__file__).resolve().parent.parent.parent / "portfolio",
)


def _sync_repo_dir() -> Optional[Path]:
    """Primer clone local del repo `portfolio` que exista, o `None` si no hay ninguno.

    Se resuelve en cada guardado y no al importar el módulo, así clonar el repo con la app ya
    abierta alcanza para que el sync empiece a andar sin reiniciar. Exige `.git` adentro, no solo
    que el directorio exista: `../portfolio` es un nombre lo bastante genérico como para chocar
    con una carpeta cualquiera, y commitear contra la de otro es peor que no sincronizar.
    """
    for candidate in _SYNC_REPO_CANDIDATES:
        if (candidate / ".git").exists():
            return candidate
    return None

COLUMNS = _lib.COLUMNS
DEFAULT_COMMISSION_COP = _lib.DEFAULT_COMMISSION_COP

validate_purchases = _lib.validate_purchases
validate_sales = _lib.validate_sales
sale_cost_basis_by_row = _lib.sale_cost_basis_by_row
summarize_by_ticker = _lib.summarize_by_ticker
realized_gains_summary = _lib.realized_gains_summary
simulate_additional_purchase = _lib.simulate_additional_purchase
build_synthetic_portfolio_series = _lib.build_synthetic_portfolio_series
project_future_value = _lib.project_future_value
commission_summary = _lib.commission_summary


def load_purchases() -> pd.DataFrame:
    return _lib.load_purchases(DATA_DIR)


def load_sales() -> pd.DataFrame:
    return _lib.load_sales(DATA_DIR)


def save_purchases(df: pd.DataFrame) -> None:
    _lib.save_purchases(df, DATA_DIR)
    _sync_to_portfolio_repo("purchases.json")


def save_sales(df: pd.DataFrame) -> None:
    _lib.save_sales(df, DATA_DIR)
    _sync_to_portfolio_repo("sales.json")


def _sync_to_portfolio_repo(filename: str) -> None:
    repo_dir = _sync_repo_dir()
    if repo_dir is None:
        return
    sync_data_dir = repo_dir / "portfolio" / "portfolio_data"
    try:
        sync_data_dir.mkdir(parents=True, exist_ok=True)
        (sync_data_dir / filename).write_text(
            (DATA_DIR / filename).read_text(encoding="utf-8"), encoding="utf-8"
        )
        subprocess.run(
            ["git", "add", f"portfolio/portfolio_data/{filename}"],
            cwd=repo_dir,
            check=True,
            capture_output=True,
        )
        staged = subprocess.run(
            ["git", "diff", "--cached", "--quiet"], cwd=repo_dir
        )
        if staged.returncode == 0:
            return
        subprocess.run(
            ["git", "commit", "-m", f"Sync {filename} from financial-advisor (auto)"],
            cwd=repo_dir,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "push"], cwd=repo_dir, check=True, capture_output=True
        )
    except Exception as exc:
        print(f"[portfolio sync] No se pudo sincronizar {filename} con el repo portfolio: {exc}")
