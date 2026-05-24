# Setup Wizard Verification Engine (Wave B)

## الهدف
تقديم محرك تحقق واقعي (Read-Only) لخطوات المعالج بدون أي كتابة على MikroTik.

## قواعد الأمان
- لا يوجد `add/set/remove/disable/reset` ضمن أي عملية تحقق.
- `RouterReadOnlyProbe.run_read_only_command` يرفض أي أمر يحتوي كلمات كتابة/تعديل محظورة.
- لا يوجد أي auto-apply أو mutation في هذا الموجة.

## أنماط التحقق
1. `pasted_output`
   - المستخدم يلصق مخرجات الطرفية.
   - المحرك يحلل إشارات النجاح/الفشل.
2. `probe`
   - محاولة قراءة مباشرة عبر adapters read-only.
   - عند عدم توفر adapter: حالة `blocked` مع تشخيص واضح.
3. `manual_contract`
   - وضع يدوي/اختبارات (checks boolean).

## المحولات (Adapters)
- `RouterReadOnlyProbe`
  - قراءة الهوية/الواجهات/العناوين/المسارات/DNS/RADIUS/Hotspot/PPPoE/NAT.
  - `ping` فقط.
  - `run_read_only_command` مع guard محظورات.
- `VpsNetworkProbe`
  - `ping_router_vpn_ip`
  - `inspect_wireguard_peer`
  - `check_udp_port_hint`
- `RadiusReadOnlyProbe`
  - `inspect_radius_config`
  - `test_auth` (اختياري وآمن فقط إذا كانت البيئة تدعمه).

## معايير النجاح لكل خطوة
- Internet:
  - الحد الأدنى: نجاح `ping_8_8_8_8`.
- VPN/RADIUS:
  - يجب نجاح:
    - `vpn_tunnel`
    - `router_ping_vps`
    - `vps_ping_router`
    - `radius_reachable`
    - `api_login`
- Hotspot:
  - يجب نجاح:
    - `hotspot_server_present`
    - `radius_enabled`
- Broadband:
  - يجب نجاح:
    - `pppoe_service_present`
    - `radius_enabled`
    - `broadband_nat_present`

## timeout policy
- جميع probes قصيرة (افتراضيًا ثوانٍ قليلة) لمنع حجز request طويل.
- عند عدم توفر probe أو timeout: يرجع `blocked/partial` بدل exception غير مسيطر عليها.

## Diagnostics
المحرك يرجع:
- `code`
- `arabic_title`
- `explanation_ar`
- `likely_causes`
- `suggested_fixes`
- `commands_to_inspect`

الأكواد تشمل:
- internet_ping_failed
- dns_failed
- default_route_missing
- nat_missing
- uplink_interface_missing
- probe_unavailable
- vpn_not_handshaking
- wrong_public_endpoint
- firewall_blocking_udp
- wrong_allowed_address
- route_missing
- radius_secret_mismatch
- radius_server_unreachable
- api_login_failed
- api_user_missing
- router_dns_issue
- router_time_issue
- duplicate_config_conflict
- management_interface_conflict
- hotspot_server_missing
- hotspot_radius_disabled
- hotspot_pool_missing
- hotspot_nat_missing
- hotspot_interface_missing
- pppoe_service_missing
- ppp_profile_missing
- broadband_pool_missing
- broadband_nat_missing
- ppp_radius_disabled

## limitations الحالية
- probes الحية تعتمد على توفر adapters في البيئة التشغيلية.
- إذا adapters غير مفعلة، التحقق يعتمد على تحليل المخرجات الملصقة.
- لا يتم تنفيذ أي تعديل على الراوتر في Wave B.
