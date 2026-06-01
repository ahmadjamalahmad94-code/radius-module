# RouterOS v6 VPN — استكشاف الأخطاء + متطلبات الخادم

وثيقة تشغيلية مرافقة لـ `ROUTEROS_V6_VPN_STRATEGY.md`. تشرح **لماذا** اخترنا
هذه البنية، **متطلبات الجانب الخادمي (VPS/CHR)**، و**كيفية تشخيص** أعطال
نفقي SSTP (الإدارة) و L2TP/IPsec (الترافيك).

---

## 1. لماذا هذه البنية؟

### لماذا لا WireGuard على RouterOS v6؟
WireGuard أُضيف في **RouterOS 7 فقط**. إصدار 6.x لا يحتوي حزمة `wireguard`،
فأي محاولة لإنشاء `/interface wireguard` تفشل. لذلك على v6 نستخدم بروتوكولات
متوفّرة أصلًا في النواة: **SSTP** و **L2TP/IPsec**.

### لماذا SSTP للإدارة فقط؟
- SSTP فوق TLS/443 — يخترق معظم جدران NAT/CGNAT ويبدو كـ HTTPS عادي.
- نستخدمه حصرًا لـ: API، Winbox، Ping، Monitoring، وأوامر HobeRadius.
- **قاعدة صارمة:** `add-default-route=no`. نفق الإدارة **لا** يمرّر إنترنت
  المشتركين أبدًا، و**لا** يملك Default Route.

### لماذا L2TP/IPsec للترافيك فقط؟
- نفق منفصل تمامًا، اختياري ومتقدّم، لخدمة **تغيير IP** أو توجيه ترافيك
  مشتركين محدّدين عبر الـ VPS.
- يملك Default Route **فقط** في وضع `full_tunnel` (وبتأكيد صريح من المشغّل).

### لماذا ليس PPTP؟
PPTP غير آمن (MS-CHAPv2 مكسور) ومُهمَل. **لا يُوصى به ولا يكون الخيار
الافتراضي إطلاقًا.**

---

## 2. نموذج فصل التوجيه (Routing Separation)

| النفق | الغرض | Default Route | Subscriber traffic |
|---|---|---|---|
| SSTP `sstp-hoberadius-mgmt` | الإدارة | **لا أبدًا** | **لا** |
| L2TP/IPsec `l2tp-hoberadius-traffic` | الترافيك | فقط في full_tunnel | حسب الوضع |

**القاعدة الذهبية:** نفق واحد فقط يملك Default Route في أي لحظة. يفرضها
`routeros_caps.validate_connection_plan` و `v6_tunnels.analyze_tunnel_conflicts`
قبل توليد/تطبيق أي سكربت.

### أوضاع الترافيك
- `disabled` — لا نفق ترافيك (الافتراضي).
- `selected_pool` / `selected_subscribers` / `policy_routing` — توجيه مُحدَّد
  النطاق عبر `address-list` + `routing-mark`، **بلا** Default Route عام وبـ
  NAT مُحدّد النطاق فقط.
- `full_tunnel` — Default Route عبر L2TP + NAT شامل. **يتطلب تأكيدًا صريحًا.**

---

## 3. أمان NAT والـ Default Route

- في الأوضاع المحدودة: NAT `srcnat` بـ `src-address-list=hoberadius-vpn-traffic-clients`
  و`out-interface=l2tp-hoberadius-traffic` فقط — **لا** masquerade شامل لكل LAN.
- في `full_tunnel` فقط: masquerade شامل عبر الـ out-interface (بعد التأكيد).
- كل القواعد التي يولّدها HobeRadius موسومة بتعليقات `HobeRadius managed:` —
  السكربتات **idempotent** ولا تلمس قواعد غير تابعة لـ HobeRadius.

---

## 4. حدود السرعة وتحذير الـ 200Mbps

> ⚠️ **لا ضمان لأي رقم سرعة محدّد (مثل 200Mbps).**

السرعة عبر L2TP/IPsec تعتمد على:
- **موديل الراوتر ومعالجه** — IPsec كثيف الحساب على المعالج.
- **دعم IPsec Hardware Acceleration** — أجهزة مثل hEX/RB4011/CCR تشفّر أسرع
  بكثير من hAP الصغيرة. بدون تسريع عتادي، السرعة تنهار تحت الحمل.
- **جودة الخط** بين الراوتر والـ VPS (RTT، فقد الحزم).
- **قدرة الـ VPS** على التوجيه/التشفير.

> اعرض دائمًا هذا التحذير قبل تفعيل full_tunnel.

---

## 5. متطلبات الجانب الخادمي (VPS/CHR)

> **الحالة الحالية:** توليد سكربت **الراوتر** جاهز ومُختبَر. تجهيز الجانب
> الخادمي خارج نطاق المرحلة الأولى — تظهر الحالة «server-side setup required».
> لا تدّعِ نجاح الجانب الخادمي تلقائيًا.

