# Network Policy Center — Implementation Plan

> **Status:** Phase 0 (inspection-only). No code touched yet.
> **Owner:** radius-module backend + radius-module-app (Flutter).
> **Out of scope:** `radius-module-admin` (license panel). Do **not** touch.
> **Date:** 2026-05-23.

---

## 1. Goal

Ship a single, unified, operator-facing module — **Network Policy Center**
(internal abbreviation **NPC**) — that consolidates three currently-scattered
operational needs into one product surface, on one router, behind one
audit trail and one permission family:

| # | Sub-service              | What it does                                                                                                | RouterOS surface                                |
|---|--------------------------|-------------------------------------------------------------------------------------------------------------|-------------------------------------------------|
| 1 | **Remote MikroTik Access** | Operator-controlled remote admin reach to a tenant router (winbox/webfig/SSH/API) over the existing WG tunnel, with revocation. | `/ip/firewall/filter` (input chain) + `/ip/service` toggles. |
| 2 | **Website / App Blocking** | Block specific destinations for hotspot+PPPoE clients (TikTok, gambling, custom domains). Time-windowed + group-scoped. | `/ip/firewall/address-list` + `/ip/firewall/filter` (forward chain). |
| 3 | **Hotspot Walled-Garden Allowlist** | Allow specific destinations to be reachable before login (captive portal allowlist) — payment gateways, SMS OTP providers, support chat. | `/ip/hotspot/walled-garden` + `/ip/hotspot/walled-garden/ip`. |

All three share the **same MikroTik adapter, audit log, permission
checker, backup-awareness guard, and dry-run preview** as VX2 Site Exit
— this is a **horizontal feature**, not three vertical silos.

## 2. Non-goals

- ❌ Replacing VX2 Site Exit. VX2 routes traffic _through_ a VPS; NPC
  blocks/allows traffic _at_ the router. Different problem, kept separate.
- ❌ Live router contact from Flutter. The Flutter app is presentation
  only; every action proxies to the Flask backend over `/api/v1`.
- ❌ Storing router admin credentials in Flutter or shipping them down.
  Credentials live in the existing `nas_devices` row, server-side only.
- ❌ DPI / URL-level blocking. RouterOS firewall operates at L3/L4 +
  address-list; we do not promise SNI inspection or content filtering.

---

## 3. Architectural constraints (from the brief — locked)

1. **Flask backend is the single source of truth.** Every policy, every
   target, every applied state is persisted in the SQLite DB inside
   radius-module. Flutter never persists state of its own.
2. **No business logic in Flutter** beyond presentation + form state.
   Validation must run server-side; Flutter mirrors error messages but
   does not duplicate the rules.
3. **Do not touch `radius-module-admin`.** That's the license panel —
   completely separate codebase.
4. **No `git add .` — explicit staging only.** Every commit lists files
   by name.
5. **No hardcoded secrets, no plaintext private keys in the DB.** The
   VX2 precedent (no `private_key` column on `vps_exit_nodes`) is
   followed verbatim.
6. **Dry-run vs apply is a contract, not a UI hint.** Operators must
   see the exact RouterOS script before any side effect. Apply must
   re-run validation server-side — never trust the preview hash alone.
7. **Anchored prefix on every managed object.** Mirroring VX2's
   `HOBE_VX2_SITE_EXIT:<id>:` convention, every NPC-managed rule
   carries `HOBE_NPC_<service>:<policy_id>:` so cleanup/rollback can
   match by exact prefix regex (`^HOBE_NPC_BLOCK:42:`).

---

## 4. Codebase findings (Phase 0 inspection)

### Backend (`radius-module`)

- **Migrations:** `app/radius/db/migrations/` — numbered 001…043; SQL
  applied at boot. **Next free number: 044.** NPC will likely span 044
  → 047 (one per sub-service plus a shared deployment/script table).
- **Repos:** `app/radius/db/repos/<thing>_repo.py` — allow-listed
  `update()`, explicit `transaction()` contexts, no implicit FK cascade
  (we cascade in the repo `delete()`).
