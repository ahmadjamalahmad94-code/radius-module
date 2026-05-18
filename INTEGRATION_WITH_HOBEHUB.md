# HobeRadius ⇄ HobeHub — وثيقة التكامل

> **القاعدة الذهبية:** التكامل يحدث عبر **REST + Webhooks** فقط.
> HobeHub لا يستورد شيئًا من HobeRadius والعكس صحيح.

---

## 1. اتجاهات التدفق

```
┌──────────────┐  REST (sync)    ┌──────────────┐
│   HobeHub    │ ───────────────▶│  HobeRadius  │
│ (الإدارة)    │◀─── Webhooks ── │  (الخدمة)    │
└──────────────┘   (async events)└──────────────┘
```

- **HobeHub → HobeRadius**: استدعاءات API متزامنة (إنشاء حساب، توليد بطاقات، قطع جلسة...).
- **HobeRadius → HobeHub**: webhooks لإبلاغ HobeHub بأحداث وقعت (جلسة بدأت، انتهت صلاحية، تجاوز quota...).

## 2. المصادقة

- **REST inbound**: `Authorization: Bearer <token>` — `HOBERADIUS_API_TOKENS` env (CSV).
- **Webhooks outbound**: `X-HobeRadius-Signature: sha256=<hex>` — HMAC على body بـ `HOBERADIUS_WEBHOOK_SECRET`.
- **Token rotation**: قائمة tokens مدعومة لتدوير بلا انقطاع.

## 3. شكل الردود الموحَّد

كل استجابة JSON بهذا الإطار:

```json
{
  "ok": true,
  "data": { ... },
  "meta": { "request_id": "uuid", "version": "v1" }
}
```

خطأ:
```json
{
  "ok": false,
  "error": {
    "code": "account_not_found",
    "message": "...",
    "details": { "username": "u1" }
  },
  "meta": { "request_id": "uuid", "version": "v1" }
}
```

أكواد الأخطاء المعتمدة: `unauthorized`, `forbidden`, `not_found`, `validation_error`,
`conflict`, `rate_limited`, `internal_error`, `not_implemented`.

## 4. نقاط التكامل الكاملة (Endpoints المخطَّطة)

| الفئة | METHOD | المسار | الغرض من جهة HobeHub |
|------|--------|--------|----------------------|
| Health | GET    | `/api/v1/health`                              | فحص الحياة قبل أي عملية |
| Version | GET   | `/api/v1/version`                             | معرفة الـ schema المتاح |
| Accounts | GET  | `/api/v1/accounts`                            | استعراض/مزامنة |
| Accounts | POST | `/api/v1/accounts`                            | إنشاء حساب من beneficiary |
| Accounts | GET  | `/api/v1/accounts/{username}`                 | قراءة فردية |
| Accounts | PATCH| `/api/v1/accounts/{username}`                 | تعديل (status, expire, profile...) |
| Accounts | DELETE| `/api/v1/accounts/{username}`                | إلغاء (soft) |
| Accounts | POST | `/api/v1/accounts/{username}/reset_password`  | تغيير كلمة سر |
| Accounts | POST | `/api/v1/accounts/{username}/extend_time`     | إضافة وقت |
| Accounts | POST | `/api/v1/accounts/{username}/disable`         | تعطيل فوري |
| Accounts | POST | `/api/v1/accounts/{username}/enable`          | إعادة تفعيل |
| Accounts | GET  | `/api/v1/accounts/{username}/usage`           | snapshot استخدام |
| Cards | POST    | `/api/v1/cards/generate`                      | توليد دفعة بطاقات |
| Cards | GET     | `/api/v1/cards/{external_id}`                 | قراءة بطاقة |
| Cards | POST    | `/api/v1/cards/{external_id}/revoke`          | إلغاء بطاقة |
| Profiles | GET  | `/api/v1/profiles`                            | قائمة الباقات |
| Profiles | GET  | `/api/v1/profiles/{id}`                       | تفصيل باقة |
| NAS | GET       | `/api/v1/nas`                                  | قائمة الـ NAS |
| Sessions | GET  | `/api/v1/sessions/online`                     | المتصلون الآن |
| Sessions | POST | `/api/v1/sessions/disconnect`                 | قطع جلسة |
| Accounting | GET| `/api/v1/accounting`                          | سجل جلسات منتهية |
| Webhooks | GET  | `/api/v1/webhooks/config`                     | معاينة إعداد HobeHub URL |
| Webhooks | PUT  | `/api/v1/webhooks/config`                     | ضبط HobeHub URL/secret |
| Webhooks | POST | `/api/v1/webhooks/test`                       | إرسال حدث وهمي للتأكد |

