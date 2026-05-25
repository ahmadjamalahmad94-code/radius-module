# Jawwal Pay Integration Notes

Jawwal Pay support is future work. This project must not pretend a real gateway
exists until official API documentation, credentials, sandbox behavior, webhook
signature rules, and provider support contacts are available.

## Current Position

- Manual Wallet ships first.
- Wallet number is routing/instructions only.
- Manual admin approval is required for payment confirmation.
- A future Jawwal Pay adapter must be disabled by default.

## Requirements Before Enabling API Mode

- Official Jawwal Pay API documentation.
- Merchant credentials and environment separation.
- Webhook signature verification specification.
- Provider transaction ID or event ID for idempotency.
- Clear mapping between provider events and local `payment_requests`.
- Operational runbook for failed, duplicate, delayed, and reversed payments.

## Safe Provider Shell Behavior

Until real provider requirements are known:

- `create_payment` may return `provider_not_configured`.
- `verify_webhook_signature` must fail closed.
- `parse_webhook_event` must not infer paid status from unknown fields.
- Webhook events may be stored for diagnostics, but unsigned events must not
  mark requests paid.
- No provider secrets may appear in logs, UI, or client responses.

## Future Confirmation Flow

1. Provider creates or accepts a payment intent.
2. Provider sends a signed webhook or backend verifies status through official
   API.
3. Backend validates signature and idempotency.
4. Backend maps event to one local request.
5. Backend marks payment paid only for confirmed success events.
6. Ledger and service application run through existing backend workflows.

