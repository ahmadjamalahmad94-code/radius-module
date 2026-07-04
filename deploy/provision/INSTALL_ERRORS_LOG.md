# HobeRadius — سجلّ أخطاء التثبيت + حلولها (Living log)

> الغرض: كل عطل نمرّ به أثناء تثبيت/ترقية/تزويد الـ VPS نسجّله هنا **بعرَضه
> وسببه وحلّه الدقيق**، فيصير التثبيت في النهاية سهلًا ومتكرّرًا. هذا الملف
> يُدمَج على `main` فيصل لكل VPS عبر `git pull`. **أضِف أيّ خطأ جديد أسفل القسم
> المناسب فور مواجهته.**

آخر تحديث: 2026-07-05.

---

## ✅ الوصفة السريعة — نسخة فارغة نظيفة لعميل جديد

بعد إصلاحات هذه الجلسة، أيّ VPS جديد من آخر `main` = **فارغ + مدير
`admin`/`123456789`** تلقائيًّا. للتثبيت النظيف:

```bash
# على Ubuntu 22.04+ نظيف (منافذ 80/8443/443 + UDP 1812/1813 + WG UDP مفتوحة)
git clone https://github.com/ahmadjamalahmad94-code/radius-module.git /tmp/rm
sudo bash /tmp/rm/deploy/provision/provision-fresh-vps.sh --sha origin/main --role app
# .env.example يضبط HOBERADIUS_NO_SEED=1 → لا بيانات تجريبيّة، ومدير افتراضيّ
# يُنشأ تلقائيًّا. لاسم/كلمة مرور مالك مخصّصة قبل أوّل إقلاع:
#   HOBERADIUS_BOOTSTRAP_ADMIN_USER=owner  HOBERADIUS_BOOTSTRAP_ADMIN_PASS=Strong123
```

تصفير VPS ثبّت سابقًا ببيانات تجريبيّة → نسخة فارغة:
```bash
cd /opt/hoberadius
git checkout -f -B main origin/main                 # (يصلح detached HEAD أيضًا)
sed -i '/^HOBERADIUS_NO_SEED=/d' .env; echo 'HOBERADIUS_NO_SEED=1' >> .env
docker compose -f deploy/docker-compose.yml stop hoberadius
mv instance/hoberadius.db instance/hoberadius.db.bak 2>/dev/null || true
rm -f instance/hoberadius.db-wal instance/hoberadius.db-shm
docker compose -f deploy/docker-compose.yml up -d --build
```
بعدها: افتح `http://<VPS>/admin/radius/login` (admin/123456789 — غيّرها فورًا)،
ثمّ **فعّل الترخيص** `/admin/radius/_license/activate`.

---

## 🧾 الأخطاء وحلولها

### تثبيت / ترقية / git

**E1 — `deploy.sh upgrade` يقف: «cannot pull with rebase: You have unstaged changes».**
- السبب: أمر `deploy.sh tls <domain>` يعدّل `deploy/nginx.conf` (وملف TLS) بـ`sed`
  على السيرفر → تعديلات محليّة دائمة تمنع `git pull --rebase`، فتموت الترقية قبل
  البناء **والتنظيف**.
- الحلّ: `deploy.sh upgrade` صار يستعمل `git pull --rebase --autostash` (دُمج).
  لأوّل ترقية على نسخة قديمة: `cd /opt/hoberadius && git stash && git pull --rebase && git stash pop`.

**E2 — `git pull` يقف: «You are not currently on a branch … specify which branch».**
- السبب: الـ repo على السيرفر في **detached HEAD** (غالبًا بسبب `checkout` على SHA
  أثناء التزويد) — فلا فرع يُرَبيس عليه، والترقية تموت.
- الحلّ: أعِد الربط بفرع `main`:
  `git fetch origin && git checkout -f -B main origin/main`
  (‏`instance/` متجاهَل بـgit فلا تُمسّ قاعدة البيانات). بعدها الترقيات تعمل عادي.

**E3 — صورة `hoberadius:latest` تتضخّم إلى ~10GB وتنمو مع كل deploy.**
- السبب: سياق البناء = جذر الريبو + `COPY . .` + `.dockerignore` denylist، فتُخبز
  كل نفايات مجلد الريبو (قاعدة instance الحيّة، دمبات، أرشيفات)، والصورة القديمة
  تبقى dangling.
