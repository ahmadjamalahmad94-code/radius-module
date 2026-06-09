# خطة دمج قسم التشغيل والمخاطر والتقارير والإدارة في محاور موحدة
# (Engagement, Reports & Administration Hubs Consolidation Plan)

> صفحة تنفيذية مكتفية ذاتيًا. الهدف: دمج **28 صفحة منفصلة** من ثلاثة أقسام رئيسية (التشغيل والمخاطر، التقارير، الإدارة) في **10 صفحات-محور موحدة**، كل صفحة فيها hero بأزرار تبويب (pills)، جداول عرض لكل تبويب، و modals للإجراءات — تمامًا كنمط **«الفواتير والكوبونات»** والمراجع الموجودة (finance_center_hub + company_inventory).

---

## القواعس الصارمة (Global Rules — كل commit يلتزم بها)

1. **UI فقط**: تجميع routes + templates لا غير. **يُمنع** تعديل منطق المراسلات (comms) أو الأحداث/المخاطر (events) أو العمليات (operations) أو التقارير أو الصلاحيات.
2. أعد استخدام **نفس استدعاءات الـ service/repo بحرفها**. لا SQL جديد، لا تعديل queries.
3. **كل رابط قديم يبقى يعمل** عبر 301/302 إلى تبويب المحور. الـ POST endpoints القديمة تبقى كما هي (أهداف النماذج).
4. **الصفحات المستقلة تبقى مستقلة:** تفاصيل الحدث `/events/<id>`، تفاصيل الطلب `/payments/requests/<id>`، صفحات العمليات الحساسة (restore، lifecycle)، تقارير مستقلة إن وجدت.
5. **Web hooks عامة تبقى مستقلة:** `/communications/bot/webhook` (POST عام، موقّع)، `/communications/channels/test` (POST → JSON).
6. **Standalone endpoints تبقى مستقلة:** تصدير (CSV/XLSX/PDF)، إرسال يدوي، حملات، جمهور، توزيع، إعادة حسابات مركبة.
7. **commit واحد منطقي لكل خطوة.** لا `git add .` — أضف بالاسم. لا تلمس ملفات dirty غير ذات صلة.
8. **كل النصوص الظاهرة بالعربية.** عزل المستأجر `tenant_id` كما هو. لا غيّر CSRF/tokens/معالجات.
9. **اختبارات لكل محور**: تطابق KPI + إعادة التوجيه + العزل (cross-tenant).
10. تحقّق (parse + url_map + pytest) ثم commit. **لا push إلا بطلب المستخدم.**

---

## الروابط المتقاطعة — لا تكرار

- `radius.finance_reports` (من accounting_hub) يظل **في قسم التقارير sidebar** كرابط متقاطع — **لا تدمجه** في أي محور جديد من التقارير.
- `radius.whatsapp` (WhatsApp subscriber gates) يبقى **fully standalone** — لا دخول في أي محور.
- `radius.settings_page`، `radius.sync_list`، `radius.tenants_list` (admin super) تبقى **خارج scope** (تحديث sidebar فقط).

---

## ترتيب البناء الموصى به (Recommended Build Order)

### المرحلة 1: قسم التشغيل والمخاطر (3 محاور = ~30 صفحة منفصلة)
1. **E1: مركز التواصل والإرسال** (6 تبويبات × 6 صفحات) — الأنظف: `comms_providers` + `comms_bot` + `notifications_engine` بسيطة.
2. **E2: مركز الأحداث والمخاطر والتحقيقات** (4 تبويبات) — معقدة: جداول كبيرة + run_risk (dry-run).
3. **E3: مركز العمليات** (2 تبويب) — صغير: speed_control (safe preview).

### المرحلة 2: قسم التقارير (5 محاور = ~21 صفحة منفصلة)
4. **R1: التقارير التنفيذية والأساسية** (4 تبويبات) — مركزي: summary + executive.
5. **R2: مركز تسجيل الدخول والأمان** (4 تبويبات) — بسيط: login_events.
6. **R3: مركز الشبكة والجلسات** (4 تبويبات) — شامل: radacct + audit_log.
7. **R4: مركز الأحداث والنشاط** (4 تبويبات) — append-only: audit_log.
8. **R5: مركز المالية والموازنات** (3 تبويبات) — صغير: cards + ledger.

### المرحلة 3: قسم الإدارة (3 محاور = ~12 صفحة)
9. **A1: مشغلو الأعمال والموزعون** (2 تبويب + modals) — متوسط: lists + CRUD.
10. **A2: الأدوار والصلاحيات** (2 تبويب) — بسيط: قراءة + matrix.
11. **A3: البيانات والحفظ والأرشفة** (3 تبويبات + high-gate modals) — حساس جداً: restore + lifecycle.

**التقدير الزمني:** ~40–50 commit عبر الثلاث مراحل. الإجمالي ≈ 4–5 أيام عمل مركّزة + اختبارات.

---

# القسم 1: التشغيل والمخاطر (ENGAGEMENT HUBS)

## نظرة عامة

