## Prompt 1 — Lab-only WireGuard server apply adapter

### Commit

423e5ea

### What implemented

- Confirmed the lab-only real server WireGuard peer apply adapter is present at the current HEAD and did not rebuild existing architecture.
- Added the production-safe `SubprocessSafeCommandRunner` path guarded by all required lab/server flags.
- Kept the default blocked adapter behavior unless all flags are explicitly enabled.
- Added real server-side apply and rollback command construction for WireGuard peers only.
- Added backup capture before apply using `wg show` and `wg showconf <interface>`.
- Added private-key masking for captured WireGuard config snapshots.
- Extended server peer apply verification states:
  - `applied_no_handshake`
  - `verified_handshake`
  - `failed_verification`
  - `missing_peer`
  - `allowed_ip_mismatch`
- Extended the V2 server peer preparation section with lab-only adapter status, confirmation input, apply/rollback controls, and stronger lab warning.
- Added real-adapter safety and behavior tests.
- Added documentation for the lab-only server WireGuard real adapter.

### Files changed

- `app/radius/services/setup_wizard_server_wg.py`
- `app/radius/services/setup_wizard_server_wg_readiness.py`
- `app/static/css/setup_wizard_v2.css`
- `app/static/js/setup_wizard_v2.js`
- `app/templates/radius/setup_wizard_v2.html`
- `docs/setup_wizard/SERVER_WG_REAL_ADAPTER.md`
- `tests/test_server_wireguard_peer_apply.py`
- `tests/test_server_wireguard_readiness.py`
- `tests/test_server_wireguard_real_adapter.py`
- `docs/setup_wizard/EXECUTION_LOG.md`

### Tests exact results

- `python -m compileall app` passed.
- `node --check app/static/js/setup_wizard_v2.js` passed.
- `python -m pytest tests/test_server_wireguard_real_adapter.py -q` passed: 16 passed, 2728 warnings in 13.47s.
- `python -m pytest tests/test_server_wireguard_peer_apply.py -q` passed: 12 passed, 2464 warnings in 12.46s.
- Setup wizard related suite passed:
  - Command:
    `python -m pytest tests/test_setup_wizard_foundation.py tests/test_setup_wizard_internet_planner.py tests/test_setup_wizard_vpn_radius_planner.py tests/test_setup_wizard_hotspot_planner.py tests/test_setup_wizard_broadband_planner.py tests/test_setup_wizard_routes.py tests/test_setup_wizard_verification_engine.py tests/test_setup_wizard_operational_waves.py tests/test_setup_wizard_pilot_drill.py tests/test_setup_wizard_lab_mode.py tests/test_setup_wizard_router_provisioning.py tests/test_setup_wizard_v2.py tests/test_router_lifecycle.py tests/test_router_provisioning_orchestrator.py tests/test_server_wireguard_peer_apply.py tests/test_server_wireguard_readiness.py tests/test_server_wireguard_real_adapter.py -q`
  - Result: 159 passed, 11955 warnings in 77.48s.
- `python -m pytest -q` was attempted and timed out after about 304 seconds. No specific failing test output was returned before timeout.
- `git diff --check` passed before the implementation commit; line-ending warnings were present for dirty unrelated files and touched files.

### Safety confirmations

- Production server apply remains blocked by default.
- Real server WireGuard mutation requires all of these flags:
  - `HOBERADIUS_SETUP_WIZARD_LAB_MODE=true`
  - `HOBERADIUS_SETUP_WIZARD_SERVER_WG_APPLY=true`
  - `HOBERADIUS_SETUP_WIZARD_SERVER_WG_READINESS=true`
  - `HOBERADIUS_SETUP_WIZARD_SERVER_WG_REAL_ADAPTER=true`
- No MikroTik live apply was added.
- No customer production automation was enabled.
- No `radius-module-admin` files were touched.
- No Flutter files were touched.
- Existing RADIUS auth/accounting behavior was not changed.
- No `shell=True` path was introduced.
- Allowed read-only server commands are limited to:
  - `wg show`
  - `wg showconf <interface>`
  - `ip addr show`
  - `ip route show`
- Allowed server apply command is limited to:
  - `wg set <interface> peer <public_key> allowed-ips <router_ip>/32`
- Allowed server rollback command is limited to:
  - `wg set <interface> peer <public_key> remove`
- Dangerous commands such as restarts, `wg-quick down/up`, broad config edits, route/firewall flushes, and broad deletions remain blocked.

### Remaining risks

- The real adapter is code-ready for a controlled VPS/CHR lab, but it has not been executed against a real VPS in this prompt.
- A real lab run still requires verifying the server interface name, WireGuard permissions, command timeout, backup capture, and out-of-band VPS access before enabling flags.
- Handshake verification can only become `verified_handshake` after the router side is connected; initial server peer application may correctly remain `applied_no_handshake`.
- Full project pytest still does not complete within the 304 second execution window in this environment.

### Full honest notes

- The repository HEAD at the start of this prompt was already `e22832a Add lab-only WireGuard server apply adapter`, not the stale prompt baseline `4e9697c`.
- Because the requested implementation was already present, this prompt did not rebuild or duplicate the architecture. It verified the existing implementation, reran the required checks, and added this execution log.
- Pre-existing unrelated dirty files remain intentionally excluded from staging and commits:
  - `app/radius/routes/print_templates.py`
  - `app/radius/seed.py`
  - `app/radius/services/operations.py`
  - `app/static/css/cards_batches_view.css`
  - `app/static/js/cards_batches_view.js`
  - `app/templates/radius/cards_batches.html`
  - `app/templates/radius/devices_list.html`
  - `app/templates/radius/mt_alerts_index.html`
  - `app/templates/radius/network_policy_list.html`
  - `app/templates/radius/print_templates.html`
  - `tests/test_card_renderer.py`
  - `tests/test_operations_foundation.py`
  - `tests/test_web_print_templates_ui.py`
  - `app/templates/radius/_npc_components.html`
