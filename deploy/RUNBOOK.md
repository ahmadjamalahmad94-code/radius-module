# HobeRadius — RUNBOOK (S5 Live Test)

> دليل تشغيل وتجربة Phase-1 على VPS حقيقي مع MikroTik فعلي.
> اتبع الخطوات بالترتيب. كل خطوة لها **معيار نجاح** صريح.

---

## A. المتطلّبات قبل البدء

| البند | الحد الأدنى |
|------|------------|
| VPS | Ubuntu 22.04+ · 1 vCPU · 1GB RAM · 20GB SSD |
| Network | IPv4 عام + port 80/443 مفتوح + (اختياري) 8728/8729 للوصول من Mikrotik للـ VPS |
| Domain | DNS A record لـ VPS IP (اختياري للـ TLS) |
| MikroTik | RouterOS ≥ 6.45 (موصى به 7.x) + API enabled |
| على جهازك | ssh + scp |

### تجهيز MikroTik (مرّة واحدة):
```
# على RouterOS:
/ip service set api disabled=no port=8728
# اختياري للأمان: TLS
/ip service set api-ssl disabled=no port=8729 certificate=auto
# مستخدم لـ HobeRadius (احذف group=full إن أردت تقييد أكثر):
/user group add name=hr-api policy=read,write,api,test
/user add name=hr password=STRONG-PASS group=hr-api
```

تحقّق:
```
/ip service print where name~"api"
/user print where name=hr
```

---

## A.2 تفعيل FreeRADIUS

> هذا القسم لازم لأي تجربة حقيقية مع MikroTik. بدون freeradius container لن
> يردّ الـ VPS على UDP 1812/1813 وبالتالي MikroTik `/radius test` يعطي
> "no response".

### تشغيل freeradius مع باقي الـ stack

```bash
# على الـ VPS، داخل /opt/hoberadius:
docker compose -f deploy/docker-compose.yml up -d freeradius
docker compose -f deploy/docker-compose.yml ps
```

**معيار النجاح**: `docker compose ps` يُظهر **4 خدمات** (app + nginx + backup + **freeradius**) كلها بحالة `Up` أو `running`.

### تحقّق سريع

```bash
# 1. منافذ UDP منشورة على الـ host
ss -lunp | grep -E ':1812|:1813|:3799'
# يجب أن يظهر كل من 1812 و 1813 (و 3799 إن فعّلت CoA)

# 2. سكربت السموك الجاهز
bash deploy/smoke_freeradius.sh
# يُكمل بـ "smoke FreeRADIUS مكتمل."
```

### secret المطلوب في .env

قبل أوّل `up`:
```bash
# على الـ VPS:
cd /opt/hoberadius
grep HOBERADIUS_INTERNAL_SECRET .env
# لو فارغ:
sed -i "s|^HOBERADIUS_INTERNAL_SECRET=.*|HOBERADIUS_INTERNAL_SECRET=$(openssl rand -hex 32)|" .env
docker compose -f deploy/docker-compose.yml up -d --force-recreate hoberadius freeradius
```

> ⚠️ نفس القيمة لازم تظهر في الـ env لكلا الـ containers — `${HOBERADIUS_INTERNAL_SECRET:-}` في compose يقرأها من الـ .env تلقائيًا.

### NAS shared secret (testing123 افتراضيًا)

`deploy/freeradius/clients.conf` يحوي client `testing123` لـ `127.0.0.1` + شبكة docker الداخلية فقط — مفيد للتجربة من الـ VPS نفسه.

للـ MikroTik الحقيقي، أضِف الجهاز من `/admin/radius/devices`. الـ secret اللي تضعه هناك هو الـ shared secret الذي يقرأه FreeRADIUS من جدول `nas` تلقائيًا (بدون restart) — انظر `mods-enabled/sql: read_clients = yes`.

### اختبار من MikroTik

```routeros
# على RouterOS:
/radius add service=hotspot address=<VPS_PUBLIC_IP> secret=<MY_NAS_SECRET>
/radius test [find address=<VPS_PUBLIC_IP>] user=qa-smoke password=qa-smoke
# المتوقّع: "rejected" (لأن المستخدم ليس مُسجَّلًا) — لكن وصول الـ rejected نفسه
# يثبت أن FreeRADIUS يردّ. "no response" يعني المنافذ مغلقة.
```

