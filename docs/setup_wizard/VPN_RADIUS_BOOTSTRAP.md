# SW3 — VPN/RADIUS Bootstrap Planner + Verification Contract

## الهدف
- توليد سكربت **Preview فقط** لإعداد VPN + RADIUS + عقد API.
- بناء عقد فحص (verification contract) منظم ببطاقات حالة.
- تقديم تشخيصات عربية جاهزة بدون أي تنفيذ مباشر على الراوتر.

## حدود الأمان
- لا يوجد `apply-to-router`.
- لا يوجد استدعاء مباشر لأي MikroTik runtime adapter.
- السكربتات المولدة تمر عبر فلتر أمان يمنع:
  - `/remove`
  - `remove` العام
  - `disable`
  - `reset-configuration`
  - `system reset`

## الخدمات المضافة
- `VpnRadiusBootstrapPlanner`
  - ينتج:
    - `script_text`
    - `rollback_script_text`
    - `validation_commands`
    - `warnings`
    - `generated_objects`
    - `masked_sensitive_values`
    - `diagnostics_hints`
- `SetupVerificationService`
  - ينتج status cards:
    - `vpn_tunnel`
    - `vps_ping`
    - `router_ping`
    - `radius_reachable`
    - `api_login`
    - `hotspot_ready`
    - `broadband_ready`
- `SetupDiagnosticsService`
  - يقدم mapping عربي للأخطاء القياسية.

## تكامل الحالة (state integration)
- لا يمكن توليد `vpn_radius_script_preview` قبل تحقق الإنترنت.
- يبقى تحقق `vpn_radius_verification` مطلوبًا قبل Hotspot/Broadband.
- بعد توليد script يتم تعليم خطوة `vpn_radius_script_preview` كـ `generated`.

## مخرجات السكربت
- جميع العناصر الموسومة بالتعليقات:
  - `HOBERADIUS_SETUP:<run_id>:vpn`
  - `HOBERADIUS_SETUP:<run_id>:radius`
  - `HOBERADIUS_SETUP:<run_id>:api`
- يوجد قسم تحذيرات وقسم تحقق نهائي داخل السكربت.
- الأسرار الحساسة تُخفى في metadata وتظهر فقط في script preview عند الحاجة.

## ملاحظة تنفيذ
هذه المرحلة تبني التخطيط + عقد التحقق فقط.
لا تشمل تنفيذ حي، ولا إنشاء مستخدمي API فعليًا، ولا تعديل مباشر على الأجهزة.
