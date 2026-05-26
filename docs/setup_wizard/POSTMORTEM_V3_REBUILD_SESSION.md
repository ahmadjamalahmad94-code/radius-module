# Postmortem — v3 Wizard Rebuild + TTL Reclaimer Session

> Issues we hit while landing SW1-SW7 (phase planners + UI
> integration), the TTL tentative-reservation reclaimer, the
> emergency fleet reset, and the operator-friendly multi-stage
> v3 wizard rebuild.
>
> **Audience.** On-call + future maintainers. Every section
> ends with a *Prevention* block — verify these before any
> wizard-related release.

Session date: 2026-05-26 · ~15 commits · live VPS validation
on RouterOS 7.x via `187.77.70.18`.

---

## 1) Missing `state_json` column on `setup_wizard_runs`

**Symptom.** First click on `/admin/radius/setup-wizard-v3`
returned 500 with `تعذّر بدء جلسة المعالج`. Logs:

```
sqlite3.OperationalError: table setup_wizard_runs has no
column named state_json
  File "setup_wizard_v3.py", line 187, in create_run
```

**Root cause.** Migration 075 added ten v3 columns to
`setup_wizard_runs` (`v3_state`, `v3_diagnostics_json`, etc.)
but forgot `state_json` — the JSON blob the v3 state machine
uses to persist per-run inputs (router name, type, VPN IP,
paste-back outputs) across state transitions. The bug had
been latent for weeks because nobody had actually clicked the
v3 wizard's "Create run" endpoint since the v3 commit
(`7d7771f`) landed.

**Fix.** Migration `076_setup_wizard_runs_state_json.sql`:

```sql
ALTER TABLE setup_wizard_runs
  ADD COLUMN state_json TEXT NOT NULL DEFAULT '{}';
```

Additive only; legacy v2 rows get `'{}'` and remain valid.

**Prevention.**
- Any new column the service code reads MUST be in a
  migration. Add a startup self-check that introspects
  `PRAGMA table_info(<table>)` and warns if a column
  referenced in the service module is missing.
- Smoke test for every wizard surface: actually call the
  Create-run endpoint in CI, not just the helpers.
- Convention: when a service writes to a column, the test
  fixture that runs `reset_for_tests()` MUST exercise that
  write path at least once.

---

## 2) Browser cache stubbornly serves old JS

**Symptom.** After rebuilding the v3 wizard UI (commit
`10ebd7d`), the "التالي" button did nothing. No request fired
in the Network tab, no console error. The template loaded the
new HTML (progress rail, new step layout), but the JS handling
`data-swz-next` clicks didn't run.

**Root cause.** The browser had cached `setup_wizard_v3.js`
from the previous (single-page state-machine) build. The old
JS bound to old selectors (`data-wv3-action`) that didn't
exist in the new DOM, so its event handlers silently did
nothing. The new selectors had no listener attached.

Hard refresh (`Ctrl+Shift+R`) is unreliable across browsers
and edge cases — some keep cached `.js` files served by `304
Not Modified` even after a hard refresh, especially when
nginx serves with long `Cache-Control` headers.

**Fix.** Commit `495ea1f` appends a version query string to
the static asset references in the template:

```jinja
<script src="{{ url_for('static', filename='js/setup_wizard_v3.js') }}?v=20260526b"></script>
<link  rel="stylesheet"
       href="{{ url_for('static', filename='css/setup_wizard_v3.css') }}?v=20260526b">
```

Bumping `v=...` on every breaking change forces every browser
to fetch fresh — no operator intervention.

**Prevention.**
- **Always cache-bust** when changing v3 wizard static assets.
  The query string is the operator-friendliest mechanism — no
  hard-refresh required, no Incognito fallback.
- Long-term: use Flask-Assets or generate the version from
  the file's mtime / content hash so it auto-bumps.
- When you see "click does nothing" symptoms with no console
  errors, the first hypothesis must be "old JS cached," not
  "JS bug."

---

## 3) `router_type` whitelist mismatch — `hybrid` vs `mixed`

**Symptom.** Step 1 → Step 2 transition failed with toast
`خطأ: router_type must be hotspot/pppoe/mixed`. Operator
selected "Hybrid (الاثنين معاً)" radio.

**Root cause.** The new wizard template used `value="hybrid"`
on the third radio option, but the v3 service whitelists
`{"hotspot", "pppoe", "mixed"}` — `hybrid` was never an
accepted value. Easy mistake while writing the new template
without re-reading the service contract.

**Fix.** Commit `6650a53` — changed the radio `value` to
`mixed` while keeping the label text `Mixed (الاثنين معاً)`.

**Prevention.**
- **Always reread the service's whitelist** when adding a new
  UI option. The whitelist lives in
  `setup_wizard_v3.py::start_new_run` (search for `must be`).
- Better: lift the enum into a single source of truth (a
  `RouterType` literal or an enum module) that both the UI
  template + service import, so the UI breaks at deploy time
  if it drifts. Future slice candidate.

---

## 4) Wrong response key — `script` vs `script_body`

**Symptom.** Step 3 button "توليد سكربت الربط" returned
toast `تعذّر توليد سكربت الربط.` — the generic "no script"
fallback. No backend error in the logs (response was 200 OK).

