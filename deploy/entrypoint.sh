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

# Hand off to gunicorn (the Dockerfile CMD).
exec "$@"
