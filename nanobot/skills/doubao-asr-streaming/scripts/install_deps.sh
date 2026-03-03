#!/usr/bin/env bash
set -euo pipefail

need_cmd() {
  command -v "$1" >/dev/null 2>&1
}

install_ffmpeg() {
  if need_cmd ffmpeg; then
    return 0
  fi

  if need_cmd brew; then
    echo "[install] ffmpeg via brew"
    brew install ffmpeg
    return 0
  fi

  if need_cmd apt-get; then
    echo "[install] ffmpeg via apt-get"
    sudo apt-get update
    sudo apt-get install -y ffmpeg
    return 0
  fi

  if need_cmd yum; then
    echo "[install] ffmpeg via yum"
    sudo yum install -y ffmpeg
    return 0
  fi

  if need_cmd dnf; then
    echo "[install] ffmpeg via dnf"
    sudo dnf install -y ffmpeg
    return 0
  fi

  echo "[error] ffmpeg not found and no supported package manager detected." >&2
  exit 2
}

install_python_deps() {
  if need_cmd python3; then
    if python3 - <<'PY' >/dev/null 2>&1
import importlib.util
import sys
sys.exit(0 if importlib.util.find_spec('websockets') else 1)
PY
    then
      echo "[ok] python websockets already installed"
      return 0
    fi

    echo "[install] python package websockets"
    python3 -m pip install --user websockets
    return 0
  fi

  echo "[error] python3 not found." >&2
  exit 2
}

install_ffmpeg
install_python_deps

echo "[ok] dependencies ready"
