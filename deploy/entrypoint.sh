#!/bin/bash
# HobeRadius — container entrypoint (Phase N4).
#
# Runs as the unprivileged `hr` user (from the Dockerfile's USER
# directive). Best-effort normalises the SQLite side-files'
# group so subsequent writes from this container don't trip the
# "attempt to write a readonly database" issue documented as #13
# in docs/radius/POSTMORTEM_PHASE_K_L_M.md.
#
# We deliberately do NOT chown the .db file itself — it's owned
# by hr already (the wizard wrote it). Only the -wal and -shm
# side-files created by the freeradius container's user (uid
# 101) need the gid bumped to 101 (which hr is in via
# supplementary group `hr-freerad-shared`). Mode 0664 makes them
# writable by both owner (freerad) and group (hr).
#
# Errors here are non-fatal. If the bind-mount is read-only or
# the files don't exist yet, the chmod simply has nothing to do
# and the container still boots.
set -e

DB_DIR=/app/instance
if [ -d "$DB_DIR" ]; then
    for f in "$DB_DIR"/*.db-wal "$DB_DIR"/*.db-shm; do
        [ -f "$f" ] || continue
        # Use chmod (we may not own the file) — making it
        # group-writable plus group=101 (which hr is in) is
        # enough. Skip silently if we can't.
        chmod g+rw "$f" 2>/dev/null || true
    done
fi

# ─── RADIUS auth instance (stress L01, 2026-09-28) ──────────────────────────
# FreeRADIUS rlm_rest used to call /api/v1/internal/* on the SAME gunicorn
# process as the admin panel: a login burst froze the panel and a busy panel
# made valid logins time out → Access-Reject. A SECOND gunicorn instance now
# serves FreeRADIUS on :8001 with its own processes/threads (mods-enabled/rest
# points there). It runs with HOBERADIUS_NO_WORKER=1 (no background threads —
# those live in the main instance only) and HOBERADIUS_NO_SEED=1.
#
# Order matters: the main instance runs the DB migrations at boot, so the auth
# instance starts only once main answers its health check (or after 180 s, so a
# slow boot can never keep logins down for good). A tiny supervisor loop
# restarts it if it ever exits. Only for the default gunicorn CMD — a one-off
# `docker compose run hoberadius <cmd>` does not spawn it.
# Opt-out: HOBERADIUS_AUTH_INSTANCE=0 (then point mods-enabled/rest back at
# :8000).
_start_auth_instance() {
    # Never let `set -e` end the supervisor: it must outlive any failure. (Its
    # exit status would also reach the main gunicorn, which reaps orphans and
    # halts on child exit codes 3/4.)
    set +e
    i=0
    while [ "$i" -lt 180 ]; do
        if curl -fsS -o /dev/null --max-time 2 \
                http://127.0.0.1:8000/admin/radius/_health 2>/dev/null; then
            break
        fi
        i=$((i + 1))
        sleep 1
    done
    while true; do
        echo "[entrypoint] starting RADIUS auth gunicorn on :8001" >&2
        HOBERADIUS_GUNICORN_ROLE=auth HOBERADIUS_NO_WORKER=1 HOBERADIUS_NO_SEED=1 \
            gunicorn -c deploy/gunicorn.conf.py wsgi:app || true
        echo "[entrypoint] RADIUS auth gunicorn exited — restarting in 2s" >&2
        sleep 2
    done
}

case "${HOBERADIUS_AUTH_INSTANCE:-1}" in
    0|false|no|off) ;;
    *)
        if [ "${1:-}" = "gunicorn" ]; then
            _start_auth_instance &
        fi
        ;;
esac

# Hand off to gunicorn (the Dockerfile CMD) — the main panel/API instance.
exec "$@"