- الحلّ (دُمج): `.dockerignore` صار **allowlist** (يمنع كل شيء إلا مسارات التشغيل)،
  وDockerfile ينسخ مسارات صريحة، و`deploy.sh upgrade` يُنظّف بعد البناء. تنظيف يدويّ
  لمرّة: `docker image prune -f && docker builder prune -f --keep-storage 2GB`.

**E4 — البناء لا يأخذ الكود الجديد بعد الترقية.**
- السبب: طبقة `COPY` مخبّأة في Docker.
- الحلّ: `docker compose -f deploy/docker-compose.yml build --no-cache` (التزويد
  يستعمله افتراضيًّا).

### قاعدة البيانات / الإقلاع

**E5 — «unable to open database file» عند فحص sqlite.**
- السبب: أُزيح/حُذف `instance/hoberadius.db` واللوحة موقوفة، فلا قاعدة نشطة (حدث
  حين أُزيحت القاعدة ثمّ فشلت الترقية عند E2 فلم تُعِد الإقلاع).
- الحلّ: أصلح السبب (E2)، ثمّ `docker compose … up -d --build` — الإقلاع يُنشئ قاعدة
  فارغة + migrations. (تحقّق: `docker exec hoberadius-backup sqlite3 -readonly /data/hoberadius.db "SELECT COUNT(*) FROM admins;"`)

**E6 — نسخة جديدة تُقلع ببيانات تجريبيّة (28 مشترك، مدير admin+operator) بدل الفراغ.**
- السبب: بذر البيانات التجريبيّة يعمل حين `HOBERADIUS_NO_SEED` غير مضبوط.
- الحلّ: اضبط `HOBERADIUS_NO_SEED=1` في `.env` (صار الافتراضيّ في `.env.example`)،
  واحذف/أزِح القاعدة التجريبيّة ثمّ أعد الإقلاع → قاعدة فارغة + مدير bootstrap.

**E7 — لا يوجد مدير للدخول على نسخة إنتاجيّة نظيفة (NO_SEED مضبوط، لا بذر).**
- السبب: قديمًا لم يكن هناك مدير مضمون إلا عبر البذر التجريبيّ.
- الحلّ (دُمج): `ensure_bootstrap_admin()` يُنشئ `admin`/`123456789` (سوبر) حين لا
  مدير — يعمل بعد البذر فلا يُجهضه. قابل للتجاوز
  `HOBERADIUS_BOOTSTRAP_ADMIN_USER/PASS`.

**E8 — دخول المدير بكلمة مرور خاطئة يُعطي 500 (Internal Server Error).**
- السبب: `auth.py` يمرّر `attempted_password=` بينما فُقد الوسيط من
  `record_login_event` (انحدار) → `TypeError` عند ربط الوسائط قبل الحارس → 500.
- الحلّ (دُمج): استُعيدت الميزة؛ الآن كلمة خاطئة = 401 + «بيانات الدخول غير صحيحة».

### التحقّق / verify-parity

**E9 — `verify-parity.sh` يطبع «N فرق» كلها «مصدر=''».**
- السبب: ملف المانيفست (`vps-manifest.json`) فارغ/غير موجود — لم يُشغَّل
  `inventory-current-vps.sh` على الـVPS **القديم** أولًا، فلا مرجع للمقارنة.
- الحلّ: شغّل الجرد على القديم أوّلًا وانقل المانيفست، **أو** تجاهل فروق المانيفست
  واعتمد قسم «الفحوصات الحيّة» (حاويات/منافذ/wg0/اللوحة/السكيمة).

**E10 — مثبّت accel يخرج صامتًا بعد «منفذ SSTP=443» فلا يُكتب `/etc/accel-ppp.conf`
(الخدمة `activating`/تفشل: `conf_file:open: No such file or directory`).**
- **السبب الجذريّ (دجاجة وبيضة):** `install-accel-selfsigned.sh` فيه
  `set -euo pipefail`، وفحص تعارض المنفذ يُسند
  `HOLDERS="$(ss -ltnpH sport=:443 | grep -oE 'users:...' | …)"`. على صندوق نظيف
  **لا أحد يستمع على :443** (accel لم يبدأ، nginx على :8443) → `grep` لا يجد
  تطابقًا → يُرجع 1 → `pipefail` يُسقط الأنبوب → `set -e` **يقتل المثبّت** قبل
  توليد الشهادة/الـconf/التشغيل. أي أنّ الفحص كان يقتل **كلّ تثبيت أوّل** (كلّما
  كان :443 حرًّا).
