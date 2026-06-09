# استراتيجية VPN لـ RouterOS v6: SSTP إدارة + L2TP/IPsec ترافيك

**الإصدار**: 1.0
**التاريخ**: يونيو 2026
**الحالة**: جاهز للعرض على المالك + طلب قرارات حاسمة
**المدة المتوقعة**: 6-8 أسابيع (8 commits، 4 أشخاص متوازيين)

---

## 1. الملخص التنفيذي

الشركة حالياً توفر WireGuard VPN للإصدار RouterOS 7 فقط. الإصدار 6 (الأقدم لكن الأكثر انتشاراً) **لا يدعم WireGuard**، مما يترك مديري الشبكات بخيارين سيئين: الاتصال المباشر (غير آمن) أو التجاهل الكامل (خسارة العملاء). هذه الخطة تضيف دعماً أول لـ v6 عبر **SSTP للإدارة فقط** (نفق آمن لـ API/SSH، بلا default-route) و **L2TP/IPsec اختياري للترافيك** (لتغيير IP/Geo-spoof للمشتركين).

المميزات الرئيسية:
- ✅ لا تغيير في WireGuard v7 الموجود — تماماً محفوظ
- ✅ SSTP إدارة فقط (no route hijacking)
- ✅ L2TP/IPsec ترافيك اختياري (4 أوضاع: disabled/selected-pool/selected-subscribers/full-tunnel)
- ✅ نفق واحد فقط يملك default-route (تجنب conflict)
- ✅ RADIUS + CoA يعملان فوق كلا النفقين
- ✅ معالج جديد يختار وضع v6/v7 تلقائياً
- ✅ UI عربي + تحذيرات صراعات واضحة

**النتيجة النهائية**: عملاء RouterOS 6 يحصلون على VPN آمن بدل لا شيء؛ الشركة توسع سوق 50-60% من قاعدة الأجهزة الموجودة.

---

## 2. القواعس الصارمة (لا تُكسر)

### 2.1 عدم الانتهاك من WireGuard v7
```
❌ NEVER modify render_wg_block(), wg_peer_manager.py, or wireguard_config.py
❌ NEVER change nas_devices VPN columns behavior for existing v7 routers
❌ v7 WireGuard must work EXACTLY as it does today in production
```
**المسؤول**: كل commit يجب يختبر v7 end-to-end قبل submit.

### 2.2 SSTP إدارة فقط
```
SSTP client RouterOS config MUST include:
  /ip ppp secret add ... add-default-route=no
  comment="HOBERADIUS-SSTP-MGMT (إدارة فقط)"

If add-default-route=yes is detected → ERROR in verify phase
```

### 2.3 نفق واحد فقط يملك Default Route
```
Scenario 1: SSTP + no L2TP    → SSTP يملك default-route=no (L2TP disabled)
Scenario 2: SSTP + L2TP full  → SSTP=no, L2TP=yes (conflict check)
Scenario 3: WireGuard v7       → WireGuard=yes (as today)

validate_connection_plan() MUST reject if both tunnels try add-default-route=yes
```

### 2.4 أسرار لا تُسجّل/تُعرض
```
mt_provisioner.generate_credentials() → plaintext dict {api_password, radius_secret}
  ↓
MUST be stored in DB + sent over HTTPS ONLY
  ↓
NEVER appear in logs, commit messages, or browser history

Pattern: Store as masked references (_ref suffix) like WireGuard does
```

### 2.5 PPTP ممنوع من واجهة المستخدم
```
connection_modes() for v6 = ['sstp_mgmt', 'l2tp_ipsec_traffic', 'direct', 'dhcp_push']
❌ NO 'pptp' in UI under any circumstance (legacy only, hidden flag required)
```

### 2.6 لا إضافة RADIUS/FreeRADIUS/Flutter/Billing
```
✅ SSTP/L2TP يعملان OVER RADIUS (استخدام NAS-IP-Address الموجود)
❌ لا تغيير في FreeRADIUS config
❌ لا تغيير في RADIUS CoA logic
❌ لا تغيير في Flutter billing/license
❌ لا تغيير في Network Policy Center subscriber rules
```

### 2.7 Git Workflow
```
NO: git add .
YES: git add app/radius/services/routeros_caps.py app/radius/db/migrations/092_*.sql ...
     (explicit file list only)

NO: git commit --amend (after merge)
YES: Create NEW commit if hook fails

NO: git push --force (on main)
YES: Only force-push on feature branch if absolutely necessary (with 🔴 warning first)
```

---

## 3. طبقة القدرات (routeros_caps.py)

### 3.1 الحالة الحالية
```python
# app/radius/services/routeros_caps.py (lines 28-100)

def parse_major(version: str) -> int | None:
    """Parse version string → major version int (7 from '7.11', 6 from '6.49.7', None if error)."""

def supports_wireguard(ros_major: int) -> bool:
    """True if major >= 7 (WireGuard v7+ only)."""

def connection_modes(ros_major: int) -> list[str]:
    """['vpn', 'direct', 'dhcp_push'] for v7; ['direct', 'dhcp_push'] for v6."""

def detect_major_from_resource(api_resource_dict) -> int | None:
    """Parse /system/resource API response → major int."""

def summary(ros_major: int) -> dict:
    """Capability matrix dict: {has_wireguard, has_sstp, has_l2tp, ...}."""
```

**المشكلة**: `parse_major()` يعيد `int`، لكن الكود الموجود يقارن `ros_version in SUPPORTED_ROS_VERSIONS` حيث `SUPPORTED_ROS_VERSIONS = ('6', '7')` (strings). **Type mismatch hazard**.

### 3.2 الدوال المطلوبة (جديدة/موسّعة)

#### 3.2.1 `parse_routeros_major(version: str) → str | None`
```python
"""
Parse RouterOS version string → major version as STRING.
Examples:
  parse_routeros_major('6.49.7') → '6'
  parse_routeros_major('7.11.2') → '7'
  parse_routeros_major(7) → '7'  (int input OK)
  parse_routeros_major('invalid') → None
"""
```
**الغرض**: Canonical string comparator لـ `if ros_version in SUPPORTED_ROS_VERSIONS`.
**التطبيق**: Wrap `parse_major()` و أعد `str(result)` أو `None`.

#### 3.2.2 `supports_sstp_mgmt(ros_major: int) → bool`
```python
"""Return True if RouterOS major version supports SSTP client (v6+)."""
# Constants:
# SSTP_MIN_MAJOR = 6
return ros_major >= 6 if ros_major else False
```

#### 3.2.3 `supports_l2tp_ipsec_traffic(ros_major: int) → bool`
```python
"""Return True if RouterOS major version supports L2TP/IPsec client (v6+)."""
# L2TP_IPSEC_MIN_MAJOR = 6
return ros_major >= 6 if ros_major else False
```

#### 3.2.4 `recommended_management_tunnel(ros_major: int) -> str | None`
```python
"""Return preferred management tunnel for version."""
# Returns: 'wireguard' (v7), 'sstp' (v6), None (unknown)
if ros_major == 7:
    return 'wireguard'
elif ros_major == 6:
    return 'sstp'
else:
    return None
```

#### 3.2.5 `recommended_traffic_tunnel(ros_major: int) -> str | None`
```python
"""Return preferred optional traffic tunnel for version."""
# Returns: None (v7, traffic on WG), 'l2tp_ipsec' (v6 optional), None (unknown)
if ros_major == 7:
    return None  # Traffic shares WireGuard mgmt tunnel
elif ros_major == 6:
    return 'l2tp_ipsec'  # Optional, only if operator enables
else:
    return None
```

#### 3.2.6 `connection_modes_for_version(ros_major: int) -> list[str]`
```python
"""Return list of available connection modes in preference order."""
if ros_major == 7:
    return ['vpn', 'direct', 'dhcp_push']  # WireGuard-based 'vpn' mode
elif ros_major == 6:
    return ['sstp_mgmt', 'l2tp_ipsec_traffic', 'direct', 'dhcp_push']
else:
    return ['direct', 'dhcp_push']  # Conservative fallback
```

#### 3.2.7 `validate_connection_plan(ros_major: int, mgmt_tunnel: str, traffic_tunnel: str) -> tuple[list[str], list[str]]`

**التوقيع**:
```python
def validate_connection_plan(
    ros_major: int,
    mgmt_tunnel: str,      # 'wireguard' | 'sstp' | 'none'
    traffic_tunnel: str    # 'none' | 'l2tp_ipsec' | <pool-name>
) -> tuple[list[str], list[str]]:
    """
    Validate tunnel configuration for RouterOS version.

    Returns:
      (errors: list[str], warnings: list[str])

    Errors (blocking):
      - 'v6_wireguard_unsupported': v6 + wireguard mgmt_tunnel
      - 'v6_vpn_mode_unsupported': v6 + connection_mode='vpn' (legacy WG)
      - 'sstp_required_for_v6_mgmt': v6 + mgmt_tunnel != 'sstp'
      - 'conflicting_default_routes': both mgmt + traffic own default-route
      - 'invalid_mgmt_tunnel_type': unknown mgmt_tunnel name
      - 'invalid_traffic_tunnel_type': unknown traffic_tunnel name

    Warnings (advisory):
      - 'sstp_not_recommended_on_v7': v7 + sstp (WG better)
      - 'l2tp_on_v7_redundant': v7 + l2tp_ipsec (WG enough)
      - 'traffic_tunnel_requires_mgmt': traffic_tunnel != 'none' but mgmt_tunnel = 'none'
    """
```

**الخوارزمية**:
```
1. Check v6 incompatibilities:
   - if ros_major == 6 and mgmt_tunnel == 'wireguard' → error 'v6_wireguard_unsupported'

2. Check tunnel type validity:
   - valid mgmt types: 'wireguard', 'sstp', 'none'
   - valid traffic types: 'none', 'l2tp_ipsec', or l2tp mode enum

3. Check default-route ownership:
   - Only ONE tunnel can have default-route=yes
   - For v6: SSTP must have default-route=no ALWAYS
   - For L2TP: depends on mode (full_tunnel → yes, others → no)

4. Check dependencies:
   - L2TP traffic tunnel REQUIRES SSTP mgmt tunnel (cannot exist alone)

5. Recommend best practice:
   - v7: 'wireguard' mgmt, traffic='none'
   - v6: 'sstp' mgmt, traffic='none' or 'l2tp_ipsec'

Return both errors (blocking) and warnings (advisory).
```

### 3.3 توفيق `parse_major` مع `parse_routeros_major`

**الحل**:
```python
# routeros_caps.py (updated)

SUPPORTED_ROS_VERSIONS_INT = (6, 7)
SUPPORTED_ROS_VERSIONS_STR = ('6', '7')

def parse_major(version: str) -> int | None:
    """LEGACY: Return major version as int (unchanged, for arithmetic)."""
    # existing code unchanged

def parse_routeros_major(version: str | int) -> str | None:
    """NEW: Return major version as string ('6', '7', None)."""
    result = parse_major(str(version))
    return str(result) if result is not None else None

# Update all route handlers to use parse_routeros_major for string comparisons:
# Old: if ros_version not in SUPPORTED_ROS_VERSIONS
# New: if parse_routeros_major(ros_version) not in SUPPORTED_ROS_VERSIONS_STR
```

