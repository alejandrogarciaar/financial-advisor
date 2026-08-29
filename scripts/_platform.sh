#!/usr/bin/env bash
# Diferencias de plataforma entre la máquina Windows (git-bash) donde arrancó este proyecto y la
# macOS donde también corre desde el 2026-08-29. Lo comparten `run_app.sh` y `stop_app.sh`: la
# detección de sistema operativo es la pieza que los dos necesitan, y tenerla duplicada en cada
# uno es exactamente cómo se desincronizan.
#
# No es ejecutable por sí solo — se hace `source`. Todas las funciones asumen que quien llama ya
# hizo `cd` a la raíz del proyecto.

#: 0 en git-bash/MSYS/Cygwin sobre Windows, 1 en macOS/Linux.
is_windows() {
  case "$(uname -s)" in
    MINGW* | MSYS* | CYGWIN* | Windows_NT) return 0 ;;
    *) return 1 ;;
  esac
}

#: Ruta al python del venv, o vacío si el venv falta o está incompleto. Prueba los dos layouts en
#: vez de confiar en `is_windows()`: así un venv armado por la otra plataforma igual se encuentra,
#: y el mensaje de error habla del venv (que es el problema real) y no del sistema operativo.
venv_python() {
  if [ -x "./venv/Scripts/python.exe" ]; then
    echo "./venv/Scripts/python.exe"
  elif [ -x "./venv/bin/python" ]; then
    echo "./venv/bin/python"
  fi
}

#: Cómo rearmar el venv en esta plataforma — se imprime cuando `venv_python()` vuelve vacío.
venv_setup_hint() {
  if is_windows; then
    echo '  "/c/Users/alejo/AppData/Local/Programs/Python/Python312/python.exe" -m venv venv'
    echo "  ./venv/Scripts/python.exe -m pip install --quiet --upgrade pip"
    echo "  ./venv/Scripts/python.exe -m pip install --quiet -r requirements.txt"
  else
    echo "  python3 -m venv venv"
    echo "  ./venv/bin/python -m pip install --quiet --upgrade pip"
    echo "  ./venv/bin/python -m pip install --quiet -r requirements.txt"
  fi
  # requirements.txt omite el paquete privado `portfolio` a propósito (rompería el deploy público
  # de Streamlit Cloud) y sin él la app entera falla al importar, no solo la pestaña Portafolio.
  echo "  # y el paquete privado que requirements.txt no incluye:"
  echo "  git clone git@github.personal:alejandrogarciaar/portfolio.git ../portfolio"
  echo "  <python del venv> -m pip install -e ../portfolio"
}

#: 0 si ya hay algo escuchando en el puerto $1, 1 si está libre.
port_in_use() {
  local port="$1"
  if is_windows; then
    powershell.exe -NoProfile -Command \
      "if (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue) { 'busy' }" \
      | grep -q busy
  else
    lsof -nP -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1
  fi
}

#: PIDs de todo proceso cuya línea de comando contenga $1, uno por línea (vacío si no hay).
#: Siempre por línea de comando, nunca por `streamlit.pid` — ver el encabezado de `stop_app.sh`
#: para el incidente que lo motivó. El `$([char]39)` en la rama de Windows evita que git-bash
#: destroce las comillas simples al pasarlas a powershell.exe; no lo simplifiques sin probarlo ahí.
matching_pids() {
  local pattern="$1"
  if is_windows; then
    powershell.exe -NoProfile -Command '
      Get-CimInstance Win32_Process -Filter "Name=$([char]39)python.exe$([char]39)" |
        Where-Object { $_.CommandLine -like "*'"$pattern"'*" } |
        ForEach-Object { $_.ProcessId }
    ' | tr -d '\r' | grep -E '^[0-9]+$' || true
  else
    pgrep -f "$pattern" || true
  fi
}
