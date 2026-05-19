#!/bin/sh
# ─────────────────────────────────────────────────────────────────────────────
# entrypoint — يستبدل placeholders من env داخل ملفات الـ config قبل
# تشغيل freeradius.
#
# لماذا config-time substitution وليس runtime xlat؟
#   FreeRADIUS 3.2.x لا يوفّر xlat موثوقًا لقراءة env vars داخل
#   `update request { ... }` blocks. الـ ${env:VAR} يعمل في بعض السياقات
#   (security, modules) لكن ليس داخل unlang. الحلّ العملي: ضع placeholder
#   ثابتاً في الـ config، ثم استبدله بـ sed قبل تشغيل freeradius.
#
# المُستبدَل:
#   __HR_INTERNAL_SECRET__   →  $HOBERADIUS_INTERNAL_SECRET
#     في sites-enabled/default ضمن: update request { REST-HTTP-Header := ... }
#     (rlm_rest يلتقطه كـ HTTP header على كل طلب).
# ─────────────────────────────────────────────────────────────────────────────
set -e

SECRET="${HOBERADIUS_INTERNAL_SECRET:-}"

if [ -z "$SECRET" ]; then
    echo "[entrypoint] WARN: HOBERADIUS_INTERNAL_SECRET فارغ — استدعاءات /api/v1/internal/* ستذهب بدون X-Internal-Secret (Flask سيرفض بـ 401 في prod)." >&2
fi

# الـ delimiter '#' لأن السرّ قد يحوي '/' أو '='. نستبدل داخل النسخة
# المنسوخة في الـ container، لا الأصل على الـ host.
sed -i "s#__HR_INTERNAL_SECRET__#${SECRET}#g" \
    /etc/freeradius/sites-enabled/default

# ـ تحقق آمن: إذا بقي أي placeholder، فالـ secret لم يصل (سنُسجّل تحذيرًا) ـ
if grep -q "__HR_INTERNAL_SECRET__" /etc/freeradius/sites-enabled/default; then
    echo "[entrypoint] ERROR: placeholder __HR_INTERNAL_SECRET__ لم يُستبدَل في sites-enabled/default — تحقّق من env." >&2
fi

# سَلِّم التحكّم لـ freeradius
exec freeradius -f -l stdout $FREERADIUS_DEBUG_LEVEL