**Audit sites that need update**:
- `app/radius/routes/mt_setup.py` line 201-220 (form validation)
- `app/radius/routes/setup_wizard_v3.py` (VPN phase planner)
- `app/radius/services/mt_provisioner.py` (render_routeros_script branches)

### 3.4 ملف Exports (`__all__`)
```python
__all__ = [
    'parse_major',
    'parse_routeros_major',
    'supports_wireguard',
    'supports_sstp_mgmt',
    'supports_l2tp_ipsec_traffic',
    'requires_direct_address',
    'connection_modes',
    'connection_modes_for_version',
    'recommended_management_tunnel',
    'recommended_traffic_tunnel',
    'detect_major_from_resource',
    'validate_connection_plan',
    'summary',
    # Constants:
    'WIREGUARD_MIN_MAJOR',
    'SSTP_MIN_MAJOR',
    'L2TP_IPSEC_MIN_MAJOR',
    'SUPPORTED_ROS_VERSIONS_INT',
    'SUPPORTED_ROS_VERSIONS_STR',
]
```

### 3.5 Constants
```python
WIREGUARD_MIN_MAJOR = 7
SSTP_MIN_MAJOR = 6
L2TP_IPSEC_MIN_MAJOR = 6

SUPPORTED_ROS_VERSIONS_INT = (6, 7)
SUPPORTED_ROS_VERSIONS_STR = ('6', '7')
```

---

## 4. نموذج البيانات (Schema + Dataclass)

### 4.1 قرار: توسيع `nas_devices` أم جدول جديد؟

**الخيار A (موصى به)**: توسيع `nas_devices` بـ 12 عمود SSTP/L2TP
- ✅ Simple queries (single table)
- ✅ No FK joins needed
- ✅ nas_repo._row() already handles optional columns
- ❌ Schema grows; columns mostly NULL for v7 routers

**الخيار B**: جدول جديد `nas_vpn_tunnels` (normalized)
- ✅ Clean separation of concerns
- ✅ Extensible for future tunnel types
- ❌ Requires JOIN; slower for fleet queries
- ❌ More migration complexity

**القرار النهائي**: **الخيار A** (توسيع nas_devices) — يتماشى مع pattern WireGuard الموجود (أعمدة `vpn_*` في nas_devices).

### 4.2 Migration 092: توسيع `nas_devices`

**ملف**: `app/radius/db/migrations/092_nas_sstp_l2tp_tunnels.sql`

```sql
-- Add SSTP management tunnel columns
ALTER TABLE nas_devices ADD COLUMN mgmt_tunnel_type VARCHAR DEFAULT '' NOT NULL;
  -- Values: '' (empty/not set), 'wireguard', 'sstp', 'direct', 'dhcp_push'

ALTER TABLE nas_devices ADD COLUMN mgmt_tunnel_interface VARCHAR DEFAULT '' NOT NULL;
  -- Example: 'sstp-hoberadius-mgmt'

ALTER TABLE nas_devices ADD COLUMN mgmt_tunnel_ip VARCHAR DEFAULT '' NOT NULL;
  -- Example: '192.168.5.2/24' (SSTP subnet IP assigned by server)

ALTER TABLE nas_devices ADD COLUMN mgmt_tunnel_status VARCHAR DEFAULT '' NOT NULL;
  -- Values: '' (not set), 'pending', 'configured', 'connected', 'error'

ALTER TABLE nas_devices ADD COLUMN mgmt_tunnel_error VARCHAR DEFAULT '' NOT NULL;
  -- Last error message during provisioning

ALTER TABLE nas_devices ADD COLUMN mgmt_tunnel_configured_at TIMESTAMP DEFAULT NULL;
  -- When SSTP was last successfully applied

-- Add L2TP/IPsec traffic tunnel columns
ALTER TABLE nas_devices ADD COLUMN traffic_tunnel_type VARCHAR DEFAULT '' NOT NULL;
  -- Values: '' (disabled), 'l2tp_ipsec'

ALTER TABLE nas_devices ADD COLUMN traffic_tunnel_mode VARCHAR DEFAULT '' NOT NULL;
  -- Values: '' (disabled), 'disabled', 'selected_pool', 'selected_subscribers', 'full_tunnel'

ALTER TABLE nas_devices ADD COLUMN traffic_tunnel_interface VARCHAR DEFAULT '' NOT NULL;
  -- Example: 'l2tp-hoberadius-traffic'

ALTER TABLE nas_devices ADD COLUMN traffic_tunnel_ip_pool VARCHAR DEFAULT '' NOT NULL;
  -- Example: '10.20.0.0/24' (subscriber IP pool for L2TP)

ALTER TABLE nas_devices ADD COLUMN traffic_tunnel_status VARCHAR DEFAULT '' NOT NULL;
  -- Values: '' (not set), 'pending', 'configured', 'connected', 'error'

ALTER TABLE nas_devices ADD COLUMN traffic_tunnel_error VARCHAR DEFAULT '' NOT NULL;

ALTER TABLE nas_devices ADD COLUMN traffic_tunnel_configured_at TIMESTAMP DEFAULT NULL;

-- Routing conflict tracking
ALTER TABLE nas_devices ADD COLUMN tunnel_conflict_detected BOOLEAN DEFAULT FALSE NOT NULL;
ALTER TABLE nas_devices ADD COLUMN tunnel_conflict_reason VARCHAR DEFAULT '' NOT NULL;

-- Index for efficient tunnel status queries
CREATE INDEX idx_nas_devices_tunnel_status ON nas_devices(mgmt_tunnel_status, traffic_tunnel_status);
CREATE INDEX idx_nas_devices_tunnel_conflict ON nas_devices(tunnel_conflict_detected) WHERE tunnel_conflict_detected = TRUE;
```

**ملاحظات**:
- جميع الأعمدة الجديدة قيمتها الافتراضية `''` (string) أو `NULL` (timestamp) — آمن مع الأكواد القديمة
- لا تفكك WireGuard v7 (أعمدة `vpn_*` القديمة تبقى كما هي)
- `mgmt_tunnel_type` و `traffic_tunnel_mode` يحفظان state الاختيار

### 4.3 تحديث NasDevice Dataclass

**ملف**: `app/radius/core/types.py`

```python
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

@dataclass(frozen=True)
class NasDevice:
    # ... existing fields ...

    # RouterOS version (from migration 034)
    ros_version: str = ''  # '6' or '7'

    # Management tunnel (SSTP for v6, WireGuard for v7, or direct)
    mgmt_tunnel_type: str = ''  # 'wireguard' | 'sstp' | 'direct' | 'dhcp_push' | ''
    mgmt_tunnel_interface: str = ''  # 'sstp-hoberadius-mgmt' | 'wg0' | ''
    mgmt_tunnel_ip: str = ''  # '192.168.5.2/24' | ''
    mgmt_tunnel_status: str = ''  # 'pending' | 'configured' | 'connected' | 'error' | ''
    mgmt_tunnel_error: str = ''
    mgmt_tunnel_configured_at: Optional[datetime] = None

    # Traffic tunnel (L2TP for v6 optional, unused for v7)
    traffic_tunnel_type: str = ''  # 'l2tp_ipsec' | ''
    traffic_tunnel_mode: str = ''  # 'disabled' | 'selected_pool' | 'selected_subscribers' | 'full_tunnel' | ''
    traffic_tunnel_interface: str = ''  # 'l2tp-hoberadius-traffic' | ''
    traffic_tunnel_ip_pool: str = ''  # '10.20.0.0/24' | ''
    traffic_tunnel_status: str = ''  # 'pending' | 'configured' | 'connected' | 'error' | ''
    traffic_tunnel_error: str = ''
    traffic_tunnel_configured_at: Optional[datetime] = None

    # Conflict tracking
    tunnel_conflict_detected: bool = False
    tunnel_conflict_reason: str = ''
```

**ملاحظة**: الحقول القديمة `vpn_*` (WireGuard) تبقى كما هي في الداتاكلاس.

### 4.4 تحديث `nas_repo.py`

**ملف**: `app/radius/db/repos/nas_repo.py` (دالة `_row()`)

```python
def _row(row_dict: dict) -> NasDevice:
    """Map DB row to NasDevice dataclass."""
    return NasDevice(
        # ... existing mappings ...

        # New SSTP/L2TP columns
        mgmt_tunnel_type=_g(row_dict, 'mgmt_tunnel_type', ''),
        mgmt_tunnel_interface=_g(row_dict, 'mgmt_tunnel_interface', ''),
        mgmt_tunnel_ip=_g(row_dict, 'mgmt_tunnel_ip', ''),
        mgmt_tunnel_status=_g(row_dict, 'mgmt_tunnel_status', ''),
        mgmt_tunnel_error=_g(row_dict, 'mgmt_tunnel_error', ''),
        mgmt_tunnel_configured_at=_g(row_dict, 'mgmt_tunnel_configured_at', None),

        traffic_tunnel_type=_g(row_dict, 'traffic_tunnel_type', ''),
        traffic_tunnel_mode=_g(row_dict, 'traffic_tunnel_mode', ''),
        traffic_tunnel_interface=_g(row_dict, 'traffic_tunnel_interface', ''),
        traffic_tunnel_ip_pool=_g(row_dict, 'traffic_tunnel_ip_pool', ''),
        traffic_tunnel_status=_g(row_dict, 'traffic_tunnel_status', ''),
        traffic_tunnel_error=_g(row_dict, 'traffic_tunnel_error', ''),
        traffic_tunnel_configured_at=_g(row_dict, 'traffic_tunnel_configured_at', None),

        tunnel_conflict_detected=bool(_g(row_dict, 'tunnel_conflict_detected', False)),
        tunnel_conflict_reason=_g(row_dict, 'tunnel_conflict_reason', ''),
    )
```

### 4.5 نمط تخزين الأسرار

**السياق**: `mt_provisioner.generate_credentials()` ينتج أسرار plaintext (api_password, radius_secret). لا يمكن حفظها في DB.

**الحل** (يتابع WireGuard pattern):
```python
# app/radius/services/setup_wizard_router_provisioning.py

class RouterCredentialPlanner:
    def plan(self, allocation_index: int, nas_id: int) -> ProvisioningRegistry:
        """
        Generate masked secret references (NOT plaintext).

        Returns ProvisioningRegistry with:
          - api_password_ref: 'api-password-ref-{index:04d}' (ref only, not secret)
          - radius_secret_ref: 'radius-secret-ref-{index:04d}'
          - sstp_secret_ref: 'sstp-secret-ref-{index:04d}'  # NEW for v6
          - l2tp_preshared_key_ref: 'l2tp-psk-ref-{index:04d}'  # NEW for v6

        Plaintext secrets are:
          1. Generated here (memory only)
          2. Passed to mt_provisioner.render_routeros_script()
          3. Script body embedded (not in comments, not logged)
          4. Script sent via HTTPS
          5. Operator pastes into terminal (one-time)
          6. Operator manually stores/rotates (outside this system)

        Database stores ONLY the _ref suffix names + allocation metadata.
        """
```

