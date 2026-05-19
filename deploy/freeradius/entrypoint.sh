#!/bin/sh
# ─────────────────────────────────────────────────────────────────────────────
# entrypoint — يحضّر الـ config والـ permissions قبل تشغيل freeradius.
#
# 1) يستبدل placeholder السرّ في mods-enabled/rest (سياق R1: rlm_rest
#    في FR 3.2.x لا يدعم custom HTTP headers ولا env xlat داخل update
#    blocks، فنُمرّر السرّ كـ `_internal_secret` في JSON body).
# 2) يُسوّي ملكية /data كي يستطيع freerad الكتابة على SQLite المشترك
#    بين Flask (uid 999) و FreeRADIUS (freerad).
# ─────────────────────────────────────────────────────────────────────────────
set -e

# ─── 1) secret substitution ──────────────────────────────────────────────────
SECRET="${HOBERADIUS_INTERNAL_SECRET:-}"

if [ -z "$SECRET" ]; then
    echo "[entrypoint] WARN: HOBERADIUS_INTERNAL_SECRET فارغ — Flask سيرفض الطلبات بـ 401." >&2
fi

case "$SECRET" in
    *\"*|*\\*)
        echo "[entrypoint] ERROR: السرّ يحوي \" أو \\ — هذا يكسر JSON body." \
             "استعمل سرّ بسيط (مثل: openssl rand -hex 32)." >&2
        ;;
esac

sed -i "s#__HR_INTERNAL_SECRET__#${SECRET}#g" \
    /etc/freeradius/mods-enabled/rest

if grep -q "__HR_INTERNAL_SECRET__" /etc/freeradius/mods-enabled/rest; then
    echo "[entrypoint] ERROR: __HR_INTERNAL_SECRET__ لم يُستبدَل — تحقّق من env." >&2
fi

# ─── 2) SQLite shared-write ownership normalization ──────────────────────────
# الـ SQLite DB يُملَك من Flask (uid 999) في حجمه الافتراضي على VPS. لكي
# يستطيع freerad (gid=101 في image الـ FR) الكتابة لـ radacct ـ نُضيف
# Group access (read+write) دون تغيير الـ owner ودون chmod 777.
#
# يعمل كـ root هنا (Dockerfile لا يحدّد USER قبل ENTRYPOINT). الـ
# freeradius daemon لاحقاً يَدرُب لـ freerad/freerad حسب radiusd.conf.
#
# الـ |true في النهاية: لا نُسقط الـ container لو فشلت أي عملية. الفحص
# النهائي بـ smoke-test هو المرجع.
if [ -d /data ]; then
    FREERAD_GID="$(getent group freerad 2>/dev/null | cut -d: -f3)"
    : "${FREERAD_GID:=101}"

    # /data نفسه: group=freerad + g+rwX + setgid لكي الـ files الجديدة
    # تأخذ نفس الـ group تلقائياً.
    chgrp "$FREERAD_GID" /data 2>/dev/null || true
    chmod g+rwX /data 2>/dev/null || true
    chmod g+s   /data 2>/dev/null || true

    # ملفّات SQLite (db + WAL/SHM/journal): group=freerad + g+rw
    for f in /data/hoberadius.db /data/hoberadius.db-wal /data/hoberadius.db-shm /data/hoberadius.db-journal; do
        if [ -e "$f" ]; then
            chgrp "$FREERAD_GID" "$f" 2>/dev/null || true
            chmod g+rw           "$f" 2>/dev/null || true
        fi
    done

    # Smoke test: هل يستطيع freerad فعلاً الكتابة في /data؟
    # نُعطي مخرَجَين واضحَين في logs الـ container كي يلاحظهما المُشغّل.
    if su -s /bin/sh freerad -c 'touch /data/.fr_write_check && rm /data/.fr_write_check' 2>/dev/null; then
        echo "[entrypoint] /data write check OK for freerad" >&2
    else
        echo "[entrypoint] WARN: freerad لا يستطيع الكتابة في /data —" \
             "accounting SQL writes ستفشل (لكن Accounting-Response يبقى" \
             "يُرسَل بفضل R1). راجع ownership على المضيف." >&2
    fi
else
    echo "[entrypoint] WARN: /data غير موجود — لن تعمل accounting SQL writes." >&2
fi

# سَلِّم التحكّم لـ freeradius
exec freeradius -f -l stdout $FREERADIUS_DEBUG_LEVEL
