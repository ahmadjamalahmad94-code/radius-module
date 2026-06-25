# accel-ppp — v6 router management tunnel (SSTP/PPTP)

RouterOS 6 has no WireGuard, so the always-on management tunnel
(router → RADIUS VPS) is served by **accel-ppp**: SSTP on :443 (default) and
PPTP on :1723, authenticating `rtr-*` accounts against the local FreeRADIUS,
with CoA/DAE on :3799.

## What lives here

| File | Purpose |
|------|---------|
| `accel_conf_gen.py` | **Stdlib-only** generator + CLI (no Flask, no `app`, no DB). The single source of truth for the config template. |
| `install-accel-selfsigned.sh` | Idempotent installer. Calls `accel_conf_gen.py` to write `/etc/accel-ppp.conf`, mints a self-signed SSTP cert, validates, (re)starts accel, runs startup health checks. |

The config is **generated**, not hand-written. The single source of truth is
`deploy/accel-ppp/accel_conf_gen.py:generate_accel_conf`, which imports **only
the Python standard library**. The app wrapper
(`app/radius/services/accel_config.py`) imports those same pure functions, so
the panel-rendered preview and the host-written file are **byte-identical**.

### Why stdlib-only (the Docker/host split)

accel-ppp binds the **host** :443, but the panel typically runs **inside
Docker** — so host `python3` has no Flask. The generator therefore depends on
nothing but stdlib and reads its values from CLI args / `--env-file` / env vars,
never by importing the app. Plain `python3 accel_conf_gen.py config` works on
any host with zero dependencies and no venv.

`params_from_settings()` / `export_env_lines()` (the DB-backed layer) live in
the app module; the installer can optionally `docker exec` the panel to run
`export_env_lines()` and feed those values to the host generator, so UI-set
overrides propagate without the host needing Flask.

Re-running the installer is a no-op when nothing changed (deterministic output).

## Why generated (not sed/append)

The original live config was built with manual `sed`/append edits, which
produced:

* **duplicated** `[radius]` / `[auth]` / `[client-ip-range]` sections, and
* an over-restrictive TLS policy (`ssl-protocol=tlsv1.2` +
  `ssl-ciphers=AES256-SHA`) that made MikroTik fail with
  `ssl: no common version (6)`.

The generator emits each section **exactly once** and deliberately emits **no**
`ssl-protocol` / `ssl-ciphers` lines, so OpenSSL negotiates freely and the
MikroTik-offered `ECDHE-RSA-AES256-GCM-SHA384` is selected. The installer
asserts both invariants before writing and refuses a config that violates them.

## RouterOS client settings (what the panel emits)

* **Profile = `default`** (NOT `default-encryption`). SSTP is already wrapped in
  TLS; adding PPP-layer MPPE on top makes RouterOS emit
  `ccp: failed to get flags` / `ppp_unit_send: short write` and the link never
  settles. PPTP (the fallback) keeps `default-encryption` because it is not
  TLS-wrapped and relies on MPPE.
* **Verify Server Certificate = no** — the accel server uses a self-signed cert.
* **TLS** — left to negotiate (AES256-GCM-SHA384).

## TLS cert/key — SEPARATE files (handshake fix)

accel `[sstp]` loads the certificate from `ssl-pemfile` and the **private key
from `ssl-keyfile`** — two **distinct** files. The installer mints them with
`openssl req -x509 -nodes … -keyout <key> -out <cert>` (different paths).

