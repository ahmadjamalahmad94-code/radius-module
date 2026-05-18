# HobeRadius — المرجع الموحَّد للمشروع

> **اقرأ هذا الملف أولًا.** كل ما تحتاجه لفهم النظام، البنية، القرارات،
> والـ contract مع HobeHub موجود هنا. ما هو خارج هذا الملف = إما تفصيل
> عميق في مستند فرعي (مذكور بالاسم) أو كود يحقّق المبادئ المذكورة هنا.

تاريخ الإصدار: 2026-05-18 · المرحلة: P1 (foundation) · النسخة: 0.1.0

---

## 0. فهرس الوثائق

| الملف | المحتوى |
|------|---------|
| **`REFERENCE.md`** (هذا) | المرجع الجامع — ابدأ هنا |
| `README.md` | تعليمات سريعة للتشغيل |
| `FOUNDATION.md` | فلسفة المنتج (الإصدار السابق — تم دمجه هنا) |
| `INTEGRATION_WITH_HOBEHUB.md` | تفصيل الـ contract مع HobeHub |
| `docs/MIKROTIK_API_ANALYSIS.md` | تحليل عميق لبروتوكول MikroTik API |
| `app/radius/docs/MODULE_MAP.md` | خريطة وحدات الـ radius |
| `app/radius/docs/SCHEMAS.md` | مسوَّدة جداول الـ SQL |
| `app/radius/docs/INTEGRATION.md` | خطة دمج HobeRadius داخل HobeHub (قديمة — مرجع تاريخي) |
| `examples/hobehub_client.py` | SDK Python لاستهلاك HobeRadius API |

---

## 1. ما هو HobeRadius؟

**نظام إدارة RADIUS مستقل** يدير حسابات الإنترنت والبطاقات والباقات والجلسات،
ويستهدف بيئات ISP/Hotspot. يقدّم:
- لوحة إدارة عربية RTL فاخرة.
- REST API كامل لاستهلاكه من HobeHub أو أي بيئة.
- ربط مباشر بـ **MikroTik RouterOS API** كـ backend فعلي.
- Adapter Layer قابل للتوسع لبقية الـ backends (FreeRADIUS, Cisco, ...).

**ما هو ليس:**
- ليس وحدة داخل HobeHub.
- ليس RADIUS protocol server بحد ذاته (UDP 1812) — نتحكّم بالـ MTs،
  والـ MTs هي من يتحدّث RADIUS لأجهزتها (أو يدير local hotspot users).

---

## 2. الفلسفة (8 مبادئ لن تُكسر)

1. **منتج مستقل** — DB/auth/إعدادات/نشر/tests خاصة به. لا تبعية كود على HobeHub.
2. **HobeHub-style فقط** — نشترك في الهوية البصرية (`#1E1E1E` / `#F4BA2A`) و RTL،
   لا نشترك في الكود.
3. **التكامل = HTTP** — REST inbound + Webhooks outbound. لا DB مشتركة، لا session،
   لا استيراد متبادل.
4. **SaaS-first** — البنية تدعم multi-tenant مستقبلًا (tenant_id في كل كيان).
5. **Adapter-based** — مصدر البيانات قابل للاستبدال:
   `ManualAdapter` (in-memory) → `MikrotikAdapter` → مستقبلًا `SqliteStore` / `FreeRadiusAdapter`.
6. **Modular monolith** — كل وحدة في مجلد بحدود واضحة. لا microservices.
7. **حدود تخميد الشيفرة**:
   - لا ملف Python > 400 سطر.
   - لا قالب يحوي business logic.
   - لا route فيه أكثر من استدعاء service واحد + render.
   - لا اعتماد ثالث-طرف إلا عند الحاجة الماسّة (حاليًا: Flask فقط).
8. **Versioned API** — `/api/v1/` لا يكسر. أي breaking change → `/v2/` بجانب v1.

---

## 3. التقنيات (Tech Stack)