افتح المنافذ على firewall الـ VPS:
```bash
sudo ufw allow 1812/udp
sudo ufw allow 1813/udp
sudo ufw allow 3799/udp     # اختياري للـ CoA
```

### troubleshooting سريع

| العَرَض | السبب الأرجح | الحل |
|--------|--------------|------|
| `docker compose ps` لا يُظهر freeradius | الـ image لم تُبنَ | `docker compose -f deploy/docker-compose.yml build freeradius` ثم `up -d freeradius` |
| freeradius يتكرّر restart | secret فارغ أو خطأ config | `docker logs hoberadius-freeradius -n 100` |
| MikroTik test = "no response" | منفذ مغلق على firewall | `sudo ufw status` ثم افتح 1812/1813/udp |
| MikroTik test = "rejected" بدون debug | طبيعي — FreeRADIUS وصلت، فقط المستخدم غير موجود |
| logs تُظهر "X-Internal-Secret mismatch" | .env و freeradius env مختلفان | حدّث `.env` ثم `docker compose up -d --force-recreate` |

---

## B. النشر على VPS (5 دقائق)

### 1. SSH وتجهيز
```bash
ssh root@YOUR_VPS_IP
cd /opt
git clone <repo-url> hoberadius
cd hoberadius
chmod +x deploy/deploy.sh deploy/backup.sh deploy/restore.sh
```

### 2. تثبيت + بناء + تشغيل (أمر واحد)
```bash
sudo bash deploy/deploy.sh init
```

سيقوم بـ:
1. تثبيت Docker إن لزم.
2. توليد `.env` بـ SECRET عشوائي.
3. بناء الصورة.
4. تشغيل containers (app + nginx + backup).
5. انتظار healthcheck.

**معيار النجاح**: تنتهي الرسالة بـ `✅ تم`.

### 3. TLS (اختياري لكن موصى به)
```bash
sudo bash deploy/deploy.sh tls radius.example.com
```

**معيار النجاح**: `curl -fsS https://radius.example.com/admin/radius/_health` يردّ 200.

---

## B.4 نفق إدارة SSTP/PPTP (راوترات RouterOS 6) — اختياري

راوترات RouterOS 6 لا تدعم WireGuard، فنفق الإدارة الدائم (راوتر → VPS) يخدمه
**accel-ppp على المضيف** (SSTP :443 / PPTP :1723) ويصادق حسابات `rtr-*` مقابل
FreeRADIUS داخل الحاوية. تخطَّ هذا القسم كليًّا لو راوتراتك تستخدم WireGuard (v7).
المرجع الكامل: `deploy/accel-ppp/README.md`.

> **التبعيّة:** نفذ هذا **بعد** B.2 (الـ stack يعمل) و A.2 (حاوية freeradius
> تعمل) — المُثبِّت يكتب ملف عميل FreeRADIUS ويعتمد على watcher الحاوية لإعادة
> التحميل. كل شيء عدا قرار المنفذ (الخطوة 1) مؤتمت وحَتمِيّ (idempotent).

### 1. ⚠️ قرار المنفذ :443 — nginx مقابل accel (يدويّ، إلزاميّ)

SSTP الافتراضي على :443، ولا يمكن لعمليّتين حجزه على المضيف. الوضع **الافتراضيّ
الحاليّ**: accel يملك `:443` وحاوية nginx تنشر `80` و`8443` فقط (اللوحة على
`https://<IP>:8443`) — فلا تعارض، ولا حاجة لقرارٍ إن قبلت هذا.

اختر غير ذلك فقط لو أردت **رابط لوحة بلا رقم منفذ**:

* **(أ) accel يبقى على :443 (الافتراضيّ — لا عمل):** اللوحة على `:8443`.
* **(ب) اللوحة على :443 وSSTP يُزاح** — بهذا **الترتيب** حصرًا:
  1. `.env`: `HOBERADIUS_ACCEL_SSTP_PORT=4443` (أو أيّ منفذ حرّ)،
  2. `sudo deploy/accel-ppp/install-accel-selfsigned.sh` (يُعيد توليد
     `/etc/accel-ppp.conf` ويتحقّق أنّ المستمع صار على المنفذ الجديد)،
  3. `.env`: `HOBERADIUS_PANEL_HTTPS_PUBLISH=443:8443`،
  4. `docker compose -f deploy/docker-compose.yml up -d nginx`.
  عملاء SSTP في RouterOS يُضبطون على `<VPS_IP>:4443` — ومولّد سكربتات نفق
  الإدارة و«اتصال البيانات» يقرأ المفتاح نفسه فيكتب المنفذ الصحيح تلقائيًّا.
  ⚠️ عكس الترتيب (nginx أوّلًا) = حاوية nginx لا تُقلع («address already in use»).

افتح منفذ SSTP المختار (و1723 لـ PPTP) في الجدار الناريّ:
```bash
sudo ufw allow 443/tcp   # أو 4443/tcp حسب اختيارك
sudo ufw allow 1723/tcp  # PPTP الاحتياطيّ (اختياريّ)
```

### 2. تشغيل المُثبِّت (مؤتمت، idempotent، آمن للتكرار)
```bash
# داخل /opt/hoberadius على الـ VPS، بصلاحيات root:
sudo deploy/accel-ppp/install-accel-selfsigned.sh
```
يقوم تلقائيًّا بـ: توليد `/etc/accel-ppp.conf` عبر مولّد stdlib (بلا Flask على
المضيف)، فحص تعارض المنفذ، تحميل وحدات PPP + `/dev/ppp`، سكّ شهادة موقّعة ذاتيًّا،
ربط البوّابة (10.50.0.1) على `lo` + وحدة systemd دائمة، كتابة عميل FreeRADIUS
(`accel-local-sstp`) بنفس سرّ accel (مصدر واحد)، ثم فحوصات إقلاع (مستمع + مصافحة
TLS). **معيار النجاح:** السطر `✔ مصافحة TLS 1.2 نجحت`.

### 3. تجهيز حسابات rtr-* (مؤتمت عند إقلاع اللوحة)
حسابات `rtr-<router>` تُنشأ تلقائيًّا في `radcheck` (Cleartext + NT-Password
بصيغة 0x + WAL checkpoint) عبر `reconcile_tunnel_accounts` عند كل إقلاع للوحة —
لكل راوتر مُسجَّل سلفًا كـ `sstp_mgmt`/`pptp_mgmt` في `nas_devices`. لإجبار تمريرة
فورًا بعد التثبيت:
```bash
docker compose -f deploy/docker-compose.yml restart hoberadius
```
أنشئ راوترات النفق الجديدة من معالج اللوحة (يكتب صفّ `nas_devices`)، ثم تتكفّل
تمريرة المصالحة بحساب RADIUS المطابق.

### 4. إعداد عميل SSTP في RouterOS
```
/interface sstp-client add name=hobe-mgmt connect-to=<VPS_IP>:<المنفذ> \
  user=rtr-<router-slug> password=<من اللوحة> \
  profile=default verify-server-certificate=no disabled=no
```
> `profile=default` (وليس `default-encryption`) — SSTP مغلّف بـTLS سلفًا.

### 5. تحقّق
```bash
# على المضيف:
ss -ltnp 'sport = :443'           # يجب أن يملكه accel-pppd
journalctl -u accel-ppp -n 50     # تفاوض الجلسة
# على RouterOS: /interface sstp-client print  → status: connected
```

---

## C. الإعداد الأوّلي

### 1. تسجيل الدخول
- افتح: `http://VPS_IP/` (منفذ 80) **أو** `https://VPS_IP:8443/` (TLS موقّع ذاتيًّا — اقبل تحذير المتصفّح مرّة).
  - منفذ 8443 يجب أن يكون مفتوحًا في الجدار الناريّ: `ufw allow 8443/tcp`.
  - منفذ 443 محجوز لـaccel/SSTP — لا يُستخدم للوحة.
- login: **`admin / admin`**
- **فورًا**: اذهب لـ `/admin/radius/admins` → غيّر كلمة admin.

