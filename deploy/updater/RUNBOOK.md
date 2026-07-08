# HobeRadius — Host Self-Update Agent · RUNBOOK

**What this is:** the panel runs inside Docker and **cannot rebuild itself**.
When the owner clicks **«حدّث الآن»** in *تحديث النظام*, the app writes a
request marker to a host-mounted directory. This agent — installed by the owner
on the **host, as root** — watches for that marker and performs the real,
verified update (backup → jump to latest → run all migrations → health-check →
rollback on any failure).

The update itself is **OPT-IN and per-instance**: it happens only after the
owner-customer confirms in their own panel. The *agent* that carries it out,
however, is now **installed automatically by provisioning** — a fresh VPS
(`curl … /deploy/install.sh | sudo bash` → `provision-fresh-vps.sh`) wires the
host agent with **zero manual steps**, and `deploy/deploy.sh init|upgrade`
re-runs the installer so existing installs pick it up on their next deploy. The
manual steps in §2 remain valid as a **fallback / reference** (e.g. a host
provisioned before this change, or a bespoke setup).

> **Auto-install:** `deploy/provision/provision-fresh-vps.sh` (step «7ب») and
> `deploy/deploy.sh` (`init` + `upgrade`) both call
> `deploy/updater/install-updater.sh`, which is idempotent: it ensures the
> shared dir, installs `hoberadius-updater.sh` to `/usr/local/bin`, writes
> `/etc/hoberadius/updater.env` (only if absent — operator overrides survive),
> and installs + enables the systemd `.service`/`.timer`. It degrades gracefully
> on non-systemd hosts and never aborts a provision/deploy.

---

## 1. Paths & contract

| Path | Who writes | Purpose |
|---|---|---|
| `/var/lib/hoberadius/update-request.json` | **container** (panel) | the owner's confirmed request |
| `/var/lib/hoberadius/update-status.json`  | **host agent** | progress the panel polls |
| `/var/lib/hoberadius/update-request.done.json` | host agent | last consumed request (archive) |
| `/var/log/hoberadius-updater.log` | host agent | full run log |
| `/opt/hoberadius/backups/preupdate-*.db.gz` | host agent | verified pre-update DB backup |

The shared dir is bind-mounted into the container by `deploy/docker-compose.yml`
(`- /var/lib/hoberadius:/var/lib/hoberadius:rw`) and created with `gid=999`
(the container's `hr` group) by `deploy/deploy.sh init` / `upgrade`.

### `update-request.json` (panel → agent)
```json
{
  "requested_version": "1.4.0",          // target; "latest"/"main"/"" → main
  "requested_by": 1,
  "requested_by_name": "owner",
  "requested_at": "2026-07-08T10:00:00Z",
  "current_version": "1.1.0",
  "install_id": "hr-…",
  "marker_schema": 1
}
```

### `update-status.json` (agent → panel)
The agent flushes this after **every stage** so the panel renders a live
progress bar + a "what's happening" log tail. All fields beyond `state`/`log`
are **additive** — an older panel ignores them, and an older marker (without
them) still renders (bar just stays at 0 until a stage arrives).
```json
{
  "state": "running",                    // queued* | running | success | failed
  "stage": "migrations",                 // machine key (start|backup|fetch|verify|build|migrations|health|done)
  "stage_label": "تشغيل ترحيلات قاعدة البيانات",  // Arabic, shown as «جارٍ: …»
  "percent": 85,                         // 0–100, monotonic per run
  "log": "10:01:02Z — …\n10:01:40Z — …", // last ~12 curated Arabic lines (tail)
  "request_at": "2026-07-08T10:00:00Z",  // echoes requested_at → ties status to request
  "requested_version": "1.4.0",
  "updated_at": "2026-07-08T10:01:40Z",  // last flush time
  "finished_at": "2026-07-08T10:02:10Z", // present on success/failed
  "updater_version": "1",
  // present only on failure:
  "error": "duplicate column x",         // the failing error text
  "failed_stage": "migrations",          // which stage died (bar freezes at its %)
  "rolled_back": true                    // code + DB were restored to the previous version
}
```
`* queued` is a panel-derived state (the marker file itself never says
"queued"): the panel shows **queued** when a request marker exists but no
status echoes its `request_at` yet, and — if it stays queued >60s — hints that
the host updater may not be installed/running.

**Stage → percent ladder:** بدء `5` → نسخة احتياطيّة `20` → سحب التحديث/git `40`
→ بناء الصورة `65` → الترحيلات `85` → فحص الصحة `95` → اكتمل `100`. On failure the
bar freezes at the stage's percent, turns red, shows the error, and (since
rollback runs) «تمّ التراجع للنسخة السابقة».

