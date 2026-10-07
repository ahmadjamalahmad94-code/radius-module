# HobeRadius terminology glossary

Owner decision, 2026-10-06. This file is the reference that translators, docs
writers and the ops/support assistant should all use. The Arabic UI wording is
the source of truth and is **not** changed by this glossary. Each concept gets
exactly one term in every other language.

Translations live in `translations/MASTER.csv`. The workflow is
`python tools/i18n_master.py sync`, then `export`, edit the CSV, then `import`.
The guard is `tests/test_i18n_no_leak_guard.py`.

## 1. The four core concepts

| Concept | Arabic UI (keep) | English | French | Turkish | Spanish | DB table | Main code names |
|---|---|---|---|---|---|---|---|
| **Plan**: a service/speed plan with speed, quota, time, price and schedule | «باقة» / «الباقة» / «باقات» (also called ملف السرعة / خطة / بروفايل in conversation) | **Plan** ("Service plan" when it needs disambiguating) | forfait | paket | plan | `access_plans` | `AccessPlan`, `PlansService`, `plan_id`, `plan_name` |
| **Offer**: a sellable commercial template. Cards are generated from it. | «عرض» / «العرض» / «العروض» | **Offer** | offre | teklif | oferta | `card_offers` (+ `card_offer_visibility`); marketplace: `card_marketplace_packages` | `CardOffersService`, `offer_id` |
| **Card batch**: a generated or imported batch of cards | «حزمة بطاقات» / «حزمة» / «الحزم» | **Card batch** / **Batch** | lot de cartes / lot | kart partisi / parti | lote de tarjetas / lote | `card_batches` | `batch_id`, `batch_code`, `package_name` (= batch display name) |
| **Card**: one prepaid login (username + password) | «كرت» / «كروت» / «بطاقة» / «بطاقات» / «كارت» | **Card** | carte | kart | tarjeta | `cards` | `card_id`, `username`, `password` |

### How the concepts relate

```
access_plans (Plan, «باقة»)
   ▲ plan_id                       ▲ plan_id (optional)
   │                               │
card_batches (Card batch) ──► card_offers (Offer, «عرض»)
   │  batch_id                     (wholesale_minor / selling_minor / duration_minutes)
   ▼
cards (Card, «كرت/بطاقة»)
```

* A **card batch** always points at a **plan** through `card_batches.plan_id`,
  and each card copies that `plan_id`.
* A sub-admin uses an **offer** to generate a **batch**. The offer locks the
  price and duration. The wallet debit has `reference_type = "card_offer_package"`
  and `metadata.offer_id`.
* A **subscriber** (`subscribers.plan_id`) is on a **plan**.

## 2. Related terms that are NOT the four concepts

These look similar, so they get their own translations. Never map them onto
plan, offer, batch or card.

| Arabic | Meaning | English | Notes |
|---|---|---|---|
| «كوبون» / «الكوبونات» | Top-up / balance voucher | **Voucher** | Table `vouchers`, API `/api/v1/vouchers`. FR coupon · TR kupon · ES cupón |
| «ملفات السرعة» | Bandwidth/speed profile | **Speed profile** | Table `bandwidth_profiles`, API `/api/v1/bandwidth-profiles` |
| «عرض» as a verb or noun for display | View / show / display; also «عرض» = width | View / Show / Display / Width | For example «عرض التفاصيل», «طريقة العرض», «عرض 360°» |
| «دفعة» meaning money | Payment | **Payment** | Ledger `payment`. The standalone msgid «دفعة» / «دفعات» is translated *Payment(s)* |
| «دفعة واحدة» | At once | at once | Not a batch |
| «حزمة» outside cards | Network packets, ZIP package (hotspot designer), permission bundle, support bundle | packets / package / bundle | Not a card batch |
| «خطة» / «الخطّة» outside plans | Action/setup plan (network policy, device health, setup wizard, recovery) | plan / setup plan | Setup wizard uses "service **setup** plan" so it is not confused with a service plan |
| «النطاق» | Scope | **Scope** | Was wrongly translated as "Plan" |
| «الشدّات» | Severities | Severities | Was wrongly translated as "Plans" / "Bundles" |

## 3. API fields and routes (exact names; do not translate)

### Plans: `access_plans`

Web routes below are relative to `/admin/radius`.

* Web: `/plans`, `/plans/<plan_id>/edit`, `/plans/new`, `/plans/overview`
* API: `/api/v1/profiles`, `/api/v1/profiles/<profile_id>`, `/api/v1/plans/options`
  (alias `/profiles/options`). **Trap:** the API name `profiles` means *plans*,
  not speed profiles.
* Fields: `id`, `name`, `code`, `plan_type`, `service_type`, `duration_value`, `duration_unit`,
  `duration_minutes`, `validity_days`, `quota_total_mb`, `speed_up_kbps`, `speed_down_kbps`,
  `burst_*`, `bandwidth_id` (→ speed profile), `concurrent_sessions`, `allowed_days`,
  `allowed_hours_from/to`, `offer_hours_from/to` (shown in Arabic as «ساعات العرض»)
* On other objects: `plan_id`, `plan_name`, `plan_ids`, `plan_status`, `plan_currency`

