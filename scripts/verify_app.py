"""Smoke-test all 6 tabs via streamlit.testing.v1.AppTest — no browser needed.

Re-typed by hand in-session at least 3 times before this existed. Run this instead:

    ./venv/Scripts/python.exe scripts/verify_app.py

Exits 0 with no output on success; prints the exception and exits 1 on failure. Does not replace
manually checking a real change in the browser (this only proves the script runs without
exceptions, not that a specific feature renders/behaves correctly) — see the `run` skill /
`us-stocks-run-app` skill for that.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from streamlit.testing.v1 import AppTest


def main() -> int:
    at = AppTest.from_file("app.py", default_timeout=90)
    at.run()
    if at.exception:
        print("EXCEPTIONS FOUND:")
        for exc in at.exception:
            print(exc)
        return 1
    print("OK - 0 exceptions across all 6 tabs")
    return 0


if __name__ == "__main__":
    sys.exit(main())
