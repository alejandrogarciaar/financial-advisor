#!/usr/bin/env bash
# Stop the Precio Justo Streamlit app — extracted from financial-advisor-run-app/SKILL.md's embedded
# bash. Uses the PowerShell command-line match directly (not the plain `kill "$(cat
# streamlit.pid)"` first) because a real incident in this project found the Bash tool's PID can
# differ from the actual Windows process — `kill` silently failed to stop the old process across
# two separate restarts, leaving a stale process alive holding stale imported Python submodules
# in memory (a fresh git-edited .py file on disk doesn't help if the running process never
# re-imports it). Matching on the command line kills every `streamlit run app.py` process
# regardless of which shell spawned it.
#
# Usage: ./scripts/stop_app.sh
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

powershell.exe -NoProfile -Command '
  Get-CimInstance Win32_Process -Filter "Name=$([char]39)python.exe$([char]39)" |
    Where-Object { $_.CommandLine -like "*streamlit run app.py*" } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
'

rm -f streamlit.pid streamlit.port

remaining="$(powershell.exe -NoProfile -Command '
  (Get-CimInstance Win32_Process -Filter "Name=$([char]39)python.exe$([char]39)" |
    Where-Object { $_.CommandLine -like "*streamlit*" }).Count
' | tr -d '[:space:]')"

if [ "${remaining:-0}" != "0" ] && [ -n "${remaining:-}" ]; then
  echo "Warning: streamlit-related python.exe processes may still be running (count: $remaining)." >&2
  echo "Check manually: Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object { \$_.CommandLine -like '*streamlit*' }" >&2
  exit 1
fi

echo "Stopped — no streamlit processes remaining."
