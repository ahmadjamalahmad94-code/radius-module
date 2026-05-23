# HobeRadius Zero-Engineer Setup Wizard — Architecture (SW0)

## Purpose

This document defines the safe architecture for a new onboarding wizard that helps a new HobeRadius customer configure:

1. Internet uplink on MikroTik
2. VPN + RADIUS + API bootstrap
3. Optional Hotspot setup
4. Optional Broadband/PPPoE setup
5. Optional added services (site-exit, network policy, etc.)

This slice is architecture-only. No live script execution is introduced here.


## Scope and Non-Goals

### In scope (planned)

- Safe state-machine wizard flow
- Deterministic MikroTik script planning/generation
- Verification contracts between backend and router/VPS checks
- Reuse of existing HobeRadius modules (VPN, network policy, site exit, hotspot patterns)
- Guided diagnostics with Arabic operator guidance

### Out of scope (SW0)

- Applying scripts directly on customer routers
- Replacing existing legacy routes immediately
- Destructive migration of existing provisioning flow
- Flutter implementation


## Existing Foundations in Current Codebase

Current repository already has building blocks we should reuse:

- Existing MT setup/provisioning route flow:
  - `/admin/radius/mt/setup`
  - `/admin/radius/mt/<nas_id>/script`
  - `/admin/radius/mt/operations`
  - File: `app/radius/routes/mt_setup.py`
- Existing provisioning helpers:
  - `app/radius/services/mt_provisioner.py`
  - `app/radius/services/wg_peer_manager.py`
  - `app/radius/services/vpn_probe.py`
- Existing NAS VPN columns migration:
  - `app/radius/db/migrations/033_nas_vpn.sql`
- Existing ecosystem to integrate later:
  - Site exit planning/render/safety services
  - Network policy center planning/apply/readiness services
  - MikroTik diagnostics and admin client services

The new wizard must layer on top of these capabilities, not duplicate them.


## Target Wizard Flow (State Machine)

Wizard flow is modeled as explicit phases:

1. `welcome`
2. `internet_source_select`
3. `internet_source_details`
4. `internet_script_preview`
5. `internet_verification`
6. `vpn_radius_script_preview`
7. `vpn_radius_verification`
8. `hotspot_choice`
9. `hotspot_config`
10. `hotspot_script_preview`
11. `hotspot_verification`
12. `broadband_choice`
13. `broadband_config`
14. `broadband_script_preview`
15. `broadband_verification`
16. `added_services_choice`
17. `added_service_config`
18. `final_summary`

### Transition rules

- A phase cannot be skipped if its predecessor requires verification.
- Verification gates must be hard gates:
  - Cannot move beyond `internet_verification` until internet check is verified.
  - Cannot move beyond `vpn_radius_verification` until VPN + API + RADIUS checks are verified.
- Optional branches (`hotspot_choice`, `broadband_choice`, `added_services_choice`) can be skipped explicitly, and must be recorded as `skipped`.

### Status model

Each step has:

- `pending`
- `generated`
- `applied_by_customer`
- `verified`
- `failed`
- `skipped`


## Planned Data Model (Minimal Additions)

Two new entities are planned:

1. `setup_wizard_runs`
2. `setup_wizard_steps`

### setup_wizard_runs (proposed)

- `id`
- `tenant_id`
- `router_id` (nullable)
- `status`
- `current_step`
- `internet_source_type`
- `selected_wan_interface`
- `generated_vpn_ip`
- `generated_router_vpn_ip`
- `generated_radius_secret_ref` (reference/masked; avoid plaintext if possible)
- `generated_api_username`
- `verification_status_json`
- `last_error`
- `created_at`
- `updated_at`
- `completed_at`

### setup_wizard_steps (proposed)

- `id`
- `wizard_run_id`
- `step_key`
- `status`
- `input_json`
- `generated_script`
- `rollback_script`
- `validation_commands_json`
- `verification_result_json`
- `created_at`
- `updated_at`

### Secret handling model

- Do not store plaintext secrets unless strictly required by existing secure pattern.
- Prefer masked render + reference storage.
- UI displays sensitive values once in script preview context, then only masked values.


## Backend Service Decomposition (Planned)

Business logic must stay in service layer, not route handlers.

