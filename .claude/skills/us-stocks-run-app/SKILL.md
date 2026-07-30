---
name: us-stocks-run-app
description: Use to launch, check, or stop the Streamlit dashboard for the USStocks (Precio Justo) project — the standard way to see a change working in the browser for this repo. Covers the venv, headless flags, health check, and teardown already validated in this environment.
---

# Run the Precio Justo Streamlit app

This project already has a working local venv at `venv/` (created against
`Python312`). Use it directly rather than relying on a global `python`/`streamlit` on PATH —
that's what was validated to work in this environment.

## Start

First check for a reusable existing instance (see "Before starting a new one" below). If none,
pick a free port — the user runs other things locally, so don't assume 8501 is available or
fight another process for it:

```bash
PORT=8501
while powershell.exe -NoProfile -Command \
  "if (Get-NetTCPConnection -LocalPort $PORT -State Listen -ErrorAction SilentlyContinue) { 'busy' }" \
  | grep -q busy; do
  PORT=$((PORT+1))
done
echo "$PORT" > streamlit.port
```

Then, from the project root:

```bash
./venv/Scripts/python.exe -m streamlit run app.py --server.headless true --server.port "$PORT" \
  > streamlit.log 2>&1 &
echo $! > streamlit.pid
```

`streamlit.port` records whichever port actually got used — every later step (health check,
stop, viewing) reads from it rather than hardcoding 8501, since a busy 8501 means the running
instance could be on 8502, 8503, etc.

If `venv/` doesn't exist yet or dependencies look stale:

```bash
"/c/Users/alejo/AppData/Local/Programs/Python/Python312/python.exe" -m venv venv
./venv/Scripts/python.exe -m pip install --quiet --upgrade pip
./venv/Scripts/python.exe -m pip install --quiet -r requirements.txt
```

`FMP_API_KEY` in `.env` is only required if you'll test the `fmp` provider from the UI — the
`yfinance` provider (the UI default) works with no key.

## Confirm it's actually up

Don't just trust that the background command didn't error — poll the health endpoint before
treating the app as running:

```bash
PORT=$(cat streamlit.port 2>/dev/null || echo 8501)
curl -sf "http://localhost:$PORT/_stcore/health"
```

Empty/`ok` response with exit code 0 means it's ready. If it fails, check `streamlit.log` for
the actual error (missing `FMP_API_KEY`, import error, etc.) before retrying — port conflicts
shouldn't happen anymore since the port is picked to be free before launch, but a
never-cleaned-up `streamlit.port` from a killed process could still point somewhere stale.

To view it: open `http://localhost:$PORT` (use the PowerShell tool with `Start-Process
"http://localhost:$PORT"`, or claude-in-chrome if you need to interact with/screenshot the
page) — substitute the actual value of `$PORT`/`streamlit.port`, not always 8501.

## Before starting a new one — check for a stale process

`streamlit.pid`/`streamlit.port` may already point at a live process from a previous session.
Check first:

```bash
cat streamlit.pid 2>/dev/null
PORT=$(cat streamlit.port 2>/dev/null || echo 8501)
curl -sf "http://localhost:$PORT/_stcore/health"
```

If the health check succeeds, reuse that running instance instead of launching a second one —
don't run the port-picking loop or start a new process at all in that case.

## Stop

```bash
kill "$(cat streamlit.pid)" 2>/dev/null
rm -f streamlit.pid streamlit.port
```

If `kill` doesn't work (common when the Bash tool's PID differs from the actual Windows
process — headless mode has no window, so `taskkill //FI "WINDOWTITLE eq streamlit*"` won't
match it either), the reliable fallback validated in this environment is a PowerShell command
matching on the actual command line instead of a window title:

```powershell
Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
  Where-Object { $_.CommandLine -like '*streamlit run app.py*' } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
```

This kills every `streamlit run app.py` process regardless of which shell spawned it — a real
incident this project hit: `kill "$(cat streamlit.pid)"` silently failed to stop the old
process across two separate restarts, leaving a stale process alive that still held stale
*imported Python submodules* in memory (not just stale bytecode — a fresh `git`-edited
`src/speculation.py` on disk doesn't help if the already-running process never re-imports it).
The result was a confusing `ImportError` on a name that demonstrably existed in the file on
disk. After running the PowerShell kill above, confirm nothing's left before starting a new
one:

```powershell
Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
  Where-Object { $_.CommandLine -like '*streamlit*' } | Select-Object ProcessId, CommandLine
```

— should return nothing. Also worth clearing `__pycache__` before restarting if you've been
debugging an import mismatch, purely to rule it out as a variable (it wasn't the actual cause
in the incident above, but it's a legitimate distinct source of "the running process doesn't
match the file on disk"):

```bash
find . -path ./venv -prune -o -name "__pycache__" -print -exec rm -rf {} +
```

After restarting, don't just trust the health check — a stale process can still answer
`/_stcore/health` with `ok` while showing an error on actual page render. Verify the *new*
process is the one actually bound to the port, and that its log has no traceback right after
startup:

```powershell
Get-NetTCPConnection -LocalPort <PORT> -State Listen | Select-Object LocalPort, OwningProcess
```

(substitute the actual port from `streamlit.port`, not always 8501)

Cross-check `OwningProcess` against the PID `streamlit.pid` implies (or against a fresh
`Get-CimInstance` query) — if they don't match, an old process is still the one actually
serving traffic and needs to be found and killed directly by its real PID.