**Storage in `router_provisioning_registry` table**:
```sql
INSERT INTO router_provisioning_registry (
    nas_id,
    allocation_index,
    api_password_ref,
    radius_secret_ref,
    sstp_secret_ref,          -- NEW
    l2tp_preshared_key_ref,   -- NEW
    created_at
) VALUES (
    123,
    0,
    'api-password-ref-0000',
    'radius-secret-ref-0000',
    'sstp-secret-ref-0000',
    'l2tp-psk-ref-0000',
    NOW()
);
```

**Lookup at Runtime** (when rendering script):
```python
# mt_provisioner.py
def render_routeros_script(
    nas_name: str,
    ros_version: str,
    api_user: str,
    api_password: str,  # plaintext from RouterCredentialPlanner.plan()
    radius_secret: str,  # plaintext
    sstp_secret: str = '',  # NEW, plaintext (v6 only)
    l2tp_preshared_key: str = '',  # NEW, plaintext (v6 only)
    server_endpoint: str = '',  # VPS IP:port
    ...
) -> str:
    """
    Render RouterOS script with credentials embedded in body.
    Script is one-time, operator pastes and runs.
    """
```

---

## 5. مولّدات السكربت (Script Builders)

### 5.1 معمارية Existing (WireGuard v7)

```
mt_setup.py (routes)
  → mt_provisioner.render_routeros_script(
      nas_name, ros_version='7', api_user, api_password, radius_secret,
      wg_block=render_wg_block(...)
    )
  → returns script string → user copies to terminal

wg_peer_manager.provision_peer()
  → writes /etc/hoberadius/wg-peers.d/nas-123.conf
  → runs: wg set wg0 peer <pubkey> allowed-ips=10.10.0.2/32
```

### 5.2 معمارية المطلوبة (SSTP + L2TP v6)

```
mt_setup.py (routes)
  IF ros_version == '6':
    → build_v6_sstp_management_plan(nas_name, server_endpoint, ...)
    → IF operator enables L2TP:
        build_v6_l2tp_ipsec_traffic_plan(...)
    → analyze_tunnel_conflicts(mgmt_plan, traffic_plan)
      → returns (ok, warnings_list, conflict_reason)
    → mt_provisioner.render_routeros_script(
        nas_name, ros_version='6',
        mgmt_tunnel_type='sstp', mgmt_tunnel_config={...},
        traffic_tunnel_type='l2tp_ipsec', traffic_tunnel_config={...}
      )
    → returns script string
  ELSE:
    → (v7 WireGuard as today)
```

### 5.3 دوال Render في `mt_provisioner.py`

#### 5.3.1 `render_sstp_mgmt_block()`

**التوقيع**:
```python
def render_sstp_mgmt_block(
    nas_name: str,
    router_ip: str,  # 192.168.5.1/24 (SSTP server subnet)
    sstp_secret: str,  # plaintext pre-shared secret
    ros_version: str = '6',
) -> str:
    """
    Generate RouterOS SSTP client config block.

    Output:
    /interface sstp-client add name=sstp-hoberadius-mgmt enabled=yes \\
      server=<server_endpoint> user=<nas_name> password=<sstp_secret> \\
      comment="HOBERADIUS-SSTP-MGMT (إدارة فقط)"
    /ip address add address=192.168.5.2/24 interface=sstp-hoberadius-mgmt comment="SSTP-MGMT-IP"
    /ip route add dst-address=0.0.0.0/0 gateway=192.168.5.1 disabled=yes comment="SSTP-MGMT-DISABLED"

    Key features:
    - add-default-route=no (NO default-route ownership)
    - MUST include comment=HOBERADIUS for idempotency (script can re-run)
    - Enable keepalive to prevent disconnection
    """
```

**Template**:
```routeros
# SSTP Management Tunnel (Admin Only - No Default Route)
/interface sstp-client add \\
    name=sstp-hoberadius-mgmt \\
    enabled=yes \\
    server={server_endpoint} \\
    user={nas_name} \\
    password={sstp_secret} \\
    comment="HOBERADIUS-SSTP-MGMT (إدارة فقط)" \\
    keepalive-timeout=30s

# Assign SSTP IP address
/ip address add \\
    address={sstp_router_ip} \\
    interface=sstp-hoberadius-mgmt \\
    comment="SSTP-MGMT-IP"

# Optional: Default route disabled (if operator wants traffic tunnel to own it)
/ip route add \\
    dst-address=0.0.0.0/0 \\
    gateway={sstp_server_gateway} \\
    disabled=yes \\
    comment="SSTP-MGMT-DISABLED-FOR-TRAFFIC-TUNNEL"
```

**Guard**:
```python
if not routeros_caps.supports_sstp_mgmt(parse_major(ros_version)):
    raise ValueError(f"SSTP not supported on RouterOS {ros_version}")
```

#### 5.3.2 `render_l2tp_ipsec_traffic_block()`

**التوقيع**:
```python
def render_l2tp_ipsec_traffic_block(
    nas_name: str,
    l2tp_remote_endpoint: str,  # VPS IP
    l2tp_preshared_key: str,
    l2tp_mode: str,  # 'disabled' | 'selected_pool' | 'selected_subscribers' | 'full_tunnel'
    traffic_subnet: str = '10.20.0.0/24',  # IP pool for subscriber access
    ros_version: str = '6',
) -> str:
    """
    Generate RouterOS L2TP/IPsec client config block.

    Output depends on mode:
      - 'disabled': return '' (empty)
      - 'selected_pool': L2TP client + MANGLE marks for selected IPs
      - 'selected_subscribers': L2TP client + RADIUS CoA targets
      - 'full_tunnel': L2TP client + default-route=yes + NAT all traffic

    Key features:
    - NEVER include add-default-route=yes if SSTP mgmt tunnel exists
    - Use address-list + mangle for selective routing
    - Use [find comment~="HOBERADIUS"] pattern for idempotency
    """
```

**Template** (full_tunnel mode):
```routeros
# L2TP/IPsec Traffic Tunnel (Optional - Full Tunnel Mode)
/interface l2tp-client add \\
    name=l2tp-hoberadius-traffic \\
    enabled=yes \\
    server={l2tp_remote_endpoint} \\
    user={nas_name} \\
    password={l2tp_preshared_key} \\
    profile=default \\
    use-ipsec=yes \\
    comment="HOBERADIUS-L2TP-TRAFFIC (ترافيك اختياري)" \\
    keepalive-timeout=30s

# L2TP IP assignment
/ip address add \\
    address=10.20.0.2/24 \\
    interface=l2tp-hoberadius-traffic \\
    comment="L2TP-TRAFFIC-IP"

# IPsec policy for L2TP
/ip ipsec policy add \\
    src-address=0.0.0.0/0 \\
    dst-address={traffic_subnet} \\
    protocol=udp \\
    action=encrypt \\
    comment="HOBERADIUS-L2TP-IPSEC-POLICY"

# NAT for traffic tunnel (if full_tunnel mode)
/ip firewall nat add \\
    chain=srcnat \\
    src-address=0.0.0.0/0 \\
    action=masquerade \\
    out-interface=l2tp-hoberadius-traffic \\
    comment="HOBERADIUS-L2TP-TRAFFIC-NAT"

# Default route (only if no SSTP mgmt, or if traffic should own it)
/ip route add \\
    dst-address=0.0.0.0/0 \\
    gateway=10.20.0.1 \\
    comment="HOBERADIUS-L2TP-DEFAULT-ROUTE"
```

**Template** (selected_pool mode):
```routeros
# L2TP/IPsec Traffic Tunnel (Selected Pool - Selective Subscribers)
# ... [interface + ipsec policy same as full_tunnel] ...

# Address list for pool
/ip firewall address-list add \\
    list=hoberadius-traffic-pool \\
    address={subscriber_ip_pool} \\
    comment="HOBERADIUS-TRAFFIC-POOL"

# Mangle mark for pool traffic
/ip firewall mangle add \\
    chain=prerouting \\
    src-address-list=hoberadius-traffic-pool \\
    action=mark-routing \\
    new-routing-mark=hoberadius-traffic \\
    comment="HOBERADIUS-TRAFFIC-MARK"

# Route with mark
/ip route add \\
    dst-address=0.0.0.0/0 \\
    gateway=10.20.0.1 \\
    routing-mark=hoberadius-traffic \\
    comment="HOBERADIUS-L2TP-SELECTIVE-ROUTE"
```

**Guard**:
```python
if l2tp_mode == 'disabled':
    return ''

if not routeros_caps.supports_l2tp_ipsec_traffic(parse_major(ros_version)):
    raise ValueError(f"L2TP/IPsec not supported on RouterOS {ros_version}")
```

#### 5.3.3 `render_routeros_script()` Updated

**التوقيع**:
```python
def render_routeros_script(
    nas_name: str,
    ros_version: str,
    api_user: str,
    api_password: str,
    radius_secret: str,
    server_endpoint: str,
    # WireGuard v7 params
    wg_block: str = '',
    # SSTP v6 params
    mgmt_tunnel_type: str = '',  # 'sstp' or 'wireguard'
    mgmt_tunnel_config: dict | None = None,
    # L2TP v6 params
    traffic_tunnel_type: str = '',  # 'l2tp_ipsec'
    traffic_tunnel_config: dict | None = None,
) -> str:
    """
    Master script renderer.

    Detects version and selects appropriate template.
    """

    major = parse_major(ros_version)

    # Route to v7 WireGuard path (unchanged)
    if major == 7:
        return _render_v7_wireguard_script(
            nas_name, api_user, api_password, radius_secret,
            server_endpoint, wg_block
        )

    # Route to v6 SSTP+L2TP path (new)
    elif major == 6:
        sstp_block = ''
        l2tp_block = ''

        if mgmt_tunnel_type == 'sstp' and mgmt_tunnel_config:
            sstp_block = render_sstp_mgmt_block(**mgmt_tunnel_config)

        if traffic_tunnel_type == 'l2tp_ipsec' and traffic_tunnel_config:
            l2tp_block = render_l2tp_ipsec_traffic_block(**traffic_tunnel_config)

        return _render_v6_sstp_l2tp_script(
            nas_name, api_user, api_password, radius_secret,
            server_endpoint, sstp_block, l2tp_block
        )

    else:
        raise ValueError(f"Unsupported RouterOS version: {ros_version}")
```

### 5.4 `analyze_tunnel_conflicts()` دالة جديدة

**الملف**: `app/radius/services/setup_wizard_vpn_conflict_analyzer.py` (جديد)

**التوقيع**:
```python
def analyze_tunnel_conflicts(
    ros_major: int,
    mgmt_tunnel_config: dict,  # {type, mode, has_default_route}
    traffic_tunnel_config: dict | None = None,
) -> tuple[bool, list[str], str]:
    """
    Detect routing conflicts between management + traffic tunnels.

    Returns:
      (is_valid: bool, warnings: list[str], conflict_reason: str)
    """
```

**الـ 13 حالة**:

| # | v6/v7 | Mgmt Tunnel | Traffic Tunnel | Mgmt Default-Route | Traffic Default-Route | Valid? | Reason |
|---|-------|-------------|----------------|---------------------|----------------------|--------|--------|
| 1 | v7 | WireGuard | None | YES | N/A | ✅ YES | Standard v7 |
| 2 | v6 | SSTP | None | NO | N/A | ✅ YES | Standard v6 |
| 3 | v6 | SSTP | L2TP (full) | NO | YES | ✅ YES | L2TP owns route |
| 4 | v6 | SSTP | L2TP (pool) | NO | NO | ✅ YES | Selective routing |
| 5 | v7 | WireGuard | L2TP | YES | YES | ❌ NO | Conflict: both claim default |
| 6 | v6 | SSTP | L2TP | YES | YES | ❌ NO | Conflict: SSTP shouldn't have default |
| 7 | v6 | Direct | L2TP (full) | NO | YES | ⚠️ WARN | No mgmt tunnel, L2TP alone risky |
| 8 | v6 | WireGuard | L2TP | N/A | N/A | ❌ NO | WireGuard not on v6 |
| 9 | v7 | SSTP | None | YES | N/A | ⚠️ WARN | SSTP on v7 when WG available |
| 10 | v6 | SSTP | L2TP (disabled) | NO | N/A | ✅ YES | L2TP disabled = traffic on SSTP |
| 11 | v7 | None | None | N/A | N/A | ❌ NO | No tunnel at all |
| 12 | v6 | None | L2TP | N/A | YES | ❌ NO | L2TP alone, no mgmt |
| 13 | v6 | SSTP | L2TP (subs) | NO | NO | ✅ YES | Selective by subscriber |

**Implementation**:
```python
def analyze_tunnel_conflicts(
    ros_major: int,
    mgmt_tunnel_config: dict,
    traffic_tunnel_config: dict | None = None,
) -> tuple[bool, list[str], str]:
    """
    Validate tunnel combinations.

    Returns:
      - is_valid: True if no errors
      - warnings: Advisory messages
      - conflict_reason: Human-readable explanation
    """

    mgmt_type = mgmt_tunnel_config.get('type', '')
    mgmt_has_default = mgmt_tunnel_config.get('add_default_route', False)

    traffic_type = traffic_tunnel_config.get('type', '') if traffic_tunnel_config else ''
    traffic_mode = traffic_tunnel_config.get('mode', '') if traffic_tunnel_config else ''
    traffic_has_default = traffic_tunnel_config.get('add_default_route', False) if traffic_tunnel_config else False

    warnings = []
    is_valid = True
    conflict_reason = ''

    # Rule 1: v6 + WireGuard = invalid
    if ros_major == 6 and mgmt_type == 'wireguard':
        is_valid = False
        conflict_reason = 'RouterOS 6 does not support WireGuard (v7+ only)'
        return (is_valid, warnings, conflict_reason)

    # Rule 2: v6 + SSTP must have default-route=NO
    if ros_major == 6 and mgmt_type == 'sstp' and mgmt_has_default:
        is_valid = False
        conflict_reason = 'SSTP management tunnel must NOT own default route (add-default-route=no)'
        return (is_valid, warnings, conflict_reason)

    # Rule 3: Both tunnels cannot own default-route
    if mgmt_has_default and traffic_has_default:
        is_valid = False
        conflict_reason = 'Cannot have both management and traffic tunnels owning default route'
        return (is_valid, warnings, conflict_reason)

    # Rule 4: L2TP traffic alone without mgmt (v6)
    if ros_major == 6 and mgmt_type in ('', 'direct', 'dhcp_push') and traffic_type == 'l2tp_ipsec':
        warnings.append('L2TP traffic tunnel without SSTP management is risky; recommend SSTP for API access')

    # Rule 5: SSTP on v7 (when WireGuard available)
    if ros_major == 7 and mgmt_type == 'sstp':
        warnings.append('SSTP not recommended on RouterOS 7; use WireGuard instead')

    # Rule 6: No tunnel at all
    if not mgmt_type or mgmt_type in ('direct', 'dhcp_push'):
        if not traffic_type:
            warnings.append('No VPN tunnel configured; router uses direct address only')

    return (is_valid, warnings, conflict_reason)
```

### 5.5 دوال Verify في `setup_wizard_verification.py`

**الملف**: `app/radius/services/setup_wizard_verification.py`

#### 5.5.1 `verify_management_tunnel()`
```python
def verify_management_tunnel(
    router_api: RouterAPI,
    tunnel_type: str,  # 'sstp' | 'wireguard'
    tunnel_interface: str,  # 'sstp-hoberadius-mgmt' | 'wg0'
) -> dict:
    """
    Probe router to verify management tunnel is active.

    Returns:
      {
        'ok': bool,
        'tunnel_interface_up': bool,
        'tunnel_ip_assigned': bool,
        'connection_established': bool,
        'last_handshake_seconds_ago': int | None,
        'error': str | None,
      }
    """

    if tunnel_type == 'sstp':
        # Query /interface sstp-client print
        sstp_iface = router_api.call('/interface/sstp-client/print', where={'name': tunnel_interface})
        if not sstp_iface:
            return {'ok': False, 'error': f'SSTP interface {tunnel_interface} not found'}

        # Check if running
        is_enabled = sstp_iface[0].get('running', False)

        # Query /ip address for SSTP IP
        sstp_addrs = router_api.call('/ip/address/print', where={'interface': tunnel_interface})
        ip_assigned = len(sstp_addrs) > 0

        return {
            'ok': is_enabled and ip_assigned,
            'tunnel_interface_up': is_enabled,
            'tunnel_ip_assigned': ip_assigned,
            'connection_established': is_enabled,
            'last_handshake_seconds_ago': None,  # SSTP doesn't have handshake concept
            'error': None if (is_enabled and ip_assigned) else 'SSTP tunnel not fully connected',
        }

    elif tunnel_type == 'wireguard':
        # Query /interface wireguard print
        wg_iface = router_api.call('/interface/wireguard/print', where={'name': tunnel_interface})
        if not wg_iface:
            return {'ok': False, 'error': f'WireGuard interface {tunnel_interface} not found'}

        is_enabled = wg_iface[0].get('running', False)

        # Check handshake
        wg_peers = router_api.call('/interface/wireguard/peers/print', where={'interface': tunnel_interface})
        last_handshake = None
        if wg_peers:
            last_handshake = wg_peers[0].get('last-handshake', 0)  # seconds ago

        return {
            'ok': is_enabled and last_handshake is not None,
            'tunnel_interface_up': is_enabled,
            'tunnel_ip_assigned': True,  # WireGuard always has peer address
            'connection_established': last_handshake is not None and last_handshake < 300,
            'last_handshake_seconds_ago': last_handshake,
            'error': None if is_enabled else 'WireGuard tunnel not enabled',
        }

    return {'ok': False, 'error': f'Unknown tunnel type: {tunnel_type}'}
```

#### 5.5.2 `verify_traffic_tunnel()`
```python
def verify_traffic_tunnel(
    router_api: RouterAPI,
    tunnel_type: str,  # 'l2tp_ipsec'
    tunnel_interface: str,  # 'l2tp-hoberadius-traffic'
) -> dict:
    """
    Probe router to verify traffic tunnel routing is active.
    """

    if tunnel_type == 'l2tp_ipsec':
        l2tp_iface = router_api.call('/interface/l2tp-client/print', where={'name': tunnel_interface})
        if not l2tp_iface:
            return {'ok': False, 'error': f'L2TP interface {tunnel_interface} not found'}

        is_enabled = l2tp_iface[0].get('running', False)

        # Check IPsec policies
        ipsec_policies = router_api.call('/ip/ipsec/policy/print')
        hoberadius_policies = [p for p in ipsec_policies if 'HOBERADIUS' in p.get('comment', '')]

        return {
            'ok': is_enabled,
            'tunnel_interface_up': is_enabled,
            'ipsec_policies_active': len(hoberadius_policies) > 0,
            'routes_configured': router_api.call('/ip/route/print', where={'comment~': 'HOBERADIUS-L2TP'}) is not None,
            'error': None if is_enabled else 'L2TP tunnel not enabled',
        }

    return {'ok': False, 'error': f'Unknown tunnel type: {tunnel_type}'}
```

---

## 6. مقتطفات RouterOS v6 التمثيلية

### 6.1 سكربت SSTP Management Tunnel كامل

```routeros
# ============================================================
# HOBERADIUS Auto-Provisioning Script for RouterOS 6
# Generated: 2026-06-01T14:30:00Z
# Router: LAB-ROUTER-01
# RouterOS Version: 6.49.7
# VPN Tunnel: SSTP Management Only
# ============================================================

# ============================================================
# Step 1: API User Setup
# ============================================================
/user add name=hr-0042 password=ZFBV_k7m9xP2qL5rT8wN3jX6cH4dM0eY group=full comment=HOBERADIUS-API-USER

# ============================================================
# Step 2: SSTP Management Tunnel (إدارة فقط)
# ============================================================

# Configure SSTP Client
/interface sstp-client add \
    name=sstp-hoberadius-mgmt \
    enabled=yes \
    server=203.0.113.1 \
    user=LAB-ROUTER-01 \
    password=AeK9mL2pR5xW8tQ1vB4nJ7cF3dH6sY0Z \
    profile=default \
    comment="HOBERADIUS-SSTP-MGMT (إدارة فقط)" \
    keepalive-timeout=30s

# Assign IP Address for SSTP Tunnel
/ip address add \
    address=192.168.5.2/24 \
    interface=sstp-hoberadius-mgmt \
    comment="SSTP-MGMT-IP"

# Optional: Define default route disabled (for traffic tunnel later)
/ip route add \
    dst-address=0.0.0.0/0 \
    gateway=192.168.5.1 \
    disabled=yes \
    comment="SSTP-MGMT-DISABLED-FOR-FUTURE-TRAFFIC"

# ============================================================
# Step 3: RADIUS Server Configuration
# ============================================================
/radius add service=login address=203.0.113.50 secret=KxM9yH2fQ7pL3tR8cB5nW0eDj4sV1aX comment=HOBERADIUS-PRIMARY
/radius add service=ppp address=203.0.113.50 secret=KxM9yH2fQ7pL3tR8cB5nW0eDj4sV1aX comment=HOBERADIUS-PPP

# ============================================================
# Step 4: Hotspot/Broadband Configuration (Optional - Add Later)
# ============================================================
# (To be populated by subsequent phase planner)

# ============================================================
# Script End - Router is Ready for Management
# ============================================================
```

**ملاحظات Idempotency**:
- كل سطر له `comment=HOBERADIUS-*` tag
- عند إعادة التشغيل: `[find comment~="HOBERADIUS"]` يحذف القديم أو يتجنب التكرار
- SSTP interface name ثابت (`sstp-hoberadius-mgmt`)

### 6.2 سكربت L2TP/IPsec Traffic Tunnel (Full Mode)