| الطبقة | الاختيار | السبب |
|--------|---------|------|
| Web | Flask 3 | بسيط، يطابق HobeHub |
| DB (لاحقًا) | SQLite (dev) / PostgreSQL (prod) | نفس مسار HobeHub، لا ORM ثقيل |
| Auth (لوحة) | session + scrypt password hash | stdlib فقط |
| Auth (API) | Bearer token (CSV قابل للتدوير) | بسيط، آمن |
| Templates | Jinja2 + RTL | Cairo font |
| Static JS | vanilla — ترقيم client-side | لا framework |
| Tests | pytest | 42 اختبار وأكثر |
| MikroTik client | محلي من الصفر — socket/ssl stdlib | تحكّم كامل، صفر تبعية |
| Webhook signing | HMAC-SHA256 | معياري |
| Deploy | gunicorn/uwsgi خلف nginx (Linux) أو waitress (Windows) | — |

---

## 4. البنية على مستوى الجملة

```
┌──────────────────────────────────────────────────────────────┐
│                       Browser (Admin)                         │
│                  Cairo / RTL / Black + Gold                   │
└────────────────────────────┬─────────────────────────────────┘
                             │ HTTPS
                             ▼
┌──────────────────────────────────────────────────────────────┐
│                     HobeRadius (Flask)                        │
│  ┌────────────┐  ┌─────────────┐  ┌──────────────────────┐   │
│  │   Routes   │→ │  Services   │→ │ RadiusAdapter (ABC)  │   │
│  │  /admin/.. │  │  business   │  │  ┌────────────────┐  │   │
│  └────────────┘  └─────────────┘  │  │ ManualAdapter  │  │   │
│  ┌────────────┐                   │  │ MikrotikAdapter│──┼──→ TCP/TLS to MikroTik
│  │ REST API   │  ┌─────────────┐  │  │ (future...)    │  │   │
│  │  /api/v1/* │→ │  Webhooks   │  │  └────────────────┘  │   │
│  └────────────┘  │  dispatcher │  └──────────────────────┘   │
│       ▲          └──────┬──────┘  ┌──────────────────────┐   │
│       │                 │         │     Local Stores      │   │
│       │                 │         │  cards / admins/roles │   │
│       │                 │         └──────────────────────┘   │
└───────┼─────────────────┼────────────────────────────────────┘
        │                 │ HMAC-signed POST
        │                 ▼
┌───────┴─────────┐  ┌─────────────────────────────┐
│ HobeHub /       │  │ HobeHub /webhooks/radius     │
│ other env       │  │ (consumer)                   │
│ (consumer)      │  └─────────────────────────────┘
└─────────────────┘
```

---

## 5. تركيب المجلدات

```
radius-module/
├── REFERENCE.md                       ← هذا الملف
├── README.md
├── FOUNDATION.md
├── INTEGRATION_WITH_HOBEHUB.md
├── wsgi.py
├── run.ps1
├── requirements.txt
├── docs/
│   └── MIKROTIK_API_ANALYSIS.md
├── examples/
│   └── hobehub_client.py              ← SDK لاستهلاك HobeRadius
├── tests/
│   ├── test_mikrotik_protocol.py      ← 28 اختبار
│   └── test_mikrotik_client.py        ← 14 اختبار (mock router)
└── app/
    ├── __init__.py                    ← Flask app + stubs (csrf/auth/arabize)
    ├── radius/
    │   ├── core/
    │   │   ├── constants.py           ← قيم النموذج (statuses, plan_types, perms…)
    │   │   ├── types.py               ← 13 DTO (frozen dataclasses)
    │   │   └── errors.py              ← تسلسل أخطاء
    │   ├── integration/
    │   │   ├── adapter.py             ← RadiusAdapter ABC + factory
    │   │   ├── manual_adapter.py      ← in-memory (للـ dev + tests)
    │   │   ├── mikrotik_adapter.py    ← يربط ABC بـ MikroTik
    │   │   ├── factory.py             ← يختار حسب RADIUS_MODE
    │   │   └── mikrotik/
    │   │       ├── protocol.py        ← word/sentence encode/decode
    │   │       ├── client.py          ← TCP/TLS + login + commands
    │   │       ├── errors.py
    │   │       └── settings.py        ← MikrotikConfigStore
    │   ├── stores/
    │   │   ├── cards_store.py         ← batches + cards in-memory
    │   │   └── admins_store.py        ← admins + roles + scrypt
    │   ├── services/
    │   │   ├── audit.py
    │   │   ├── devices.py
    │   │   ├── plans.py
    │   │   ├── users.py
    │   │   ├── cards.py
    │   │   ├── admins.py
    │   │   ├── sessions.py
    │   │   └── dashboard.py
    │   ├── routes/                    ← Flask blueprint /admin/radius
    │   │   ├── blueprint.py
    │   │   ├── dashboard.py
    │   │   ├── devices.py
    │   │   ├── plans.py
    │   │   ├── users.py
    │   │   ├── cards.py
    │   │   ├── sessions.py
    │   │   └── admins.py
    │   ├── templates/
    │   │   └── radius/                ← 13 قالب Jinja
    │   ├── seed.py                    ← بيانات ديمو عند الإقلاع
    │   └── docs/                      ← وثائق قديمة (مرجع)
    ├── api/                           ← REST /api
    │   ├── blueprint.py
    │   ├── auth.py                    ← Bearer token
    │   ├── responses.py               ← شكل JSON موحَّد
    │   └── v1/
    │       ├── health.py
    │       ├── accounts.py            ← 10 endpoints
    │       ├── cards.py
    │       ├── profiles.py
    │       ├── nas.py
    │       ├── sessions.py
    │       ├── accounting.py
    │       ├── webhooks.py
    │       └── mikrotik.py            ← 6 endpoints لإدارة الـ MTs
    ├── webhooks/
    │   ├── events.py                  ← قائمة الأنواع (12 حدث)
    │   ├── config.py                  ← WebhookConfigStore
    │   └── dispatcher.py              ← HMAC + delivery
    ├── static/
    │   ├── css/
    │   │   ├── admin_layout.css       ← Premium Corporate (~250 سطر)
    │   │   └── dashboard_table.css
    │   └── js/
    │       └── dashboard_table.js     ← ترقيم client-side
    └── templates/
        └── admin/
            └── _admin_layout.html     ← Layout أساسي + sidebar
```

