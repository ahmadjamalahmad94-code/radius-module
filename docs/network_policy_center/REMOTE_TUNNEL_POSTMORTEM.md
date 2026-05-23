# NPC Remote-Tunnel — Postmortem of the First Deployment

Working remote-access via VPS works (Winbox from outside opens cleanly through `187.77.70.18:51000`). But the road there hit thirteen distinct issues, and the operator's experience was "a thousand checks for nothing." This document catalogues every issue, its root cause, and the prevention so we never repeat it.

> **Audience**: on-call + future maintainers. NOT end-users.

## TL;DR

The product hypothesis (one button → working remote URL) was correct. The implementation took thirteen small things to get right because the failure modes were silent (file silently created as dir, env var silently ignored, etc.) and each one required a round-trip to diagnose.

## Issue catalogue

### 1. VPS git checkout drifted behind origin

* **Symptom**: After `docker compose up -d --build`, `nginx-main.conf` wasn't loaded; the default `/docker-entrypoint.sh` ran instead of our custom one.
* **Root cause**: `git pull` was never run before the rebuild, so the `nginx-main.conf` and `nginx-entrypoint.sh` files literally didn't exist on disk. Docker silently created the bind-mount source paths as empty directories instead of failing.
* **Prevention**: deploy.sh wrapper should `git pull` before every rebuild and refuse to proceed if pulls fail. Also a startup health-check that asserts `nginx-main.conf` is present and parseable.

### 2. Stale env-var instructions in `docker-compose.yml`

* **Symptom**: VPS docker-compose.yml had three env vars from the original (allowlist-gated) design: `HOBERADIUS_NPC_LIVE_EXECUTOR`, `_LIVE_ROUTER_IDS`, `_LIVE_DRY_RUN`. These were obsoleted by the default-on simplification commit but kept lingering.
* **Root cause**: Operator edited compose by hand based on my earlier instructions, then I changed the design without explicitly telling them to revert the env-var block. `git pull` couldn't merge because of the local edit.
* **Prevention**: Avoid env-var-driven feature flags for default UX. When the design has to change, ship the change AND a fix script that resets any stale operator edits.

### 3. Env vars added to the wrong service

* **Symptom**: The three NPC env vars ended up under `freeradius:` not `hoberadius:` in docker-compose.yml. Without `hoberadius` seeing them, the live executor would never have wired anyway — even if the variables had still been needed.
* **Root cause**: My instructions were ambiguous about which service. Two adjacent `environment:` blocks in the compose file.
* **Prevention**: Always reference env vars by their consumer service explicitly, and prefer `env_file: ../.env` (already in place) so per-service blocks don't accidentally get them.

### 4. Default-deny adapter design was over-engineered

* **Symptom**: First implementation required THREE env vars to opt in (master switch + allowlist + dry-run). User pushed back: "all this complication and stress?"
* **Root cause**: The original brief was paranoid about live execution. I generalised that paranoia into an allowlist on top of the contracts engine, even though the contracts engine already gates apply.
* **Prevention**: Trust upstream gating. When the brief is paranoid, ship the safety scaffolding once, then make the obvious default work. Don't double-gate.

### 5. nginx-streams.d permission denied

* **Symptom**: First apply via `/admin/.../apply` returned 500. Cause: `PermissionError: [Errno 13] Permission denied: '/etc/hoberadius/nginx-streams.d/npc_remote.conf.tmp'`.
* **Root cause**: Host directory was created with `sudo mkdir -p` + `chmod 755`, owned `root:root`. The hoberadius container runs as a non-root user that can't write there. Bind mounts honour host permissions strictly.
* **Prevention**: First-boot init must chown the directory to the container's user/group. Either via deploy.sh, an entrypoint helper, or a Docker named volume that defaults to writable. Documented and codified — see `deploy/deploy.sh init-npc-streams`.

### 6. NameError in the exception handler

* **Symptom**: When the nginx-streams write failed, the exception handler itself raised `NameError: name 'current_app' is not defined`, turning a clean fallback into a 500 response.
* **Root cause**: I wrote `current_app.logger.exception(...)` in the except block but forgot to `from flask import current_app`. The handler was never exercised during local pytest because the local file system was always writable.
* **Prevention**: Use module-local `logging.getLogger(__name__)` in except blocks — it can't NameError. Also add a test that explicitly triggers the side-effect failure path.

### 7. Default nginx entrypoint silently replaced ours

* **Symptom**: `docker compose up -d` started, but nginx logs showed `/docker-entrypoint.sh: Launching ...` (the default) instead of `[nginx-entrypoint] watching streams.d`. The custom auto-reload loop wasn't running. nginx-main.conf wasn't loaded either, so no `stream {}` block.
* **Root cause**: The bind-mount source `./nginx-entrypoint.sh` didn't exist on the host (see issue #1). Docker bind-mount semantics: when the source doesn't exist, Docker creates it as an **empty directory**. The container then has a directory at `/usr/local/bin/nginx-entrypoint.sh`, which is unreadable as a script, so the entrypoint silently falls back to the image default.
* **Prevention**: Always validate bind-mount sources exist before `docker compose up`. The deploy.sh wrapper should do this. Long-term: switch from bind mounts to a copied-into-image or a Docker named volume.

### 8. Wrong DB filename in diagnostic commands

