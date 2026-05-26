# Setup Wizard — Current State Map (SW0 — addendum)

> **Status:** Slice SW0 addendum, no code change.
> **Companion docs:**
> - [`SETUP_WIZARD_ARCHITECTURE.md`](SETUP_WIZARD_ARCHITECTURE.md) — full target architecture
> - [`MIKROTIK_SCRIPT_SAFETY_RULES.md`](MIKROTIK_SCRIPT_SAFETY_RULES.md) — script generation safety contract
>
> **Audience:** anyone picking up SW1-SW6 work. Read the two companion docs first; this one tells you *what is already implemented* vs *what is still planning*.

---

## Why this addendum

The two companion documents were written as **planning** documents at slice SW0 start. Since then, the team has shipped real v3 code through commits `af172cd → 7d7771f`. This document tracks the *current implementation reality* so the next slice (SW1) doesn't either:

1. Duplicate code that already exists, or
2. Assume code exists when only the design did.

Every section below has the form: **"Brief calls for X. Today we have Y. Gap is Z."**

This addendum is the ground truth for the next implementer. Refresh it at the end of every slice.

---

## 1. Wizard surfaces — what exists today

Three wizard URLs coexist in the codebase right now:

| URL | What it is | Status |
|---|---|---|
| `/admin/radius/mt/setup` | Legacy MikroTik onboarding form. Generates a single setup script. | Untouched by SW work. Used historically. Should remain available until SW6 completes. |
| `/admin/radius/setup-wizard-v2` | "Engineering view" of v2 — 13-step paste-back flow. | Still functional; carries field-tested logic for internet uplink, hotspot, broadband. **Reference source** for SW2/SW4/SW5 planners. |
| `/admin/radius/setup-wizard-v3` | Single-page state machine shipped at commit `7d7771f`. Covers WireGuard bootstrap, paste-mode public key submission, peer-file write, NAS registration. | The **primary** wizard going forward. SW1-SW6 extend this. |

The brief's target wizard is v3. v2 is treated as a reference; the legacy `/mt/setup` form is treated as untouchable until v3 covers its surface area completely.

---

## 2. Phase coverage map

This is the same 17-phase flow from the brief. For each, what's actually implemented:

| # | Phase | Brief calls for | What v3 has today | What v2 has | Gap |
|---|---|---|---|---|---|
| 1 | welcome | One-line welcome page | ✅ Hero card | ✅ | None |
| 2 | internet_source_select | VLAN / Static / DHCP / PPPoE picker | ❌ | ✅ in v2 form | **Wire v2 form into v3** in SW2 |
| 3 | internet_source_details | Dynamic form per choice | ❌ | ✅ in v2 | **Wire into v3** in SW2 |
| 4 | internet_script_preview | Generated `.rsc` with ping check | ⚠️ partial — v3 only generates the WG portion | ✅ v2 has a builder for all 4 source types in `setup_wizard.py` (`InternetSourceScriptBuilder` is the concept; the actual function in v2 emits an `.rsc` body with ping checks) | **Extract** to a clean `InternetUplinkScriptBuilder` module in SW2 |
| 5 | internet_verification | Paste-back ping output | ❌ | ✅ in v2 paste-back service | **Migrate** to v3 in SW2 |
| 6 | vpn_radius_script_preview | WG + RADIUS + API | ✅ `_render_unified_script()` covers WG; **RADIUS + API user are NOT included** | ✅ v2 has `VpnRadiusBootstrapPlanner` covering all three | **Extend** v3's renderer with the RADIUS+API portions from v2's planner in SW3 |
| 7 | vpn_radius_verification | Tunnel + ping + RADIUS + API | ⚠️ v3 verifies handshake only; doesn't probe RADIUS reachability or API login | ✅ v2 has the full verification | **Consolidate** in SW3 |
| 8 | hotspot_choice | Skip / configure decision | ❌ | ✅ | New state in SW4 |
| 9 | hotspot_config | Manual or smart mode form | ❌ | ⚠️ partial in v2 | New in SW4 |
| 10 | hotspot_script_preview | Generated bridge + IP + pool + profile + server + RADIUS + NAT | ❌ | ⚠️ partial | New `HotspotBootstrapPlanner` in SW4 |
| 11 | hotspot_verification | Server up, profile valid | ❌ | ⚠️ partial | New in SW4 |
| 12 | broadband_choice | Skip / configure decision | ❌ | ⚠️ | New state in SW5 |
| 13 | broadband_config | Manual or smart | ❌ | ⚠️ partial | New in SW5 |
| 14 | broadband_script_preview | PPPoE server + profile + pool + RADIUS + NAT masquerade | ❌ | ⚠️ partial | New `BroadbandBootstrapPlanner` in SW5 |
| 15 | broadband_verification | Server + NAT check | ❌ | ⚠️ partial | New in SW5 |
| 16 | added_services_choice | Anti-sharing / site-exit / walled-garden / block-sites | ❌ | ❌ (manual; operator visits each module separately) | **Integration orchestrator** in SW6 — does NOT reimplement these services, just hands off cleanly |
| 17 | final_summary | Done card + deep-link to ops room | ✅ COMPLETE state with stat grid | ✅ | None |

