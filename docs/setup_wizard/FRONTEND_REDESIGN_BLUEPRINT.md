# Setup Wizard Frontend Redesign Blueprint

## Scope

This document audits the current Setup Wizard UI and defines a safe frontend redesign plan. It does not change backend behavior, routes, safety gates, RADIUS/auth/accounting logic, or live-apply policy.

The target experience is an Apple-style setup assistant combined with a calm SaaS onboarding flow and a MikroTik assistant:

- one screen equals one step
- beginner-first Arabic RTL
- no visible JSON in normal mode
- clear emotional progress
- advanced engineering details hidden behind Advanced Mode
- existing console/debug value preserved as Engineering View

## 1. UX Audit

Audited files:

- `app/templates/radius/setup_wizard.html`
- `app/static/css/setup_wizard.css`
- `app/static/js/setup_wizard.js`
- `app/radius/routes/setup_wizard.py`
- `app/radius/services/setup_wizard.py`
- `app/radius/services/setup_wizard_operations.py`
- `app/radius/services/setup_wizard_pilot.py`
- `app/radius/services/setup_wizard_lab.py`

### Why the Current UI Feels Like a Dev Console

The current page exposes every subsystem at once:

- Internet, VPN/RADIUS, Hotspot, Broadband, script output, guarded operations, lab timeline, inventory, and pilot drill all appear on a single scrolling page.
- Several inputs require raw JSON, which is appropriate for test coverage but intimidating for first-time operators.
- The user is asked to understand internal step names such as `internet`, `vpn`, `hotspot`, and `broadband`.
- Primary and secondary actions are mixed together. A beginner sees `Dry run`, `Apply guarded`, `Rollback preview`, `Health`, `Support bundle`, and `Added services catalog` before understanding the next required step.
- The stepper lists phases, but the page does not behave like a one-step-at-a-time wizard.
- Status cards show backend states, but they do not create a guided sense of progress or relief.
- Engineering details are useful but not hidden: JSON payloads, terminal output, operation queues, confirmation phrases, and feature flags are visible in the main path.
- The lab safety panel and pilot panel are correct safety tools, but visually they compete with the beginner setup journey.
- Error and diagnostic details are dumped as JSON in the output panel instead of being translated into "what happened / why / what to do now".

### What Must Be Preserved

- Existing backend routes and services.
- Current safety gates, dry-run, lab policy, support bundle, health, and pilot drill.
- CSRF behavior.
- The current debug-style page as an Advanced Engineering View.
- Ability to inspect scripts, operations, diagnostics, and raw payloads when Advanced Mode is enabled.

## 2. New Information Architecture

### Step 1: Welcome

- Purpose: reassure the operator and explain the path.
- Primary CTA: "ابدأ الإعداد"
- Secondary CTA: "فتح العرض الهندسي"
- Visible fields: none.
- Hidden advanced fields: run ID, actor, existing summary.
- Success state: wizard run created.
- Failure state: "تعذر بدء المعالج. حدّث الصفحة أو راجع الصلاحيات."
- Backend endpoint: `POST /admin/radius/setup-wizard/runs`
- Required gate: authenticated admin session.

### Step 2: Internet Source Selection

- Purpose: choose how the MikroTik gets internet.
- Primary CTA: "التالي"
- Secondary CTA: "لست متأكدًا"
- Visible fields: source cards for DHCP, Static IP, VLAN, PPPoE.
- Hidden advanced fields: raw `source_type`.
- Success state: source type selected locally.
- Failure state: "اختر نوع مصدر الإنترنت أولًا."
- Backend endpoint: none until details step.
- Required gate: active run.

### Step 3: Internet Source Details

- Purpose: collect only fields required for selected source.
- Primary CTA: "تجهيز سكربت الإنترنت"
- Secondary CTA: "رجوع"
- Visible fields:
  - DHCP: interface, add default route, use peer DNS, NAT.
  - Static: interface, IP/CIDR, gateway, DNS, NAT.
  - VLAN: parent interface, VLAN ID/name, DHCP/static mode, DNS, NAT.
  - PPPoE: interface, username, password, service name, route/DNS/NAT.
