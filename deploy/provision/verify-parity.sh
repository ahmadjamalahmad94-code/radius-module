#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# verify-parity.sh — run on the NEW VPS after provisioning. Re-inventories the
# box and DIFFS it against the source vps-manifest.json, plus live health probes.
# Prints PASS, or the exact list of mismatches. Exit 0 = parity, non-zero = drift.
#
#   sudo bash verify-parity.sh --manifest /path/vps-manifest.json [--root /opt/hoberadius]
#   sudo bash verify-parity.sh                # live checks only (no source manifest)
# ─────────────────────────────────────────────────────────────────────────────
set -u
SELF_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=/dev/null
. "$SELF_DIR/_lib.sh"

SRC_MANIFEST=""; ROOT="/opt/hoberadius"
while [ $# -gt 0 ]; do
  case "$1" in
    --manifest) SRC_MANIFEST="$2"; shift 2 ;;
    --root) ROOT="$2"; shift 2 ;;
    -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
    *) die "arg مجهول: $1" ;;
  esac
done

PASS=0; MISS=0
pass() { ok "$*"; PASS=$((PASS+1)); }
miss() { printf '%s✗ MISMATCH:%s %s\n' "$_C_R" "$_C_0" "$*"; MISS=$((MISS+1)); }

# ── produce a fresh manifest of THIS box ──
step "إعادة الجرد لهذا الـ VPS"
NEW_DIR="$(mktemp -d)"; trap 'rm -rf "$NEW_DIR"' EXIT
if [ -f "$SELF_DIR/inventory-current-vps.sh" ]; then
  HR_ROOT="$ROOT" bash "$SELF_DIR/inventory-current-vps.sh" -o "$NEW_DIR" >/dev/null 2>&1 || warn "الجرد الجديد أرجع تحذيرات"
fi
NEW_MANIFEST="$NEW_DIR/vps-manifest.json"

# ── compare a key between source and new manifests ──
cmp_key() { # dotted.key  human-label
  [ -n "$SRC_MANIFEST" ] || return 0
  want="$(manifest_get "$SRC_MANIFEST" "$1")"
  got="$(manifest_get "$NEW_MANIFEST" "$1")"
  if [ -z "$want" ] && [ -z "$got" ]; then return 0; fi
  if [ "$want" = "$got" ]; then pass "$2 = $got"; else miss "$2: مصدر='$want' جديد='$got'"; fi
}

step "مقارنة المانيفست (المصدر ↔ الجديد)"
cmp_key nginx.client_max_body_size "حدّ رفع nginx"
cmp_key db.migration_version       "إصدار الهجرات (schema)"
cmp_key wg.subnet                  "subnet نفق WG"
cmp_key accel.sstp_port            "منفذ SSTP"
cmp_key accel.service_enabled      "accel-ppp enabled على الإقلاع"
cmp_key accel.sstp_module_enabled  "وحدة SSTP مفعّلة في accel"
cmp_key ports.443_owner            "مالك :443"
cmp_key freeradius.has_sql         "FreeRADIUS SQL module"
cmp_key freeradius.has_mschap      "FreeRADIUS mschap module"
cmp_key licensing.bridge_enabled   "تفعيل جسر الترخيص"

# ── live health probes (independent of a source manifest) ──
step "فحوصات حيّة"

# containers
if have docker; then
  up="$(docker ps --format '{{.Names}}' 2>/dev/null)"
  for c in hoberadius hoberadius-freeradius hoberadius-nginx hoberadius-backup; do
    printf '%s\n' "$up" | grep -qx "$c" && pass "الحاوية $c تعمل" || miss "الحاوية $c ليست قيد التشغيل"
  done
fi

# ports
port_open() { # proto host port
  if have ss; then ss -lntuH 2>/dev/null | grep -qE "[:.]$3\b"; else return 1; fi
}
for p in 80 8443; do port_open tcp 0 "$p" && pass "منفذ TCP $p مفتوح" || miss "منفذ TCP $p غير مفتوح"; done
if have ss; then
  ss -lunH 2>/dev/null | grep -qE '[:.]1812\b' && pass "RADIUS auth 1812/udp يستمع" || miss "1812/udp لا يستمع (freeradius؟)"
  ss -lunH 2>/dev/null | grep -qE '[:.]1813\b' && pass "RADIUS acct 1813/udp يستمع" || miss "1813/udp لا يستمع"
