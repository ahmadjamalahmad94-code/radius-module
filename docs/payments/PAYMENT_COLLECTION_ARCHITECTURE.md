# Payment Collection Architecture

This document defines the customer-network payment collection domain for
`radius-module`. It is separate from the commercial license/payment domain in
`radius-module-admin`.

## Domain Boundary

`radius-module` owns payments for customer network operations:

- Card purchases.
- Monthly subscriber subscriptions.
- Subscriber renewals.
- Quota topups.
- Time extensions.
- Distributor or manager payments.
- Loan settlements.

`radius-module-admin` owns payments for selling HobeRadius itself: plans,
licenses, provisioning orders, renewals, and capacity contracts. These two
domains must not share tables, routes, or state transitions.

The Flask backend is the source of truth. Flutter and other clients are only
presentation/API clients and must not decide paid status, ledger entries,
entitlements, or service application.

## Payment Modes

### `manual_wallet`

Manual Wallet is the first production mode. The backend creates a payment
request and returns wallet instructions. The wallet number is routing
information only. It does not prove payment.

The payer submits a reference number, note, or safe proof attachment if the
project has a vetted upload path. An admin reviews the proof. Only admin
approval can move the request to `paid`.

### `jawwal_pay_gateway_future`

Jawwal Pay gateway support is future work. It requires official provider API
documentation, credentials, webhook signature verification, and idempotency.

Until those are present, any provider adapter must be disabled by default and
must not mark requests as paid from unsigned or unverified payloads.

## Flow

1. Payment request is created by the backend with amount, purpose, payer, and
   reference code.
2. Manual proof or a future signed webhook is attached to the request.
3. Verification happens in the backend.
4. A verified payment creates immutable accounting/ledger records.
5. Service application runs only after paid status and ledger linkage exist.

Financial success and service fulfillment are separate states. A paid request
may still be pending fulfillment if the target service cannot be safely applied
automatically.

## Payment Purposes

- `card_purchase`
- `monthly_subscription`
- `subscriber_renewal`
- `quota_topup`
- `time_extension`
- `distributor_payment`
- `loan_settlement`

## Conceptual Tables

- `tenant_payment_settings`: tenant-level provider, wallet, currency, and
  manual/API confirmation settings.
- `payment_requests`: canonical payment intent and status.
- `payment_proofs`: payer-submitted manual proof and review metadata.
- `payment_transactions`: verified provider/manual transaction records.
- `payment_webhook_events`: raw future provider events with signature and
  processing metadata.
- Ledger entries: immutable accounting records linked back to payment requests.

## Statuses

- `pending`
- `proof_submitted`
- `under_review`
- `paid`
- `rejected`
- `expired`
- `cancelled`
- `failed`

## Security Rules

- Never apply service before paid/admin accepted status.
- Never infer paid status from wallet number, screenshot alone, or client state.
- Webhooks must be idempotent.
- Future API mode requires signature verification before payment confirmation.
- Ledger entries are immutable; corrections use reversal or correction entries.
- Proof images are supporting evidence, not automatic proof.
- Provider secrets and raw sensitive payloads must not be exposed to clients.

## Future Flutter Responsibilities

Flutter may display settings, instructions, requests, proof forms, review
queues, and statuses. It must call backend APIs only and must never duplicate
payment, ledger, entitlement, or service-apply logic.

