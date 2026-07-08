#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# HobeRadius — HOST self-update agent (runs OUTSIDE Docker, as root).
#
# The panel (inside the container) CANNOT rebuild itself. When the owner clicks
# «حدّث الآن», the app writes an update-request marker to a host-mounted dir.
# THIS script — installed on the host by the owner (systemd timer or --watch) —
# consumes that marker and performs the real update, safely:
#
#   1. mandatory VERIFIED backup (SQLite .backup + gzip + integrity check)
#   2. record a rollback point (current git commit + retag current image)
#   3. (optional) VERIFY the update signature/tag before applying
#   4. git fetch + checkout the requested version (a vX.Y.Z tag) or main —
#      always jumping STRAIGHT to the target (no version-by-version stepping)
#   5. docker compose build --no-cache <svc> + up -d --force-recreate
#   6. run ALL pending migrations in one pass (the runner is order-safe +
#      records each once — a v1→v4 jump applies 2,3,4 in strict order)
#   7. health-check the new container
#   8. on ANY failure → ROLLBACK code (git) AND DB (restore backup) + old image
#   9. write update-status.json throughout (running → success | failed)
#
# Idempotent, logged, safe to re-run. NEVER deletes production data.
#
# Usage:
#   hoberadius-updater.sh --once     # process one pending request then exit (timer)
#   hoberadius-updater.sh --watch    # poll every INTERVAL seconds forever
#   hoberadius-updater.sh --status   # print current status marker
# ─────────────────────────────────────────────────────────────────────────────
set -uo pipefail

UPDATER_VERSION="1"

# ── configuration (override via /etc/hoberadius/updater.env) ──────────────────
PROJECT_ROOT="${HOBERADIUS_PROJECT_ROOT:-/opt/hoberadius}"
UPDATE_DIR="${HOBERADIUS_UPDATE_DIR:-/var/lib/hoberadius}"
SERVICE="${HOBERADIUS_SERVICE:-hoberadius}"          # compose service + container name
DB_PATH="${HOBERADIUS_DB_PATH:-$PROJECT_ROOT/instance/hoberadius.db}"
BACKUP_DIR="${HOBERADIUS_BACKUP_DIR:-$PROJECT_ROOT/backups}"
LOG_FILE="${HOBERADIUS_UPDATER_LOG:-/var/log/hoberadius-updater.log}"
WATCH_INTERVAL="${HOBERADIUS_UPDATER_INTERVAL:-30}"
HEALTH_TIMEOUT="${HOBERADIUS_UPDATER_HEALTH_TIMEOUT:-120}"   # seconds to wait for healthy
# Signature policy: set REQUIRE_SIGNATURE=1 in production. See verify_target().
REQUIRE_SIGNATURE="${HOBERADIUS_UPDATE_REQUIRE_SIGNATURE:-0}"
GPG_KEYRING="${HOBERADIUS_UPDATE_GPG_KEYRING:-}"     # optional: path to a keyring for git verify-tag

[ -f /etc/hoberadius/updater.env ] && . /etc/hoberadius/updater.env

COMPOSE="docker compose -f $PROJECT_ROOT/deploy/docker-compose.yml"
REQ_FILE="$UPDATE_DIR/update-request.json"
STATUS_FILE="$UPDATE_DIR/update-status.json"
ROLLBACK_IMAGE="hoberadius:rollback"
LIVE_IMAGE="hoberadius:latest"

# ── logging ───────────────────────────────────────────────────────────────────
log() { echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] $*" | tee -a "$LOG_FILE" >&2; }

# ── json helpers (python3 is always present on the host per deploy README) ────
json_get() {  # json_get <file> <key>
    python3 - "$1" "$2" <<'PY' 2>/dev/null || true
import json, sys
try:
    with open(sys.argv[1], encoding="utf-8") as f:
        d = json.load(f)
    v = d.get(sys.argv[2], "")
    print(v if v is not None else "")
except Exception:
    print("")
PY
}

