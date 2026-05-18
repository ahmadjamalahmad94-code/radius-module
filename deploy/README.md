# HobeRadius — VPS Deployment Guide

دليل نشر دقيق على Ubuntu 22.04+ VPS. وقت متوقّع: **15-30 دقيقة**.

> الافتراض: لديك VPS، domain (مثلاً `radius.example.com`) يشير لـ IP الـ VPS،
> و MikroTik يمكنه الوصول للـ VPS عبر الشبكة (أو العكس).

---

## الخيار A — Docker (الموصى به)

### 1. تجهيز الخادم

```bash
ssh root@YOUR_VPS

# تثبيت Docker
apt update && apt install -y docker.io docker-compose-plugin
systemctl enable --now docker

# Let's Encrypt (لـ HTTPS)
apt install -y certbot
```

### 2. الكود

```bash
mkdir -p /opt/hoberadius && cd /opt/hoberadius
git clone <your-repo-or-zip> .
# أو scp -r local:./radius-module/ root@vps:/opt/hoberadius
```

### 3. الإعدادات

```bash
cp deploy/.env.example .env
# عدّل .env: FLASK_SECRET (32 بايت عشوائية)، أي إعدادات أخرى
openssl rand -hex 32   # ضع الناتج في FLASK_SECRET
```

### 4. HTTPS — أصدر شهادة قبل أوّل تشغيل

```bash
# توقّف لو nginx شغّال على :80
systemctl stop nginx 2>/dev/null || true

certbot certonly --standalone -d radius.example.com \
    --non-interactive --agree-tos -m you@example.com

# تجديد تلقائي
echo "0 3 * * * certbot renew --quiet && docker compose -f /opt/hoberadius/deploy/docker-compose.yml exec nginx nginx -s reload" \
    | crontab -
```

### 5. تعديل nginx.conf

```bash
sed -i "s/YOUR_DOMAIN/radius.example.com/g" deploy/nginx.conf
```

### 6. التشغيل

```bash
cd /opt/hoberadius
docker compose -f deploy/docker-compose.yml up -d --build

# تابع
docker compose -f deploy/docker-compose.yml logs -f app
```

### 7. تحقّق

```bash
curl -fsS https://radius.example.com/admin/radius/_health
# {"module":"radius","status":"ok","stage":"P2"}
```

افتح المتصفّح: `https://radius.example.com/` → سيُحوَّل لـ login.

---

## الخيار B — نشر مباشر (بدون Docker)

```bash
# تثبيت python
apt install -y python3.12 python3.12-venv nginx certbot

# المستخدم
useradd -r -s /usr/sbin/nologin -d /opt/hoberadius hr

# الكود
mkdir -p /opt/hoberadius && cd /opt/hoberadius
git clone <repo> .
chown -R hr:hr .

# venv
sudo -u hr python3.12 -m venv .venv
sudo -u hr .venv/bin/pip install -r requirements.txt

# env
cp deploy/.env.example .env
openssl rand -hex 32 > /tmp/secret.txt
sed -i "s|change-me-to-32-random-bytes-please|$(cat /tmp/secret.txt)|" .env

# systemd
cp deploy/hoberadius.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now hoberadius
systemctl status hoberadius

# nginx (يعمل خارج docker)
cp deploy/nginx.conf /etc/nginx/sites-available/hoberadius
sed -i "s/YOUR_DOMAIN/radius.example.com/g" /etc/nginx/sites-available/hoberadius
sed -i "s|server app:8000;|server 127.0.0.1:8000;|" /etc/nginx/sites-available/hoberadius
ln -s /etc/nginx/sites-available/hoberadius /etc/nginx/sites-enabled/
nginx -t && systemctl reload nginx
```

---

## أوّل تشغيل — خطوات إلزامية

1. افتح الموقع، login بـ `admin / admin`.
2. **فورًا**: غيّر كلمة المرور (`/admin/radius/admins`).
3. أضف **MikroTik connection**: `/admin/radius/mt/new` → host/user/pass.
4. اختبر: زر "اختبار الاتصال" → يجب أن يقرأ identity من الـ router.
5. أنشئ Plan + Subscriber → خلال 3 ثوانٍ تظهر في `/ip hotspot user print`.
6. Webhook: `/admin/radius/webhooks` → ضع target_url وsecret.

---

## Backup

- يومي تلقائي داخل container الـ backup (راجع docker-compose.yml).
- يدوي:
  ```bash
  docker compose exec backup /usr/local/bin/backup.sh
  ls -lh ../backups/
  ```
- استعادة:
  ```bash
  ./deploy/restore.sh /opt/hoberadius/backups/hoberadius-YYYYMMDD-HHMMSS.db.gz
  ```

---

## Monitoring

- Health: `curl https://radius.example.com/admin/radius/_health`
- Logs:
  - Docker: `docker compose logs -f app`
  - systemd: `journalctl -u hoberadius -f`
- DB size: `ls -lh instance/hoberadius.db`
- Sync queue: ادخل `/admin/radius/audit` لاحقًا (يأتي قريبًا) أو SQL مباشر.

---

## Upgrade

```bash
cd /opt/hoberadius
git pull
docker compose -f deploy/docker-compose.yml up -d --build
# migrations تُطبّق تلقائيًا عند الإقلاع
```

---

## استكشاف الأخطاء

| المشكلة | الحل |
|---------|-------|
| `connection refused` على 8000 | تحقّق `docker compose ps app` — لربما crashed |
| TLS errors | `certbot certificates` للتأكد من الشهادة + nginx error log |
| sync_queue يفشل | افتح `/admin/radius/audit` (قريبًا) أو SQL: `SELECT * FROM sync_queue WHERE status='failed'` |
| MT timeout | تحقّق `/admin/radius/mt` + اضغط "اختبار". افحص firewall على port الـ API (8728/8729). |
| DB locked | عادةً WAL يحلها. لو استمرت: `docker compose restart app` |

---

## ما ينقص للإنتاج طويل المدى (لاحقًا)

- PostgreSQL (لما يتجاوز 5000 subscriber).
- Redis + Celery (لو deliveries كثيرة جدًا).
- Multi-region failover.
- SAML/OIDC SSO للأدمن.

كل ذلك Phase-2+. الـ Phase-1 الحالي **يكفي تمامًا** لـ ISP صغير-متوسط.
