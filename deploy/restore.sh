#!/bin/sh
# HobeRadius — استعادة backup.
# الاستخدام: ./restore.sh /backups/hoberadius-YYYYMMDD-HHMMSS.db.gz

set -e
if [ -z "$1" ] || [ ! -f "$1" ]; then
    echo "usage: $0 <backup.db.gz>" >&2
    exit 1
fi

DB_PATH="${DB_PATH:-/app/instance/hoberadius.db}"
SRC="$1"

echo "stopping app (docker compose) ..."
docker compose stop app || true

echo "backing up current DB ..."
cp "$DB_PATH" "$DB_PATH.before-restore.$(date -u +%Y%m%dT%H%M%SZ)"

echo "restoring from $SRC ..."
gunzip -c "$SRC" > "$DB_PATH"

echo "starting app ..."
docker compose start app

echo "done."
