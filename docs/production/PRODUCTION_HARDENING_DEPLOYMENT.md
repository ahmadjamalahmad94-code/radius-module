# دليل النشر الإنتاجي المُتصلَّب — HobeRadius

**الإصدار:** 1.0 — 2026-06-08
**الحالة:** جاهز للنشر (يتطلب التحقق من كل خطوة)

---

## الأمان — قواعد ثابتة

| القاعدة | التفصيل |
|---------|---------|
| wg-mgmt **لا تحمل بيانات أبدًا** | لا NAT، لا default route، لا forwarding — فقط منافذ API/telemetry، سرعة 1-2 Mbps |
| wg-data **منفصلة تمامًا** | عدّادات مستقلة، كوتا مستقلة، interface مستقل |
| CHR nodes **لا ترى** عناوين RADIUS العملاء | تتصل بالوكيل المركزي فقط |
| المفاتيح الخاصة **لا تُخزَّن في لوحة التراخيص** | فقط مراجع vault |
| ServiceAllocation **من المدير فقط** | العملاء لا ينشئون ولا يعدّلون |
| منافذ RADIUS 1812/1813 **لا تُفتح للإنترنت** | عبر WireGuard فقط |
| كل مستأجر **realm منفرد** | تنسيق user@client5 |
| سياسة السعة: `<70%` مسموح / `70-85%` تحذير / `>85%` حجب | |

---

## البنية الثلاثية

```
لوحة التراخيص (radius-module-admin)
        ↕ HTTPS + HMAC-SHA256
وكيل RADIUS المركزي (radius-proxy)  ←→  CHR Nodes (MikroTik)
        ↕ WireGuard mgmt
RADIUS العميل (radius-module)
```

---

## متطلبات بيئة الإنتاج

### radius-module (Customer RADIUS Panel)

```bash
# ملف /opt/hoberadius/.env
HOBERADIUS_ENV=production
FLASK_SECRET=<random-64-chars>
HOBERADIUS_DB_PATH=/var/lib/hoberadius/radius.db
HOBERADIUS_ADMIN_SECRET=<shared-secret-with-admin-panel>
HOBERADIUS_ADMIN_BASE_URL=https://panel.hoberadius.com
HOBERADIUS_TENANT_ID=1
HOBERADIUS_RADIUS_SECRET=<shared-secret-with-mikrotik>
HOBERADIUS_WG_INTERFACE=wg-data
HOBERADIUS_WG_PEERS_DIR=/etc/wireguard/wg-data.d
HOBERADIUS_NO_SEED=1
```

### radius-module-admin (License Panel)

```bash
# ملف /opt/hoberadius-admin/.env
LICENSE_PANEL_ENV=production
FLASK_SECRET=<random-64-chars>
DATABASE_URL=postgresql://hoberadius:PASSWORD@localhost/hoberadius_admin
LICENSE_CHECK_HMAC_SECRET=<random-64-chars>
LICENSE_CHECK_SIGNATURE_REQUIRED=1
LICENSE_CHECK_ALLOW_UNSIGNED=0
SESSION_COOKIE_SECURE=1
RATE_LIMITS_ENABLED=1
CUSTOMER_VAULT_ENCRYPTION_KEY=<vault-key-from-secrets-manager>
LICENSE_ADMIN_PASSWORD=<strong-admin-password>
```

### radius-proxy (Central RADIUS Proxy)

```bash
# ملف /opt/hoberadius-proxy/.env
ADMIN_BASE_URL=https://panel.hoberadius.com
RADIUS_PROXY_SHARED_SECRET=<shared-secret-with-admin-panel>
PROXY_CHR_SECRET=<shared-secret-with-chr-nodes>
PROXY_LISTEN_HOST=0.0.0.0
PROXY_AUTH_PORT=1812
PROXY_ACCT_PORT=1813
PROXY_ROUTING_REFRESH=60
PROXY_FAIL_OPEN_CHR_ALLOWLIST=false
PROXY_ACCT_TIMEOUT_MODE=drop
```

---

## التحقق من اكتمال .env.example

```bash
# radius-module
cd radius-module && python scripts/check_env_completeness.py

# radius-module-admin
cd radius-module-admin && python scripts/check_env_completeness.py
```

---

## Systemd Services

### radius-module — enforce-expiry

الملف: `/etc/systemd/system/hobe-enforce-expiry.service`

```ini
[Unit]
Description=HobeRadius — Expiry & Quota Enforcer
After=network.target

[Service]
Type=oneshot
User=hoberadius
WorkingDirectory=/opt/hoberadius
EnvironmentFile=/opt/hoberadius/.env
ExecStart=/opt/hoberadius/venv/bin/flask enforce-expiry --tenant-id 1 --apply
StandardOutput=journal
StandardError=journal
SyslogIdentifier=hobe-enforce-expiry

ProtectSystem=strict
ReadWritePaths=/var/lib/hoberadius /etc/wireguard /etc/hoberadius
PrivateTmp=true
NoNewPrivileges=true
CapabilityBoundingSet=CAP_NET_ADMIN
AmbientCapabilities=CAP_NET_ADMIN
```

