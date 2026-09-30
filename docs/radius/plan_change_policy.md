# Change-plan and quota rules

This page records the rules for changing a subscriber's plan and for quota
periods and top-ups. The web panel and `/api/v1` use the same service code, so
these rules apply to both, and the Flutter app is expected to follow them.

Source files:
- `app/radius/services/users.py`: `change_plan`, `plan_change_direction`,
  `plan_rate_per_minute`, `add_quota` and `reset_daily_quota`.
- `app/radius/services/quota_period.py`: quota periods, the top-up scope and the
  daily and monthly windows.
- `app/radius/services/policy_engine.py`: `_check_quota`, the enforcement point.

Tests: `tests/test_fix2_plansreports_plans.py` and
`tests/test_fix2_plansreports_quota.py`.

## 1. Change plan

**Direction is decided by the price per minute, not by the total price.**
- Each plan's period is its `duration_minutes`. If that is not set, it is
  `validity_days` × 1440. If neither is set, it is **43200 minutes (30 days)**.
  Payments, extend, loans and the app's `actions-context` use the same 30-day
  fallback.
- The rate is `price / period`.
- A free plan, or a subscriber with no plan, has a rate of 0.
- `lower`: the new rate is smaller. Moving to a free plan also counts as
  `lower`.
- `higher`: the new rate is larger. Moving from a free plan (or from no plan) to
  a paid plan also counts as `higher`.
- `neutral`: the rates are equal (relative tolerance 1e-9), or both plans are
  free.
- Example: moving from 70 ILS / 30 days to 5 ILS / 1 day is **higher**, because
  the per-day price goes from 2.33 to 5.

| policy | allowed direction | effect |
|---|---|---|
| `lower_compensate` | lower | The remaining value is converted into more time: `remaining × old_rate / new_rate`. The target must not be free. |
| `lower_keep_expiry` | lower | The plan changes and the expiry stays the same. |
| `higher_debt` | higher | A debt of `(new_rate − old_rate) × remaining_minutes` is recorded in the system currency. |
| `higher_reduce_days` | higher | The remaining time shrinks to `remaining × old_rate / new_rate`. |
| `higher_keep_expiry` | higher | The plan changes and the expiry stays the same. |
| `neutral_keep_expiry` | neutral **only** | The plan changes and the expiry stays the same. |

The following requests are refused with a 422 in Arabic (a 404 where noted):
- A policy that does not match the direction. This includes a "neutral" change
  between plans whose prices differ.
- The subscriber's **current** plan.
- A **disabled** plan.
- An **archived** plan (404).
- **Owner caps (fix wave 3, F04 M1 / F08 H2) — refused, never clamped:**
  - `lower_compensate` whose computed extra time is more than **one year**
    (525,600 minutes) → 422 «أقصى تمديد في المرة الواحدة سنة — …» with the
    computed compensation and the hint to use «تغيير العرض بدون تعويض» and
    extend by hand in steps. Clamping was rejected on purpose: it would drop
    part of what the subscriber is owed without anyone noticing.
  - Any resulting expiry at or after **2101-01-01** → 422 «المدة الناتجة
    تتجاوز الحدّ المسموح.».
  - `higher_debt` whose debt is above **100,000** (the single-operation money
    cap) → 422, with the hint to use «إنقاص الأيام» or keep the expiry.
  - Nothing is written when a cap refuses (same transaction). Web and API use
    the same service, so the web flash and the API 422 carry the same text.

If the current plan is archived, its price still decides the direction.

**Expiry arithmetic.** Compensating or reducing adds the minute difference to
the current expiry, so the seconds of the expiry are kept. A zero difference
leaves the expiry unchanged. The result is never earlier than now.

Every change of plan starts a **new quota period** (section 2).

The change-plan call is idempotent: send an `Idempotency-Key` header and a
retry returns the first result.

API response additions:
- `change-plan` returns `direction`.
- `actions-context.plan` includes `rate_per_minute`.
- `/profiles` items include `period_minutes` and `rate_per_minute`.

App pickers must compare `rate_per_minute` and must hide disabled plans and the
current plan.

## 2. Quota periods and top-ups

**The period.** A quota period starts when either of these happens:
- The plan changes.
- The subscriber renews. A renewal is time added to an account that had expired
  (or had no expiry), or time added that equals at least one full plan period.
  Examples are a 30-day payment or extend on a 30-day plan.

Loans and partial extends are not renewals.

When a period starts, usage counts from that moment. The code stores a baseline
of radacct octets, so growth of sessions that are already open still counts.
If a subscriber has had no period event yet, usage is counted over all time, as
before.

**Total top-up** (`quota/topup` when the plan or the subscriber has a total
cap):
- The top-up adds to the cap that is enforced. This is the subscriber override
  when one is set, otherwise the plan's `quota_total_mb`.
- The top-up belongs to the current period only. When the next period starts,
  the override goes back to its value before the first top-up. The exception is
  when the operator has edited the override by hand since the top-up; then it is
  kept.
- So upgrading from 1 GB to 20 GB gives 20 GB, and moving to a plan with no
  quota gives no cap.

**Monthly and daily plan quotas** are enforced:
- The fields are `quota_monthly_mb` / `monthly_combined_quota_mb`,
  `monthly_download_quota_mb` / `monthly_upload_quota_mb`, `quota_daily_mb` /
  `daily_combined_quota_mb`, and `daily_download_quota_mb` /
  `daily_upload_quota_mb`.
- Windows follow the tenant's **local** calendar day and month.
- Download is `acctoutputoctets` (traffic to the user). Upload is
  `acctinputoctets`.
- If a period started this month, the monthly window starts there.

When there is no total cap, a top-up goes to the monthly window if there is one,
otherwise to the daily window. It then applies to the **current month or day
only**. The body field `quota_window` (`total` / `monthly` / `daily`) chooses a
window explicitly. The API response and `actions-context` return `daily` and
`monthly` objects with the caps and usage for each direction.

**Reset daily** («استعادة الكوتة اليوميّة») starts a fresh daily window from
now, for both the daily quota and the daily time cap. It is idempotent, so a
retry with the same `Idempotency-Key` charges only once.

**Enforcement** checks every window: total, monthly, daily and each direction.
- RADIUS authorize runs `_check_quota`. A rejection has reason `quota_exhausted`,
  and the Reply-Message names the window.
- The policy reconciler runs the same check after a save.
- `accounting_events` runs it on each **Interim-Update**. If the quota is
  exhausted, the policy reconciler sends a PoD.
- A **live sweep** runs every minute from the schedule-window worker, because in
  production FreeRADIUS writes radacct directly. Set
  `HOBERADIUS_QUOTA_SWEEP_ENABLED=0` to turn it off.

**Currency.** Quota money (paid or debt top-up and reset) is always recorded in
the system currency. A `currency` sent by a form is ignored.
