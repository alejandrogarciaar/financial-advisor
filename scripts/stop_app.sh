#!/usr/bin/env bash
# Stop the Precio Justo Streamlit app — extracted from financial-advisor-run-app/SKILL.md's embedded
# bash. Matches on the process command line (not the plain `kill "$(cat streamlit.pid)"` first)
# because a real incident in this project found the Bash tool's PID can differ from the actual
# Windows process — `kill` silently failed to stop the old process across two separate restarts,
# leaving a stale process alive holding stale imported Python submodules in memory (a fresh
# git-edited .py file on disk doesn't help if the running process never re-imports it). Matching
# on the command line kills every `streamlit run app.py` process regardless of which shell
# spawned it, which is the property worth keeping — so the portable version keeps doing that
# (`pgrep -f` on macOS/Linux, the same PowerShell CIM query on Windows) rather than falling back
# to the pid file just because it's simpler off Windows.
#
# Usage: ./scripts/stop_app.sh
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
# shellcheck source=scripts/_platform.sh
source scripts/_platform.sh

pids="$(matching_pids "streamlit run app.py")"

if [ -n "$pids" ]; then
  # SIGTERM primero y SIGKILL solo a lo que sobreviva: `Stop-Process -Force` (lo que hacía la
  # versión Windows-only) es un kill duro, y para un servidor que no tiene estado propio que
  # perder da igual — pero pedirle que cierre bien primero no cuesta nada y evita dejar el puerto
  # en TIME_WAIT más de lo necesario.
  # shellcheck disable=SC2086
  kill $pids 2>/dev/null
  for _ in 1 2 3 4 5; do
    [ -z "$(matching_pids "streamlit run app.py")" ] && break
    sleep 1
  done
  # shellcheck disable=SC2086
  remaining_pids="$(matching_pids "streamlit run app.py")"
  if [ -n "$remaining_pids" ]; then
    # shellcheck disable=SC2086
    kill -9 $remaining_pids 2>/dev/null
    sleep 1
  fi
fi

rm -f streamlit.pid streamlit.port

remaining="$(matching_pids "streamlit" | wc -l | tr -d '[:space:]')"

if [ "${remaining:-0}" != "0" ]; then
  echo "Warning: streamlit-related processes may still be running (count: $remaining)." >&2
  if is_windows; then
    echo "Check manually: Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object { \$_.CommandLine -like '*streamlit*' }" >&2
  else
    # `-fl`, no `-af`: en el pgrep de macOS/BSD `-a` significa "incluir ancestros del match", así
    # que `-af` lista además la shell desde la que se corrió y parece que quedó algo vivo cuando no.
    echo "Check manually: pgrep -fl streamlit" >&2
  fi
  exit 1
fi

echo "Stopped — no streamlit processes remaining."
