# HobeRadius — VPS Provisioning RUNBOOK (`deploy/provision/`)

> هدف هذا المجلّد: **(أ)** تكتشف كل تعديلاتك اليدويّة على الـ VPS الحالي،
> و**(ب)** تُنشئ VPS جديدًا **مطابقًا** للإنتاج بضغطة واحدة قابلة للتكرار،
> و**(ج)** تتحقّق أن الجديد يطابق القديم.
>
> **مبدأ أمان:** لا سرّ (secret/key) يُكتب في الريبو أبدًا. ملفّات العميل
> (`.env`، مفاتيح WG، مفتاح الترخيص) تبقى محليّة على الخادم فقط.

الملفّات:

| الملف | يُشغَّل على | ماذا يفعل |
|------|-----------|-----------|
| `inventory-current-vps.sh` | الـ VPS **الحالي** | قراءة فقط — يكتشف كل شيء + «تقرير التعديلات اليدويّة» |
| `provision-fresh-vps.sh` | الـ VPS **الجديد** | يُنشئ الحزمة كاملة، idempotent، خطوة‑بخطوة |
| `verify-parity.sh` | الـ VPS **الجديد** | يعيد الجرد ويقارن بالمصدر + فحوصات حيّة → PASS/فروق |
| `_lib.sh` | (مكتبة مشتركة لـ provision/verify) | — |

---

## المعماريّة باختصار (تحقّق دائمًا من الريبو، لا من الذاكرة)

- **radius-module** = حزمة Docker: `hoberadius` (اللوحة، منفذ 8000 على loopback فقط)،
  `hoberadius-freeradius` (شبكة host، UDP 1812/1813/3799)، `hoberadius-nginx`
  (80 + 8443 + منافذ 51000‑51199 للأنفاق البعيدة)، `hoberadius-backup`.
  النشر = `git pull` + `docker compose build --no-cache && up -d`.
- **الجداول تُنشأ تلقائيًّا** عند إقلاع اللوحة (`app/__init__` → `_init_db` +
  migrations). لا تُنشئ الجداول يدويًّا. إصدار الـ schema في جدول `_migrations`.
- **accel-ppp على المضيف** يوفّر نفق الإدارة SSTP (`assel`) على `:443`
  (`deploy/accel-ppp/install-accel-selfsigned.sh` → `/etc/accel-ppp.conf`).
- **WireGuard `wg0`** (افتراضيًّا `10.10.0.0/24`، السيرفر `10.10.0.1`) لنفق إدارة
  الراوترات. النظائر (peers) تُزوَّد لكل راوتر من اللوحة — تبدأ فارغة على الجديد.
- **nginx** حدّ الرفع: `client_max_body_size 16M` عامّ + **`1024m` على
  `/admin/radius/migrate/`** (رفع ملفّات الترحيل الكبيرة). قد تكون لديك تعديلات
  يدويّة على `deploy/nginx.conf` / `deploy/nginx-tls-8443.conf`.
- **radius-proxy** دور VPS منفصل (WG + ترحيل نسخ الترخيص فقط).
- **لوحة الترخيص** المركزيّة تحدّد الخدمات المفعّلة لكل عميل.

---

## STEP 0 — اكتشف تعديلاتك على الـ VPS الحالي (قراءة فقط، لا تغيير)

الـ VPS الحالي غالبًا على checkout أقدم لا يحوي هذا المجلّد بعد. لذا **انسخ سكربت
الجرد وحده** (مكتفٍ ذاتيًّا) وشغّله:

```bash
# من جهازك:
scp deploy/provision/inventory-current-vps.sh root@CURRENT_VPS:/root/

# على الـ VPS الحالي:
sudo bash /root/inventory-current-vps.sh -o /root/hr-inv
#   لو الجذر ليس /opt/hoberadius:
#   HR_ROOT=/path/to/hoberadius sudo -E bash /root/inventory-current-vps.sh -o /root/hr-inv
```