# write_status <state> <log-text> [finished]
write_status() {
    local state="$1"; local logtext="$2"; local finished="${3:-}"
    mkdir -p "$UPDATE_DIR"
    REQ_AT="$REQ_AT" STATE="$state" LOGTEXT="$logtext" FINISHED="$finished" \
    TGT="${REQ_VERSION:-}" UV="$UPDATER_VERSION" \
    python3 - "$STATUS_FILE" <<'PY'
import json, os, sys, datetime
out = {
    "state": os.environ.get("STATE", ""),
    "log": os.environ.get("LOGTEXT", ""),
    "request_at": os.environ.get("REQ_AT", ""),
    "requested_version": os.environ.get("TGT", ""),
    "updater_version": os.environ.get("UV", ""),
    "updated_at": datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
}
if os.environ.get("FINISHED"):
    out["finished_at"] = out["updated_at"]
tmp = sys.argv[1] + ".tmp"
with open(tmp, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=2)
os.replace(tmp, sys.argv[1])
PY
}

# ── backup (verified) ─────────────────────────────────────────────────────────
make_backup() {
    mkdir -p "$BACKUP_DIR"
    local ts out
    ts="$(date -u +%Y%m%d-%H%M%S)"
    out="$BACKUP_DIR/preupdate-$ts.db"
    log "backup: $DB_PATH → $out.gz"
    if [ ! -f "$DB_PATH" ]; then
        log "backup: DB not found at $DB_PATH — aborting (refuse to update without a backup)"
        return 1
    fi
    # Consistent online copy (SQLite .backup), then integrity check, then gzip.
    sqlite3 "$DB_PATH" ".backup '$out'" || { log "backup: .backup failed"; return 1; }
    local chk
    chk="$(sqlite3 "$out" 'PRAGMA integrity_check;' 2>/dev/null | head -1)"
    if [ "$chk" != "ok" ]; then
        log "backup: integrity_check FAILED ($chk) — aborting"
        rm -f "$out"
        return 1
    fi
    gzip -9 "$out" || { log "backup: gzip failed"; return 1; }
    BACKUP_GZ="$out.gz"
    log "backup: verified OK ($BACKUP_GZ)"
    return 0
}

# ── git ref resolution (STRAIGHT to target — never step-by-step) ──────────────
resolve_ref() {  # echoes the git ref to checkout for a requested version
    local want="$1"
    case "$want" in
        ""|latest|main|LATEST|MAIN) echo "main"; return 0 ;;
    esac
    git -C "$PROJECT_ROOT" fetch --tags --quiet origin 2>>"$LOG_FILE" || true
    if git -C "$PROJECT_ROOT" rev-parse -q --verify "refs/tags/v$want" >/dev/null; then
        echo "v$want"; return 0
    fi
    if git -C "$PROJECT_ROOT" rev-parse -q --verify "refs/tags/$want" >/dev/null; then
        echo "$want"; return 0
    fi
    log "resolve_ref: no tag for '$want' — falling back to main (latest)"
    echo "main"
}

# ── signature/trust verification (documented in RUNBOOK) ──────────────────────
verify_target() {  # verify_target <ref>
    local ref="$1"
    if [ "$REQUIRE_SIGNATURE" != "1" ]; then
        # Best-effort: try to verify a signed tag if a keyring is configured.
        if [ -n "$GPG_KEYRING" ] && [ "$ref" != "main" ]; then
            if git -C "$PROJECT_ROOT" verify-tag "$ref" >>"$LOG_FILE" 2>&1; then
                log "verify: tag $ref signature OK (advisory)"
            else
                log "verify: tag $ref NOT verified (advisory; REQUIRE_SIGNATURE=0 → proceeding)"
            fi
        fi
        return 0
    fi
    # Enforced: a moving branch cannot be trust-pinned — refuse.
    if [ "$ref" = "main" ]; then
        log "verify: REQUIRE_SIGNATURE=1 but target is 'main' (unsigned moving branch) — refusing"
        return 1
    fi
    if ! git -C "$PROJECT_ROOT" verify-tag "$ref" >>"$LOG_FILE" 2>&1; then
        log "verify: signed-tag verification FAILED for $ref — refusing to apply"
        return 1
    fi
    log "verify: signed-tag $ref verification OK"
    return 0
}

