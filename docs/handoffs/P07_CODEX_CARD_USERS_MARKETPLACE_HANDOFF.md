# Prompt 07 — Card Users, Wallet Portal Model, and Card Marketplace Handoff

## Commit

This commit. The exact final hash is recorded in the execution report.

## What changed

- Added additive database foundations for card users, marketplace packages, and card-user purchases.
- Added `CardUsersMarketplaceService` for:
  - card-user profile creation
  - card-user wallet creation/recharge
  - marketplace package creation/listing
  - wallet-funded package purchase
  - local card generation/assignment
  - Business OS ledger, revenue, and event recording
  - card-user 360 aggregation
- Added web routes under the existing Radius namespace:
  - `GET/POST /admin/radius/card-users`
  - `GET /admin/radius/card-users/<id>`
  - `POST /admin/radius/card-users/<id>/recharge`
  - `POST /admin/radius/card-users/<id>/purchase`
  - `GET /admin/radius/card-marketplace`
  - `POST /admin/radius/card-marketplace/packages`
- Added UI templates for card-user list, card-user 360, and marketplace.

## Safety notes

- No live RADIUS mutation was introduced.
- No MikroTik or router execution was added.
- Purchases generate local card records and financial events only.
- Card delivery is intentionally recorded as an `event_only` placeholder.
- Existing card/batch UI files with unrelated dirty changes were not touched or staged.

## Tests run

- `python -m compileall app` — passed
- `python -m pytest tests/test_card_users_marketplace.py -q` — 5 passed
- `python -m pytest tests/test_card_users_marketplace.py tests/test_subscriber_360.py tests/test_finance_center_web.py tests/test_business_os_core_foundations.py -q` — 20 passed
- `git diff --check` scoped to Prompt 07 files — passed; Git emitted existing LF-to-CRLF warnings

## Remaining risks

- Marketplace package creation currently requires an existing `access_plans.id` because generated cards must stay compatible with the existing card schema.
- Message delivery remains a placeholder event and is not yet SMS/WhatsApp/email delivery.
- Purchases create one local batch/card per purchase; future scaling work can pool inventory by package.
