#!/bin/sh
# HobeRadius — backup يومي.
# يُشغّل من cron داخل container الـ backup (راجع docker-compose.yml).
# أو من cron مباشرة على VPS غير-docker.

set -e

DB_PATH="${DB_PATH:-/data/hoberadius.db}"
BACKUP_DIR="${BACKUP_DIR:-/backups}"
KEEP_DAYS="${KEEP_DAYS:-14}"

TS="$(date -u +%Y%m%d-%H%M%S)"
OUT="$BACKUP_DIR/hoberadius-$TS.db"

mkdir -p "$BACKUP_DIR"

# نسخ آمن لـ SQLite (يستخدم .backup للحصول على نسخة consistent)
sqlite3 "$DB_PATH" ".backup '$OUT'"
gzip -9 "$OUT"

# تنظيف
find "$BACKUP_DIR" -name 'hoberadius-*.db.gz' -mtime +$KEEP_DAYS -delete

echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] backup done: $OUT.gz"