# ── health check ──────────────────────────────────────────────────────────────
wait_healthy() {
    local deadline=$(( $(date +%s) + HEALTH_TIMEOUT ))
    while [ "$(date +%s)" -lt "$deadline" ]; do
        local hs
        hs="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$SERVICE" 2>/dev/null || echo missing)"
        if [ "$hs" = "healthy" ]; then return 0; fi
        # If the image has no HEALTHCHECK, fall back to an HTTP probe.
        if [ "$hs" = "none" ]; then
            if curl -fsS http://127.0.0.1:8000/admin/radius/_healthz >/dev/null 2>&1; then return 0; fi
        fi
        if [ "$hs" = "missing" ]; then log "health: container $SERVICE not found"; fi
        sleep 4
    done
    return 1
}

# ── migrations (all pending, one pass, order-safe) ────────────────────────────
run_migrations() {
    # The app already runs migrations at boot; re-running is idempotent and
    # gives an explicit pass/fail signal. Applies ALL pending in strict order.
    log "migrations: applying all pending (one pass)"
    if docker exec "$SERVICE" python -c \
        "from app.radius.db import run_pending_migrations as r; print('migrations applied:', r())" \
        >>"$LOG_FILE" 2>&1; then
        log "migrations: OK"
        return 0
    fi
    log "migrations: FAILED"
    return 1
}

# ── rollback (code AND DB) ────────────────────────────────────────────────────
rollback() {
    local reason="$1"
    log "ROLLBACK: $reason"
    write_status "running" "فشل التحديث ($reason) — جارٍ الاستعادة (الكود + قاعدة البيانات)…"

    $COMPOSE stop "$SERVICE" >>"$LOG_FILE" 2>&1 || true

    # 1) DB — restore the verified pre-update backup.
    if [ -n "${BACKUP_GZ:-}" ] && [ -f "$BACKUP_GZ" ]; then
        log "rollback: restoring DB from $BACKUP_GZ"
        gunzip -c "$BACKUP_GZ" > "$DB_PATH" || log "rollback: DB restore FAILED"
    else
        log "rollback: no backup available — DB left as-is"
    fi

    # 2) Code — return to the previous commit.
    if [ -n "${PREV_COMMIT:-}" ]; then
        log "rollback: git reset --hard $PREV_COMMIT"
        git -C "$PROJECT_ROOT" reset --hard "$PREV_COMMIT" >>"$LOG_FILE" 2>&1 || log "rollback: git reset FAILED"
    fi

    # 3) Image — reuse the exact previous image (no rebuild needed).
    if docker image inspect "$ROLLBACK_IMAGE" >/dev/null 2>&1; then
        docker tag "$ROLLBACK_IMAGE" "$LIVE_IMAGE" >>"$LOG_FILE" 2>&1 || true
        $COMPOSE up -d --no-build --force-recreate "$SERVICE" >>"$LOG_FILE" 2>&1 || true
    else
        # No saved image — rebuild the previous code.
        $COMPOSE up -d --build --force-recreate "$SERVICE" >>"$LOG_FILE" 2>&1 || true
    fi

    if wait_healthy; then
        log "rollback: complete, service healthy on previous version"
        write_status "failed" "فشل التحديث ($reason). تمّت استعادة النظام إلى إصداره السابق (الكود + قاعدة البيانات) وهو يعمل الآن." finished
    else
        log "rollback: service still UNHEALTHY after restore — MANUAL INTERVENTION NEEDED"
        write_status "failed" "فشل التحديث ($reason) وتعذّرت الاستعادة التلقائية. يلزم تدخّل يدويّ على الخادم." finished
    fi
}