| الصفحات الحالية | → المحور الجديد | base_url | التبويبات |
|---|---|---|---|
| communications (dashboard) + channels + bot + notifications + quota + templates | E1: مركز التواصل | `/communications-center` | 6 تبويبات |
| events_center + events_risk + events_security + events_investigations | E2: مركز الأحداث والمخاطر | `/events-center` | 4 تبويبات |
| operations_center + operations_speed_control | E3: مركز العمليات | `/operations-center` | 2 تبويب |

---

## محور E1 — مركز التواصل والإرسال (Communications Center Hub)

**ملف route جديد:** `app/radius/routes/communications_center_hub.py` · `_BASE='/communications-center'` · `_TABS=('dashboard','channels','bot','notifications','quota','templates')`
**قالب:** `app/templates/radius/communications_center_hub.html`
**المصدر:** `app/radius/routes/communications.py` (6 صفحات مستقلة)

### Endpoints المُمتصّة

| endpoint | URL | methods | يصبح | ملاحظات |
|---|---|---|---|---|
| radius.communications | /communications | GET | hub_landing tab=dashboard | live status aggregation |
| radius.communications_channels | /communications/channels | GET | tab=channels | HTTP SMS/WhatsApp/Telegram config |
| radius.communications_bot | /communications/bot | GET | tab=bot | WhatsApp self-service |
| radius.communications_notifications | /communications/notifications | GET | tab=notifications | event triggers |
| radius.communications_quota | /communications/quota | GET | tab=quota | balance + ledger |
| radius.communications_templates | /communications/templates | GET | tab=templates | template CRUD |

### Endpoints المُحفوظة (kept_standalone)

```
POST /communications/channels → form target (add/edit channel)
POST /communications/channels/test → JSON response (test endpoint)
POST /communications/bot → form target (configure bot)
POST /communications/notifications → form target (toggle per-event)
POST /communications/quota/request → form target (AdminPanelClient bridge)
POST /communications/quota/credit → form target (manual credit)
POST /communications/templates → form target (create/update template)
GET+POST /communications/send → standalone (manual send + preview)
GET+POST /communications/campaigns → standalone (campaign editor + dry-run)
GET+POST /communications/audience → standalone (segment manager)
GET /communications/deliveries → standalone or hub tab (read-only log)
GET /communications/guide → standalone or hub tab (read-only setup guide)
POST /communications/bot/webhook → public, signed (inbound webhook)
```

### حقول النماذج (Modal Forms)

- **channel:** `name, type (sms|whatsapp|telegram), api_key, account_id, enabled`
- **bot config:** `enabled, webhook_url, allowed_commands (comma-separated)`
- **notification toggle:** `event_key, enabled, channel_list`
- **quota/credit:** `amount, notes`
- **template:** `name, body (text), variables (JSON)`
- **CSRF:** كل نموذج يحمل `<input type="hidden" name="_csrf_token">`

### KPIs (Dashboard)

عبر `CommsProviders` + `CommsBot` + `NotificationCampaignService`:

- **Channels active:** count of {sms, whatsapp, telegram} where enabled=1
- **WhatsApp bot:** enabled flag + active command count
- **Event notifications:** count of enabled rules
- **Deliveries:** sent/failed/queued counts (من delivery log)
- **Quota balance:** current balance per channel
- **Templates:** count of active templates
- **Latest deliveries:** mini-table (last 8)

### service/repo calls (إعادة استخدام)

`CommsProviders.HTTP_CHANNELS/get_channel/create_channel/test_channel` · `CommsBot.load_bot_config/save_config` · `notifications_engine.load_rules/toggle_rule` · `CommsQuota.quota_status/request_quota` · `NotificationCampaignService.list_templates/create_template/list_deliveries/dashboard`

### خريطة إعادة التوجيه (301/302)

```
/communications → /communications-center?tab=dashboard
/communications/channels → /communications-center?tab=channels
/communications/bot → /communications-center?tab=bot
/communications/notifications → /communications-center?tab=notifications
/communications/quota → /communications-center?tab=quota
/communications/templates → /communications-center?tab=templates
```

### sidebar

استبدل 6 أسطر (`radius.communications`, `radius.communications_channels`, إلخ) بسطر واحد:
```jinja2
{%- set m_communications_center_hub = m_communications or m_communications_channels or ... -%}
sub_item('radius.communications_center_hub', 'التواصل والإرسال — المركز الموحّد', m_communications_center_hub)
```
أضف `m_communications_center_hub` إلى `sec_engagement_active`.

### commits

- **E1.1** scaffold communications_center_hub.py (route + _TABS + legacy 301/302 handlers) + register في blueprint.py. *verify:* `flask routes | grep communications-center`.
- **E1.2** communications_center_hub.html (hero/pills/6 sections/KPI grid/modals). *verify:* GET all tabs 200; dialog.showModal() works.
- **E1.3** ربط context بـ `_svc()` helpers + populate KPI vars. *verify:* KPI values match legacy `/communications` GET.
- **E1.4** sidebar سطر واحد + `sec_engagement_active` update. *verify:* dashboard sidebar shows new hub link.
- **E1.5** tests/test_communications_center_hub_web.py (parity KPI + redirect 301 + POST form targets work + isolation cross-tenant). *verify:* pytest + re-run test_communications_web.py.

---

## محور E2 — مركز الأحداث والمخاطر والتحقيقات (Events & Risk Center Hub)