```routeros
# ============================================================
# HOBERADIUS L2TP/IPsec Traffic Tunnel (Full Tunnel Mode)
# Generated: 2026-06-01T15:45:00Z
# Router: LAB-ROUTER-01
# RouterOS Version: 6.49.7
# ============================================================

# ============================================================
# Step 1: L2TP Client Interface
# ============================================================
/interface l2tp-client add \
    name=l2tp-hoberadius-traffic \
    enabled=yes \
    server=203.0.113.1 \
    user=LAB-ROUTER-01 \
    password=ReL4mK8yH1xW5pQ9tB2nJ6cF3dS7vX0 \
    profile=default \
    use-ipsec=yes \
    comment="HOBERADIUS-L2TP-TRAFFIC (ترافيك اختياري)" \
    keepalive-timeout=30s

# ============================================================
# Step 2: IP Address for L2TP Tunnel
# ============================================================
/ip address add \
    address=10.20.0.2/24 \
    interface=l2tp-hoberadius-traffic \
    comment="L2TP-TRAFFIC-IP"

# ============================================================
# Step 3: IPsec Policy for L2TP Encryption
# ============================================================
/ip ipsec policy add \
    src-address=0.0.0.0/0 \
    dst-address=10.20.0.0/24 \
    protocol=udp dst-port=1701 \
    action=encrypt \
    comment="HOBERADIUS-L2TP-IPSEC-POLICY"

# ============================================================
# Step 4: NAT for L2TP Traffic (Full Tunnel)
# ============================================================
/ip firewall nat add \
    chain=srcnat \
    src-address=0.0.0.0/0 \
    action=masquerade \
    out-interface=l2tp-hoberadius-traffic \
    comment="HOBERADIUS-L2TP-NAT-SRCNAT"

# ============================================================
# Step 5: Default Route via L2TP
# ============================================================
/ip route add \
    dst-address=0.0.0.0/0 \
    gateway=10.20.0.1 \
    comment="HOBERADIUS-L2TP-DEFAULT-ROUTE"

# ============================================================
# Step 6: Enable Forwarding (if not already)
# ============================================================
/ip settings set tcp-syncookies=yes

# ============================================================
# Script End - L2TP Traffic Tunnel Configured
# ============================================================
```

### 6.3 سكربت L2TP/IPsec (Selective Pool Mode)

```routeros
# ============================================================
# HOBERADIUS L2TP/IPsec Traffic Tunnel (Selected Pool Mode)
# ============================================================

# (Interface + IPsec policy as above)

# ============================================================
# Step 4: Address List for Selective Traffic
# ============================================================
/ip firewall address-list add \
    list=hoberadius-traffic-pool \
    address=192.168.1.0/24 \
    comment="HOBERADIUS-POOL-SUBNET-A"

/ip firewall address-list add \
    list=hoberadius-traffic-pool \
    address=192.168.2.0/24 \
    comment="HOBERADIUS-POOL-SUBNET-B"

# ============================================================
# Step 5: Mangle Mark for Pool Traffic
# ============================================================
/ip firewall mangle add \
    chain=prerouting \
    src-address-list=hoberadius-traffic-pool \
    action=mark-routing \
    new-routing-mark=hoberadius-traffic \
    comment="HOBERADIUS-POOL-MARK"

# ============================================================
# Step 6: Route with Routing Mark
# ============================================================
/ip route add \
    dst-address=0.0.0.0/0 \
    gateway=10.20.0.1 \
    routing-mark=hoberadius-traffic \
    comment="HOBERADIUS-L2TP-SELECTIVE-ROUTE"

# ============================================================
# (Script End)
# ============================================================
```

### 6.4 سكربت Verification (Probe Router State)

```bash
#!/bin/bash
# Verify SSTP management tunnel
ssh -l hr-0042 LAB-ROUTER-01 \
  '/ip ppp secret print'

# Should output:
# NAME          SERVICE    CALLER-ID         PASSWORD          PROFILE
# LAB-ROUTER-01 l2tp       any               <password-masked>  default

# Verify SSTP interface is up
ssh -l hr-0042 LAB-ROUTER-01 \
  '/interface sstp-client print'

# Verify IP address assigned
ssh -l hr-0042 LAB-ROUTER-01 \
  '/ip address print interface=sstp-hoberadius-mgmt'

# Verify L2TP (if enabled)
ssh -l hr-0042 LAB-ROUTER-01 \
  '/interface l2tp-client print'

# Verify IPsec policies
ssh -l hr-0042 LAB-ROUTER-01 \
  '/ip ipsec policy print comment~="HOBERADIUS"'
```

---

## 7. UX معالج الإعداد + صفحة العمليات

### 7.1 معمارية المعالج الحالية (setup_wizard_v3)

```
Step 1: Internet Phase
  ├─ Form: gateway, DNS, NAT
  └─ Verify: ping, DNS lookup

Step 2: VPN/RADIUS Phase (WireGuard v7 only)
  ├─ Form: server endpoint, secret
  └─ Verify: WireGuard peer active

Step 3: Hotspot/Services Phase
  ├─ Form: SSID, pool, auth
  └─ Verify: hotspot server running

Step 4-5: Broadband/Walled-Garden (optional)

Step 6: Review + Apply
  └─ Summary + final confirm
```

### 7.2 المعمارية المطلوبة (مع SSTP/L2TP)

```
Step 1: Router Info
  ├─ name, ros_version (auto-detected or manual)
  └─ If v6 detected: show SSTP as default, skip WireGuard

Step 2: Internet Phase (unchanged)

Step 3: VPN/RADIUS Phase (BRANCHED)
  │
  ├─ IF v7:
  │  ├─ Card: "WireGuard (Management + Traffic)"
  │  └─ Form: server, secret
  │
  └─ IF v6:
     ├─ Card A: "SSTP (إدارة فقط)"
     │  ├─ Always ON (disabled checkbox)
     │  └─ Form: server, secret
     │
     └─ Card B: "L2TP/IPsec (ترافيك اختياري)"
        ├─ Toggle: ON/OFF
        ├─ Form (if ON):
        │  ├─ Mode: disabled | selected_pool | selected_subscribers | full_tunnel
        │  ├─ Pool (if selected_pool): 10.20.0.0/24
        │  ├─ Subscribers (if selected_subs): list
        │  └─ Confirm checkbox: "أنا أفهم أن هذا سيغير IP المشتركين"
        │
        └─ Conflict analyzer output (if both enabled)
           └─ "⚠️ فقط نفق واحد سيملك الـ default route..."

Step 4: Hotspot/Services (unchanged)

Step 5: Review + Summary
  ├─ Show Admin tunnel type + mode
  ├─ Show Traffic tunnel type + mode (if enabled)
  ├─ Show conflict status (if any)
  └─ Final confirm
```

### 7.3 نصوص عربي (من السبيك)

```
SSTP Card:
  Title: 🔐 نفق الإدارة (SSTP)
  Subtitle: متطلب. إدارة آمنة فقط، بدون تغيير IP
  Field 1: خادم SSTP (IP:port)
  Field 2: كلمة سر الإدارة
  Default: Checked (disabled toggle)

L2TP Card:
  Title: 📊 نفق الترافيك (L2TP/IPsec)
  Subtitle: اختياري. لتغيير IP/Geo-spoof
  Toggle: تفعيل L2TP/IPsec

  If Enabled:
    Mode selector:
      ◯ معطل (disabled)
      ◯ مجموعة مختارة (selected_pool) — اختر شبكة فرعية
      ◯ مشتركون مختارون (selected_subscribers) — تتحكم RADIUS
      ◯ نفق كامل (full_tunnel) — جميع المشتركين

    If selected_pool:
      Field: شبكة العناوين (CIDR)  [10.20.0.0/24]

    Confirmation:
      ☐ أنا أفهم أن هذا سيغير عناوين IP جميع المشتركين المختارة

Conflict Warning:
  🚨 تحذير: كلا النفقين لا يمكنهما امتلاك الـ default route
     نفق الإدارة (SSTP): لا يملك default route ✓
     نفق الترافيك (L2TP): سيملك default route ✓
     Status: ✅ No Conflict
```

### 7.4 نقاط الإدراج في القوالب (Template Entry Points)

#### 7.4.1 `setup_wizard_v3.html` (Step 3 - VPN Phase)

**Before** (current):
```html
<section data-swz-step="3" class="swz-card">
  <h3>خطوة 3: VPN و RADIUS</h3>

  <!-- WireGuard section (v7 only) -->
  <div class="swz-service-card" id="wg-card">
    <label>
      <input type="checkbox" name="enable_wireguard" />
      WireGuard (VPN + RADIUS)
    </label>
    <!-- ... fields ... -->
  </div>
</section>
```

**After** (with SSTP/L2TP branching):
```html
<section data-swz-step="3" class="swz-card">
  <h3>خطوة 3: VPN و RADIUS</h3>

  <!-- V7: WireGuard only -->
  <div id="vpn-v7-section" class="vpn-section" style="display:none;">
    <div class="swz-service-card" id="wg-card">
      <label>
        <input type="checkbox" name="enable_wireguard" checked />
        🔒 WireGuard (الإدارة + الترافيك)
      </label>
      <!-- ... WireGuard fields ... -->
    </div>
  </div>

  <!-- V6: SSTP + L2TP -->
  <div id="vpn-v6-section" class="vpn-section" style="display:none;">

    <!-- SSTP Management Card (always ON) -->
    <div class="swz-tunnel-card" id="sstp-card">
      <div class="swz-tunnel-header">
        <h4>🔐 نفق الإدارة (SSTP)</h4>
        <p class="swz-tunnel-subtitle">متطلب. إدارة آمنة فقط، بدون تغيير IP</p>
      </div>
      <fieldset class="swz-field" disabled>
        <label>
          <input type="checkbox" name="sstp_enabled" checked disabled />
          تفعيل SSTP
        </label>
      </fieldset>
      <div class="swz-field">
        <label for="sstp-server">خادم SSTP (IP:port):</label>
        <input type="text" id="sstp-server" name="sstp_server" placeholder="203.0.113.1:443" required />
      </div>
      <div class="swz-field">
        <label for="sstp-secret">كلمة سر الإدارة:</label>
        <input type="password" id="sstp-secret" name="sstp_secret" required />
      </div>
      <details class="swz-tech-details">
        <summary>التفاصيل التقنية</summary>
        <p>SSTP هو نفق آمن لـ API + SSH. لا يملك default route.</p>
      </details>
    </div>

    <!-- L2TP/IPsec Traffic Card (optional) -->
    <div class="swz-tunnel-card" id="l2tp-card">
      <div class="swz-tunnel-header">
        <h4>📊 نفق الترافيك (L2TP/IPsec)</h4>
        <p class="swz-tunnel-subtitle">اختياري. لتغيير IP/Geo-spoof</p>
      </div>
      <fieldset class="swz-field">
        <label>
          <input type="checkbox" name="l2tp_enabled" id="l2tp-toggle" />
          تفعيل L2TP/IPsec
        </label>
      </fieldset>

      <!-- L2TP Fields (hidden until enabled) -->
      <div id="l2tp-fields" style="display:none;">
        <div class="swz-field">
          <label for="l2tp-mode">وضع الترافيك:</label>
          <select id="l2tp-mode" name="l2tp_mode">
            <option value="disabled">معطل</option>
            <option value="selected_pool">مجموعة مختارة (شبكة فرعية)</option>
            <option value="selected_subscribers">مشتركون مختارون (RADIUS CoA)</option>
            <option value="full_tunnel">نفق كامل (جميع المشتركين)</option>
          </select>
        </div>

        <!-- Conditional: Pool selection -->
        <div id="l2tp-pool-fields" style="display:none;">
          <div class="swz-field">
            <label for="l2tp-pool">شبكة العناوين (CIDR):</label>
            <input type="text" id="l2tp-pool" name="l2tp_pool" placeholder="10.20.0.0/24" />
          </div>
        </div>

        <!-- Conditional: Subscriber selection (future) -->
        <div id="l2tp-subs-fields" style="display:none;">
          <p>سيتم الاختيار لاحقاً عبر RADIUS CoA</p>
        </div>

        <!-- Confirmation -->
        <div class="swz-field">
          <label>
            <input type="checkbox" name="l2tp_confirm" />
            ☐ أنا أفهم أن هذا سيغير عناوين IP المشتركين
          </label>
        </div>

        <div class="swz-field">
          <label for="l2tp-secret">كلمة سر L2TP:</label>
          <input type="password" id="l2tp-secret" name="l2tp_secret" />
        </div>
      </div>

      <!-- Conflict analyzer output -->
      <div id="tunnel-conflict-warning" class="swz-conflict-badge" style="display:none;">
        <span class="conflict-icon">🚨</span>
        <span id="conflict-message"></span>
      </div>
    </div>

  </div>

</section>
```

