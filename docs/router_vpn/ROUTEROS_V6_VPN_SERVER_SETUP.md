# RouterOS v6 VPN — تجهيز الجانب الخادمي (VPS) — Runbook

كيف تُفعّل خدمة أنفاق RouterOS v6 **من الـ VPS**. التطبيق يولّد سكربت
**الراوتر** (العميل)؛ هذه الوثيقة تجهّز **الخادم** المقابل.

> ⚠️ **الحالة:** هذا التجهيز **يدوي حاليًا** — أتمتة الجانب الخادمي (إنشاء
> `/ppp secret` تلقائيًا + عرض القيم في المعالج) مرحلة لاحقة (Phase 2). لا
> يدّعي التطبيق نجاح الجانب الخادمي تلقائيًا.

---

## 1. ما الذي نبنيه؟

سكربتات الراوتر تستخدم `sstp-client` / `l2tp-client` / `pptp-client` تتصل
بخادم VPN على الـ VPS:

| النفق | بروتوكول الخادم | الغرض | المنفذ |
|---|---|---|---|
| الإدارة | **SSTP** | API/Winbox/ping/أوامر HobeRadius — بلا default route | TCP 443 |
| الترافيك | **L2TP/IPsec** (موصى) | تغيير IP / توجيه مشتركين | UDP 500/4500/1701 |
| الترافيك | **PPTP** (Legacy/غير آمن) | بديل عند عدم دعم L2TP | TCP 1723 + GRE(47) |

**الخادم الموصى به:** **MikroTik CHR** (Cloud Hosted Router) على الـ VPS —
لأن كل شيء RouterOS، فالتطابق كامل. (بديل لينكس في القسم 9.)

**فصل الشبكات (مهم):**
- شبكة إدارة: `10.10.0.0/24` (SSTP) — الـ VPS يصل لـ API الراوتر هنا.
- شبكة ترافيك: `10.20.0.0/24` (L2TP/PPTP) — خروج ترافيك المشتركين.

---

## 2. تجهيز CHR على الـ VPS (لمرة واحدة)

