# MikroTik Script Safety Rules for Setup Wizard (SW0)

## Why this file exists

Wizard-generated scripts can affect uplink, routing, NAT, VPN, RADIUS, and API access.
Any unsafe command can break customer connectivity or lock out management.

These rules are mandatory for all wizard script builders.


## Safety Principles

1. Generate first, apply manually by customer/admin.
2. Prefer additive/idempotent operations.
3. Never delete unknown existing production config.
4. Scope all generated objects with `HOBERADIUS_SETUP:<run_id>:<step>`.
5. Include verification commands and rollback notes in each script.
6. Warn clearly before touching routes, NAT, or firewall behavior.


## Forbidden Command Patterns

The wizard must never emit blind destructive commands such as:

- `/ip route remove [find]`
- `/ip firewall filter remove [find]`
- `/ip firewall nat remove [find]`
- `/interface disable [find]`
- `/interface remove [find]`
- `/user remove [find]`
- `/radius remove [find]`
- any global `remove [find]` or `set [find]` without strict selector/tag guard

Also forbidden:

- overriding default route blindly
- deleting unknown pools/profiles/servers
- broad route/firewall resets


## Allowed Safe Patterns

Allowed patterns are explicit and scoped:

- Add only if object does not already exist by exact name/comment/tag.
- Update only object created by wizard tag or explicit operator-selected target.
- Use comments/tags for ownership tracking.
- Use exact matching selectors, never global `[find]` without constraints.

Examples (conceptual):

- Check if named object exists, else add it.
- Add route/profile/pool with explicit unique name and tag.
- Add NAT rule with exact source subnet and comment tag.


## Idempotency Rules

Every generated script must be re-runnable safely:

- Re-running should not create duplicate objects.
- Re-running should not widen access unexpectedly.
- Re-running should keep same naming/tagging references.
- Re-running should avoid reordering critical firewall chains unless explicitly needed.


## Conflict Detection Rules

Before rendering script:

- detect subnet overlap with WAN/LAN/VPN/hotspot/broadband pools
- detect conflicting existing names (pool/profile/server/bridge/interface alias)
- detect route conflicts (duplicate default route behavior)
- detect potential management-path impact
- detect existing RADIUS/API users/servers with different credentials

On conflict:

- return structured warning/diagnostic
- require explicit user acknowledgement where needed
- do not produce unsafe destructive merge script


## Default Route Safety Rules

If script can affect default route:

- show a clear warning in wizard UI
- include route validation commands
- include rollback route command guidance
- avoid removing existing default routes automatically
- prefer adding route with explicit distance/metric rules when safe


## Firewall/NAT Safety Rules

- Do not reorder entire chains globally.
- Do not delete existing rules.
- Add only tagged rules.
- Keep new rules as narrow as possible:
  - explicit source/destination subnet/interface
  - explicit protocol/ports when needed
- Document where inserted and why.


## Interface Safety Rules

- Never disable interfaces automatically.
- Never rename interfaces automatically.
- Never bridge WAN interface into access bridge by default.
- Warn if selected interface is likely management/uplink interface.


## RADIUS/API Safety Rules

- Do not remove existing RADIUS servers blindly.
- Do not overwrite existing API admin users blindly.
- Prefer dedicated wizard-created API user with limited required permissions.
- Never persist plaintext passwords in logs/audit events.
- Mask secrets in UI after first render where possible.


## Script Output Structure (Required)

Every script must contain:

1. Header:
   - run id
   - step key
   - generation timestamp
   - safety warning
2. Planned changes list
3. Main commands (tagged and scoped)
4. Validation commands block
5. Rollback notes or rollback block
6. Operator reminder to verify before next wizard step


## Validation Commands (Minimum)

Include relevant checks per step. For uplink/VPN steps at minimum:

- route inspection command(s)
- IP reachability ping(s)
- DNS reachability check where DNS is configured
- object existence checks for created resources


## Rollback Rules

- Roll back only wizard-tagged objects from the same run/step.
- Never roll back unknown legacy objects.
- If deterministic rollback script is not safe, emit manual rollback guidance only.
- Rollback guidance must be explicit and scoped by object names/tags.


## Diagnostics Contract (UI-facing)

Verification failures must map to stable diagnostic codes and include:

- Arabic explanation
- likely cause
- suggested fix
- optional inspection command

No raw ambiguous error-only output.


## Logging and Audit Requirements

- Record script generation event with run id and step key.
- Record verification attempts and outcomes.
- Record conflict warnings and user acknowledgements for risky steps.
- Never log plaintext secrets.


## Reviewer Checklist (Pre-Merge)

Before merging any wizard script builder:

- [ ] no forbidden blind delete/remove commands
- [ ] no uncontrolled default-route overwrite
- [ ] all objects scoped/tagged
- [ ] validation commands present
- [ ] rollback guidance present
- [ ] idempotent re-run behavior validated by tests
- [ ] conflicts detected and surfaced
- [ ] secrets masked in logs/UI

