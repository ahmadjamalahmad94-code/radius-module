# تحديث الـ VPS — Cheat Sheet

> تشغيل سريع بعد كل push على `origin/main`.
> آخر تحديث: 2026-05-22 — Phase L + M0b (WG bind-mount + reloader).

---

## الوضع المرجعي (لتغييره ضع قيمك)

| البند | القيمة |
|---|---|
| VPS IP | `187.77.70.18` |
| SSH user | `root` |
| مسار المشروع على الـ VPS | `/opt/hoberadius` |
| docker compose file | `/opt/hoberadius/deploy/docker-compose.yml` |
| container الـ app | `hoberadius` |
| container الـ web | `hoberadius-nginx` (نشر 80/443) |
| container الـ RADIUS | `hoberadius-freeradius` (نشر 1812/1813/3799 UDP) |
| رابط الإدارة | `http://187.77.70.18/admin/radius/login` |
| رابط الـ API | `http://187.77.70.18/api/v1` |
| Bearer dev token (dev mode فقط) | `dev-token-please-change` |

---

## 1) من المحلي — ادفع الـ commits

```powershell
cd "C:\Users\Ahmad J Ahmad\Desktop\hub\radius-module"
git status --short       # تأكّد لا يوجد WIP غير مقصود مع الـ push
git push origin main
```

`git push` يرسل الـ commits فقط — أي WIP غير مُلتزم يبقى محلياً.

---

## 2) على الـ VPS — حدّث وأعد بناء

```bash
ssh root@187.77.70.18
sudo bash /opt/hoberadius/deploy/deploy.sh upgrade
```

ما يفعله الأمر:
1. `git pull --rebase` داخل `/opt/hoberadius`
2. `docker compose -f deploy/docker-compose.yml up -d --build`
3. يطبع `docker compose ps` في النهاية

نجاح = container `hoberadius` يطلع `Up X seconds (health: starting)` ثم `(healthy)` خلال 30 ثانية.

---

## 3) على الـ VPS — تحقّق صحة + الـ routes الجديدة

```bash
sleep 30 && \
echo "=== health ===" && \
curl -fsS http://127.0.0.1/admin/radius/_healthz; echo; \
echo "=== MikroTik routes count ===" && \
curl -fsS -H "Authorization: Bearer dev-token-please-change" \
  http://127.0.0.1/api/v1/_routes | \
  python3 -c "import sys,json; r=json.load(sys.stdin)['data']['routes']; mt=[x for x in r if '/mikrotik/' in x['rule'] and '<int:nas_id>' in x['rule']]; print(f'{len(mt)} mikrotik routes')"
```

نجاح:
- `_healthz` يطبع `{"status":"ok","checks":{"db":"ok",...}}`
- `MikroTik routes count` = **25** (بعد K9.3)

---

## 4) أوامر تشخيص لو في مشكلة

| الحالة | الأمر |
|---|---|
| `container Restarting` | `sudo bash /opt/hoberadius/deploy/deploy.sh logs` (Ctrl+C للخروج) |
| `503 Service Unavailable` | راجع health-check: `docker inspect hoberadius --format '{{json .State.Health}}' \| python3 -m json.tool` |
| تعديلات على الـ VPS تمنع الـ pull | `cd /opt/hoberadius && git status --short` — لو في `M` ملفات: `git stash` ثم upgrade ثم `git stash pop` |
| migrations فشلت | `docker exec hoberadius cat /app/logs/app.log \| grep -i migration \| tail -20` |
| توكن dev لا يعمل | تحقّق `docker exec hoberadius env \| grep -i hoberadius_env` — لو `prod` لازم تستعمل توكن DB من `/admin/radius/tokens` |

---

## 5) Rollback لو التحديث كسر شي

```bash
ssh root@187.77.70.18
cd /opt/hoberadius
git log --oneline -5                # شوف الـ commit السابق
git reset --hard <COMMIT_SHA_السابق>
sudo bash deploy/deploy.sh upgrade  # يعيد البناء على الـ commit القديم
```

> ⚠️ `git reset --hard` يفقد أي تعديلات محلية — استعمل `git stash` أولاً لو في تعديلات تريد حفظها.

---

## 6) إعداد WireGuard reloader (مرّة واحدة — Phase M)

بعد أول `upgrade` يجلب M0b، نصّب الـ path-unit:

```bash
sudo bash /opt/hoberadius/deploy/deploy.sh init-wg-reloader
```

ما يفعله:
- ينسخ `wg-reload.service` و `wg-reload.path` إلى `/etc/systemd/system/`
- يفعّل `wg-reload.path` (يبدأ تلقائياً عند إعادة الإقلاع)
- النتيجة: كل ما الـ container يكتب على `wg0.conf` → `wg syncconf wg0` يشتغل فوراً على الـ host

تحقّق:
```bash
systemctl status wg-reload.path     # لازم active (waiting)
journalctl -u wg-reload.service -n 20    # سجل آخر عمليات reload
```

---

## 7) اختبار يدوي بعد كل تحديث

من جهازك (PowerShell على Windows):

```powershell
$base = "http://187.77.70.18/api/v1"
$h    = @{ Authorization = "Bearer dev-token-please-change" }

# سريع — كم mikrotik route مسجّل (لازم 25):
((Invoke-RestMethod "$base/_routes" -Headers $h).data.routes | Where-Object { $_.rule -match "mikrotik" -and $_.rule -match "nas_id" }).Count

# هل الـ dashboard route مسجّل؟
Invoke-WebRequest "http://187.77.70.18/admin/radius/mt/1/dashboard" `
  -MaximumRedirection 0 -ErrorAction SilentlyContinue |
  Select-Object StatusCode, StatusDescription
# المتوقع: 302/303 (redirect لـ login) أو 200 إذا session موجود
```

ثم افتح المتصفّح:
```
http://187.77.70.18/admin/radius/login
```
سجّل دخول → http://187.77.70.18/admin/radius/mt/1/dashboard
