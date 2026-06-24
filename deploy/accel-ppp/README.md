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

## Deploying on the Docker VPS (this deployment)

Topology: the panel (`hoberadius`), FreeRADIUS (`hoberadius-freeradius`) and
nginx (`hoberadius-nginx`) run in Docker; **accel-ppp runs on the HOST** and
binds host `:443`. The host repo is at `/opt/hoberadius` (git-pulled).

### Step أ — FreeRADIUS mschap (the `mschap` mod + updated site)

FreeRADIUS config is **baked into `hoberadius-freeradius:latest`** (the
Dockerfile `COPY`s `mods-enabled/` + `sites-enabled/` into `/etc/freeradius/`);
it is **not** volume-mounted, so changes need an image rebuild to persist.

```bash
cd /opt/hoberadius && git pull          # brings mods-enabled/mschap + new site

# Immediate (test now, lost on next recreate): copy in + restart
docker cp deploy/freeradius/mods-enabled/mschap   hoberadius-freeradius:/etc/freeradius/mods-enabled/mschap
docker cp deploy/freeradius/sites-enabled/default hoberadius-freeradius:/etc/freeradius/sites-enabled/default
docker exec hoberadius-freeradius chown freerad:freerad \
    /etc/freeradius/mods-enabled/mschap /etc/freeradius/sites-enabled/default
docker restart hoberadius-freeradius
# verify mschap loads + the rtr- guard is present + config is valid:
docker exec hoberadius-freeradius sh -c 'ls /etc/freeradius/mods-enabled/mschap && grep -c "rtr-" /etc/freeradius/sites-enabled/default'
docker exec hoberadius-freeradius freeradius -XC 2>&1 | tail -5   # "Configuration appears to be OK"

# Permanent (so a future `compose up` keeps it): rebuild the image
cd /opt/hoberadius/deploy && docker compose build freeradius && docker compose up -d freeradius
```

### Step ب — accel-ppp on the host

```bash
cd /opt/hoberadius
sudo deploy/accel-ppp/install-accel-selfsigned.sh
```
Host `python3` runs the stdlib generator directly (no Flask, no venv). If the
host has no `python3`, the installer auto-falls back to
`docker exec -i hoberadius python3` (the panel container). The `:443` check
recognises **accel-pppd** as our own server and won't false-abort on a re-run;
it only refuses a foreign holder.

> nginx note: the repo `docker-compose.yml` still lists `"443:443"` for the
> nginx service. For accel to bind host `:443`, that mapping must be removed
> from nginx (this VPS already does — nginx publishes `:80` + `51000-51199`).
> If the installer ever aborts with a foreign holder `docker-proxy` on `:443`,
> drop nginx's `443:443` and `docker compose up -d nginx`.

### Step ج — restart the panel (runs the boot reconcile)

```bash
cd /opt/hoberadius/deploy && docker compose up -d hoberadius   # provisions rtr-* accounts
```