**ملف route جديد:** `app/radius/routes/events_risk_center_hub.py` · `_BASE='/events-center'` · `_TABS=('dashboard','risk','security','investigations')`
**قالب:** `app/templates/radius/events_risk_center_hub.html`
**المصدر:** `app/radius/routes/events_risk.py` (4 صفحات)

### Endpoints المُمتصّة

| endpoint | URL | methods | يصبح |
|---|---|---|---|
| radius.events_center | /events | GET | hub_landing tab=dashboard |
| radius.events_risk | /events/risk | GET | tab=risk |
| radius.events_security | /events/security | GET | tab=security |
| radius.events_investigations | /events/investigations | GET | tab=investigations |

### Endpoints المُحفوظة (kept_standalone)

```
GET /events/<int:event_id> → detail page (timeline, full context) — لا يُدمج أبداً
POST /events/risk → dry-run risk rules (creates fraud flags, safe)
POST /events/investigations → create investigation form
```

### حقول النماذج

- **run_risk (POST /events/risk):** بلا حقول (dry-run في preview modal)
- **investigation:** `title, description, assigned_to, priority, tags`

### KPIs (EventsRiskCenterService)

- **Total events:** count من business_events
- **Open fraud flags:** count where status='open' (من fraud_flags)
- **Critical events:** count where severity='critical'
- **Security events:** last 200 filtered by category='security'
- **Open investigations:** count where status='open'

### service/repo calls (إعادة استخدام)

`EventsRiskCenterService.dashboard/list_events/list_fraud_flags/run_risk_rules` · `EventsRiskCenterService.list_investigations/create_investigation`

### خريطة إعادة التوجيه

```
/events → /events-center?tab=dashboard
/events/risk → /events-center?tab=risk
/events/security → /events-center?tab=security
/events/investigations → /events-center?tab=investigations
```

### sidebar

استبدل 4 أسطر بسطر واحد + `sec_engagement_active`.

### commits

- **E2.1** scaffold events_risk_center_hub.py + legacy 301 redirects. *verify:* url_map.
- **E2.2** events_risk_center_hub.html (4 pills + risk table with POST button + investigations form modal). *verify:* tabs 200.
- **E2.3** ربط context + verify `/events/<id>` detail page untouched. *verify:* /events/123 still works.
- **E2.4** sidebar + sec_engagement_active. *verify:* dashboard hub link visible.
- **E2.5** tests (4 tabs parity + POST run_risk appends flags + isolation). *verify:* pytest + re-run test_events_risk_*.

---

## محور E3 — مركز العمليات (Operations Center Hub)

**ملف route جديد:** `app/radius/routes/operations_center_hub.py` · `_BASE='/operations-center'` · `_TABS=('dashboard','speed-control')`
**قالب:** `app/templates/radius/operations_center_hub.html`
**المصدر:** `app/radius/routes/operations.py` (2 صفحات)

### Endpoints المُمتصّة

| endpoint | URL | methods | يصبح |
|---|---|---|---|
| radius.operations_center | /operations | GET | hub_landing tab=dashboard |
| radius.operations_speed_control | /operations/speed-control | GET | tab=speed-control |

### Endpoints المُحفوظة (kept_standalone)

```
POST /operations/speed-control → save speed policy (dry-run safe, no CoA execution)
```

### حقول النماذج

- **speed_preview (POST ?dry-run):** `profile_id, preset (json)` → returns impact dict (no save)
- **save_policy (POST save_policy=1):** `profile_id, preset (json)` → save to temp, no execute

### KPIs (OperationsSpeedCenterService)

- **Connected users:** من operations_snapshot
- **NAS health status:** من operations_snapshot
- **Accounting failures:** من operations_snapshot
- **Active sessions:** من operations_snapshot

### service/repo calls (إعادة استخدام)

`OperationsSpeedCenterService.operations_snapshot/list_policies/SPEED_PRESETS`

### خريطة إعادة التوجيه

```
/operations → /operations-center?tab=dashboard
/operations/speed-control → /operations-center?tab=speed-control
```

### sidebar

استبدل 2 أسطر بسطر واحد + `sec_engagement_active`.

### commits

- **E3.1** scaffold + legacy 301 redirects. *verify:* url_map.
- **E3.2** operations_center_hub.html (2 pills + speed modal safe dry-run). *verify:* tabs 200.
- **E3.3** ربط context + verify no CoA execution in preview. *verify:* POST dry-run returns dict, no DB changes.
- **E3.4** sidebar. *verify:* dashboard hub link.
- **E3.5** tests (2 tabs parity + dry-run safety + isolation). *verify:* pytest.

---

# القسم 2: التقارير (REPORTS HUBS)

## نظرة عامة

| الصفحات الحالية | → المحور الجديد | base_url | التبويبات |
|---|---|---|---|
| reports_home + reports_financial + reports_cards + reports_distributors | R1: التقارير التنفيذية | `/reports-hub` | 4 تبويبات |
| rep_login_states + rep_failed_logins + rep_login_status + rep_manager_login_status | R2: مركز تسجيل الدخول | `/reports/logins-security` | 4 تبويبات |
| rep_sessions + rep_mac_history + rep_coa_failures + rep_speed_failures | R3: مركز الشبكة | `/reports/network-sessions` | 4 تبويبات |
| rep_manager_events + rep_user_events + rep_profile_changes + rep_api_messages | R4: مركز الأحداث والنشاط | `/reports/activity-events` | 4 تبويبات |
| rep_used_cards + rep_cash_transactions + rep_balance_movements | R5: مركز المالية | `/reports/finance-ledger` | 3 تبويبات |

