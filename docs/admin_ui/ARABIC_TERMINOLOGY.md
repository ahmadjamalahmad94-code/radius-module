# Hoberadius — Arabic Terminology & Voice Guide

Single source of truth for every operator-facing word in
the Hoberadius admin UI. Use these terms verbatim — don't
invent synonyms, don't import English jargon into Arabic
body text.

## Tone

- **اللهجة**: فصحى مبسَّطة (modern standard Arabic, plain).
  Never colloquial. Never academic.
- **الجملة قصيرة**: max 12 كلمة في الجملة الواحدة.
  Long sentences are a sign of a missing component (move
  detail to the side drawer).
- **نخاطب المشغّل، لا نخاطب الجهاز**: "احذف" لا "يُحذَف";
  "اضغط" لا "يُضغَط".
- **بدون كلمات مخيفة**: لا "خطر"، "تحذير شديد"، "كارثة"
  دون سبب فعلي. الـ pill اللوني يكفي.

## Core vocabulary

| English | Arabic | Notes |
|---|---|---|
| Apply | تطبيق آمن | The button label is always «تطبيق آمن» — the «آمن» makes it explicit this is the gated path. |
| Rollback | تراجع | Used as both verb and noun. |
| Preview | معاينة | |
| Policy | سياسة | |
| Router | راوتر | Not «جهاز» — operators say "راوتر" for MikroTik. |
| Device | جهاز | Reserved for end-user devices (phones / laptops). |
| Snapshot | لقطة | The state-capture artifact. |
| Deploy | نشر | |
| Blocker | مانع | A hard gate that stops apply. |
| Warning | تنبيه | A soft signal that doesn't block. |
| Confirmation | تأكيد | Operator must tick. |
| Ready | جاهز | Use sparingly — overused word. Prefer specific. |
| Not ready | غير جاهز | Always paired with the reason. |
| Running | قيد التنفيذ | Never «جارٍ» (passive voice). |
| Succeeded | ناجح | |
| Failed | فشل | |
| Partial | جزئي | |
| Rolled back | تمّ التراجع | |
| Expired | منتهي | |
| Online | متّصل | Network reachable. |
| Offline | غير متّصل | Network unreachable. |
| Unreachable | تعذّر الوصول | Stronger than offline. |
| Live mode | الوضع المباشر | |
| Dry run | تجريبي / محاكاة | |
| Audit log | سجلّ التغييرات | Not «سجل التدقيق» (jargon). |
| Permissions | الصلاحيات | |
| Alert | تنبيه | |
| Problem | مشكلة | |
| Diagnostic | تشخيص | |
| Topology | خريطة الشبكة | Not «طوبولوجيا». |
| IP pool | نطاق العناوين | |
| Credentials | بيانات الدخول | |
| Source address list | قائمة العناوين المسموحة | |
| Blast radius | نطاق التأثير | |
| Health score | درجة السلامة | |
| Actor | المنفِّذ | Who clicked the button. |
| Tenant | حساب المستأجر | |

## Forbidden anglicisms

These words appear in some legacy pages but should NEVER appear in new copy:

| Bad | Good |
|---|---|
| الـ API | واجهة البرمجة (or just drop it) |
| طوبولوجيا | خريطة الشبكة |
| سيرفر | خادم |
| رول-باك | تراجع |
| ديپلوي | نشر |
| كومبوننت | عنصر |
| داشبورد | لوحة التحكّم |
| كونفيغ | الإعدادات |
| لوغ | السجلّ |
| تروبلشوت | تشخيص |
| إنابلد / ديسابلد | مفعَّل / معطَّل |
| الستاتس | الحالة |

## Status pill copy

The component macro `ui.status_pill(...)` enforces these.
Don't bypass it.

| status | label_ar |
|---|---|
| `succeeded` | ناجح |
| `failed` | فشل |
| `partial` | جزئي |
| `rolled_back` | تمّ التراجع |
| `running` | قيد التنفيذ |
| `ready` | جاهز |
| `not_ready` | غير جاهز |
| `warning` | تنبيه |
| `info` | ملاحظة |
| `online` | متّصل |
| `offline` | غير متّصل |
| `unknown` | غير معروف |

## Page-title pattern

```
<service_label_ar> — <action_label_ar>
```

Examples:
- "سياسات الوصول البعيد — قائمة"
- "سياسات الوصول البعيد — معاينة"
- "الراوترات — لوحة التحكّم"
- "الراوترات — إضافة جديد"

Avoid:
- "List of Remote Access Policies" (English)
- "إدارة السياسات / الراوترات / الإعدادات" (catch-all "إدارة" is weak)

## Empty-state copy

Every "empty list" needs three things:

1. **اللوحة فارغة** — what's missing in one short sentence
2. **لماذا قد يكون كذلك** — one line of context (optional)
3. **زرّ يُحلّ الفراغ** — the CTA, e.g. «إنشاء أول سياسة»

Use `ui.empty_state(...)` macro.

## Button label conventions

- Primary CTAs: verb-first («إنشاء سياسة جديدة»، «تطبيق آمن»، «حفظ»)
- Destructive: «حذف», «إيقاف», «التراجع عن التطبيق» — never just «نعم»
- Secondary: noun-first or verb-first («تفاصيل»، «معاينة»، «تحرير»)
- Cancel: «إلغاء» (never «إغلاق» which means "close window")

## Time / numbers

- Timestamps: عرض "منذ N دقيقة" حين أقلّ من ساعة، وإلّا تاريخ كامل
- Big numbers: استخدم فاصلة الآلاف الإنجليزية `1,234` (لا تستخدم `١٬٢٣٤` — تكسر مع الـ JS).
- العملات: «د.أ» للدينار، «$» للدولار، «€» لليورو.

## Side-drawer pattern

When a page would have more than 4 cards in the main body,
move the secondary cards to the right-side drawer. The body
shows:

1. Page hero (one card max).
2. The user's primary action (one button, one form).
3. The 2-3 most important data cards.

Everything else lives in `<aside class="npc-drawer">` and is
opened from the thin icon strip on the start-side.

See `network_policy_preview.html` for the canonical
implementation. The same pattern propagates to all 12 admin
pages over time.

## Component primitives

All in `app/templates/radius/_npc_components.html`:

- `ui.status_pill(status, label_ar=None)` — colored status chip
- `ui.stat_card(value, label, tone, icon, hint)` — KPI card
- `ui.section_card(title, hint, action, icon)` — content section
- `ui.action_button(label, href, icon, tone)` — CTA
- `ui.empty_state(title, hint, icon, action_label, action_href)`
- `ui.tip_strip(text, icon, tone)` — one-line subtle hint

Import once per template:
```jinja
{% import "radius/_npc_components.html" as ui %}
```
