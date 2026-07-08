#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# install-updater.sh — install the HobeRadius HOST self-update agent.
#
# Idempotent, re-runnable helper that wires the host-side self-update agent so a
# fresh VPS has «حدّث الآن» working out of the box — with ZERO manual steps.
# It is called by:
#   • deploy/provision/provision-fresh-vps.sh  (fresh install)
#   • deploy/deploy.sh init|upgrade            (existing installs, on next deploy)
# and can also be run standalone by an operator:  sudo bash install-updater.sh
#
# What it does (all steps idempotent + tolerant — NEVER aborts the caller):
#   1. Ensure the shared dir /var/lib/hoberadius exists (gid 999, mode 2775).
#   2. install -m 0755 hoberadius-updater.sh → /usr/local/bin/.
#   3. Write /etc/hoberadius/updater.env (ONLY if absent — operator overrides
#      survive re-provision) with the DETECTED project root + compose service.
#   4. install the .service + .timer → /etc/systemd/system/, daemon-reload,
#      enable --now the timer.
#   5. Degrade gracefully (clear log line) on non-systemd hosts or missing files.
#
# Project root resolution (in order):  $1  →  $HR_ROOT / $HOBERADIUS_PROJECT_ROOT
#   →  derived from this script's location (<root>/deploy/updater/…).
# Compose service name is DETECTED from docker-compose.yml (not hardcoded).
# ─────────────────────────────────────────────────────────────────────────────
set -uo pipefail

SELF_DIR="$(cd "$(dirname "$0")" && pwd)"

# ── logging (self-contained: this runs as its own process, not sourced) ───────
if [ -t 1 ]; then
  _C_G=$(printf '\033[32m'); _C_Y=$(printf '\033[33m'); _C_0=$(printf '\033[0m')
else
  _C_G=; _C_Y=; _C_0=
fi
u_log()  { printf '%s[updater-install]%s %s\n' "$_C_G" "$_C_0" "$*"; }
u_warn() { printf '%s[updater-install ⚠]%s %s\n' "$_C_Y" "$_C_0" "$*" >&2; }

# ── project root ──────────────────────────────────────────────────────────────
# The installer lives at <root>/deploy/updater/install-updater.sh, so the repo
# root is two levels up — a reliable default when no explicit root is passed.
HR_ROOT_DEFAULT="$(cd "$SELF_DIR/../.." && pwd)"
HR_ROOT="${1:-${HR_ROOT:-${HOBERADIUS_PROJECT_ROOT:-$HR_ROOT_DEFAULT}}}"

COMPOSE_FILE="$HR_ROOT/deploy/docker-compose.yml"

