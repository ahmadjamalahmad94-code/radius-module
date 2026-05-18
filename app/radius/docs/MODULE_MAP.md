# خريطة وحدة RADIUS داخل HobeHub

> هذه الوحدة **module داخلي** في HobeHub — ليست تطبيقًا منفصلًا، ولا microservice، ولا استبدالًا لـ legacy.
> HobeHub يبقى System of Record. RADIUS هنا = طبقة تنظيم وإدارة لمعطيات RADIUS.

---

## 1. الحدود (Module Boundaries)

```
app/
├── radius/                  ← الوحدة الجديدة
│   ├── core/                ← types, constants, errors (لا I/O)
│   ├── integration/         ← Adapter Layer (يعزل عن backend)
│   ├── devices/             ← NAS Devices
│   ├── profiles/            ← Access Profiles
│   ├── accounting/          ← Accounting Sessions
│   ├── sessions/            ← Online Sessions
│   ├── policies/            ← Radius Policies
│   ├── services/            ← orchestration (audit, settings)
│   ├── routes/              ← Flask blueprint
│   ├── templates/           ← Jinja (RTL أولًا)
│   └── docs/
│
├── services/                ← موجود سابقًا — لا يُلمس
│   ├── radius_client/       ← REST client لـ app_ad2 (يلفّه api_adapter)
│   └── radius_dashboard.py  ← cache layer (يبقى كما هو)
│
└── legacy_parts/            ← لا تُضاف ملفات RADIUS جديدة هنا
```

### قواعد ثابتة
- **لا** يستورد `app/legacy_parts/*` من `app/radius/*`.
- **لا** يستورد `app/radius/*` من `app/legacy_parts/*` إلا في `legacy.py` نفسه (نقطة التسجيل الوحيدة).
- `app/radius/services/*` لا تستدعي `requests` ولا `sqlite3` ولا `psycopg` مباشرة — كل I/O عبر الـ adapter.
- `app/radius/templates/*` لا تحتوي business logic.

---

## 2. الوحدات التسع (1:1 مع مجلدات)

| #  | الوحدة             | المسؤولية                                | المجلد                | المصدر الأساسي للإلهام |
|----|--------------------|------------------------------------------|------------------------|-----------------------|
| 1  | NAS Devices        | إدارة الـ NAS/BRAS (CRUD + اختبار)       | `devices/`             | toughradius `NetNas`   |
| 2  | Access Profiles    | قوالب الباقات (rate, quota, pool)        | `profiles/`            | toughradius `RadiusProfile` |
| 3  | Radius Accounts    | حسابات RADIUS وربطها بالـ beneficiaries  | `services/` + adapter  | toughradius `RadiusUser` |
| 4  | Online Sessions    | المتصلون الآن + Disconnect (CoA/DM)      | `sessions/`            | toughradius `RadiusOnline` |
| 5  | Accounting Sessions| سجلات المحاسبة المنتهية + تقارير         | `accounting/`          | toughradius `RadiusAccounting` |
| 6  | Radius Policies    | سياسات: rate-limit, quota, time-window   | `policies/`            | freeradius (مفاهيم فقط)|
| 7  | Radius Audit Logs  | كل تغيير على كيانات RADIUS               | `services/audit.py`    | toughradius `SysOprLog`|
| 8  | Radius Settings    | الـ mode + flags + base url               | `services/settings.py` | HobeHub CLAUDE.md §4   |
| 9  | Radius Adapter     | عقد تجريد للـ backend (manual/api/direct)| `integration/`         | فلسفة HobeHub §0.1     |

---

## 3. Services Map

كل service: ملف واحد، ≤ 300 سطر، يستلم `RadiusAdapter` في الـ constructor.

```
services/
├── audit.py            → RadiusAuditService.record(...)
├── settings.py         → RadiusSettingsService.current() / .set_mode(...)
├── accounts.py         → RadiusAccountsService (CRUD + reset_password)
├── devices.py          → NasDevicesService (CRUD + ping test)
├── profiles.py         → AccessProfilesService (CRUD)
├── sessions.py         → OnlineSessionsService (list + disconnect)
├── accounting.py       → AccountingService (queries + aggregates)
└── policies.py         → PoliciesService (CRUD + evaluate)
```

التوقيع المعتمَد:

```python
class RadiusAccountsService:
    def __init__(self, adapter: RadiusAdapter, audit: RadiusAuditService): ...
    def create(self, *, actor: str, dto: RadiusAccount) -> RadiusAccount: ...
    def update(self, *, actor: str, username: str, patch: dict) -> RadiusAccount: ...
    def disable(self, *, actor: str, username: str) -> None: ...
```

- `actor` يأتي من session الإدارة (مسؤولية الـ route).
- كل عملية كتابة تستدعي `audit.record(...)` قبل العودة.

---

## 4. Models Map (Domain ↔ DB ↔ Source of Truth)

| Domain (`core/types.py`) | جدول SQL                  | Source of Truth         | ملاحظات |
|--------------------------|---------------------------|--------------------------|---------|
| `NasDevice`              | `radius_nas_devices`      | HobeHub DB               | secret يُخزَّن مشفَّر (Fernet)   |
| `AccessProfile`          | `radius_access_profiles`  | HobeHub DB               | المرجع المعياري للباقات        |
| `RadiusAccount`          | `radius_accounts`         | HobeHub DB (mirror)      | المعتمد عند `mode=api`: API هو SoT والـ table mirror |
| `OnlineSession`          | —                         | Backend live (لا تُخزَّن) | تُجلب عند الطلب فقط             |
| `AccountingSession`      | `radius_accounting`       | Backend RADIUS           | يُرحَّل دوريًا (مرحلة لاحقة)     |
| `RadiusPolicy`           | `radius_policies`         | HobeHub DB               | تُترجم لـ attributes داخل الـ adapter |
| `RadiusAuditEntry`       | `radius_audit_logs`       | HobeHub DB               | لا يُحذف منها أبدًا              |
| `RadiusSettings`         | `radius_settings` (k/v)   | HobeHub DB + env         | env يطغى على DB                  |

> **قاعدة:** أي حقل في الـ DTO غير موجود في الجدول = خطأ تصميم؛ صحّح أحدهما قبل المتابعة.

---

## 5. علاقات التكامل مع HobeHub الحالي

| تفاعل                                | كيف يتم؟                                            |
|--------------------------------------|------------------------------------------------------|
| ربط حساب RADIUS بمستفيد              | `RadiusAccount.beneficiary_id` → `beneficiaries.id` |
| استدعاء الـ adapter من view قديم    | استورد `from app.radius.services.accounts import ...`|
| الـ cache الحالي `radius_dashboard`  | يبقى كما هو — adapter mode=api يستفيد منه           |
| الـ `RadiusClient` الحالي             | يُلفّ في `integration/api_adapter.py` لاحقًا         |
| الـ MikroTik (مستقبلًا)              | `integration/direct_adapter.py` بنفس الـ ABC         |

---

## 6. ما لن يحدث في هذه المرحلة

- ❌ تسجيل الـ blueprint في `legacy.py` بعد (الـ stub جاهز فقط).
- ❌ كتابة أي migration لقاعدة البيانات.
- ❌ إنشاء routes فعلية أو templates.
- ❌ نقل أي ملف من `app/services/` أو `app/legacy_parts/`.
- ❌ تعديل `legacy.py` أو `_LEGACY_PARTS`.

كل ما سبق يأتي في الخطوات القادمة، كل خطوة في مراجعة منفصلة.