- **Pure services:** `app/radius/services/<thing>.py` — validators,
  classifiers, importers, planners, renderers. Importable with **no
  DB / network / FS side effects** (VX2 ships an explicit test for
  this; we will too).
- **Routes:** `app/radius/routes/<thing>.py` — Flask blueprints, CSRF
  via `_csrf_token`, permission guards via
  `mt_permissions.requires_perm`. Registered in
  `app/radius/routes/blueprint.py`.
- **MikroTik adapter:** `app/radius/integration/mikrotik/client.py` —
  thread-safe `MikrotikClient` context manager; `run()` for mutations,
  `print_()` for reads. Already used by VX2 apply.
- **Audit:** `app/radius/services/audit.py` exposes `get_audit_service()`
  with `.record(actor=..., event_type=..., subject=..., payload={...})`.
  VX2 records `site_exit.apply_attempted`, `site_exit.applied`,
  `site_exit.apply_failed`. NPC will record analogous events scoped to
  its three sub-services.
- **Permissions:** `app/radius/services/mt_permissions.py` — extend
  `ALL_PERMISSIONS` tuple and `_IMPLIED_BY_ADMIN` frozenset. Pin test
  in `tests/` will need its allowlist updated (mirrors the VX2 pattern).
- **Sidebar:** `app/templates/admin/_sidebar.html` — Network section
  (`data-hb-section="network"`) is where the NPC entry will live.
- **REST API:** `app/api/v1/<resource>.py`, each module exposes a
  `register(v1_blueprint)` function called from `app/api/v1/__init__.py`.
  Pattern: `/api/v1/<resource>` for collection, `/api/v1/<resource>/<id>/<action>` for actions. NAS is the cleanest reference.
- **Tests:** `tests/test_<phase>_<topic>.py` — pytest, all pure tests
  green with no test DB needed for service-layer files. UI tests use
  Flask test client with CSRF helper. VX2 suite is the template.
- **Docs:** `docs/radius/<TOPIC>.md` for per-feature writeups;
  `docs/network_policy_center/` is **this** plan's home.

### Flutter (`radius-module-app`)

- **Feature layout:** `lib/features/<name>/{data, domain, presentation,
  application}/` — Riverpod providers for data layer, freezed-ish models
  in `domain`, screens in `presentation`. NAS is the cleanest template.
- **API client:** `lib/core/api/api_client.dart` — single `Dio`
  instance, Bearer auth, `/api/v1/<resource>` paths, JSON envelope
  `{ok, data, error}`. We reuse this verbatim — no new HTTP layer.
- **Router:** `lib/core/router/app_router.dart` (go_router). New
  routes register in the same provider.
- **Shell:** `lib/features/shell/shell_scaffold.dart` — bottom-nav
  scaffold. NPC will surface from the "More" tab
  (`features/more/presentation/more_screen.dart`) initially, then
  graduate to its own section if the operator pilot validates traffic.
- **No `radius-module-admin` touchpoints required.** That panel is
  the license module; NPC has nothing to say to it.

---

## 5. Data model — proposed tables (Phase 1)

Schema decisions inherit VX2's defensive pattern:

- Enums live in repo constants, **not** DB CHECKs, so we can add new
  values without a migration.
- `created_at` / `updated_at` ISO-8601 strings.
- `tenant_id` on every top-level row.
- One **policy** per (router, sub-service, name) tuple.
- A **deployments** lifecycle row per policy (draft → previewed →
  applied / failed), reusable across applies.
- A **script versions** history with the rendered RouterOS script + a
  hash — exactly mirroring VX2's audit-safe reproduction story.

