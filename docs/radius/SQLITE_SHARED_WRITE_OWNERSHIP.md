# SQLite Shared-Write Ownership (R4)

## السياق
نفس ملفّ SQLite (`hoberadius.db`) يُكتَب من اثنين:

| Container | يكتب | كـ user (uid/gid) | mount داخل الـ container |
|-----------|------|---------------------|---------------------------|
| `hoberadius` (Flask) | كل الجداول | `999:999` (gunicorn user) | `/app/instance/hoberadius.db` |
| `hoberadius-freeradius` (FR) | `radacct` فقط عبر rlm_sql_sqlite | `freerad:freerad` (`101:101` في image الـ FR) | `/data/hoberadius.db` |

كلاهما مربوط بنفس الـ host volume `../instance`. لكن uid 999 و uid 101
لا يربطهما شيء على الـ filesystem افتراضيًا → SQLite يفشل الـ
`INSERT` من FR لأن owner=999 mode=0644 يمنع الـ group/other من الكتابة.

النتيجة على VPS قبل R4:
```
docker exec hoberadius-freeradius su -s /bin/sh freerad \\
    -c 'touch /data/.test'
# touch: /data/.test: Permission denied
```
و في logs FR: `rlm_sql_sqlite: (1) attempt to write a readonly database`
أو `unable to open database file`.

## الإصلاح في R4
الـ entrypoint.sh الخاص بـ FreeRADIUS يُسوّي الـ permissions عند كل
إقلاع (root → drop to freerad بعد ذلك في daemon):

```sh
chgrp <freerad_gid> /data
chmod g+rwX /data
chmod g+s   /data           # files جديدة تأخذ freerad group تلقائيًا
chgrp <freerad_gid> /data/hoberadius.db /data/hoberadius.db-wal …
chmod g+rw           /data/hoberadius.db /data/hoberadius.db-wal …
```

ثم smoke test:
```sh
su -s /bin/sh freerad -c 'touch /data/.fr_write_check && rm /data/.fr_write_check'
```

لو نجح → `[entrypoint] /data write check OK for freerad` في logs.
لو فشل → `[entrypoint] WARN: freerad لا يستطيع الكتابة في /data`.

## لماذا لا chmod 777؟
`chmod 777` يجعل أي مستخدم على الـ host (incl. أي container مستقبلي)
قادراً على الكتابة في الـ DB. هذا يُهَدّد:
- سرّية بيانات المشتركين والإيرادات.
- سلامة الجلسات (شخص يقدر يحذف radacct).
- audit trail.

`chmod g+rw` + ضبط الـ group على `freerad` فقط = شخصان من الـ users
يَكتُبان (uid 999 لـ Flask كـ owner، uid 101 لـ freerad كـ group)،
كل من سواهما read-only أو محرومين.

## لماذا لا nchown إلى freerad فقط؟
لأنّ Flask هو الـ writer الرئيسي وله مزيد من العمليات. تحويل الـ owner
لـ freerad يَكسر Flask إلى read-only — ينعكس على كل auth وكل DB write
آخر. الحفاظ على owner=999 + إعطاء freerad group access هو التوازن.

## استبقاء بعد restart
- الـ entrypoint يُعيد التطبيق على كل إقلاع → آمن من permission drift.
- على الـ host: لا حاجة لتعديل ownership يدويًا بعد الآن. لو حدث drift
  (مثلاً restore من backup خاطئ)، إعادة تشغيل الـ FR container تعالج
  المشكلة.

## verification commands (على VPS بعد deploy)

```bash
# 1. تأكّد من إقلاع الـ FR container وظهور الـ smoke test OK:
docker logs hoberadius-freeradius 2>&1 | grep -E '/data write check|WARN:'

# المتوقّع:
#   [entrypoint] /data write check OK for freerad

# 2. تحقّق ownership من داخل الـ container:
docker exec hoberadius-freeradius stat -c '%U:%G %a %n' \\
    /data /data/hoberadius.db

# المتوقّع (مثال):
#   root:freerad 2775 /data
#   <owner>:freerad 664 /data/hoberadius.db
# الـ <owner> غالباً سيكون uid 999 (لا يُحَلّ كاسم — هذا طبيعي).

# 3. اختبار يدوي صريح:
docker exec hoberadius-freeradius su -s /bin/sh freerad \\
    -c 'touch /data/fr_user_write_test && rm /data/fr_user_write_test' \\
    && echo OK || echo FAIL

# 4. تأكّد من أن radacct بدأ يمتلئ بعد login حقيقي على MT:
docker exec hoberadius sqlite3 /app/instance/hoberadius.db \\
    "SELECT acctsessionid, acctuniqueid, username, acctstarttime
       FROM radacct ORDER BY radacctid DESC LIMIT 3;"
```

## hardening موصى به (لم يُفعَّل في R4)
هذه إعدادات أمنية في `clients.conf` للـ `client mt_main_*`. **لا** نفعّلها
الآن لأن تفعيلها بدون اختبار قد يكسر MikroTik:

```conf
client mt_main_213_6_169_138 {
    ...
    require_message_authenticator = yes   # حماية من BlastRADIUS (CVE-2024-3596)
    limit_proxy_state = yes               # تجاهل Proxy-State من NAS غير الـ proxy
}
```

**خطوات تفعيلها بأمان**:
1. تأكّد أن RouterOS على الـ MT حديث (7.x+) ويُرسل `Message-Authenticator`
   تلقائيًا. تحقّق عبر:
   ```
   /log print where topics~"radius"
   ```
   ابحث عن `message-authenticator` في الـ outgoing packets.
2. لو RouterOS قديم → حدّث أولاً، أو أضف يدويًا في الـ MT:
   `/radius incoming set accept=yes`.
3. غيّر `require_message_authenticator = yes` في clients.conf.
4. أعد بناء image الـ FR و restart.
5. راقب أول 5 دقائق: لو login بدأ يفشل → ارجع للقيمة `no`.

`limit_proxy_state = yes` كذلك آمن غالبًا (MT لا يُرسل Proxy-State
عادةً) لكن لم نفعّله كي يبقى تغيير R4 يركّز على الـ permissions فقط.

## ربط بـ سلسلة الـ slices

| Slice | الإصلاح |
|-------|--------|
| R1 (`f72e3d4`) | accounting{} غير حاجز → Accounting-Response يُرسَل |
| R2 (`b8bd072`) | radacct schema يحتوي أعمدة IPv6 → INSERT ينجح |
| R3 (`dcb6526`) | accounting_puller writes معطّلة → FR canonical |
| **R4 (current)** | **permissions على /data → FR فعليًا يستطيع الكتابة** |

بعد R4، أول Acct-Start من MT يجب أن يُنشئ صفًا في radacct تلقائيًا،
بدون أي تدخّل يدوي على الـ host.

## ما لاحق R4

- **R5**: dedup migration حذِر للـ rows القديمة (مرحلة R1→R2→R3) — انظر
  [ACCOUNTING_RESPONSE_PATH.md](ACCOUNTING_RESPONSE_PATH.md#r4-موصى-به-لا-يُنفَّذ-هنا--dedup-migration-حذِر).
- **client hardening**: تفعيل `require_message_authenticator` بعد التحقّق من MT.
- **enrichment**: تكييف accounting_puller ليكون read-only ويُحدّث metadata
  على rows FR (UPDATE فقط، لا INSERT).
