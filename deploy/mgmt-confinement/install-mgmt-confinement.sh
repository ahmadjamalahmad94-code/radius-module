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
#
# لماذا لا eval: النسخة القديمة كانت تعمل eval لمخرجات الحاوية كما هي. أيّ سطر
# دخيل يطبعه create_app() على stdout (مثل «NPC live adapters installed
# (default-on).») أو قيمة pool فيها قوس/مسافة كانت تُفجّر eval بـ
# «syntax error near unexpected token '('». الآن بايثون يطبع أسطرًا بسابقة
# حارسة HRSET فقط، والشِّل يلتقطها بـ sed إلى متغيّرات صريحة — لا eval إطلاقًا،
# فأيّ محتوى (أقواس/مسافات/سجلّات دخيلة) صار خاملًا.
if command -v docker >/dev/null 2>&1 && docker ps --format '{{.Names}}' | grep -qx "$PANEL_CONTAINER"; then
  echo "→ reading mgmt-tunnel settings from container '$PANEL_CONTAINER'…"
  _exported="$(docker exec "$PANEL_CONTAINER" python -c "
import sys
from app import create_app
from app.radius.services import router_mgmt_tunnel as r
create_app()
sys.stdout.write('HRSET rate=%s\n' % (r.mgmt_rate_mbps(),))
sys.stdout.write('HRSET pool=%s\n' % (r.load_config().pool,))
" 2>/dev/null || true)"
  _rate="$(printf '%s\n' "$_exported" | sed -n 's/^HRSET rate=//p' | head -n1)"
  _pool="$(printf '%s\n' "$_exported" | sed -n 's/^HRSET pool=//p' | head -n1)"
  # لا نعتمد إلا قيمًا بصيغة سليمة (المعدّل عدد صحيح). ملاحظة set -e: نستعمل
  # if الصريحة لا `[ … ] && …` — القائمة الفاشلة تقتل السكربت تحت set -e.
  case "$_rate" in
    ''|*[!0-9]*)
      if [ -n "$_rate" ]; then
        echo "  (تجاهُل معدّل غير رقميّ من الحاوية: '$_rate')"
      fi ;;
    *) export HOBERADIUS_MGMT_TUNNEL_RATE_MBPS="$_rate" ;;
  esac
  if [ -n "$_pool" ]; then
    export HOBERADIUS_MGMT_TUNNEL_POOL="$_pool"
  fi
fi

RATE="${HOBERADIUS_MGMT_TUNNEL_RATE_MBPS:-10}"
echo "→ applying confinement (rate cap ${RATE} Mbps) …"
python3 "$HERE/confine_rules_gen.py" install

echo "→ done. Verify with:"
echo "    iptables -L HR-MGMT-CONFINE -n -v"
echo "    tc -s qdisc show dev ${HOBERADIUS_WG_MGMT_IFACE:-wg0}"