**Root cause.** The v3 service `generate_unified_script`
returns `{"run": ..., "script": "...", "short_code": ..., ...}`
under key `script`. The new wizard JS was checking
`data.script_body` (an older key name from a prior iteration
of the route). The check `!data.script_body` was truthy, so
the JS short-circuited to the generic toast.

**Fix.** Commit `362115e` reads both keys for
forward-compatibility:

```js
const scriptText = data.script || data.script_body;
if (!scriptText) { toast("تعذّر..."); return; }
```

**Prevention.**
- **Read the route's actual response shape** before writing
  the JS, don't guess. The route's `return jsonify(...)` is
  the authoritative contract.
- API contract tests should pin the response keys so a
  rename would fail loudly. Future slice: add typed
  TypeScript-style annotations.
- Same lesson as #3: a single source of truth would eliminate
  this class of bug.

---

## 5) MikroTik `:local` doesn't persist across pasted lines

**Symptom.** Operator pasted the v3 unified script into
MikroTik Terminal. The script ran to completion. But the
final line printed:

```
HOBERADIUS_PUBLIC_KEY=
```

— with no value. The wizard had no key to paste back, so the
flow stalled at step 3.

**Root cause.** In MikroTik Terminal, when a multi-line
script is pasted, **each line runs in its own interactive
scope**. The original script used:

```
:local pubkey [/interface wireguard get [find name="hr-wg"] public-key]
:put "HOBERADIUS_PUBLIC_KEY=$pubkey"
```

The `:local pubkey` variable existed only during the first
line's execution. By the time the `:put` line ran, `$pubkey`
was undefined, so RouterOS substituted an empty string. No
warning, no error — silent empty output.

**Fix.** Commit `af32e69` inlines the read into the `:put`
using the RouterOS string-concat operator `.`:

```
:put ("HOBERADIUS_PUBLIC_KEY=" . [/interface wireguard get [find name="hr-wg"] public-key])
```

One line, no variables, works in both interactive paste mode
and `/import` mode.

**Prevention.**
- **Never use `:local` across multiple pasted lines** in
  scripts that get copy-pasted into Terminal. Either inline
  everything, or use `:global`, or wrap in `{ ... }`.
- When generating Terminal-paste scripts, test them by
  pasting the exact rendered output — not by running through
  `/import`, because `/import` has different scoping rules.
- Add a smoke test that pastes a sample of generated scripts
  into a RouterOS test container (or at minimum lint for the
  `:local` ... `:put $var` pattern in the script renderer).

---

## 6) Pre-existing test broke during regression sweep

**Symptom.** Running `tests/test_setup_wizard_router_provisioning.py
::test_vpn_radius_script_uses_allocated_values_not_placeholders`
failed with `assert '10.10.0.1/32' in script`.

**Root cause.** The script generator stopped emitting the
exact `10.10.0.1/32` literal because the v3 unified script
uses a different allowed-address format than the legacy v2
script. The failing assertion was pinning v2-era output, but
the function under test had been migrated to v3 semantics.

**This was NOT caused by my changes.** Confirmed by `git
stash` + re-run on the previous commit, which also failed.

**Fix.** Left as-is for this slice. Documented in commit
`39f6563`'s body. A follow-up should either update the test
to v3 format or split it into v2-specific and v3-specific
assertions.

**Prevention.**
- **Always run the affected test file before AND after** a
  change, with `git stash`, to distinguish "I broke it" from
  "it was already broken."