fi

# accel-ppp: active + enabled + :443 owned by accel-pppd (نفق الإدارة SSTP)
if have systemctl; then
  a="$(systemctl is-active  accel-ppp 2>/dev/null)"
  e="$(systemctl is-enabled accel-ppp 2>/dev/null)"
  [ "$a" = "active" ]  && pass "accel-ppp active"   || miss "accel-ppp ليس active (=$a)"
  [ "$e" = "enabled" ] && pass "accel-ppp enabled على الإقلاع" || miss "accel-ppp ليس enabled (=$e)"
fi
if have ss; then
  o443="$(ss -lntupH 2>/dev/null | grep -E '[^[:space:]]:443[[:space:]]' | sed -nE 's/.*users:\(\("([^"]+)".*/\1/p' | head -1)"
  if [ "$o443" = "accel-pppd" ]; then pass ":443 مملوك لـ accel-pppd (SSTP mgmt)"
  elif [ -z "$o443" ] && [ "$(id -u)" -ne 0 ]; then warn ":443 — لم نتمكّن من قراءة المالك (شغّل verify بـ sudo)"
  else miss ":443 مالكه='${o443:-لا شيء يستمع}' (متوقَّع accel-pppd)"; fi
  # PPTP (:1723 tcp) — نفق الإدارة البديل (أساسيّ مثل SSTP). accel يستمع عليه
  # حين تُحمَّل وحدة pptp. (بيانات PPTP تحتاج بروتوكول GRE=47 مفتوحًا أيضًا.)
  o1723="$(ss -lntupH 2>/dev/null | grep -E '[^[:space:]]:1723[[:space:]]' | sed -nE 's/.*users:\(\("([^"]+)".*/\1/p' | head -1)"
  if [ "$o1723" = "accel-pppd" ]; then pass ":1723 مملوك لـ accel-pppd (PPTP mgmt)"
  elif [ -z "$o1723" ] && [ "$(id -u)" -ne 0 ]; then warn ":1723 — لم نتمكّن من قراءة المالك (شغّل verify بـ sudo)"
  else miss ":1723 مالكه='${o1723:-لا شيء يستمع}' (متوقَّع accel-pppd — وحدة pptp)"; fi
fi

# tunnels
ip link show wg0 >/dev/null 2>&1 && pass "wg0 UP" || miss "wg0 غير نشط"

# panel reachable
curl -fsS --max-time 5 http://127.0.0.1/admin/radius/_health >/dev/null 2>&1 \
  && pass "اللوحة تستجيب على :80" || miss "اللوحة لا تستجيب على :80"

# upload limit effective (regex the live nginx default.conf inside the container)
if have docker && docker ps --format '{{.Names}}' 2>/dev/null | grep -qx hoberadius-nginx; then
  if docker exec hoberadius-nginx sh -c 'grep -RqiE "client_max_body_size[[:space:]]+1024m" /etc/nginx/conf.d' 2>/dev/null; then
    pass "حدّ رفع الترحيل 1024m مطبَّق في nginx"
  else miss "حدّ رفع الترحيل 1024m غير موجود في nginx (رفع ملفات الترحيل قد يفشل بـ413)"; fi
fi

# radtest (best-effort; skipped if radtest/creds absent)
if have radtest && [ -n "${RADTEST_USER:-}" ] && [ -n "${RADTEST_PASS:-}" ]; then
  if radtest "$RADTEST_USER" "$RADTEST_PASS" 127.0.0.1 0 testing123 2>/dev/null | grep -q 'Access-Accept'; then
    pass "radtest → Access-Accept"
  else miss "radtest لم يرجع Access-Accept"; fi
else
  warn "radtest تُخطّى (لا radtest أو RADTEST_USER/PASS) — شغّل deploy/smoke_freeradius.sh يدويًّا"
fi

# ── verdict ──
step "النتيجة"
if [ "$MISS" -eq 0 ]; then
  ok "PASS — تطابق كامل ($PASS فحص ناجح)."
  exit 0
else
  printf '%s✗ %d فرق / %d ناجح — النظام غير مطابق بعد.%s\n' "$_C_R" "$MISS" "$PASS" "$_C_0"
  exit 1
fi