كل هذه الـ endpoints **مُسجَّلة من الآن**، تعيد `not_implemented` حيث المنطق غير
جاهز، لكن الـ contract (المسار + شكل JSON + auth) مستقر ولن يتغير عند الدمج.

## 5. أحداث الـ Webhooks (HobeRadius → HobeHub)

```json
{
  "event": "session.started",
  "event_id": "uuid",
  "occurred_at": "2026-05-18T13:21:00Z",
  "data": { ... },
  "version": "v1"
}
```

| Event | متى |
|-------|-----|
| `account.created`      | حساب أُنشئ |
| `account.updated`      | تعديل (profile/status/expire) |
| `account.disabled`     | تعطيل يدوي |
| `account.expired`      | انتهت الصلاحية تلقائيًا |
| `card.generated`       | دفعة بطاقات صدرت |
| `card.consumed`        | بطاقة استُهلكت أول مرة |
| `session.started`      | جلسة بدأت |
| `session.stopped`      | جلسة انتهت + bytes/duration |
| `session.disconnected` | قطع يدوي عبر API |
| `quota.threshold`      | 80% / 95% / 100% من الـ quota |
| `nas.unreachable`      | NAS لا يرد على فحص دوري |

## 6. ضمان توافق طويل المدى

- **Versioned base**: `/api/v1/` — أي breaking change → `/api/v2/`.
- **Additive only**: حقول جديدة مسموح إضافتها في v1، لكن لا حذف ولا تغيير نوع.
- **Idempotency keys**: عمليات POST تقبل header `Idempotency-Key` (مهم لـ retries).
- **Pagination معيارية**: `?limit=&cursor=` + `meta.next_cursor`.
- **Pagination افتراضية**: 50، حد أقصى 500.

## 7. التحويل من المرحلة الحالية للتكامل

| المرحلة الآن | عند الجاهزية |
|--------------|---------------|
| `ManualAdapter` in-memory | يبقى للـ tests |
| لا DB | `SqliteAdapter` للتخزين الدائم |
| `api/v1/*` يعيد 501 stubs | كل endpoint يستدعي service مكتمل |
| Webhooks dispatcher يلوغ فقط | يدفع فعلًا لـ HobeHub URL |
| لا Idempotency store | جدول `idempotency_keys` + TTL |

## 8. أمثلة سريعة (للمراجعة الذهنية)

### HobeHub ينشئ حسابًا من beneficiary
```http
POST /api/v1/accounts HTTP/1.1
Authorization: Bearer <token>
Idempotency-Key: ben-1234-create
Content-Type: application/json

{ "username": "u1234", "password": "x", "profile_id": 2, "beneficiary_ref": "1234" }
```

### HobeRadius يبلّغ HobeHub بانتهاء جلسة
```http
POST https://hobehub.example.com/webhooks/radius
X-HobeRadius-Signature: sha256=...
Content-Type: application/json

{
  "event": "session.stopped",
  "event_id": "ev_5f...",
  "occurred_at": "2026-05-18T13:21:00Z",
  "data": {
    "username": "u1234",
    "session_id": "s9-...",
    "duration_sec": 3600,
    "bytes_in": 102400,
    "bytes_out": 50000
  },
  "version": "v1"
}
```

## 9. مَن يحدّد الـ URL الذي يستقبل الـ webhooks؟

- يُضبط من واجهة HobeRadius (شاشة Settings → Webhooks) أو عبر
  `PUT /api/v1/webhooks/config`.
- يُحفظ في DB، ويُحمَّل في الذاكرة عند الإقلاع.

## 10. ما لن يدخل أبدًا

- مشاركة DB.
- استيراد كود متبادل.
- مصادقة مشتركة (session).
- نسخة من كود HobeHub داخل HobeRadius.

كل تكامل = HTTP. نقطة.