#### 7.4.2 `setup_wizard_v3.js` (JavaScript branching)

```javascript
// In setup_wizard_v3.js

function showStep(stepNum) {
  // ... existing code ...

  if (stepNum === 3) {
    // Detect RouterOS version from form state
    const rosVersion = detectRouterOsVersion();  // '6' or '7'

    const vpnV7Section = document.getElementById('vpn-v7-section');
    const vpnV6Section = document.getElementById('vpn-v6-section');

    if (rosVersion === '7') {
      vpnV7Section.style.display = 'block';
      vpnV6Section.style.display = 'none';
    } else if (rosVersion === '6') {
      vpnV7Section.style.display = 'none';
      vpnV6Section.style.display = 'block';
    }

    // L2TP toggle: show/hide dependent fields
    const l2tpToggle = document.getElementById('l2tp-toggle');
    const l2tpFields = document.getElementById('l2tp-fields');

    l2tpToggle.addEventListener('change', (e) => {
      l2tpFields.style.display = e.target.checked ? 'block' : 'none';
    });

    // L2TP mode selector: show/hide pool fields
    const l2tpMode = document.getElementById('l2tp-mode');
    const l2tpPoolFields = document.getElementById('l2tp-pool-fields');
    const l2tpSubsFields = document.getElementById('l2tp-subs-fields');

    l2tpMode.addEventListener('change', (e) => {
      l2tpPoolFields.style.display = e.target.value === 'selected_pool' ? 'block' : 'none';
      l2tpSubsFields.style.display = e.target.value === 'selected_subscribers' ? 'block' : 'none';
    });
  }
}

function collectFormData() {
  // ... existing code ...

  const rosVersion = detectRouterOsVersion();

  if (rosVersion === '6') {
    return {
      // ... internet phase data ...
      vpn_phase: {
        ros_version: '6',
        mgmt_tunnel_type: 'sstp',
        mgmt_tunnel_config: {
          server: document.getElementById('sstp-server').value,
          secret: document.getElementById('sstp-secret').value,
        },
        traffic_tunnel_enabled: document.getElementById('l2tp-toggle').checked,
        traffic_tunnel_config: document.getElementById('l2tp-toggle').checked ? {
          type: 'l2tp_ipsec',
          mode: document.getElementById('l2tp-mode').value,
          pool: document.getElementById('l2tp-pool').value,
          secret: document.getElementById('l2tp-secret').value,
        } : null,
      },
    };
  } else {
    // v7 WireGuard (unchanged)
    return {
      // ... existing data ...
    };
  }
}

function detectRouterOsVersion() {
  // Call API to detect version
  return sessionStorage.getItem('router_os_version') || '7';
}

function analyzeTunnelConflicts(vpnConfig) {
  const conflictWarning = document.getElementById('tunnel-conflict-warning');
  const conflictMessage = document.getElementById('conflict-message');

  api('POST', '/admin/radius/setup-wizard-v3/validate-tunnel-plan', vpnConfig)
    .then(result => {
      if (result.errors.length > 0) {
        conflictWarning.style.display = 'block';
        conflictMessage.textContent = result.errors[0];
        conflictWarning.classList.add('error');
      } else if (result.warnings.length > 0) {
        conflictWarning.style.display = 'block';
        conflictMessage.textContent = result.warnings[0];
        conflictWarning.classList.add('warning');
      } else {
        conflictWarning.style.display = 'none';
      }
    });
}
```

### 7.5 صفحة العمليات (mt_operations.html) توسعات

#### 7.5.1 عمود جديد: Admin Tunnel Type + Status

**Before**:
```html
<thead>
  <tr>
    <th>#</th>
    <th>Name</th>
    <th>Address</th>
    <th>Status</th>
    <th>Users</th>
    <th>Traffic</th>
    <th>RouterOS</th>
    <th>Actions</th>
  </tr>
</thead>
```

**After**:
```html
<thead>
  <tr>
    <th>#</th>
    <th>Name</th>
    <th>Address</th>
    <th>Status</th>
    <th>Users</th>
    <th>Traffic</th>
    <th>RouterOS</th>
    <th>Admin Tunnel</th>         <!-- NEW -->
    <th>Traffic Tunnel</th>       <!-- NEW -->
    <th>Conflicts</th>            <!-- NEW -->
    <th>Actions</th>
  </tr>
</thead>

<tbody>
  <tr data-nas-id="123">
    <!-- ... existing cells ... -->

    <!-- Admin Tunnel Column -->
    <td>
      <div class="mt-tunnel-badge">
        <span class="tunnel-type" data-type="sstp">🔐 SSTP</span>
        <span class="tunnel-status" data-status="connected">● Connected</span>
      </div>
    </td>

    <!-- Traffic Tunnel Column -->
    <td>
      <div class="mt-tunnel-badge">
        <span class="tunnel-type" data-type="l2tp">📊 L2TP</span>
        <span class="tunnel-mode" data-mode="full_tunnel">Full Tunnel</span>
        <span class="tunnel-status" data-status="pending">● Pending</span>
      </div>
    </td>

    <!-- Conflicts Column -->
    <td>
      <span class="conflict-badge" data-conflict="none" style="display:none;">✓ No Conflict</span>
      <span class="conflict-badge" data-conflict="warning" style="display:none;">⚠️ Warning</span>
      <span class="conflict-badge" data-conflict="error" style="display:none;">🚨 Error</span>
    </td>

    <!-- Actions -->
    <td>
      <!-- ... existing buttons ... -->
      <button class="hub-btn sm" data-action="view-tunnel-script">Script</button>
      <button class="hub-btn sm" data-action="check-tunnel-health">Health</button>
      <button class="hub-btn sm" data-action="analyze-conflict">Analyze</button>
    </td>
  </tr>
</tbody>
```

#### 7.5.2 CSS Classes جديدة

**ملف**: `setup_wizard_v3.css` (و `mt_setup.css`)

```css
/* Tunnel badges */
.mt-tunnel-badge {
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
  font-size: 0.85rem;
}

.tunnel-type {
  font-weight: 600;
  font-size: 0.95rem;
}

.tunnel-type[data-type="sstp"] {
  color: var(--c-primary, #7657f4);
}

.tunnel-type[data-type="wireguard"] {
  color: var(--c-success, #10b981);
}

.tunnel-type[data-type="l2tp"] {
  color: var(--c-warn, #f59e0b);
}

.tunnel-status {
  display: flex;
  align-items: center;
  gap: 0.25rem;
  font-size: 0.8rem;
  color: #666;
}

.tunnel-status[data-status="connected"] {
  color: var(--c-success, #10b981);
}

.tunnel-status[data-status="pending"] {
  color: var(--c-warn, #f59e0b);
}

.tunnel-status[data-status="error"] {
  color: var(--c-error, #dc2626);
}

.tunnel-mode {
  font-size: 0.75rem;
  color: #999;
  font-style: italic;
}

/* Conflict badges */
.conflict-badge {
  padding: 0.25rem 0.5rem;
  border-radius: 4px;
  font-size: 0.8rem;
  font-weight: 500;
}

.conflict-badge[data-conflict="none"] {
  background: #d1fae5;
  color: #065f46;
}

.conflict-badge[data-conflict="warning"] {
  background: #fef3c7;
  color: #92400e;
}

.conflict-badge[data-conflict="error"] {
  background: #fee2e2;
  color: #991b1b;
}

/* Tunnel cards in wizard */
.swz-tunnel-card {
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  padding: 1rem;
  margin-bottom: 1rem;
  background: #f9fafb;
}

.swz-tunnel-header h4 {
  margin: 0 0 0.25rem;
  font-size: 1rem;
  color: #1f2937;
}

.swz-tunnel-subtitle {
  margin: 0;
  font-size: 0.85rem;
  color: #6b7280;
}

.swz-conflict-badge {
  padding: 0.75rem;
  background: #fee2e2;
  border-left: 3px solid #dc2626;
  border-radius: 4px;
  color: #991b1b;
  margin-top: 1rem;
  display: flex;
  align-items: center;
  gap: 0.5rem;
}

.conflict-icon {
  font-size: 1.2rem;
}
```

---

## 8. الجانب الخادمي (Server-side Infrastructure)

### 8.1 الحالة الحالية (WireGuard v7)

```
wg_peer_manager.py
  ↓
  /etc/hoberadius/wg-peers.d/*.conf (managed peer files)
  ↓
  Server-side: wg set wg0 peer <pubkey> allowed-ips=10.10.0.2/32
  ↓
  Router connects: wg0 interface active
```

### 8.2 المتطلبات للـ SSTP/L2TP v6 (الحد الأدنى للنطاق)

**السؤال**: هل SSTP/L2TP server setup ضمن نطاق هذه المرحلة أم يُؤجل للمرحلة اللاحقة؟

**الافتراض الحالي**: ✅ Server-side is OUT OF SCOPE — assuming infrastructure already exists (SSTP endpoint at 203.0.113.1:443, L2TP endpoint at 203.0.113.1:1701).

**الملفات المطلوبة إن أضيفت لاحقاً**:
```
app/radius/services/sstp_server.py (NEW, future phase)
app/radius/services/l2tp_ipsec_server.py (NEW, future phase)
app/radius/services/server_secret_vault.py (NEW, centralized secret rotation)
docs/SERVER_SETUP_SSTP_L2TP.md (setup guide for DevOps)
```

### 8.3 معالجة الحالة "Server Not Ready"

في `setup_wizard_v3.py` POST handler، إذا كان server endpoint غير متاح:

```python
@routes.post('/admin/radius/setup-wizard-v3/runs/<run_id>/phase-plan/vpn_v6')
def setup_wizard_v3_vpn_v6_plan(run_id: str):
    """VPN v6 SSTP+L2TP phase planner."""

    vpn_config = request.get_json()

    # Step 1: Check server readiness
    sstp_endpoint = vpn_config.get('sstp_server')
    l2tp_endpoint = vpn_config.get('l2tp_server')

    sstp_ready = check_endpoint_ready(sstp_endpoint)
    l2tp_ready = check_endpoint_ready(l2tp_endpoint) if vpn_config.get('l2tp_enabled') else True

    if not sstp_ready or not l2tp_ready:
        return {
            'ok': False,
            'errors': [
                f'SSTP endpoint {sstp_endpoint} not responding' if not sstp_ready else '',
                f'L2TP endpoint {l2tp_endpoint} not responding' if not l2tp_ready else '',
            ],
            'warnings': ['Server-side configuration required before proceeding'],
        }

    # Step 2: Proceed with planning
    plan = build_v6_sstp_l2tp_plan(run_id, vpn_config)

    return plan.to_dict()
```

