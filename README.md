# HobeRadius (standalone)

نظام RADIUS مستقل بواجهة عربية فاخرة و REST API كامل، يربط مباشرة بـ MikroTik.

> 📘 **للمرجع الكامل اقرأ `REFERENCE.md`** — كل ما تحتاجه (فلسفة، بنية، نماذج، API،
> Webhooks، MikroTik، خارطة الطريق، قواعد التطوير) موجود هناك في ملف واحد.

## البنية

```
radius-module/
├── wsgi.py
├── run.ps1
├── requirements.txt
├── README.md
└── app/
    ├── __init__.py              ← Flask app + stubs (CSRF/auth/arabize)
    ├── radius/                  ← الوحدة الأساسية (تنتقل كما هي إلى HobeHub)
    │   ├── core/, integration/, services/, routes/, templates/, docs/
    ├── templates/admin/
    │   └── _admin_layout.html   ← layout مستقل بنفس بلوكات HobeHub
    └── static/
        ├── css/admin_layout.css, dashboard_table.css
        └── js/dashboard_table.js
```

> الـ folder `app/radius/` متطابق 1:1 مع `HobeHub/app/radius/`.
> أي تغيير هنا = نسخ مباشر إلى HobeHub عند الدمج.

## التشغيل

```powershell
cd C:\Users\Ahmad J Ahmad\Desktop\hub\radius-module
pip install -r requirements.txt
.\run.ps1
```

ثم افتح http://127.0.0.1:5050/ — تُعاد التوجيهة لـ `/admin/radius/devices`.

### الـ URLs

| URL | ماذا |
|-----|------|
| `/`                                 | يُعيد التوجيه لـ devices |
| `/admin/radius/_health`             | JSON تأكيد bp |
| `/admin/radius/devices`             | قائمة NAS (manual storage) |
| `/admin/radius/devices/new`         | إضافة جهاز |
| `/admin/radius/devices/<id>/edit`   | تعديل |
| `/admin/radius/online`              | الجلسات المباشرة (manual=فارغة) |

## الفرق عن HobeHub

| الجانب | HobeHub | radius-module |
|--------|----------|---------------|
| CSRF | middleware في `12_csrf_context_hooks.py` | stub داخل `app/__init__.py` |
| Auth | `login_required` + `before_request` عام | لا توجد — مفتوح للتطوير |
| Layout | `templates/admin/_admin_layout.html` (HobeHub design) | layout مبسَّط بنفس البلوكات |
| Arabize | فلاتر حقيقية | no-op |

## كيف ندمج لاحقًا

1. انسخ `app/radius/` كاملًا فوق `HobeHub/app/radius/` (overwrite).
2. تأكد أن `legacy.py` فيه كتلة تسجيل الـ blueprint (سبق إضافتها).
3. **لا تنسخ** `app/__init__.py` ولا `templates/admin/_admin_layout.html`
   ولا `static/` — هذه خاصة بالـ standalone فقط.
4. تأكد من `RADIUS_MODE` في بيئة HobeHub (تبقى manual افتراضيًا).

## وضع الـ adapter

- `RADIUS_MODE=manual` (الافتراضي) → in-memory.
- `RADIUS_MODE=live` + `RADIUS_API_READY=1` → `ApiAdapter` يلفّ
  `app.services.radius_client` (متاح فقط داخل HobeHub — في الـ standalone
  سيُحاول الاستيراد ويفشل، لذا أبقِها `manual` هنا).