**Phases delivered today: 1, 6 (partial), 7 (partial), 17.**
**Phases pending: 2, 3, 4, 5, 8-16.**

---

## 3. Schema coverage map

Brief requires `setup_wizard_runs` and `setup_wizard_steps` tables. Today:

| Brief column | Where it lives today | Gap |
|---|---|---|
| `setup_wizard_runs.id` | ✅ existing (pre-v3) | None |
| `.tenant_id` | ✅ | None |
| `.status` | ✅ | None |
| `.current_step` | ✅ but is a v2-era enum; v3 uses `v3_state` | **Reconcile** in SW1 — pick one or document both |
| `.internet_source_type` | ❌ | **Add** in SW1 migration |
| `.selected_wan_interface` | ❌ | **Add** in SW1 |
| `.generated_vpn_ip` | ⚠️ partially in `state_json` blob | **Promote** to a real column in SW1 |
| `.generated_router_vpn_ip` | ⚠️ in `state_json` | **Promote** in SW1 |
| `.generated_radius_secret_ref` | ❌ | **Add** in SW1 |
| `.generated_api_username` | ❌ | **Add** in SW1 |
| `.verification_status_json` | ⚠️ partially via `v3_diagnostics_json` | **Rename / consolidate** in SW1 |
| `.last_error` | ❌ | **Add** in SW1 |
| `.created_at` / `.updated_at` / `.completed_at` | ✅ (`.v3_completed_at` exists) | None |
| `setup_wizard_steps` (whole table) | ❌ does not exist | **Create** in SW1 migration 076 |

**Migration 075 (already in place)** added the v3-specific columns: `v3_state`, `v3_diagnostics_json`, `api_mode`, `nas_device_id`, `ops_room_router_id`, `unified_script_short_code`, `unified_script_sha256`, `handshake_first_seen_at`, `handshake_last_seen_at`, `v3_completed_at`, plus the `setup_wizard_v3_unified_scripts` and `setup_wizard_v3_probe_attempts` tables.

**Migration 076 (proposed for SW1)** should:

