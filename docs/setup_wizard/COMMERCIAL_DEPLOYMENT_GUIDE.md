# HobeRadius — دليل التثبيت التجاري الشامل

> **هدف هذا المستند:** المرجع الوحيد لتثبيت HobeRadius على
> خادم العميل. يجمع كل خطوة، كل مشكلة محتملة، كل حلّ، وكل
> ضمان أُسِّس خلال 22 postmortem.

**آخر تحديث:** 2026-05-26
**الإصدار:** setup_wizard_v3 + host networking + atomic provisioning

---

## 📑 المحتويات

1. [نظرة عامة على الـ Architecture](#1-نظرة-عامة)
2. [متطلبات الخادم](#2-متطلبات-الخادم)
3. [خطوات التثبيت من الصفر](#3-التثبيت-من-الصفر)
4. [التحقّق بعد التثبيت](#4-التحقّق-بعد-التثبيت)
5. [إعداد الراوتر الأوّل (Setup Wizard)](#5-إعداد-الراوتر-الأول)
6. [المراقبة اليوميّة](#6-المراقبة-اليومية)
7. [النسخ الاحتياطي](#7-النسخ-الاحتياطي)
8. [الترقية](#8-الترقية)
9. [المشاكل المعروفة وحلولها](#9-المشاكل-المعروفة)
10. [إجراءات الاستعادة](#10-إجراءات-الاستعادة)
11. [Integration مع المراقبة الخارجيّة](#11-المراقبة-الخارجية)
12. [الـ Invariants المضمونة](#12-الـ-invariants)
13. [مرجع سريع للأوامر](#13-مرجع-سريع)

---

## 1. نظرة عامة

### Components

| Component | Role | Network mode |
|-----------|------|--------------|
| **hoberadius** | Flask app: UI + API + worker threads | Bridge `hrnet`, ports `127.0.0.1:8000` |
| **freeradius** | RADIUS protocol terminator (FreeRADIUS 3.2.5) | **`host`** — required for source IP preservation |
| **nginx** | Reverse proxy + stream forwarder | Bridge, ports 80/443/51000-51199 |
| **backup** | hourly DB snapshot | Bridge |

### Data Flow

```
Subscriber  ──Hotspot login──►  MikroTik (10.10.0.x via WG)
                                       ▼
                              RADIUS Access-Request (UDP 1812)
                                       ▼
                              WG tunnel ──► VPS wg0 ──► host
                                       ▼
                              FreeRADIUS (host networking)
                              reads /data/freeradius-clients-wizard/wizard-run-N.conf
                                       ▼
                              rest_module → http://127.0.0.1:8000/api/v1/internal/auth
                                       ▼
                              hoberadius Flask: policy decision
                                       ▼
                              Access-Accept / Reject → router → subscriber
```

### Workers running inside hoberadius

| Worker | Interval | Purpose |
|--------|----------|---------|
| `webhook` | 5s | Webhook delivery queue |
| `sync_worker` | 3s | Webhook sync |
| `accounting_puller` | 30s | radacct sync (writes disabled by default) |
| `stale_session_reaper` | 60s | Closes zombie radacct rows |
| `device_fingerprint_worker` | 120s | DHCP lease sync |
| `mt_reconciler` | 30s | MikroTik state sync |
| `setup_wizard_tentative_reclaimer` | 300s | Cleans abandoned wizard runs (TTL=30min) |
| **`setup_wizard_radius_reconciler`** | 300s | **Wizard invariants enforcement (cross-tenant)** |

### Key Directories

| Path | Owner | Purpose |
|------|-------|---------|
| `/opt/hoberadius/` | git working tree | Source code |
| `/opt/hoberadius/instance/` | persistent volume | SQLite DB |
| `/opt/hoberadius/instance/freeradius-clients-wizard/` | shared (hoberadius/freeradius) | Per-run client configs |
| `/etc/wireguard/wg0.conf` | root | WG server private key + listen port |
| `/etc/hoberadius/wg-peers.d/` | gid=999 | WG peer files (container writes, host reloads) |
| `/etc/hoberadius/nginx-streams.d/` | gid=999 | Per-router stream config for nginx |

---

## 2. متطلبات الخادم

### الحدّ الأدنى

- **OS:** Ubuntu 22.04+ أو Debian 12+
- **CPU:** 2 cores
- **RAM:** 2 GB
- **Disk:** 20 GB
- **Network:** عنوان IP عام ثابت

### حِزَم النظام

- `docker.io` (Docker Engine 24+)
- `docker-compose-v2`
- `wireguard-tools`
- `git`
- `curl`, `jq`, `python3` (للـ verification scripts)

### المنافذ المطلوب فتحها

| Port | Protocol | Purpose |
|------|----------|---------|
| `22` | TCP | SSH |
| `80` | TCP | HTTP admin panel |
| `443` | TCP | HTTPS admin panel (إذا فعّلت TLS) |
| `51820` | UDP | WireGuard (للراوترات) |
| `1812` | UDP | RADIUS Auth |
| `1813` | UDP | RADIUS Accounting |
| `3799` | UDP | RADIUS CoA (Disconnect/Update) |
| `51000-51199` | TCP | NPC remote tunnel (Winbox via VPS) |

```bash
# مثال UFW:
ufw allow 22/tcp 80/tcp 443/tcp
ufw allow 51820/udp
ufw allow 1812:1813/udp 3799/udp
ufw allow 51000:51199/tcp
ufw enable
```

---

## 3. التثبيت من الصفر

### الخطوة 1: تثبيت حِزَم النظام

```bash
apt update
apt install -y docker.io docker-compose-v2 wireguard-tools \
               git curl jq python3
```

### الخطوة 2: إعداد WireGuard على الـ host

```bash
# توليد المفاتيح
cd /etc/wireguard
umask 077
wg genkey | tee server_private.key | wg pubkey > server_public.key
chmod 600 server_private.key

# إنشاء wg0.conf
cat > /etc/wireguard/wg0.conf <<EOF
[Interface]
PrivateKey = $(cat /etc/wireguard/server_private.key)
ListenPort = 51820
Address    = 10.10.0.1/24

# الـ peers تُضاف ديناميكياً من /etc/hoberadius/wg-peers.d/
EOF

# تشغيل
systemctl enable --now wg-quick@wg0

# التحقّق
ip link show wg0
wg show
```

### الخطوة 3: Clone المشروع

```bash
git clone https://github.com/ahmadjamalahmad94-code/radius-module.git /opt/hoberadius
cd /opt/hoberadius
```

### الخطوة 4: إنشاء `.env`

```bash
# توليد الأسرار
INTERNAL_SECRET=$(openssl rand -hex 32)
WG_PUBKEY=$(cat /etc/wireguard/server_public.key)
PUBLIC_IP=$(curl -s ifconfig.me)

# كتابة .env
cat > /opt/hoberadius/.env <<EOF
HOBERADIUS_INTERNAL_SECRET=$INTERNAL_SECRET
HOBERADIUS_WG_SERVER_PUBKEY=$WG_PUBKEY
HOBERADIUS_WG_SERVER_ENDPOINT=$PUBLIC_IP:51820
HOBERADIUS_PUBLIC_HOST=$PUBLIC_IP
EOF

chmod 600 /opt/hoberadius/.env
```

### الخطوة 5: تهيئة الـ host directories

```bash
sudo bash /opt/hoberadius/deploy/deploy.sh init-wg-reloader
```

هذا يُنشئ:
- `/etc/hoberadius/wg-peers.d/` (gid=999, setgid)
- `/etc/hoberadius/nginx-streams.d/` (gid=999, setgid)
- `wg-reload.path` systemd unit (يراقب peers.d ويعمل `wg syncconf`)

### الخطوة 6: Build + Start

```bash
cd /opt/hoberadius
docker compose -f deploy/docker-compose.yml up -d --build
```

أوّل build يأخذ 2-3 دقائق.

### الخطوة 7: التحقّق

```bash
sudo bash /opt/hoberadius/deploy/fresh-install-check.sh
```

**الناتج المطلوب:** `OK ≥ 15, FAIL = 0`.

لو طلع `FAIL` → اقرأ الرسالة (تحوي الـ fix بالضبط) وأعد التحقّق.

### الخطوة 8: إنشاء حساب admin

```bash
docker exec hoberadius python -c "
from app import create_app
app = create_app()
with app.app_context():
    from app.radius.services.admins import create_admin
    create_admin('admin', 'CHANGE_THIS_PASSWORD', 'super')
    print('admin created')
"
```

افتح `http://YOUR_VPS_IP/admin` وسجّل دخول.

---

## 4. التحقّق بعد التثبيت

### Script التحقّق التلقائي

```bash
sudo bash /opt/hoberadius/deploy/fresh-install-check.sh
```

يفحص 8 طبقات (Host, Directories, systemd, .env, Containers, FreeRADIUS network, Schema, System health).

### System health endpoint

```bash
curl -s http://localhost/admin/radius/setup-wizard/_system_health \
  | jq -r '"overall: \(.overall)", (.checks | to_entries[] | "\(.value.status)  \(.key)  \(.value.details)")'
```

**Overall expected:** `healthy` (أو `degraded` لفترة قصيرة بعد كل deploy).

### Reachability tests

```bash
# RADIUS Auth (UDP 1812)
docker exec hoberadius-freeradius bash -c \
  "echo 'User-Name=test,User-Password=x' | radclient -x 127.0.0.1:1812 auth testing123 2>&1 | tail -3"
# Expected: Received Access-Reject (الرفض طبيعي، المهم أن في reply)

# Admin UI
curl -I http://localhost/admin/radius/_health
# Expected: HTTP/1.1 200 OK
```

---

## 5. إعداد الراوتر الأول

### من الـ UI

افتح `http://YOUR_VPS/admin/radius/setup-wizard-v3`.

الخطوات:

1. **عرّفنا على الراوتر**: اسم + نوع الخدمة (Hotspot / PPPoE / Mixed)
2. **مصدر الإنترنت**: DHCP / Static / PPPoE / VLAN (يُولِّد سكربت WAN)
3. **الربط بالخادم**: ضغطة واحدة → سكربت WireGuard + RADIUS موحَّد
4. **التحقّق**: فحص حقيقي عبر TCP probe على VPN tunnel
5. **خدمات إضافيّة** (اختياري): Hotspot / Broadband / Walled Garden / Block Sites
6. **التسجيل**: الـ API user يُولَّد تلقائياً

### من الراوتر (MikroTik):

في كل خطوة، الويزرد يعرض سطراً موحَّداً للنسخ:

```
/tool fetch url="http://YOUR_VPS/admin/radius/wz/<short>.rsc" mode=http dst-path="hr-setup.rsc"; /import file-name="hr-setup.rsc"
```

(سطر واحد بـ `;` للأمان — انظر postmortem #16)

### بعد الإنهاء

- الراوتر يظهر في `/admin/radius/setup-wizard/fleet`
- يظهر في `/admin/radius/devices`
- جاهز للمصادقة + CoA (Disconnect + Bandwidth changes)

---

## 6. المراقبة اليوميّة

### نقطة فحص واحدة

```bash
curl -s http://localhost/admin/radius/setup-wizard/_system_health | jq -r '.overall'
```

| النتيجة | المعنى | الإجراء |
|---------|--------|---------|
| `healthy` | كل شي تمام | لا شي |
| `degraded` | self-healed drift | راجع audit_log لاحقاً |
| `critical` | شي مكسور | حقّق فوراً |

### عرض تفصيلي

```bash
curl -s http://localhost/admin/radius/setup-wizard/_system_health \
  | jq -r '.checks | to_entries[] | "\(.value.status)  \(.key)  \(.value.details)"'
```

### عرض تصحيحات الـ reconciler

```bash
docker exec hoberadius python -c "
from app import create_app
app = create_app()
with app.app_context():
    from app.radius.db.connection import db
    rows = db().execute(
        \"SELECT created_at, action, target_id FROM audit_log \"
        \"WHERE actor='setup_wizard_radius_reconciler' \"
        \"AND created_at > datetime('now', '-24 hours') \"
        \"ORDER BY id DESC\"
    ).fetchall()
    for r in rows:
        print(f'[{r[\"created_at\"]}] {r[\"action\"]} target={r[\"target_id\"]}')
"
```

في إنتاج صحّي، النتيجة فارغة. أي سطر = drift اكتشف وتم تصحيحه.

### عرض الـ logs الحالية

```bash
docker logs hoberadius --tail 100 -f
docker logs hoberadius-freeradius --tail 100 -f
```

---

## 7. النسخ الاحتياطي

### اليومي (موصى به)

```bash
#!/bin/bash
# /etc/cron.daily/hoberadius-backup
DATE=$(date +%F)
BACKUP_DIR=/backup/hoberadius/$DATE
mkdir -p $BACKUP_DIR

# DB
cp /opt/hoberadius/instance/hoberadius.db $BACKUP_DIR/

# .env (للأسرار)
cp /opt/hoberadius/.env $BACKUP_DIR/

# WireGuard
cp -r /etc/wireguard $BACKUP_DIR/wireguard/

# Host config
cp -r /etc/hoberadius $BACKUP_DIR/hoberadius-etc/

# تنظيف backups أقدم من 30 يوم
find /backup/hoberadius -mindepth 1 -maxdepth 1 -type d -mtime +30 -exec rm -rf {} +
```

```bash
chmod +x /etc/cron.daily/hoberadius-backup
```

### الاسترجاع

```bash
# DB:
docker compose -f /opt/hoberadius/deploy/docker-compose.yml down
cp /backup/hoberadius/2026-05-26/hoberadius.db /opt/hoberadius/instance/
docker compose -f /opt/hoberadius/deploy/docker-compose.yml up -d
```

---

## 8. الترقية

### الترقية الآمنة

```bash
cd /opt/hoberadius

# 1. Backup
bash /etc/cron.daily/hoberadius-backup

# 2. Pull
git pull

# 3. Rebuild
docker compose -f deploy/docker-compose.yml up -d --build

# 4. Verify
sudo bash deploy/fresh-install-check.sh
```

**ما الفرق بين `restart` و `--build`:**
- `restart` لا يجلب التحديثات (يستخدم الـ image الموجود)
- `--build` يبني الـ image من الكود الجديد ← **استخدم هذا بعد كل `git pull`**

---

## 9. المشاكل المعروفة

### قائمة الـ Postmortems الـ 22

كل واحدة من هذي **مغلقة في الكود** بمنع التكرار:

| # | المشكلة | الإصلاح |
|---|---------|---------|
| 1 | عمود `state_json` ناقص في `setup_wizard_runs` | migration 076 |
| 2 | Browser cache يخدم JS قديم | `?v=YYYYMMDDx` cache-buster |
| 3 | `router_type=hybrid` غير مقبول | غيّر القيمة لـ `mixed` |
| 4 | JS يقرأ `script_body` و service يرجّع `script` | accept both keys |
| 5 | `:local pubkey` لا يستمر بين سطور MT Terminal | inline في سطر واحد |
| 6 | Test قديم يفشل | علامة informational فقط |
| 7 | UNIQUE constraint في test fixtures | counter دوّار |
| 8 | kwarg `target` vs `to_state` | تصحيح |
| 9 | تجاوز state وسيط في transitions | إضافة `router_key_received` |
| 10 | `restart` لا ينفع لتعديل template | لازم `--build` |
| 11 | `state_json` reset مفاجئ | لم يحدث بعد الـ atomic write |
| 12 | Hotspot NAT يفشل (WAN list فارغ) | bootstrap WAN list |
| 13 | Hotspot UI يقبل منفذ واحد | multi-select checkboxes |
| 14 | تكرار `/radius add` على re-paste | wrapped بـ `:if` guard |
| 15 | MikroTik Terminal يبتر سطور طويلة | use `/tool fetch + /import` |
| 16 | `/tool fetch` progress يبتلع السطر التالي | join بـ `;` في سطر واحد |
| 17 | `$INCLUDE *.conf` يكسر FR 3.x | directory form بـ `/` |
| 18 | hardcoded `mt_vpn_10_10_0_2` يتضارب | حذف الـ block |
| 19 | Docker SNAT يكسر CoA | `network_mode: host` |
| 20 | سرّ يختلف بين الراوتر/الخادم | atomic write + reconciler |
| 21 | reconciler يحذف tenants أخرى | global active-runs query |
| 22 | NAS بدون secret يكسر CoA | wizard يكتب secret في nas_devices |

التفاصيل الكاملة في `POSTMORTEM_V3_REBUILD_SESSION.md`.

---

## 10. إجراءات الاستعادة

### 🚨 سيناريو 1: FreeRADIUS crash-loop

**الأعراض:** subscribers يقولون "no response"؛ `docker logs hoberadius-freeradius` يكرّر `exited with code 0, restarting`.

```bash
# تشخيص:
docker exec hoberadius-freeradius freeradius -X 2>&1 | head -40

# إصلاح: تأكد من _placeholder.conf
docker exec hoberadius bash -c \
  'echo "# placeholder" > /app/instance/freeradius-clients-wizard/_placeholder.conf'
docker compose -f /opt/hoberadius/deploy/docker-compose.yml restart freeradius
```

### 🚨 سيناريو 2: CoA لا يعمل (disconnect / bandwidth change)

**الأعراض:** "لا يوجد جلسة متاحة" أو السرعة لا تتغيّر.

```bash
# تشخيص:
docker logs hoberadius --tail 50 | grep -i coa

# لو يقول "no enabled nas_devices row with a secret":
docker exec hoberadius python -c "
from app import create_app
app = create_app()
with app.app_context():
    from app.radius.services.setup_wizard_v3 import recover_nas_secrets_from_state_json
    print(recover_nas_secrets_from_state_json())
"
```

### 🚨 سيناريو 3: راوتر عالق في run فاشل

**الأعراض:** الراوتر في الـ fleet لكن لا يتصل، runs قديمة تتراكم.

من UI: اضغط زر "إلغاء المحاولة" على الراوتر المعطّل.

أو: انتظر TTL (30 دقيقة) للـ janitor يلتقطه.

أو من CLI:
```bash
docker exec hoberadius python -c "
from app import create_app
app = create_app()
with app.app_context():
    from app.radius.services.setup_wizard_tentative_reclaimer import SetupWizardTentativeReclaimer
    r = SetupWizardTentativeReclaimer().reclaim_all_expired(tenant_id=1, actor='manual')
    print(r)
"
```

### 🚨 سيناريو 4: Catastrophic reset مطلوب

**الحالة:** فوضى كاملة، تبيّ تبدأ من جديد بدون فقد المشتركين/البطاقات.

من UI: `/admin/radius/setup-wizard/fleet` → "🚨 تفريغ طوارئ" → اكتب `RESET-WIZARD-FLEET`.

هذا يمسح:
- router_provisioning_registry
- prepared_wireguard_peers
- setup_wizard_runs (+ steps + ops + snapshots)
- wizard-tagged nas_devices
- WG peer files (hr-peer-*, hr-router-*)
- wizard-clients-wizard/*.conf (via reconciler INV-2)

ولا يمسح:
- المشتركين
- البطاقات
- سياسات NPC غير tagged
- nas_devices أُضيفت يدوياً

### 🚨 سيناريو 5: قاعدة البيانات تالفة

```bash
# 1. أوقف
docker compose -f /opt/hoberadius/deploy/docker-compose.yml stop

# 2. استعد من backup
cp /backup/hoberadius/$(ls -t /backup/hoberadius | head -1)/hoberadius.db \
   /opt/hoberadius/instance/

# 3. شغّل
docker compose -f /opt/hoberadius/deploy/docker-compose.yml start
```

---

## 11. المراقبة الخارجيّة

### Uptimerobot / Healthchecks.io / Pingdom

```
URL:           http://YOUR_VPS/admin/radius/setup-wizard/_system_health
Method:        GET
Expected:      HTTP 200
Alert on:      HTTP 5xx, timeout
Interval:      5 minutes
```

### Cron-based alerting

```bash
# /etc/cron.hourly/hoberadius-health-check
#!/bin/bash
VERDICT=$(curl -s http://localhost/admin/radius/setup-wizard/_system_health | jq -r '.overall')
if [ "$VERDICT" = "critical" ]; then
    echo "$(date): HobeRadius CRITICAL" | mail -s "ALERT" admin@yourdomain.com
fi
```

### Prometheus / Grafana

`/admin/radius/_system_health` يرجّع JSON قابل للـ scrape. Sample:

```promql
# (يحتاج exporter بسيط يحوّل JSON → metrics)
hoberadius_check_status{check="wizard_invariants"} 1   # 1=ok, 0.5=warn, 0=fail
hoberadius_overall_status                              0=critical, 0.5=degraded, 1=healthy
```

---

## 12. الـ Invariants

### السبع invariants المضمونة

| # | Invariant | المنفّذ | الكشف |
|---|-----------|---------|------|
| INV-1 | لكل run نشط، ملفّ بسرّ متطابق مع state_json | atomic write + reconciler | `wizard_invariants` |
| INV-2 | لكل ملفّ، run نشط في أي tenant | reconciler (global) | `wizard_invariants` |
| INV-3 | لا أكثر من ملفّ لـ ipaddr واحد | _purge_stale + reconciler | `wizard_invariants` |
| INV-4 | الراوتر لا يرى سكربت قبل كتابة الخادم | atomic generate_unified_script | راوتر بدون file = error |
| INV-5 | كل nas_devices wizard-tagged له secret | wizard register + system_health | `wizard_nas_secrets` |
| INV-6 | لا حذف ملفّات tenant آخر | global active-runs query | test regression |
| INV-7 | الـ worker لا يموت بصمت | nested try/except | `recent_reconciler_drift` (غياب = warn) |

### مكتسبات الأمان

- **Atomic provisioning**: مستحيل للراوتر يحمل سرّ ما يعرفه الخادم
- **Cross-tenant safety**: مستحيل reconciler يحذف data من tenant مختلف
- **Worker resilience**: لا يموت من خطأ عابر
- **Audit trail**: كل تصحيح مُسجَّل في audit_log
- **Forensic SHA**: لا secrets في الـ logs، فقط `sha256[:12]` prefix

---

## 13. مرجع سريع

### التحقّق الشامل

```bash
sudo bash /opt/hoberadius/deploy/fresh-install-check.sh
```

### System health JSON

```bash
curl -s http://localhost/admin/radius/setup-wizard/_system_health | jq
```

### عرض الـ recent reconciler activity

```bash
docker exec hoberadius python -c "
from app import create_app
app = create_app()
with app.app_context():
    from app.radius.db.connection import db
    for r in db().execute(\"SELECT created_at, action, target_id FROM audit_log WHERE actor LIKE 'setup_wizard%' ORDER BY id DESC LIMIT 20\").fetchall():
        print(f'[{r[\"created_at\"]}] {r[\"action\"]} target={r[\"target_id\"]}')
"
```

### NAS list

```bash
docker exec hoberadius python -c "
from app import create_app
app = create_app()
with app.app_context():
    from app.radius.db.connection import db
    for r in db().execute('SELECT id, name, address, tags FROM nas_devices WHERE enabled=1').fetchall():
        print(f'#{r[\"id\"]} {r[\"name\"]} @ {r[\"address\"]} ({r[\"tags\"]})')
"
```

### Active wizard runs

```bash
docker exec hoberadius python -c "
from app import create_app
app = create_app()
with app.app_context():
    from app.radius.db.connection import db
    for r in db().execute(\"SELECT id, v3_state, current_step FROM setup_wizard_runs WHERE v3_state IS NOT NULL ORDER BY id DESC LIMIT 10\").fetchall():
        print(f'run={r[\"id\"]} state={r[\"v3_state\"]} step={r[\"current_step\"]}')
"
```

### Restart freeradius فقط

```bash
docker compose -f /opt/hoberadius/deploy/docker-compose.yml restart freeradius
```

### Full rebuild

```bash
cd /opt/hoberadius && git pull && \
  docker compose -f deploy/docker-compose.yml up -d --build
```

### Logs streaming

```bash
docker logs hoberadius -f
docker logs hoberadius-freeradius -f
```

### Emergency recovery (NAS secrets)

```bash
docker exec hoberadius python -c "
from app import create_app
app = create_app()
with app.app_context():
    from app.radius.services.setup_wizard_v3 import recover_nas_secrets_from_state_json
    print(recover_nas_secrets_from_state_json())
"
```

### Force reconciler now

```bash
docker exec hoberadius python -c "
from app import create_app
app = create_app()
with app.app_context():
    from app.radius.services.setup_wizard_v3_radius_server_provisioning import reconcile_with_state
    print(reconcile_with_state(tenant_id=None))
"
```

---

## ملاحظات نهائيّة للمشغّل التجاري

### عند البيع لعميل جديد

1. **اتبع خطوات القسم #3** بالضبط
2. **شغّل fresh-install-check.sh** قبل التسليم — لازم يطلع `OK ≥ 15, FAIL = 0`
3. **علّم العميل على نقطتين فقط:**
   - `_system_health` endpoint للمراقبة
   - الـ wizard لإضافة راوترات
4. **اتفق على backup schedule** (يومي موصى به)
5. **سلّم نسخة من `.env`** بشكل آمن (offline channel)

### للدعم الفنّي

1. **ابدأ بـ `_system_health`** — يحدّد الطبقة المعطّلة
2. **اقرأ audit_log** للتاريخ الكامل
3. **استخدم postmortem #1-#22** كقائمة diagnostic للأنماط المعروفة
4. **لو في نمط جديد** — أضفه postmortem #23 → fix → test → push

### الترقية للإصدارات القادمة

كل ترقية يجب أن:
1. تَمرّ من tests
2. تَمرّ من `fresh-install-check.sh`
3. تَمرّ من `_system_health` بعد deploy
4. توثَّق أي تغيير سلوكي

---

**نهاية الدليل**

للمساعدة الإضافيّة، راجع:
- `POSTMORTEM_V3_REBUILD_SESSION.md` — تفاصيل كل مشكلة
- `TROUBLESHOOTING_GUIDE.md` — أعراض → حلول
- `setup_wizard_v3_radius_server_provisioning.py` (الكود) — توثيق inline للـ invariants
