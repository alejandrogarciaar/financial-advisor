---
name: financial-advisor-run-app
description: Use to launch, check, or stop the Streamlit dashboard for the financial-advisor (Precio Justo) project — the standard way to see a change working in the browser for this repo. Covers the venv, headless flags, health check, and teardown already validated in this environment.
---

# Run the Precio Justo Streamlit app

This project already has a working local venv at `venv/` (created against
`Python312`). Use it directly rather than relying on a global `python`/`streamlit` on PATH —
that's what was validated to work in this environment.

The actual start/stop logic now lives in `scripts/run_app.sh` / `scripts/stop_app.sh` (promoted
from bash embedded in this file — see the `token-audit` skill's "Historial de auditorías" for
why) — real, testable scripts instead of prose an LLM re-types every session. This file covers
the decision logic and what to do when a script's output isn't enough on its own.

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

If it exits 1 because `venv/` is missing or looks stale, set it up first:

```bash
"/c/Users/alejo/AppData/Local/Programs/Python/Python312/python.exe" -m venv venv
./venv/Scripts/python.exe -m pip install --quiet --upgrade pip
./venv/Scripts/python.exe -m pip install --quiet -r requirements.txt
```

`FMP_API_KEY` in `.env` is only required if you'll test the `fmp` provider from the UI — the
`yfinance` provider (the UI default) works with no key.

## Confirm it's actually up

`run_app.sh` already polls the health endpoint and won't report success until it answers — but a
stale process can still answer `/_stcore/health` with `ok` while showing an error on actual page
render (see "Stop" below for why). To view it: open `http://localhost:$(cat streamlit.port)`
(use the PowerShell tool with `Start-Process "http://localhost:$PORT"`, or claude-in-chrome if
you need to interact with/screenshot the page) — substitute the actual value of
`streamlit.port`, not always 8501.

## Stop

```bash
./scripts/stop_app.sh
```

Kills every `streamlit run app.py` process by matching its command line (not
`kill "$(cat streamlit.pid)"` — see why below), then confirms nothing's left and warns if it is.

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

```powershell
Get-NetTCPConnection -LocalPort <PORT> -State Listen | Select-Object LocalPort, OwningProcess
```

(substitute the actual port from `streamlit.port`, not always 8501) — cross-check
`OwningProcess` against the PID `streamlit.pid` implies (or against a fresh `Get-CimInstance`
query); if they don't match, an old process is still the one actually serving traffic and needs
to be found and killed directly by its real PID (same class of mismatch `stop_app.sh` already
guards against, surfaced here in case a *manual* `kill` was used instead of the script).

Also worth clearing `__pycache__` before restarting if you've been debugging an import mismatch,
purely to rule it out as a variable (it wasn't the actual cause in the incident above, but it's a
legitimate distinct source of "the running process doesn't match the file on disk"):

```bash
find . -path ./venv -prune -o -name "__pycache__" -print -exec rm -rf {} +
```