---

## 6. النماذج (Data Models)

كل نموذج `@dataclass(frozen=True)` في `app/radius/core/types.py`.

### 6.1 NasDevice — جهاز MikroTik أو غيره
حقول رئيسية: `name, address, secret, vendor, nas_type, auth_port, acct_port, coa_port,
location, coordinates, monitoring_enabled, enabled, last_seen_at`.

### 6.2 AccessPlan (Plan / Offer) — العرض الغني
بُعد كامل:
- **الوقت**: `duration_minutes, validity_days, max_daily_minutes, max_weekly_minutes, max_monthly_minutes, session_timeout_sec, idle_timeout_sec`.
- **الكوتا**: `quota_total_mb, quota_daily_mb, quota_monthly_mb, quota_reset_strategy`.
- **السرعة**: `speed_up_kbps, speed_down_kbps, burst_up_kbps, burst_down_kbps, burst_threshold_kbps, burst_time_sec`.
- **الشبكة**: `concurrent_sessions, address_pool, framed_pool, vlan_id, ipv6_pool`.
- **القيود**: `bind_mac, bind_ip`.
- **الإتاحة**: `allowed_days, allowed_hours_from, allowed_hours_to`.
- **تجاري**: `price, currency, color, priority`.

### 6.3 Subscriber — مشترك أو مستخدم بطاقة
- `username, password, user_type, plan_id`.
- شخصي: `full_name, mobile, email, address, national_id`.
- حالة: `status, first_login_at, expire_at, last_login_at`.
- شبكة: `mac_lock, static_ip, vlan_id, override_concurrent`.
- استخدام: `used_seconds, used_bytes_in, used_bytes_out`.
- ربط: `beneficiary_ref` (HobeHub), `card_batch_id`.

### 6.4 CardBatch + Card
- `CardBatch`: `batch_code, plan_id, count, generated, used, username_prefix, username_length, password_length, created_by, status`.
- `Card`: `batch_id, username, password, plan_id, used, first_used_at, used_by_mac, expire_at, revoked`.

### 6.5 OnlineSession + AccountingSession
- `OnlineSession`: `username, session_id, nas_id, nas_address, framed_ip, mac_address, started_at, last_update_at, bytes_in, bytes_out, plan_name, user_type`.
- `AccountingSession`: نفسها + `stopped_at, duration_sec, terminate_cause`.

### 6.6 Admin + Role
- `Admin`: `username, password_hash (scrypt), full_name, email, mobile, role_id, enabled, last_login_at`.
- `Role`: `name, display_name, description, permissions (tuple), is_system`.

