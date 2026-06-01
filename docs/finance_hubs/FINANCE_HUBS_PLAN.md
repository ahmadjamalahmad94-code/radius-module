# خطة دمج قسم «المال والتحصيل» في 4 صفحات-محور (Finance Hubs Consolidation Plan)

> صفحة تنفيذية مكتفية ذاتيًا. الهدف: دمج 13 عنصر تحت «المال والتحصيل» في **4 صفحات-محور** (+ محور المخزون الموجود)، كل صفحة واحدة فيها hero بأزرار تبويب (pills)، جداول عرض لكل تبويب، وإجراءات الإضافة داخل `<dialog>` modals — تمامًا كصفحة **«مخزون ومصروفات الشركة»** المرجعية.
>
> **النمط المرجعي المؤكد من الكود:**
> - Route module: `app/radius/routes/company_inventory.py` — `register_company_inventory_routes(bp)` + `bp.add_url_rule(...)` + endpoint إعادة توجيه قديم `company_inventory_legacy_redirect`.
> - Template: `app/templates/radius/company_inventory_expenses.html` — `.cie-hero` + `.cie-tabs` pills (`<a href="?tab=..." class="{{ 'is-active' if tab=='...' }}">`) + `{% if tab=='x' %}` أقسام + `<dialog class="cie-modal" data-modal="...">` + كتلة JS لفتح/إغلاق الـ modal.
> - Registration: `app/radius/routes/blueprint.py` — استيراد + استدعاء `register_*_routes(bp)` داخل `_register_all`.
> - Sidebar: `app/templates/admin/_sidebar.html` — `{%- set m_* = ... -%}` ثم تجميع في `sec_billing_active` (سطر 215-217) ثم `sub_item('radius.<endpoint>', 'نص عربي', m_*)` داخل قسم `data-hb-section="billing"` (سطر 402-426).
> - Tests: `tests/test_company_inventory_expenses_web.py` — fixture بـ `tmp_path` + `HOBERADIUS_NO_WORKER/NO_SEED` + `reset_for_tests` + `run_pending_migrations`، و`_auth_session`، و`_post`، واختبار العزل المالي `_existing_financial_tables`/`_table_counts` (before==after).

---

## القواعد الصارمة (Global Rules — كل commit يلتزم بها)
1. **UI فقط**: تجميع routes + templates لا غير. **يُمنع** تعديل منطق ledger/payments/wallets/revenue/profit/loans/debts.
2. أعد استخدام نفس استدعاءات الـ service/repo بحرفها. لا SQL جديد.
3. **كل رابط قديم يبقى يعمل** عبر 301/302 إلى تبويب المحور. الـ POST endpoints القديمة تبقى كما هي (هدف النماذج).
4. **الإعدادات تبقى قابلة للوصول** (payment_collection_settings كـ modal، الحفظ POST سليم).
5. **request_detail تبقى مستقلة** وعملياتها approve/reject/apply-service سليمة.
6. **Exports + /users/<username>/finance تبقى مستقلة** (csv/xlsx/pdf/snapshot/void) — إعادة تنسيق فقط.
7. commit واحد منطقي لكل خطوة. **لا `git add .`** — أضف بالاسم. لا تلمس ملفات dirty غير ذات صلة.
8. كل النصوص الظاهرة بالعربية. عزل المستأجر `tenant_id` كما هو. `*_minor` يبقى أعدادًا صحيحة + فلتر `|money`.
9. **اختبارات لكل محور**: تطابق KPI + إعادة التوجيه + العزل المالي.
10. تحقّق (parse + url_map + pytest) ثم commit. لا push إلا بطلب المستخدم.

---

## ترتيب البناء الموصى به
1. **الفواتير والكوبونات** (الأنظف/الأعلى أثرًا — ملف واحد `saas_modules.py`، repos بسيطة، لا دمج معقّد).
2. **التحصيل والمدفوعات** (يُدخل نمط settings-modal + detail-standalone على وحدة معزولة).
3. **السجل والتقارير المحاسبية** (append-only ledger + exports + user-finance بارامتري).
4. **المركز المالي** (الأصعب: gates للصلاحيات + dashboard بـ 11 KPI + دمج debts/loans).

كل محور = ملف route جديد `*_hub.py` بقاعدة URL جديدة + قالب `*_hub.html` + تعديل سطر sidebar واحد + ملف اختبار. الـ handlers الأصلية والـ POST endpoints **لا تُحذف** — تبقى مسجّلة (تُستورد) كي تبقى أهداف النماذج والـ url_for تعمل.