# ── detect the compose service name (the app service — image hoberadius:latest)
# The updater uses this value both as a compose service (build/up) AND as the
# container name (docker exec/inspect); in this stack they are identical.
detect_service() {
  local svc=""
  if [ -f "$COMPOSE_FILE" ]; then
    svc="$(awk '
      /^services:[[:space:]]*$/ { in_s=1; next }
      in_s && /^[^[:space:]#]/  { in_s=0 }
      in_s && /^  [A-Za-z0-9_.-]+:[[:space:]]*$/ { cur=$1; sub(/:.*/,"",cur) }
      in_s && /^[[:space:]]*image:[[:space:]]*hoberadius:latest[[:space:]]*$/ { print cur; exit }
    ' "$COMPOSE_FILE" 2>/dev/null)"
  fi
  # Fall back to the updater's own default if detection yields nothing.
  printf '%s' "${svc:-hoberadius}"
}

SERVICE="$(detect_service)"

u_log "project root : $HR_ROOT"
u_log "compose svc  : $SERVICE"
[ -f "$COMPOSE_FILE" ] || u_warn "compose file not found at $COMPOSE_FILE — using detected/default service '$SERVICE'"

# ── 1) shared dir (gid 999, setgid 2775) — same logic as deploy.sh/provision ──
# Idempotent: mkdir -p + chgrp + chmod are safe to re-run. gid=999 = the `hr`
# group inside the container so the panel can write update-request.json here.
ensure_shared_dir() {
  mkdir -p /var/lib/hoberadius 2>/dev/null || { u_warn "could not create /var/lib/hoberadius"; return 0; }
  chgrp 999 /var/lib/hoberadius 2>/dev/null || u_warn "could not chgrp 999 /var/lib/hoberadius (continuing)"
  chmod 2775 /var/lib/hoberadius 2>/dev/null || u_warn "could not chmod 2775 /var/lib/hoberadius (continuing)"
  u_log "shared dir   : /var/lib/hoberadius (gid 999, mode 2775)"
}
ensure_shared_dir

# ── 2) install the agent script → /usr/local/bin ──────────────────────────────
AGENT_SRC="$SELF_DIR/hoberadius-updater.sh"
AGENT_DST="/usr/local/bin/hoberadius-updater.sh"
if [ -f "$AGENT_SRC" ]; then
  if install -m 0755 "$AGENT_SRC" "$AGENT_DST" 2>/dev/null; then
    u_log "agent        : installed $AGENT_DST (0755)"
  else
    u_warn "could not install $AGENT_DST (need root?) — skipping"
  fi
else
  u_warn "agent script missing at $AGENT_SRC — skipping install"
fi

# ── 3) /etc/hoberadius/updater.env — write ONLY if absent (respect overrides) ──
ENV_DST="/etc/hoberadius/updater.env"
if [ -f "$ENV_DST" ]; then
  u_log "env          : $ENV_DST exists — leaving operator overrides untouched"
else
  if mkdir -p /etc/hoberadius 2>/dev/null && cat > "$ENV_DST" <<ENV 2>/dev/null
# HobeRadius self-update agent — host configuration.
# Written once by install-updater.sh; edit freely — re-provision won't clobber it.
HOBERADIUS_PROJECT_ROOT=$HR_ROOT
HOBERADIUS_SERVICE=$SERVICE
# Production: set to 1 to require a signed release tag before applying (RUNBOOK §5).
HOBERADIUS_UPDATE_REQUIRE_SIGNATURE=0
ENV
  then
    chmod 0644 "$ENV_DST" 2>/dev/null || true
    u_log "env          : wrote $ENV_DST (root=$HR_ROOT, service=$SERVICE)"
  else
    u_warn "could not write $ENV_DST (need root?) — the agent will use its built-in defaults"
  fi
fi

# ── 4) systemd .service + .timer (degrade gracefully without systemd) ─────────
SVC_SRC="$SELF_DIR/hoberadius-updater.service"
TMR_SRC="$SELF_DIR/hoberadius-updater.timer"
if ! command -v systemctl >/dev/null 2>&1; then
  u_warn "systemd not present on this host — skipping timer install."
  u_warn "Run the agent another way (e.g. cron or: hoberadius-updater.sh --watch)."
  exit 0
fi
if [ ! -f "$SVC_SRC" ] || [ ! -f "$TMR_SRC" ]; then
  u_warn "unit file(s) missing next to installer — skipping systemd install."
  exit 0
fi

_unit_ok=1
install -m 0644 "$SVC_SRC" /etc/systemd/system/hoberadius-updater.service 2>/dev/null \
  || { u_warn "could not install hoberadius-updater.service (need root?)"; _unit_ok=0; }
install -m 0644 "$TMR_SRC" /etc/systemd/system/hoberadius-updater.timer 2>/dev/null \
  || { u_warn "could not install hoberadius-updater.timer (need root?)"; _unit_ok=0; }

if [ "$_unit_ok" = "1" ]; then
  systemctl daemon-reload 2>/dev/null || u_warn "systemctl daemon-reload failed (continuing)"
  if systemctl enable --now hoberadius-updater.timer 2>/dev/null; then
    u_log "timer        : hoberadius-updater.timer enabled + started"
  else
    u_warn "could not enable/start hoberadius-updater.timer — enable it manually:"
    u_warn "  sudo systemctl enable --now hoberadius-updater.timer"
  fi
else
  u_warn "unit files not fully installed — self-update timer NOT active."
fi

u_log "done."
exit 0
