# Fixed-IP Allocation Contract (`app/radius/fixed_ip.py`)

CHR Fleet — Phase 2 / task **P2-T6**. Implements the `radius-module` side of
the fixed-IP design in
`radius-proxy/docs/chr_fleet/04_FIXED_IP_AND_SESSIONS.md` (§4.1–4.3) and the
`fixed_ip_pool` shape in `02_DATA_MODEL.md` §2.12.

## Invariant

> **A user's `Framed-IP-Address` is a property of the user, not of the CHR.**

RADIUS (`radius-module`) is the **sole** source of each user's private IP. CHRs
carry **no local pool** — RouterOS PPP/IPsec take `remote-address` from RADIUS
only. Therefore the same username gets the **same** internal IP on **every**
CHR and while roaming → existing IP-keyed routes/firewall rules keep working
(goal **G2**: no duplicate IPs).

## Authoritative table

`fixed_ip_pool` lives here (the panel mirrors it read-only):

```sql
fixed_ip_pool(
  username    TEXT    PRIMARY KEY,
  framed_ip   TEXT    NOT NULL UNIQUE,   -- hard dedupe guarantee
  customer_id INTEGER NOT NULL,
  assigned_at TEXT    NOT NULL
)
```

Created idempotently by `ensure_schema()` (called by every public function), so
the module is self-contained and needs no separate migration.

## Allocation scheme

1. **Per-customer slice.** Each customer owns a deterministic `/16` carved from
   a configurable supernet (default `10.0.0.0/8` → `10.<cust>.0.0/16`), so
   addresses never collide **across customers**. Slice =
   `customer_id mod (#slices)`-th block of the supernet.
   Override via env `HOBERADIUS_FIXED_IP_SUPERNET` / `HOBERADIUS_FIXED_IP_CUSTOMER_PREFIX`.
2. **Per-user host.** Within the slice, the host offset is derived
   **deterministically** from `sha256("<customer_id>:<username>")`, mapped into
   the usable range. Reserved offsets are excluded: `.0` (network), `.1`
   (CHR gateway / `local-address`, per 04 §4.2), and the last (broadcast).
3. **Idempotent.** If the user already has a row, that exact IP is returned —
   never recomputed, never changed («same IP forever»).
4. **Unique (defense in depth).** Two different usernames could hash to the same
   start offset; the allocator then **probes deterministically** to the nearest
   free offset. The `framed_ip UNIQUE` constraint is the hard backstop — a
   genuine duplicate `INSERT` is **rejected** by the DB (race-safe: the loser of
   a concurrent insert re-reads its own row or probes onward).
5. **Exhaustion.** If the whole slice is full, `FixedIpExhausted` (HTTP 409) is
   raised rather than reusing an address.
6. **De-allocation** only via `release_fixed_ip()` on user delete / explicit
   release — never during normal traffic.

## Public API

| Function | Purpose |
|---|---|
| `allocate_fixed_ip(username, customer_id, cfg=None) -> str` | Idempotent + unique allocation; returns the stable IP. |
| `framed_ip_for(username) -> str \| None` | Read-only lookup at Access-Accept time (no allocation). |
| `release_fixed_ip(username) -> bool` | Free the mapping (user delete / explicit release). |
| `assign_specific_ip(username, framed_ip, customer_id) -> str` | Import/migrate a specific IP; raises `RadiusConflict` if it's already taken (proves duplicate rejection). |
| `customer_network(customer_id, cfg=None)` | The customer's `/N` slice. |
| `ensure_schema()` | Create `fixed_ip_pool` if absent (idempotent). |

`cfg: FixedIpConfig(supernet, customer_prefix)` is injectable for tests
(e.g. a tiny `/29` slice to exercise probe + exhaustion deterministically).

## Where it plugs in

`framed_ip_for(username)` returns the value RADIUS writes into
`Framed-IP-Address` (attr 8) on every Access-Accept — keeping it identical across
CHRs. `allocate_fixed_ip(...)` is called once at user creation. See the kill-old
-session / CoA runtime layer in 04 §4.4 (panel + proxy side, separate tasks).

## Verification

`tests/test_fixed_ip_allocator.py` proves: idempotency (same user → same IP),
uniqueness (no two users share an IP; collisions probe to distinct addresses),
deterministic per-customer slicing + cross-customer isolation, broadcast/network/
gateway exclusion, `FixedIpExhausted` on a full slice, and `RadiusConflict` on a
duplicate `assign_specific_ip`.
