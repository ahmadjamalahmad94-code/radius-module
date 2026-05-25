# P14 Codex Handoff - Subscriber and Card User Portals

## Commit

Recorded in Git with prompt commit `feat: add subscriber card user portals`.

## Scope

Prompt 14 added self-scoped customer portal foundations inside `radius-module`.

## Implemented

- Added `customer_portal_requests` for renewal/loan/support request tracking.
- Added `CustomerPortalService` with:
  - subscriber credential authentication
  - purchased-card authentication for card users
  - self-scoped subscriber dashboard data
  - self-scoped card-user dashboard data
  - profile-level loan policy evaluation
  - loan request auto-approval when plan policy allows
  - renewal request placeholder tracking
  - marketplace purchase delegation through the existing card marketplace service
- Added public portal routes under `/admin/radius/portal/...`.
- Added standalone portal templates without admin navigation.

## Safety Notes

- Portals expose only the authenticated subscriber/card-user scope.
- No MikroTik walled-garden configuration is changed automatically.
- No live RADIUS activation is introduced.
- Renewal/payment remains a placeholder unless existing gateway work is added later.
- Walled-garden requirement is documented in the portal UI and release docs should list required URLs.

## Tests

Run after implementation:

```powershell
python -m compileall app
python -m pytest tests/test_customer_portals.py -q
python -m pytest tests/test_customer_portals.py tests/test_card_users_marketplace.py tests/test_accounting_loans_foundation.py -q
git diff --check
git status --short
```

## Remaining Risks

- Portal routes currently live under the existing radius blueprint prefix, so public URLs are `/admin/radius/portal/...` unless a future app-level public blueprint is added.
- Subscriber/card credentials rely on existing local credential storage; no new password hashing migration is introduced here.
- Payment/recharge gateway integration remains a placeholder.
- MikroTik walled-garden entries must be configured manually or through a future guarded planner.
