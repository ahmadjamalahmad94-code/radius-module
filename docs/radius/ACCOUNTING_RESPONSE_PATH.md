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

## الـ slice التالي (موصى به، لا يُنفَّذ هنا)

1. **schema alignment**: migration `017_radacct_ipv6_columns.sql` تضيف:
   ```sql
   ALTER TABLE radacct ADD COLUMN framedipv6prefix TEXT DEFAULT '';
   ALTER TABLE radacct ADD COLUMN framedinterfaceid TEXT DEFAULT '';
   ALTER TABLE radacct ADD COLUMN delegatedipv6prefix TEXT DEFAULT '';
   ```
   ثم radacct rows ستُدخَل عبر FR sql فعليًا.

2. **حسم تعارض accounting_puller**: قرار بين:
   - (أ) إيقاف accounting_puller بعد تفعيل FR sql (single source).
   - (ب) جعل accounting_puller يقرأ فقط ولا يكتب (read-only للـ MT API،
        يُكمّل ما لا يصل عبر RADIUS مثل uptime).
   - (ج) تنسيق المفاتيح: استخدام نفس acctuniqueid بدل acctsessionid.

3. **Acct-Status-Type-aware response shaping**: حاليًا نُرجع response
   لكل request. يمكن تحسينه لـ rate-limit أو drop عند فيضان.

4. **مراقبة**: مقياس counter لـ Accounting-Request received vs
   Response sent vs sql success. يُمكن إضافته في Flask عبر webhook من
   FR post-auth (لكن sql post-auth معطّل — يلزم endpoint مستقل).