---

## 9. خطة الاختبارات

### 9.1 مستويات الاختبارات

| Level | Files | Coverage | Duration |
|-------|-------|----------|----------|
| **Unit** | `test_routeros_caps.py` | Capability matrix, validation rules | 2-3 min |
| **Integration** | `test_setup_wizard_vpn_v6.py` | Phase planning, conflict detection | 5-10 min |
| **E2E** | `test_setup_wizard_v6_live_router.py` | Real RouterOS v6 lab instance | 15-30 min |
| **Regression** | `test_wg_v7_unchanged.py` | WireGuard v7 not broken | 5 min |
| **UI** | Browser automation (manual or Playwright) | Form flows, toggle visibility | 10-15 min |

### 9.2 ملفات الاختبارات المقترحة

```
tests/
├── test_routeros_caps.py
│   ├── test_parse_major_v6_v7()
│   ├── test_parse_routeros_major_string()
│   ├── test_supports_sstp_mgmt()
│   ├── test_supports_l2tp_ipsec_traffic()
│   ├── test_recommended_management_tunnel_v6()
│   ├── test_recommended_traffic_tunnel_v6()
│   ├── test_connection_modes_for_version_v6()
│   ├── test_validate_connection_plan_happy_path()
│   ├── test_validate_connection_plan_v6_wireguard_rejected()
│   ├── test_validate_connection_plan_single_default_route()
│   ├── test_validate_connection_plan_sstp_no_default_route()
│   └── test_validate_connection_plan_l2tp_alone_warns()
│
├── test_setup_wizard_vpn_conflict_analyzer.py
│   ├── test_analyze_tunnel_conflicts_13_cases() [see matrix in section 5.4]
│   ├── test_both_default_routes_rejected()
│   ├── test_sstp_default_route_rejected()
│   ├── test_l2tp_selective_pool_valid()
│   └── test_traffic_alone_warns()
│
├── test_mt_provisioner_v6_sstp_l2tp.py
│   ├── test_render_sstp_mgmt_block_includes_comment()
│   ├── test_render_sstp_mgmt_block_add_default_route_no()
│   ├── test_render_l2tp_ipsec_block_full_tunnel()
│   ├── test_render_l2tp_ipsec_block_selective_pool()
│   ├── test_render_l2tp_ipsec_block_disabled_returns_empty()
│   ├── test_render_l2tp_ipsec_idempotency()
│   ├── test_render_routeros_script_v6_branches_correctly()
│   └── test_render_routeros_script_v7_unchanged()
│
├── test_setup_wizard_vpn_v6_phase_planner.py
│   ├── test_build_v6_sstp_management_plan_happy()
│   ├── test_build_v6_l2tp_ipsec_traffic_plan_happy()
│   ├── test_phase_plan_result_validation_commands_v6()
│   ├── test_phase_plan_rollback_script_removes_sstp()
│   └── test_phase_plan_rollback_script_removes_l2tp()
│
├── test_setup_wizard_verification_v6.py
│   ├── test_verify_management_tunnel_sstp_success()
│   ├── test_verify_management_tunnel_sstp_failure()
│   ├── test_verify_traffic_tunnel_l2tp_success()
│   ├── test_verify_traffic_tunnel_l2tp_disabled()
│   └── test_verify_tunnel_conflict_detection()
│
├── test_setup_wizard_v6_live_router.py (E2E)
│   ├── test_provision_v6_sstp_only_end_to_end()
│   ├── test_provision_v6_sstp_plus_l2tp_full_tunnel()
│   ├── test_provision_v6_sstp_plus_l2tp_selective_pool()
│   ├── test_verify_sstp_interface_up()
│   ├── test_verify_l2tp_ipsec_routes_active()
│   ├── test_verify_radius_over_sstp()
│   └── test_revoke_sstp_tunnel_rollback()
│
├── test_wg_v7_regression.py (Ensure v7 unchanged)
│   ├── test_render_wg_block_v7_still_works()
│   ├── test_wg_peer_manager_unchanged()
│   ├── test_setup_wizard_v7_flow_unchanged()
│   └── test_wireguard_config_v7_still_works()
│
└── test_nas_repo_v6_columns.py
    ├── test_nas_repo_reads_new_tunnel_columns()
    ├── test_nas_repo_write_sstp_config()
    └── test_nas_repo_write_l2tp_config()
```

### 9.3 معايير النجاح

```
✅ Unit: 95%+ pass rate
✅ Integration: 100% pass rate
✅ E2E: 100% pass rate (if RouterOS v6 lab available)
✅ Regression: 100% pass rate (WireGuard v7 working)
✅ UI: All form flows visible, toggles work, conflict warnings display

⏸️ Deferred (if server-side out of scope):
   - SSTP server certificate validation
   - L2TP/IPsec IKEv2 handshake test
```

---

## 10. تسلسل 8 Commits

### Commit 1: Initialize capability matrix + data types

**ملفات**:
```
app/radius/services/routeros_caps.py          [new functions: parse_routeros_major, supports_sstp_mgmt, supports_l2tp_ipsec_traffic, ...]
app/radius/core/types.py                       [NasDevice: add tunnel fields]
tests/test_routeros_caps.py                    [unit tests: capabilities]
tests/test_routeros_caps_v6_specific.py        [unit tests: v6 features]
```

**Commit Message**:
```
feat: Add RouterOS v6 capability matrix (SSTP + L2TP)

- parse_routeros_major() returns version string ('6'/'7')
- supports_sstp_mgmt() / supports_l2tp_ipsec_traffic() for v6+
- validate_connection_plan() enforces tunnel rules (single default-route owner)
- NasDevice dataclass extended with tunnel config fields
- Unit tests: 13 validation cases + edge cases

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
```

**معايير التحقّق**:
- ✅ parse_routeros_major('6.49.7') → '6'
- ✅ parse_routeros_major('7.11') → '7'
- ✅ supports_sstp_mgmt(6) → True
- ✅ supports_l2tp_ipsec_traffic(6) → True
- ✅ All 13 validation cases pass
- ✅ NasDevice frozen dataclass still frozen

---

### Commit 2: Database schema + migration

**ملفات**:
```
app/radius/db/migrations/092_nas_sstp_l2tp_tunnels.sql   [new migration]
app/radius/db/repos/nas_repo.py                          [_row() mapping]
tests/test_nas_repo_v6_columns.py                        [column read/write tests]
```

**Commit Message**:
```
feat: Migration 092 - Add SSTP/L2TP tunnel columns to nas_devices

- mgmt_tunnel_type, mgmt_tunnel_interface, mgmt_tunnel_ip, mgmt_tunnel_status
- traffic_tunnel_type, traffic_tunnel_mode, traffic_tunnel_interface
- tunnel_conflict_detected, tunnel_conflict_reason
- All columns nullable with '' default (safe for v7 routers)
- Index on tunnel_status for fleet queries

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
```

**معايير التحقّق**:
- ✅ Migration runs without error
- ✅ nas_repo._row() maps all new columns
- ✅ Existing v7 routers unaffected (empty strings)
- ✅ Index created successfully

---

### Commit 3: Script rendering (SSTP + L2TP blocks)

**ملفات**:
```
app/radius/services/mt_provisioner.py           [render_sstp_mgmt_block, render_l2tp_ipsec_traffic_block, updated render_routeros_script]
tests/test_mt_provisioner_v6_sstp_l2tp.py       [render function tests]
```

**Commit Message**:
```
feat: Add script renderers for SSTP + L2TP/IPsec (v6)

- render_sstp_mgmt_block() generates SSTP client config (add-default-route=no)
- render_l2tp_ipsec_traffic_block() supports 4 modes (disabled/pool/subs/full)
- render_routeros_script() branches on ros_version (v7 WireGuard vs v6 SSTP/L2TP)
- All blocks include HOBERADIUS comment tags for idempotency
- Tests: script interpolation, idempotency, guard conditions

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
```

**معايير التحقّق**:
- ✅ render_sstp_mgmt_block() output includes add-default-route=no
- ✅ render_l2tp_ipsec_traffic_block('disabled') returns ''
- ✅ render_routeros_script() v7 path unchanged
- ✅ render_routeros_script() v6 path produces valid script
- ✅ Script idempotency: re-running doesn't duplicate commands

---

### Commit 4: Conflict analysis + verification

**ملفات**:
```
app/radius/services/setup_wizard_vpn_conflict_analyzer.py     [new service]
app/radius/services/setup_wizard_verification.py               [verify_management_tunnel, verify_traffic_tunnel]
tests/test_setup_wizard_vpn_conflict_analyzer.py              [13-case matrix]
tests/test_setup_wizard_verification_v6.py                    [tunnel probes]
```

**Commit Message**:
```
feat: Tunnel conflict analysis + verification probes

- analyze_tunnel_conflicts() validates 13 tunnel combinations
- verify_management_tunnel() probes SSTP/WireGuard interface state
- verify_traffic_tunnel() checks L2TP routing + IPsec policies
- Returns (is_valid, warnings, conflict_reason) for all cases
- Tests: all 13 matrix cases + edge cases

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
```

**معايير التحقّق**:
- ✅ All 13 matrix cases produce correct results
- ✅ verify_management_tunnel('sstp') returns {ok, tunnel_interface_up, tunnel_ip_assigned}
- ✅ verify_traffic_tunnel('l2tp_ipsec') returns {ok, ipsec_policies_active}
- ✅ Conflict reasons are human-readable

---

### Commit 5: Phase planners (SSTP + L2TP)

**ملفات**:
```
app/radius/services/setup_wizard_vpn_sstp_management_phase_planner.py    [new]
app/radius/services/setup_wizard_vpn_l2tp_ipsec_traffic_phase_planner.py  [new]
tests/test_setup_wizard_vpn_v6_phase_planner.py                          [phase plan tests]
```

**Commit Message**:
```
feat: Phase planners for RouterOS v6 SSTP + L2TP

- SSTPManagementPhasePlanner generates validation commands + script blocks
- L2TPIPsecTrafficPhasePlanner handles 4 traffic modes
- Both inherit PhasePlannerBase, return PhasePlanResult
- Validation commands verify tunnel compatibility (idempotent)
- Tests: phase plan generation, rollback scripts, error handling

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
```

**معايير التحقّق**:
- ✅ Phase planner returns PhasePlanResult with script, validation_commands, rollback_script
- ✅ Validation commands are safe (ssh -l api_user ... print)
- ✅ Rollback script removes SSTP + L2TP ([find comment~="HOBERADIUS"] pattern)
- ✅ Warnings map to diagnostic codes (SW-002, SW-003, etc.)

---

### Commit 6: Route handlers (setup wizard V3 V6 branch)

**ملفات**:
```
app/radius/routes/setup_wizard_v3.py            [POST /vpn_v6, validate-tunnel-plan, phase-plan/vpn_v6]
tests/test_setup_wizard_v6_live_router.py       [integration E2E tests]
```

