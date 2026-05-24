# Setup Wizard Added Services Engine

## Purpose

The Added Services Engine lets Setup Wizard V2 present optional network services after the core router connection flow. It is preview-only by default and does not apply router changes.

The engine reuses existing project foundations instead of duplicating network policy logic.

## Service Catalog

| Service | Status | Delegate | Risk |
| --- | --- | --- | --- |
| Anti-sharing / anti-tethering | not_supported_yet | none | high |
| Walled Garden / open sites without login | partial | `npc_walled_garden_planner` | medium |
| Block Sites | partial | `npc_web_block_planner` | medium |
| Change Public IP / Site Exit | partial | `site_exit_script_planner` | high |

## Status Meaning

- `supported`: safe end-to-end planning exists inside Setup Wizard.
- `partial`: a real planner exists, but persistence, production policy lifecycle, or apply flow belongs to the delegated module.
- `not_supported_yet`: no stable planner exists for Setup Wizard; the UI must not pretend it can generate a real script.

In this phase, Walled Garden, Block Sites, and Site Exit are intentionally marked `partial` because they delegate to existing planners and still rely on their owning module for production lifecycle.

## Integration Points

### Walled Garden

Delegates to:

- `app/radius/services/npc_walled_garden_planner.py`
- `app/radius/services/npc_script_renderer.py`

Inputs:

- `domains`
- optional `hotspot_profile`

Verification guidance:

- `/ip hotspot walled-garden print detail`
- `/ip hotspot walled-garden ip print detail`

### Block Sites

Delegates to:

- `app/radius/services/npc_web_block_planner.py`
- `app/radius/services/npc_script_renderer.py`

Inputs:

- `domains`
- optional `category`

Verification guidance:

- `/ip firewall address-list print detail`
- `/ip firewall filter print detail`

### Site Exit / Change Public IP

Delegates to:

- `app/radius/services/site_exit_script_planner.py`
- `app/radius/services/site_exit_script_renderer.py`

Inputs:

- `destinations`
- `wireguard_interface_name`
- optional `wan_interface_list`
- optional `enable_dns_helper`

Verification guidance:

- `/ip route print detail`
- `/ip firewall mangle print detail`
- `/routing table print detail`

## Wizard Tagging

Generated add commands are tagged with:

`HOBERADIUS_SETUP:<run_id>:added-service:<service_key>`

The delegated planner's native ownership tag remains in place so rollback and cleanup continue to follow the original module's safety rules.

## Presets

Presets only preselect services and starter inputs. They never auto-apply.

- ISP Basic: block sites optional + minimal walled garden
- Hotel / Cafe: walled garden + limited block rules
- School: stronger block-site defaults + education allowlist
- Gaming Center: site-exit where VX2 prerequisites exist + limited block rules

## Safety Model

- No live execution is introduced here.
- Apply remains outside this V2 added-services flow.
- Dry-run returns structured operation previews only.
- Unknown services are rejected.
- Unsupported services return a clear `not_supported_yet` response.
- Existing delegated renderers keep their scoped cleanup and rollback contracts.

## Known Limitations

- Anti-sharing is visible as a catalog item but is not supported until a stable planner exists.
- Site Exit remains partial because real production use depends on VX2 policy, exit node, and safety lifecycle.
- Walled Garden and Block Sites do not create persistent NPC policy rows from Setup Wizard in this phase.
- Verification is guidance-only until each delegated service has a safe read-only verification adapter.

## Future Apply Path

The future apply path should route through the existing guarded Setup Wizard operation engine or the owning delegated module's safety layer. It must not bypass:

- dry-run
- explicit confirmation
- rollback preview
- scoped ownership tags
- feature flags
- verification
