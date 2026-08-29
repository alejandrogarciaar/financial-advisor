---
name: financial-advisor-run-app
description: Use to launch, check, or stop the Streamlit dashboard for the financial-advisor (Precio Justo) project — the standard way to see a change working in the browser for this repo. Covers the venv, headless flags, health check, and teardown already validated in this environment.
---

# Run the Precio Justo Streamlit app

This project already has a working local venv at `venv/`. Use it directly rather than relying on
a global `python`/`streamlit` on PATH — that's what was validated to work in this environment.
The venv layout differs by platform (`venv/Scripts/python.exe` on Windows, `venv/bin/python` on
macOS/Linux); the scripts below resolve it themselves, so don't hardcode either one.

The actual start/stop logic now lives in `scripts/run_app.sh` / `scripts/stop_app.sh` (promoted
from bash embedded in this file — see the `token-audit` skill's "Historial de auditorías" for
why) — real, testable scripts instead of prose an LLM re-types every session. This file covers
the decision logic and what to do when a script's output isn't enough on its own.

**Both scripts are portable across Windows (git-bash) and macOS/Linux since 2026-08-29.** They
used to be Windows-only — hardcoded `venv/Scripts/python.exe`, PowerShell `Get-NetTCPConnection`
for port probing, PowerShell `Get-CimInstance` for killing — so on macOS `run_app.sh` exited 1 at
the venv check and the app had to be launched by hand. Everything platform-specific now lives in
`scripts/_platform.sh` (sourced by both, not executable on its own): `is_windows`,
`venv_python`, `venv_setup_hint`, `port_in_use`, `matching_pids`. **Add new platform branches
there, not in the scripts** — that file is the only place either OS is named.

## Start

```bash
./scripts/run_app.sh
```

Handles all of this internally: reusing a live instance if one already answers its health check
(no port-picking or second process in that case), picking a free port otherwise (never assumes
8501 is free — the user runs other things locally), launching headless, and polling the health
endpoint before reporting success. Writes `streamlit.pid` / `streamlit.port` in the project root,
same as before this script existed — every other step below reads the port from there, not a
hardcoded 8501.

If it exits 1 because `venv/` is missing or looks stale, it prints the setup commands for the
platform you're actually on (`venv_setup_hint` in `scripts/_platform.sh`) — follow those rather
than retyping a path from memory.

Whatever the platform, the venv alone is not enough: the private `portfolio` package is
deliberately absent from `requirements.txt` (it would break the public Streamlit Cloud deploy),
and without it the **whole app** dies at import time with `ModuleNotFoundError: No module named
'portfolio'` — `app.py:11`, before any tab renders, not just Portafolio. Clone it as a sibling
checkout and install editable:

```bash
git clone git@github.personal:alejandrogarciaar/portfolio.git ../portfolio
./venv/bin/python -m pip install -e ../portfolio    # venv/Scripts/python.exe on Windows
```

The `github.personal` SSH alias matters — `~/.ssh/config` has no `Host github.com` entry, so a
plain `git@github.com:` URL fails with `Permission denied (publickey)`. See `CLAUDE.md`.

`FMP_API_KEY` in `.env` is only required if you'll test the `fmp` provider from the UI — the
`yfinance` provider (the UI default) works with no key.

## Confirm it's actually up

`run_app.sh` already polls the health endpoint and won't report success until it answers — but a
stale process can still answer `/_stcore/health` with `ok` while showing an error on actual page
render (see "Stop" below for why). To view it: open `http://localhost:$(cat streamlit.port)` —
substitute the actual value of `streamlit.port`, not always 8501. To open a browser: `open
"http://localhost:$PORT"` on macOS, the PowerShell tool's `Start-Process` on Windows, or
claude-in-chrome on either if you need to interact with/screenshot the page.

## Stop

```bash
./scripts/stop_app.sh
```

Kills every `streamlit run app.py` process by matching its command line (not
`kill "$(cat streamlit.pid)"` — see why below), then confirms nothing's left and warns if it is.
Off Windows it sends SIGTERM first and SIGKILL only to whatever survives 5s; on Windows it's the
same `Stop-Process -Force` as before.

Why command-line matching, not the pid file: a real incident this project hit — the Bash tool's
PID differed from the actual Windows process, so `kill "$(cat streamlit.pid)"` silently failed to
stop the old process across two separate restarts, leaving a stale process alive that still held
stale *imported Python submodules* in memory (not just stale bytecode — a fresh `git`-edited
`src/speculation.py` on disk doesn't help if the already-running process never re-imports it).
The result was a confusing `ImportError` on a name that demonstrably existed in the file on disk.
`scripts/stop_app.sh` matches on the actual command line instead (`Get-CimInstance Win32_Process
... Where-Object CommandLine -like '*streamlit run app.py*'`), which kills every matching process
regardless of which shell spawned it.

After restarting, don't just trust the health check — verify the *new* process is the one
actually bound to the port, and that its log has no traceback right after startup:

```bash
lsof -nP -iTCP:<PORT> -sTCP:LISTEN                 # macOS/Linux
```

```powershell
Get-NetTCPConnection -LocalPort <PORT> -State Listen | Select-Object LocalPort, OwningProcess
```

(substitute the actual port from `streamlit.port`, not always 8501) — cross-check the owning PID
against the one in `streamlit.pid`; if they don't match, an old process is still the one actually
serving traffic and needs to be found and killed directly by its real PID (same class of mismatch
`stop_app.sh` already guards against, surfaced here in case a *manual* `kill` was used instead of
the script).

Listing streamlit processes by hand on macOS: `pgrep -fl streamlit`, **not** `pgrep -af` — BSD
pgrep's `-a` means "include process ancestors in the match list", so `-af` also lists the shell
you ran it from and looks like a leftover process that isn't there.

Also worth clearing `__pycache__` before restarting if you've been debugging an import mismatch,
purely to rule it out as a variable (it wasn't the actual cause in the incident above, but it's a
legitimate distinct source of "the running process doesn't match the file on disk"):

```bash
find . -path ./venv -prune -o -name "__pycache__" -print -exec rm -rf {} +
```