يخرج ملفّان في `/root/hr-inv/`:

1. **`vps-drift-report.txt`** ← **اقرأه أوّلًا.** كل تعديلاتك اليدويّة مجموعة
   ومقروءة: فروق nginx (حدّ الرفع/TLS)، أقسام accel، عملاء FreeRADIUS، إلخ.
2. **`vps-manifest.json`** ← بصمة كاملة يقرأها `provision` و`verify` آليًّا.

انسخهما لجهازك:
```bash
scp root@CURRENT_VPS:/root/hr-inv/vps-*.* ./
# ولو لديك تعديلات nginx يدويّة تريد نقلها حرفيًّا:
scp root@CURRENT_VPS:/opt/hoberadius/deploy/nginx.conf ./nginx.conf.current
scp root@CURRENT_VPS:/opt/hoberadius/deploy/nginx-tls-8443.conf ./nginx-tls.current
```

> ما يلتقطه الجرد: نظام التشغيل/النواة، الحزم، إصدارات Docker/compose، الحاويات
> وصورها، `docker compose config` الفعّال، مفاتيح `.env` (بصمة للأسرار لا قيمتها)،
> nginx الحيّ + حدّ الرفع + فروق vs الريبو، `/etc/accel-ppp.conf` + مالك :443 +
> `/dev/ppp` + وحدات PPP، wg0 + النظائر + subnet (بلا مفاتيح خاصّة)، FreeRADIUS
> mods/sites/clients + مجلّد `$INCLUDE`، iptables/mgmt-confinement، وحدات systemd،
> cron، عناوين lo اليدويّة، مسار DB + إصدار الهجرات + الجداول وعدد صفوفها (بنية
> وأعداد فقط، **لا بيانات**)، ربط الترخيص (مفتاح مُخفى)، وHEAD لكل ريبو.

---

## STEP 1..n — أنشئ الـ VPS الجديد

على Ubuntu نظيف (22.04+، 1vCPU/1GB كحدّ أدنى، IPv4 عام، منافذ 80/8443/443 و
UDP 1812/1813 و WG UDP مفتوحة):

```bash
# 1) اجلب أدوات التزويد (الريبو كامل أو المجلّد فقط):
git clone https://github.com/ahmadjamalahmad94-code/radius-module.git /tmp/rm
# (أو scp deploy/provision/ كاملًا للخادم)

# 2) شغّل التزويد (idempotent — أعد تشغيله بأمان):
sudo bash /tmp/rm/deploy/provision/provision-fresh-vps.sh \
     --sha origin/main \
     --role app \
     --manifest ./vps-manifest.json \
     --nginx-conf ./nginx.conf.current \
     --nginx-tls  ./nginx-tls.current
```

خيارات:
- `--sha <git-sha>` — ثبّت إصدارًا محدّدًا (افتراضي `origin/main`). للإنتاج ثبّت SHA.
- `--role app|proxy` — `proxy` يستنسخ radius-proxy أيضًا.
- `--manifest FILE` — يعيد إنتاج تعديلاتك (حدّ الرفع، subnet، ربط الترخيص…). بدونه = افتراضات الريبو.
- `--nginx-conf/--nginx-tls FILE` — يضع ملفّات nginx المخصّصة التي التقطتها حرفيًّا.

الخطوات التي ينفّذها (كلٌّ محروس، يتخطّى لو تمّ):
1. تثبيت Docker + الأدوات. 2. استنساخ الريبو عند SHA. 3. كتابة `.env` (يولّد
`FLASK_SECRET` + `HOBERADIUS_INTERNAL_SECRET`، يطلب/يحمل الباقي). 4. تجهيز مجلّدات
المضيف. 5. إعادة تطبيق تعديلات nginx. 6. رفع نفق `wg0` (يولّد مفاتيح جديدة، يطبع
pubkey). 7. `docker compose build --no-cache && up -d` (الإقلاع يُنشئ DB +
migrations = الجداول). 8. مثبّت accel/assel. 9. جدار mgmt-confinement. 10. إرشاد
ربط الترخيص من اللوحة. 11. تحقّق ذاتي.

