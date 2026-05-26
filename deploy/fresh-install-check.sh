#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
# HobeRadius — Fresh-Install Verification
# ─────────────────────────────────────────────────────────────────────────────
# Run after a fresh server format + deploy to verify every prerequisite
# captured in postmortems #1-#22 is in place. Prints a colored report and
# returns non-zero if any check failed.
#
# Usage:
#   sudo bash /opt/hoberadius/deploy/fresh-install-check.sh
# ─────────────────────────────────────────────────────────────────────────────

set -u

RED=$(printf '\033[31m'); GREEN=$(printf '\033[32m')
YELLOW=$(printf '\033[33m'); BLUE=$(printf '\033[34m')
RESET=$(printf '\033[0m')

OK=0; WARN=0; FAIL=0

ok()   { echo "${GREEN}✓${RESET} $*"; OK=$((OK+1)); }
warn() { echo "${YELLOW}⚠${RESET} $*"; WARN=$((WARN+1)); }
fail() { echo "${RED}✗${RESET} $*"; FAIL=$((FAIL+1)); }
sec()  { echo; echo "${BLUE}── $* ──${RESET}"; }

# ─── 1) Host prerequisites ──────────────────────────────────────────────────
sec "Host prerequisites"

if command -v docker >/dev/null; then
    ok "Docker installed: $(docker --version | awk '{print $3}' | tr -d ',')"
else
    fail "Docker not installed — install Docker + compose plugin first"
fi

if docker compose version >/dev/null 2>&1; then
    ok "Docker Compose v2 available"
else
    fail "Docker Compose v2 not available"
fi

if command -v wg >/dev/null; then
    ok "wireguard-tools installed"
else
    fail "wireguard-tools missing — apt install wireguard"
fi

if ip link show wg0 >/dev/null 2>&1; then
    ok "wg0 interface UP on host"
else
    fail "wg0 interface NOT UP — configure /etc/wireguard/wg0.conf + wg-quick up wg0"
fi

# ─── 2) /etc/hoberadius/ directories ────────────────────────────────────────
sec "Host directories"

for d in /etc/hoberadius/wg-peers.d /etc/hoberadius/nginx-streams.d; do
    if [ -d "$d" ]; then
        gid=$(stat -c %g "$d")
        if [ "$gid" = "999" ]; then
            ok "$d exists (gid=999 OK)"
        else
            warn "$d exists but gid=$gid (should be 999 — run deploy.sh init-wg-reloader)"
        fi
    else
        fail "$d MISSING — run: sudo bash $(dirname "$0")/deploy.sh init-wg-reloader"
    fi
done

# ─── 3) systemd wg-reload unit ──────────────────────────────────────────────
sec "wg-reload systemd unit"

if systemctl is-active --quiet wg-reload.path 2>/dev/null; then
    ok "wg-reload.path active — peer files trigger wg syncconf"
elif [ -f /etc/systemd/system/wg-reload.path ]; then
    warn "wg-reload.path installed but not active — systemctl enable --now wg-reload.path"
else
    fail "wg-reload.path NOT installed — wizard-provisioned peers won't load"
fi

# ─── 4) .env file ───────────────────────────────────────────────────────────
sec "Environment variables"

ENV_FILE=/opt/hoberadius/.env
if [ -f "$ENV_FILE" ]; then
    ok ".env present"
    for var in HOBERADIUS_INTERNAL_SECRET HOBERADIUS_WG_SERVER_PUBKEY HOBERADIUS_WG_SERVER_ENDPOINT; do
        if grep -q "^$var=." "$ENV_FILE" 2>/dev/null; then
            ok "  $var is set"
        else
            fail "  $var missing or empty in .env"
        fi
    done
else
    fail ".env missing — create /opt/hoberadius/.env with required secrets"
fi

# ─── 5) Containers running ──────────────────────────────────────────────────
sec "Containers"

for c in hoberadius hoberadius-freeradius hoberadius-nginx; do
    if docker inspect "$c" >/dev/null 2>&1; then
        status=$(docker inspect -f '{{.State.Status}}' "$c" 2>/dev/null)
        if [ "$status" = "running" ]; then
            ok "$c running"
        else
            fail "$c status=$status (expected: running)"
        fi
    else
        fail "$c container missing — docker compose up -d --build"
    fi
done

# ─── 6) FreeRADIUS host networking ──────────────────────────────────────────
sec "FreeRADIUS network mode"

if docker inspect hoberadius-freeradius >/dev/null 2>&1; then
    mode=$(docker inspect -f '{{.HostConfig.NetworkMode}}' hoberadius-freeradius 2>/dev/null)
    if [ "$mode" = "host" ]; then
        ok "freeradius using network_mode: host (postmortem #19)"
    else
        fail "freeradius NOT on host networking (mode=$mode) — CoA + per-router secrets will break"
    fi
fi

# ─── 7) Migrations applied ──────────────────────────────────────────────────
sec "Database schema"

if docker exec hoberadius python -c "
from app import create_app
app = create_app()
with app.app_context():
    from app.radius.db.connection import db
    cols = {r['name'] for r in db().execute('PRAGMA table_info(setup_wizard_runs)').fetchall()}
    needed = {'state_json', 'v3_state', 'v3_diagnostics_json'}
    missing = needed - cols
    if missing:
        print('FAIL:', sorted(missing))
        exit(1)
" 2>/dev/null; then
    ok "setup_wizard_runs has all v3 columns"
else
    fail "setup_wizard_runs missing critical columns — migrations didn't fully run"
fi

# ─── 8) System health endpoint ──────────────────────────────────────────────
sec "Wizard system health"

if response=$(curl -s -o /tmp/_sh.json -w '%{http_code}' http://localhost/admin/radius/setup-wizard/_system_health 2>/dev/null); then
    if [ "$response" = "200" ]; then
        overall=$(python3 -c "import json;print(json.load(open('/tmp/_sh.json'))['overall'])" 2>/dev/null)
        if [ "$overall" = "healthy" ]; then
            ok "system_health: healthy"
        elif [ "$overall" = "degraded" ]; then
            warn "system_health: degraded (functional, investigate audit_log)"
        else
            fail "system_health: unexpected status='$overall'"
        fi
    elif [ "$response" = "503" ]; then
        fail "system_health returned 503 — at least one check failed"
        python3 -c "
import json
d = json.load(open('/tmp/_sh.json'))
for name, c in d['checks'].items():
    if c['status'] != 'ok':
        print(f\"    {c['status']}: {name} — {c['details']}\")
" 2>/dev/null
    else
        fail "system_health endpoint returned HTTP $response"
    fi
else
    fail "Cannot reach http://localhost/admin/radius/setup-wizard/_system_health"
fi

rm -f /tmp/_sh.json

# ─── Summary ────────────────────────────────────────────────────────────────
echo
echo "${BLUE}─── Summary ───${RESET}"
echo "  ${GREEN}OK:${RESET}   $OK"
echo "  ${YELLOW}WARN:${RESET} $WARN"
echo "  ${RED}FAIL:${RESET} $FAIL"
echo

if [ "$FAIL" -gt 0 ]; then
    echo "${RED}Fresh install is NOT ready — fix the FAIL items above before going live.${RESET}"
    exit 1
fi
if [ "$WARN" -gt 0 ]; then
    echo "${YELLOW}Fresh install ready with caveats — review WARN items.${RESET}"
    exit 0
fi
echo "${GREEN}✓ Fresh install fully verified.${RESET}"
exit 0
