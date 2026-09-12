#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="${VENV_DIR:-${ROOT_DIR}/.venv}"
INSTALL_MEDIAMTX="${INSTALL_MEDIAMTX:-1}"
MEDIAMTX_BIN="${MEDIAMTX_BIN:-/usr/local/bin/mediamtx}"

echo "[setup] UniPod MT11 SDK package"

need_cmd() {
  command -v "$1" >/dev/null 2>&1
}

require_sudo() {
  if [[ "${EUID}" -eq 0 ]]; then
    SUDO=()
  elif need_cmd sudo; then
    SUDO=(sudo)
  else
    echo "[setup] ERROR: sudo is required to install MediaMTX to ${MEDIAMTX_BIN}."
    echo "        Run as root, install sudo, or set INSTALL_MEDIAMTX=0."
    exit 1
  fi
}

detect_mediamtx_arch() {
  case "$(uname -m)" in
    x86_64|amd64)
      echo "amd64"
      ;;
    aarch64|arm64)
      echo "arm64"
      ;;
    armv7l|armhf)
      echo "armv7"
      ;;
    *)
      echo "unsupported"
      ;;
  esac
}

latest_mediamtx_url() {
  local arch="$1"
  python3 - "$arch" <<'PY'
import json
import sys
import urllib.request

arch = sys.argv[1]
api_url = "https://api.github.com/repos/bluenviron/mediamtx/releases/latest"

with urllib.request.urlopen(api_url, timeout=30) as response:
    release = json.load(response)

needle = f"linux_{arch}.tar.gz"

for asset in release.get("assets", []):
    url = asset.get("browser_download_url", "")
    if needle in url:
        print(url)
        raise SystemExit(0)

raise SystemExit(f"No mediamtx asset found for {needle}")
PY
}

install_mediamtx() {
  if [[ "${INSTALL_MEDIAMTX}" != "1" ]]; then
    echo "[setup] Skipping MediaMTX install because INSTALL_MEDIAMTX=${INSTALL_MEDIAMTX}"
    return
  fi

  if need_cmd mediamtx || [[ -x "${MEDIAMTX_BIN}" ]]; then
    echo "[setup] MediaMTX already installed: $(command -v mediamtx || echo "${MEDIAMTX_BIN}")"
    return
  fi

  if ! need_cmd curl || ! need_cmd tar; then
    echo "[setup] WARNING: curl and tar are required for automatic MediaMTX install."
    echo "        Install them manually, then rerun setup or set INSTALL_MEDIAMTX=0."
    return
  fi

  require_sudo

  local arch
  arch="$(detect_mediamtx_arch)"
  if [[ "${arch}" == "unsupported" ]]; then
    echo "[setup] WARNING: unsupported architecture for automatic MediaMTX install: $(uname -m)"
    echo "        Install mediamtx manually or set INSTALL_MEDIAMTX=0."
    return
  fi

  local tmpdir=""

  cleanup_mediamtx_tmpdir() {
    if [[ -n "${tmpdir:-}" && -d "${tmpdir}" ]]; then
      rm -rf "${tmpdir}"
    fi
  }

  echo "[setup] Resolving latest MediaMTX release for linux_${arch}"
  local url
  url="$(latest_mediamtx_url "${arch}")"

  tmpdir="$(mktemp -d)"
  trap cleanup_mediamtx_tmpdir RETURN

  echo "[setup] Downloading MediaMTX"
  curl -L --fail "${url}" -o "${tmpdir}/mediamtx.tar.gz"
  tar -xzf "${tmpdir}/mediamtx.tar.gz" -C "${tmpdir}"
  "${SUDO[@]}" install -m 0755 "${tmpdir}/mediamtx" "${MEDIAMTX_BIN}"

  cleanup_mediamtx_tmpdir
  trap - RETURN
}

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

install_mediamtx

echo "[setup] Creating virtual environment: ${VENV_DIR}"
python3 -m venv "${VENV_DIR}"

echo "[setup] Installing Python package and UI dependencies"
"${VENV_DIR}/bin/python" -m pip install --upgrade pip setuptools wheel
"${VENV_DIR}/bin/python" -m pip install -e "${ROOT_DIR}[ui]"

cat > "${ROOT_DIR}/run_mt11control.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "${ROOT_DIR}/.venv/bin/python" "${ROOT_DIR}/ui/mt11_dashboard_qt.py" "$@"
EOF
chmod +x "${ROOT_DIR}/run_mt11control.sh"
ln -sf run_mt11control.sh "${ROOT_DIR}/run_dashboard.sh"

echo
echo "[setup] Done."
echo "[setup] Run MT11Control:"
echo "        ${ROOT_DIR}/run_mt11control.sh"
