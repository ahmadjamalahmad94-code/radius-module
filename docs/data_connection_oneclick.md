# «اتصال بيانات» — زر اتصال المشترك بضغطة واحدة (feat/data-connection-oneclick)

المشترك في RADIUS يريد ربط مايكروتيك جديد: يختار إصدار المايكروتيك ويضغط
زرًّا واحدًا، فيحصل على سكربت `.rsc` جاهز للّصق. **كل شيء يحدث على RADIUS
VPS الخاص بالعميل** — لا بروكسي، لا CHR، ولا أي نداء للوحة التراخيص في مسار
الاتصال. الوجهة الوحيدة لكل سكربت هي النطاق الفرعي للعميل
`clientN.hoberadius.com`.

## التدفّق
- **v6** → حساب accel-ppp عبر SSTP أو PPTP. يُضبط `transport=vps_accel` على
  المشترك، ويُكتب reply الراديوس = **Filter-Id بسرعة 5 ميجابت فقط** (بلا
  كوتا، بلا قطع، بلا CHR). يُعاد استخدام بيانات دخول المشترك نفسها.
- **v7** → قرين WireGuard: واجهة + قرين يشير لنقطة WG على الـVPS، وعنوان نفق
  من مجمّع WG مستقلّ لكل VPS (افتراضي `10.60.0.0/24`).

السرعة ثابتة 5 ميجابت في كل الحالات (بلا حدّ للبيانات وبلا قطع).

## الملفات
| الملف | الدور |
|------|------|
| `app/radius/services/data_connection.py` | المولّد الخالص + حارس التسرّب + قرّاء التهيئة |
| `app/radius/services/data_connection_wg.py` | قرين WG: المجمّع/المنفذ/المفتاح + تخصيص + **stubs الـVPS** |
| `app/radius/services/data_connection_provision.py` | المنسّق: إنشاء الحساب + بناء السكربت |
| `app/radius/db/repos/data_connection_wg_peers_repo.py` | repo جدول الأقران |
| `app/radius/db/migrations/123_data_connection.sql` | `subscribers.transport` + جدول الأقران |
| `app/radius/routes/customer_portals.py` | مساري POST (توليد) + GET (تنزيل) |
| `app/templates/radius/portal_subscriber.html` | تبويب «اتصال بيانات» |
| `tests/test_data_connection.py` | 25 اختبار وقت-توليد |

الإعدادات (من صفحة إعدادات النظام، مجموعة الشبكة):
`HOBERADIUS_CLIENT_SUBDOMAIN` · `HOBERADIUS_DATA_WG_POOL` ·
`HOBERADIUS_DATA_WG_PORT` · `HOBERADIUS_DATA_WG_PUBKEY`.

## عيّنات السكربت (مُنقّحة)

### v6 — SSTP (`.rsc`)
```
/interface sstp-client add name="hobe-data-sstp" connect-to=client7.hoberadius.com port=443 user="subscriber01" password="<REDACTED>" profile=default-encryption verify-server-certificate=yes add-default-route=no disabled=no comment="HobeRadius DATA subscriber01"
```
> v7 SSTP يضيف `tls-version=only-1.2` (المولّد يدعمه؛ تدفّق الواجهة يوجّه v7 إلى WireGuard).

### v6 — PPTP (`.rsc`)
```
/interface pptp-client add name="hobe-data-pptp" connect-to=client7.hoberadius.com user="subscriber01" password="<REDACTED>" profile=default-encryption add-default-route=no disabled=no comment="HobeRadius DATA subscriber01"
```

### v7 — WireGuard (`.rsc`)
```
/interface wireguard add name="hobe-data-wg" private-key="<CLIENT_PRIVATE_KEY_REDACTED>" comment="HobeRadius DATA subscriber01"
/interface wireguard peers add interface="hobe-data-wg" public-key="<SERVER_PUBLIC_KEY>" endpoint-address=client7.hoberadius.com endpoint-port=51821 allowed-address=0.0.0.0/0 persistent-keepalive=25s comment="HobeRadius DATA subscriber01"
/ip address add address=10.60.0.5/32 interface="hobe-data-wg"
```

## بنود LAB-PENDING (متابعة مخبرية)
1. **شكل `Filter-Id` الدقيق** — `accel_attributes.ACCEL_FILTER_ID_FORM`
   (`kbit_symmetric` افتراضًا → `"5120"`). تحقّق مقابل بناء accel-ppp المثبّت.
2. **دفع قرين WG إلى واجهة الـVPS الحيّة** — `data_connection_wg.push_peer_to_vps`
   حاليًا لا يفعل شيئًا (`applied_to_vps=0`). القرين يُسجّل في DB فقط.
3. **سقف 5 ميجابت لكل قرين WG** — `data_connection_wg.apply_peer_queue`
   حاليًا لا يفعل شيئًا (`queue_applied=0`). المطلوب: queue/tc مربوط بعنوان
   القرين على واجهة WG للبيانات.
4. **منفذ WG للبيانات** — `HOBERADIUS_DATA_WG_PORT` (افتراضي 51821) يجب أن
   يطابق مستمع WG الفعلي للبيانات على الـVPS.
5. **المفتاح العام لخادم WG (بيانات)** — `HOBERADIUS_DATA_WG_PUBKEY` (يدخل في
   كل سكربت WG؛ v7 يفشل برسالة واضحة إن لم يُضبط).
6. **النطاق الفرعي** — `HOBERADIUS_CLIENT_SUBDOMAIN`. تُنشئه لوحة التراخيص
   لاحقًا عبر Cloudflare (دور منفصل خارج مسار الاتصال)؛ يُضبط يدويًا حتى ذلك.

## ضمانات الاختبار
- العيّنات الثلاث تُطابَق حرفيًّا؛ نظافة ASCII؛ الوجهة = النطاق الفرعي فقط.
- حارس `assert_no_leakage` يرفض أي `10.99./10.98./10.51./10.10./chr/proxy`.
- `vps_accel` يكتب reply = `Filter-Id 5120` فقط (لا `Mikrotik-Rate-Limit`).
- أمان الحقن: اقتباس/سطر جديد/محارف غير ASCII في الحقول تُرفَض.
- المفتاح الخاص للعميل لا يُخزَّن (يُعرض مرّة واحدة في السكربت).