### ملاحظات حرجة على التقارير

1. **Read-only تماماً:** جميع endpoints محاور التقارير هي GET فقط. لا POSTs كاتبة.
2. **Filters preservation:** كل tab يحتفظ بـ `?q`, `?date_from`, `?date_to` + فلاتر خاصة (status, account_type).
3. **audit_log عمود نقدي:** R4 يستخدم `_audit_rows()` مع base_where مختلفة. الحفاظ على معايير البحث بالضبط.
4. **Pagination:** كل جدول له limit محدد (200–500). عدم التعديل يحافظ على الأداء.
5. **DashboardReportsService:** R1 يستخدم dashboard + summary + catalog. إعادة استخدام بحرفها.
6. **Cross-link:** `radius.finance_reports` يبقى في قسم التقارير sidebar — **لا تدمجه** في R5 أو أي محور.

---

## محور R1 — التقارير التنفيذية والأساسية (Executive Reports Hub)

**ملف route جديد:** `app/radius/routes/reports_executive_hub.py` · `_BASE='/reports-hub'` · `_TABS=('executive','financial','cards','distributors')`
**قالب:** `app/templates/radius/reports_executive_hub.html`

### Endpoints المُمتصّة

| endpoint | URL | methods | يصبح |
|---|---|---|---|
| radius.reports_home | /reports | GET | hub_landing tab=executive |
| radius.reports_financial | /reports/financial | GET | tab=financial |
| radius.reports_cards | /reports/cards | GET | tab=cards |
| radius.reports_distributors | /reports/distributors | GET | tab=distributors |

### Endpoints المُحفوظة (kept_standalone)

```
GET /reports/summary.json → service endpoint (JSON API)
GET /reports/archive → standalone archive page
POST /reports/archive/create → form target (create snapshot)
```

### KPIs

عبر `DashboardReportsService.summary`:

- إجمالي المشتركون
- المتصلون الآن
- إجمالي الإيرادات
- عدد البطاقات
- المشتركون النشطون
- البطاقات غير المستخدمة

### service/repo calls

`DashboardReportsService.dashboard/summary/report_catalog/report_data` · `ReportsRepository.list_*`

### خريطة إعادة التوجيه

```
/reports → /reports-hub?tab=executive
/reports/financial → /reports-hub?tab=financial
/reports/cards → /reports-hub?tab=cards
/reports/distributors → /reports-hub?tab=distributors
```

### commits

- **R1.1** scaffold + legacy 301 redirects. *verify:* url_map.
- **R1.2** reports_executive_hub.html (4 pills + KPI grid). *verify:* tabs 200.
- **R1.3** ربط context + KPI parity. *verify:* GET summary.json == dashboard values.
- **R1.4** sidebar + sec_reports_active. *verify:* hub link visible.
- **R1.5** tests (4 tabs parity + redirect + isolation). *verify:* pytest.

---

## محور R2 — مركز تسجيل الدخول والأمان (Logins & Security Center)

**ملف route جديد:** `app/radius/routes/reports_logins_hub.py` · `_BASE='/reports/logins-security'` · `_TABS=('login_states','failed_logins','subscriber_status','manager_status')`
**قالب:** `app/templates/radius/reports_logins_hub.html`

### Endpoints المُمتصّة

| endpoint | URL | methods | يصبح |
|---|---|---|---|
| radius.rep_login_states | /reports/login_states | GET | tab=login_states |
| radius.rep_failed_logins | /reports/failed_logins | GET | tab=failed_logins |
| radius.rep_login_status | /reports/login_status | GET | tab=subscriber_status |
| radius.rep_manager_login_status | /reports/manager_login_status | GET | tab=manager_status |

### KPIs

- إجمالي أحداث الدخول
- نسبة النجاح
- المصادر النشطة (panel, portal, RADIUS)
- المشتركون المفعلون
- المدراء النشطون

### service/repo calls

`fetch_login_events` · `LoginEventsRepository.*` · `admin_repo.list_admins`

### خريطة إعادة التوجيه

```
/reports/login_states → /reports/logins-security?tab=login_states
/reports/failed_logins → /reports/logins-security?tab=failed_logins
/reports/login_status → /reports/logins-security?tab=subscriber_status
/reports/manager_login_status → /reports/logins-security?tab=manager_status
```

### commits

- **R2.1** scaffold + legacy redirects. *verify:* url_map.
- **R2.2** reports_logins_hub.html (4 tabs + filters). *verify:* tabs 200.
- **R2.3** ربط context + filters preservation. *verify:* GET ?q=... filters work.
- **R2.4** sidebar. *verify:* hub link.
- **R2.5** tests (parity + filters + isolation). *verify:* pytest.

---

## محور R3 — مركز الشبكة والجلسات (Network & Sessions Center)

**ملف route جديد:** `app/radius/routes/reports_network_hub.py` · `_BASE='/reports/network-sessions'` · `_TABS=('sessions','mac_history','coa_failures','speed_failures')`
**قالب:** `app/templates/radius/reports_network_hub.html`

