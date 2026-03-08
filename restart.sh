#!/usr/bin/env bash
set -euo pipefail

# Restart nanobot service in current repo:
# 1) activate .venv
# 2) (optional) reinstall editable package
# 3) start gateway
#
# Usage:
#   ./restart.sh
#   ./restart.sh --reinstall
#   ./restart.sh -p 18790 [--reinstall]

PORT=18790
REINSTALL=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    -p|--port)
      PORT="${2:-}"
      if [[ -z "$PORT" ]]; then
        echo "Error: missing value for $1"
        exit 1
      fi
      shift 2
      ;;
    --reinstall)
      REINSTALL=1
      shift
      ;;
    -h|--help)
      echo "Usage: $0 [-p PORT] [--reinstall]"
      exit 0
      ;;
    *)
      echo "Unknown option: $1"
      exit 1
      ;;
  esac
done

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT_DIR"

if [[ ! -f ".venv/bin/activate" ]]; then
  echo "Error: .venv not found. Please create it first."
  exit 1
fi

# shellcheck disable=SC1091
source .venv/bin/activate

# Force LiteLLM to use local model cost map to avoid GitHub fetch timeout warnings.
export LITELLM_LOCAL_MODEL_COST_MAP=true

# Ensure single instance: stop existing "nanobot gateway" processes first.
CURRENT_PID="$$"
mapfile -t OLD_PIDS < <(pgrep -f "nanobot gateway" | grep -v "^${CURRENT_PID}$" || true)
if [[ "${#OLD_PIDS[@]}" -gt 0 ]]; then
  echo "Found existing nanobot gateway process(es): ${OLD_PIDS[*]}"
  echo "Stopping existing process(es)..."
  kill "${OLD_PIDS[@]}" || true

  # Wait up to 10s for graceful exit
  for _ in {1..10}; do
    sleep 1
    mapfile -t STILL_RUNNING < <(pgrep -f "nanobot gateway" | grep -v "^${CURRENT_PID}$" || true)
    if [[ "${#STILL_RUNNING[@]}" -eq 0 ]]; then
      break
    fi
  done

  mapfile -t STILL_RUNNING < <(pgrep -f "nanobot gateway" | grep -v "^${CURRENT_PID}$" || true)
  if [[ "${#STILL_RUNNING[@]}" -gt 0 ]]; then
    echo "Force killing remaining process(es): ${STILL_RUNNING[*]}"
    kill -9 "${STILL_RUNNING[@]}" || true
  fi
fi

echo "Using python: $(which python)"
if [[ "$REINSTALL" -eq 1 ]]; then
  echo "Installing nanobot in editable mode..."
  python -m pip install -e .
else
  echo "Skip reinstall (use --reinstall to run pip install -e .)"
fi

echo "Starting nanobot gateway on port ${PORT}..."
exec nanobot gateway --port "$PORT"