### 6.7 RadiusAuditEntry — سجل العمليات
`actor, action, target_type, target_id, payload, created_at`.

### 6.8 RadiusSettings + DashboardSnapshot
- `RadiusSettings`: `mode, api_ready, api_writes_enabled, base_url, timeout_sec`.
- `DashboardSnapshot`: 16 حقلًا (KPIs + recent_actions + top_plans).

---

## 7. الأدوار والصلاحيات

27 صلاحية في 8 فئات في `app/radius/core/constants.py`:

| فئة | صلاحيات |
|-----|---------|
| `dashboard` | view |
| `users` | view, create, edit, delete, disconnect |
| `cards` | view, generate, revoke |
| `plans` | view, create, edit, delete |
| `nas` | view, create, edit, delete |
| `sessions` | view, disconnect |
| `admins` | view, create, edit, delete |
| `settings/audit/api` | settings.view, settings.edit, audit.view, api.use |

### الأدوار الافتراضية
- `super_admin` — كل الصلاحيات.
- `operator` — إدارة users/cards/sessions/audit.
- `support` — view + disconnect.
- `billing` — view فقط للأقسام المالية.
- `viewer` — dashboard فقط.

---

## 8. الـ Adapter Pattern

`RadiusAdapter` (ABC في `integration/adapter.py`) يعرّف عقد التعامل مع
أي backend. الـ services لا تعرف ما هو الـ backend.

### Adapters حاليًا
| Adapter | الوضع | الاستخدام |
|---------|------|----------|
| `ManualAdapter` | `manual` | dev + tests + بدون MT |
| `MikrotikAdapter` | `mikrotik` / `direct` | الإنتاج — MT هو SoT |

### الاختيار
- `RADIUS_MODE=manual` (افتراضي) → ManualAdapter
- `RADIUS_MODE=mikrotik` → MikrotikAdapter (يحتاج `MIKROTIK_HOST` + credentials)

### إضافة Adapter جديد (5 خطوات)
1. أنشئ `integration/<name>_adapter.py` يرث `RadiusAdapter`.
2. نفّذ كل abstract methods.
3. `register_adapter("name", YourAdapter)` في نهاية الملف.
4. أضف معرف الوضع في `core/constants.py`.
5. حدّث `factory._resolve_mode()` ليقبل الاسم الجديد.

---

## 9. MikroTik Integration (ملخّص)

> التفصيل في `docs/MIKROTIK_API_ANALYSIS.md`.

### المسارات المستخدمة
```
/login                            ← post-6.43 plain
/system/identity/print            ← healthcheck
/system/resource/print            ← uptime/cpu/version
/ip/hotspot/user/print/add/set/remove
/ip/hotspot/user/profile/print/add/set/remove
/ip/hotspot/active/print/remove   ← online + disconnect
```

### Mapping (نماذجنا ↔ RouterOS)
- `Subscriber.username` ↔ `=name`
- `Subscriber.mac_lock` ↔ `=mac-address`
- `Subscriber.status` (enabled/disabled) ↔ `=disabled` (no/yes)
- `AccessPlan.speed_up_kbps/down` ↔ `=rate-limit=UPk/DOWNk`
- `OnlineSession.username` ↔ `=user`
- `OnlineSession.session_id` ↔ `=.id` (داخلي MT)

### الـ Connection (4 صور)
| host port | TLS | verify | الحالة |
|-----------|-----|--------|--------|
| `8728` | – | – | Plain (لا تستخدم في الإنتاج) |
| `8729` | ✓ | ✓ | TLS مع شهادة |
| `8729` | ✓ | ✗ | TLS بدون شهادة (Anonymous DH) |
| – | – | – | local dev: `manual` adapter |

### اختبار الاتصال (3 طرق)
1. **عبر env + UI**: ضع `MIKROTIK_HOST/USER/PASSWORD` وشغّل الـ app.
2. **عبر API**:
   ```
   POST /api/v1/mikrotik/test-credentials
   { "host":"…", "username":"…", "password":"…" }
   ```
3. **عبر MikrotikConfigStore**: أضف اتصالًا ثم `POST /api/v1/mikrotik/<id>/test`.

---

## 10. REST API