- **الإصلاح:** أضِف `|| true` داخل بدل الأمر — عدم تطابق grep نتيجة متوقَّعة
  (المنفذ حرّ)، لا خطأ. أُصلح أيضًا نفس الصنف في سطر cipher بمسار فحص TLS
  (السطر ~469) الذي كان سيقتل المثبّت بعد **نجاح** المصافحة.
- **الدرس العامّ:** أيّ `VAR="$(… | grep … )"` تحت `set -euo pipefail` قنبلة —
  grep بلا تطابق = خروج 1 = موت صامت بلا رسالة `die`. احْمِ كلّ بدل أمر ينتهي
  بـgrep/awk اختياريّ بـ`|| true`.
- **الأعراض المميِّزة:** مخرجات التثبيت تنتهي عند `منفذ SSTP=…`، ومجلّد
  `/etc/accel-ppp/` **غير موجود أصلًا** (لم تُنشأ الشهادة)، و`journalctl -u
  accel-ppp` = `conf_file:open: No such file or directory`.

### مكوّنات ناقصة بعد التثبيت (ليست أخطاء — خطوات)

**S1 — اللوحة تحوّل إلى `/admin/radius/_license/activate`.**
- المعنى: جسر الترخيص غير مفعَّل. فعّله من تلك الصفحة بمفتاح العميل.

**S2 — نفق الإدارة (accel-ppp / SSTP / PPTP) غير نشط: `accel inactive`، `:443`/`:1723` لا أحد يستمع.**
- **أساسيّ ودائم:** accel-ppp (SSTP :443 **و** PPTP :1723) جزء أساسيّ من التشغيل.
  `provision-fresh-vps.sh` (STEP 8) يثبّته ويُفعّله ويشغّله تلقائيًّا مع الإقلاع،
  ويولّد `/etc/accel-ppp.conf` بوحدتَي `sstp` + `pptp` (عبر `accel_conf_gen.py`).
  إن ظهر غير نشط فالتزويد لم يُكمل STEP 8 أو فشل بيئيًّا.
- التفعيل اليدويّ على VPS قائم:
  ```
  sudo bash deploy/accel-ppp/install-accel-selfsigned.sh
  sudo systemctl enable --now accel-ppp
  sudo bash deploy/mgmt-confinement/install-mgmt-confinement.sh   # اختياري: تقييد الوصول
  ```
- التحقّق (كلاهما لازم يستمع لـ accel-pppd):
  ```
  sudo ss -lntp | grep -E ':443|:1723'
  sudo bash deploy/provision/verify-parity.sh    # يفحص accel active/enabled + :443 + :1723
  ```
- **متطلّبات:** `python3` + `/dev/ppp` + وحدات PPP، و`:443`/`:1723` غير مملوكة من
  nginx/docker. **PPTP** يحتاج فتح **TCP 1723 + بروتوكول GRE (47)** في جدار المزوّد
  السحابيّ (بعض المزوّدين يحجب GRE — لو عُطِّل GRE يبقى SSTP بديلًا يعمل عبر :443).

**S3 — بناء accel-ppp من المصدر يطبع «جدار» تحذيرات `MD5_Init/SHA1/DES … is
deprecated: Since OpenSSL 3.0».**
- **ليست أخطاء — طبيعيّة تمامًا.** accel-ppp يستعمل تشفير PPP القديم
  (MD4/MD5/SHA1/DES) الذي أهملته OpenSSL 3.0؛ البناء ينجح رغمها. الدليل على
  النجاح سطر `بُني accel-ppp <ver> من المصدر وثُبِّت` + `كُتبت الوحدة
  accel-ppp.service وفُعِّلت`. لا تتوقّف عند التحذيرات.
- (سبب البناء من المصدر: accel-ppp غير مُحزَّم في apt على Ubuntu noble.)
- تحذير `تعذّر تصدير إعدادات اللوحة من الحاوية — أعتمد env/الافتراضات` طبيعيّ على
  نسخة فارغة؛ يعتمد الافتراضات (SSTP 443، gateway 10.50.0.1) وهي صحيحة.

---

## قالب إضافة خطأ جديد

```
**E# — <العرَض الحرفيّ كما ظهر>.**
- السبب: <الجذر>.
- الحلّ: <الأمر/التغيير الدقيق>. (دُمج <SHA> إن كان إصلاح كود.)
```
