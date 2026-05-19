# FreeRADIUS Container — HobeRadius

## دور هذه الحاوية

تحوّل HobeRadius إلى **RADIUS Authority** كاملة:

- تستقبل **Access-Request** من MikroTik (و أي NAS متوافق) على UDP/1812.
- تستقبل **Accounting** على UDP/1813 و تكتبه في `radacct`.
- **لا تُقرّر** القبول/الرفض بنفسها — تستدعي `hoberadius:/api/v1/internal/auth` (rest module) و الـ Python هو الذي يقرّر.

CoA (Disconnect/CoA) **outbound** لـ MikroTik يتمّ من **HobeRadius مباشرة** (انظر `app/radius/integration/radius_coa.py`)، لا من FreeRADIUS.

## البنية

```
MikroTik ──Access-Request 1812──> [FreeRADIUS] ──HTTP POST──> [HobeRadius /api/v1/internal/auth]
                                       │                              │
                                       │                              └─> policy_engine → AuthDecision
                                       │
                                       └──> SQLite (radacct, radpostauth)
```

## التشغيل

```bash
cd deploy
docker compose up -d --build freeradius
docker logs -f hoberadius-freeradius
```

اختبر:

```bash
docker exec -it hoberadius-freeradius radtest username password localhost 0 testing123
```

## ملفات التكوين

| ملف | الدور |
|---|---|
| `radiusd.conf` | تكوين عام (listen, threads, security) |
| `clients.conf` | NAS clients ثابتة (localhost + docker network) — الباقي يأتي من DB عبر sql module |
| `mods-enabled/rest` | يستدعي `/api/v1/internal/auth` للـ authorize و `/api/v1/internal/postauth` للـ logging |
| `mods-enabled/sql` | SQLite — يقرأ `nas` للـ clients، يكتب `radacct` و `radpostauth` |
| `sites-enabled/default` | virtual server يربط الـ modules بـ authorize/authenticate/accounting/post-auth |

## التشخيص

شغّل في foreground مع debug verbose:

```bash
# في .env: FREERADIUS_DEBUG_LEVEL=-X
docker compose restart freeradius
docker logs -f hoberadius-freeradius
```

## ملاحظة أمنية

ضع `HOBERADIUS_INTERNAL_SECRET` في `.env` و لا تتركه فارغًا في الإنتاج. هذا الـ secret يحرس endpoint `/api/v1/internal/auth` من أي طرف يستطيع الوصول لـ HobeRadius عبر الشبكة الداخلية.