### 2.1 شهادة SSTP (إجباري)
```
/certificate add name=hr-sstp common-name=vpn.yourdomain.com key-size=2048 days-valid=3650 key-usage=tls-server
/certificate sign hr-sstp
```
> للإنتاج: استورد شهادة موثوقة (Let's Encrypt) بدل self-signed، وفعّل
> `verify-server-certificate=yes` في المعالج. للمختبر: self-signed + الإبقاء
> على `verify-server-certificate=no` (مع تحذير أمني).

### 2.2 نطاقات IP + ملفات PPP
```
/ip pool add name=hr-mgmt-pool    ranges=10.10.0.10-10.10.0.250
/ip pool add name=hr-traffic-pool ranges=10.20.0.10-10.20.0.250
/ppp profile add name=hr-mgmt    local-address=10.10.0.1 remote-address=hr-mgmt-pool only-one=yes
/ppp profile add name=hr-traffic local-address=10.20.0.1 remote-address=hr-traffic-pool
```
> ملف `hr-mgmt` للإدارة فقط — **لا** تضع فيه ما يدفع default route. سكربت
> الراوتر أصلًا `add-default-route=no`.

### 2.3 خادم SSTP (الإدارة)
```
/interface sstp-server server set enabled=yes certificate=hr-sstp \
  default-profile=hr-mgmt authentication=mschap2 tls-version=only-1.2
```

### 2.4 خادم L2TP/IPsec (الترافيك)
```
/interface l2tp-server server set enabled=yes use-ipsec=required \
  ipsec-secret="REPLACE_WITH_STRONG_SECRET" default-profile=hr-traffic authentication=mschap2
```
> `ipsec-secret` هنا = نفس القيمة التي تُدخلها في المعالج لكل راوتر L2TP.

### 2.5 خادم PPTP (Legacy — فعّله فقط عند الحاجة)
```
/interface pptp-server server set enabled=yes default-profile=hr-traffic authentication=mschap2
```
> PPTP غير مشفّر فعليًا — لا تفعّله إلا للراوترات التي لا تدعم L2TP/IPsec.

---

## 3. حساب لكل راوتر (`/ppp secret`)

لكل راوتر تضيفه من المعالج، أنشئ حسابًا مطابقًا للـ username/password المعروض:
```
/ppp secret add name=<router-user> password=<router-pass> service=any profile=hr-mgmt
```
- `service=any` يسمح بـ SSTP + L2TP + PPTP بنفس الحساب، أو حدّد
  `service=sstp` / `service=l2tp` / `service=pptp` لفصلها.
- لفصل الإدارة عن الترافيك: حساب على `profile=hr-mgmt` وآخر على `hr-traffic`.

> **أمان:** لا تُعِد استخدام كلمة سر واحدة لكل الراوترات. ولّد كلمة لكل راوتر.

---

## 4. تمرير الترافيك + NAT (لخدمة تغيير IP)

على الـ CHR، اجعل ترافيك شبكة الترافيك يخرج عبر WAN الـ VPS:
```
/ip firewall nat add chain=srcnat src-address=10.20.0.0/24 out-interface=<WAN-iface> action=masquerade comment="hoberadius traffic egress"
```
- CHR يوجّه افتراضيًا؛ تأكد أن forward chain يسمح:
```
/ip firewall filter add chain=forward src-address=10.20.0.0/24 action=accept comment="hoberadius traffic forward"
```
- **لا تفعّل** خروجًا واسعًا لشبكة الإدارة `10.10.0.0/24` — الإدارة فقط.

---

## 5. فتح المنافذ (جدار الـ VPS + input chain على CHR)

| الخدمة | البروتوكول/المنفذ |
|---|---|
| SSTP | TCP **443** |
| L2TP/IPsec | UDP **500**, UDP **4500**, UDP **1701** |
| PPTP | TCP **1723** + IP protocol **47 (GRE)** |

مثال input على CHR (قيّدها بمصادر معروفة إن أمكن):
```
/ip firewall filter add chain=input protocol=tcp dst-port=443 action=accept comment="hoberadius sstp"
/ip firewall filter add chain=input protocol=udp dst-port=500,4500,1701 action=accept comment="hoberadius l2tp/ipsec"
# PPTP فقط إن فعّلته:
/ip firewall filter add chain=input protocol=tcp dst-port=1723 action=accept comment="hoberadius pptp"
/ip firewall filter add chain=input protocol=gre action=accept comment="hoberadius pptp gre"
```

---

## 6. ربط القيم بحقول المعالج

| حقل المعالج | القيمة على الـ VPS |
|---|---|
| `server host` (SSTP/L2TP/PPTP) | IP/دومين الـ CHR العام |
| `username` / `password` | الـ `/ppp secret` الذي أنشأته للراوتر |
| `ipsec_secret` (L2TP) | قيمة `ipsec-secret` لخادم L2TP |
| شبكة الإدارة | `10.10.0.0/24` |
| شبكة الترافيك | `10.20.0.0/24` |
| `verify-server-certificate` | `no` للمختبر / `yes` بشهادة موثوقة للإنتاج |

> بعد اتصال SSTP، يأخذ الراوتر عنوانًا من `hr-mgmt-pool` (مثل `10.10.0.x`)؛
> التطبيق يصل لـ API الراوتر على هذا العنوان (وضع الاتصال الإداري).

---

## 7. التحقق

على الـ CHR:
```
/interface sstp-server print
/interface l2tp-server print
/interface pptp-server print
/ppp active print            ← يظهر الراوترات المتصلة + عناوينها
/ip address print            ← عناوين 10.10.0.1 / 10.20.0.1
```
من الـ VPS بعد اتصال الراوتر:
```
ping 10.10.0.x               ← عنوان الراوتر الإداري
```
ثم من HobeRadius: «إدارة الراوترات» → «تحقّق» → API يصل عبر نفق الإدارة.

---

## 8. استكشاف الأعطال (مرجع سريع)

راجع `ROUTEROS_V6_VPN_TROUBLESHOOTING.md` للتفصيل. أكثرها شيوعًا:
- **SSTP لا يتصل:** شهادة/443 محجوب/ساعة الراوتر (NTP).
- **L2TP يتصل ثم ينقطع:** `ipsec-secret` غير مطابق، أو UDP 500/4500 محجوب.
- **PPTP لا يتصل:** GRE(47) أو 1723 محجوب على المسار.
- **تغيير IP لا يعمل:** NAT/forward ناقص على الـ CHR، أو `traffic_mode` خاطئ.

---

## 9. بديل لينكس (بلا CHR)

ممكن لكنه أصعب — تحتاج:
- **SSTP:** `accel-ppp` (يدعم SSTP) + شهادة TLS.
- **L2TP/IPsec:** `strongSwan` (IPsec) + `xl2tpd` (L2TP) + `accel-ppp`/`xl2tpd` للـ PPP.
- **PPTP:** `pptpd` (مهمل، غير آمن).
- إعداد `/etc/sysctl` لتفعيل `net.ipv4.ip_forward=1` + `iptables MASQUERADE`.

التوصية تبقى **CHR** للتطابق والبساطة.

---

## 10. لاحقًا — أتمتة الجانب الخادمي (Phase 2)

الخطوة التالية المنطقية: يجهّز HobeRadius تلقائيًا — عند إضافة راوتر:
1. يولّد + يخزّن `/ppp secret` على الـ CHR عبر API.
2. يعرض القيم الجاهزة (host/user/secret/subnet) في المعالج بلا نسخ يدوي.
3. يتحقق من اتصال الراوتر تلقائيًا ويحدّث حالة النفق.

عند البدء بها: نتبع نفس نمط المشروع (discovery → خطة → تنفيذ مُختبَر).