> **الأسرار:** تُكتب في `/opt/hoberadius/.env` (مُتجاهَل في git). مفتاح الترخيص
> يُدخَل من اللوحة (سرّ لكل عميل)، لا من السكربت.

---

## التحقّق

```bash
sudo bash /opt/hoberadius/deploy/provision/verify-parity.sh \
     --manifest ./vps-manifest.json
```

يطبع `PASS` أو قائمة الفروق بدقّة: الحاويات، المنافذ (80/8443/1812/1813/443)،
حدّ الرفع، wg0 up، إصدار الهجرات، `radtest`، وصول اللوحة. (اضبط `RADTEST_USER`/
`RADTEST_PASS` لفحص radtest، أو شغّل `deploy/smoke_freeradius.sh`.)

بعد النجاح: افتح `http://<VPS_IP>/admin/radius/login` (admin/admin — **غيّرها فورًا**)
وأكمِل ربط الترخيص من صفحة الجسر.

---

## Rollback / إن فشل شيء

- التزويد idempotent: صحّح السبب وأعد تشغيل نفس الأمر — يتخطّى ما تمّ (علامات في
  `/var/lib/hoberadius-provision/*.done`؛ احذف علامةً لإعادة خطوة).
- الحاويات فقط: `cd /opt/hoberadius && docker compose -f deploy/docker-compose.yml down` ثم `up -d`.
- تراجُع كامل: احذف `/opt/hoberadius` و`/etc/wireguard/wg0.conf` و`/etc/accel-ppp.conf`
  و`/var/lib/hoberadius-provision/` وأعد التزويد (البيانات في `instance/` — خذ نسخة أولًا).
- استعادة بيانات: انسخ `instance/hoberadius.db` من نسخة احتياطيّة قبل `up` (اللوحة
  تُشغّل الهجرات تلقائيًّا على القاعدة المستعادة).

---

## مزالق النشر المعروفة (Gotchas)

1. **`git pull` يتوقّف بسبب تعديلاتك المحليّة على nginx.**
   الأعراض: `error: Your local changes ... would be overwritten`.
   العلاج: `cd /opt/hoberadius && git stash && git pull --rebase && git stash pop`
   (ثمّ حُلّ أي تعارض). الجرد يكشف هذه التعديلات مسبقًا (`nginx.local_git_edits`).
2. **طبقة `COPY` مخبّأة في Docker تُبقي الكود قديمًا بعد التحديث.**
   العلاج: `docker compose build --no-cache` (provision يستعمله افتراضيًّا).
3. **مثبّت accel يحتاج python3 على المضيف** (لا Flask/venv) — يولّد
   `/etc/accel-ppp.conf` بمولّد stdlib فقط. تأكّد `python3` موجود، و`/dev/ppp` +
   وحدات PPP محمّلة، و`:443` غير مملوك من nginx/docker (accel يرفض الاصطدام).
4. **إعادة تحميل عملاء FreeRADIUS.** العملاء المُزوَّدون من المعالج في
   `instance/freeradius-clients-wizard/*.conf` (يقرأها `$INCLUDE` بمجلّد — بشرطة
   مائلة نهائيّة، لا wildcards). بعد تعديل يدوي: `touch
   instance/freeradius-clients-wizard/.reload-trigger` (الحاوية تعيد التحميل ~5s).
5. **freeradius على شبكة host** (لا bridge) كي تصل حزم RADIUS بعنوان المصدر
   الحقيقي (10.10.0.x عبر WG) بلا SNAT. لا تُضِف `ports:` له.
6. **`:443` لـ accel لا لـ nginx.** لا تَنشُر 443 في compose — نفق SSTP يملكه.
7. **subnet الـ WG وdeploy/wg-reload.sh مرتبطان** — لو غيّرت subnet غيّر الاثنين.