---

## محور 1 — الفواتير والكوبونات
**ملف route جديد:** `app/radius/routes/billing_hub.py` · `_BASE='/billing-hub'` · `_TABS=('invoices','vouchers')`
**قالب:** `app/templates/radius/billing_hub.html`

### Endpoints المُمتصّة (المصدر: `app/radius/routes/saas_modules.py`)
| endpoint | url | methods | يصبح |
|---|---|---|---|
| radius.inv_list | /invoices | GET | tab=invoices |
| radius.inv_new | /invoices/new | GET | modal_form (invoice) |
| radius.inv_create | /invoices | POST | kept_standalone |
| radius.inv_status | /invoices/<int:iid>/status | POST | kept_standalone (inline form) |
| radius.vch_list | /vouchers | GET | tab=vouchers |
| radius.vch_generate | /vouchers/generate | GET,POST | modal_form (voucher) / POST kept |
| radius.vch_revoke | /vouchers/<int:vid>/revoke | POST | kept_standalone (inline form) |

### الحقول الدقيقة للنماذج
- **invoice (POST radius.inv_create):** `subscriber_id, plan_id, amount, direction, service_type, payment_method, status, expiration_at, note` — direction∈[charge,refund,deposit,withdraw,credit]; payment_method∈[cash,transfer,card,online,manual]; service_type∈[Hotspot(افتراضي),PPPoE,Balance]; status∈[paid,pending,failed,refunded,canceled].
- **voucher (POST radius.vch_generate):** `count, amount, plan_id, expire_at` (expire_at اختياري/فارغ=لا نهائي).
- **inv_status (POST):** `status, note`. **vch_revoke (POST):** بلا حقول (soft-delete status='revoked').
- CSRF hidden في كل نموذج: `<input type="hidden" name="_csrf_token" value="{{ csrf_token() if csrf_token is defined else '' }}">`.

### KPIs
عبر `invoices_repo.stats()` → total/paid/pending/count ، و`vouchers_repo.stats()` → active/used/revoked/total_amount.
الفلاتر: كلا الجدولين يدعم `?status=`. فلاتر الكوبونات [active,used,revoked,expired]، الفواتير [paid,pending,failed,refunded,canceled].

### service/repo calls (إعادة استخدام)
`invoices_repo.list_all/stats/create/update_status` · `vouchers_repo.list_all/stats/generate_bulk/revoke` · `plans_repo.list_plans/get_plan` · `subscribers_repo.list_subscribers`.

### خريطة إعادة التوجيه (301/302)
`/invoices→/billing-hub?tab=invoices` · `/invoices/new→/billing-hub?tab=invoices` · `/vouchers→/billing-hub?tab=vouchers` · `/vouchers/generate (GET)→/billing-hub?tab=vouchers`.

### sidebar
استبدل سطري `sub_item('radius.inv_list',...)` و`sub_item('radius.vch_list',...)` بسطر واحد `sub_item('radius.billing_hub','الفواتير والكوبونات', m_billing_hub or m_invoices or m_vouchers)`؛ أضف `m_billing_hub` إلى `sec_billing_active`.

### commits
- **F1.1** scaffold billing_hub.py (route+tabs+legacy redirects) + register في blueprint.py بعد register_saas_routes. *verify:* url_map.
- **F1.2** billing_hub.html (hero/pills/جدولين/modal invoice+voucher + نفس JS). *verify:* GET tabs 200 + dialog موجود.
- **F1.3** ربط context بـ stats/list. *verify:* KPI == stats().
- **F1.4** sidebar سطر واحد. *verify:* dashboard فيه /billing-hub.
- **F1.5** tests/test_billing_hub_web.py (parity+redirect+modal+isolation). *verify:* pytest + إعادة تشغيل test_api_saas_modules.py.

---

## محور 2 — التحصيل والمدفوعات
**ملف route جديد:** `app/radius/routes/payment_collection_hub.py` · `_BASE='/payment-collection-hub'` · `_TABS=('requests','review','reconciliation','settings')`
**قالب:** `app/templates/radius/payment_collection_hub.html` · المصدر: `app/radius/routes/payment_collection.py`

