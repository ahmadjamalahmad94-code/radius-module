# SW4 — Interface Discovery Contract + Hotspot Planner

## الهدف
- تجهيز مخطط Hotspot (Manual/Smart) بشكل deterministic.
- بدون أي تنفيذ مباشر على MikroTik.
- مع عقد واضح لاكتشاف الواجهات لاحقًا.

## العقد (Contract)
- `InterfaceDiscoveryContract`
  - دالة: `list_interfaces(tenant_id, run_id)`
  - تعيد قائمة `InterfaceInfo` فقط.
- `StaticInterfaceDiscovery`
  - تنفيذ ثابت للاختبارات (mock-safe).

## مخطط Hotspot
- `HotspotBootstrapPlanner`
  - يدعم:
    - `manual`
    - `smart`
  - يمنع اختيار:
    - واجهة WAN المحددة في wizard run
    - واجهة VPN (`hr-wg`)
  - يمنع تضارب subnet مع شبكات WAN/VPN/المعطاة كـ blocked.

## مخرجات الخطة
- `script_text`
- `rollback_script_text`
- `validation_commands`
- `warnings`
- `generated_objects`
- `computed` (الشبكة النهائية، pool، أسماء الكيانات)

## قواعد أمان
- لا يوجد remove/disable/reset.
- NAT مقيّد على شبكة hotspot فقط (`src-address=<hotspot_cidr>`).
- كل عنصر موسوم:
  - `HOBERADIUS_SETUP:<run_id>:hotspot`

## تكامل الحالة
- لا يمكن توليد `hotspot_script_preview` إلا بعد:
  - تحقق internet
  - تحقق vpn/radius

## ملاحظة
هذه المرحلة تبني التخطيط والتحقق فقط.
لا يوجد أي تكامل تنفيذ حي مع الراوتر في SW4.
