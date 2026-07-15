# GenieACS — تشغيل وحدة «إدارة الراوترات» التجريبيّة على نسخة عميل

وحدة **اختياريّة تجريبيّة**. تُشغَّل فقط على VPS العميل الذي فُعِّلت له الخدمة.
GenieACS خدمة منفصلة؛ HobeRadius يتحكّم بها عبر NBI محليًّا. الراوترات تتّصل
بـ CWMP خلف Nginx/HTTPS. **لا** يُكشف NBI ولا Mongo للإنترنت.

## المتطلّبات
- Node.js غير مطلوب (نشغّل GenieACS في حاوية).
- MongoDB (حاوية). **لا Redis** (غير مطلوب لـ GenieACS 1.2).
- شبكة docker الخارجيّة `radius-module_hrnet` (تنشأ مع hoberadius).

## 1) تفعيل الوحدة في التطبيق
في بيئة نسخة العميل (`deploy/docker-compose.yml` env أو إعدادات اللوحة):
```
HOBERADIUS_TR069_ENABLED=1
HOBERADIUS_GENIEACS_NBI_URL=http://hoberadius-genieacs:7557   # أو 127.0.0.1:7557
HOBERADIUS_GENIEACS_CWMP_URL=https://acs.clientN.hoberadius.com/
```
> بدون `HOBERADIUS_TR069_ENABLED=1` تبقى الوحدة **مخفيّة تمامًا** (لا قسم، والمسارات 404).

## 2) تشغيل GenieACS + Mongo
```bash
cd /opt/hoberadius            # جذر deploy لنسخة العميل
export GENIEACS_UI_JWT_SECRET="$(openssl rand -hex 24)"   # سرّ فريد لكل عميل
docker compose -f deploy/genieacs/docker-compose.genieacs.yml up -d
docker compose -f deploy/genieacs/docker-compose.genieacs.yml ps
```
تحقّق من NBI محليًّا:
```bash
curl -s http://127.0.0.1:7557/devices/?query=%7B%7D | head -c 80   # [] يعني يعمل
```
عندها تُظهر صفحة «إدارة الراوترات» شارة **GenieACS متصل**.

## 3) نقطة CWMP للراوترات (Nginx + HTTPS)
انسخ `nginx-cwmp.conf.example` إلى إعداد Nginx للعميل، عدّل النطاق الفرعيّ،
واحصل على شهادة (Let's Encrypt). الراوترات ستتّصل بـ:
```
https://acs.clientN.hoberadius.com/
```

## 4) تسجيل راوتر تجريبيّ
1. من اللوحة: **المعمل ← إدارة الراوترات ← تسجيل راوتر** → اختر اسم PPPoE.
2. انسخ **ACS URL + CWMP Username/Password** (تُعرَض مرّة واحدة).
3. في الراوتر (مثال MikroTik، يتطلّب حزمة `tr069-client`):
   ```
   /tr069-client set enabled=yes acs-url="https://acs.clientN.hoberadius.com/" \
       username="<CWMP_USERNAME>" password="<CWMP_PASSWORD>" \
       periodic-inform-enabled=yes periodic-inform-interval=300
   ```
4. عند أوّل Inform (خلال دقائق) يتحوّل الراوتر إلى **مُسجَّل** ويظهر بكل بياناته،
   وتعمل أوامر «تحديث البيانات / إعادة تشغيل / طلب اتصال».

## قرار مفتوح للـ PoC — مطابقة التسجيل
الكود يولّد بيانات CWMP لكل جهاز. لمطابقة أوّل Inform بالجهاز المعلّق نستخدم
**tag = رمز التسجيل** على مستوى GenieACS. الطريقة القياسيّة: Provision/Preset في
GenieACS يقرأ اعتماد CWMP ويضع الـ tag. يُحسم شكل هذا الـ extension أثناء الـ PoC
(GenieACS يدعم Extensions). حتى ذلك الحين يمكن **الربط اليدويّ** من صفحة التفاصيل.

## الأمان
- NBI (7557) وMongo (27017): `127.0.0.1` فقط — تأكّد ألّا تنشرهما جدارك الناريّ.
- CWMP: HTTPS إلزاميّ؛ DNS-only للنطاق الفرعيّ (لا Cloudflare Proxy قبل التحقّق).
- بيانات CWMP مشفّرة Fernet في قاعدة HobeRadius ولا تُعاد للواجهة.
- Firmware/Factory-Reset معطّلان في هذه المرحلة التجريبيّة.

## النسخ الاحتياطيّ
أضِف `genieacs-data/mongo` إلى نظام النسخ. فقدان Mongo لا يُفقد ربط الأجهزة
(محفوظ في HobeRadius) لكن يُفقد حالة GenieACS — تُعاد ببناء الأجهزة عند Inform التالي.
