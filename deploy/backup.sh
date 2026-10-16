#!/bin/sh
# HobeRadius - daily SQLite backup, run by crond inside the backup container
# (see deploy/docker-compose.yml) or from cron on a non-docker VPS.
# Fails loudly: every outcome is written to $BACKUP_DIR/STATUS and to stdout,
# so a silent failure cannot look like a success.

set -e

# A dump holds subscriber credentials and admin hashes: never world-readable.
umask 077

DB_PATH="${DB_PATH:-/data/hoberadius.db}"
BACKUP_DIR="${BACKUP_DIR:-/backups}"
KEEP_DAYS="${KEEP_DAYS:-14}"
KEEP_MIN="${KEEP_MIN:-3}"
MIN_FREE_MB="${MIN_FREE_MB:-1024}"

TS="$(date -u +%Y%m%d-%H%M%S)"
NOW="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
OUT="$BACKUP_DIR/hoberadius-$TS.db"

mkdir -p "$BACKUP_DIR"

fail() {
    echo "[$NOW] BACKUP FAILED: $1" >&2
    echo "FAIL $TS $1" > "$BACKUP_DIR/STATUS" 2>/dev/null || true
    rm -f "$OUT" "$OUT.gz" 2>/dev/null || true
    exit 1
}

# Never build an archive on a nearly full disk.
FREE_MB="$(df -Pm "$BACKUP_DIR" | awk 'NR==2{print $4}')"
[ "${FREE_MB:-0}" -ge "$MIN_FREE_MB" ] \
    || fail "only ${FREE_MB}MB free on $BACKUP_DIR (need ${MIN_FREE_MB}MB)"

[ -f "$DB_PATH" ] || fail "database not found at $DB_PATH"

# Consistent online copy of a live (WAL) SQLite database.
sqlite3 "file:$DB_PATH?mode=ro" ".backup '$OUT'" || fail "sqlite3 .backup failed"
[ -s "$OUT" ] || fail "sqlite3 produced an empty file"

# Verify before it is allowed to count as a backup.
IC="$(sqlite3 "file:$OUT?mode=ro" 'pragma integrity_check;' 2>/dev/null | head -1)"
[ "$IC" = "ok" ] || fail "integrity_check on the new copy returned '$IC'"
ROWS="$(sqlite3 "file:$OUT?mode=ro" 'select count(*) from subscribers;' 2>/dev/null || echo 0)"
[ "${ROWS:-0}" -gt 0 ] || fail "the new copy holds no subscriber rows"

# The integrity read above leaves WAL sidecars next to the copy; drop them.
rm -f "$OUT-wal" "$OUT-shm"

gzip -9 "$OUT" || fail "gzip failed"
gzip -t "$OUT.gz" || fail "gzip integrity test failed"

SUM="$(sha256sum "$OUT.gz" | cut -d' ' -f1)"
SZ="$(wc -c < "$OUT.gz" | tr -d ' ')"
echo "OK $TS $(basename "$OUT.gz") sha256=$SUM bytes=$SZ subscribers=$ROWS" > "$BACKUP_DIR/STATUS"
echo "$SUM  $(basename "$OUT.gz")" >> "$BACKUP_DIR/SHA256SUMS"

# Retention: only now that the new copy is proven sound, and never below
# KEEP_MIN surviving copies.
COUNT="$(find "$BACKUP_DIR" -name 'hoberadius-*.db.gz' | wc -l)"
if [ "$COUNT" -gt "$KEEP_MIN" ]; then
    find "$BACKUP_DIR" -name 'hoberadius-*.db.gz' -mtime "+$KEEP_DAYS" 2>/dev/null \
    | sort | while read -r old; do
        LEFT="$(find "$BACKUP_DIR" -name 'hoberadius-*.db.gz' | wc -l)"
        [ "$LEFT" -gt "$KEEP_MIN" ] || break
        rm -f "$old" && echo "[$NOW] pruned $(basename "$old")"
    done
fi

# Cap our own log so backup bookkeeping can never grow the disk (the 188MB
# log-retention incident). Keep the tail, drop the rest.
LOGF="$BACKUP_DIR/cron.log"
if [ -f "$LOGF" ] && [ "$(wc -c < "$LOGF" | tr -d ' ')" -gt 1048576 ]; then
    tail -c 262144 "$LOGF" > "$LOGF.trim" && mv "$LOGF.trim" "$LOGF"
fi

echo "[$NOW] backup done: $OUT.gz ($SZ bytes, sha256 $SUM, $ROWS subscribers)"