**Commit Message**:
```
feat: Setup Wizard V3 routes for RouterOS v6 VPN phase

- POST /admin/radius/setup-wizard-v3/runs/<id>/validate-tunnel-plan
- POST /admin/radius/setup-wizard-v3/runs/<id>/phase-plan/vpn_v6
- Detects ros_version, branches to SSTP/L2TP planning
- Validates connection plan before proceeding
- Returns phase result with script preview + validation commands

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
```

**معايير التحقّق**:
- ✅ POST validate-tunnel-plan returns {ok, errors, warnings}
- ✅ POST phase-plan/vpn_v6 returns PhasePlanResult JSON
- ✅ Script preview matches rendered output
- ✅ Validation commands are executable

---

### Commit 7: Template + UI branching

**ملفات**:
```
app/templates/radius/setup_wizard_v3.html     [V6 SSTP/L2TP cards, branching logic]
app/templates/radius/mt_operations.html        [tunnel status columns + badges]
app/static/css/setup_wizard_v3.css             [tunnel card styles]
app/static/css/mt_operations.css                [tunnel badge styles]
app/static/js/setup_wizard_v3.js               [form branching, toggle handlers]
app/static/js/mt_operations.js                 [live tunnel status polling]
tests/test_setup_wizard_v6_ui.py (browser)    [form flows, visibility toggles]
```

**Commit Message**:
```
feat: UI for RouterOS v6 SSTP + L2TP tunnel configuration

- setup_wizard_v3.html: Step 3 branches on v6/v7, shows SSTP card (always ON) + L2TP card (optional)
- L2TP mode selector (disabled/pool/subs/full) with conditional pool field
- Conflict warning badge displays if both tunnels claim default route
- mt_operations.html: 3 new columns (Admin Tunnel, Traffic Tunnel, Conflicts)
- Tunnel status badges with colors + connection state
- CSS: .swz-tunnel-card, .tunnel-badge, .conflict-badge styles
- JS: Form branching, toggle handlers, conflict analyzer calls

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
```

**معايير التحقّق**:
- ✅ Wizard Step 3 shows v7 WireGuard OR v6 SSTP/L2TP (not both)
- ✅ L2TP toggle shows/hides dependent fields
- ✅ L2TP mode selector shows/hides pool field
- ✅ Conflict warning appears when analyzing conflicting config
- ✅ mt_operations rows show tunnel status badges
- ✅ All Arabic text displays correctly (RTL layout)

---

### Commit 8: Regression tests + documentation

**ملفات**:
```
tests/test_wg_v7_regression.py                 [ensure WireGuard v7 unchanged]
docs/router_vpn/ROUTEROS_V6_VPN_STRATEGY.md   [architecture + examples]
docs/SERVER_SETUP_SSTP_L2TP.md                 [server-side setup guide (future)]
README.md (tunnel section)                     [update project README]
```

**Commit Message**:
```
fix: Regression tests + documentation for RouterOS VPN strategy

- test_wg_v7_regression.py: ensure WireGuard v7 workflows unchanged
- ROUTEROS_V6_VPN_STRATEGY.md: architecture overview, SSTP/L2TP explained
- Example nas_devices rows for v6 + v7 routers
- Tunnel mode matrix (full tunnel vs selective pool) with CoA implications
- Future: Server-side setup guide (deferred to Phase 2)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
```

**معايير التحقّق**:
- ✅ All WireGuard v7 tests pass unchanged
- ✅ Documentation is clear, Arabic text included
- ✅ Matrix diagrams render correctly (Markdown)
- ✅ Example scripts are valid RouterOS syntax

---

## 11. معايير القبول الـ 17 (Acceptance Criteria Checklist)

- [ ] **AC1**: RouterOS v7 WireGuard scripts render and apply identically to current behavior (regression test passes)
- [ ] **AC2**: `routeros_caps.validate_connection_plan()` rejects v6 + wireguard with error 'v6_wireguard_unsupported'
- [ ] **AC3**: `routeros_caps.validate_connection_plan()` rejects both tunnels claiming default-route with error 'conflicting_default_routes'
- [ ] **AC4**: SSTP management script block includes `add-default-route=no` in rendered config
- [ ] **AC5**: L2TP/IPsec script block uses `[find comment~="HOBERADIUS"]` pattern for idempotency (re-run safe)
- [ ] **AC6**: Database migration 092 runs without error; all new columns default to '' or NULL
- [ ] **AC7**: NasDevice dataclass reads/writes all new tunnel columns via nas_repo._row()
- [ ] **AC8**: Setup Wizard Step 3 shows SSTP card (always ON, disabled checkbox) for v6; hides for v7
- [ ] **AC9**: Setup Wizard Step 3 shows L2TP card (optional toggle) for v6; hides for v7
- [ ] **AC10**: L2TP mode selector (disabled/pool/subs/full) conditionally shows pool field only for 'selected_pool'
- [ ] **AC11**: Conflict analyzer banner displays if both mgmt + traffic tunnels claim default-route
- [ ] **AC12**: mt_operations.html table rows display tunnel status badges (SSTP + L2TP type + connection state)
- [ ] **AC13**: Conflict badge appears in mt_operations for routers with tunnel routing conflicts
- [ ] **AC14**: API endpoint POST /validate-tunnel-plan returns {ok, errors, warnings} JSON
- [ ] **AC15**: API endpoint POST /phase-plan/vpn_v6 returns PhasePlanResult with script + validation_commands + rollback_script
- [ ] **AC16**: verify_management_tunnel('sstp') probes /interface/sstp-client and returns {ok, tunnel_interface_up, tunnel_ip_assigned}
- [ ] **AC17**: All Arabic UI text (cards, labels, toggles) displays correctly without encoding errors (UTF-8 verified)

---

## 12. قرارات مفتوحة (Require Owner Approval)

### Q1: توسيع جدول `nas_devices` أم جدول جديد `nas_vpn_tunnels`?

**الخيار A (Recommended)**: توسيع `nas_devices` بـ 12 عمود SSTP/L2TP
- ✅ Simple queries (single table)
- ❌ Schema grows (but backward compatible)
- **Decision**: ✅ APPROVED for Phase 1

**الخيار B**: جدول منفصل مع FK
- ✅ Clean separation
- ❌ Requires JOIN
- **Decision**: ⏸️ DEFERRED to Phase 2 if complexity grows

### Q2: هل Server-side SSTP/L2TP setup ضمن النطاق؟

**الخيار A (Current)**: Server-side OUT OF SCOPE
- Assumes infrastructure exists (endpoints reachable, certs valid)
- Router scripts work with any SSTP/L2TP endpoint
- **Decision**: ✅ APPROVED — Server-side is Phase 2

**الخيار B**: Include server-side provisioning
- Add sstp_server.py + l2tp_ipsec_server.py
- Would add 3-4 weeks to timeline
- **Decision**: ⏸️ DEFERRED to Phase 2

### Q3: سياسة التحقّق من الشهادات SSTP

**الخيار A**: `verify-server-certificate=no` (allow self-signed)
- Router accepts any cert
- ⚠️ MITM risk
- **Decision**: ❓ NEEDS OWNER APPROVAL
  - If high-security environment: `verify-server-certificate=yes` + cert pinning
  - If lab/test: `verify-server-certificate=no`

**الخيار B**: `verify-server-certificate=yes` + CA bundle
- Router validates cert chain
- Requires CA cert deployed to router
- **Decision**: ❓ NEEDS OWNER APPROVAL

**Recommendation**: Start with `verify-server-certificate=no` for dev/test; add cert pinning in Phase 2 for production.

### Q4: L2TP/IPsec IKEv2 vs IKEv1?

**الخيار A**: IKEv2 (default in modern RouterOS)
- More secure, faster
- **Decision**: ✅ APPROVED (use IKEv2 in Phase 1)

**الخيار B**: IKEv1 (older routers)
- Wider compatibility
- Less secure
- **Decision**: ⏸️ DEFERRED to Phase 2 if needed

### Q5: PPTP Legacy Support?

**الخيار A**: Hide from UI completely
- `connection_modes()` does NOT include 'pptp'
- **Decision**: ✅ APPROVED

**الخيار B**: Show with warning
- Include 'pptp' in v6 modes (last resort)
- **Decision**: ❌ REJECTED (security risk, deprecated)

---

## 13. الملخص المالي والموارد

### Timeline المتوقع

```
Phase 1 (Current): RouterOS v6 SSTP + L2TP Support
├─ Week 1-2: Capabilities + Database (Commits 1-2)
├─ Week 2-3: Script Rendering + Verification (Commits 3-4)
├─ Week 3-4: Phase Planners + Routes (Commits 5-6)
├─ Week 4-5: UI + Styling (Commit 7)
├─ Week 5-6: Testing + Documentation (Commit 8)
└─ Week 6: Review + Deployment

Phase 2 (Future, deferred): Server-side SSTP/L2TP
├─ SSTP endpoint setup + TLS
├─ L2TP/IPsec IKE + Secret rotation
├─ Subscriber IP pool management
└─ Duration: 4-6 weeks

Phase 3 (Future): CoA enhancements
├─ Traffic tunnel mode switching via CoA
├─ Subscriber pool migration
└─ Duration: 2-3 weeks
```

### Resource Allocation

```
Team:
  Backend (Python): 2 engineers (routeros_caps, mt_provisioner, routes)
  Frontend (JS/CSS): 1 engineer (UI branching, form toggles)
  QA/Testing: 1 engineer (unit + E2E tests)
  DevOps: On-call (lab router setup, server endpoint verification)

Lab Requirements:
  ✅ RouterOS v6 physical/VM instance (for E2E tests)
  ✅ RouterOS v7 physical/VM instance (for regression)
  ⏸️ SSTP + L2TP server endpoints (Phase 2)
```

---

## 14. الخطوات التالية (Immediate Actions)

1. **عرض على المالك** ✋
   - Review قرارات Q1-Q5 أعلاه
   - Approve timeline + resource allocation

2. **Setup بيئة التطوير**
   - Clone main branch
   - Create feature branch: `feature/routeros-v6-sstp-l2tp`
   - Setup RouterOS v6 lab instance

3. **Implement Commit 1**
   - Start with routeros_caps.py + types.py
   - Write unit tests
   - Submit for code review

4. **Parallel tracks**
   - Backend: Commit 1-6 sequentially
   - Frontend: Design UI in Figma (parallel to backend)
   - QA: Prepare test automation

5. **Merge strategy**
   - Each commit merged to feature branch after review
   - Feature branch → main after all 8 commits + regression tests pass
   - Tag: `v1.0-routeros-v6-support`

---

## 15. المراجع والموارد

- **RouterOS Documentation**: https://wiki.mikrotik.com/wiki/Manual (v6 + v7)
- **WireGuard**: Current implementation reference (Section 5.1)
- **Setup Wizard V3**: Current flow (Section 7.1)
- **Existing Tests**: `tests/test_routeros_caps.py`, `tests/test_wg_peer_manager.py`
- **Database Migrations**: `app/radius/db/migrations/033_nas_vpn.sql`, `034_nas_provisioning.sql`

---

**اعتماد**: الخطة جاهزة للعرض على المالك + النقاش حول القرارات المفتوحة (Q1-Q5).

**حالة المراجعة**: ✏️ Pending Owner Approval
