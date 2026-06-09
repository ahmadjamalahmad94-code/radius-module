# تقرير تدقيق UI/UX — قطاع المال والتحصيل والمحاسبة
**التاريخ:** 2026-06-06 · **المنهج:** 10 وكلاء خبراء بالتوازي (ملكية ملفات حصرية) + تحقّق مركزي مستقل (إعادة إطلاق بعد موت الجلسة الأصلية).

## خلاصة التحقق
| الفحص | النتيجة |
|---|---|
| تجميع بنية القوالب (Jinja parse) | **29/29 ✓** |
| رندر صفحات GET المستقل (test_client) | **21/21 ✓ (لا 500)** |
| اختبارات القطاع المتأثرة (pytest، كل ملف منفرداً) | **64/64 ✓** (center_hub 5 · accounting_hub 4 · billing 11 · collection 12 · payment_web 4 · accounting_ui 6 · arabic_i18n 17 · card_pricing 5 · pdf_exports 2) |
| py_compile لملفات `.py` الجديدة من الجلسة الميتة | **سليم ✓** |
| confirm()/alert()/prompt() متبقٍّ في ملفات القطاع المعدّلة | **صفر ✓** |
| تشغيل سيرفر | لم يُشغَّل (ممنوع) ✓ |

> فشل سابق خارج عملي: `tests/test_payment_collection_review.py` يفشل بـ`KeyError: 'data'` في مسار JSON API (`/api/v1/payments/requests`) — **يفشل على HEAD أيضاً** (أثبتُّه بـgit stash). طبقة API لا تُرندر قوالب، فلا علاقة له بتدقيق الواجهة.

## مراجعة تعديلات الجلسة الميتة
ثلاثة مسارات جديدة سليمة أنشأتها الجلسة الميتة وأبقيتُها وأكملتُ فوقها: `recharge_panel.py` (لوحة الشحن — قراءات JSON فقط، صفر إعادة تنفيذ مالي)، `payments_lab.py` (مختبر دفع تجريبي + `pay_demo`)، `admin_pricing.py` (أسعار العروض للمدراء) — مع خدمة `payment_providers/` وrepo+migration للـcheckouts. كلها مسجَّلة في الـblueprint وتُرندر بلا أخطاء.

---

## الإصلاحات حسب الصفحة (صفحة → مشكلة → الحالة)

### الصفحات الحيّة المُصلَّحة
| الصفحة | المشكلة | الحالة |
|---|---|---|
| finance_center_hub (المركز المالي) | CSS ميت بـmargin حرفي؛ hex خام في select الأزرق؛ حالة المحفظة خام إنجليزية | صُلح (حذف الميت + توكِنات الأزرق الدلالية + ماكرو wallet_status_label مع الخام في title) |
| finance_accounting (السجل والتقارير المحاسبية) | جداول القيود/التقارير/اللقطات بلا data-uds-export-title | صُلح (عناوين تصدير عربية للثلاثة) |
| finance_billing (الفواتير والكوبونات) | page-size=10؛ لا عنوان تصدير | صُلح (25 + «الفواتير»/«الكوبونات»؛ نظام data-modal محفوظ) |
| finance_collection (التحصيل والمدفوعات — Hub) | حالة/غرض/دافع خام إنجليزي في الفلتر والجدولين؛ margin حرفي؛ لا عنوان تصدير | صُلح (خرائط STATUS/PURPOSE/PAYER عربية + الخام في title + توكِن المسافة + عناوين تصدير) |
| payment_collection_request_detail | margin/radius حرفي؛ hex خام؛ enums (غرض/دافع/تطبيق خدمة) خام | صُلح (توكِنات + PURPOSE_AR/PAYER_AR مع الخام في title) |
| payment_collection_review_queue | عمود «الغرض» خام | صُلح (PURPOSE_AR + الخام في title) |
| **recharge_panel (لوحة الشحن)** | **9× alert() محظور**؛ تاريخ الإيصال لاتيني | **صُلح (تنبيه عائم نظامي toast بـrole=alert + توكِنات؛ تعريب التاريخ)** |
| payments_lab (مختبر الدفع) | page-size=15؛ حالة بلا الخام في title؛ مبلغ بخريطة رموز يدوية | صُلح (25 + title للخام + فلتر money الموحّد) |
| **admin_pricing (أسعار المدراء)** | **2× confirm() خام** (صف + داخل التعديل)؛ احتمال تكدّس نوافذ | **صُلح (نافذتا تأكيد fcw خطيرتان بـPOST فعلي + closeAll قبل الفتح + حذف النموذج المخفي اليتيم)** |
| card_pricing_batch | KPIs مال عارٍ؛ حالة خام (posted/settled)؛ لا عنوان تصدير | صُلح (money + خريطة حالة + «كروت الدفعة» + hub-table) |
| card_pricing | جدول السياسات بلا عنوان تصدير | صُلح («سياسات تسعير الباقات») |
| cards_recharge_list / cards_recharge_batch | جداول uds بلا عنوان تصدير | صُلح («حزم الشحن المسبق» / «بطاقات حزمة الشحن») |
| accounting_reports / accounting_ledger (يتيمتان) | hex خام #bfdbfe؛ رؤوس أعمدة إنجليزية خام؛ لا عنوان تصدير | صُلح احتياطاً (توكِن + col_labels عربية + money + عناوين تصدير) — تبقى يتيمة |
| users_finance (مالية المشترك) | — | كان مطابقاً بالكامل (لا تعديل) |

