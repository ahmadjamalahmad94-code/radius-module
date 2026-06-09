# استهلاك الجسر الموقّع — أنفاق CHR + فرض السوبر يوزر

هذان عقدان بُنيا في لوحة التراخيص، وهذا توثيق **الطرف المستهلِك** المنفَّذ في
لوحة العميل (radius-module) ليكتملا end-to-end.

> ⚠️ **أمان**: لوحة العميل (RADIUS) مباعة للعملاء. لا تُخزَّن فيها أي أسرار
> CHR/نفق خام إطلاقًا — فقط استهلاك الجسر الموقّع (HTTPS + HMAC + `license_key`
> + `server_fingerprint`)، بنفس آلية `admin_panel_client`.

كل طلبات الجسر تمرّ عبر `AdminPanelClient` الموجود (توقيع `sign_admin_bridge_payload`،
ترويسة `X-HobeRadius-Admin-Secret`، مغلّف `_license_check_payload`).

---

## العقد ١ — أنفاق CHR (SSTP/PPTP/L2TP/IPsec)

### النقاط على الجسر
| الغرض | المسار |
|------|--------|
| طلب نفق | `POST /api/integration/hoberadius/vpn/tunnels/request` |
| مزامنة/التقاط القائمة | `POST /api/integration/hoberadius/vpn/tunnels` |
| تأكيد ما خُزِّن | `POST /api/integration/hoberadius/vpn/tunnels/ack` |

### المكوّنات
- **العميل**: `AdminPanelClient.request_vpn_tunnel / fetch_vpn_tunnels / ack_vpn_tunnels`
  (في `services/admin_panel_client.py`). الطلب والمزامنة يستخدمان `sanitize=False`
  لأن الخدمة تحتاج كلمة المرور **لمرة واحدة** للحقن المحلي.
- **الخدمة**: `services/license_tunnel_bridge.py` — `LicenseTunnelBridgeService`:
  - `request_tunnel` → يستلم يوزر/باس SSTP، يخزّن **بيانات وصفية + بصمة مرجعية
    لا رجعية للسر** فقط، يؤكّد (ack) فورًا، ويعيد كلمة المرور **مرة واحدة**
    للحقن (لا تُكتب في القاعدة أبدًا).
  - `sync_tunnels` → يلتقط القائمة (حتى اليدوية PPTP/L2TP/IPsec)، يطبّق دورة الحياة،
    ثم يؤكّد أسماء ما خُزِّن (بعدها يتوقف الجسر عن إعادة كلمات المرور).
- **التخزين**: جدول `bridge_tunnels` (migration 110) + `repos/bridge_tunnels_repo.py`.
  لا عمود لكلمة المرور إطلاقًا؛ `secret_ref = "ref:<sha256[:12]>"` (بصمة، ليست السر).
- **الواجهة**: `routes/tunnels.py` + `templates/radius/tunnels.html` — **عرض + طلب فقط**
  (لا توليد بيانات اعتماد CHR في لوحة العميل). تظهر في سايدبار «التكامل والجسر» →
  «الأنفاق». بيانات الدخول تُعرض مرة واحدة في جسد الاستجابة (لا في الكوكي ولا القاعدة).
- **دورة الحياة**: `revoked` → حذف محلي (`delete_tunnel`)؛ `suspended` → تعطيل محلي
  (`status='suspended', enabled=0`، مع إبقاء السجل).
- **المزامنة الدورية**: `_maybe_sync_tunnels` في `workers/admin_bridge_sync_worker.py`
  خلف العلم `license_admin_bridge.tunnel_sync_enabled` (أو `HOBERADIUS_ADMIN_TUNNEL_SYNC_ENABLED`).

---

## العقد ٢ — فرض السوبر يوزر

مصدر الحقيقة لمن يكون سوبر هو لوحة التراخيص.

### النقاط على الجسر
| الغرض | المسار |
|------|--------|
| تقرير جرد المدراء (منتِج) | `POST /api/integration/hoberadius/admins/report` |
| استلام القرارات (مستهلِك) | ضمن رد `…/identity-sync` تحت `admin_super_overrides` |

### المنتِج (التقرير)
- العميل: `AdminPanelClient.post_admins_report(admins=[...])`.
- الخدمة: `services/license_admin_inventory_report.py` —
  `build_admin_inventory()` يرسل **فقط** الحقول:
  `id, username, role, is_super_admin, enabled, managed_by_license_admin,
  external_identity_provider` (لا تُرسل أي بصمة كلمة مرور).
- يُشغَّل في عامل المزامنة **قبل** مزامنة الهوية (`_maybe_report_admins`) خلف نفس
  علم مزامنة الهوية، حتى تعود قرارات السوبر في رد الهوية نفسه.

### المستهلِك (الفرض)
- في `LicenseAdminIdentitySyncService.sync_once`، بعد معالجة المستخدمين، يُستدعى
  `apply_super_admin_overrides(payload["admin_super_overrides"])`.
- لكل عنصر `{radius_admin_id, username, is_super_admin}`: يُطابَق الأدمن المحلي
  بـ`radius_admin_id` **ثم** `username`، ويُضبط **`is_super_admin` فقط**
  (`admins_repo.apply_super_admin_override`):
  - **idempotent**: لا كتابة (ولا تحديث `updated_at`) إن كانت القيمة مطابقة.
  - **لا يلمس** كلمة المرور، ولا مزوّد الهوية، ولا الدور، ولا حالة التفعيل.

---

## الاختبارات
`tests/test_bridge_consumers.py` — تغطّي المنتِج/المستهلِك (محاكاة ردود الجسر)،
ودورة حياة مزامنة الأنفاق، وتأكيد **عدم تخزين السر الخام** (بصمة فقط)، ورندر قسم
الأنفاق. لا تُمسّ الشبكة (نقل وهمي عبر `RoutingTransport`).
