#!/usr/bin/env bash
# HobeRadius — management-tunnel abuse prevention (host side).
#
# Installs the idempotent iptables confinement + WireGuard tc cap. Pulls the
# UI-set rate (and pools) from the running panel container when available, so the
# value the operator set in Settings → network is what gets applied; otherwise
# falls back to env / defaults.
#
# Re-runnable: safe to call on every deploy. Pairs with the accel-ppp installer
# (SSTP/PPTP shaping is in /etc/accel-ppp.conf, applied there).
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
PANEL_CONTAINER="${HOBERADIUS_PANEL_CONTAINER:-hoberadius}"

# Pull effective settings from the panel (DB → env → default) if reachable, so a
# value set ONLY in the UI still propagates to the host. Best-effort.
if command -v docker >/dev/null 2>&1 && docker ps --format '{{.Names}}' | grep -qx "$PANEL_CONTAINER"; then
  echo "→ reading mgmt-tunnel settings from container '$PANEL_CONTAINER'…"
  eval "$(docker exec "$PANEL_CONTAINER" python -c "
from app import create_app
from app.radius.services import router_mgmt_tunnel as r
create_app()
print(f'export HOBERADIUS_MGMT_TUNNEL_RATE_MBPS={r.mgmt_rate_mbps()}')
print(f'export HOBERADIUS_MGMT_TUNNEL_POOL={r.load_config().pool}')
" 2>/dev/null || true)"
fi

RATE="${HOBERADIUS_MGMT_TUNNEL_RATE_MBPS:-10}"
echo "→ applying confinement (rate cap ${RATE} Mbps) …"
python3 "$HERE/confine_rules_gen.py" install

echo "→ done. Verify with:"
echo "    iptables -L HR-MGMT-CONFINE -n -v"
echo "    tc -s qdisc show dev ${HOBERADIUS_WG_MGMT_IFACE:-wg0}"