- Hidden advanced fields: complete JSON payload preview.
- Success state: internet script generated.
- Failure state: inline field errors plus a simple diagnosis.
- Backend endpoint:
  - `POST /admin/radius/setup-wizard/runs/<id>/internet-source`
  - `POST /admin/radius/setup-wizard/runs/<id>/generate-internet-script`
- Required gate: active run.

### Step 4: Internet Script Preview

- Purpose: present a copyable MikroTik script without overwhelming the user.
- Primary CTA: "نسخ السكربت"
- Secondary CTA: "عرض التفاصيل المتقدمة"
- Visible fields: short instruction card, copy button, script summary, validation command list.
- Hidden advanced fields: full script, rollback notes, generated objects.
- Success state: copied script and "بعد التنفيذ اضغط تحقق".
- Failure state: copy failure fallback with selectable script.
- Backend endpoint: uses generated plan from previous step.
- Required gate: internet script generated.

### Step 5: Internet Verification

- Purpose: confirm first internet access before continuing.
- Primary CTA: "تحقق من الاتصال"
- Secondary CTA: "لصق مخرجات Terminal"
- Visible fields: pasted output box only when needed.
- Hidden advanced fields: raw checks JSON.
- Success state: green success card "تم تأكيد الإنترنت".
- Failure state: DiagnosticsCard with likely causes and commands.
- Backend endpoint: `POST /admin/radius/setup-wizard/runs/<id>/verify-internet`
- Required gate: internet script generated.

### Step 6: VPN/RADIUS Script Preview

- Purpose: prepare secure HobeRadius-to-router bootstrap.
- Primary CTA: "تجهيز سكربت الربط"
- Secondary CTA: "عرض التفاصيل المتقدمة"
- Visible fields: intended VPN IPs, server endpoint, API user name masked/summary.
- Hidden advanced fields: full JSON payload, script body, rollback notes.
- Success state: script ready to copy.
- Failure state: missing required generated values.
- Backend endpoint: `POST /admin/radius/setup-wizard/runs/<id>/generate-vpn-radius-script`
- Required gate: internet verification.

### Step 7: VPN/RADIUS Verification

- Purpose: confirm tunnel, RADIUS, and API before services.
- Primary CTA: "فحص الربط"
- Secondary CTA: "لصق نتائج Terminal"
- Visible fields: status cards for VPN tunnel, VPS ping, Router ping, RADIUS, API.
- Hidden advanced fields: raw output and check map.
- Success state: "تم ربط الراوتر بنجاح".
- Failure state: diagnostic code translated into cause and next action.
- Backend endpoint: `POST /admin/radius/setup-wizard/runs/<id>/verify-vpn-radius`
- Required gate: VPN/RADIUS script generated.

### Step 8: Router Connected Success

- Purpose: emotional checkpoint and confidence moment.
- Primary CTA: "اختيار الخدمة"
- Secondary CTA: "حفظ تقرير الدعم"
- Visible fields: router identity if available, tunnel status, next options.
- Hidden advanced fields: support bundle.
- Success state: connection confirmed.
- Failure state: cannot reach this step without verification.
- Backend endpoint: `GET /admin/radius/setup-wizard/runs/<id>/summary`
- Required gate: VPN/RADIUS verified.

### Step 9: Choose Service Path

- Purpose: choose service installation path.
- Primary CTA: "متابعة"
- Secondary CTA: "تخطي الخدمات الآن"
- Visible fields: cards for Hotspot, Broadband/PPPoE, Both, Skip.
- Hidden advanced fields: raw mode/selection.
- Success state: selected path saved locally.
- Failure state: "اختر خدمة أو تخطى هذه المرحلة."
- Backend endpoint: no mutation required yet.
- Required gate: VPN/RADIUS verified.

### Step 10: Interface Selection

