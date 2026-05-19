# RADIUS Accounting Response Path — تحليل وإصلاح

## المُشكلة المُلاحظة
على MikroTik:
```
RADIUS accounting request not sent: no response
```

## الـ path الكامل
```
MikroTik (NAS)  --UDP/1813 Accounting-Request-->  FreeRADIUS 3.2.5
                                                     ↓
                                                 server default {
                                                   preacct { preprocess }
                                                   accounting { detail; sql }   ← يفشل هنا
                                                 }
                <--UDP/1813 Accounting-Response--   (لم يُرسَل)
```

## السبب الجذري

سلسلة سببية مزدوجة:

### 1. الـ INSERT في `accounting_start_query` يكتب أعمدة غير موجودة
`deploy/freeradius/mods-enabled/sql` يحتوي `accounting_start_query`
الذي يكتب 20 عمودًا، ثلاثة منها غير موجودة في schema الحالي
لجدول `radacct` (المعرَّف في `app/radius/db/migrations/006_logs.sql`):

| العمود | في query | في schema |
|--------|----------|-----------|
| `framedipv6prefix`  | ✓ | ✗ |
| `framedinterfaceid` | ✓ | ✗ |
| `delegatedipv6prefix` | ✓ | ✗ |

النتيجة على كل Acct-Start packet:
```
sqlite3.OperationalError: no such column: framedipv6prefix
→ rlm_sql_sqlite ُيرجع rcode = fail
```

### 2. FreeRADIUS يحوّل فشل sql إلى "لا response"
في FR 3.2.x، الـ default action لـ rcode `fail` في `accounting {}` section
هو `return` (return من الـ section بنفس الـ rcode). نتيجة الـ section:
```
section rcode = fail  →  FreeRADIUS لا يُرسل Accounting-Response
```

ومن جانب MikroTik: عدم وصول الـ response خلال timeout يُعرض كـ
"accounting request not sent: no response" (نص الـ log مضلِّل قليلًا — الـ
request فعليًا أُرسل، لكن الـ response لم يصل).

## الإصلاح في هذه الـ slice

**ملف معدَّل واحد**: `deploy/freeradius/sites-enabled/default`.

```
accounting {
    detail
    sql {
        fail     = 1
        reject   = 1
        invalid  = 1
        notfound = 1
    }
    ok
}
```

### كيف يعمل
- `{ fail = 1; ... }` تُجبر FR على التعامل مع rcode الفشل بـ priority=1
  (تابِع) بدل الـ default `return`.
- `ok` (instance من `rlm_always` المُعرَّف في `mods-enabled/always`) يُرجع
  دائمًا rcode=ok. وضعه في النهاية يضمن أن آخر rcode في الـ section هو ok.
- نتيجة الـ section = ok → FreeRADIUS يُرسل Accounting-Response.

### ما يبقى عاملًا
- `detail` module يكتب نسخة file-based في
  `/var/log/freeradius/radacct/<nas-ip>/detail-<date>` بغضّ النظر عن
  حالة SQL. الـ data موجود وقابل للاستيراد لاحقًا.
- `sql` يحاول الـ INSERT/UPDATE كالسابق؛ نجاحه إضافي وليس شرطًا للردّ.

## ما لم نُغيِّره (مُتعمَّد)
- **auth path** — `rest` يقرّر Accept/Reject عبر `/api/v1/internal/auth`.
  لم نمسّ authorize/authenticate/post-auth.
- **SQL في auth** — يبقى معطّلًا (commit 2726782 ساري).
- **clients.conf** — أجهزة MikroTik محدَّدة هنا.
- **Docker compose** — UDP/1812 + UDP/1813 + UDP/3799 مكشوفة كما هي.
- **Flask** — لا تغيير على endpoints.
- **MikroTik** — لا تغيير.

## حالة الـ Accounting الحالية (قبل وبعد الإصلاح)

| Acct-Status-Type | قبل | بعد |
|-------------------|-----|-----|
| Start             | لا response؛ row غير مُدرَج في radacct | response مُرسل؛ row غير مُدرَج (schema mismatch لم يُحَل) |
| Interim-Update    | لا response؛ لا UPDATE | response مُرسل؛ UPDATE يفشل بصمت (لأن INSERT الأصلي فشل) |
| Stop              | لا response؛ لا UPDATE | response مُرسل؛ UPDATE يفشل بصمت |

**خلاصة**: الإصلاح يُعالج "no response" فقط. radacct لا يزال غير مُكتمل
عبر FreeRADIUS sql. مصدر `radacct` العامل حاليًا هو `accounting_puller`
في Flask (يقرأ من MT API مباشرة).

## تعارض محتمل: accounting_puller vs FreeRADIUS sql

```
accounting_puller (Flask, kل 30s)        FreeRADIUS sql (per-packet)
       ↓                                          ↓
       └────────  /data/hoberadius.db (radacct)  ────────┘
```

`app/workers/accounting_puller.py` يستعلم MT API كل 30 ثانية و يُدخل/
يُحدّث radacct. مفتاحه: `(tenant_id, acctsessionid, nasipaddress)`.

`rlm_sql_sqlite` (لو نجح) يُدخل بمفتاح `acctuniqueid`. الـ row identities
مختلفة. لو الاثنان عملا معًا بدون تنسيق:
- **double-counting**: نفس الجلسة تظهر صفّين.
- **race conditions على نفس acctsessionid** خلال window 30s.

