# HobeRadius — وثيقة الأساس

> منتج RADIUS مستقل بالكامل، يشترك مع HobeHub في الهوية البصرية فقط.
> يُبنى ليعيش كنظام قائم بذاته (SoT خاص به)، ثم يتكامل مع HobeHub لاحقًا
> عبر واجهة محددة (API/webhooks) — لا عن طريق نسخ الكود.

---

## 1. الفلسفة

| المبدأ | معناه |
|---------|--------|
| منتج لا وحدة | له DB، auth، إعدادات، نشر، tests خاصة به |
| HobeHub-style فقط | الألوان (`#1E1E1E` / `#F4BA2A`)، RTL، Premium Corporate. لا تبعية كود |
| SaaS-first | كل صف يحمل `tenant_id` (مستقبلًا) — اليوم single-tenant ضمنيًا |
| Adapter-based | الـ RADIUS backend قابل للاستبدال (manual → freeradius → mikrotik) |
| Modular monolith | كل وحدة في مجلد، حدود واضحة، لا microservices الآن |
| لا ملف > 400 سطر | إعادة تجزئة قبل تخطّي الحد |
| لا منطق في القوالب | view → service → adapter → store |
| Migration-safe | كل تغيير DB عبر migration مرقَّم |

## 2. الـ Tech Stack

| الطبقة | الخيار |
|---------|--------|
| Web    | Flask 3 |
| DB     | SQLite (تطوير) / PostgreSQL (إنتاج) — نفس schema |
| ORM    | بدون ORM ثقيل — `sqlite3` + helpers بسيطة (مثل HobeHub) |
| Auth   | session-based + password hashing (`hashlib.scrypt` أو `passlib`) |
| Templates | Jinja2 + RTL |
| Static | CSS موحَّد + JS خفيف |
| Tests  | pytest |
| Deploy | gunicorn خلف nginx (Linux) — أو Waitress على Windows |

---

## 3. خريطة الوحدات الكاملة

| # | الوحدة | الحالة |
|---|--------|--------|
| 1 | **Core** (types, constants, errors)          | ✅ موجود |
| 2 | **Integration Adapter** (ABC + factory)      | ✅ موجود |
| 3 | **Manual Adapter** (in-memory)               | ✅ موجود |
| 4 | **API Adapter** (يلفّ HobeHub لاحقًا)         | يُحذف هنا — يبقى في طبقة التكامل المستقبلية |
| 5 | **SQLite Store** (تخزين دائم)                | ⏳ التالي |
| 6 | **Migrations**                               | ⏳ مع الـ store |
| 7 | **Auth** (admins)                            | ⏳ |
| 8 | **NAS Devices**                              | ✅ CRUD |
| 9 | **Access Profiles**                          | ⏳ |
| 10 | **Radius Accounts**                          | ⏳ |
| 11 | **Online Sessions**                          | ✅ قراءة فقط |
| 12 | **Accounting**                               | ⏳ |
| 13 | **Policies**                                 | ⏳ |
| 14 | **Audit Logs**                               | ✅ in-memory — يحتاج DB |
| 15 | **Settings UI**                              | ⏳ |
| 16 | **Dashboard** (KPIs)                         | ⏳ |
| 17 | **REST API** (للتكامل مع HobeHub)            | ⏳ |
| 18 | **RADIUS Protocol Server** (UDP 1812/1813)   | مرحلة متقدمة جدًا — اختياري |

## 4. خطة المراحل

| Phase | الناتج |
|-------|--------|
| **P0** | حجر الأساس (تم) — ABC + manual + NAS + Online |
| **P1** | تخزين دائم: SQLite store + migrations + إعادة استخدام manual adapter كـ in-memory للـ tests |
| **P2** | Auth + Settings UI + Dashboard فارغ |
| **P3** | Access Profiles + Radius Accounts (CRUD) |
| **P4** | Accounting + Audit Logs + Reports |
| **P5** | Policies + Apply-to-profile |
| **P6** | REST API + توثيق |
| **P7** | RADIUS protocol server (اختياري) |
| **P∞** | تكامل HobeHub ↔ HobeRadius عبر REST، لا مشاركة كود |

## 5. الـ ApiAdapter القديم

كان يلفّ `app.services.radius_client` داخل HobeHub. الآن قرارنا:
- لا نحمله في الـ standalone (نزيله أو نحوّله إلى `legacy_api_adapter.py.skip`).
- التكامل مع HobeHub في المستقبل يكون من جهة HobeHub: HobeHub يستهلك REST لـ HobeRadius، وليس العكس.

## 6. الواجهة البصرية

- Sidebar أسود + شعار ذهبي + اسم المنتج «HobeRadius».
- خطوط: Cairo / Segoe UI.
- Buttons: ذهبي primary، أبيض بحدود secondary.
- Tables: نفس معيار HobeHub (ترقيم client-side، احتفاظ بحجم الصفحة في localStorage).
- Flashes: success/error/warning/info بألوان متناسقة.

## 7. حدود واضحة عن HobeHub

| نشترك في | لا نشترك في |
|----------|--------------|
| الهوية البصرية | DB |
| نمط أسماء الـ blocks | كود الـ views |
| معيار الجداول | الـ models / الـ schema |
| فلسفة modular | الـ legacy_parts |
| RTL + عربية أولًا | الـ auth flow |

## 8. قواعد لن تُكسر

1. لا تستورد من HobeHub أبدًا.
2. لا تنسخ ملف GPL داخل المشروع.
3. كل migration له رقم ثابت + رولباك.
4. كل route يستدعي service واحدًا فقط.
5. كل كتابة DB تُسجَّل في audit.
6. كل ملف ≤ 400 سطر.
7. كل وحدة جديدة تأتي مع: model + service + route + template + test.
