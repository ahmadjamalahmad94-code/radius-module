-- 105_tunnel_chr_provisioning — تتبّع إنشاء حساب النفق فعليًا على خادم CHR المركزي.
--
-- ⚠️ محجوزة لإعادة بناء مركزية: أُزيلت ميزة الأنفاق/الـCHR من لوحة العميل
-- (يُعاد بناؤها مركزياً عبر لوحة التراخيص — قرار معماري). هذه الهجرة تبقى
-- كما هي تفادياً لانحراف المخطط؛ أعمدة *_chr_* على nas_devices موجودة لكن
-- غير مستخدمة حالياً (حُذف chr_provisioner و router_tunnels_repo).
--
-- خلفية: migration 092 خزّن ملف النفق لكل راوتر لكن إنشاء المستخدم على
-- الـ CHR (الـ /ppp secret المقابل) كان يدويًا (راجع docs/router_vpn/
-- ROUTEROS_V6_VPN_SERVER_SETUP.md §10 — Phase 2). هذه الهجرة تضيف الأعمدة
-- التي يسجّل فيها مُجهِّز CHR (services/chr_provisioner.py) نتيجة الإنشاء
-- التلقائي عبر RouterOS API: اسم المستخدم على CHR، حالة الإنشاء، وقته،
-- وآخر خطأ إن وُجد.
--
-- أمان: لا تُخزَّن كلمة مرور المستخدم هنا إطلاقًا — تبقى على نمط
-- *_secret_ref/العرض لمرة واحدة (migration 092). هذه الأعمدة وصفية فقط
-- (اسم/حالة/وقت/خطأ).
--
-- الحالات الممكنة في *_chr_status:
--   'pending'  — لم يُحاوَل الإنشاء بعد (CHR غير مُعَدّ في الإعدادات مثلًا)
--   'created'  — أُنشئ/حُدّث الحساب على CHR بنجاح عبر API
--   'failed'   — فُشلت محاولة الإنشاء (السبب في *_chr_error)

-- ── جانب نفق الإدارة (SSTP user على CHR) ──
ALTER TABLE nas_devices ADD COLUMN management_tunnel_chr_user TEXT NOT NULL DEFAULT '';
ALTER TABLE nas_devices ADD COLUMN management_tunnel_chr_status TEXT NOT NULL DEFAULT 'pending';
ALTER TABLE nas_devices ADD COLUMN management_tunnel_chr_created_at TEXT NOT NULL DEFAULT '';
ALTER TABLE nas_devices ADD COLUMN management_tunnel_chr_error TEXT NOT NULL DEFAULT '';

-- ── جانب نفق الترافيك (PPTP/L2TP user على CHR — IPsec النقي لا يحتاج ppp secret) ──
ALTER TABLE nas_devices ADD COLUMN traffic_tunnel_chr_user TEXT NOT NULL DEFAULT '';
ALTER TABLE nas_devices ADD COLUMN traffic_tunnel_chr_status TEXT NOT NULL DEFAULT 'pending';
ALTER TABLE nas_devices ADD COLUMN traffic_tunnel_chr_created_at TEXT NOT NULL DEFAULT '';
ALTER TABLE nas_devices ADD COLUMN traffic_tunnel_chr_error TEXT NOT NULL DEFAULT '';