### إصلاح القائد المركزي
- **admin_pricing**: استبدلتُ `#dc2626` الخام (في أيقونتَي نافذتَي fcw) بـ`var(--hub-red,#dc2626)` — توحيد لنمط المتغيّر-مع-البديل المعتمد في الملف (الوكيل أدخل واحداً وكرّر السابق؛ بلا تغيير بصري).

---

## اكتشاف معماري مهم — قوالب يتيمة (مساراتها 302 → الـHubs)
الصفحة الحيّة لكل مجموعة هي الـHub المدمج؛ القوالب التالية لا تُرندَر إطلاقاً (المسار يعيد توجيهاً، ودوال `*_legacy_context` كود ميت غير مسجَّل):
- `finance_center` / `finance_revenue` / `finance_debts` / `finance_loans` / `finance_wallets` → `finance_center_hub`.
- `accounting_ledger` / `accounting_reports` → `accounting_hub` (finance_accounting).
- `invoices_list` / `invoices_form` → بوّابة الفوترة (finance_billing).
- `payment_collection_requests` / `_review_queue` / `_reconciliation` / `_settings` → `collection_hub` (finance_collection). الوحيد الحيّ: `payment_collection_request_detail`.

اختبارات التحصيل تمرّ لأنها `follow_redirects=True` فتصل للـHub الحيّ. **توصية تنظيف (تخص مالك .py):** حذف القوالب اليتيمة ودوال `*_legacy_context` الميتة من `accounting.py` و`finance_center.py`.

---

## معلّق — قرارات منتج/معماري (لم تُصلَّح عمداً)
1. **hex خام داخل كتل `<style>` المحلية لنوافذ fcw والطبقات الجمالية المكرّسة**: finance_center_hub/finance_accounting/finance_billing (نمط fcw منقول من المرجع)، شريط التجميد `.fc-freeze` (تدرّج أصفر/أزرق بلا توكِن)، الطبقة الجمالية في recharge_panel، هوية cyan/teal في cards_recharge_list/new/batch، `:root` المستقل في pay_demo (صفحة عميل موبايل-أولاً لا تحمّل CSS الإدارة)، `#f1edff` في finance_wallets اليتيم. **الحل:** إضافة توكِنات لونية (تنبيه/تجميد/cyan) في النظام ومركزة CSS نوافذ fcw — refactor واسع يخص مالك CSS.
2. **ماكرو `hub.kpi`** يطبع القيمة مع autoescape فلا يقبل `title`/HTML — يمنع إظهار enum الخام في tooltip داخل KPI. يحتاج معاملاً اختيارياً `raw/title` في `_partials/hub.html`.
3. **قوالب ودوال يتيمة** (انظر أعلاه) — تنظيف يخص مالك `.py`.
4. **قيمة `accounting_ledger_entries` الخام** في عمود «المصدر» بتقرير ربح/خسارة — يؤكّدها اختبار API كقيمة بيانات؛ تعريبها يخص طبقة `.py`.
5. **حقول `_csrf_token` يدوية مكرّرة** مع الحاقن التلقائي (admin_pricing/payments_lab/card_pricing) — غير ضارة (نفس القيمة)؛ تنظيف اختياري يخص مالك `.py`.
6. **`pay_demo`** صفحة دفع عميل قائمة بذاتها — ألوان `:root` محلية وعرض العملة كرمز ISO منفصل (قرار تصميم متعمّد للموبايل).

## ملاحظات نطاق
- بقيتُ ضمن قوالب القطاع حصرياً (15 ملفاً معدّلاً). لم يُلمَس أي `.py` ولا CSS/JS مشترك ولا `hub.html` ولا أي `docs_*`.
- كل أنظمة المودال المختلفة محفوظة: `data-modal` (الفوترة) · `data-ff-modal`/`sd.showModal` (التحصيل) · `fcw` (المركز/المحاسبة/الأسعار) — لم يُستبدل أيٌّ بآخر (تكسر الاختبارات).
- سكربت التحقق: `_design_review/_verify_finance.py`.