- Purpose: choose safe candidate interfaces.
- Primary CTA: "تأكيد الواجهات"
- Secondary CTA: "تحديث inventory"
- Visible fields: InterfacePicker with excluded WAN/VPN clearly disabled.
- Hidden advanced fields: raw snapshot/risk report.
- Success state: interfaces selected.
- Failure state: WAN/VPN selected or inventory missing.
- Backend endpoints:
  - `POST /admin/radius/setup-wizard/runs/<id>/inventory`
  - `POST /admin/radius/setup-wizard/runs/<id>/interfaces/candidates`
- Required gate: VPN/RADIUS verified and inventory available.

### Step 11: Hotspot/Broadband Smart/Manual Config

- Purpose: keep smart defaults first, manual override second.
- Primary CTA: "تجهيز الخطة"
- Secondary CTA: "تعديل متقدم"
- Visible fields: Smart/Manual toggle, recommended subnet, service name, DNS/name fields.
- Hidden advanced fields: blocked networks, raw planner payload.
- Success state: script generated.
- Failure state: subnet conflict, interface conflict, missing required field.
- Backend endpoints:
  - `POST /admin/radius/setup-wizard/runs/<id>/generate-hotspot-script`
  - `POST /admin/radius/setup-wizard/runs/<id>/generate-broadband-script`
  - or orchestration preview endpoints where already used.
- Required gate: VPN/RADIUS verified.

### Step 12: Dry-run Review

- Purpose: show what would happen without applying anything.
- Primary CTA: "مراجعة آمنة"
- Secondary CTA: "عرض العمليات"
- Visible fields: operation count, risk badges, rollback availability.
- Hidden advanced fields: full command list.
- Backend endpoint: `POST /admin/radius/setup-wizard/runs/<id>/dry-run/<step>`
- Required gate: script generated.

### Step 13: Optional Lab Apply Panel

- Purpose: internal CHR lab only.
- Primary CTA: disabled unless both flags and policy pass.
- Secondary CTA: "إنشاء قائمة اختبار CHR"
- Visible fields: strong warning, feature-flag state, policy blocking reasons.
- Hidden advanced fields: confirmation phrase, raw operation queue.
- Success state: apply attempt recorded and verification required.
- Failure state: blocked reason or failed operation with rollback suggestion.
- Backend endpoints:
  - `GET /admin/radius/setup-wizard/runs/<id>/pilot-drill`
  - `POST /admin/radius/setup-wizard/runs/<id>/apply/<step>`
  - `POST /admin/radius/setup-wizard/runs/<id>/rollback/<step>`
- Required gate: lab mode only, not customer path.

### Step 14: Added Services

- Purpose: choose optional policies after core connection.
- Primary CTA: "اختيار الخدمات"
- Secondary CTA: "تخطي"
- Visible fields: service catalog cards and presets.
- Hidden advanced fields: planner delegate output.
- Success state: plan generated or safe unsupported response.
- Failure state: unsupported service explanation.
- Backend endpoints:
  - `GET /admin/radius/setup-wizard/added-services/catalog`
  - `POST /admin/radius/setup-wizard/runs/<id>/added-services/plan`
- Required gate: VPN/RADIUS verified.

### Step 15: Final Summary / Success

- Purpose: close the wizard with confidence and next actions.
- Primary CTA: "إنهاء"
- Secondary CTA: "تحميل تقرير الدعم"
- Visible fields: completed steps, skipped steps, diagnostics resolved, next recommendations.
- Hidden advanced fields: full support bundle JSON.
- Backend endpoints:
  - `GET /admin/radius/setup-wizard/runs/<id>/summary`
  - `GET /admin/radius/setup-wizard/runs/<id>/support-bundle`
- Required gate: at least VPN/RADIUS verified or explicitly abandoned/skipped services.

## 3. UI Component System

### WizardShell

- Visual purpose: one-step viewport with calm background and fixed progress header.
- Props/data: run ID, current step, mode, route/action handlers.
- Arabic microcopy: "سنمشي خطوة بخطوة. لن يتم تنفيذ أي شيء على الراوتر بدون مراجعة."
- Empty/loading/error: skeleton header, blocked access message.

### WizardStepper

