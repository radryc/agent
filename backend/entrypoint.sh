#!/usr/bin/env bash
# Start the writable MonoFS workspace (when configured) and hand off to the app.
set -euo pipefail

MONOFS_MOUNT="${MONOFS_MOUNT:-/workspace}"
MONOFS_OVERLAY="${MONOFS_OVERLAY:-/data/monofs-overlay}"
export MONOFS_OVERLAY_DIR="${MONOFS_OVERLAY_DIR:-$MONOFS_OVERLAY}"

mkdir -p "$MONOFS_MOUNT" "$MONOFS_OVERLAY"

if [ -n "${MONOFS_ROUTER:-}" ]; then
  if mountpoint -q "$MONOFS_MOUNT" 2>/dev/null; then
    echo "[entrypoint] MonoFS already mounted at $MONOFS_MOUNT"
  elif ! command -v monofs-client >/dev/null 2>&1; then
    echo "[entrypoint] monofs-client not found; skipping workspace mount" >&2
  else
    echo "[entrypoint] Starting writable MonoFS mount: $MONOFS_ROUTER -> $MONOFS_MOUNT (overlay: $MONOFS_OVERLAY)"
    nohup monofs-client \
      --router "$MONOFS_ROUTER" \
      --mount "$MONOFS_MOUNT" \
      --overlay "$MONOFS_OVERLAY" \
      --writable \
      >/data/monofs-client.log 2>&1 &
    echo "[entrypoint] monofs-client started (pid $!)"
  fi
else
  echo "[entrypoint] MONOFS_ROUTER not set; Steve workspace tools will report unavailable"
fi

exec "$@"