**⚠️ ملاحظة مهمة:** الأمر يتضمن `--apply`. بدونه يعمل في وضع dry-run فقط ولا يُطبّق أي تغييرات.

الملف: `/etc/systemd/system/hobe-enforce-expiry.timer`

```ini
[Unit]
Description=HobeRadius — Expiry & Quota Enforcer Timer
Requires=hobe-enforce-expiry.service

[Timer]
OnBootSec=2min
OnUnitActiveSec=15min
Persistent=true

[Install]
WantedBy=timers.target
```

### radius-module-admin — enforce-allocations

الملف: `/etc/systemd/system/hobe-enforce-allocations.service`

```ini
[Unit]
Description=HobeRadius Admin — Service Allocation Expiry Enforcer
After=network.target

[Service]
Type=oneshot
User=hoberadius
WorkingDirectory=/opt/hoberadius-admin
EnvironmentFile=/opt/hoberadius-admin/.env
ExecStart=/opt/hoberadius-admin/venv/bin/flask enforce-allocations --apply
StandardOutput=journal
StandardError=journal
SyslogIdentifier=hobe-enforce-allocations

ProtectSystem=strict
ReadWritePaths=/var/lib/hoberadius-admin
PrivateTmp=true
NoNewPrivileges=true
```

---

## وضع dry-run (اختبار قبل الإنتاج)

```bash
# اختبار expiry enforcer بدون كتابة
flask enforce-expiry --tenant-id 1
# أو بشكل صريح:
flask enforce-expiry --tenant-id 1 --dry-run

# اختبار allocation enforcer
flask enforce-allocations --dry-run
flask enforce-allocations --dry-run --customer-id 5

# التطبيق الفعلي
flask enforce-expiry --tenant-id 1 --apply
flask enforce-allocations --apply
flask enforce-allocations --apply --customer-id 5
```

---

## فحص الصحة قبل النشر

### 1. تحقق من البناء

```bash
# كل المشاريع
cd radius-module       && python -m compileall app tests && python -m pytest tests/ -q
cd radius-module-admin && python -m compileall app tests && python -m pytest tests/ -q
cd radius-proxy        && python -m compileall . tests   && python -m pytest tests/ -q
```

### 2. تحقق من .env.example

```bash
cd radius-module       && python scripts/check_env_completeness.py
cd radius-module-admin && python scripts/check_env_completeness.py
```

### 3. اختبار dry-run على خادم الإنتاج

```bash
# قبل تفعيل systemd timers — تأكد من النتائج
flask enforce-expiry --tenant-id 1 --dry-run
flask enforce-allocations --dry-run
```

### 4. تفعيل systemd timers

```bash
systemctl daemon-reload
systemctl enable --now hobe-enforce-expiry.timer
systemctl enable --now hobe-enforce-allocations.timer

# مراقبة
journalctl -u hobe-enforce-expiry -f
journalctl -u hobe-enforce-allocations -f
```

---

## radius-proxy — إعدادات الأمان

| الإعداد | القيمة الآمنة | الوصف |
|---------|--------------|-------|
| `PROXY_FAIL_OPEN_CHR_ALLOWLIST` | `false` | قائمة CHR المسموحة — مغلقة في الإنتاج |
| `PROXY_ACCT_TIMEOUT_MODE` | `drop` | timeout للـ Accounting → إسقاط (لا بيانات مصطنعة) |
| `PROXY_CHR_SECRET` | سر قوي | لا تترك القيمة الافتراضية `changeme-chr-secret` |

---

## نقاط الربط والإنتاج — ملخص سريع

| المكوّن | الحالة | ملاحظات |
|---------|--------|---------|
| radius-module | ✅ جاهز | enforce-expiry يتطلب --apply في systemd |
| radius-module-admin | ✅ جاهز | enforce-allocations يتطلب --apply في systemd |
| radius-proxy | ✅ جاهز | FAIL_OPEN=false في الإنتاج |
| WireGuard mgmt/data | ✅ منفصلان | wg-mgmt: API فقط / wg-data: بيانات مستخدمين |
| مفاتيح خاصة | ✅ آمن | لا تُخزَّن في DB — vault references فقط |
| RADIUS ports | ✅ آمن | 1812/1813 مقيَّدان بـ WireGuard — لا إنترنت مباشر |

---

## استجابة الحوادث

### expiry enforcer لا يعمل

```bash
# تحقق من الـ timer
systemctl status hobe-enforce-expiry.timer
systemctl status hobe-enforce-expiry.service

# شغّل يدويًا (dry-run أولاً)
flask enforce-expiry --tenant-id 1 --dry-run
flask enforce-expiry --tenant-id 1 --apply
```

### مزامنة الترخيص متوقفة

```bash
flask sync-license --tenant-id 1
journalctl -u hobe-sync-license -n 50
```

### وكيل RADIUS لا يستجيب

```bash
systemctl status hobe-radius-proxy
journalctl -u hobe-radius-proxy -n 100

# تحقق من قائمة CHR
curl -H "..." https://panel.hoberadius.com/api/proxy/routing-table
```

---

*وثيقة منشأة تلقائيًا — راجع CHANGES.md للتفاصيل الكاملة.*