- Visual purpose: compact progress rail with completed/current/locked states.
- Props/data: step list, statuses, gates.
- Arabic microcopy: "الخطوة الحالية"، "مكتملة"، "مقفلة".
- States: locked tooltip explains missing verification.

### WizardHeroCard

- Visual purpose: welcoming step intro, not a dense panel.
- Props/data: title, subtitle, icon, safety note.
- Arabic microcopy: beginner-first instruction per step.
- States: success variant, warning variant, blocked variant.

### StepStatusCard

- Visual purpose: show one check status at a time.
- Props/data: status, title, details, diagnostic code.
- Arabic microcopy: "جاهز"، "يحتاج انتباه"، "تم".
- States: pending, running, success, failed, blocked.

### SourceTypeCard

- Visual purpose: replace select dropdown with clear cards.
- Props/data: DHCP/static/VLAN/PPPoE descriptions.
- Arabic microcopy:
  - DHCP: "الراوتر يأخذ الإنترنت تلقائيًا من المودم."
  - Static: "مزود الخدمة أعطاك IP وGateway."
  - VLAN: "الإنترنت يحتاج VLAN من المزود."
  - PPPoE: "الإنترنت يحتاج اسم مستخدم وكلمة مرور."
- States: selected, disabled, recommended.

### ScriptPreviewCard

- Visual purpose: guided copy/paste experience.
- Props/data: script summary, full script, validation commands, rollback notes.
- Arabic microcopy: "انسخ السكربت والصقه في Terminal داخل MikroTik."
- States: copied, copy failed, advanced expanded.

### VerificationPanel

- Visual purpose: focus the user on a single "verify now" action.
- Props/data: verification endpoint, pasted output, check cards.
- Arabic microcopy: "بعد تنفيذ السكربت اضغط فحص الربط."
- States: waiting, checking, success, failed, partial.

### DiagnosticsCard

- Visual purpose: turn raw diagnostic codes into useful next actions.
- Props/data: code, explanation, likely causes, fixes, commands.
- Arabic microcopy: "ما الذي حدث؟"، "السبب المحتمل"، "ماذا تفعل الآن؟"
- States: collapsed summary, expanded details.

### InterfacePicker

- Visual purpose: select safe service interfaces.
- Props/data: inventory interfaces, excluded interfaces, risk report.
- Arabic microcopy: "هذه الواجهة تبدو WAN لذلك لا يمكن اختيارها."
- States: safe, excluded, conflict, unknown.

### SmartManualToggle

- Visual purpose: default to smart setup, allow manual override.
- Props/data: mode, recommended values.
- Arabic microcopy: "اختيار ذكي موصى به"، "تعديل يدوي متقدم".
- States: smart selected, manual selected, warning on manual.

### AdvancedDrawer

- Visual purpose: keep engineering power without overwhelming beginners.
- Props/data: JSON payload, raw output, operations, support bundle.
- Arabic microcopy: "تفاصيل هندسية متقدمة".
- States: closed by default, open, copy raw.

### DryRunReview

- Visual purpose: review operation queue safely.
- Props/data: operation count, safety warnings, rollback availability.
- Arabic microcopy: "هذه مراجعة فقط. لم يتم تنفيذ أي أمر."
- States: safe, warning, blocked.

### LabSafetyPanel

- Visual purpose: separate CHR lab from normal customer wizard.
- Props/data: live flag, lab flag, policy, pilot checklist.
- Arabic microcopy: "مختبر داخلي فقط. لا تستخدمه على راوتر زبون."
- States: disabled, eligible, blocked, apply attempted, rollback available.

### FinalSummaryCard

- Visual purpose: finish with clear status and next actions.
- Props/data: completed steps, skipped steps, diagnostics, support bundle.
- Arabic microcopy: "تم تجهيز الراوتر بنجاح."
- States: success, partial, needs review.

## 4. Arabic Microcopy

### Welcome

العنوان: "إعداد الراوتر خطوة بخطوة"