### 2. إنشاء API Token
- `/admin/radius/tokens` → "إنشاء توكن" → سمّه `prod-key`.
- **انسخ القيمة الكاملة فورًا** (تظهر مرة واحدة).
- احفظها في مكان آمن (سنستخدمها للـ smoke test).

### 3. إضافة MikroTik connection
- `/admin/radius/mt/new`
- املأ: host, port (8728), username (hr), password (STRONG-PASS).
- اختر **اختبار** — يجب أن يقرأ identity من الـ router.
- **معيار النجاح**: رسالة "اتصال ناجح: <router-name>".

### 4. إنشاء Plan
- `/admin/radius/plans/new` → مثال: name=`hour-plan`, speed_down=4096, speed_up=2048, session_timeout=3600s.
- بعد الحفظ: `/admin/radius/sync` → ترى job `plan_upsert` → خلال 3 ثوانٍ → status=`done`.
- **معيار النجاح**: على الـ router:
  ```
  /ip hotspot user profile print where name=hour-plan
  ```
  يظهر الـ profile.

### 5. إنشاء Subscriber
- `/admin/radius/users/new` → username=`testuser`, password=`test123`, plan=hour-plan.
- `/admin/radius/sync` → status=`done`.
- على الـ router:
  ```
  /ip hotspot user print where name=testuser
  ```
  يجب أن يظهر.

---

## D. الاختبار الحي مع موبايل

### 1. الاتصال بـ Hotspot
- اتصل بـ Wi-Fi الـ MikroTik HotSpot.
- في صفحة الدخول: `testuser / test123` → نجاح.

### 2. تحقّق من الجلسة في HobeRadius
- `/admin/radius/online` → خلال 5-30 ثانية تظهر الجلسة (IP, MAC, NAS).
- على الـ router للمقارنة: `/ip hotspot active print where user=testuser`.

### 3. تحقّق من accounting
- انتظر 30+ ثانية.
- `/admin/radius/users/testuser/edit` (مؤقتًا، حتى نضيف tab) — استخدم API:
  ```bash
  curl -fsS -H "Authorization: Bearer YOUR_TOKEN" \
       https://YOUR_DOMAIN/api/v1/accounting?username=testuser | jq
  ```
- يجب أن تجد row مع bytes_in/out.

### 4. قطع الجلسة
- `/admin/radius/online` → "قطع" بجانب الجلسة.
- خلال 3 ثوانٍ: الموبايل ينقطع فعلًا.
- audit: `/admin/radius/audit` → سطر `disconnect`.

### 5. accounting بعد الانقطاع
- بعد 30s إضافية: row في `radacct` بـ `acctstoptime` مُعبّأ.
- webhook (إن مُعدّ): `session.stopped` event.

---

## E. اختبار Webhooks

### 1. إعداد target
- `/admin/radius/webhooks` → target_url=`https://webhook.site/your-unique-id` + secret كيفما تشاء.
- اضغط حفظ.

### 2. تجربة
- API: `curl -X POST -H "Authorization: Bearer YOUR_TOKEN" https://YOUR_DOMAIN/api/v1/webhooks/test`
- على webhook.site → يجب أن يصل request مع `X-HobeRadius-Signature`.

### 3. أحداث حقيقية
- أنشئ subscriber آخر → webhook `account.created`.
- اقطع جلسة → `session.disconnected`.
- `/admin/radius/webhooks/deliveries` → سجل الإرسال.

---

## F. تحقّق Restart Persistence

```bash
docker compose -f deploy/docker-compose.yml restart app
sleep 5
curl -fsS https://YOUR_DOMAIN/admin/radius/_healthz
```

ثم في المتصفّح:
- login يعمل (admin/passwd الجديد).
- subscribers/plans/cards الموجودة.
- `radacct` ما زال يحوي الجلسات السابقة.

**معيار النجاح**: كل البيانات موجودة + healthy.

---

## G. Smoke Test تلقائي

من جهازك (أو من VPS نفسه):
```bash
python tests/smoke_e2e.py \
    --url https://radius.example.com \
    --token YOUR_TOKEN \
    --mt "host=10.0.0.1,user=hr,pass=STRONG-PASS,port=8728"
```