### خادم SSTP (للإدارة)
- نقطة نهاية: host/domain يصله الراوتر (443).
- شهادة SSL صالحة (أو self-signed مع `verify-server-certificate=no` — إعداد
  المشغّل، الافتراضي no مع تحذير أمني + TODO لـ cert pinning).
- توفير user/secret لكل راوتر.
- شبكة إدارة خاصة (mgmt subnet) للوصول API من الخادم لعنوان الراوتر داخل النفق.
- جدار ناري: السماح بـ API/Winbox من mgmt subnet فقط.

### خادم L2TP/IPsec (للترافيك)
- تفعيل L2TP/IPsec server على الـ VPS/CHR.
- توفير IPsec secret + username/password لكل راوتر.
- traffic VPN subnet منفصل.
- تفعيل IP forwarding على الـ VPS.
- NAT على الـ VPS عند استخدامه لتغيير IP.
- جدار ناري: السماح بـ UDP 500/4500/1701 حسب الحاجة.
- مراقبة.

---

## 6. استكشاف أعطال نفق الإدارة (SSTP)

| العَرَض | السبب المحتمل | الحل |
|---|---|---|
| الواجهة لا تتصل | اسم مستخدم/كلمة سر SSTP خاطئة | راجع بيانات الاعتماد المُولَّدة |
| لا يتصل أبدًا | الخادم غير قابل للوصول | تحقّق من host:443 من الراوتر |
| خطأ TLS/شهادة | مشكلة شهادة | فعّل/عطّل verify-server-certificate حسب جاهزية CA |
| محجوب | جدار ناري/ISP يحجب 443 | جرّب منفذًا/مسارًا آخر |
| توقيت/شهادة | ساعة الراوتر خاطئة | اضبط NTP على الراوتر |

**أوامر فحص على الراوتر:**
```
/interface sstp-client print
/interface sstp-client monitor sstp-hoberadius-mgmt once
/ip address print where interface=sstp-hoberadius-mgmt
```
ومن الـ VPS: `ping <router-mgmt-vpn-ip>` ثم اختبار API.

---

## 7. استكشاف أعطال نفق الترافيك (L2TP/IPsec)

| العَرَض | السبب المحتمل | الحل |
|---|---|---|
| L2TP لا يتصل | بيانات اعتماد L2TP خاطئة | راجع user/password |
| يتصل ثم ينقطع | IPsec secret خاطئ | راجع `ipsec-secret` |
| لا اتصال | منافذ UDP محجوبة | افتح 500/4500/1701 |
| فشل IPsec | عدم تطابق proposal / NAT-T | راجع proposal و NAT traversal |
| تعارض مسار | Default Route مزدوج | نفق واحد فقط يملك Default Route |
| تكرار routing-mark | علامة مستخدَمة مسبقًا | استخدم علامة HobeRadius فقط |
| بطء شديد | CPU مرتفع / لا تسريع عتادي | راجع موديل الراوتر |
| لا خروج للإنترنت | NAT/forwarding ناقص على الـ VPS | فعّل IP forwarding + NAT |

**أوامر فحص:**
```
/interface l2tp-client print
/ip ipsec active-peers print
/ip route print where comment~"HobeRadius"
/ip firewall mangle print where comment~"HobeRadius"
/ip firewall address-list print where list=hoberadius-vpn-traffic-clients
```

---

## 8. التراجع / التعطيل (Rollback / Disable)

كل عناصر HobeRadius موسومة بالاسم أو التعليق، فالتراجع آمن وانتقائي:

**تعطيل نفق الترافيك** (يُبقي الإدارة تعمل):
```
/interface l2tp-client set [find name=l2tp-hoberadius-traffic] disabled=yes
/ip route disable [find comment~"HobeRadius managed: traffic"]
/ip firewall mangle disable [find comment~"HobeRadius managed: traffic policy routing"]
/ip firewall nat disable [find comment~"HobeRadius managed: traffic tunnel NAT"]
```

**إزالة كاملة لعناصر الترافيك:**
```
/ip route remove [find comment~"HobeRadius managed: traffic"]
/ip firewall mangle remove [find comment~"HobeRadius managed: traffic policy routing"]
/ip firewall nat remove [find comment~"HobeRadius managed: traffic tunnel NAT"]
/ip firewall address-list remove [find list=hoberadius-vpn-traffic-clients]
/interface l2tp-client remove [find name=l2tp-hoberadius-traffic]
```

> ⚠️ **لا تُزِل نفق الإدارة (SSTP)** أثناء استكشاف أعطال الترافيك — هو مسار
> وصولك الوحيد للراوتر خلف NAT.

---

## 9. ملاحظة المشغّل (Arabic operator note)

> في RouterOS v6 يتم استخدام SSTP للإدارة فقط، ولا يتم تمرير إنترنت
> المشتركين من خلاله. عند الحاجة لتغيير IP أو تمرير ترافيك محدد، يتم تجهيز
> L2TP/IPsec كنفق منفصل مع Routing مستقل.