### Endpoints المُمتصّة

| endpoint | URL | methods | يصبح |
|---|---|---|---|
| radius.rep_sessions | /reports/sessions | GET | tab=sessions |
| radius.rep_mac_history | /reports/mac_history | GET | tab=mac_history |
| radius.rep_coa_failures | /reports/coa_failures | GET | tab=coa_failures |
| radius.rep_speed_failures | /reports/speed_failures | GET | tab=speed_failures |

### KPIs (Aggregate)

- إجمالي الجلسات
- عدد العناوين الفريدة (COUNT DISTINCT MAC)
- فشل CoA المتراكم
- فشل تحديث السرعة
- متوسط مدة الجلسة

### service/repo calls

Raw SQL (radacct, sync_queue, audit_log queries) مع الحفاظ على الفلاتر والـ limits.

### خريطة إعادة التوجيه

```
/reports/sessions → /reports/network-sessions?tab=sessions
/reports/mac_history → /reports/network-sessions?tab=mac_history
/reports/coa_failures → /reports/network-sessions?tab=coa_failures
/reports/speed_failures → /reports/network-sessions?tab=speed_failures
```

### commits

- **R3.1** scaffold + redirects. *verify:* url_map.
- **R3.2** reports_network_hub.html (4 tabs + queries). *verify:* tabs 200.
- **R3.3** ربط context + query parity. *verify:* aggregate counts == legacy.
- **R3.4** sidebar. *verify:* hub link.
- **R3.5** tests (parity + filters + isolation). *verify:* pytest.

---

## محور R4 — مركز الأحداث والنشاط (Activity & Events Center)

**ملف route جديد:** `app/radius/routes/reports_activity_hub.py` · `_BASE='/reports/activity-events'` · `_TABS=('manager_events','user_events','profile_changes','api_messages')`
**قالب:** `app/templates/radius/reports_activity_hub.html`

### Endpoints المُمتصّة

| endpoint | URL | methods | يصبح |
|---|---|---|---|
| radius.rep_manager_events | /reports/manager_events | GET | tab=manager_events |
| radius.rep_user_events | /reports/user_events | GET | tab=user_events |
| radius.rep_profile_changes | /reports/profile_changes | GET | tab=profile_changes |
| radius.rep_api_messages | /reports/api_messages | GET | tab=api_messages |

### KPIs (audit_log aggregate)

- إجمالي أحداث المدراء
- عدد المشتركين المتأثرين
- تحديثات الخطط
- طلبات API المعالجة
- آخر نشاط إداري

### service/repo calls

`_audit_rows(base_where, q_cols, limit)` مع base_where مختلفة per tab:
- manager_events: `actor LIKE 'admin:%'`
- user_events: `entity_type = 'subscriber'`
- profile_changes: `action = 'update' AND entity_type = 'profile'`
- api_messages: `actor LIKE 'api-token%'`

### خريطة إعادة التوجيه

```
/reports/manager_events → /reports/activity-events?tab=manager_events
/reports/user_events → /reports/activity-events?tab=user_events
/reports/profile_changes → /reports/activity-events?tab=profile_changes
/reports/api_messages → /reports/activity-events?tab=api_messages
```

### commits

- **R4.1** scaffold + redirects. *verify:* url_map.
- **R4.2** reports_activity_hub.html (4 tabs + _audit_rows decorators). *verify:* tabs 200.
- **R4.3** ربط context + _decorate_audit_rows preservation. *verify:* counts == legacy.
- **R4.4** sidebar. *verify:* hub link.
- **R4.5** tests (parity + base_where filters + isolation). *verify:* pytest.

---

## محور R5 — مركز المالية والموازنات (Finance & Ledger Center)

**ملف route جديد:** `app/radius/routes/reports_finance_hub.py` · `_BASE='/reports/finance-ledger'` · `_TABS=('used_cards','cash_transactions','balance_movements')`
**قالب:** `app/templates/radius/reports_finance_hub.html`

### Endpoints المُمتصّة

| endpoint | URL | methods | يصبح |
|---|---|---|---|
| radius.rep_used_cards | /reports/used_cards | GET | tab=used_cards |
| radius.rep_cash_transactions | /reports/cash_transactions | GET | tab=cash_transactions |
| radius.rep_balance_movements | /reports/balance_movements | GET | tab=balance_movements |

### KPIs (Aggregate)

- البطاقات المستخدمة
- إجمالي معاملات الكاش
- الخصومات المعطاة
- حركات الرصيد العامة
- حركات رصيد الموزعين

### service/repo calls

Raw SQL (cards, payment_transactions, accounting_ledger_entries queries) بـ SUM/COUNT.

### خريطة إعادة التوجيه

```
/reports/used_cards → /reports/finance-ledger?tab=used_cards
/reports/cash_transactions → /reports/finance-ledger?tab=cash_transactions
/reports/balance_movements → /reports/finance-ledger?tab=balance_movements
```

### sidebar

سطر واحد + sec_reports_active.

### commits

