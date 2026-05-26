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
