# accel-ppp — v6 router management tunnel (SSTP/PPTP)

RouterOS 6 has no WireGuard, so the always-on management tunnel
(router → RADIUS VPS) is served by **accel-ppp**: SSTP on :443 (default) and
PPTP on :1723, authenticating `rtr-*` accounts against the local FreeRADIUS,
with CoA/DAE on :3799.

## What lives here

| File | Purpose |
|------|---------|
| `install-accel-selfsigned.sh` | Idempotent installer. Generates `/etc/accel-ppp.conf` from panel settings, mints a self-signed SSTP cert, validates, (re)starts accel, runs startup health checks. |

The config itself is **generated**, not hand-written — the single source of
truth is `app/radius/services/accel_config.py:generate_accel_conf`. The panel
can preview the exact output (and run health checks) from the SSTP credentials
page, and the installer writes the same bytes on the server. Re-running the
installer is a no-op when nothing changed.

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
sudo ./install-accel-selfsigned.sh
# optional MSCHAP probe of a provisioned account:
ACCEL_TEST_USER=rtr-ccr4 ACCEL_TEST_PASS='the-password' sudo ./install-accel-selfsigned.sh
```

All knobs (accel host, SSTP port, mgmt pool, RADIUS server/secret, cert path)
come from the panel settings (DB → env → default), so the server is configured
from the UI, never by editing files by hand.
