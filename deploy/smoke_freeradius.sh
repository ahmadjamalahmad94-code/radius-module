#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# smoke_freeradius.sh — تأكّد أن خدمة FreeRADIUS قيد التشغيل على الـ VPS.
#
# لا يتطلّب MikroTik فعلي. يفحص:
#   1. docker compose ps يُظهر freeradius Up
#   2. UDP 1812/1813 (و3799 لو CoA enabled) مفتوحة على الـ host
#   3. اختبار auth داخلي (radclient) ضد 127.0.0.1:1812
#
# الاستخدام:
#   bash deploy/smoke_freeradius.sh
#   bash deploy/smoke_freeradius.sh --no-auth   # تخطّ خطوة radclient
#
# الـ exit code:  0 = كل شيء OK،  1 = هناك فشل.
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMPOSE_FILE="${SCRIPT_DIR}/docker-compose.yml"

GREEN=$'\033[0;32m'
RED=$'\033[0;31m'
YELLOW=$'\033[0;33m'
NC=$'\033[0m'

ok()   { echo -e "${GREEN}✓${NC} $*"; }
fail() { echo -e "${RED}✗${NC} $*"; exit 1; }
warn() { echo -e "${YELLOW}!${NC} $*"; }

RUN_AUTH=1
for arg in "$@"; do
    case "$arg" in
        --no-auth) RUN_AUTH=0 ;;
    esac
done

# ── 1. docker compose ps ───────────────────────────────────────────────
echo "── 1. docker compose ps ──"
if ! command -v docker >/dev/null 2>&1; then
    fail "docker غير مُثبَّت."
fi

if ! docker compose -f "$COMPOSE_FILE" ps 2>/dev/null | grep -E 'freeradius.*(Up|running)' >/dev/null; then
    fail "container freeradius غير قيد التشغيل. شغّل: docker compose -f $COMPOSE_FILE up -d freeradius"
fi
ok "container freeradius قيد التشغيل."

# ── 2. منافذ UDP ───────────────────────────────────────────────────────
echo "── 2. منافذ UDP ──"
if ! command -v ss >/dev/null 2>&1; then
    warn "ss غير موجود — تخطّيت فحص منافذ الـ host (ثبّت iproute2)."
else
    for port in 1812 1813; do
        if ss -lun | grep -q ":$port"; then
            ok "UDP $port مفتوح على الـ host."
        else
            fail "UDP $port مغلق على الـ host. تحقّق من ports: في docker-compose.yml."
        fi
    done
    if ss -lun | grep -q ':3799'; then
        ok "UDP 3799 (CoA) مفتوح."
    else
        warn "UDP 3799 (CoA) غير منشور — اختياري لكن مفيد إن أردت Disconnect-Request."
    fi
fi

# ── 3. health داخل الـ container ─────────────────────────────────────────
# صورة FreeRADIUS الدنيا لا تحوي `ss` (iproute2) → الاعتماد عليه يعطي إنذارًا
# كاذبًا («لا يستمع») رغم أنّ الخدمة سليمة. نستعمل `ss` كمسار سريع إن وُجد، ثم
# نرجع إلى `/proc/net/udp[6]` (موجود دائمًا في كلّ نواة لينكس، بلا أيّ حزمة).
# منفذ 1812 = 0x0714 (بأحرف كبيرة) في العمود المحلّي لـ/proc/net/udp.
echo "── 3. health داخل الـ container ──"
if docker exec hoberadius-freeradius sh -c \
    'command -v ss >/dev/null 2>&1 && ss -lun 2>/dev/null | grep -q ":1812" \
       || grep -qi ":0714" /proc/net/udp /proc/net/udp6 2>/dev/null' 2>/dev/null; then
    ok "FreeRADIUS يستمع على 1812/udp داخليًا."
else
    fail "FreeRADIUS لا يستمع داخل الـ container — افحص: docker logs hoberadius-freeradius"
fi

# ── 4. (اختياري) radclient auth-test ─────────────────────────────────────
if [[ $RUN_AUTH -eq 1 ]]; then
    echo "── 4. radclient auth-test ──"
    if ! command -v radclient >/dev/null 2>&1; then
        warn "radclient غير مُثبَّت على الـ host. للتخطي: --no-auth"
        warn "للتثبيت على Ubuntu:  sudo apt-get install -y freeradius-utils"
    else
        # نستخدم client testing123 المعرَّف في clients.conf على docker_network +
        # localhost. الاسم/الكلمة غير موجودة فعليًا → الردّ المتوقّع Reject،
        # لكن مجرد وصول الـ packet للـ server يثبت أن الـ socket والـ secret
        # يعملان.
        out=$(echo "User-Name=qa-smoke,User-Password=qa-smoke" | \
              radclient -t 3 -r 1 127.0.0.1:1812 auth testing123 2>&1 || true)
        if echo "$out" | grep -Eq 'Access-(Accept|Reject)'; then
            ok "radclient وصل إلى FreeRADIUS وعاد بقرار صالح."
            ok "الردّ: $(echo "$out" | grep -Eo 'Access-(Accept|Reject)' | head -1)"
        else
            fail "radclient لم يستلم ردًا. الأمر الكامل:\n$out"
        fi
    fi
fi

echo ""
ok "smoke FreeRADIUS مكتمل."
echo "  المرحلة التالية: شغّل اختبار من MikroTik:"
echo "    /radius add service=hotspot address=<VPS_IP> secret=<RADIUS_SHARED_SECRET>"
echo "    /radius test <id> user=<u> password=<p>"
