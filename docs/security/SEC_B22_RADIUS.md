# SEC B-22 (radius side): signed `owner_admins` on the runtime contract

| | |
|---|---|
| Branch | `agent/sec-fix-b22-radius` (local only; not pushed, not deployed) |
| Base | `agent/sec-integration` @ `36646db3` |
| Panel counterpart | `radius-module-admin` `agent/sec-fix-b22` `c289390` (+ doc `SEC_FIX_B22.md`) |
| Commits | `159918f8` failing test · `bcd27845` fix · this doc |

## Problem
`LicenseAdminRuntimeSyncService.sync_runtime_contract_once` passed
`contract["owner_admins"]` straight to `apply_owner_admins_designation()`.
Whoever answered `POST /api/integration/hoberadius/runtime-contract` (a rogue or
repointed panel URL, MITM on a non-HTTPS bridge URL) could name any local admin
OWNER: full RBAC bypass, uncapped credit, every tenant on a hosting server.

## Fix
`app/radius/services/license_admin_runtime_sync.py::_apply_signed_owner_admins`

* Non-empty `owner_admins` is applied **only** if the **raw** response carries a
  `_bridge_sig` that we reproduce with **our own** `AdminBridgeConfig.license_key`.
* Verification reuses `license_admin_identity_sync._bridge_signature_valid`
  (canonical spec byte-identical to the panel's `license_signing.sign_bridge_response`:
  `HMAC-SHA256(key=license_key.strip().upper(), json.dumps(body_without_sig, ensure_ascii=False, separators=(",",":"), sort_keys=True))`,
  constant-time compare). No new key material; the licence key is already on the box.
* Verified over `_raw_response`, not the stored snapshot, because the snapshot
  masks `license_key` / `bridge_token` and would never verify.
* Refused → current designation kept (or the min-id fallback stays), one
  `WARNING` line (`runtime-contract: owner_admins designation (N key(s)) REFUSED — signature missing|invalid`).
  The list contents are not logged.
* Everything else in the contract (status, limits, services, bridge_token) syncs as before.
* Unlike identity-sync this path does **not** require the
  `HOBERADIUS_BRIDGE_TRUST_ADMIN_ESCALATION` opt-in: runtime-contract owner designation is
  the normal, already-in-production way owners are set; gating it behind an opt-in that is
  OFF everywhere would silently freeze every customer's owner list. See owner decision 1.

## Tests
`tests/test_sec_b22_runtime_owner_admins.py` (11): before fix 5 failed / 6 passed; after 11 passed.
Covers unsigned, another customer's key (cross-tenant), tampered list, signature made with
a key echoed inside the body, garbage signature, correct signature, signature over a body
with sensitive fields, key case/whitespace normalisation, old panel without the field,
signed empty list (never strips), no local key.

Regression (all pass): `test_owner_designation_sync`, `test_sec_bridge_trust`,
`test_license_admin_runtime_sync`, `test_license_heartbeat_runtime_contract`,
`test_license_admin_identity_sync`, `test_bridge_token_sync`.

## Behaviour change / migration impact
* No schema or data migration.
* **Rollout order: panel first, then radius.** A radius running this fix against an
  unpatched panel refuses every owner designation update (logs a warning each cycle)
  and keeps the owners it already has: fail-safe, nobody loses access, but new
  designations made in the panel will not arrive until the panel is upgraded.
* A radius whose `license_key` differs from the key the panel signs with (e.g. key
  rotated on the panel but not locally) will refuse designations until fixed.

## Rollback
Revert `bcd27845` (code only) and rebuild the image. Stored designations are untouched.

## Owner decisions
1. Keep "signature only" for runtime-contract (this branch), or also require the
   `HOBERADIUS_BRIDGE_TRUST_ADMIN_ESCALATION` opt-in as the panel doc §6.1 suggests
   (would stop owner designation on every server that has not set it).
2. `capacity-contract` is signed by the panel but its `owner_admins` is not consumed
   on the radius today; no change needed unless a consumer is added.