```
remote_access_policies          (id, tenant_id, router_id, name, slug,
                                  expires_at, allow_winbox, allow_ssh,
                                  allow_api, allow_webfig, allow_https,
                                  source_address_list, enabled, …)

web_block_policies              (id, tenant_id, router_id, name, slug,
                                  scope, target_group_id, time_window_id,
                                  fail_open, enabled, …)
web_block_targets               (id, policy_id, target_type, value,
                                  normalized_value, group_name, status,
                                  …)                    -- normalized=lowercase,
                                                            no scheme/trailing dot

walled_garden_policies          (id, tenant_id, router_id, hotspot_profile,
                                  name, slug, enabled, …)
walled_garden_entries           (id, policy_id, entry_type, value,
                                  normalized_value, status, …)
                                                          -- entry_type ∈
                                                            {dst-host, dst-address,
                                                             dst-port, protocol}

npc_deployments                 (id, tenant_id, service, policy_id,
                                  router_id, status, script_hash,
                                  last_preview_at, last_applied_at,
                                  last_error, last_audit_id, …)
npc_script_versions             (id, service, policy_id, deployment_id,
                                  script_hash, script_body,
                                  rollback_script_body, command_count,
                                  generated_by_admin_id, created_at)
```

`service ∈ {remote_access, web_block, walled_garden}` keeps a single
deployments table reusable across the three sub-services without a
table explosion. Final shape locks in Phase 1 when the migration lands;
any drift from this plan gets recorded as an `[A]mendment` block at
the bottom of this file before commit.

---

## 6. Permission catalogue (Phase 3)

Following VX2's split between view / manage / preview / apply:

```
npc.remote_access.view
npc.remote_access.manage      -- create/edit policy
npc.remote_access.preview     -- generate the RouterOS preview
npc.remote_access.apply       -- actually push to router

npc.web_block.view
npc.web_block.manage
npc.web_block.preview
npc.web_block.apply

npc.walled_garden.view
npc.walled_garden.manage
npc.walled_garden.preview
npc.walled_garden.apply
```

`PERM_ADMIN` implies all `*.view`, `*.manage`, `*.preview` — but **not**
`*.apply`. Apply is opt-in even for admins, matching VX2's stance on
destructive surfaces.

---

## 7. Audit events (Phase 3)

Every state-changing action emits:

```
npc.<service>.preview_generated
npc.<service>.apply_attempted          -- pre-router-contact
npc.<service>.applied                  -- success
npc.<service>.apply_failed             -- transport / trap / rollback
npc.<service>.rolled_back              -- explicit cleanup
npc.<service>.policy_created
npc.<service>.policy_updated
npc.<service>.policy_deleted
npc.<service>.target_added
npc.<service>.target_removed
```

Payload always includes `tenant_id`, `router_id`, `policy_id`, the
script hash, the actor admin id, and (for failures) the trap message.

---

## 8. API surface (Phase 4 + 7)

```
GET    /api/v1/network-policy/<service>/policies?router_id=<id>
POST   /api/v1/network-policy/<service>/policies
GET    /api/v1/network-policy/<service>/policies/<id>
PATCH  /api/v1/network-policy/<service>/policies/<id>
DELETE /api/v1/network-policy/<service>/policies/<id>

POST   /api/v1/network-policy/<service>/policies/<id>/targets
DELETE /api/v1/network-policy/<service>/policies/<id>/targets/<tid>

POST   /api/v1/network-policy/<service>/policies/<id>/preview   -- returns script + diff
POST   /api/v1/network-policy/<service>/policies/<id>/apply     -- guarded; returns deployment row
POST   /api/v1/network-policy/<service>/policies/<id>/rollback
```

`<service> ∈ {remote-access, web-block, walled-garden}`. Server-rendered
HTML routes live under `/admin/radius/network-policy/<service>/...`
mirroring the JSON paths so deep links from the Flutter app can hand
off to the web UI for advanced operations.

---

## 9. UI plan

### Server-rendered (Phase 6) — primary

- Sidebar entry under **الشبكة → سياسات الشبكة (Network Policy Center)**.
- Three-tab landing page (one tab per sub-service) so operators get
  one URL to bookmark.