### Offers: `card_offers`

* Web: `/cards/offers`, `/cards/offers/<offer_id>/edit|toggle|use|visibility`
* Fields: `id`, `name`, `plan_id` (optional plan link), `duration_minutes`, `wholesale_minor`
  (wholesale price × 100), `selling_minor` (retail price × 100), `currency`, `active`,
  `device_limit_mode`, `notes`
* Visibility allow-list: `card_offer_visibility(offer_id, admin_id)`
* Marketplace offers («عروض» in the card marketplace) live in `card_marketplace_packages`.
  Web: `/card-marketplace/packages/<package_id>/…`. Store API: `/api/v1/store/packages`,
  field `package_id`. Here *package* in code = **Offer** in the UI.

### Card batches: `card_batches`

* Web: `/cards/batches`, `/cards/batches/<batch_id>/cards`, `/cards/batches/<batch_id>/edit`,
  `/cards/batches/export.csv|xlsx|pdf`, `/cards/batches/import`, `/cards/batches/bulk`;
  top-up batches: `/cards/recharge/<batch_id>`
* API: `/api/v1/cards/batches`, `/api/v1/cards/batches/<batch_id>`, `…/<batch_id>/cards`,
  `…/<batch_id>/summary`, `/api/v1/cards/generate`
* Fields: `id`, `batch_code`, **`package_name`** (the batch's display name, «اسم الحزمة»;
  *package* in code = **Batch** in the UI), `plan_id`, `count`, `generated`, `used`, `status`,
  `price_per_card`, `price_bulk`, `total_price`, `currency`, `username_prefix/suffix/length`,
  `password_length/charset`, `include_batch_number`, `expire_at`,
  `validity_after_first_login_days`, `count_from_first_connect`, `distributor_id`, `assigned_to`,
  `service_name`, `source_type`
* On other objects: `batch_id`, `batch_ids`, `card_batch_id`, `batch_name`

### Cards: `cards`

* API: `/api/v1/cards/<card_id>` (+ `/disable`, `/enable`, `/revoke`, `/reset-usage`,
  `/adjust-time`, `/lock-mac`, `/unlock-mac`, `/disconnect`, `/delete-permanent`)
* Fields: `id`, `batch_id`, `plan_id`, `username`, `password`, `used`, `revoked`, `archived`,
  `expire_at`, `first_used_at`, `used_by_mac`, `locked_mac`
* Card users (store customers): table `card_users`, API `/api/v1/store/*`, `/api/v1/hotspot/cards/*`
  ("Card users" / «مستخدمو البطاقات»)

## 4. Known Arabic-side inconsistencies (not changed; the owner decides)

The Arabic UI is frozen by the owner's decision. Translations follow the
**concept**, not the literal Arabic word, in the places listed below.

1. **«العرض» labels the plan selector (`plan_id`) in many places.** Examples:
   subscriber list column, sessions filter, audit field `plan`, card-batch form,
   speed-rules target `plan`. The English says **Offer** there, because it
   follows the Arabic word. The code entity underneath is `access_plans`.
2. **«باقة كروت» / «باقة الكروت» is used for a card batch.** Examples: the
   batch edit page title, and the speed-schedule target. The English says
   **Card batch**.
3. **«دفعة» / «دفعات» is used for card batches.** Examples: cards overview
   counter, cards list filter, print batches, top-up batches. The batch
   meaning is translated *Batch* when the msgid is batch-specific, such as
   «دفعة طباعة جديدة» or «كل الدفعات». The bare msgids «دفعة» and «دفعات» are
   shared with the payments ledger, so they stay *Payment(s)*. The cards
   overview subtitle uses `ar_count(_('دفعة'), …)`, so it shows "payments" in
   English. The fix is to switch it to `ar_count('batch')`, which needs
   «حزمة» in Arabic.
4. **«الباقة (الحزمة)»** in `manager_distributor_ops._ENTITY_LABELS["batch"]`
   mixes both words for a batch.
5. **The bare msgid «عرض» is used for two different things.** It is the
   "View" button (eye icon, `permission_labels["view"]`) and it is also the
   audit noun for `plan` / `offer` (`audit_format`, `audit_log`,
   `manager_activity_audit`). One msgid can hold only one translation, so it
   stays **View**. The fix is code-side: the noun call sites should use
   `«العرض»`, which translates as *Offer*, or a `pgettext` context.
6. **Batch PDF export headers** (`pdf_theme.build_batches_pdf`) use «الباقة»
   for the batch name column and «الخطة» for the plan.

## 5. Rules for new strings

* Wrap the Arabic with `_()`, `_tr()`, `N_()` or `hrT()`. Then run
  `i18n_master.py sync` and `export`, fill in en/fr/tr/es using the table in
  §1, then run `import`.
* Never use *Package / Pack / Bundle* for a card batch. Never use *Plan* for
  an offer or a batch. Never use *Voucher* for a card, because *Voucher* is
  reserved for «كوبون».
* For a check, grep `MASTER.csv` for rows whose msgid contains «حزم» and
  whose `en` lacks "batch". Do the same for «باق» with "plan", «العروض» with
  "offer", and «كرت/بطاقة» with "card". Each hit should be one of the
  exceptions in §2.