**معيار النجاح**: `✅ كل الاختبارات نجحت.` في الأسفل.

---

## H. مراقبة استمرارية التشغيل (15 دقيقة)

### الواجهة المُلتقطة:
- `/admin/radius/_status` — auto-refresh كل 10s.
- يجب أن ترى:
  - 3 workers `is_alive=true`
  - sync_queue: queued ينخفض → done يرتفع
  - MT Routers: enabled count > 0
  - last_seen_at للـ MT يتحدّث

### الـ logs:
```bash
sudo bash deploy/deploy.sh logs
```

ابحث عن:
- ❌ `Traceback` → خطأ غير متوقّع، أبلغني
- ✅ `sync_worker job=N done` → يعمل
- ✅ `webhook sent event=...` → webhooks تخرج

---

## I. ما الذي تتفقّده يوميًا (Sanity Checks)

| البند | الأمر | المتوقّع |
|------|-------|---------|
| Health | `curl https://X/admin/radius/_healthz` | `status: ok` |
| Disk | `du -sh /opt/hoberadius/instance/` | `< 200MB في أول شهر` |
| Backups | `ls -lh /opt/hoberadius/backups/` | يومي، آخر 14 |
| Sync queue stuck | `/admin/radius/sync?status=failed` | فارغة أو معالَجة |
| MT last_seen | `/admin/radius/_status` | < ساعة |

---

## J. Troubleshooting

| العَرَض | الفحص | الحل |
|---------|--------|------|
| login يفشل | `docker compose ps app` healthy? | restart container |
| sync فاشل لكل jobs | افحص `/admin/radius/mt/<id>/test` | جدّد credentials، تأكد port 8728 مفتوح من VPS لـ MT |
| `connection refused` على MT | `nc -zv MT_IP 8728` من VPS | افتح firewall على MT أو غيّر network |
| webhooks لا تصل | `/admin/radius/webhooks/deliveries` | افحص target_url، آخر error excerpt |
| Disk full بسرعة | `du -sh /opt/hoberadius/*` | راجع logs/ → `docker system prune -af` |
| Login redirect loop | امسح cookies للـ domain | جلسة قديمة من قبل تغيير SECRET |
| 502 Bad Gateway | `docker compose logs nginx` | تأكد app يستجيب على 8000 |
| DB locked | تتم محاكاتها تلقائيًا (busy_timeout=30s) | لو استمرّت: `docker compose restart app` |

---

## K. خطوات التراجع (Rollback)

```bash
# 1. أوقف
sudo bash deploy/deploy.sh status     # تأكد من الإصدار
docker compose -f deploy/docker-compose.yml down

# 2. استعد آخر backup
ls /opt/hoberadius/backups/
sudo bash deploy/restore.sh /opt/hoberadius/backups/hoberadius-YYYYMMDD-HHMMSS.db.gz

# 3. ارجع لـ commit سابق
cd /opt/hoberadius && git log --oneline | head -10
git checkout <previous-sha>

# 4. شغّل
sudo bash deploy/deploy.sh upgrade
```

---

## L. معايير نجاح S5 الكاملة

أنجزت S5 بنجاح **فقط** عند تحقّق هذه:

- [ ] `deploy.sh init` يعمل من VPS فارغ
- [ ] TLS مفعَّل (إذا domain)
- [ ] login + change password
- [ ] إنشاء MT config + اختبار = success
- [ ] إنشاء plan → ظهور على MT خلال 5s
- [ ] إنشاء subscriber → ظهور على MT خلال 5s
- [ ] موبايل يتصل بـ Hotspot
- [ ] `/online` يعرض الجلسة الحية
- [ ] قطع جلسة من UI → الموبايل ينقطع
- [ ] accounting puller يكتب radacct خلال 30s
- [ ] webhook test ينجح + الـ signature صحيحة
- [ ] restart container → كل البيانات موجودة
- [ ] `smoke_e2e.py` ينجح
- [ ] **15 دقيقة تشغيل بدون crash**
- [ ] sync_queue.failed = 0 بعد كل العمليات

عند تحقّق **كل ما سبق** = Phase-1 مُسلَّمة فعليًا.
