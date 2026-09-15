#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUILD_DIR="${ROOT_DIR}/build-appimage"
VENV_DIR="${BUILD_DIR}/venv"
APPDIR="${BUILD_DIR}/MT11Control.AppDir"
APP_NAME="MT11Control"
APP_ID="mt11-control"
APPIMAGE_OUT="${ROOT_DIR}/MT11Control.AppImage"
BUILD_VIDEO_CORE="${BUILD_VIDEO_CORE:-1}"

echo "[appimage] Building ${APP_NAME}"

if ! command -v python3 >/dev/null 2>&1; then
  echo "[appimage] ERROR: python3 is required."
  exit 1
fi

if ! command -v ffmpeg >/dev/null 2>&1; then
  echo "[appimage] WARNING: ffmpeg was not found on this system."
  echo "            The AppImage launcher will still prefer bundled ffmpeg if copied,"
  echo "            but RTSP playback needs ffmpeg available at runtime."
fi

appimage_arch() {
  case "$(uname -m)" in
    x86_64|amd64)
      echo "x86_64"
      ;;
    aarch64|arm64)
      echo "aarch64"
      ;;
    *)
      echo "unsupported"
      ;;
  esac
}

build_video_core() {
  if [[ "${BUILD_VIDEO_CORE}" != "1" ]]; then
    echo "[appimage] Skipping C++ video core build because BUILD_VIDEO_CORE=${BUILD_VIDEO_CORE}"
    return
  fi

  if ! command -v cmake >/dev/null 2>&1 || ! command -v g++ >/dev/null 2>&1; then
    echo "[appimage] WARNING: cmake and g++ are required to bundle mt11_video_core."
    echo "           AppImage will run with Python fallback unless mt11_video_core is provided."
    return
  fi

  echo "[appimage] Building C++ video core"
  cmake -S "${ROOT_DIR}/video_core" -B "${ROOT_DIR}/video_core/build"
  cmake --build "${ROOT_DIR}/video_core/build" --parallel
}

rm -rf "${BUILD_DIR}"
mkdir -p "${BUILD_DIR}"

build_video_core

python3 -m venv "${VENV_DIR}"
"${VENV_DIR}/bin/python" -m pip install --upgrade pip setuptools wheel
"${VENV_DIR}/bin/python" -m pip install "${ROOT_DIR}[ui]" pyinstaller

echo "[appimage] Running PyInstaller"
"${VENV_DIR}/bin/pyinstaller" \
  --noconfirm \
  --clean \
  --name MT11Control \
  --windowed \
  --paths "${ROOT_DIR}" \
  --collect-submodules mt11_sdk \
  --hidden-import json \
  --hidden-import urllib.request \
  --hidden-import urllib.parse \
  "${ROOT_DIR}/ui/mt11_dashboard_qt.py"

mkdir -p "${APPDIR}/usr/bin" "${APPDIR}/usr/share/applications" "${APPDIR}/usr/share/icons/hicolor/256x256/apps"
cp -a "${ROOT_DIR}/dist/MT11Control/." "${APPDIR}/usr/bin/MT11Control/"

if command -v ffmpeg >/dev/null 2>&1; then
  cp "$(command -v ffmpeg)" "${APPDIR}/usr/bin/ffmpeg" || true
fi

if command -v mediamtx >/dev/null 2>&1; then
  cp "$(command -v mediamtx)" "${APPDIR}/usr/bin/mediamtx" || true
fi

if [[ -x "${ROOT_DIR}/video_core/build/mt11_video_core" ]]; then
  cp "${ROOT_DIR}/video_core/build/mt11_video_core" "${APPDIR}/usr/bin/mt11_video_core"
else
  echo "[appimage] WARNING: mt11_video_core was not bundled."
fi

cat > "${APPDIR}/AppRun" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
HERE="$(dirname "$(readlink -f "$0")")"
unset PYTHONHOME
unset PYTHONPATH
export PYTHONNOUSERSITE=1
export PATH="${HERE}/usr/bin:${PATH}"
if [[ -x "${HERE}/usr/bin/mt11_video_core" ]]; then
  export MT11_VIDEO_CORE_BIN="${HERE}/usr/bin/mt11_video_core"
fi
if [[ -x "${HERE}/usr/bin/mediamtx" ]]; then
  export MT11_MEDIAMTX_BIN="${HERE}/usr/bin/mediamtx"
fi
export QT_QPA_PLATFORM_PLUGIN_PATH="${HERE}/usr/bin/MT11Control/_internal/PyQt5/Qt5/plugins:${QT_QPA_PLATFORM_PLUGIN_PATH:-}"
exec "${HERE}/usr/bin/MT11Control/MT11Control" "$@"
EOF
chmod +x "${APPDIR}/AppRun"

cat > "${APPDIR}/${APP_ID}.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=${APP_NAME}
Exec=AppRun
Icon=${APP_ID}
Categories=Utility;AudioVideo;Network;
Terminal=false
EOF
cp "${APPDIR}/${APP_ID}.desktop" "${APPDIR}/usr/share/applications/${APP_ID}.desktop"

"${VENV_DIR}/bin/python" - <<PY
from pathlib import Path
from PIL import Image, ImageDraw
root = Path("${APPDIR}")
for target in [
    root / "${APP_ID}.png",
    root / "usr/share/icons/hicolor/256x256/apps/${APP_ID}.png",
]:
    img = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((18, 18, 238, 238), radius=34, fill=(6, 10, 11, 255), outline=(88, 247, 232, 255), width=6)
    d.rectangle((52, 76, 204, 164), fill=(0, 0, 0, 255), outline=(242, 242, 239, 255), width=4)
    d.line((72, 194, 184, 194), fill=(88, 247, 232, 255), width=8)
    d.text((70, 96), "MT11", fill=(242, 242, 239, 255))
    img.save(target)
PY

ARCH_NAME="$(appimage_arch)"
if [[ "${ARCH_NAME}" == "unsupported" ]]; then
  echo "[appimage] ERROR: unsupported AppImage architecture: $(uname -m)"
  exit 1
fi

APPIMAGETOOL="${BUILD_DIR}/appimagetool-${ARCH_NAME}.AppImage"
if command -v appimagetool >/dev/null 2>&1; then
  APPIMAGETOOL="$(command -v appimagetool)"
else
  echo "[appimage] Downloading appimagetool"
  curl -L --fail \
    -o "${APPIMAGETOOL}" \
    "https://github.com/AppImage/AppImageKit/releases/download/continuous/appimagetool-${ARCH_NAME}.AppImage"
  chmod +x "${APPIMAGETOOL}"
fi

echo "[appimage] Packaging AppImage"
ARCH="${ARCH_NAME}" "${APPIMAGETOOL}" "${APPDIR}" "${APPIMAGE_OUT}"
chmod +x "${APPIMAGE_OUT}"

echo
echo "[appimage] Done:"
echo "           ${APPIMAGE_OUT}"