النص: "سنساعدك على توصيل MikroTik مع HobeRadius بدون تعقيد. لن يتم تنفيذ أي شيء على الراوتر تلقائيًا في الوضع العادي."

CTA: "ابدأ الإعداد"

### Internet Selection

"كيف يصل الإنترنت إلى الراوتر؟ اختر الطريقة الأقرب لإعداد مزود الخدمة."

### Script Copy Instructions

"انسخ السكربت التالي والصقه في Terminal داخل MikroTik. بعد التنفيذ ارجع لهذه الصفحة واضغط تحقق."

### Verification Waiting

"ننتظر نتيجة الفحص. إذا نفذت السكربت على الراوتر، اضغط تحقق الآن."

### Verification Success

"تم التأكيد. الاتصال يعمل ويمكننا الانتقال للخطوة التالية."

### Verification Failed

"لم ينجح الفحص بعد. لا تقلق، سنعرض السبب المحتمل والخطوة التالية."

### VPN/RADIUS Connected

"تم ربط الراوتر مع HobeRadius بنجاح. الآن يمكننا قراءة الواجهات وتجهيز الخدمات."

### Hotspot Choice

"هل تريد تجهيز خدمة كروت Hotspot على واجهات محددة؟"

### Broadband Choice

"هل تريد تجهيز PPPoE/Broadband للمشتركين؟"

### Lab Safety Warning

"هذا القسم للمختبر الداخلي فقط. لا تستخدمه على راوتر زبون. تأكد من وجود backup ودخول خارجي قبل أي تجربة."

### Final Success

"اكتمل الإعداد الأساسي. يمكنك الآن إدارة الراوتر من HobeRadius ومراجعة التقرير عند الحاجة."

## 5. Implementation Plan

### FR1: Add New Wizard Shell Behind Safe Entry

- Add v2 shell route or feature flag.
- Keep current page as Engineering View.
- No backend behavior change.
- Acceptance: v2 shell renders static step state and links to Engineering View.

### FR2: Internet Steps Redesign

- Replace JSON-first internet panel with source cards and typed forms.
- Generate current backend payload client-side.
- Keep raw payload in AdvancedDrawer.

### FR3: Script Preview and Verification Redesign

- Add ScriptPreviewCard and VerificationPanel.
- Convert raw JSON responses into readable success/failure states.
- Preserve raw output in AdvancedDrawer.

### FR4: VPN/RADIUS Steps Redesign

- Use simple fields and masked secrets.
- Show status cards for VPN/RADIUS/API checks.
- Keep generated script behind copy-focused card.

### FR5: Service Choice and Interface Picker

- Add service path cards.
- Add inventory-driven InterfacePicker.
- Clearly disable WAN/VPN interfaces.

### FR6: Dry-run, Lab, and Advanced Drawer

- Move Dry-run/Lab controls into gated Advanced Mode.
- LabSafetyPanel remains hidden unless "مختبر CHR" mode is selected.
- Preserve all operational guardrails.

### FR7: Final Summary and Polish

- Add final success screen.
- Add support bundle download/copy action.
- Polish responsive RTL layout and focus states.

### FR8: Retire Old Console From Main Path

- Keep current console as `/admin/radius/setup-wizard/engineering` or Advanced Engineering View.
- Do not delete debug tools until v2 is validated end-to-end.

## 6. Prototype Decision

No static prototype was added in this phase. The current requirement is a blueprint without backend behavior changes. Adding a new route/template is safe later, but it should be done as FR1 after reviewing this document so the prototype does not drift from the approved information architecture.

## 7. Non-Goals

- No backend rewrite.
- No route removal.
- No live apply enablement.
- No customer production mode.
- No changes to RADIUS/auth/accounting behavior.
- No changes to `radius-module-admin`.
- No Flutter changes.

## 8. Next Recommended Slice

Start FR1:

- Add `/admin/radius/setup-wizard-v2-preview` as a static/mock shell.
- Include WizardShell, WizardStepper, Welcome, and SourceTypeCard only.
- Keep current setup wizard page unchanged.
- Add route render tests and `node --check` if JS is introduced.