حاليًا الـ FR sql يفشل دائمًا فالتعارض غير ظاهر. لو أصلحنا الـ schema
سيظهر فورًا.

## التحقّق المتوقَّع بعد deploy

```bash
cd /opt/radius-module && git pull
docker compose -f deploy/docker-compose.yml up -d --force-recreate freeradius

# 1. Listening sockets داخل الـ container:
docker exec hoberadius-freeradius ss -lun
# expected: UDP *:1812, *:1813, *:3799

# 2. تشغيل FR في debug mode داخل الـ container (يدويًا، إيقاف الـ daemon أولاً):
docker exec hoberadius-freeradius pkill freeradius
docker exec -it hoberadius-freeradius freeradius -X
# قم بـ login من MT وراقب:
#   - "Received Accounting-Request Id ..."
#   - "Sending Accounting-Response Id ..."
# لو لم يُرسَل Accounting-Response → الـ section لا تزال تُرجع fail.

# 3. من MikroTik:
/log print where topics~"radius"
# يجب ألا يُظهِر "accounting request not sent" بعد الآن.

# 4. file-based detail logs:
docker exec hoberadius-freeradius ls /var/log/freeradius/radacct/
# لازم تظهر directories بأسماء NAS IPs مع ملفات detail-YYYYMMDD.
```

## R2 — schema alignment (مُنفَّذ)

migration: `app/radius/db/migrations/016_radacct_ipv6_columns.sql`

```sql
ALTER TABLE radacct ADD COLUMN framedipv6prefix    TEXT DEFAULT '';
ALTER TABLE radacct ADD COLUMN framedinterfaceid   TEXT DEFAULT '';
ALTER TABLE radacct ADD COLUMN delegatedipv6prefix TEXT DEFAULT '';
```

بعد تطبيقها كل أعمدة الـ FR accounting queries موجودة:

| Query | الأعمدة المُستخدَمة | الحالة |
|-------|-----------------------|--------|
| `accounting_start_query` | 20 عمودًا (بما فيها الـ 3 الجديدة) | ✓ مكتمل |
| `accounting_update_query` | 6 أعمدة (لا ipv6 الجديدة) | ✓ كانت موجودة |
| `accounting_stop_query`   | 6 أعمدة | ✓ كانت موجودة |

idempotency: `_migrations` table يتتبّع الـ migration باسم الـ file؛
لن يُعاد تشغيلها على نفس DB.

## حالة الـ Accounting الحالية (بعد R1 + R2)

| Acct-Status-Type | بعد R1 فقط | بعد R1 + R2 |
|-------------------|------------|--------------|
| Start             | response مُرسل؛ row غير مُدرَج | response مُرسل؛ row مُدرَج |
| Interim-Update    | response مُرسل؛ UPDATE فاشل (لا row) | response مُرسل؛ UPDATE ناجح |
| Stop              | response مُرسل؛ UPDATE فاشل | response مُرسل؛ UPDATE ناجح |

## ⚠ تحذير: تعارض مزدوج مع accounting_puller الآن ظاهر

بعد R1 + R2:
- **FreeRADIUS sql**: يكتب row في radacct على كل Acct-Start، بـ
  `acctuniqueid` = FR-generated UUID (مختلف عن MT session-id).
- **accounting_puller** (Flask، كل 30s): يكتب row في radacct بـ
  `acctsessionid` = `acctuniqueid` = MT .id.

النتيجة المتوقّعة: **صفّان لكل جلسة** — واحد من FR (كل event)
وواحد من puller (poll-based). آثار:
- عدّ الجلسات المتّصلة مضاعف.
- إجمالي bytes مكرَّر إذا جمعت بدون deduplication.
- race conditions على نفس username/nas/time-window.

**هذا متعمَّد لـ R2** — تركنا puller يعمل لضمان عدم انقطاع reporting
خلال الانتقال. الـ slice التالي R3 يحسم الـ ownership.

## الـ slice التالي R3 (موصى به، لا يُنفَّذ هنا)

**حسم تعارض accounting_puller**. الخيارات:

- (أ) **إيقاف الـ writes في puller** بعد تأكيد أن FR sql يكتب فعلاً
  (انتظار يوم/يومين). الـ puller يبقى read-only للـ MT API ليُكمّل
  metadata غير المتوفّرة عبر RADIUS (مثل MT-uptime، interface stats).
  → single source of truth = FR sql.

- (ب) **إبقاء puller writer** وإيقاف FR sql في accounting{} (نُبقي
  ok-only). يتطلّب موثوقية mt-pool عالية و polling interval قصير.
  → single source of truth = MT API via puller.

- (ج) **dedup view**: نُبقي الاثنين كاتبَيْن، نُضيف SQL VIEW يجمع
  حسب `(tenant_id, username, nasipaddress, date_floor(acctstarttime))`.
  → تعقيد إضافي، لكن يُعطي fallback لو أحدهما تعطّل.

التوصية: (أ). FR sql أدقّ زمنيًا (per-packet vs 30s poll) ويحوي
Acct-Terminate-Cause المفيد لـ disconnect reporting.

## Slices لاحقة (ما بعد R3)

- **Acct-Status-Type-aware response shaping**: حاليًا نُرجع response
  لكل request. يمكن إضافة rate-limit/drop عند فيضان.
- **مراقبة**: counter للـ Accounting-Request received vs Response sent
  vs sql success — مفيد للـ alerting.
