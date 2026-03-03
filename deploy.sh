#!/usr/bin/env bash
set -euo pipefail

# Sync only source files (tracked by git) to a remote machine over SSH.
#
# Usage:
#   ./deploy.sh
#   ./deploy.sh -r
#   ./deploy.sh [-r] [user@host:/absolute/remote/path]
#
# Optional env vars:
#   SSH_PORT=22
#   SSH_KEY=~/.ssh/id_rsa

DEFAULT_DEST="eric@worker1_zerotier:/home/eric/repos/nanobot"

DEST="$DEFAULT_DEST"
RUN_REAL=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    -r)
      RUN_REAL=1
      shift
      ;;
    -h|--help)
      echo "Usage: $0 [-r] [user@host:/absolute/remote/path]"
      echo "  default: dry-run preview"
      echo "  -r:      execute real sync"
      exit 0
      ;;
    *)
      if [[ "$1" == -* ]]; then
        echo "Unknown option: $1"
        exit 1
      fi
      DEST="$1"
      shift
      ;;
  esac
done

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT_DIR"

if ! command -v git >/dev/null 2>&1; then
  echo "Error: git is required."
  exit 1
fi

if ! command -v rsync >/dev/null 2>&1; then
  echo "Error: rsync is required."
  exit 1
fi

SSH_PORT="${SSH_PORT:-22}"
SSH_KEY="${SSH_KEY:-}"
SSH_CMD=(ssh -p "$SSH_PORT")
if [[ -n "$SSH_KEY" ]]; then
  SSH_CMD+=(-i "$SSH_KEY")
fi

# Sync tracked + untracked (but not ignored) files.
FILES_LIST="$(mktemp)"
trap 'rm -f "$FILES_LIST"' EXIT
{
  git ls-files -z
  git ls-files --others --exclude-standard -z
} > "$FILES_LIST"

echo "Syncing git-tracked files to: $DEST"
if [[ "$RUN_REAL" -eq 0 ]]; then
  echo "Mode: dry-run (preview only, no remote changes)"
else
  echo "Mode: real sync"
fi

RSYNC_ARGS=(
  -rz
  --delete
  --checksum
  --no-times
  --no-perms
  --no-owner
  --no-group
  --executability
  --itemize-changes
  --out-format=%i'|'%n%L
  --from0
  --files-from="$FILES_LIST"
  -e "${SSH_CMD[*]}"
)

if [[ "$RUN_REAL" -eq 0 ]]; then
  RSYNC_ARGS+=(--dry-run)
fi

RSYNC_LOG="$(mktemp)"
MOD_LIST="$(mktemp)"
DEL_LIST="$(mktemp)"
trap 'rm -f "$FILES_LIST" "$RSYNC_LOG" "$MOD_LIST" "$DEL_LIST"' EXIT

if ! rsync "${RSYNC_ARGS[@]}" "$ROOT_DIR/" "$DEST/" 2>&1 | tee "$RSYNC_LOG" >/dev/null; then
  echo "rsync failed. Raw output:"
  cat "$RSYNC_LOG"
  exit 1
fi

awk -F'\\|' '
  /^\*deleting / {
    path = $0
    sub(/^\*deleting +/, "", path)
    if (length(path) > 0) print path
    next
  }
' "$RSYNC_LOG" > "$DEL_LIST"

awk -F'\\|' '
  /^[^|]+\|/ {
    code = $1
    path = $2
    if (code ~ /^\*deleting /) next
    if (length(path) > 0) print path
  }
' "$RSYNC_LOG" > "$MOD_LIST"

echo
echo "Changed / Added:"
if [[ -s "$MOD_LIST" ]]; then
  cat "$MOD_LIST"
else
  echo "(none)"
fi

echo
echo "Deleted:"
if [[ -s "$DEL_LIST" ]]; then
  cat "$DEL_LIST"
else
  echo "(none)"
fi

echo
echo "Done."