### القواعد
- Prefix: `/api/v1/`.
- Auth: `Authorization: Bearer <token>` (من `HOBERADIUS_API_TOKENS` CSV).
- شكل الرد:
  ```json
  {
    "ok": true|false,
    "data": {...},                ← عند ok=true
    "error": {"code","message","details"},  ← عند ok=false
    "meta": {"request_id","version":"v1"}
  }
  ```
- أكواد الخطأ: `unauthorized, forbidden, not_found, validation_error, conflict,
  rate_limited, internal_error, not_implemented, auth_error, connect_error, mikrotik_error`.

### Endpoints (31 endpoint)

| الفئة | METHOD | المسار |
|------|--------|--------|
| Discovery | GET | `/api/v1/_routes` (يُرجع كل الـ routes) |
| Health | GET | `/api/v1/health` (no auth) |
| Version | GET | `/api/v1/version` (no auth) |
| Accounts | GET POST | `/api/v1/accounts` |
| Accounts | GET PATCH DELETE | `/api/v1/accounts/<username>` |
| Accounts | POST | `/api/v1/accounts/<u>/(disable\|enable\|reset_password\|extend_time)` |
| Accounts | GET | `/api/v1/accounts/<u>/usage` |
| Cards | POST GET | `/api/v1/cards/generate`, `/cards/<id>`, `/cards/<id>/revoke` |
| Profiles | GET | `/api/v1/profiles`, `/profiles/<id>` |
| NAS | GET | `/api/v1/nas` |
| Sessions | GET POST | `/api/v1/sessions/online`, `/sessions/disconnect` |
| Accounting | GET | `/api/v1/accounting` |
| Webhooks | GET PUT POST | `/api/v1/webhooks/config`, `/webhooks/test` |
| MikroTik | GET POST PATCH DELETE | `/api/v1/mikrotik`, `/mikrotik/<id>` |
| MikroTik | POST | `/api/v1/mikrotik/<id>/test`, `/mikrotik/test-credentials` |

### Idempotency
عمليات POST تقبل `Idempotency-Key: <key>` — تخزَّن في جدول لاحقًا.

---

## 11. Webhooks (HobeRadius → HobeHub)

### القواعد
- التوقيع: `X-HobeRadius-Signature: sha256=<hmac-hex>` على body خام بـ `WEBHOOK_SECRET`.
- إعداد الـ target: `PUT /api/v1/webhooks/config { "target_url", "secret", "enabled_events" }`.
- اختبار: `POST /api/v1/webhooks/test` → يُرسل `webhook.test`.

### شكل الحدث
```json
{
  "event": "session.stopped",
  "event_id": "ev_<16hex>",
  "occurred_at": "2026-05-18T13:21:00Z",
  "data": { ... },
  "version": "v1"
}
```

### قائمة الأحداث (12)
| الحدث | متى |
|------|-----|
| `account.created/updated/disabled/expired` | تغييرات على المشترك |
| `card.generated/consumed` | البطاقات |
| `session.started/stopped/disconnected` | الجلسات |
| `quota.threshold` | تجاوز 80/95/100% |
| `nas.unreachable` | NAS لا يرد |
| `webhook.test` | اختبار يدوي |

### من جهة HobeHub (verify)
```python
from examples.hobehub_client import verify_webhook_signature
ok = verify_webhook_signature(request.get_data(), request.headers["X-HobeRadius-Signature"], SECRET)
```

---

## 12. تشغيل المشروع

### Dev (manual mode)
```powershell
$env:FLASK_APP = "wsgi:app"
$env:FLASK_DEBUG = "1"
$env:RADIUS_MODE = "manual"
$env:HOBERADIUS_API_TOKENS = "dev-token-please-change"
python -m flask run --host 127.0.0.1 --port 5050
```
يبذر تلقائيًا 4 NAS، 8 خطط، 36 مشترك، مديرَين (`admin/admin`, `operator/operator`).

### Production (MikroTik mode)
```powershell
$env:RADIUS_MODE = "mikrotik"
$env:MIKROTIK_HOST = "10.0.0.1"
$env:MIKROTIK_PORT = "8729"
$env:MIKROTIK_TLS = "1"
$env:MIKROTIK_TLS_VERIFY = "0"   # لو الشهادة self-signed
$env:MIKROTIK_USER = "admin"
$env:MIKROTIK_PASSWORD = "<strong>"
$env:HOBERADIUS_API_TOKENS = "<rotateable,csv>"
$env:HOBERADIUS_NO_SEED = "1"    # لا seed في الإنتاج
python -m gunicorn -w 2 wsgi:app -b 127.0.0.1:5050     # Linux
# أو
python -m waitress --listen=127.0.0.1:5050 wsgi:app    # Windows
```

