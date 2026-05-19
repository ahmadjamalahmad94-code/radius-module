#!/bin/sh
# ─────────────────────────────────────────────────────────────────────────────
# entrypoint — يستبدل placeholder السرّ في configs قبل تشغيل freeradius.
#
# سبب الـ placeholder: rlm_rest في FreeRADIUS 3.2.x لا يدعم:
#   - custom HTTP headers (`header = ...` يُتجاهَل، REST-HTTP-Header غير
#     موجود في 3.2.x — هو ميزة FR 4.x).
#   - runtime env xlat داخل update blocks بشكل موثوق.
# الحلّ: نمرّر السرّ كـ `_internal_secret` field داخل JSON body في
# mods-enabled/rest، ونستبدل الـ placeholder __HR_INTERNAL_SECRET__ هنا
# بقيمة env عند بدء الـ container. Flask يقبل السرّ من header أو body.
# ─────────────────────────────────────────────────────────────────────────────
set -e

SECRET="${HOBERADIUS_INTERNAL_SECRET:-}"

if [ -z "$SECRET" ]; then
    echo "[entrypoint] WARN: HOBERADIUS_INTERNAL_SECRET فارغ — Flask سيرفض الطلبات بـ 401." >&2
fi

# الـ delimiter '#' لأن السرّ قد يحوي '/' أو '='. JSON-aware: لا نسمح
# بسرّ يحتوي علامة اقتباس " أو backslash \ كي لا يكسر الـ JSON. لو احتاج
# المُشغّل سرّاً معقّداً، يجب escape مسبقاً عبر openssl rand -hex 32.
case "$SECRET" in
    *\"*|*\\*)
        echo "[entrypoint] ERROR: السرّ يحوي \" أو \\ — هذا يكسر JSON body." \
             "استعمل سرّ بسيط (مثل: openssl rand -hex 32)." >&2
        ;;
esac

sed -i "s#__HR_INTERNAL_SECRET__#${SECRET}#g" \
    /etc/freeradius/mods-enabled/rest

# ـ فحص bootstrap: لو بقي placeholder، فالـ env فارغ ـ
if grep -q "__HR_INTERNAL_SECRET__" /etc/freeradius/mods-enabled/rest; then
    echo "[entrypoint] ERROR: __HR_INTERNAL_SECRET__ لم يُستبدَل — تحقّق من env." >&2
fi

# سَلِّم التحكّم لـ freeradius
exec freeradius -f -l stdout $FREERADIUS_DEBUG_LEVEL
