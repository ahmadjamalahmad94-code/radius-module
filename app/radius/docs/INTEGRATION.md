# خطة دمج وحدة RADIUS داخل HobeHub

> الهدف: تشغيل الوحدة الجديدة **بدون كسر أي route حالي** ودون migrations فورية.

---

## مبدأ التدرّج

نتبع مراحل صغيرة، كل مرحلة يجب أن تترك المشروع شغّالًا:

```
M0  ✅  حجر الأساس (هذه المرحلة)
M1     تسجيل blueprint فارغ + healthcheck داخلي
M2     manual_adapter (in-memory) + page واحدة قراءة فقط
M3     api_adapter يلفّ app/services/radius_client/ الحالي
M4     migrations (override-style) للجداول الجديدة
M5     ربط الـ accounts module بالـ beneficiaries
M6     نقل تدريجي لشاشات RADIUS من legacy_parts
```

كل مرحلة = PR/commit مستقل، قابل للمراجعة، قابل للتراجع.

---

## M0 — ما أُنجز الآن

- مجلد `app/radius/` بكامل البنية.
- `core/` (types, constants, errors) — بدون I/O.
- `integration/adapter.py` — ABC + factory.
- `routes/blueprint.py` — factory فقط، **غير مسجَّل بعد**.
- وثائق: MODULE_MAP, SCHEMAS, INTEGRATION.

**أثر على المشروع:** صفر. لا استيرادات من `legacy.py`، لا تغييرات على routes، لا migrations.

تحقّق:
```bash
python -c "from app.radius import RadiusAdapter; print(RadiusAdapter)"
python -m py_compile app/radius/integration/adapter.py app/radius/core/*.py
```

---

## M1 — تسجيل الـ Blueprint (الخطوة التالية المقترحة)

### التعديل المطلوب على `app/legacy.py`
نضيف بعد تحميل `_LEGACY_PARTS` (في نهاية الـ entrypoint):

```python
# RADIUS module (modular boundary — لا يدخل _LEGACY_PARTS)
try:
    from app.radius.routes import get_radius_blueprint
    if "radius" not in app.blueprints:
        app.register_blueprint(get_radius_blueprint())
except Exception:  # noqa: BLE001
    app.logger.exception("radius blueprint registration failed (non-fatal)")
```

**لماذا try/except؟** لأن M0 لا تضمن وجود dependencies تشغيل (مثلًا أي drift في Flask). الوحدة اختيارية حتى تنضج.

### اختبار قبول M1
- `flask routes | grep radius` → يُظهر 0 endpoints (الـ bp فارغ — متوقع).
- جميع الـ routes الحالية تعمل كما هي.

---

## M2 — manual_adapter + قراءة فقط

ملف جديد `integration/manual_adapter.py`:
- يخزن البيانات in-memory (أو SQLite صغير في `instance/radius_manual.db`).
- لا اتصال خارجي.
- يفيدنا للتطوير المحلي قبل ربط الـ API.

route قراءة واحد: `/admin/radius/devices` يعرض جدول NAS فارغ ابتدائيًا، يلتزم بمعيار جداول HobeHub (راجع §1 من CLAUDE.md).

---

## M3 — api_adapter (يلفّ الحالي)

`integration/api_adapter.py` يستخدم:
- `app.services.radius_client.RadiusClient` (الموجود)
- `app.services.radius_dashboard` (cache layer — كما هو)

نقاط حذر:
- `RADIUS_MODE=manual` → نختار manual adapter.
- `RADIUS_MODE=live` + `RADIUS_API_READY=1` → api adapter قراءة.
- `RADIUS_API_WRITES_ENABLED=1` → نسمح بـ writes.

(هذا يلتزم بالقاعدة §4 من CLAUDE.md حرفيًا.)

---

## M4 — Migrations

نمط override (CLAUDE.md §5):
- `app/legacy_parts/16_sqlite_schema_02b_radius_module.py`
- `app/legacy_parts/17_postgres_schema_setup_03b_radius_module.py`

يُضافان قبل `..._05_entrypoint.py` في قائمة `_LEGACY_PARTS`.
كل ملف يُنشئ جداوله بـ `CREATE TABLE IF NOT EXISTS` فقط — لا تعديل على الجداول الموجودة.

---

## M5 — ربط beneficiaries

`RadiusAccountsService.create(..., beneficiary_id=...)` يكتب FK.
لا تعديل على جدول beneficiaries نفسه — FK يُضاف على `radius_accounts` فقط.

---

## M6 — هجرة الشاشات

نقل تدريجي للـ legacy_parts المتعلقة بـ RADIUS عبر نمط override (CLAUDE.md §5)، ملف ملف.

---

## قواعد ثابتة طوال الدمج

1. **لا git add .** — كل التزام يحدد ملفاته بالاسم.
2. **لا تعديل عشوائي** لـ services أو templates خارج `app/radius/`.
3. **لا ملف > 400 سطر** داخل الوحدة.
4. **لا business logic** في templates.
5. **لا استيراد دائري** بين `app/radius/` و `app/legacy_parts/`.
6. الـ blueprint **لا يُسجَّل** قبل M1 — حتى ولو الاستيراد آمن.

---

## مرجع التحقق الذاتي (قبل أي merge)

```bash
# 1) لا كسر استيرادات
python -c "import app; print('ok')"

# 2) لا ملف يتجاوز 400 سطر
find app/radius -name "*.py" -exec wc -l {} + | awk '$1>400 {print}'

# 3) لا استيرادات legacy من داخل radius
grep -RIn "from app.legacy" app/radius/ && echo "VIOLATION" || echo "clean"
grep -RIn "from app.legacy_parts" app/radius/ && echo "VIOLATION" || echo "clean"
```