1. Add the missing per-phase columns to `setup_wizard_runs` (the brief's list above).
2. Create `setup_wizard_steps` table with the schema in `SETUP_WIZARD_ARCHITECTURE.md` section "Planned Data Model".
3. Backfill existing v3 rows: copy applicable values out of `state_json` into the new typed columns.

---

## 4. Service layer coverage map

Brief lists nine services. Today:

| Service | Module | Status |
|---|---|---|
| `SetupWizardService` | `setup_wizard.py` (v2) + `setup_wizard_v3.WizardV3Service` | **Two implementations.** SW1 should pick one — v3's is cleaner. v2's stays as a reference; future slices port v2's methods into v3. |
| `MikroTikScriptPlanner` | Not factored; logic scattered across builders | **Create** an abstract `PhasePlanner` protocol in SW1 |
| `InternetUplinkScriptBuilder` | Logic exists in v2's `setup_wizard.py` but not as a separate module | **Extract** in SW2 |
| `VpnRadiusBootstrapPlanner` | `setup_wizard_vpn_radius_planner.py` (v2) — solid | **Keep**, refactor to v3's `PhasePlanner` interface in SW3 |
| `HotspotBootstrapPlanner` | Concept exists in v2 inline; no clean module | **New module** in SW4 |
| `BroadbandBootstrapPlanner` | Concept in v2 inline | **New module** in SW5 |
| `AddedServicesPlanner` | Does NOT exist. Existing NPC/site-exit/walled-garden services do exist as **separate** features. | **Orchestrator only** in SW6 — wraps existing services, doesn't reimplement |
| `SetupVerificationService` | v2 has paste-back parsers per phase; v3 has `mark_handshake_observed()` | **Consolidate** in SW1; one verification service that takes (phase, pasted_text) → diagnostic |
| `SetupDiagnosticsService` | Diagnostic codes live inline in v3 service | **Extract** to a catalogue module in SW1 (parallel session's `WIZARD_DIAGNOSTICS.md` already enumerates them) |

---

## 5. Safety rules — current compliance

The 18 rules in `MIKROTIK_SCRIPT_SAFETY_RULES.md` are MANDATORY. Today's compliance:

| Rule | v3 today | Notes |
|---|---|---|
| Generate first, apply manually | ✅ | v3 is paste-only; no live execution. |
| Idempotent / additive | ✅ | Cleanup block scoped to `comment~"HOBERADIUS_SETUP"` runs at the top of v3's bootstrap script. |
| Never delete unknown config | ✅ | All `remove` calls are filtered by our comment prefix. |
| Scope objects with `HOBERADIUS_SETUP:<run_id>:<step>` | ✅ | Every WG interface, address, route, and peer carries this tag. |
| Verification + rollback notes in each script | ⚠️ | v3's script has validation block. Rollback script is NOT yet a separate artifact — implicit in the cleanup block. SW1 should split them. |
| Warn before touching routes/NAT/firewall | ❌ today | v3 doesn't touch them (only WG). When SW2 (internet uplink) ships, this rule becomes load-bearing. |
| Forbidden patterns absent | ✅ | Verified by reading the rendered script — no blind `[find]` removal. |
| RouterOS version detection | ❌ | Not implemented. v3 assumes 7.x. SW3 should add a `:put [:version]` line to the validation block and let the planner refuse if the operator pastes back a 6.x version we haven't tested. |
| Conflict detection | ⚠️ | v3 detects "peer with same key on different router" via DB unique constraint, but does NOT read the router's existing state before generating. SW2 should add a pre-generation paste-back of `/ip/address/print` to detect subnet overlap. |
| Default route safety | ❌ today | Not relevant for v3's WG-only scope. **Critical** for SW2's PPPoE/DHCP cases. |

**Conclusion**: v3 satisfies the safety rules within its scope. The risky rules (default-route, conflict-detection) bite during SW2 — which is why SW2 must be approached carefully.

---

## 6. Verification strategy — current implementation

Brief calls for paste-back and API-pull modes. Today:

| Mode | v3 status |
|---|---|
| Paste-back (default) | ✅ v3 accepts pasted `/interface wireguard print detail` output, extracts the public key via regex. |
| API-pull (opt-in fallback) | ✅ v3 can call MikroTik API to read `/interface/wireguard` directly if router credentials are provided (the legacy "API mode" tab in the auto-finalize card). |

The two modes are **operator-selectable** in the v3 UI. The brief calls for API-pull to be auto-detected after the tunnel is up; today it's manually toggled. Auto-detection is a SW3 follow-up.

---

## 7. Diagnostics catalogue — current state

Parallel session shipped `WIZARD_DIAGNOSTICS.md` with 35+ codes. v3 today implements **eleven** of them inline in `setup_wizard_v3._STATE_AR` and the diagnostic blobs emitted in the BLOCKED state:

* `PUBLIC_KEY_NOT_FOUND`
* `MISSING_PEER_INPUT`
* `PEERS_DIR_UNWRITABLE`
* `PEER_FILE_WRITE_FAILED`
* `MISSING_REGISTRATION_INPUT`
* `NAS_INSERT_FAILED`

Other codes from the catalogue (`vpn_not_handshaking`, `wrong_public_endpoint`, `firewall_blocking_udp`, etc.) are NOT yet wired into v3. SW1 should consolidate the catalogue into a single `setup_wizard_diagnostics.py` module that v3 imports.

---

## 8. Risks not yet mitigated

Items in `SETUP_WIZARD_ARCHITECTURE.md § Risks and Guardrails` that v3 does **not** yet address:

| Risk | Mitigation status |
|---|---|
| Plaintext RADIUS secret in `setup_wizard_steps.generated_script` | ❌ table doesn't exist yet; secret lives in `state_json` blob. SW1 must decide on encryption-at-rest before introducing the steps table. |
| Default route hijack | ❌ Not yet relevant (no internet phase). SW2 must implement the paste-back of `/ip/route/print` before generating. |
| Operator pastes script on wrong router | ⚠️ v3's script includes `HOBERADIUS_RUN:<id>` in `:put` line. The paste-back validator checks for this — but only loosely. SW3 should harden. |
| Customer leaves mid-run | ✅ v3 persists state; operator can resume. |

---

## 9. Out-of-scope guardrails — adherence check

The brief enumerates what the wizard MUST NOT do. Today's v3 vs the list:

| Guardrail | v3 today |
|---|---|
| Don't touch `radius-module-admin` | ✅ |
| Don't touch Flutter | ✅ |
| Don't break existing RADIUS auth/accounting/VPN | ✅ — all v3 endpoints are new URLs; no existing endpoint modified |
| Don't modify live dangerous scripts without dry-run | ✅ — no live execution at all |
| Don't auto-apply MikroTik scripts | ✅ — paste-only |
| No git add . | ✅ — every commit in the v3 series uses explicit file staging |
| Tests pass before commit | ✅ — verified at each commit (130/130 NPC, 14/14 v2, 7/7 vpn-planner) |

**Compliance: clean.**

---

## 10. Recommended SW1 entry point

When picking up SW1, the next implementer should:

1. **Read** `SETUP_WIZARD_ARCHITECTURE.md` end-to-end (15 min).
2. **Read** `MIKROTIK_SCRIPT_SAFETY_RULES.md` end-to-end (10 min).
3. **Read** this document (10 min).
4. **Create migration 076** that adds the missing `setup_wizard_runs` columns and the new `setup_wizard_steps` table per § 3 above.
5. **Backfill** existing v3 runs by extracting fields from `state_json` into the new typed columns.
6. **Add** `setup_wizard_diagnostics.py` consolidating the diagnostic codes from `WIZARD_DIAGNOSTICS.md` + the eleven v3 inline ones.
7. **Add** an abstract `PhasePlanner` protocol that all future planners implement.
8. **Tests** for the migration's backfill + the diagnostic catalogue.
9. **Commit** as `Add setup wizard state foundation`.

SW1 should NOT introduce any new UI or any new endpoint. It's a data-model + service-skeleton slice.

---

## 11. Tracking — to be updated by each slice

| Slice | Status | Commit |
|---|---|---|
| SW0 — architecture | ✅ done (this doc + companions) | `af172cd` (companions) + (this commit) |
| SW1 — data model + service skeleton | ⏳ pending | |
| SW2 — internet uplink planner | ⏳ pending | |
| SW3 — VPN/RADIUS planner cleanup | ⏳ pending | |
| SW4 — interface discovery + hotspot | ⏳ pending | |
| SW5 — broadband | ⏳ pending | |
| SW6 — added services integration | ⏳ pending | |

When each slice ships, the implementer:

1. Updates this table.
2. Adds a brief "What this slice changed in current state" section at the bottom of this doc.
3. Re-validates the safety-rules compliance table in § 5.

---

End of current-state map.