* **Symptom**: `sqlite3 /app/instance/test.db ".tables"` returned empty even though the migration applied. Then sqlite3 wasn't installed at all in the container.
* **Root cause**: I guessed `test.db` (the test fixture filename) instead of looking at the actual `HOBERADIUS_DB_PATH`. Production DB is `hoberadius.db`. Two wrong guesses in two minutes.
* **Prevention**: Never hard-code DB paths. Either read from `HOBERADIUS_DB_PATH` env in the container or `glob.glob('/app/instance/*.db')`. Document the canonical filename in the deployment docs.

### 9. `from app import create_app` from inside the container failed

* **Symptom**: `ModuleNotFoundError: No module named 'app'` when running `docker exec hoberadius python /tmp/script.py`.
* **Root cause**: The container's `WORKDIR` is `/app`, and the Python module `app` is at `/app/app/`. Without `PYTHONPATH=/app`, the script's directory (`/tmp`) was on sys.path but `/app` wasn't.
* **Prevention**: All ad-hoc admin scripts inside the container must `docker exec -e PYTHONPATH=/app`. Ideally add a wrapper `hr-admin` script in the image that sets PYTHONPATH and lets operators just call `docker exec hoberadius hr-admin <script>`.

### 10. Heredoc through `docker exec` mangled the script

* **Symptom**: A 30-line Python script sent via `docker exec hoberadius python <<'PYEOF'` got truncated at random points, sometimes mid-line.
* **Root cause**: Combination of (a) heredoc passing stdin to docker exec, (b) docker exec without `-i` ignoring some stdin bytes, and (c) shell quoting eating quotes inside the heredoc.
* **Prevention**: Write the script to a host file, `docker cp` it into the container, then exec it. Document this as the canonical pattern for one-off admin scripts.

### 11. Address-list bootstrap is chicken-and-egg

* **Symptom**: First apply succeeded but Winbox-via-VPS wouldn't connect — the router's `vps-relay` address list was empty, so the firewall rule the policy created denied all sources.
* **Root cause**: The user has to add the VPS's WG IP to the `vps-relay` list BEFORE applying the policy. But the typical operator doesn't have a way to add an address-list entry from inside Hoberadius without first opening Winbox (which is what we're trying to enable from outside!).
* **Prevention**: **This is the big one**. The apply itself should inject the VPS WG IP into the source list as part of the rendered script. The operator picks a name, presses apply, and everything is wired — no manual address-list step. This is now fixed (see commit after this postmortem).

### 12. Source-address-list name typo

* **Symptom**: First policy attempt: user typed `source_address_list=remote-via-vps` (same as policy name) instead of `vps-relay` (the list that had the VPS WG IP).
* **Root cause**: The UI label didn't make it clear that the name must match an existing list on the router (or one that the policy will populate). Easy to confuse with the policy slug.
* **Prevention**: Once auto-injection lands (issue #11), the field can be hidden entirely for the common case. Operators who need a custom list still can, but the default works.

### 13. `HOBERADIUS_PUBLIC_HOST` env var was missing

* **Symptom**: The "from outside the network (via VPS)" section never rendered because `compute_remote_access_urls(public_host="", ...)` returns empty when host is empty.
* **Root cause**: The route falls back to `request.host` if the env var is unset — but that depends on the operator currently browsing via the public IP. Robust to having the env var set explicitly.
* **Prevention**: deploy.sh should auto-set `HOBERADIUS_PUBLIC_HOST` based on what the operator already configured for the WireGuard endpoint (it's the same VPS public IP).

## Themes

Four root causes show up repeatedly:

1. **Silent failure modes** (issues #1, #5, #7, #13). Docker creates missing bind-mount sources as empty dirs. nginx silently falls back to default config. Permission errors only surface at first write. → Need explicit validation at startup.
2. **Operator has to do too much** (issues #2, #3, #5, #11, #12). The "automatic" promise fell apart at every manual step. → Inject sensible defaults; refuse to ship anything that requires hand-editing infra files.
3. **Diagnostic dead-ends** (issues #8, #9, #10). Each debugging session took 2-3 round-trips. → Build an admin CLI inside the container that knows the right paths/env, instead of always shell-ing in raw.
4. **Untested fallback paths** (issue #6). The exception handler had never run in practice. → Tests that explicitly trigger side-effect failures.

## What changed after this postmortem

Code lands in the same commit:

* **Auto-inject VPS WG IP into source list** — the apply script now creates `/ip/firewall/address-list/add list=<source_list> address=<HOBERADIUS_WG_SERVER_IP> comment="HOBE_NPC_REMOTE:<id>:vps-relay-anchor"` for every remote-access apply. The operator picks toggles, presses apply, done.
* **Smart default for source_address_list** — empty → auto-generate `npc-vps-<policy_id>`. Avoids the "name typo" failure mode.
* **First-boot init in deploy.sh** — `init-npc-streams` subcommand chowns / chmods `/etc/hoberadius/nginx-streams.d/` for the container's group. Operators no longer chmod 777.
* **Bind-mount validation at deploy time** — deploy.sh `up` refuses to proceed if `nginx-main.conf` or `nginx-entrypoint.sh` are missing. No more silent empty-dir bind mounts.

## What is NOT fixed by this postmortem

* The `docker exec -e PYTHONPATH=/app hoberadius python ...` ergonomics. A future PR should add a `hr-admin` wrapper.
* sqlite3 missing in the image. A future PR should add it to the Dockerfile for diagnostics.
* The general "deploy.sh hardening" — there's still room for more validation, dry-run output, and explicit health-checks.

These are not blockers, just follow-ups.