- **R5.1** scaffold + redirects. *verify:* url_map.
- **R5.2** reports_finance_hub.html (3 tabs + ledger tables). *verify:* tabs 200.
- **R5.3** ربط context + aggregate queries. *verify:* SUM/COUNT == legacy.
- **R5.4** sidebar + **تحقق من sec_reports_active شامل** + أرشيف مستقل. *verify:* hub link + archive link separate.
- **R5.5** tests (parity + aggregates + isolation). *verify:* pytest + **verify radius.finance_reports still accessible separately**.

---

# القسم 3: الإدارة (ADMINISTRATION HUBS)

## نظرة عامة

| الصفحات الحالية | → المحور الجديد | base_url | التبويبات |
|---|---|---|---|
| business_operators + admins_list + admins_profile_summary + distributors_list | A1: مشغلو الأعمال والموزعون | `/admin-operations` | 2 تبويب + modals |
| roles_list + mt_permission_matrix | A2: الأدوار والصلاحيات | `/roles-permissions` | 2 تبويب |
| backups + recycle_bin + lifecycle_settings | A3: البيانات والحفظ | `/data-protection` | 3 تبويبات + high-gate modals |

### ملاحظات حرجة على الإدارة

1. **High-gate operations:** backups_restore، lifecycle_run تحتاج typed-confirmation + permission gate.
2. **Append-only audits:** إضافة عنصر (admin/role/distributor) لا يحذفه — soft-delete فقط (status=inactive).
3. **Service isolation:** كل محور يستخدم خدمة منفصلة (AdminService, RoleService, BackupService).
4. **Sidebar sectioning:** update `sec_admin_active` لتشمل جميع endpoints المدمجة.
5. **Standalone POST endpoints:** admins_create/edit/delete، roles_create/edit، distributors_update/settle تبقى مستقلة (form targets).

---

## محور A1 — مشغلو الأعمال والموزعون (Admin Operations Hub)

**ملف route جديد:** `app/radius/routes/admin_operations_hub.py` · `_BASE='/admin-operations'` · `_TABS=('operators','distributors')`
**قالب:** `app/templates/radius/admin_operations_hub.html`

### Endpoints المُمتصّة

| endpoint | URL | methods | يصبح |
|---|---|---|---|
| radius.business_operators | /admin/radius/business-operators | GET | tab=operators |
| radius.admins_list | /admin/radius/admins | GET | tab=operators (merged) |
| radius.admins_profile_summary | /admin/radius/admins/profile-summary | GET | tab=operators (merged) |
| radius.distributors_list | /admin/radius/distributors | GET | tab=distributors |

### Endpoints المُحفوظة (kept_standalone)

```
GET /admin/radius/admins/new + POST /admin/radius/admins → CRUD forms
GET /admin/radius/admins/<id>/edit + POST .../update → CRUD forms
POST /admin/radius/admins/<id>/delete → soft-delete
GET /admin/radius/distributors/new + POST → CRUD forms
GET /admin/radius/distributors/<id>/detail → detail page
GET /admin/radius/distributors/<id>/edit + POST .../update → CRUD forms
POST /admin/radius/distributors/assign_batch → form target
POST /admin/radius/distributors/settle → form target
```

### حقول النماذج

- **admin:** `username, email, password, role_id, enabled`
- **distributor:** `name, contact_email, wallet_balance, contract_status (active|paused|terminated)`

### KPIs

- عدد المدراء النشطين
- عدد الموزعين
- إجمالي أرصدة المحافظ للموزعين

### service/repo calls

`AdminService.list_admins/get_admin/create_admin/update_admin/deactivate_admin` · `DistributorService.list_distributors/get_distributor/create_distributor/update_distributor/settle`

### خريطة إعادة التوجيه

```
/admin/radius/business-operators → /admin-operations?tab=operators
/admin/radius/admins → /admin-operations?tab=operators
/admin/radius/admins/profile-summary → /admin-operations?tab=operators
/admin/radius/distributors → /admin-operations?tab=distributors
```

### sidebar

سطر واحد + sec_admin_active.

### commits

- **A1.1** scaffold + legacy 301 redirects. *verify:* url_map (detail pages standalone).
- **A1.2** admin_operations_hub.html (2 tabs + two tables + modals for add/edit). *verify:* tabs 200.
- **A1.3** ربط context + KPI stats. *verify:* counts == legacy.
- **A1.4** sidebar. *verify:* hub link.
- **A1.5** tests (parity + redirect + CRUD form targets work + isolation). *verify:* pytest.

---

## محور A2 — الأدوار والصلاحيات (Roles & Permissions Hub)

**ملف route جديد:** `app/radius/routes/roles_permissions_hub.py` · `_BASE='/roles-permissions'` · `_TABS=('roles','permissions')`
**قالب:** `app/templates/radius/roles_permissions_hub.html`

### Endpoints المُمتصّة

| endpoint | URL | methods | يصبح |
|---|---|---|---|
| radius.roles_list | /admin/radius/roles | GET | tab=roles |
| radius.mt_permission_matrix | /admin/radius/permissions | GET | tab=permissions (read-only) |

### Endpoints المُحفوظة (kept_standalone)

```
GET /admin/radius/roles/new + POST /admin/radius/roles/create → form
GET /admin/radius/roles/<id>/edit + POST .../save → form
POST /admin/radius/roles/<id>/delete → soft-delete
```

### حقول النماذج