The panel computes the display state as: **queued** (marker present, agent
hasn't echoed this `request_at` yet) → **running** → **success | failed**.

---

## 2. Install (once, as root) — *fallback / reference*

> Provisioning now does all of this for you (see the note above). Follow these
> steps by hand only on a host that predates the auto-installer, or to
> re-create the wiring manually. Running `deploy/updater/install-updater.sh`
> (as root) performs steps 0–3a in one idempotent shot.

```bash
# 0) Make sure the shared dir exists (deploy.sh already does this on init/upgrade)
sudo mkdir -p /var/lib/hoberadius && sudo chgrp 999 /var/lib/hoberadius && sudo chmod 2775 /var/lib/hoberadius

# 1) Install the agent script
sudo install -m 0755 /opt/hoberadius/deploy/updater/hoberadius-updater.sh \
     /usr/local/bin/hoberadius-updater.sh

# 2) (optional) Configuration overrides
sudo tee /etc/hoberadius/updater.env >/dev/null <<'ENV'
HOBERADIUS_PROJECT_ROOT=/opt/hoberadius
HOBERADIUS_SERVICE=hoberadius
# Production: require a signed release tag before applying (see §5)
HOBERADIUS_UPDATE_REQUIRE_SIGNATURE=0
# HOBERADIUS_UPDATE_GPG_KEYRING=/etc/hoberadius/release-signing.gpg
ENV

# 3a) RECOMMENDED — systemd timer (polls every minute; exits instantly if no request)
sudo install -m 0644 /opt/hoberadius/deploy/updater/hoberadius-updater.service /etc/systemd/system/
sudo install -m 0644 /opt/hoberadius/deploy/updater/hoberadius-updater.timer   /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now hoberadius-updater.timer

# 3b) ALTERNATIVE — a long-running watch loop instead of the timer
#     sudo hoberadius-updater.sh --watch    (or wrap in your own unit)
```

Verify:
```bash
systemctl status hoberadius-updater.timer          # active (waiting)
sudo hoberadius-updater.sh --status                # {"state":"idle"} when nothing pending
tail -f /var/log/hoberadius-updater.log
```

---

## 3. Request / status flow (end to end)

1. Owner opens **تحديث النظام**, sees `current → latest` + the cumulative
   changelog, clicks **حدّث الآن** and confirms.
2. Panel writes `update-request.json` and shows **«جارٍ التحديث… لا تُغلق»**,
   polling `update-status.json`.
3. Within ≤60s the timer fires `hoberadius-updater.sh --once` (or the watch loop
   picks it up). The agent:
   - takes a **verified** SQLite backup (`.backup` + `PRAGMA integrity_check`),
   - tags the current image `hoberadius:rollback` and records the current commit,
   - resolves + (optionally) verifies the target, checks out **straight to the
     target** (a `vX.Y.Z` tag, or `origin/main` for "latest"),
   - `docker compose build --no-cache` + `up -d --force-recreate`,
   - waits for the container to become **healthy**,
   - runs **all** pending migrations in one pass,
   - re-confirms health, writes `state:success`, archives the request.
4. Panel's poller flips to **«تم التحديث»**; the owner reloads.

---

## 4. Multi-version jumps (v1 → v4 in one shot)

If releases 2, 3, 4 accumulated and the customer updates only at v4, the agent
goes **straight to the latest** — it does **not** step version-by-version:

- **Code:** one `git checkout v4` (or `origin/main`).
- **Migrations:** `run_pending_migrations()` applies **every** pending migration
  in strict filename order in a **single pass** (2, 3, 4), recording each in the
  `_migrations` table so it's applied exactly once. The runner is order-safe and
  re-runnable (idempotent) by design — see `app/radius/db/migrations_runner.py`.
- **Changelog:** the panel shows the **cumulative** notes for every release the
  customer skipped (v2+v3+v4), not just v4 — the check sends the running version
  so the central endpoint (or the local `releases[]` handling) returns the span.

### `min_version` — the one case we do NOT blind-jump
Each release advertises a hard floor `min_version`. If the customer's current
version is **below** the latest's `min_version`, a direct jump could break a
migration chain. In that case:

- the panel surfaces **«لا يمكن القفز مباشرة، يلزم تحديث وسيط»** and the confirm
  button targets the **intermediate** `min_version` instead of the latest;
- the agent updates to that intermediate version + runs its migrations; the next
  check then offers the (now-safe) jump to latest.

Normal case (current ≥ `min_version`) = direct jump to latest + run-all-migrations.

---

## 5. Trust / signature verification (do this in production)

The update payload should be **verified from the central source before applying**
— the same trust posture as the license bridge (the `license_key` is the shared
credential; HTTPS only). Two supported mechanisms:

1. **Signed git tags (recommended).** Sign each release tag
   (`git tag -s v1.4.0`) with your release key. On the host, import that key and
   set:
   ```
   HOBERADIUS_UPDATE_REQUIRE_SIGNATURE=1
   HOBERADIUS_UPDATE_GPG_KEYRING=/etc/hoberadius/release-signing.gpg
   ```
   The agent runs `git verify-tag <tag>` and **refuses** to apply if it fails.
   With `REQUIRE_SIGNATURE=1` it also refuses to update to the moving `main`
   branch (unsigned) — only signed tags are accepted.

2. **Panel manifest signature (optional extension).** Have the central endpoint
   return a signed manifest of the target (`version` + commit + signature). Add
   the verification to `verify_target()` before the checkout step. The function
   is isolated precisely so you can slot this in without touching the update flow.

With `REQUIRE_SIGNATURE=0` (default for first-run/dev) the agent does a
best-effort advisory verify if a keyring is set, and proceeds. **Turn it on for
production.**

---

## 6. Rollback (code AND DB)

On **any** failure — build error, `up` error, unhealthy container, or a
migration failing midway through a multi-version jump — the agent automatically:

1. `docker compose stop hoberadius`,
2. **restores the DB** from the verified pre-update backup (`gunzip → DB_PATH`),
3. **resets the code** to the previous commit (`git reset --hard <prev>`),
4. brings back the **exact previous image** (`docker tag hoberadius:rollback
   hoberadius:latest` + `up -d --no-build --force-recreate`; rebuilds if the tag
   is gone),
5. waits for healthy and writes `state:failed` with the reason.

So a half-applied migration set can never leave a broken schema against new code:
both sides are restored together to the last-known-good state.

If rollback itself cannot reach a healthy container, the status says a **manual
intervention** is needed and the log has the full trace — restore by hand with
`deploy/restore.sh <backup.db.gz>` and `git reset --hard <prev>`.

---

## 7. Troubleshooting

| Symptom | Check |
|---|---|
| Panel stuck on «في قائمة الانتظار» | `systemctl status hoberadius-updater.timer`; `journalctl -u hoberadius-updater -n 50` |
| Nothing happens on confirm | Is `/var/lib/hoberadius` mounted & writable by gid 999? `docker exec hoberadius ls -l /var/lib/hoberadius` |
| «فشل التحديث» | `tail -100 /var/log/hoberadius-updater.log` — the failing step is logged; the DB+code were rolled back |
| Want to retry | The failed request is not archived; fix the cause and it will re-run next tick, or `sudo hoberadius-updater.sh --once` |
| Force a manual update | still supported: `sudo bash /opt/hoberadius/deploy/deploy.sh upgrade` |
