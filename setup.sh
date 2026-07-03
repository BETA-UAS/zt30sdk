#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="${ROOT_DIR}/.venv"

echo "[setup] SIYI ZT30 SDK package"

if ! command -v python3 >/dev/null 2>&1; then
  echo "[setup] ERROR: python3 is required."
  exit 1
fi

if ! python3 -m venv --help >/dev/null 2>&1; then
  echo "[setup] ERROR: python3 venv support is missing."
  echo "        Ubuntu/Debian: sudo apt install python3-venv"
  exit 1
fi

if ! command -v ffmpeg >/dev/null 2>&1; then
  echo "[setup] WARNING: ffmpeg is not installed. RTSP playback needs ffmpeg."
  if command -v apt-get >/dev/null 2>&1; then
    read -r -p "[setup] Install ffmpeg with sudo apt-get now? [y/N] " answer
    if [[ "${answer,,}" == "y" || "${answer,,}" == "yes" ]]; then
      sudo apt-get update
      sudo apt-get install -y ffmpeg
    fi
  fi
fi

echo "[setup] Creating virtual environment: ${VENV_DIR}"
python3 -m venv "${VENV_DIR}"

echo "[setup] Installing Python package and UI dependencies"
"${VENV_DIR}/bin/python" -m pip install --upgrade pip setuptools wheel
"${VENV_DIR}/bin/python" -m pip install -e "${ROOT_DIR}[ui]"

cat > "${ROOT_DIR}/run_dashboard.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "${ROOT_DIR}/.venv/bin/python" "${ROOT_DIR}/ui/zt30_dashboard_qt.py" "$@"
EOF
chmod +x "${ROOT_DIR}/run_dashboard.sh"

echo
echo "[setup] Done."
echo "[setup] Run dashboard:"
echo "        ${ROOT_DIR}/run_dashboard.sh"