### الـ URLs
- `/` → يحوّل لـ Dashboard
- `/admin/radius/` → لوحة التحكم
- `/admin/radius/users | plans | cards | devices | online | admins | roles`
- `/api/v1/_routes` → كل endpoints الـ API
- `/api/v1/health` (no auth) → نبض الخدمة

---

## 13. الاختبار

```powershell
python -m pytest tests/ -q
```

حاليًا: **42 اختبار** يغطّي:
- `tests/test_mikrotik_protocol.py` — 28 اختبار للترميز (length, word, sentence, attrs).
- `tests/test_mikrotik_client.py` — 14 اختبار مع mock TCP server (login, run, print, trap).

---

## 14. التكامل مع HobeHub (الـ Contract الكامل)

> تفصيل في `INTEGRATION_WITH_HOBEHUB.md`.

### من جهة HobeHub
1. ينسخ `examples/hobehub_client.py` إلى `HobeHub/app/services/hoberadius_client.py`.
2. يضبط env:
   - `HOBERADIUS_BASE_URL=https://radius.example.com`
   - `HOBERADIUS_API_TOKEN=<from-rotateable-csv>`
   - `HOBERADIUS_WEBHOOK_SECRET=<shared>`
3. يستخدم:
   ```python
   from app.services.hoberadius_client import HobeRadiusClient
   c = HobeRadiusClient()
   c.create_account(username="u1", password="x", profile_id=2,
                    beneficiary_ref="1234", idempotency_key="ben-1234-create")
   ```
4. يستقبل webhooks على `/webhooks/radius` ويتحقق من التوقيع.

### من جهة HobeRadius
- ضبط الـ webhook عبر:
  ```
  PUT /api/v1/webhooks/config
  { "target_url":"https://hobehub.example.com/webhooks/radius",
    "secret":"<shared>",
    "enabled_events":["session.started","session.stopped","quota.threshold"] }
  ```

### قواعد التوافق طويل المدى
- v1 لا يكسر. حقول جديدة مسموح إضافتها، حذف/تغيير ممنوع.
- pagination معيارية: `?limit=&cursor=` + `meta.next_cursor`.
- `Idempotency-Key` احترامها واجب للـ POST.

---

## 15. خارطة الطريق (Roadmap)

| Phase | الناتج | الحالة |
|-------|--------|------|
| **P0** | ABC + manual + NAS + Online + REST API + Webhooks | ✅ |
| **P1** | Dashboard فاخر + الـ 14 شاشة + MikroTik integration كاملة + 42 test | ✅ |
| **P2** | تخزين دائم SQLite + migrations مرقَّمة + admin login screen | ⏳ |
| **P3** | listen() async من MT للجلسات الحية + queue webhooks مع retry | ⏳ |
| **P4** | Accounting + reports + استرجاع dakelib بطاقات + invoices | ⏳ |
| **P5** | Policies engine + apply-to-profile + scheduling | ⏳ |
| **P6** | OpenAPI 3.1 spec + SDK Python/JS رسمي + rate limit | ⏳ |
| **P7** | RADIUS protocol server (UDP 1812/1813) ← اختياري متقدم | — |

---

## 16. قواعد المطوّر (Dev Rules — لا تُكسر)

### Python
1. **لا ملف > 400 سطر**. إن قارب، قسّم لـ submodules.
2. **`from __future__ import annotations`** في كل ملف.
3. **dataclasses frozen** للـ DTOs. تعديل = `dataclasses.replace(...)`.
4. **services لا تستورد Flask** — تأخذ `actor: str` من الـ route.
5. **routes لا تكتب SQL** ولا تستدعي adapter مباشرة — service فقط.
6. **kwarg-only** للوسائط الحرجة في الـ services (`def create(self, *, actor, dto)`).

### Templates
1. **لا منطق** — لا `if x and y or z` معقد. أحضر الشرط جاهزًا من الـ view.
2. **`extends "admin/_admin_layout.html"`** + بلوكات: `title, page_title, crumbs, head_extra, content`.
3. **RTL** افتراضي. مكوّنات صغيرة قابلة لإعادة الاستخدام.

