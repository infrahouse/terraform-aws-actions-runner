#!/usr/bin/env bash
set -euo pipefail

# Inputs (override via env or flags)
REQUIREMENTS_FILE="${REQUIREMENTS_FILE:-requirements.txt}"
OUT_DIR="${TARGET_DIR:-build}"
PY_VER="${PY_VER:-$(python3 -c 'import sys;print(f"{sys.version_info.major}.{sys.version_info.minor}")')}"
ARCH="${ARCH:-auto}" # auto | arm64 | aarch64 | x86_64
CODE_DIR="$(dirname "$REQUIREMENTS_FILE")"

norm_arch() {
  local a="${1}"
  case "$a" in
    auto)
      # Detect host arch; we still cross-install Linux wheels.
      local m
      m="$(uname -m)"
      case "$m" in
        arm64|aarch64) echo "aarch64" ;;
        x86_64|amd64)  echo "x86_64" ;;
        *) echo "x86_64" ;;  # default
      esac
      ;;
    arm64|aarch64) echo "aarch64" ;;
    x86_64|amd64)  echo "x86_64" ;;
    *) echo "x86_64" ;;
  esac
}

plat_tag() {
  # Map to manylinux platform tags expected by AWS Lambda (glibc)
  case "$1" in
    aarch64) echo "manylinux2014_aarch64" ;;
    x86_64)  echo "manylinux2014_x86_64" ;;
    *) echo "manylinux2014_x86_64" ;;
  esac
}

install_for_arch() {
  local arch="$1"
  local plat; plat="$(plat_tag "$arch")"
  local dest="${OUT_DIR}/${arch}"

  echo "==> Installing for arch=${arch} platform=${plat} python=${PY_VER} into ${dest}"
  rm -rf "${dest}"
  mkdir -p "${dest}"

  # Install ONLY manylinux binary wheels for the requested platform & CPython ABI.
  # This avoids accidental source builds for macOS or musl (Alpine) artifacts.
  python3 -m pip install \
    --only-binary=:all: \
    --platform "${plat}" \
    --implementation cp \
    --python-version "${PY_VER}" \
    --target "${dest}" \
    --upgrade \
    -r "${REQUIREMENTS_FILE}"

  cp "$CODE_DIR/main.py" "${dest}"
  # Clean pyc caches
  find "${dest}" -type d -name "__pycache__" -exec rm -rf {} +
}

main() {
  if [[ ! -f "${REQUIREMENTS_FILE}" ]]; then
    echo "Requirements file not found: ${REQUIREMENTS_FILE}" >&2
    exit 1
  fi

  local a; a="$(norm_arch "${ARCH}")"

  install_for_arch "${a}"

  echo "==> Done. Output in ${OUT_DIR}/(aarch64|x86_64)"
  echo "Tip: package as a Lambda Layer zip, e.g.:"
  echo "  (cd ${OUT_DIR}/aarch64 && zip -r ../layer-aarch64.zip .)"
  echo "  (cd ${OUT_DIR}/x86_64 && zip -r ../layer-x86_64.zip .)"
}

main "$@"