A single combined pemfile minted with `-keyout f -out f` is unreliable: the
cert write truncates the key, so accel gets a certificate with **no usable
private key** → the TLS handshake fails and `accel-pppd -t` warns. Defaults:
`/etc/accel-ppp/accel-selfsigned.pem` (cert) + `…/accel-selfsigned.key`
(key, `0600`). The installer re-mints the pair if **either** file is missing —
healing a legacy keyless pemfile — and the boot TLS probe connects with
`-tls1_2` and requires a real negotiated cipher + a presented certificate (so it
matches a RouterOS SSTP client and won't false-pass on `(NONE)` or false-fail).

## RADIUS provisioning

The tunnel account `rtr-<router>` is provisioned in `radcheck` with an
MSCHAP-v2-compatible secret — **both** `Cleartext-Password` and `NT-Password`
(MD4 of UTF-16-LE) — by `app/radius/services/router_mgmt_tunnel.py`. It never
reuses the admin web bcrypt/scrypt hash (irreversible → unusable for MSCHAP).
FreeRADIUS verifies the MSCHAP-v2 response via the `mschap` module
(`deploy/freeradius/mods-enabled/mschap`), which the site
(`deploy/freeradius/sites-enabled/default`) invokes — alongside `sql` to load
`radcheck` — **only** for `User-Name =~ /^rtr-/`. Regular subscriber auth is
untouched (still the REST policy engine, PAP/CHAP).

## Usage

```bash
# Full install (auto-detects the panel container + an env-file; falls back to
# env/defaults). Host python3 only — no venv, no Flask needed.
sudo ./install-accel-selfsigned.sh

# Optional MSCHAP probe of a provisioned account:
ACCEL_TEST_USER=rtr-ccr4 ACCEL_TEST_PASS='the-password' sudo ./install-accel-selfsigned.sh

# Point at an explicit env-file or panel container:
sudo HOBERADIUS_ENV_FILE=/opt/hoberadius/.env ./install-accel-selfsigned.sh
sudo HOBERADIUS_PANEL_CONTAINER=hoberadius-app ./install-accel-selfsigned.sh
sudo HOBERADIUS_NO_DOCKER_EXPORT=1 ./install-accel-selfsigned.sh   # env/defaults only
```

The generator can also be driven directly (debug / one-off):

```bash
python3 accel_conf_gen.py config                 # print the full conf
python3 accel_conf_gen.py config --out /etc/accel-ppp.conf
python3 accel_conf_gen.py print sstp_port        # one value
python3 accel_conf_gen.py --pool 10.50.0.0/24 --sstp-port 443 config
python3 accel_conf_gen.py --env-file /opt/hoberadius/.env config
```

All knobs (SSTP port, mgmt pool/gateway, RADIUS server/secret, cert path) resolve
from **CLI args → `--env-file` → env → defaults**. Defaults match the panel, and
the optional docker export pulls UI-set values — so the server is configured
from the UI/settings, never by editing files by hand.

## Fresh-VPS bootstrap (git clone → working SSTP, ZERO manual patching)

On a brand-new VPS the entire SSTP path comes up from the checked-in repo with
**no `docker cp`, no hand-editing**. The FreeRADIUS fixes (`mschap`, the rtr-
`sites-enabled/default` guard, and `mods-enabled/sql` with `sql_user_name` +
`authorize_check_query`/`authorize_reply_query`) are **baked into the
`hoberadius-freeradius` image at build time** — `deploy/freeradius/Dockerfile`
`COPY`s the whole `mods-enabled/` + `sites-enabled/` dirs. So
`docker compose build` ships a FreeRADIUS that already authenticates `rtr-*`.

```bash
# 0) clone + minimal env
git clone <repo> /opt/hoberadius && cd /opt/hoberadius
cp .env.example .env && nano .env          # set FLASK_SECRET, HOBERADIUS_PUBLIC_IP, etc.

# 1) bring up the containers — BUILDS the images, baking the FreeRADIUS config
cd deploy && docker compose build && docker compose up -d
#    (boot reconcile in the panel provisions rtr-* accounts as routers are added)

# 2) accel-ppp on the HOST — one command does lo-IP+systemd, cert/key,
#    /etc/accel-ppp.conf, the FreeRADIUS client file, and health checks:
cd /opt/hoberadius && sudo deploy/accel-ppp/install-accel-selfsigned.sh

# 3) onboard a RouterOS-6 router in the panel (SSTP), paste the generated script.
#    Done — the tunnel authenticates. Verify:
sudo ACCEL_TEST_USER=rtr-<name> ACCEL_TEST_PASS='<pw>' deploy/accel-ppp/install-accel-selfsigned.sh
```

That is the whole bootstrap. The `docker cp` commands in "Step أ" below are **only
for hot-patching an already-running container** without a rebuild (what we did
live while debugging); a fresh `docker compose build && up` never needs them.

> **Host `:443` is reserved for accel — no manual step needed.** The repo
> `docker-compose.yml` deliberately does **not** publish `443` on nginx (only
> `80` + `51000-51199`), and the active `nginx.conf` only `listen 80`. So a
> fresh `docker compose up` leaves `:443` free and the accel installer binds it
> without any unbinding. The panel is reached over **`http://<IP>`** (port 80);
> there is no origin HTTPS in this topology (a separate future task — would
> require moving SSTP off 443 first, then re-adding a 443 mapping + applying
> `deploy/nginx.tls.conf.example`). The installer's `:443` check still aborts if
> some *other* process grabs the port (it names the foreign holder).

## Deploying on the Docker VPS (hot-patch an existing box)

Topology: the panel (`hoberadius`), FreeRADIUS (`hoberadius-freeradius`) and
nginx (`hoberadius-nginx`) run in Docker; **accel-ppp runs on the HOST** and
binds host `:443`. The host repo is at `/opt/hoberadius` (git-pulled).

### Step أ — FreeRADIUS config into `hoberadius-freeradius` (mschap + site + sql)

FreeRADIUS config is **baked into `hoberadius-freeradius:latest`** (the
Dockerfile `COPY`s `mods-enabled/` + `sites-enabled/` into `/etc/freeradius/`);
it is **not** volume-mounted, so changes need an image rebuild to persist. Three
files changed: `mods-enabled/mschap` (new), `sites-enabled/default` (rtr- guard),
`mods-enabled/sql` (`read_groups=no`).

```bash
cd /opt/hoberadius && git fetch origin && git checkout agent/accel-conf-stdlib   # or after merge: main

# Immediate (test now, lost on next recreate): copy in + restart
docker cp deploy/freeradius/mods-enabled/mschap   hoberadius-freeradius:/etc/freeradius/mods-enabled/mschap
docker cp deploy/freeradius/mods-enabled/sql      hoberadius-freeradius:/etc/freeradius/mods-enabled/sql
docker cp deploy/freeradius/sites-enabled/default hoberadius-freeradius:/etc/freeradius/sites-enabled/default
docker exec hoberadius-freeradius chown freerad:freerad \
    /etc/freeradius/mods-enabled/mschap /etc/freeradius/mods-enabled/sql /etc/freeradius/sites-enabled/default
# IMPORTANT: never leave a .bak/.orig in mods-enabled/ or sites-enabled/ —
# FreeRADIUS loads EVERY file there → duplicate-module fatal. docker cp doesn't
# create backups; if you ever hand-edit in the container, edit in place.
docker exec hoberadius-freeradius sh -c 'ls /etc/freeradius/mods-enabled/*.bak /etc/freeradius/sites-enabled/*.bak 2>/dev/null && echo "REMOVE THESE" || echo "no stray .bak — good"'
docker restart hoberadius-freeradius
docker exec hoberadius-freeradius freeradius -XC 2>&1 | tail -5   # "Configuration appears to be OK"

# Permanent (so a future `compose up` keeps it): rebuild the image
cd /opt/hoberadius/deploy && docker compose build freeradius && docker compose up -d freeradius
```

### Step ب — accel-ppp on the host (one command does everything)

```bash
cd /opt/hoberadius
sudo deploy/accel-ppp/install-accel-selfsigned.sh
```
This single command now: resolves params via host `python3` + the stdlib
generator (no Flask; falls back to `docker exec -i hoberadius python3`);
**adds the gateway IP `10.50.0.1/32` to `lo` + installs a persistent systemd
unit** (`hoberadius-accel-mgmt-ip.service`, `Before=accel-ppp.service`) so accel
can bind its RADIUS source after every reboot; mints a **separate cert+key**;
writes a clean `/etc/accel-ppp.conf`; restarts accel; **auto-provisions the
FreeRADIUS client** `instance/freeradius-clients-wizard/accel-local-sstp.conf`
(`ipaddr=10.50.0.1`, secret = the same `accel-local-secret` accel uses) and
touches `.reload-trigger` so the container reloads (~5s, no restart); then runs
self-signed-safe TLS-1.2 + optional `radtest mschap` health checks.

> nginx note: `docker-compose.yml` no longer publishes `443` on nginx (it maps
> only `:80` + `51000-51199`), so host `:443` is free for accel out of the box.
> If you ever pull an older compose that still has `"443:443"`, drop it and
> `docker compose up -d nginx`. The installer aborts only if a *foreign* process
> (named in the error) holds `:443`.

### Step ج — restart the panel (runs the boot reconcile)

```bash
cd /opt/hoberadius/deploy && docker compose up -d hoberadius   # provisions rtr-* accounts
```
The boot reconcile writes each `rtr-*` account **and checkpoints the WAL**, so
the FreeRADIUS container's SQLite reader sees the rows immediately (this is the
fix for the `Invalid user: [rtr-ccr5]` blocker — see below).