- Each tab uses the existing **`access_schedule_picker`** for time
  windows and **`unit_input_picker`** for value+unit inputs (per the
  canonical cookbook entry — no bespoke pickers).
- Preview drawer renders the exact RouterOS script, copy-to-clipboard,
  diff against last-applied version.
- Apply button is `disabled` until preview is generated **and** the
  operator's permission set includes `*.apply`.

### Flutter (Phase 7 + 8) — companion

- New feature folder `lib/features/network_policy/`.
- Three list screens (one per sub-service) + a shared policy-edit
  screen with target list + apply button.
- No script preview in Flutter (too dense for mobile) — the apply
  button opens a confirmation sheet with the diff summary and a
  "View full script" link that opens the Flask preview page in an
  external browser.

---

## 10. Risk register

| # | Risk                                                          | Mitigation                                                                                                                |
|---|---------------------------------------------------------------|---------------------------------------------------------------------------------------------------------------------------|
| 1 | Locking the operator out via Remote Access policy             | Apply path always emits the inverse cleanup script first into `npc_script_versions` so rollback is a single button.       |
| 2 | A `walled_garden` block on a payment gateway breaks captive portal | Walled-garden _adds_ entries; never removes default permissive entries; classifier warns on inputs that look like login providers. |
| 3 | Web-block list growing past RouterOS firewall capacity        | Validator caps per-policy target count (mirrors VX2 cap). UI surfaces the count.                                          |
| 4 | Tenant cross-contamination                                    | Every repo query scopes by `tenant_id`; tests assert mismatched-tenant 404.                                               |
| 5 | Sibling-automation contamination (the `.codex-recovery/` issue from VX2) | `.codex-recovery/` already in `.gitignore`. Stage files explicitly each commit.                                            |
| 6 | Apply against a router with placeholder credentials           | Reuse VX2.6d `_check_router_credentials()` guard verbatim — refuses to dial if password is `change-me` etc.               |

---

## 11. Phase index (commit discipline)

Each phase is a single PR-sized commit. Commit message stem is
`NPC <phase>: <topic>`.

| Phase | Commit subject                                                   | Acceptance gate                                            |
|-------|------------------------------------------------------------------|------------------------------------------------------------|
| 0     | Document network policy center implementation plan               | This file lands; nothing else changes.                     |
| 1     | NPC migrations + repos                                           | `pytest tests/test_npc_1_*` green; migration applies clean.|
| 2     | NPC pure services (validator + classifier + planner + renderer)  | Pure-test suite green; no DB / network in service imports. |
| 3     | NPC permissions + audit events                                   | Pin test updated; events appear in audit log on test path. |
| 4     | NPC routes + dry-run preview                                     | Preview returns script; CSRF works; perms enforced.        |
| 5     | NPC guarded apply integration                                    | Apply records deployment; failure path rolls back cleanly. |
| 6     | NPC server-rendered UI (Arabic RTL)                              | Three tabs render; sidebar entry visible; pickers reused.  |
| 7     | NPC Flutter API client + models                                  | `dart analyze` clean; repo methods return typed models.    |
| 8     | NPC Flutter screens + navigation                                 | Three screens reachable from More; widget test green.      |
| 9     | NPC tests — backend + Flutter                                    | Full suite green; coverage proves regression protection.   |
| 10    | NPC docs + completion log                                        | README + cookbook entry; this file marked Final.           |

---

## 12. Out-of-band rules carried over from VX2

- Append every new service file to `SERVICES_COOKBOOK.md` immediately
  after creating it.
- Parse-check then commit after every addition; never batch unrelated
  changes.
- Reuse `access_schedule_picker` + `unit_input_picker`; never write
  custom day pickers or value+unit inputs.
- `docs/network_policy_center/` is the only place this feature owns
  documentation. Existing `docs/radius/` is for radius-core topics.

---

## 13. Amendments

_Reserved. Any decision that diverges from §5–§10 gets logged here
with a date stamp before the diverging commit lands._
