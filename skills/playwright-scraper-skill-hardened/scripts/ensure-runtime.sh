#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
SKILL_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd)"
STATE_DIR="$SKILL_DIR/.state"
STATE_FILE="$STATE_DIR/runtime-state.json"
LOCK_FILE="$SKILL_DIR/package-lock.json"

need_cmd() {
  command -v "$1" >/dev/null 2>&1
}

require_cmd() {
  local cmd="$1"
  if ! need_cmd "$cmd"; then
    echo "[error] Missing required command: $cmd" >&2
    exit 2
  fi
}

now_utc() {
  date -u +"%Y-%m-%dT%H:%M:%SZ"
}

sha256_file() {
  local file="$1"
  if [[ ! -f "$file" ]]; then
    echo ""
    return 0
  fi
  if need_cmd shasum; then
    shasum -a 256 "$file" | awk '{print $1}'
    return 0
  fi
  if need_cmd sha256sum; then
    sha256sum "$file" | awk '{print $1}'
    return 0
  fi
  echo ""
}

read_state_installed_at() {
  if [[ ! -f "$STATE_FILE" ]]; then
    return 0
  fi
  STATE_FILE="$STATE_FILE" node <<'NODE'
const fs = require("fs");
const path = process.env.STATE_FILE;
try {
  const data = JSON.parse(fs.readFileSync(path, "utf8"));
  if (data && typeof data.installedAt === "string" && data.installedAt.length > 0) {
    process.stdout.write(data.installedAt);
  }
} catch {}
NODE
}

playwright_module_ready() {
  (
    cd "$SKILL_DIR"
    node -e "require.resolve('playwright/package.json')"
  ) >/dev/null 2>&1
}

chromium_ready() {
  (
    cd "$SKILL_DIR"
    node -e "const fs=require('fs');const { chromium }=require('playwright');const p=chromium.executablePath();if(!p||!fs.existsSync(p)){process.exit(1)}"
  ) >/dev/null 2>&1
}

playwright_version() {
  (
    cd "$SKILL_DIR"
    node -e "console.log(require('playwright/package.json').version)"
  ) 2>/dev/null || true
}

write_state() {
  local installed_at="$1"
  local source="$2"
  local checked_at lock_hash node_version npm_version python_version venv_path venv_python pw_version

  mkdir -p "$STATE_DIR"

  checked_at="$(now_utc)"
  lock_hash="$(sha256_file "$LOCK_FILE")"
  node_version="$(node -v 2>/dev/null || true)"
  npm_version="$(npm -v 2>/dev/null || true)"
  python_version="$(python3 --version 2>/dev/null | awk '{print $2}' || true)"
  venv_path="${VIRTUAL_ENV:-}"
  venv_python=""
  if [[ -n "$venv_path" && -x "$venv_path/bin/python" ]]; then
    venv_python="$venv_path/bin/python"
  fi
  pw_version="$(playwright_version)"

  STATE_FILE="$STATE_FILE" \
  INSTALLED_AT="$installed_at" \
  CHECKED_AT="$checked_at" \
  SOURCE="$source" \
  LOCK_HASH="$lock_hash" \
  NODE_VERSION="$node_version" \
  NPM_VERSION="$npm_version" \
  PYTHON_VERSION="$python_version" \
  VENV_PATH="$venv_path" \
  VENV_PYTHON="$venv_python" \
  PW_VERSION="$pw_version" \
  node <<'NODE'
const fs = require("fs");
const path = process.env.STATE_FILE;
const payload = {
  schemaVersion: 1,
  status: "ready",
  installedAt: process.env.INSTALLED_AT || process.env.CHECKED_AT,
  checkedAt: process.env.CHECKED_AT,
  source: process.env.SOURCE || "unknown",
  lockHash: process.env.LOCK_HASH || "",
  dependencies: {
    packageManager: "npm",
    package: "playwright",
    packageVersion: process.env.PW_VERSION || "",
    browsers: ["chromium"],
  },
  runtime: {
    node: process.env.NODE_VERSION || "",
    npm: process.env.NPM_VERSION || "",
    python: process.env.PYTHON_VERSION || "",
    venvPath: process.env.VENV_PATH || "",
    venvPython: process.env.VENV_PYTHON || "",
  },
};
fs.writeFileSync(path, JSON.stringify(payload, null, 2) + "\n", "utf8");
NODE
}

install_runtime() {
  (
    cd "$SKILL_DIR"
    if [[ -f package-lock.json ]]; then
      echo "[install] npm ci"
      npm ci
    else
      echo "[install] npm install"
      npm install
    fi
    if chromium_ready; then
      echo "[ok] Chromium browser already available (skip download)"
    else
      echo "[install] npx playwright install chromium"
      npx playwright install chromium
    fi
  )
}

require_cmd node
require_cmd npm
require_cmd npx

existing_installed_at="$(read_state_installed_at)"
if playwright_module_ready && chromium_ready; then
  write_state "${existing_installed_at:-$(now_utc)}" "reuse_existing"
  echo "[ok] Playwright runtime already ready (skip install)"
  echo "[ok] State file: $STATE_FILE"
  exit 0
fi

echo "[install] Playwright runtime missing or incomplete"
install_runtime

if ! playwright_module_ready || ! chromium_ready; then
  echo "[error] Runtime install finished but verification failed" >&2
  exit 3
fi

write_state "$(now_utc)" "fresh_install"
echo "[ok] Playwright runtime installed"
echo "[ok] State file: $STATE_FILE"
