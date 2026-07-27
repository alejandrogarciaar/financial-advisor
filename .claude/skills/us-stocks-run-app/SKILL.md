---
name: us-stocks-run-app
description: Use to launch, check, or stop the Streamlit dashboard for the USStocks (Precio Justo) project — the standard way to see a change working in the browser for this repo. Covers the venv, headless flags, health check, and teardown already validated in this environment.
---

# Run the Precio Justo Streamlit app

This project already has a working local venv at `venv/` (created against
`Python312`). Use it directly rather than relying on a global `python`/`streamlit` on PATH —
that's what was validated to work in this environment.

## Start

From the project root:

```bash
./venv/Scripts/python.exe -m streamlit run app.py --server.headless true --server.port 8501 \
  > streamlit.log 2>&1 &
echo $! > streamlit.pid
```

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
curl -sf http://localhost:8501/_stcore/health
```

Empty/`ok` response with exit code 0 means it's ready. If it fails, check `streamlit.log` for
the actual error (missing `FMP_API_KEY`, port already in use, import error, etc.) before
retrying.

To view it: open `http://localhost:8501` (use the PowerShell tool with `Start-Process
"http://localhost:8501"`, or claude-in-chrome if you need to interact with/screenshot the
page).

## Before starting a new one — check for a stale process

`streamlit.pid` may already point at a live process from a previous session. Check first:

```bash
cat streamlit.pid 2>/dev/null
curl -sf http://localhost:8501/_stcore/health
```

If the health check succeeds, reuse that running instance instead of launching a second one on
the same port (the second `streamlit run` will fail to bind :8501 anyway).

## Stop

```bash
kill "$(cat streamlit.pid)" 2>/dev/null
rm -f streamlit.pid
```

If `kill` doesn't work (common when the Bash tool's PID differs from the actual Windows
process), fall back to:

```bash
taskkill //F //IM python.exe //FI "WINDOWTITLE eq streamlit*"
```

Note this last one kills *all* python.exe processes tagged with a streamlit window title, not
just this app's — prefer the PID-based kill when `streamlit.pid` is valid.