- **role:** `name, description, color (color picker), permissions (multi-checkbox)`

### KPIs

- عدد الأدوار المفعَّلة
- إجمالي الصلاحيات المسندة
- أدوار بصلاحيات حساسة

### service/repo calls

`RoleService.list_roles/get_role/create_role/update_role/deactivate_role` · `PermissionsMatrix.build_matrix/all_permissions`

### خريطة إعادة التوجيه

```
/admin/radius/roles → /roles-permissions?tab=roles
/admin/radius/permissions → /roles-permissions?tab=permissions
```

### sidebar

سطر واحد + sec_admin_active.

### commits

- **A2.1** scaffold + redirects. *verify:* url_map.
- **A2.2** roles_permissions_hub.html (2 tabs + role CRUD modal + permissions read-only matrix). *verify:* tabs 200.
- **A2.3** ربط context + matrix build. *verify:* permissions == legacy.
- **A2.4** sidebar. *verify:* hub link.
- **A2.5** tests (parity + permissions matrix content + CRUD form targets + isolation). *verify:* pytest.

---

## محور A3 — البيانات والحفظ والأرشفة (Data Protection & Backup Hub)

**ملف route جديد:** `app/radius/routes/data_protection_hub.py` · `_BASE='/data-protection'` · `_TABS=('backups','recycle','lifecycle')`
**قالب:** `app/templates/radius/data_protection_hub.html`

### Endpoints المُمتصّة

| endpoint | URL | methods | يصبح |
|---|---|---|---|
| radius.backups | /admin/radius/backups | GET | tab=backups |
| radius.recycle_bin | /admin/radius/recycle-bin | GET | tab=recycle |
| radius.lifecycle_settings | /admin/radius/lifecycle | GET | tab=lifecycle |

### Endpoints المُحفوظة (kept_standalone — حساسة جداً)

```
POST /admin/radius/backups/run → trigger local backup
POST /admin/radius/backups/run_all → trigger all backups
POST /admin/radius/backups/upload_computer → upload local
POST /admin/radius/backups/schedule → schedule form
POST /admin/radius/backups/upload_panel → upload to AdminPanel
GET /admin/radius/backups/download → download backup
GET /admin/radius/backups/content → list backup contents
POST /admin/radius/backups/restore → **HIGH GATE: typed confirmation + gate check**
POST /admin/radius/backups/delete → delete backup
POST /admin/radius/backups/settings → save settings
POST /admin/radius/backups/gdrive_save → Google Drive OAuth
POST /admin/radius/backups/gdrive_start → start GDrive backup
POST /admin/radius/backups/gdrive_poll → poll GDrive status
POST /admin/radius/backups/gdrive_disconnect → disconnect GDrive
POST /admin/radius/recycle_bin/restore → **HIGH GATE: typed confirmation**
POST /admin/radius/lifecycle_policy_create → create policy
POST /admin/radius/lifecycle_policy_disable → disable policy
POST /admin/radius/lifecycle_run → **HIGH GATE: dry-run preview + confirmation**
```

### حقول النماذج (Modal Forms)

- **backup_schedule:** `frequency (daily|weekly|monthly), retention_days, enabled`
- **backup_restore:** `backup_id, typed_confirmation ('استعادة')` — gate `HOBERADIUS_LOCAL_RESTORE_DISABLED`
- **lifecycle_policy:** `table_name, action (archive|delete), age_days, dry_run_first`
- **recycle_restore:** `item_id, item_type (admin|role|subscriber), typed_confirmation`

### KPIs

- عدد النسخ الاحتياطية المحلية
- حجم النسخ الاحتياطية
- عناصر محذوفة (قابلة للاستعادة)
- سياسات الأرشفة النشطة

### service/repo calls

`BackupService.list_backups/create_backup/schedule_backup/restore_backup` · `RecycleBinService.list_deleted/restore_item` · `LifecycleService.list_policies/create_policy/run_policy_preview/execute_policy`

### خريطة إعادة التوجيه

```
/admin/radius/backups → /data-protection?tab=backups
/admin/radius/recycle-bin → /data-protection?tab=recycle
/admin/radius/lifecycle → /data-protection?tab=lifecycle
```

### sidebar

سطر واحد + sec_admin_active + **تحقق من settings/sync/tenants منفصل** (super_admin).

### commits

- **A3.1** scaffold + redirects (restore/lifecycle POSTs تبقى standalone بـ high gates). *verify:* url_map.
- **A3.2** data_protection_hub.html (3 tabs + backup list + restore/lifecycle modals with confirmation). *verify:* tabs 200.
- **A3.3** ربط context + verify no execution in preview. *verify:* lifecycle_run ?dry-run returns dict.
- **A3.4** sidebar + sec_admin_active + verify settings/sync/tenants sidebar entries untouched. *verify:* hub link + super_admin links.
- **A3.5** tests (parity + restore requires confirmation + lifecycle dry-run safety + recycle isolation + all POSTs remain standalone). *verify:* pytest + **verify settings_page + sync_list + tenants_list still accessible**.

---

# ملخص الأعمال والتقديرات

## عدد الـ Commits

