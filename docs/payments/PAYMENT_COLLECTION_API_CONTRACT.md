# Payment Collection API Contract

This contract belongs to `radius-module` customer-network payments only.
Commercial HobeRadius license payments belong to `radius-module-admin`.

## Modes

- `manual_wallet`: returns backend-generated payment instructions and requires
  manual admin review before paid status.
- `jawwal_pay_gateway_future`: reserved for a future signed provider
  integration. Disabled until official API/webhook details exist.

## Purposes

- `card_purchase`
- `monthly_subscription`
- `subscriber_renewal`
- `quota_topup`
- `time_extension`
- `distributor_payment`
- `loan_settlement`

## Statuses

- `pending`: request created and awaiting payment/proof.
- `proof_submitted`: payer submitted manual proof for review.
- `under_review`: admin review is in progress.
- `paid`: backend verified payment manually or through a future signed provider.
- `rejected`: proof/payment was rejected.
- `expired`: request passed its configured TTL.
- `cancelled`: request was cancelled before payment.
- `failed`: provider or processing failure.

## Planned Endpoints

### Admin settings

- `GET /api/v1/payments/settings`
- `PATCH /api/v1/payments/settings`

Settings responses must not expose provider secrets.

### Payment requests

- `POST /api/v1/payments/requests`
- `GET /api/v1/payments/requests`
- `GET /api/v1/payments/requests/<id>`
- `GET /api/v1/payments/requests/<id>/instructions`

Creation validates tenant settings, amount, currency, purpose, provider, and
purpose enablement. The created request starts as `pending`, receives a
collision-safe `reference_code`, and copies the receiver wallet for audit
stability.

The instructions endpoint returns safe payer fields only:

- amount
- currency
- receiver_wallet
- wallet_owner_name
- reference_code
- expires_at
- instructions
- status

### Proof and review

- `POST /api/v1/payments/requests/<id>/proofs`
- `GET /api/v1/admin/payments/review-queue`
- `POST /api/v1/admin/payments/requests/<id>/approve`
- `POST /api/v1/admin/payments/requests/<id>/reject`

Clients cannot set `paid`. Approval is admin-only and creates verified payment
transaction records before ledger/service-apply slices run.

### Future Jawwal Pay webhook shell

- `POST /api/v1/payments/webhooks/jawwal-pay`

The endpoint stores raw events and idempotency keys. It must not mark paid
unless signature verification and event mapping are implemented and pass.

## Validation Rules

- Amount must be positive.
- Currency must be allow-listed.
- Provider must be allow-listed.
- Purpose must be allow-listed.
- Status transitions must be controlled by backend services.
- Wallet number is stored as text and is never treated as proof of payment.

## Service Application Contract

Service application is a later backend-only step. It requires:

1. payment request status `paid`
2. verified transaction
3. linked ledger entry
4. idempotency guard to prevent double issuance, extension, or credit

