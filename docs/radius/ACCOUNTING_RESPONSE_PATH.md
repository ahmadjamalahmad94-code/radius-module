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

## R3 — accounting_puller writes معطّلة (مُنفَّذ)

**القرار**: FreeRADIUS هو الكاتب الوحيد لـ radacct. الـ puller يبقى
يعمل (heartbeat + استعلام MT) لكن لا يُنفّذ INSERT/UPDATE.

التغيير في `app/workers/accounting_puller.py`:
- helper جديد `acct_puller_writes_enabled()` يقرأ
  `HOBERADIUS_ACCT_PULLER_WRITES` (default: false).
- داخل `_tick`: قبل استدعاء `_upsert_session` و `_close_stale_sessions`
  يفحص الـ flag. لو disabled → يتجاوزها (لكن الـ MT poll يكتمل، نحسب
  `total_sessions` للـ heartbeat).
- على الإقلاع: `_run_loop` يطبع INFO أو WARNING واضح يوضّح الـ mode.

| القيمة | يُعتبر | الاستخدام |
|--------|--------|-----------|
| unset, "", "0", "false", "maybe" | **disabled** (default R3) | الإنتاج |
| "1", "true", "yes", "on" (case-insensitive) | enabled | الطوارئ لو FR sql تعطّل |

**Failure mode**: "stay off" — أي قيمة غير معروفة تبقى الـ writes
disabled كي لا نعود لـ double-writes بالخطأ.

## مخاطر القراءة بعد R3 (overcounting من duplicates قديمة)

الجدول `radacct` قد يحوي rows مكرَّرة من فترة R1→R2 (قبل R3): واحدة
من puller (بـ `acctsessionid = acctuniqueid = MT .id`) وواحدة من FR
(بـ `acctuniqueid` = UUID). بعد R3:
- **لا rows جديدة مكرَّرة** — FR فقط يكتب.
- **rows قديمة لا تُحذَف** (لم نُنفّذ dedup مدمّر — انظر R4 أدناه).

القرّاء المحتمل تأثرهم بـ overcounting لجلسات تلك الفترة:

| ملف | استعلام | الأثر |
|-----|---------|--------|
| `app/radius/services/dashboard_metrics.py:72` | `COUNT(*) FROM radacct WHERE acctstoptime IS NULL` | "online now" قد يكون مضاعفًا للجلسات القديمة المفتوحة |
| `app/radius/services/policy_engine.py:199` | `COUNT(*) ... WHERE username=? AND acctstoptime IS NULL` | `concurrent_limit` قد يرفض مستخدمًا له session واحدة فقط بسبب row مكرَّر مفتوح من الفترة السابقة |
| `app/radius/integration/sqlite_adapter.py:292` | `SELECT * FROM radacct` (online sessions list) | عرض مكرَّر |
| `app/radius/routes/reports.py:44, 87` | reports queries | totals مضاعفة لتلك الفترة |
| `app/radius/integration/radius_coa.py:306` | `SELECT acctsessionid, nasipaddress FROM radacct` (للـ disconnect lookup) | قد يُرسل disconnect لـ acctsessionid قديم من puller — غير خطر لأن MT يتجاهل غير الموجود |

**التخفيف الفوري**: لو لاحظت overcounting يُؤثّر على تجربة المستخدم
(مثل `concurrent_limit` خاطئ)، نفّذ يدويًا (آمن، لا يلمس الجلسات النشطة):
```sql
UPDATE radacct
SET acctstoptime = COALESCE(acctstoptime, datetime('now')),
    acctterminatecause = COALESCE(NULLIF(acctterminatecause,''), 'R3-Cleanup')
WHERE acctstoptime IS NULL
  AND acctstarttime < <timestamp of R3 deploy>
  AND acctsessionid = acctuniqueid;  -- علامة rows الـ puller
```
هذا closes rows القديمة المفتوحة من الـ puller (signature: session_id == unique_id).
الـ FR rows لها UUIDs مختلفة في الحقلين، لن تتأثّر.

**لا تُنفَّذ تلقائيًا** — يعتمد القرار على إذا كان لديك جلسات
"معلَّقة" (نادرة).

## R4 (موصى به، لا يُنفَّذ هنا) — dedup migration حذِر

اختياري لو تأثّر reporting:

1. migration `017_radacct_dedup_legacy_puller_rows.sql`:
   - يحذف الـ rows المُغلَقة (acctstoptime IS NOT NULL) التي
     `acctsessionid = acctuniqueid` (سيغناتشر الـ puller).
   - لا يحذف الـ rows المفتوحة (احتمال جلسة قيد التشغيل).
   - يحذف الـ FR rows التي تحمل نفس (tenant_id, username, nasipaddress,
     acctstarttime ± 30s) كـ puller-rows في الفترة الـ R1→R2.
2. الـ migration يكون **مرّة واحدة** — يفحص شرطًا (مثل
   COUNT puller-rows > 0) قبل التنفيذ.
3. ينبغي backup قبل التنفيذ:
   `cp /app/instance/hoberadius.db /backups/hoberadius.pre-R4.db`.

**سبب التأجيل**: dedup مدمّر — أي خطأ في المنطق يفقد بيانات. R3
يوقف النزيف؛ R4 يُنظّف عند الحاجة فقط، بعد تأكيد الشكل الفعلي للبيانات
على الإنتاج.

## Slices لاحقة (ما بعد R3/R4)

- **enrichment via puller**: استخدام `_tick` لاستخراج metadata من MT
  (uptime، interface counters، router-set tags) وحقنها في radacct
  rows التي كتبها FR (UPDATE لا INSERT). single-source preserved.
- **Acct-Status-Type-aware response shaping**: rate-limit/drop عند فيضان.
- **مراقبة**: counter للـ Accounting-Request received vs Response sent
  vs sql success — مفيد للـ alerting.

## Slices لاحقة (ما بعد R3)

- **Acct-Status-Type-aware response shaping**: حاليًا نُرجع response
  لكل request. يمكن إضافة rate-limit/drop عند فيضان.
- **مراقبة**: counter للـ Accounting-Request received vs Response sent
  vs sql success — مفيد للـ alerting.
