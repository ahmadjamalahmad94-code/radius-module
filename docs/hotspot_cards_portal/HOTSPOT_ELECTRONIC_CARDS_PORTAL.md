# Hotspot Electronic Cards Portal

## Architecture

The Hotspot Electronic Cards Portal keeps MikroTik as a lightweight captive
portal UI only. The router page will collect username/password and call
radius-module APIs. All business logic stays in radius-module:

- Portal authentication.
- Wallet balance reads and debits.
- Catalog visibility and pricing.
- Electronic card purchase.
- Immutable ledger entry.
- Card reservation/issuance.
- SMS attempt logging.

## API Contract

All endpoints are JSON-only and live under `/api/v1/hotspot/cards`.

### `POST /api/v1/hotspot/cards/login`

Request:

```json
{"username": "user", "password": "secret"}
```

Response:

```json
{
  "ok": true,
  "token": "short-lived-token",
  "expires_in": 900,
  "user": {
    "id": "subscriber:1",
    "username": "user",
    "display_name": "User",
    "phone": "059...",
    "wallet_balance": "10.00",
    "currency": "ILS"
  }
}
```

### Authenticated Calls

Send the token using:

```http
Authorization: Bearer <token>
```

or:

```http
X-Hotspot-Portal-Token: <token>
```

### Other Endpoints

- `GET /api/v1/hotspot/cards/me`
- `GET /api/v1/hotspot/cards/catalog`
- `GET /api/v1/hotspot/cards/my-cards`
- `POST /api/v1/hotspot/cards/purchase`
- `POST /api/v1/hotspot/cards/send-sms`

## Security Model

- No admin session is required.
- No license-panel credentials are accepted.
- Portal tokens are random, hashed before storage, and expire after 15 minutes.
- Login is rate-limited in memory when the app is not running tests.
- Passwords are never logged by this module.
- Existing subscriber passwords may be legacy cleartext in the current RADIUS
  schema; this slice does not create or store new subscriber plaintext secrets.
- Card-user passwords use the existing portal password hash column when present.
- Inactive, suspended, disabled, archived, deleted, or expired accounts are
  rejected.

## Why MikroTik Is UI Only

Prices, wallet balances, package availability, purchase decisions, and card
credentials are backend-owned. A future MikroTik HTML slice should only render a
small form and call these APIs. It must not contain pricing logic, wallet math,
or package business rules.

## Wallet And Ledger Rules

- The service reuses the existing Business OS `wallets`,
  `wallet_transactions`, `ledger_entries`, and `business_events` tables.
- Purchases debit the authenticated user's wallet.
- A `card_sale` ledger entry is created for every successful purchase.
- The frontend never sends or controls the price.
- `client_request_id` provides idempotency per portal user to prevent double
  purchases on refresh.

## Card Issuance

Successful purchases create one electronic `card_batches` row and one `cards`
row. The purchase table links that issued card to the portal user. The card is
not marked as used at purchase time; RADIUS accounting/use still owns the usage
state later.

## SMS Behavior

`POST /api/v1/hotspot/cards/send-sms` verifies ownership of the purchase before
doing anything. If no SMS provider is configured, the API returns:

```json
{"ok": false, "error": "sms_not_configured"}
```

Every SMS request is logged in `hotspot_card_sms_attempts` and in
`business_events`.

## Known Limitations

- The MikroTik captive portal page is not implemented in this slice.
- No actual SMS provider is implemented here.
- No payment gateway integration is implemented here.
- The portal currently defaults to tenant `1` unless `X-Tenant-Id` is provided.