### Endpoints
| endpoint | url | methods | يصبح |
|---|---|---|---|
| radius.payment_collection_requests | /payments/requests | GET | hub_landing tab=requests |
| radius.payment_collection_review_queue_web | /payments/review-queue | GET | tab=review |
| radius.payment_collection_reconciliation_web | /payments/reconciliation | GET | tab=reconciliation |
| radius.payment_collection_settings | /payments/settings | GET,POST | GET→modal tab=settings ؛ POST يبقى هدف الحفظ |
| radius.payment_collection_request_detail | /payments/requests/<int:request_id> | GET | **kept_standalone** |
| radius.payment_collection_approve_web | .../approve | POST | kept_standalone (من صفحة التفاصيل) |
| radius.payment_collection_reject_web | .../reject | POST | kept_standalone |
| radius.payment_collection_apply_service_web | .../apply-service | POST | kept_standalone |

### حقول النماذج
- **settings (POST, admin_only):** `enabled, provider, currency, wallet_number, wallet_owner_name, confirmation_mode, payment_request_ttl_minutes, auto_apply, min_amount, max_amount, allow_cards, allow_monthly_subscriptions, allow_distributor_payments`.
- **approve/reject (POST, admin_only):** `review_note`. **apply-service (POST):** `simulate_failure`.

### KPIs (من قائمة الطلبات)
إجمالي الطلبات=`items|length` · بانتظار المراجعة=count(status∈proof_submitted,under_review) · معلقة=count(status=pending) · مدفوعة=count(status=paid).

### service calls
`PaymentRequestRepository.list/get/list_for_review/update_status` · `PaymentSettingsRepository.get/upsert` · `PaymentProofRepository.*` · `PaymentTransactionRepository.create` · `PaymentCollectionLedgerRepository.apply_paid_request` · `PaymentServiceApplyRepository.*` · `PaymentReconciliationRepository.summary`.

### إعادة التوجيه
`/payments/requests→hub?tab=requests` · `/payments/review-queue→hub?tab=review` · `/payments/reconciliation→hub?tab=reconciliation` · `/payments/settings (GET)→hub?tab=settings`.

### sidebar
استبدل الأربعة (settings/requests/review_queue/reconciliation) بسطر `sub_item('radius.payment_collection_hub','التحصيل والمدفوعات', m_payment_collection)`؛ أبقِ المطابقات المسارية. صفوف جدول requests تربط لصفحة التفاصيل المستقلة عبر `url_for('radius.payment_collection_request_detail', request_id=...)`.

### commits
- **F2.1** scaffold routes + legacy redirects (detail+POSTs تبقى مستقلة). *verify:* url_map (detail موجود).
- **F2.2** القالب (3 تبويبات + settings modal POST لـ settings). *verify:* tabs 200.
- **F2.3** ربط context بالـ repos. *verify:* counts == legacy.
- **F2.4** sidebar سطر واحد. *verify:* dashboard فيه الرابط.
- **F2.5** tests (parity+redirect+detail-standalone+settings-saves+isolation). *verify:* pytest + إعادة تشغيل test_payment_collection_web/_review/_api.

---

## محور 3 — السجل والتقارير المحاسبية
**ملف route جديد:** `app/radius/routes/finance_accounting.py` · `_BASE='/finance/accounting'` · `_TABS=('ledger','reports')`
**قالب:** `app/templates/radius/finance_accounting.html` · المصدر: `app/radius/routes/accounting.py`

### Endpoints
| endpoint | url | methods | يصبح |
|---|---|---|---|
| radius.finance_ledger | /finance/ledger | GET | redirect إلى `radius.accounting_hub` مع `tab=ledger` |
| radius.finance_ledger_void | /finance/ledger/void | POST | modal void (kept_standalone) |
| radius.finance_reports | /finance/reports | GET | redirect إلى `radius.accounting_hub` مع `tab=reports` |
| radius.finance_reports_export_csv/xlsx/pdf | /finance/reports/export.* | GET | **kept_standalone** (أزرار تصدير) |
| radius.finance_reports_snapshot | /finance/reports/snapshot | POST | modal snapshot (kept) |
| radius.users_finance | /users/<username>/finance | GET | **standalone** tab=finance (restyle فقط) |
| radius.users_payment_create/users_loan_create/users_loan_settle | /users/<username>/... | POST | kept_standalone (modals بصفحة المستخدم) |

### حقول النماذج
- **void:** `entry_id, reason`. **snapshot:** `report_type`.
- **users_payment_create:** `amount,currency,method,custom_price,discount_amount,discount_reason,rounding_mode,notes,apply_to_radius,dry_run`.
- **users_loan_create:** `hours,days,duration_minutes,amount,currency,reason,apply_to_radius,dry_run`.
- **users_loan_settle:** `amount,currency,method,settlement_type,notes`.

