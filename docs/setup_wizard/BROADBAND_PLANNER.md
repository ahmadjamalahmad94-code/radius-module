# SW5 — Broadband/PPPoE Planner

## الهدف
- توليد مخطط Broadband/PPPoE بشكل deterministic.
- بدون تنفيذ حي على MikroTik.
- مع حماية ضد تعارض WAN/VPN/Hotspot.

## الخدمة
- `BroadbandBootstrapPlanner`
  - modes:
    - `manual`
    - `smart`
  - outputs:
    - `script_text`
    - `rollback_script_text`
    - `validation_commands`
    - `warnings`
    - `generated_objects`
    - `computed`

## سلوك الأمان
- استبعاد واجهات WAN/VPN من الاختيار.
- منع تعارض الـ pool مع الشبكات المحجوبة.
- NAT مقيّد على `src-address=<remote_pool_cidr>` فقط.
- منع أوامر تدميرية (`remove/disable/reset`).
- وسم كل العناصر:
  - `HOBERADIUS_SETUP:<run_id>:broadband`

## المقاطع المولدة في السكربت
1. PPP profile
2. IP pool
3. ربط pool بالـ profile
4. PPPoE server على الواجهات المختارة
5. NAT مقيّد بنطاق broadband
6. validation commands

## تكامل الحالة
- لا يمكن إنشاء `broadband_script_preview` إلا بعد:
  - internet verified
  - vpn/radius verified

## ملاحظة
SW5 في هذه المرحلة هو planning + verification contract integration فقط.
لا يتضمن أي apply مباشر أو أوامر تنفيذ حية على الأجهزة.
