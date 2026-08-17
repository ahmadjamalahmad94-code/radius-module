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

# ‏--env-file صريح — انظر التعليق في deploy/deploy.sh. مهمٌّ هنا تحديدًا: هذا
# الوكيل يُعيد إنشاء الحاويات دوريًّا، فبدونه يدهس التحديثُ الذاتيّ خريطةَ منافذ
# مضبوطةً في .env (مثلًا اللوحة على :443) ويعيدها للافتراضيّ.
COMPOSE="docker compose --env-file $PROJECT_ROOT/.env -f $PROJECT_ROOT/deploy/docker-compose.yml"
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

# ── granular status writer (host agent → panel) ───────────────────────────────
# The status marker carries live staged progress the panel renders as a bar +
# a "what's happening" log tail. All fields are additive (backward-compatible).
#   state | stage | stage_label | percent | log | error | failed_stage |
#   rolled_back | request_at | requested_version | updated_at | finished_at
STATE="running"; STAGE=""; STAGE_LABEL=""; PERCENT=0
ERROR=""; FAILED_STAGE=""; ROLLED_BACK="0"; FINISHED=""
PROGRESS_LOG=""   # curated newline-separated tail (NOT the verbose build log)

# Prefix each line with a MACHINE-READABLE full ISO-8601 UTC timestamp (not a
# baked local/UTC display string). The panel converts it to the tenant-local
# timezone at render time, so the log reads correctly regardless of the host's
# clock/zone. Format kept as "<ISO8601Z> — <message>".
append_log() { PROGRESS_LOG="${PROGRESS_LOG}$(date -u +%Y-%m-%dT%H:%M:%SZ) — $1"$'\n'; }

# Serialise the current globals to the status marker atomically.
_flush_status() {
    mkdir -p "$UPDATE_DIR"
    REQ_AT="$REQ_AT" STATE="$STATE" STAGE="$STAGE" STAGE_LABEL="$STAGE_LABEL" \
    PERCENT="$PERCENT" ERROR="$ERROR" FAILED_STAGE="$FAILED_STAGE" \
    ROLLED_BACK="$ROLLED_BACK" FINISHED="$FINISHED" LOGTEXT="$PROGRESS_LOG" \
    TGT="${REQ_VERSION:-}" UV="$UPDATER_VERSION" \
    python3 - "$STATUS_FILE" <<'PY'
import json, os, sys, datetime
log = os.environ.get("LOGTEXT", "")
lines = [l for l in log.splitlines() if l.strip()]
try:
    pct = max(0, min(100, int(float(os.environ.get("PERCENT", "0") or 0))))
except ValueError:
    pct = 0
out = {
    "state": os.environ.get("STATE", ""),
    "stage": os.environ.get("STAGE", ""),
    "stage_label": os.environ.get("STAGE_LABEL", ""),
    "percent": pct,
    "log": "\n".join(lines[-12:]),          # last N curated lines
    "request_at": os.environ.get("REQ_AT", ""),
    "requested_version": os.environ.get("TGT", ""),
    "updater_version": os.environ.get("UV", ""),
    "updated_at": datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
}
if os.environ.get("ERROR"):
    out["error"] = os.environ["ERROR"]
if os.environ.get("FAILED_STAGE"):
    out["failed_stage"] = os.environ["FAILED_STAGE"]
if os.environ.get("ROLLED_BACK") == "1":
    out["rolled_back"] = True
if os.environ.get("FINISHED"):
    out["finished_at"] = out["updated_at"]
tmp = sys.argv[1] + ".tmp"
with open(tmp, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=2)
os.replace(tmp, sys.argv[1])
PY
}

# stage <key> <percent> <arabic-label> — mark a running stage + flush live.
stage() {
    STATE="running"; STAGE="$1"; PERCENT="$2"; STAGE_LABEL="$3"; FINISHED=""
    append_log "$3"
    log "stage: $1 (${2}%) — $3"
    _flush_status
}

# finish_success <arabic-label>
finish_success() {
    STATE="success"; STAGE="done"; PERCENT=100; STAGE_LABEL="$1"; FINISHED="1"
    append_log "$1"
    _flush_status
}