### Why `Invalid user: [rtr-ccr5]` happened (WAL visibility)

The panel opens SQLite in WAL journal mode; low-volume `radcheck` writes sat in
the `-wal` sidecar and never reached the main `hoberadius.db`. The FreeRADIUS
container reads the **main** file (and as a different OS user may not attach the
app-owned `-wal`/`-shm`), so `sql` returned *notfound* for a row that "exists".
Fix: the app now runs `PRAGMA wal_checkpoint(TRUNCATE)` after every tunnel-account
write (`app/radius/db/connection.py:checkpoint_wal`, called from
`router_mgmt_tunnel`). For a one-off manual flush: `docker exec hoberadius python3
-c "from app.radius.db import connection as c; print(c.checkpoint_wal())"`.

### Capturing a live `freeradius -X` trace (if WAL wasn't the cause)

Non-disruptive: run a SECOND freeradius in the foreground on alt ports so it
doesn't fight the running one, then `radtest` against it.

```bash
# 1) snapshot the runtime SQL query the server uses (no restart):
docker exec hoberadius-freeradius sh -c 'grep -n "authorize_check_query\|sql_user_name\|SQL-User-Name" -r /etc/freeradius || true'
# 2) foreground debug instance on 18120/18121/13799 (leaves prod FR untouched):
docker exec -it hoberadius-freeradius sh -c 'freeradius -X -p 18120 2>&1' &   # Ctrl-C to stop
docker exec -it hoberadius-freeradius sh -c 'echo "User-Name=rtr-ccr5" | radclient -x 127.0.0.1:18120 auth testing123' || true
# 3) confirm the row is actually in the MAIN db file the container reads:
docker exec hoberadius-freeradius sh -c 'command -v sqlite3 >/dev/null && sqlite3 /data/hoberadius.db "SELECT username,attribute,substr(value,1,12) FROM radcheck WHERE username=\"rtr-ccr5\";" || echo "no sqlite3 in image — use the app: docker exec hoberadius python3 -c \"from app.radius.db.repos import freeradius_repo as f; print(f.list_user_check(1,\047rtr-ccr5\047))\""'
```
If step 3 shows the row in `/data/hoberadius.db` but `-X` still logs notfound,
capture the exact `rlm_sql (sql): Executing query:` line from `-X` and send it —
that reveals a query/username-mangling mismatch rather than WAL.