# ── main update flow ──────────────────────────────────────────────────────────
process_request() {
    [ -f "$REQ_FILE" ] || return 0

    REQ_AT="$(json_get "$REQ_FILE" requested_at)"
    REQ_VERSION="$(json_get "$REQ_FILE" requested_version)"
    REQ_BY="$(json_get "$REQ_FILE" requested_by_name)"

    # Idempotency: skip if we already reported a terminal state for THIS request.
    if [ -f "$STATUS_FILE" ]; then
        local s_at s_state
        s_at="$(json_get "$STATUS_FILE" request_at)"
        s_state="$(json_get "$STATUS_FILE" state)"
        if [ "$s_at" = "$REQ_AT" ] && { [ "$s_state" = "success" ] || [ "$s_state" = "failed" ]; }; then
            return 0   # already processed
        fi
    fi

    log "── update request: version='$REQ_VERSION' by='$REQ_BY' at='$REQ_AT' ──"
    write_status "running" "بدء التحديث — يُؤخذ نسخ احتياطيّ أولًا…"

    # Record rollback point.
    PREV_COMMIT="$(git -C "$PROJECT_ROOT" rev-parse HEAD 2>/dev/null || echo '')"
    log "rollback point: commit=$PREV_COMMIT"
    if docker image inspect "$LIVE_IMAGE" >/dev/null 2>&1; then
        docker tag "$LIVE_IMAGE" "$ROLLBACK_IMAGE" >>"$LOG_FILE" 2>&1 || true
    fi

    # 1) Backup (mandatory + verified).
    if ! make_backup; then
        write_status "failed" "تعذّر أخذ نسخة احتياطيّة موثّقة — أُلغي التحديث ولم يتغيّر شيء." finished
        return 0
    fi

    # 2) Resolve + verify target.
    write_status "running" "التحقّق من الإصدار المطلوب…"
    local ref; ref="$(resolve_ref "$REQ_VERSION")"
    log "target ref: $ref"
    if ! verify_target "$ref"; then
        write_status "failed" "تعذّر التحقّق من توقيع التحديث — أُلغي التحديث (لم يتغيّر شيء)." finished
        return 0
    fi

    # 3) Fetch + checkout STRAIGHT to target.
    write_status "running" "جلب التحديث وتبديل الكود…"
    git -C "$PROJECT_ROOT" fetch --tags --quiet origin >>"$LOG_FILE" 2>&1 || true
    if [ "$ref" = "main" ]; then
        if ! git -C "$PROJECT_ROOT" reset --hard origin/main >>"$LOG_FILE" 2>&1; then
            rollback "git checkout main failed"; return 0
        fi
    else
        if ! git -C "$PROJECT_ROOT" checkout -f "$ref" >>"$LOG_FILE" 2>&1; then
            rollback "git checkout $ref failed"; return 0
        fi
    fi

    # 4) Build + recreate.
    write_status "running" "بناء الصورة الجديدة وإعادة التشغيل…"
    if ! $COMPOSE build --no-cache "$SERVICE" >>"$LOG_FILE" 2>&1; then
        rollback "docker build failed"; return 0
    fi
    if ! $COMPOSE up -d --force-recreate "$SERVICE" >>"$LOG_FILE" 2>&1; then
        rollback "docker up failed"; return 0
    fi

    # 5) Wait for the new container to be healthy (catches boot-time migration
    #    crashes on a big multi-version jump before we even reach step 6).
    write_status "running" "فحص صحّة النظام الجديد…"
    if ! wait_healthy; then
        rollback "health check failed after update"; return 0
    fi

    # 6) Apply ALL pending migrations explicitly (idempotent verification).
    if ! run_migrations; then
        rollback "migrations failed"; return 0
    fi

    # 7) Final health re-confirm.
    if ! wait_healthy; then
        rollback "health check failed after migrations"; return 0
    fi

    # Success — consume the request marker (idempotency) + drop the rollback image.
    NEW_COMMIT="$(git -C "$PROJECT_ROOT" rev-parse HEAD 2>/dev/null || echo '')"
    log "update SUCCESS: $PREV_COMMIT → $NEW_COMMIT ($ref)"
    write_status "success" "تم التحديث بنجاح إلى $REQ_VERSION." finished
    mv -f "$REQ_FILE" "$UPDATE_DIR/update-request.done.json" 2>/dev/null || rm -f "$REQ_FILE"
    docker image rm "$ROLLBACK_IMAGE" >/dev/null 2>&1 || true
    docker image prune -f >>"$LOG_FILE" 2>&1 || true
}

main() {
    mkdir -p "$UPDATE_DIR" "$BACKUP_DIR"
    touch "$LOG_FILE" 2>/dev/null || true
    case "${1:---once}" in
        --once)
            process_request
            ;;
        --watch)
            log "updater watch loop started (interval=${WATCH_INTERVAL}s)"
            while true; do
                process_request
                sleep "$WATCH_INTERVAL"
            done
            ;;
        --status)
            [ -f "$STATUS_FILE" ] && cat "$STATUS_FILE" || echo '{"state":"idle"}'
            ;;
        *)
            echo "usage: $0 [--once|--watch|--status]" >&2
            exit 2
            ;;
    esac
}

main "$@"