| المرحلة | المحاور | عدد الـ Commits |
|---|---|---|
| E (Engagement) | 3 محاور | 3 × 5 = **15 commit** |
| R (Reports) | 5 محاور | 5 × 5 = **25 commit** |
| A (Administration) | 3 محاور | 3 × 5 = **15 commit** |
| **الإجمالي** | **11 محور** | **~55 commit** |

## التقدير الزمني

- **E (3 أيام):** E1 (0.5 يوم نظيف) + E2 (1 يوم معقد) + E3 (0.5 يوم صغير) = 2 يوم
- **R (3 أيام):** R1 (0.5 يوم) + R2 (0.5 يوم) + R3 (1 يوم) + R4 (0.5 يوم) + R5 (0.5 يوم) = 3 أيام
- **A (2 يوم):** A1 (0.5 يوم) + A2 (0.5 يوم) + A3 (1.5 يوم حساسة) = 2.5 يوم
- **الإجمالي:** 7–8 أيام عمل مركّزة + اختبارات كاملة

## المخاطر الرئيسية

### 1. Tenant Isolation (كرتيكال)
- كل مركز قراءة يجب أن يمرّر `tenant_id` من `session.get('tenant_id')` في كل استعلام.
- **اختبار مخصص:** `_existing_financial_tables` snapshot قبل/بعد GET all tabs.

### 2. High-gate Operations (حساسة جداً)
- `backups_restore` + `lifecycle_run` + `recycle_restore` **تحتاج typed-confirmation** قبل تنفيذ.
- `SafetyGateService` يجب أن يُطبّق عليها.
- **اختبار:** محاولة POST بدون confirmation → must fail.

### 3. Dry-run Safety (operations + campaigns)
- `POST /operations/speed-control ?dry-run` و `POST /communications/campaigns ?dry_run` **لا يجب أن ينفذا**.
- التحقق: POST dry-run يرجع dict/JSON فقط، لا DB changes.

### 4. Webhook & Public Endpoints
- `/communications/bot/webhook` (POST عام، موقّع) **يجب أن تبقى standalone**.
- `/communications/channels/test` (POST → JSON) **يجب أن تبقى standalone**.

### 5. Append-only Ledgers
- fraud_flags.status = 'closed' (لا DELETE).
- business_events لا تُحذف أبداً.
- investigations صف جديد per create (لا update من UI).
- **اختبار:** run_risk_rules appends count(fraud_flags_before) < count(fraud_flags_after).

### 6. Cross-link Preservation
- `radius.finance_reports` يظل في **قسم التقارير sidebar** منفصل.
- `radius.whatsapp` يظل **fully standalone**.
- `radius.settings_page` + `radius.sync_list` + `radius.tenants_list` يبقى **super_admin فقط**.

### 7. Service Call Discipline
- لا تعديل على أي service/repo. استدعِ بحرفها.
- KPI = نتيجة service call مباشرة (لا حساب يدوي).

---

# أسئلة مفتوحة وقرارات تحتاج موافقة المالك

1. **توسيع محاور التقارير:**
   - هل نُضيف `/communications/deliveries` كـ **tab قراءة فقط** في E1 (communications-center hub)؟
   - هل نُضيف `/communications/guide` كـ **tab قراءة فقط** في E1؟
   - **القرار:** الآن سطر 1 (keep as standalone)، يمكن توسيع لاحقًا.

2. **Lifecycle Policy Execution:**
   - هل `POST /admin/radius/lifecycle_run` سيكون **async job** (مع callback) أم **sync with timeout**؟
   - لو async: هل نشير preview modal لـ job status page؟
   - **القرار:** الآن sync محمية بـ dry-run prompt + confirmation typed.

3. **AdminPanelClient Bridge (WhatsApp quota):**
   - إذا `AdminPanelClient.get_whatsapp_status()` timeout/fail: هل نُعرض **status=unavailable** صامتة أم **alert/warning**؟
   - **القرار:** Graceful degradation (render partial, no error page).

4. **Sidebar Grouping (sec_*_active):**
   - بعد دمج 11 محور: هل نُنشئ **subsections** جديدة (E, R, A) مع **collapsible groups**؟
   - **القرار:** الآن نُحافظ على structure الحالي (data-hb-section=...) + update matchers.

5. **Test Coverage Scope:**
   - هل نُعيد تشغيل **الـ test_* الأصلية** (test_communications_web.py، etc.) بالكامل أم فقط POST endpoints؟
   - **القرار:** re-run كاملة (get_green_gate قبل commit).

---

# التسلسل الموصى به للتنفيذ

1. **Scaffold phase:** E1.1 + E2.1 + E3.1 + R1.1 + R2.1 + ... (15 commits parallel-safe)
2. **Template phase:** E1.2 + E2.2 + ... (15 commits)
3. **Context binding:** E1.3 + E2.3 + ... (15 commits)
4. **Sidebar + tests:** E1.4 + E1.5 + E2.4 + E2.5 + ... (25 commits final)

**Safeguard:** بعد كل phase، run:
```bash
python -c 'from app import create_app; app=create_app(); \
  [print(r) for r in app.url_map if "hub" in str(r)]' | wc -l
# يجب يزيد count من 0 إلى 11
```

---

**التاريخ:** يونيو 2026
**الحالة:** جاهز للتنفيذ بعد موافقة المالك على 5 الأسئلة المفتوحة.
