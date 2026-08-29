#!/usr/bin/env bash
# Start the Precio Justo Streamlit app — extracted from financial-advisor-run-app/SKILL.md's embedded
# bash so it's a real, testable script instead of prose an LLM re-types every session. Reuses a
# live instance if one already answers its health check; otherwise picks a free port (never
# assumes 8501 is free — the user runs other things locally) and launches a fresh one.
#
# Portable across the Windows (git-bash) machine this started on and macOS/Linux since 2026-08-29
# — it used to hardcode `venv/Scripts/python.exe` and a PowerShell port probe, so on macOS it just
# exited 1 at the venv check and the app had to be launched by hand. Everything platform-specific
# lives in `scripts/_platform.sh`; nothing else in here knows which OS it's on.
#
# Usage: ./scripts/run_app.sh
# Writes streamlit.pid / streamlit.port in the project root (same as before this script existed).
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
# shellcheck source=scripts/_platform.sh
source scripts/_platform.sh

if [ -f streamlit.port ] && [ -f streamlit.pid ]; then
  PORT="$(cat streamlit.port)"
  if curl -sf "http://localhost:$PORT/_stcore/health" >/dev/null 2>&1; then
    echo "Reusing existing healthy instance on port $PORT (pid file: $(cat streamlit.pid))"
    exit 0
  fi
fi

PY="$(venv_python)"
if [ -z "$PY" ]; then
  echo "venv/ not found or incomplete — set it up first:" >&2
  venv_setup_hint >&2
  exit 1
fi

PORT=8501
while port_in_use "$PORT"; do
  PORT=$((PORT + 1))
done
echo "$PORT" > streamlit.port

# --server.fileWatcherType none: Streamlit's default file-watcher spawns a SECOND, separate
# server process to watch for source changes — found the hard way that this child process ends
# up being the one actually bound to the port (not the venv-launched parent), resolved via
# sys._base_executable rather than the venv, landing on the SYSTEM Python install instead (which
# doesn't even have streamlit installed standalone — it only worked via inherited env/sys.path
# from the parent's spawn). Since this project always restarts manually via stop_app.sh/
# run_app.sh anyway (see this skill), there's no reliance on hot-reload-on-save, so disabling the
# watcher removes the whole dual-process ambiguity: one process, unambiguously the venv one,
# guaranteed to be running whatever's on disk right now.
"$PY" -m streamlit run app.py \
  --server.headless true --server.port "$PORT" --server.fileWatcherType none \
  > streamlit.log 2>&1 &
echo $! > streamlit.pid

echo "Starting on port $PORT (pid $(cat streamlit.pid))..."
for _ in 1 2 3 4 5 6 7 8 9 10; do
  if curl -sf "http://localhost:$PORT/_stcore/health" >/dev/null 2>&1; then
    echo "Healthy on port $PORT"
    exit 0
  fi
  sleep 1
done

echo "Did not become healthy in time — check streamlit.log:" >&2
tail -n 30 streamlit.log >&2
exit 1
