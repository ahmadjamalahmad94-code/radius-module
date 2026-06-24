# إعداد دفع الإشعارات (FCM) على الخادم — Firebase Cloud Messaging

هذا الدليل للمالك (لديه SSH على الخادم). يشرح **أين** تضع ملفّ حساب
الخدمة من Firebase و**أيّ** متغيّر بيئة تضبط، كي يبدأ مُرسِل الدفع الخادمي
بإيصال إشعارات الجرس إلى تطبيق الجوّال.

> ⚠️ **الملفّ سرّ.** حساب الخدمة (`firebase-adminsdk-…json`) يحوي **مفتاحًا
> خاصًّا**. لا يُوضَع في git أبدًا، ولا في صورة Docker، ولا يُرسَل في أيّ رسالة.
> المستودع يَرفضه تلقائيًّا عبر `.gitignore` (أنماط `*firebase*adminsdk*.json`،
> `firebase-credentials*.json`، …). إن تسرّب، **دوّره فورًا** من Firebase
> Console ← Project settings ← Service accounts ← Generate new private key.

المشروع: **`hoberadius`** · حساب الخدمة:
`firebase-adminsdk-fbsvc@hoberadius.iam.gserviceaccount.com`

---

## 1) ضع ملفّ الاعتماد على الخادم (خارج المستودع)

انسخ ملفّ JSON إلى مسار آمن **خارج** مجلّد المستودع، يَقرؤه مستخدم الخدمة فقط:

```bash
sudo mkdir -p /etc/hoberadius
sudo cp ~/firebase-adminsdk-hoberadius.json /etc/hoberadius/firebase-adminsdk.json

# صلاحيات ضيّقة: يَقرؤه مستخدم تشغيل التطبيق فقط (مثال: www-data)
sudo chown www-data:www-data /etc/hoberadius/firebase-adminsdk.json
sudo chmod 600 /etc/hoberadius/firebase-adminsdk.json
```

> لا تضعه داخل مجلّد المشروع. `/etc/hoberadius/` مكان جيّد؛ أيّ مسار خارج
> الريبو يَقرؤه مستخدم الخدمة يَعمل.

## 2) اضبط متغيّر البيئة (أيّ واحد من الاثنين)

المُرسِل يَقبل أيًّا من المتغيّرين (يُجرّب الأوّل ثم الثاني):

| المتغيّر | ملاحظة |
|---|---|
| `FIREBASE_CREDENTIALS_PATH` | الخاصّ بهذا التطبيق (مُفضَّل) |
| `GOOGLE_APPLICATION_CREDENTIALS` | متغيّر Google القياسي (يَعمل أيضًا) |

**systemd** (`/etc/systemd/system/hoberadius.service` → قسم `[Service]`):

```ini
Environment=FIREBASE_CREDENTIALS_PATH=/etc/hoberadius/firebase-adminsdk.json
```

ثم:

```bash
sudo systemctl daemon-reload
sudo systemctl restart hoberadius
```

**أو** عبر ملفّ بيئة gunicorn / `.env` يُحمّله مشغّل الخدمة:

```bash
FIREBASE_CREDENTIALS_PATH=/etc/hoberadius/firebase-adminsdk.json
```

## 3) ثبّت الاعتماديّة

```bash
pip install -r requirements.txt   # يَجلب firebase-admin
```

## 4) تحقّق

بعد إعادة التشغيل، سجلّ التطبيق يَطبع سطرًا واحدًا عند الإقلاع/أوّل إرسال:

```
INFO  app.services.fcm_push  FCM push enabled (credential: /etc/hoberadius/firebase-adminsdk.json)
```

إن رأيت بدلًا منه `FCM push disabled: no credential file …` فالمسار/المتغيّر
غير مضبوط — راجع الخطوتين 1 و2.

---

## السلوك عند غياب الاعتماد (مهمّ)

إن لم يُضبَط المتغيّر، أو غاب الملفّ، أو لم تُثبَّت `firebase-admin`:

- المُرسِل **يُعطَّل بهدوء** (no-op) — **لا انهيار، ولا أخطاء متكرّرة**.
- اللوحة + مركز الإشعارات داخل التطبيق + إشعارات ويندوز **تَعمل كالمعتاد**.
- ما إن تضبط الاعتماد وتُعيد التشغيل حتى يبدأ الدفع تلقائيًّا.

إيقاف مؤقّت رغم وجود الاعتماد: اضبط `HOBERADIUS_FCM_DISABLED=1`.

---

## كيف يَعمل (مرجع سريع)

1. تطبيق Flutter يُسجّل رمز الجهاز:
   `POST /api/v1/devices/push-token` بجسم `{ "token": "<fcm>", "platform": "android" }`
   (يُلغيه عند الخروج عبر `DELETE /api/v1/devices/push-token`). الرمز يُحفَظ في
   جدول `device_push_tokens` (migration 138)، tenant-scoped.
2. حين يُكتب إشعار في الجرس (`notify()` → `panel_notifications`)، يُدفَع
   الإشعار نفسه (العنوان/النصّ + `data` يحمل `notification_id`/`link`
   للتنقّل العميق) إلى كل رموز المستأجر عبر FCM — fire-and-forget.
3. الرموز التي يُبلِغ FCM أنها غير مُسجَّلة تُحذَف تلقائيًّا من الجدول.
