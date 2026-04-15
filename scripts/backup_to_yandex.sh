#!/usr/bin/env bash
set -euo pipefail

ENV_FILE="/root/FairLabQueueBot/.env"
LOCAL_DIR="/root/backups/fairlab"
REMOTE_NAME="${REMOTE_NAME:-yandex}"
REMOTE_DIR="${REMOTE_DIR:-FairLabQueueBot/backups}"
LOCAL_KEEP="${LOCAL_KEEP:-7}"
REMOTE_KEEP="${REMOTE_KEEP:-30}"

mkdir -p "$LOCAL_DIR"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "env file not found: $ENV_FILE" >&2
  exit 1
fi

DB_URL_RAW="$(grep '^DATABASE_URL=' "$ENV_FILE" | cut -d= -f2- || true)"
if [[ -z "$DB_URL_RAW" ]]; then
  echo "DATABASE_URL not found in $ENV_FILE" >&2
  exit 1
fi

# pg_dump understands postgresql:// ; normalize if +psycopg2 present
DB_URL="${DB_URL_RAW/postgresql+psycopg2:\/\//postgresql://}"

TS="$(date +%F_%H-%M)"
BASE="fairlab_queue_${TS}"
DUMP_PATH="${LOCAL_DIR}/${BASE}.sql"
SHA_PATH="${DUMP_PATH}.sha256"

pg_dump "$DB_URL" --inserts --column-inserts --no-owner --no-privileges -f "$DUMP_PATH"
sha256sum "$DUMP_PATH" > "$SHA_PATH"

rclone copyto "$DUMP_PATH" "${REMOTE_NAME}:${REMOTE_DIR}/${BASE}.sql"
rclone copyto "$SHA_PATH" "${REMOTE_NAME}:${REMOTE_DIR}/${BASE}.sql.sha256"

# Remote retention: keep only latest REMOTE_KEEP sql files (+ their sha256)
mapfile -t REMOTE_DUMPS < <(
  rclone lsf "${REMOTE_NAME}:${REMOTE_DIR}" --files-only \
    | grep -E '^fairlab_queue_[0-9]{4}-[0-9]{2}-[0-9]{2}_[0-9]{2}-[0-9]{2}\.sql$' \
    | sort
)

if (( ${#REMOTE_DUMPS[@]} > REMOTE_KEEP )); then
  TO_DELETE_COUNT=$(( ${#REMOTE_DUMPS[@]} - REMOTE_KEEP ))
  for ((i=0; i<TO_DELETE_COUNT; i++)); do
    OLD="${REMOTE_DUMPS[$i]}"
    rclone deletefile "${REMOTE_NAME}:${REMOTE_DIR}/${OLD}" || true
    rclone deletefile "${REMOTE_NAME}:${REMOTE_DIR}/${OLD}.sha256" || true
  done
fi

# Local retention: keep only latest LOCAL_KEEP sql files (+ sha256)
mapfile -t LOCAL_DUMPS < <(
  ls -1 "${LOCAL_DIR}"/fairlab_queue_*.sql 2>/dev/null | sort || true
)
if (( ${#LOCAL_DUMPS[@]} > LOCAL_KEEP )); then
  TO_DELETE_LOCAL=$(( ${#LOCAL_DUMPS[@]} - LOCAL_KEEP ))
  for ((i=0; i<TO_DELETE_LOCAL; i++)); do
    OLD_PATH="${LOCAL_DUMPS[$i]}"
    rm -f "$OLD_PATH" "${OLD_PATH}.sha256"
  done
fi

echo "backup ok: ${BASE}.sql"