# finish_failed <failed_stage_key> <arabic-label> <error> <rolled_back 0|1>
# Keeps PERCENT at whatever the last stage reached (the bar shows where it died).
finish_failed() {
    STATE="failed"; FAILED_STAGE="$1"; STAGE="$1"; STAGE_LABEL="$2"
    ERROR="$3"; ROLLED_BACK="${4:-0}"; FINISHED="1"
    append_log "فشل عند مرحلة: $2"
    [ -n "$3" ] && append_log "الخطأ: $3"
    [ "${4:-0}" = "1" ] && append_log "تمّ التراجع للنسخة السابقة (الكود + قاعدة البيانات)."
    _flush_status
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
# _restore rolls the code+DB+image back to the pre-update state and returns 0 if
# the service comes back healthy, 1 otherwise. It does NOT write the terminal
# status — the caller (fail_with) owns the failed-state message + stage/percent.
_restore() {
    log "RESTORE: rolling code + DB back to pre-update state"
    append_log "جارٍ التراجع (الكود + قاعدة البيانات)…"
    _flush_status
    $COMPOSE stop "$SERVICE" >>"$LOG_FILE" 2>&1 || true

    # 1) DB — restore the verified pre-update backup.
    if [ -n "${BACKUP_GZ:-}" ] && [ -f "$BACKUP_GZ" ]; then
        log "restore: DB from $BACKUP_GZ"
        gunzip -c "$BACKUP_GZ" > "$DB_PATH" || log "restore: DB restore FAILED"
    else
        log "restore: no backup available — DB left as-is"
    fi

    # 2) Code — return to the previous commit.
    if [ -n "${PREV_COMMIT:-}" ]; then
        log "restore: git reset --hard $PREV_COMMIT"
        git -C "$PROJECT_ROOT" reset --hard "$PREV_COMMIT" >>"$LOG_FILE" 2>&1 || log "restore: git reset FAILED"
    fi

    # 3) Image — reuse the exact previous image (no rebuild needed).
    if docker image inspect "$ROLLBACK_IMAGE" >/dev/null 2>&1; then
        docker tag "$ROLLBACK_IMAGE" "$LIVE_IMAGE" >>"$LOG_FILE" 2>&1 || true
        $COMPOSE up -d --no-build --force-recreate "$SERVICE" >>"$LOG_FILE" 2>&1 || true
    else
        $COMPOSE up -d --build --force-recreate "$SERVICE" >>"$LOG_FILE" 2>&1 || true
    fi

    if wait_healthy; then
        log "restore: complete, service healthy on previous version"
        return 0
    fi
    log "restore: service still UNHEALTHY — MANUAL INTERVENTION NEEDED"
    return 1
}

# fail_with <failed_stage_key> <arabic-stage-label> <error> — restore + report.
fail_with() {
    local key="$1" label="$2" err="$3"
    log "FAIL at stage=$key: $err"
    if _restore; then
        finish_failed "$key" "$label" "$err" 1
    else
        finish_failed "$key" "$label" "$err — وتعذّرت الاستعادة التلقائية (يلزم تدخّل يدويّ)" 1
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
    PROGRESS_LOG=""   # fresh curated tail for this request
    # ── stage: بدء (5%) ──
    stage "start" 5 "بدء التحديث"

    # Record rollback point.
    PREV_COMMIT="$(git -C "$PROJECT_ROOT" rev-parse HEAD 2>/dev/null || echo '')"
    log "rollback point: commit=$PREV_COMMIT"
    if docker image inspect "$LIVE_IMAGE" >/dev/null 2>&1; then
        docker tag "$LIVE_IMAGE" "$ROLLBACK_IMAGE" >>"$LOG_FILE" 2>&1 || true
    fi

    # ── stage: نسخة احتياطية (20%) — nothing changed yet, so no rollback on fail ──
    stage "backup" 20 "أخذ نسخة احتياطيّة موثّقة"
    if ! make_backup; then
        finish_failed "backup" "أخذ نسخة احتياطيّة موثّقة" \
            "تعذّر أخذ نسخة احتياطيّة (فشل .backup أو فحص السلامة) — أُلغي التحديث ولم يتغيّر شيء." 0
        return 0
    fi

    # ── stage: سحب التحديث / git (40%) ──
    stage "fetch" 40 "سحب التحديث وتبديل الكود"
    local ref; ref="$(resolve_ref "$REQ_VERSION")"
    log "target ref: $ref"
    if ! verify_target "$ref"; then
        finish_failed "verify" "التحقّق من توقيع التحديث" \
            "تعذّر التحقّق من توقيع التحديث — أُلغي التحديث (لم يتغيّر شيء)." 0
        return 0
    fi
    git -C "$PROJECT_ROOT" fetch --tags --quiet origin >>"$LOG_FILE" 2>&1 || true
    if [ "$ref" = "main" ]; then
        git -C "$PROJECT_ROOT" reset --hard origin/main >>"$LOG_FILE" 2>&1 \
            || { fail_with "fetch" "سحب التحديث وتبديل الكود" "git checkout main failed"; return 0; }
    else
        git -C "$PROJECT_ROOT" checkout -f "$ref" >>"$LOG_FILE" 2>&1 \
            || { fail_with "fetch" "سحب التحديث وتبديل الكود" "git checkout $ref failed"; return 0; }
    fi

    # ── stage: بناء الصورة (65%) — build --no-cache + recreate ──
    stage "build" 65 "بناء الصورة الجديدة وإعادة التشغيل"
    if ! $COMPOSE build --no-cache "$SERVICE" >>"$LOG_FILE" 2>&1; then
        fail_with "build" "بناء الصورة الجديدة" "docker build --no-cache failed"; return 0
    fi
    if ! $COMPOSE up -d --force-recreate "$SERVICE" >>"$LOG_FILE" 2>&1; then
        fail_with "build" "إعادة تشغيل الحاوية" "docker up --force-recreate failed"; return 0
    fi
    # Boot-time health guard (catches a migration crash at container start on a
    # big multi-version jump before we reach the explicit migration stage).
    if ! wait_healthy; then
        fail_with "build" "إقلاع الحاوية الجديدة" "container unhealthy right after recreate"; return 0
    fi

    # ── stage: تشغيل الترحيلات (85%) — ALL pending in one pass ──
    stage "migrations" 85 "تشغيل ترحيلات قاعدة البيانات"
    if ! run_migrations; then
        fail_with "migrations" "تشغيل ترحيلات قاعدة البيانات" "one or more migrations failed"; return 0
    fi

    # ── stage: فحص الصحة (95%) ──
    stage "health" 95 "فحص صحّة النظام الجديد"
    if ! wait_healthy; then
        fail_with "health" "فحص صحّة النظام" "health check failed after migrations"; return 0
    fi

    # ── stage: اكتمل (100%) ──
    NEW_COMMIT="$(git -C "$PROJECT_ROOT" rev-parse HEAD 2>/dev/null || echo '')"
    log "update SUCCESS: $PREV_COMMIT → $NEW_COMMIT ($ref)"
    finish_success "تمّ التحديث بنجاح إلى ${REQ_VERSION}"
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
