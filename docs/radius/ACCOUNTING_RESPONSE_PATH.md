# RADIUS Accounting Response Path

This note records the P12 accounting ACK review.

## Scope

The target path is:

```text
MikroTik Accounting-Request -> FreeRADIUS UDP/1813 -> Accounting-Response
```

Authentication remains unchanged:

- FreeRADIUS authorization still delegates to Flask via `rest`.
- SQL auth remains disabled.
- Post-auth SQL remains disabled.
- Flask remains the source of truth for auth decisions.

## Current Findings

### UDP/1813

`deploy/docker-compose.yml` exposes:

```yaml
"1813:1813/udp"
```

`deploy/freeradius/sites-enabled/default` has an accounting listener:

```freeradius
listen {
    ipaddr = *
    port   = 0
    type   = acct
    limit {}
}
```

In the FreeRADIUS virtual server, `port = 0` means the default accounting port
for `type = acct`, which is UDP/1813.

### Accounting Section

The `accounting {}` section attempts SQL accounting, but makes SQL failure
non-blocking and ends with `ok`:

```freeradius
accounting {
    sql {
        fail     = 1
        reject   = 1
        invalid  = 1
        notfound = 1
    }
    ok
}
```

This is the intended first stabilization: SQLite lock, schema mismatch, or
temporary SQL failure should not prevent FreeRADIUS from sending
Accounting-Response.

### SQL Module

`deploy/freeradius/mods-enabled/sql` points to the same mounted SQLite DB:

```freeradius
filename = "/data/hoberadius.db"
```

The configured accounting table is `radacct`, created by the application
migrations.

### Accounting Puller

The legacy MikroTik accounting puller remains read-side/heartbeat only by
default. It does not write `radacct` unless `HOBERADIUS_ACCT_PULLER_WRITES` is
explicitly enabled.

## What P12 Changed

No FreeRADIUS runtime behavior was changed in this slice because the minimal ACK
configuration was already present. P12 adds tests and this document to lock the
expected behavior:

- UDP/1813 is exposed.
- Accounting listener exists.
- SQL accounting failure cannot block ACK.
- SQL auth remains disabled.
- Shared SQLite/radacct path is configured.
- accounting_puller writes remain disabled by default.

## What Requires a Real Lab

The following evidence still requires a running lab:

- `docker compose config`
- `docker compose logs freeradius --tail=200`
- `freeradius -X` showing Accounting-Request and Accounting-Response
- MikroTik no longer showing `RADIUS accounting request not sent: no response`

## Acceptance

For a live CHR/VPS lab, acceptance is:

- FreeRADIUS receives Accounting-Request on UDP/1813.
- FreeRADIUS sends Accounting-Response even if SQL accounting write fails.
- `radacct` persists Start/Interim/Stop when SQL is healthy.
- Existing authentication still succeeds.
- SQL auth remains disabled.