- `SetupWizardService`
- `MikroTikScriptPlanner`
- `InternetUplinkScriptBuilder`
- `VpnRadiusBootstrapPlanner`
- `HotspotBootstrapPlanner`
- `BroadbandBootstrapPlanner`
- `AddedServicesPlanner`
- `SetupVerificationService`
- `SetupDiagnosticsService`

### Service principles

- Deterministic script output for same input
- Stateless planners where possible
- Explicit conflict checks
- Structured diagnostics output (machine-readable + Arabic guidance)


## Internet Uplink Planning Contract

Supported source types:

1. VLAN (DHCP/static sub-mode)
2. Static IP
3. Direct DHCP client
4. PPPoE

Each generated script must include:

- HOBERADIUS_SETUP tags/comments
- Safe conditional creation patterns
- No blind delete/remove
- Validation commands:
  - route print (filtered)
  - ping `8.8.8.8`
  - DNS ping/check when DNS configured
- Rollback notes or rollback block where feasible


## VPN/RADIUS/API Bootstrap Contract

Planned outputs:

- VPN config script (using project-supported VPN method)
- RADIUS server bootstrap script block
- API user bootstrap block with limited permissions where possible

Verification contract returns structured statuses:

- `vpn_not_handshaking`
- `wrong_public_endpoint`
- `firewall_blocking_udp`
- `wrong_allowed_address`
- `route_missing`
- `radius_secret_mismatch`
- `radius_server_unreachable`
- `api_login_failed`
- `router_time_or_dns_issue`
- `duplicate_config_conflict`

Each status includes:

- Arabic human-readable explanation
- likely cause
- suggested fix
- optional command/operator check


## Interface Discovery and Candidate Filtering

After VPN/API verification:

- fetch MikroTik interfaces
- detect wizard-selected uplink/WAN interface
- exclude WAN from Hotspot/Broadband candidate interface list
- preserve raw interface names
- optional aliases only for display


## Optional Modules Integration Strategy

For added services, the wizard must integrate with existing modules:

- Site exit
- Network policy center
- Walled garden / block sites
- Anti-sharing/tethering support where implemented

No duplicate logic; wizard acts as orchestration and script planning surface.


## Safety Model

Primary safety guarantees:

- Script generation first, manual execution by customer
- Verification before next critical phase
- No blind destructive commands
- No default-route override without warning + rollback guidance
- No firewall/routing wide delete operations
- No silent overwrite of RADIUS/API critical config
- Full step audit trail in wizard run/steps records


## Rollback Strategy

For every generated script:

- include rollback notes at minimum
- include rollback block when feasible for objects tagged with `HOBERADIUS_SETUP:<run_id>:<step>`
- never rely on deleting unknown legacy objects
- roll back only objects created by the same wizard step/tag


## Verification Strategy

Verification is phase-aware:

- Internet verification:
  - route presence/health checks
  - ping public IP and DNS name checks
- VPN/RADIUS verification:
  - peer handshake/readiness where available
  - VPS tunnel ping checks
  - router-side API auth check
  - RADIUS reachability logical checks
- Hotspot/Broadband verification:
  - generated objects existence checks
  - service/profile/pool consistency checks

Verification results are persisted per step and summarized in run status.


## Risks and Guardrails

Key risks:

- Wrong uplink interface selection can break management path
- Wrong route/NAT sequencing can break internet
- Conflicting existing RADIUS entries can cause auth failures
- Overlapping subnets can break LAN/VPN/hotspot/broadband traffic
- Blind script execution can lock customer out

Guardrails:

- explicit conflict detection
- management-interface warning
- manual apply confirmation
- verification gates
- structured diagnostics before continue


## Planned Delivery Slices

- SW0: Architecture + safety docs (this slice)
- SW1: Wizard run/step state foundation (models + migration + service skeleton)
- SW2: Internet uplink planner + validation + script preview
- SW3: VPN/RADIUS planner + verification contract + diagnostics mapping
- SW4: Interface discovery + hotspot planner
- SW5: Broadband planner
- SW6: Added services integration


## Implementation Notes

- Follow existing radius blueprint registration conventions.
- Keep state transitions explicit; reject invalid transitions.
- Keep route handlers thin, delegate to services.
- Keep behavior backward compatible with current provisioning flows until wizard is feature-complete.