### KPIs (accounting_repo)
إجمالي الدفعات · عدد السلف المفتوحة · إجمالي السلف · دقائق التفعيل الممنوحة · الرصيد الصافي(profit_loss credits−debits) · عدد القيود.
فلاتر: entry_type∈[payment,loan,settlement,void,reversal,correction] · report_type∈[daily,monthly,yearly,subscriber_payments,loans,activations,card_sales,profit_loss,distributor_debts].

### gotcha حرجة
دفتر **append-only**: لا حذف. التصحيح عبر void/reversal (صف جديد). التقارير تحترم الانعكاسات — **نادِ نفس دوال repo** ولا تعد الحساب يدويًا. CSV يكتب UTF-8 BOM. snapshot يخزّن النتيجة JSON في `financial_report_snapshots`.

### إعادة التوجيه
`/finance/ledger` → `/finance/accounting?tab=ledger` · `/finance/reports` → `/finance/accounting?tab=reports`. (`/users/<username>/finance` يبقى على رابطه.)

### sidebar
استبدل `sub_item('radius.finance_ledger',...)` بـ `sub_item('radius.accounting_hub','السجل والتقارير المحاسبية', m_accounting_hub or m_ledger)`. أبقِ `radius.finance_reports` في قسم «التقارير» (رابط متقاطع — لا تحذفه).

### commits
- **F3.1** scaffold (exports+void+snapshot+user-finance تبقى مستقلة) + redirects. *verify:* url_map.
- **F3.2** القالب (ledger+reports، أزرار export، modals void/snapshot). *verify:* tabs 200 + روابط export سليمة.
- **F3.3** ربط context بـ accounting_repo. *verify:* أرقام == legacy.
- **F3.4** users_finance pills restyle + sidebar. *verify:* /users/.../finance 200.
- **F3.5** tests (parity+redirect+export content-types+void-appends-1+snapshot-row+isolation). *verify:* pytest + إعادة تشغيل test_web_accounting_ui/test_accounting_*.

---

## محور 4 — المركز المالي
**ملف route جديد:** `app/radius/routes/finance_center_hub.py` · `_BASE='/finance-center'` · `_TABS=('dashboard','wallets','revenue','loans_debts')`
**قالب:** `app/templates/radius/finance_center_hub.html` · المصدر: `app/radius/routes/finance_center.py`

### Endpoints
| endpoint | url | methods | يصبح |
|---|---|---|---|
| radius.business_finance | /finance | GET | hub_landing tab=dashboard |
| radius.business_finance_wallets | /finance/wallets | GET | tab=wallets |
| radius.business_finance_wallets_create | /finance/wallets | POST | kept_standalone (modal wallet) |
| radius.business_finance_wallet_credit | /finance/wallets/<id>/credit | POST | modal credit |
| radius.business_finance_wallet_debit | /finance/wallets/<id>/debit | POST | modal debit |
| radius.business_finance_revenue | /finance/revenue | GET | tab=revenue |
| radius.business_finance_debts | /finance/debts | GET | tab=loans_debts (status=open) |
| radius.business_finance_loans | /finance/loans | GET | tab=loans_debts |

### حقول النماذج + بوابات الصلاحية
- **wallet create (POST business_finance_wallets_create):** `_csrf_token, owner_type, owner_id, currency`.
- **credit (POST .../credit):** `_csrf_token, amount, notes` — gate `PERM_WALLET_CREDIT='wallet.credit'`.
- **debit (POST .../debit):** `_csrf_token, amount, notes` — gate `PERM_WALLET_DEBIT='wallet.debit'` + موافقة اختيارية إذا amount>1000.00 (max_wallet_debit=5000.00). `SafetyGateService` يرجع `gate.allowed` + `gate.requires_approval`. أظهر modal credit/debit فقط إذا `can_wallet_credit`/`can_wallet_debit`.
- **loans (status filter):** `status` (GET ?status=) ∈ ['',open,settled,voided].

### دمج debts+loans (gotcha)
كلاهما يقرأ `loan_entries`. **debts = WHERE status='open'** (مستحقات). **loans = يقبل ?status** (open/settled/voided). دمج في **تبويب واحد** بقائمة status منسدلة افتراضها **open**. أعمدة تغطي العرضين: id, username, amount, reason/duration_minutes, approval_status/status, starts_at, ends_at, created_at. الـ status form يرسل GET ?status= ويُبقى القيمة في القائمة.

