#!/bin/sh
# ─────────────────────────────────────────────────────────────────────────────
# entrypoint — يستبدل placeholders من env داخل ملفات الـ config قبل
# تشغيل freeradius. سبب وجوده: المعامل `header` في mods-enabled/rest
# لا يمرّ بـ xlat الديناميكي على FreeRADIUS 3.2.x، فيفشل ${env:VAR}.
# نعالج المشكلة بـ sed عند الإقلاع.
# ─────────────────────────────────────────────────────────────────────────────
set -e

SECRET="${HOBERADIUS_INTERNAL_SECRET:-}"

if [ -z "$SECRET" ]; then
    echo "[entrypoint] WARN: HOBERADIUS_INTERNAL_SECRET فارغ — استدعاءات /api/v1/internal/* ستذهب بدون رأس X-Internal-Secret." >&2
fi

# الـ delimiter '#' لأن السرّ قد يحوي / أو =. نستبدل في النسخة المنسوخة
# داخل الـ container، لا في الأصل على الـ host.
sed -i "s#\${env:HOBERADIUS_INTERNAL_SECRET}#${SECRET}#g" \
    /etc/freeradius/mods-enabled/rest

# سَلِّم التحكّم لـ freeradius
exec freeradius -f -l stdout $FREERADIUS_DEBUG_LEVEL