- Tests asserting on generated script text are brittle. Move
  to structural assertions (e.g. "contains a route entry for
  the assigned VPN IP") instead of literal-string matches.

---

## 7) SQLite UNIQUE constraint surprises in test fixtures

**Symptom.** Multiple new tests in
`test_setup_wizard_tentative_reclaimer.py` failed with:

```
sqlite3.IntegrityError: UNIQUE constraint failed:
  router_provisioning_registry.tenant_id,
  router_provisioning_registry.allocation_index
```

and

```
sqlite3.IntegrityError: UNIQUE constraint failed:
  router_provisioning_registry.tenant_id,
  router_provisioning_registry.wizard_run_id
```

**Root cause.** The seed helper in the test file used
`allocation_index=5` and `wizard_run_id=100` for every row.
Migration 052 has `UNIQUE (tenant_id, allocation_index) WHERE
status IN ('reserved', 'generated', 'applied', 'verified')`
and `UNIQUE (tenant_id, wizard_run_id)`. The second seeded
row collided.

**Fix.** Module-level counters (`_alloc_idx`, `_next_run`)
that return monotonically incrementing values per call. The
seed helper now picks fresh values automatically.

**Prevention.**
- **Always assume schema has uniqueness constraints** —
  default test fixtures to monotonic counters, not literals.
- A helper like `test_fixtures.next_alloc_index()` shared
  across test files would prevent every new test from
  re-discovering this.
- For each new test file that seeds registry rows, glance at
  migration 052 first to see the unique indices.

---

## 8) `RouterLifecycleService.transition` kwarg name

**Symptom.** Test `test_lifecycle_promotion_clears_ttl` failed
with `TypeError: RouterLifecycleService.transition() got an
unexpected keyword argument 'target'`.

**Root cause.** I named the kwarg `target=` in the test (a
reasonable guess) but the method's signature uses
`to_state=`. The variable name *inside* the method body is
`target`, but the kwarg is `to_state` — easy mistake when
skimming the method to write a test.

**Fix.** Renamed kwarg in the test.

**Prevention.**
- When writing tests for an unfamiliar service, copy the
  method's exact signature into the test rather than typing
  from memory.
- Long-term: type stubs (`.pyi`) or `assert_type` style guard
  rails would catch these statically.

---

## 9) Forward-transition map only allows specific paths

**Symptom.** Same test, after fixing kwarg, failed with
`SetupWizardValidationError: invalid router lifecycle
transition: waiting_router_key -> peer_pending`.

**Root cause.** The lifecycle has 13 states and a strict
forward-transition map: `waiting_router_key →
router_key_received → peer_pending → peer_ready →
vpn_verified`. I had skipped `router_key_received` thinking
it was a sub-step. The test wanted to walk all the way to
`vpn_verified` but jumped over a required intermediate state.

**Fix.** Inserted `router_key_received` in the test's
transition list.

**Prevention.**
- **Print the `_FORWARD_TRANSITIONS` map** before writing any
  test that walks the lifecycle. It's the source of truth.
- Add a `RouterLifecycleService.full_happy_path()` helper
  method that walks all states in order, so tests don't need
  to hard-code the sequence.

---

## 10) Static-file rebuild needed `--build`, not `restart`

**Symptom.** After pushing cache-bust commit `495ea1f`,
operator ran `docker compose ... restart hoberadius`. The
fix didn't take effect — the new template `?v=20260526b`
still served `?v=20260526a` (the old cache-bust string).

**Root cause.** `docker compose restart` reuses the existing
image. The new template file was on the host (after `git
pull`), but the running container's `/app/app/templates/...`
filesystem inside the image was the old one. Restart doesn't
re-sync from the host.

**Fix.** Operator re-ran with `docker compose ... up -d
--build` which rebuilds the image with the new files.

**Prevention.**
- Document clearly: **template / static file changes need
  `--build`, not `restart`**. Restart only works for Python
  changes when the image has a bind-mount on the source
  directory.
- Consider adding a bind-mount on `app/templates/` and
  `app/static/` in `docker-compose.yml` for production — so
  `git pull && docker compose restart` is enough for asset
  changes. Trade-off: slower container start.
- A `deploy.sh` wrapper that decides `--build` vs `restart`
  based on which files changed would eliminate this class
  of mistake.

---

## 11) Hot-spot script pasted with placeholder values

**Symptom.** After completing Step 5 (Hotspot service) and
pasting into MikroTik, two RADIUS rows appeared in
`/radius print` with:

```
secret: REPLACE_ME
status: binding:Address not available
```

The "Address not available" came from `src-address=10.10.0.5`
when the router actually had `10.10.0.8` (the run's
allocated VPN IP). And `REPLACE_ME` was the literal string
the JS sent — meaning even if authentication had been
attempted, FreeRADIUS would reject it (shared-secret
mismatch).

**Root cause.** In the JS handler for
`generateHotspotScript()`, both `radius_secret` and
`router_vpn_ip` were hard-coded to placeholder values:

```js
radius_secret: "REPLACE_ME",
router_vpn_ip: "10.10.0.5",
```

The phase planner faithfully baked those into the generated
script, with no way of knowing they were placeholders.

**Fix.** Commit (this slice):

1. JS calls `fetchRunState()` to read the real
   `router_vpn_ip` from the run before submitting.
2. New `radius_secret` input field on the Hotspot card,
   auto-filled with 32 hex chars from `crypto.getRandomValues`
   on first toggle-on.
3. Toast reminds the operator to save the secret because
   FreeRADIUS `clients.conf` on the server must have the
   same value.
4. Guard: if the user reaches Step 5 before Step 3 generated
   the unified script (so `router_vpn_ip` is still empty),
   we block with a friendly Arabic message instead of
   silently using a stale default.

**Prevention.**
- **Never hard-code per-run values** in client-side code.
  Pull from server state at the moment of use.
- Phase planner inputs that affect router-server integration
  (secrets, IPs, ports) MUST come from the run state — not
  the JS defaults. Validate this at the route level: if a
  caller sends `radius_secret="REPLACE_ME"`, reject as 400.
- Add a server-side check: any input value matching a
  short-list of obvious sentinels (`REPLACE_ME`, `TODO`,
  `X.X.X.X`) gets a 400 with a clear "looks like a
  placeholder" error.
- Idempotency for the hotspot RADIUS row: a follow-up
  should wrap the `/radius add` in `:if ([:len [/radius
  find where comment~"HOBERADIUS_SETUP:<run>:hotspot"]] = 0)`
  so pasting twice doesn't double-add.

---

## 12) Hotspot NAT didn't masquerade — empty `WAN` interface-list

**Symptom.** Hotspot users on the router got an IP, opened
the captive portal, authenticated — and then had no
internet. The Hotspot RADIUS auth worked, but outbound
packets weren't being NAT'd to the router's WAN IP.

**Root cause.** The hotspot planner generated this NAT rule:

```
/ip firewall nat add chain=srcnat src-address=10.99.X.0/24 \
  out-interface-list=WAN action=masquerade ...
```

`out-interface-list=WAN` means "match only if the packet
exits through an interface in the list named WAN." A fresh
MikroTik has no interface list named WAN by default — so
the rule never matched, no packet got NAT'd, no internet.

**Fix.** The planner now accepts an optional `wan_interface`
input. When provided (the v3 wizard's Hotspot card always
provides it), the script prepends an idempotent
bootstrap that creates the list and adds the WAN interface
as a member:

```
:if ([:len [/interface list find where name="WAN"]] = 0) do={
  /interface list add name=WAN
}
:if ([:len [/interface list member find where
            list=WAN interface="ether1"]] = 0) do={
  /interface list member add list=WAN interface="ether1"
}
```

Now the existing NAT rule actually matches.

**Prevention.**
- **Never reference a named resource (interface-list, profile,
  pool) without ensuring it exists.** Every reference must
  either pin a previously-emitted definition or include a
  bootstrap.
- A script linter that flags `out-interface-list=X` without
  a matching `/interface list add name=X` in the same
  script would catch this. Future slice candidate.

---

## 13) Hotspot UI only sent one interface — multi-port lost

**Symptom.** Operator wanted Hotspot on ether2 + ether3 +
wlan1 but the wizard form only accepted a single interface
name in a text field. Even though the legacy planner
supports an array of `selected_interfaces`, the JS
hard-coded a single-element array.

**Root cause.** New wizard's Hotspot card UI was built
quickly to validate the flow end-to-end with one port. It
worked, but the "multi-port hotspot" use case (every
ether is a customer port with its own /24 + DHCP) was the
ACTUAL operator need — and the UI didn't expose it.

**Fix.** Replaced the single text input with a checkbox
grid of the 6 most-common interfaces (ether2-5, wlan1,
sfp1) plus a free-text input for custom interfaces
(bridge1, vlan10, etc.). The JS de-duplicates and passes
the full array to the planner.

```js
function collectHotspotInterfaces() {
  const checked = Array.from(
    root.querySelectorAll(
      "[data-swz-hotspot-ifaces] input[type=checkbox]:checked"
    )
  ).map((cb) => cb.value);
  const custom = (getValue(custom_field) || "")
    .split(/[,\s]+/).map((s) => s.trim()).filter(Boolean);
  return Array.from(new Set([...checked, ...custom]));
}
```

The planner already supported the multi-interface path —
this slice just connected the UI to it.

**Prevention.**
- When wrapping a multi-value backend in UI, **always start
  with multi-value UI** even if only one is in the demo. A
  single-value UI is harder to upgrade later (operators
  build muscle memory around the wrong shape).
- Follow-up: an "🔍 اكتشاف المنافذ" button that pastes the
  output of `/interface print` from MikroTik, parses it,
  and renders checkboxes for the actual interfaces on the
  router. Removes guesswork for non-standard layouts.

---

## 14) Repeated `/radius add` on re-paste — duplicate rows

**Symptom.** Operator pasted the hotspot script twice
(manually, while debugging). The RADIUS table on MikroTik
ended up with two identical-ish HOBERADIUS rows. Both
showed `binding:Address not available` because the secret
+ src-address were stale placeholders from issue #11.

**Root cause.** The legacy hotspot planner emitted a bare
`/radius add ...` without idempotency. Every script paste
appends another row.

**Fix.** Wrapped the `/radius add` in a tagged guard:

```
:if ([:len [/radius find where
            comment="HOBERADIUS_SETUP:<run>:hotspot"]] = 0) do={
  /radius add ... comment="<tag>"
} else={
  /radius set [find where comment="<tag>"] address=... \
    secret=... src-address=...
}
```

Repeat pastes now update the same row rather than
duplicating. The legacy test (`test_hotspot_manual_script_sections_and_validation`)
was loosened to assert the key fields without pinning the
exact prefix (no more single-line literal match).

**Prevention.**
- **All RouterOS `add` commands in generated scripts MUST
  be wrapped in an idempotency guard.** Pattern:
  `:if ([:len [<find query>]] = 0) do={ <add> } else={ <set> }`.
- Tests should assert structural properties (entry exists
  with these key fields) rather than literal-line matches —
  brittle to formatting tweaks.

---

## 15) MikroTik Terminal silently drops long pasted lines

**Symptom.** After Step 3 the operator pastes the unified
script into MikroTik Terminal. The session shows every line
echoed back EXCEPT two:

```
> /user remove [find where name="hr-api-37"]
>                                                ← /user add gone
> /ip service enable api
...
> /radius remove [find where comment~"HOBERADIUS_SETUP:37:radius"]
>                                                ← /radius add gone
> /radius incoming set accept=yes port=3799
```

The dropped lines happened to be the two longest in the
script — `/user add ... password=<24ch> group=full comment=...`
(~140 chars) and `/radius add service=hotspot,ppp,login
address=10.10.0.1 secret=<32ch> ... comment=...` (~220 chars).

Result: the public-key paste-back succeeded (visible from
the print output), the WireGuard handshake worked — but the
router had no `hr-api-37` user and no `/radius` row pointing
at 10.10.0.1. Step 5's hotspot config and Step 6's
register-router both failed downstream.

**Root cause.** MikroTik RouterOS Terminal (Winbox + SSH
both) silently truncates pasted input that exceeds the
internal line buffer (~200 chars in 7.x). The drops happen
without an error message — the terminal just shows an empty
prompt where the line should be, and the operator assumes
"empty line" rather than "line eaten."

This isn't a bug in our script; it's a paste-flow-control
limitation of RouterOS Terminal. The .rsc text we generate
is syntactically perfect — it just doesn't reach the parser
in one piece.

**Fix.** Pivot the Step 3 UX from "copy + paste the whole
script" to "copy + paste TWO LINES that download and import
the script":

```
/tool fetch url="http://187.77.70.18/wz/<short>.rsc" mode=http dst-path="hr-setup.rsc"
/import file-name="hr-setup.rsc"
```

The .rsc file is served by the existing
`setup_wizard_v3_serve_script` route at `/wz/<code>.rsc`.
`/tool fetch` downloads the entire file atomically, and
`/import` executes every line including the long ones.

The wizard now renders the fetch+import pair inside a green
"🚀 الطريقة الموصى بها" card as the primary call-to-action.
The full-paste textbox still exists, collapsed inside a
`<details>` "or paste manually (older method)" with an
amber warning explaining the 200-char limit.

**Prevention.**
- **Generated scripts should never assume the paste path
  works for long lines.** Default to `/tool fetch` + `/import`
  for any script over ~10 lines or any single line over
  150 chars.
- For lines that must be pasted (e.g. one-time public-key
  retrieval), keep them under 100 chars.
- Lint generated scripts at render time: warn (or split) if
  any line exceeds 200 chars. Future slice.

---

## 16) `/tool fetch` progress eats the next pasted line

**Symptom.** Operator pasted the two recommended lines:

```
/tool fetch url="http://.../wz/<code>.rsc" mode=http dst-path="hr-setup.rsc"
/import file-name="hr-setup.rsc"
```

The fetch completed successfully (3 KiB, 1s) — but the
`/import` line never executed. No error, no echo, just an
empty prompt where `/import` should have been:

```
> /tool fetch url="..." mode=http dst-path="hr-setup.rsc"
      status: finished
  downloaded: 3KiB
       total: 3KiB
    duration: 1s
> ← /import line is gone
```

**Root cause.** While `/tool fetch` runs, MikroTik Terminal
prints a multi-line progress block (status / downloaded /
total / duration). Each rewrite of that block consumes
input from the paste buffer the same way it consumes a
keystroke from the user. By the time the fetch finishes,
the `/import ...` line that was pasted right after has been
silently absorbed by the progress renderer — never queued
for execution.

This is the same "paste-loss" failure class as Issue #15,
but triggered by an in-progress command rather than the
200-char buffer limit.

**Fix.** Render the two commands as ONE LINE joined by `;`:

```
/tool fetch url="..." mode=http dst-path="hr-setup.rsc"; /import file-name="hr-setup.rsc"
```

RouterOS parses + queues both commands atomically as a
single input line. Fetch runs first (with its progress
output), import runs immediately on completion. Nothing
can sneak in between because there's nothing in the buffer
to be eaten.

Total length: ~130 chars — under the 200-char limit from
Issue #15, so we're safe on both fronts.

**Prevention.**
- **Multi-line paste workflows with progress-emitting
  commands are unreliable.** Default to `;`-joined single
  lines whenever an interactive command (`fetch`, `import`,
  `ping`, `traceroute`) precedes anything else.
- The pattern `cmd_with_progress; cmd_after` is the
  RouterOS-equivalent of `&&` in bash. Use it for any
  generated routine that has more than one command.
- If a command line risks going over 200 chars, split into
  multiple `;`-joined lines — but each individual line must
  stay under 200.

---

## 17) FreeRADIUS crash-loop because `$INCLUDE` doesn't support wildcards

**Symptom.** Subscriber tries to log in via Hotspot. Router
shows `radius monitor: pending-replies: 1`. Login screen
hangs and eventually says "لا يستجيب". `radclient` test from
inside the freeradius container itself:

```
(0) No reply from server for ID 87 socket 3
```

docker logs of `hoberadius-freeradius`:

```
[entrypoint] freeradius exited with code 0, restarting in 1s
[entrypoint] freeradius exited with code 0, restarting in 1s
... (every second forever)
```

Running `freeradius -X` manually:

```
including configuration file /data/freeradius-clients-wizard/*.conf
Unable to open file "/data/freeradius-clients-wizard/*.conf":
No such file or directory
Errors reading or parsing /etc/freeradius/radiusd.conf
```

The directory has 5 valid `.conf` files. They are readable
by the `freerad` user (verified with `ls -la` inside the
container). And FreeRADIUS STILL says "No such file" for
the wildcard path.

**Root cause.** I added this line to `clients.conf` in
commit 67afa95:

```
$INCLUDE /data/freeradius-clients-wizard/*.conf
```

I assumed `$INCLUDE` supported glob expansion the way bash
does. **It does not.** Quote from `man radiusd.conf`:

> "There are no character globbing or wildcards supported."

FreeRADIUS tries to open the literal string
`/data/freeradius-clients-wizard/*.conf` as a single file
path. The literal asterisk in the path → no such file exists
→ parse fails → daemon exits → supervisor restarts → loop.

My earlier diagnosis (commit 323a199, "empty dir") was
WRONG. The real fix needed was a syntax change.

The supervisor loop in entrypoint.sh masked the real error
by printing only the "restarting in 1s" line on every
iteration, drowning the actual "Unable to open file" line
in noise.

**Fix.** Two layers:

1. **Immediate manual recovery** (patch the running
   container's clients.conf in-place + restart):
   ```bash
   docker exec hoberadius-freeradius sed -i \
     's|\$INCLUDE /data/freeradius-clients-wizard/\*\.conf|$INCLUDE /data/freeradius-clients-wizard/|' \
     /etc/freeradius/clients.conf
   docker compose -f deploy/docker-compose.yml restart freeradius
   ```

2. **Permanent fix in deploy/freeradius/clients.conf**:
   replace the wildcard form with the directory form (path
   ending in a trailing slash):
   ```
   $INCLUDE /data/freeradius-clients-wizard/
   ```
   FreeRADIUS treats a trailing-slash path as a directory and
   auto-loads every `.conf` file inside (the same effect we
   wanted, with documented semantics). Also tolerates an
   empty directory — the daemon starts even if zero wizard
   rows exist yet.

The `_placeholder.conf` from commit 323a199 is no longer
strictly required (the directory form handles empty dirs)
but it stays as defence-in-depth: a marker file documenting
the convention to operators.

**Prevention.**
- **Read the daemon's docs, don't assume bash semantics.**
  When a config language LOOKS like shell, that doesn't
  mean it BEHAVES like shell. Always check the manual for
  globbing / quoting / escape rules before guessing.
- **Supervisor loops can hide real errors.** Mine restarted
  freeradius every second on exit code 0, masking the parse
  error. A supervisor should at minimum:
    * log the first failure with full stderr context
    * back off exponentially (1s → 2s → 4s → ...) so the
      log doesn't drown in restart messages
    * bail out after N consecutive immediate failures
- **Add a comment block above any `$INCLUDE`** explaining
  why the chosen form was picked (literal file vs.
  directory). Stops the next maintainer from "simplifying"
  back to a wildcard.

---

## 18) Wizard-allocated IP collided with hardcoded clients.conf entry

**Symptom.** After fixing the `$INCLUDE` syntax (issue #17),
FreeRADIUS still wouldn't start. New error:

```
Failed to add duplicate client wizard-ccr2
/data/freeradius-clients-wizard/wizard-run-42.conf[7]:
Failed to add client router-42
```

**Root cause.** `deploy/freeradius/clients.conf` had a
pre-existing hardcoded block from before the Setup Wizard
existed:

```
client mt_vpn_10_10_0_2 {
    ipaddr   = 10.10.0.2
    secret   = 123123
    ...
}
```

The wizard's IP allocator picks the next free VPN IP by
scanning `nas_devices.vpn_peer_address` for collisions. It
does NOT look at hardcoded entries in clients.conf.

After an emergency reset cleared `nas_devices`, the
allocator saw 10.10.0.2 as free → assigned it to run #42 →
wrote `wizard-run-42.conf` with `ipaddr = 10.10.0.2`.
Result: two clients with the same ipaddr → FreeRADIUS
refuses to start (it considers same-ipaddr clients as
duplicates regardless of the client-block name).

**Fix.** Removed the hardcoded `mt_vpn_10_10_0_2` block
from `deploy/freeradius/clients.conf`. Replaced with a
comment explaining why: the wizard now owns every VPN-range
client. Manually-managed entries for routers OUTSIDE the
VPN range (e.g. `mt_main_213_6_169_138` at a public IP)
stay untouched.

In-container immediate recovery (no rebuild needed):
```bash
docker exec hoberadius-freeradius sed -i \
  '/^client mt_vpn_10_10_0_2 {/,/^}$/d' \
  /etc/freeradius/clients.conf
docker compose -f deploy/docker-compose.yml restart freeradius
```

**Prevention.**
- **The wizard's IP allocator should also scan
  clients.conf** (or any source of authoritative client
  definitions) before assigning an IP. A follow-up slice
  could add a config parser that walks the static
  clients.conf and feeds the used-IP set the allocator
  consults.
- **Static clients.conf entries are an anti-pattern now
  that the wizard exists.** Any entry that the wizard
  could re-provision via its catalogue should move to
  the wizard pipeline. Keep static entries only for
  routers OUTSIDE the wizard's VPN range.
- **When introducing a new authoritative system (here:
  the wizard), audit existing manual configs for overlap.**
  This block should have been removed in the same PR that
  introduced the wizard-clients-wizard directory include.

---

## 19) Docker bridge SNAT-ed router RADIUS traffic to a generic catch-all

**Symptom.** After issues #17 (`$INCLUDE` syntax) and #18
(hardcoded VPN-IP collision) were fixed, FreeRADIUS finally
booted with `Ready to process requests`. But a subscriber
login attempt from the wizard-provisioned MT router at
10.10.0.2 produced:

```
Info: Dropping packet without response because of error:
Received packet from 172.18.0.1 with invalid Message-Authenticator!
(Shared secret is incorrect.) (from client docker)
```

Two surprises:

1. `Received packet from 172.18.0.1` — but the MT router's
   tunnel IP is 10.10.0.2.
2. `(from client docker)` — matched a catch-all
   `client docker_network { ipaddr=172.16.0.0/12 secret=testing123 }`
   block instead of the per-run `client router-42 { ipaddr=10.10.0.2 }`.

**Root cause.** FreeRADIUS ran on the docker bridge network
with `ports: "1812:1812/udp"` etc. Linux iptables on the host
had a `MASQUERADE` rule for `-s 10.10.0.0/24 ! -o wg0`,
intended to let routers reach the internet via the VPS uplink.
But the same rule rewrote inbound RADIUS source IPs:

```
MT (10.10.0.2) → wg0 → host iptables PREROUTING (DNAT to
   freeradius container ip) → routing decision: exit via
   br-XXX (the docker bridge) → POSTROUTING MASQUERADE
   matches (src=10.10.0.0/24, out!=wg0) → src rewritten to
   172.18.0.1 → freeradius sees 172.18.0.1 → matches the
   broad `client docker_network` block → secret mismatch
   (catch-all uses legacy `testing123`, wizard-provisioned
   routers use unique per-run secrets) → packet dropped.
```

Per-router unique secrets — the SAFE design — could never
work as long as FreeRADIUS lived behind the docker bridge.

**Fix.** Two changes:

1. **`deploy/docker-compose.yml`**: switch freeradius to
   `network_mode: host`. The daemon binds directly to
   `0.0.0.0:1812/1813/3799` on the host's network stack.
   No DNAT, no SNAT, no docker bridge between the router
   packet and the daemon. RADIUS packets arrive with their
   real WireGuard source IP (10.10.0.2), so
   `client router-42 { ipaddr=10.10.0.2 secret=<unique> }`
   matches and the wizard's per-router secrets work end to
   end.

2. **`deploy/freeradius/mods-enabled/rest`**: change
   `connect_uri` from `http://hoberadius:8000` to
   `http://127.0.0.1:8000`. The hostname `hoberadius` is a
   docker-DNS name only resolvable from inside the bridge
   network — a host-networked container can't reach it.
   To make hoberadius reachable from host-networked
   freeradius, the hoberadius service now publishes
   `127.0.0.1:8000:8000`. This binds 8000 to the host's
   LOOPBACK ONLY — not to all interfaces, so the policy
   endpoint stays invisible to the public internet while
   freeradius (and any other process on the host) can hit
   it via 127.0.0.1.

**Why NOT the alternative ("use `testing123` everywhere").**
That was the fast path — just make every wizard run use the
same shared secret as the catch-all client. It would have
been a 2-line wizard change. But it sacrifices the security
property the per-run secret was introduced for: if any one
router gets compromised, an attacker can impersonate every
other router under the same RADIUS server. The wizard's
random `secrets.token_hex(16)` is wasted if all routers
collapse to one shared key. Host networking preserves the
property at the cost of a small compose + URL change.

**Verification checklist:**
- `docker compose ... up -d --build freeradius`
- `docker logs hoberadius-freeradius --tail 20` shows
  `Ready to process requests` with no `exited with code`
  loop.
- From host: `ss -lunp | grep 1812` shows freeradius (PID)
  bound on `*:1812`.
- A login from MT router produces a log line:
  `Auth: Login OK: [<user>] (from client router-<run>
  port 0)` — NOT `from client docker`.
- `client docker_network` remains in clients.conf as a
  catch-all for bridge-internal testing but should no longer
  match any production traffic.

**Prevention.**
- **Daemons that need real source IPs (RADIUS, syslog,
  GTP, IPSec, anything matching by client IP) should run
  on host networking from day one.** The docker bridge's
  SNAT magic breaks this class of protocols silently.
- **Per-client identification at the network layer
  (clients.conf matched by source IP) is fragile.** When
  feasible, identify clients by an in-packet attribute
  (e.g. `NAS-Identifier`) so SNAT can't break the lookup.
- **`expose:` vs `ports:` matters.** `expose: ["8000"]`
  makes the port accessible only on the bridge network.
  `ports: "8000:8000"` exposes to ALL host interfaces.
  `ports: "127.0.0.1:8000:8000"` exposes only to the host's
  loopback — usually what you want for an internal service
  reachable by host-networked sibling containers.

---

## 20) Router secret and server secret drifted because they were two separate steps

**Symptom (production-class).** After all the network-layer
fixes (#17 / #18 / #19), live testing surfaced one more
class of bug: the secret on the MikroTik router and the
secret in the server's `wizard-run-<id>.conf` could fall out
of sync. Specifically the user ran the wizard repeatedly
(runs #41 → #42 → #43 → #44 over the debugging session) and
ended up with:

* Router (MT): `secret = b5c5...` from run #44
* Server (`wizard-run-42.conf`): `secret = b092...` from run #42
* No `wizard-run-44.conf` on the server at all

The auth packet from MT carried run #44's secret, FreeRADIUS
checked it against the only file it had (run #42), HMAC
mismatch, dropped.

This isn't catastrophic in a lab, but **in production**:
- A subscriber would try to log in, get "no response", call
  support
- Support would have to manually compare router and server
  secrets, hand-correct the file
- Each affected router needs a separate diagnosis

The user explicitly asked: "how do we prevent this
fundamentally, with strong invariants?"

**Root cause.** The wizard was designed as two separate
operations:

1. `generate_unified_script` — generates a new secret, bakes
   it into the router-side script, stores it in
   `state_json`.
2. `apply-server-radius` button — writes `wizard-run-<id>.conf`
   on the server.

The two were independent and depended on the operator
clicking the button. If they didn't (forgot, distracted,
network hiccup), the router moved forward with a new secret
and the server kept its old one.

**Fix.** Three layers of defence, all enforced by code:

### Layer 1: Atomicity at generation time

`generate_unified_script` now writes the server-side
`wizard-run-<id>.conf` IMMEDIATELY after computing the
secret, in the same call, BEFORE returning the script body.
If the write fails, the whole script generation fails — the
operator never sees a script whose secret the server doesn't
know about.

```python
# in setup_wizard_v3.py::generate_unified_script
try:
    from .setup_wizard_v3_radius_server_provisioning import (
        write_client_for_run,
    )
    server_provisioning = write_client_for_run(
        run_id=run_id,
        router_vpn_ip=router_vpn_ip,
        radius_secret=radius_secret,
        shortname=...,
    )
except Exception as exc:
    raise V3Error(
        "تعذّر كتابة إعداد RADIUS على الخادم: ... "
        "لا يمكن المتابعة لأن الراوتر سيحصل على سرّ "
        "غير معروف على الخادم."
    ) from exc
```

This eliminates the "operator forgot to click" failure mode.
The manual button stays as a recovery tool but is no longer
on the critical path.

### Layer 2: One-IP-one-file invariant

`write_client_for_run` now calls
`_purge_stale_files_for_ip()` BEFORE writing the new file.
For each existing `wizard-run-*.conf` whose `ipaddr =` value
matches the new one (but with a different run_id), the old
file is deleted. The filesystem can never hold two active
client entries claiming the same VPN IP — collision
detection happens at write time, not at FR-startup time.

### Layer 3: Periodic reconciler (defence in depth)

A new worker (`setup_wizard_radius_reconciler_worker.py`)
runs every 5 minutes and enforces three invariants against
the wizard's source of truth (`setup_wizard_runs.state_json`):

- **INV-1**: every active run (v3_state ∈ {VERIFYING,
  REGISTERING, COMPLETE}) MUST have a matching
  `wizard-run-<id>.conf` whose `secret` field equals
  `state_json.radius_secret`. Violations → rewrite.
- **INV-2**: every `wizard-run-<id>.conf` MUST correspond
  to an active run. Orphans → delete.
- **INV-3**: at most one file per `ipaddr` value.
  Duplicates → keep the newest (highest run_id), delete
  the rest.

After any reconciliation action, the `.reload-trigger` is
touched so FreeRADIUS picks up the new state within ~5s.

This catches every drift case the primary path can't:
- Manual operator deletions/renames in the directory
- DB restores that change state_json without touching
  files
- Future wizard slices that forget to call
  write_client_for_run
- Anything else we haven't thought of

### Tests (6 new) pinning each invariant

- `test_reconciler_writes_missing_file_for_active_run`
- `test_reconciler_rewrites_file_with_drifted_secret`
- `test_reconciler_deletes_orphan_file`
- `test_reconciler_dedupes_files_sharing_ipaddr`
- `test_write_client_purges_stale_file_for_same_ip`
- `test_reconciler_quiet_when_everything_matches`

**Prevention rule for the future.** Any time the wizard
generates an artifact that lives on BOTH the router AND the
server (secrets, keys, IP allocations), the two sides MUST
be provisioned atomically OR a reconciler MUST verify they
stay in sync. The same pattern applies to:

- WireGuard public keys (already handled by the WG peers.d
  + wg-reload pipeline)
- Future: API users, certificates, etc.

Single-step operator interactions are unsafe for split-state
provisioning — they break the moment the operator gets
distracted, the wizard times out, or the page is refreshed.

---

## Themes

Three root causes show up repeatedly:

1. **Silent UI failure modes** (#2, #4). Click does nothing,
   button toggles return empty, JS short-circuits without a
   user-visible signal. → Every UI surface needs an explicit
   "request fired" toast / error path.

2. **Single source of truth violations** (#3, #4, #8). The
   same string / kwarg name lives in multiple files and
   drifts. → Lift enums + response shapes into a shared
   module or generate one from the other.

3. **Cross-environment assumptions** (#5, #10, #2). Local
   pytest passes, then production breaks because RouterOS
   has different scoping rules, Docker doesn't re-sync
   files, browsers cache `.js`. → Test in the actual
   environment (paste into Terminal, refresh the real
   browser, deploy to a staging container) before declaring
   "done."

---

## What changed structurally after this session

Code-level fixes are listed per issue above. Beyond that:

* **Cache-busting convention** — every v3 wizard static
  asset now carries a `?v=<date>` suffix. Bump on every
  breaking change. Future: derive automatically from file
  hash.

* **TTL reclaimer + worker** — closes the "ghost reservation"
  failure mode that nobody had a clean recovery path for
  before. Documented in commits T1/T2/T3.

* **Emergency fleet reset** — for the catastrophic case the
  TTL reclaimer can't handle. Documented in
  `setup_wizard_fleet_emergency_reset.py`.

* **Phase planner endpoints in v3** — SW7 binds the
  per-phase script generators into the v3 route layer so a
  future toolbox page (operator request, captured for next
  session) can reuse them.

---

## Open follow-ups (not blockers)

* Single source of truth for `router_type` enum (issue #3).
* Add `--build`-vs-`restart` decision to deploy.sh (issue #10).
* Per-step verification UI: each wizard step should require
  a real paste-back + parse before letting the operator
  advance. Captured as operator request — not yet
  implemented.
* Standalone "Script Toolbox" page: standalone form for
  generating any phase script outside the full wizard run.
  Captured as operator request — not yet implemented.
* Migrate test fixtures to use shared monotonic counter
  helpers (issue #7).
