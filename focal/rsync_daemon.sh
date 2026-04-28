#!/usr/bin/env bash
set -euo pipefail

BUFFER_ROOT="${BUFFER_ROOT:-/mnt/ssd_buffer/focal}"
REMOTE_MOUNT="${REMOTE_MOUNT:-/mnt/astro_runs}"
TARGET_SUBDIR="${TARGET_SUBDIR:-FOCAL}"
INTERVAL_SECONDS="${INTERVAL_SECONDS:-30}"
LOG_FILE="${LOG_FILE:-./logs/rsync_daemon.log}"

mkdir -p "$(dirname "$LOG_FILE")"

log() {
  printf '%s | %s\n' "$(date -Iseconds)" "$1" | tee -a "$LOG_FILE"
}

run_sync() {
  local target_dir
  target_dir="${REMOTE_MOUNT%/}/${TARGET_SUBDIR}"

  if ! command -v rsync >/dev/null 2>&1; then
    log "rsync is not installed on this host"
    return 1
  fi

  if [ ! -d "$BUFFER_ROOT" ]; then
    log "buffer root missing: $BUFFER_ROOT"
    return 0
  fi

  if ! mountpoint -q "$REMOTE_MOUNT"; then
    log "remote mount unavailable: $REMOTE_MOUNT"
    return 0
  fi

  mkdir -p "$target_dir"
  log "sync start: $BUFFER_ROOT -> $target_dir"
  rsync -av --partial --inplace --ignore-existing --remove-source-files \
    "$BUFFER_ROOT/" "$target_dir/" >> "$LOG_FILE" 2>&1
  find "$BUFFER_ROOT" -type d -empty -delete || true
  log "sync complete"
}

main() {
  if [ "${1:-}" = "--once" ]; then
    run_sync
    return
  fi

  log "starting rsync daemon loop"
  while true; do
    run_sync || true
    sleep "$INTERVAL_SECONDS"
  done
}

main "$@"