### API
1. **كل response عبر `ok()` / `fail()` / `not_implemented()`** — لا `jsonify` مباشر.
2. **validation أولًا** — إن نقص حقل → `validation_error` 422.
3. **endpoint جديد = ملف جديد** تحت `app/api/v1/`.

### MikroTik
1. **لا third-party deps** — كل شيء stdlib.
2. **كل اتصال له timeout** (افتراضي 10s).
3. **MikrotikTrap يُحوَّل** لـ `RadiusAdapterError` في الـ adapter.

### Git/Commits
1. **لا `git add .`** — حدّد الملفات.
2. **لا destructive** بدون إذن المستخدم.
3. **رسالة commit** = ماذا + لماذا (سطر واحد + paragraph عند الحاجة).

---

## 17. سجل القرارات المعمارية (ADR Lite)

| # | القرار | السبب |
|---|--------|------|
| 1 | منتج مستقل لا وحدة | HobeHub لا يجب أن يتضخّم؛ التكامل أنظف عبر HTTP |
| 2 | Adapter Pattern | تبديل backend بدون لمس services |
| 3 | MikroTik client من الصفر | تحكّم كامل، لا تبعية، 80 سطر فقط للبروتوكول |
| 4 | REST + Webhooks (لا shared session) | تكامل قابل للنشر منفصل، آمن |
| 5 | Bearer tokens CSV | rotation بدون انقطاع |
| 6 | HMAC SHA-256 للـ webhooks | معياري، يمنع spoofing |
| 7 | API versioning من البداية | v1 لا يكسر، v2 يقف بجانبه |
| 8 | scrypt للـ passwords | stdlib، قوي، آمن |
| 9 | in-memory stores في P1 | بساطة، انتقال SQLite في P2 بنفس الواجهة |
| 10 | seed data في dev | تجربة فورية، إيقاف بـ `HOBERADIUS_NO_SEED=1` |

---

## 18. أوامر مفيدة (Cheatsheet)

```bash
# تشغيل
python -m flask --app wsgi:app run --port 5050

# tests
python -m pytest tests/ -q
python -m pytest tests/test_mikrotik_protocol.py -v

# اكتشاف endpoints
curl -H "Authorization: Bearer dev-token-please-change" \
     http://127.0.0.1:5050/api/v1/_routes | jq

# health
curl http://127.0.0.1:5050/api/v1/health

# اختبار MikroTik
curl -X POST http://127.0.0.1:5050/api/v1/mikrotik/test-credentials \
  -H "Authorization: Bearer dev-token-please-change" \
  -H "Content-Type: application/json" \
  -d '{"host":"10.0.0.1","username":"admin","password":"x"}'

# توليد دفعة بطاقات
curl -X POST http://127.0.0.1:5050/api/v1/cards/generate \
  -H "Authorization: Bearer dev-token-please-change" \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: batch-2026-05-18-001" \
  -d '{"category_code":"Q5G","count":50}'
```

---

## 19. كيف تستكشف المشروع لأول مرة

1. اقرأ هذا الملف.
2. `app/radius/core/types.py` — تعرّف على النماذج (13 DTO، دقيقتان قراءة).
3. `app/radius/integration/adapter.py` — العقد المعماري.
4. `app/api/v1/__init__.py` ثم أي ملف v1 — كيف نخدم الـ HTTP.
5. `app/radius/integration/mikrotik/protocol.py` — البروتوكول الحقيقي.
6. `tests/test_mikrotik_protocol.py` — أمثلة عمل البروتوكول.
7. شغّل الـ app + افتح `/admin/radius/` — كل شيء حي.

---

## 20. ما هو ليس في هذا المرجع

- تفصيل عمليات MikroTik (فئات الـ trap، query operators، listen) → `docs/MIKROTIK_API_ANALYSIS.md`.
- تفصيل كل endpoint من جهة HobeHub → `INTEGRATION_WITH_HOBEHUB.md`.
- مسوَّدة جداول SQL القادمة في P2 → `app/radius/docs/SCHEMAS.md`.

---

**هذا الملف حي.** عند أي قرار معماري جديد، حدّث القسم المناسب هنا قبل
أي شيء آخر. هو السطح الأول الذي يقرأه أي مطوّر/مساعد جديد.
