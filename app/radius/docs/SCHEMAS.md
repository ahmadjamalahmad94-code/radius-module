# مخططات الجداول — وحدة RADIUS

> **هذه مسوَّدة فقط.** لا تُطبَّق migrations الآن.
> الهدف: مرجع موحَّد لِما سنكتبه لاحقًا في:
> - `app/legacy_parts/16_sqlite_schema_02_radius.py` (تحديث)
> - `app/legacy_parts/17_postgres_schema_setup_03_radius_internet.py` (تحديث)
> أو في ملف جديد منفصل `..._radius_module.py` لتجنّب لمس القديم.

## مبادئ
1. كل جدول SQLite و Postgres متطابق بالأسماء والأنواع (مع `AUTOINCREMENT` vs `BIGSERIAL`).
2. مفتاح أساسي `id` رقمي. مفاتيح خارجية تستخدم `ON DELETE RESTRICT` افتراضيًا.
3. أعمدة زمنية `created_at`/`updated_at` `TIMESTAMP` UTC naïve.
4. لا أعمدة JSON كبيرة بدون مبرر؛ سياسات RADIUS فقط (`policies.params`) تستخدم JSON.
5. كل جدول مُفهرس على الأعمدة المستخدمة في الـ WHERE الشائعة (راجع §11 من تقرير الفحص).

---

## 1. `radius_nas_devices`

| العمود        | النوع         | ملاحظات                            |
|----------------|---------------|-------------------------------------|
| id             | INTEGER PK    |                                     |
| name           | TEXT NOT NULL | فريد                                 |
| address        | TEXT NOT NULL | IP أو hostname                       |
| secret_enc     | TEXT NOT NULL | shared secret (Fernet)               |
| vendor         | TEXT NOT NULL | mikrotik / cisco / huawei / other    |
| description    | TEXT          |                                     |
| enabled        | INTEGER(0/1)  | افتراضي 1                            |
| created_at     | TIMESTAMP     |                                     |
| updated_at     | TIMESTAMP     |                                     |

فهارس: `UNIQUE(name)`, `INDEX(address)`.

---

## 2. `radius_access_profiles`

| العمود              | النوع          | ملاحظات                  |
|---------------------|----------------|---------------------------|
| id                  | INTEGER PK     |                            |
| name                | TEXT UNIQUE    |                            |
| up_rate_kbps        | INTEGER        | 0 = unlimited              |
| down_rate_kbps      | INTEGER        |                            |
| session_timeout_sec | INTEGER        |                            |
| idle_timeout_sec    | INTEGER        |                            |
| concurrent_sessions | INTEGER        | افتراضي 1                  |
| address_pool        | TEXT           |                            |
| description         | TEXT           |                            |
| enabled             | INTEGER(0/1)   |                            |
| created_at/updated_at| TIMESTAMP     |                            |

فهارس: `UNIQUE(name)`, `INDEX(enabled)`.

---

## 3. `radius_accounts`

| العمود          | النوع           | ملاحظات                                    |
|-----------------|-----------------|---------------------------------------------|
| id              | INTEGER PK      |                                             |
| username        | TEXT UNIQUE     |                                             |
| password_hash   | TEXT NOT NULL   | adapter يقرر hashing (manual=plain للاختبار)|
| profile_id      | INTEGER FK      | → radius_access_profiles(id)                |
| status          | TEXT NOT NULL   | enabled/disabled/expired/suspended          |
| expire_at       | TIMESTAMP       |                                             |
| mac_lock        | TEXT            |                                             |
| static_ip       | TEXT            |                                             |
| beneficiary_id  | INTEGER FK      | → beneficiaries(id) — قد يكون NULL         |
| remark          | TEXT            |                                             |
| created_at/updated_at | TIMESTAMP |                                             |

فهارس: `INDEX(beneficiary_id)`, `INDEX(status)`, `INDEX(expire_at)`.

---

## 4. (لا جدول) `online_sessions`

تُجلب من الـ adapter وقت الطلب فقط — **لا تُخزَّن**.

---

## 5. `radius_accounting`

| العمود           | النوع         | ملاحظات                       |
|------------------|---------------|--------------------------------|
| id               | INTEGER PK    |                                |
| username         | TEXT          | INDEX                          |
| session_id       | TEXT          |                                |
| nas_id           | TEXT          |                                |
| started_at       | TIMESTAMP     | INDEX                          |
| stopped_at       | TIMESTAMP     |                                |
| duration_sec     | INTEGER       |                                |
| bytes_in         | BIGINT        |                                |
| bytes_out        | BIGINT        |                                |
| terminate_cause  | TEXT          |                                |

فهارس: `INDEX(username, started_at)`, `INDEX(session_id)`.

> **سياسة الاحتفاظ:** archive بعد 90 يوم — يُترك للمرحلة اللاحقة.

---

## 6. `radius_policies`

| العمود        | النوع          | ملاحظات                            |
|----------------|----------------|-------------------------------------|
| id             | INTEGER PK     |                                     |
| name           | TEXT UNIQUE    |                                     |
| policy_type    | TEXT NOT NULL  | rate_limit/quota/time_window/...    |
| params_json    | TEXT NOT NULL  | JSON (يُرمَّز عبر helper)           |
| enabled        | INTEGER(0/1)   |                                     |
| priority       | INTEGER        | افتراضي 100                         |
| description    | TEXT           |                                     |
| created_at/updated_at | TIMESTAMP|                                     |

---

## 7. `radius_audit_logs`

| العمود        | النوع          | ملاحظات                |
|----------------|----------------|--------------------------|
| id             | INTEGER PK     |                          |
| actor          | TEXT           | admin username           |
| action         | TEXT           | create/update/delete/... |
| target_type    | TEXT           | account/profile/nas/...  |
| target_id      | TEXT           |                          |
| payload_json   | TEXT           |                          |
| created_at     | TIMESTAMP      | INDEX                    |

> لا يُحذف منها أبدًا. أرشفة لاحقًا لو نمت.

---

## 8. `radius_settings` (k/v بسيط)

| العمود     | النوع       | ملاحظات                         |
|------------|-------------|----------------------------------|
| key        | TEXT PK     | "mode" / "api_ready" / ...      |
| value      | TEXT        |                                  |
| updated_at | TIMESTAMP   |                                  |
| updated_by | TEXT        |                                  |

> env vars تطغى على هذه القيم وقت القراءة.

---

## ملاحظة على التطبيق
الـ migrations الفعلية تنتظر تأكيدًا منك. عند الموافقة سنضيف:
- `app/legacy_parts/16_sqlite_schema_02b_radius_module.py`
- `app/legacy_parts/17_postgres_schema_setup_03b_radius_module.py`

بدون لمس الملفات الأصلية (§5 من CLAUDE.md — نمط override).