### KPIs (11 — FinanceCenterService.dashboard)
إجمالي الإيرادات · التحصيلات · أرصدة المحافظ · الربح الصافي · عدد المحافظ · قيود دفتر الأستاذ · سجلات الإيراد · سلف مفتوحة · حصص الموزعين · إجمالي السلف المفتوحة · إجمالي دفتر الأستاذ.

### service calls (إعادة استخدام بحرفها)
`FinanceCenterService.dashboard/wallets/wallet_transactions/revenue/loans/debts` · `WalletService.create_wallet/credit/debit`. لا تلمس منطق credit/debit/create.

### إعادة التوجيه
`/finance→tab=dashboard` · `/finance/wallets→tab=wallets` · `/finance/revenue→tab=revenue` · `/finance/debts→tab=loans_debts&status=open` · `/finance/loans→tab=loans_debts`.

### sidebar
استبدل الخمسة (business_finance/wallets/revenue/debts/loans) بسطر `sub_item('radius.finance_center_hub','المركز المالي', m_finance_center_hub or m_business_finance or m_business_wallets or m_business_revenue or m_business_debts or m_business_loans)`.

### commits
- **F4.1** scaffold + redirects (credit/debit/create POST تبقى مستقلة بـ gates). *verify:* url_map.
- **F4.2** القالب (dashboard 11 KPI، wallets+modals، revenue، loans_debts مدموج بـ status). *verify:* 4 tabs 200 + debts=open.
- **F4.3** ربط context + إعادة حساب gates مثل _common_context. *verify:* dashboard == legacy /finance؛ status=open==debts.
- **F4.4** sidebar سطر واحد. *verify:* dashboard فيه /finance-center.
- **F4.5** tests (11 KPI parity + merge + wallet-gate preserved + approval>1000 + isolation). *verify:* pytest + إعادة تشغيل test_finance_center_web.py + full smoke.

---

## خطة اختبار التطابق (Parity) — لكل محور
اتبع `tests/test_company_inventory_expenses_web.py` حرفيًا (fixture: tmp_path DB + HOBERADIUS_NO_WORKER/NO_SEED + reset_for_tests + run_pending_migrations؛ `_auth_session`؛ `_post` يحقن `_csrf_token` + follow_redirects).
1. **KPI parity:** نادِ نفس دالة الـ service/repo (غير معدّلة = الأساس الثابت) داخل app_context وقارن الناتج؛ ثم تأكد أن الـ hub HTML يعرض نفس الأرقام (substring على قيمة `|money`).
2. **page↔hub equivalence:** seed ثابت → GET الرابط القديم (الحي عبر redirect) + تبويب المحور → الأرقام متطابقة. للدمج: `loans_debts?status=open`==legacy debts، `?status=''`==legacy loans.
3. **العزل المالي (regression مسمّى):** `_existing_financial_tables` على [ledger_entries, accounting_ledger_entries, distributor_ledger_entries, revenue_records, wallets, wallet_transactions, payment_requests, payment_transactions, payment_collection_transactions, financial_report_snapshots, subscribers]؛ snapshot قبل → GET كل تبويبات المحور (قراءة فقط) → snapshot بعد → assert after==before. للـ POSTs الكاتبة (credit/debit/void/snapshot/invoice) تأكّد أن دلتا الصفوف = ما ينتجه الـ endpoint الأصلي بالضبط (void يضيف صفًا واحدًا ولا يحذف).
4. **redirect tests:** كل old_url ∈ {301,302} وLocation ينتهي بهدف hub?tab=.
5. **gate/standalone preservation:** credit/debit تحترم PERM_* + بوابة amount>1000؛ settings POST يحفظ؛ request_detail قابل للوصول؛ exports content-types صحيحة (CSV UTF-8 BOM).
6. **green-gate قبل الـ commit:** بعد كل F#.5 أعد تشغيل السويتات القائمة للمنطقة (test_finance_center_web.py، test_web_accounting_ui.py، test_accounting_events_engine.py، test_payment_collection_web/_review/_api.py، test_api_saas_modules.py) واشترط نجاحها جميعًا.

---

## التقدير الزمني
~20 commit (5 لكل محور × 4). محور1≈نصف يوم، محور2≈نصف-يوم، محور3≈يوم، محور4≈يوم. الإجمالي ≈ 3 أيام عمل مركّزة بما فيها الاختبارات والتحقق.
