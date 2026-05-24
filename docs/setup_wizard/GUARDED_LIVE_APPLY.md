# Guarded Live Apply Engine

The setup wizard can prepare MikroTik write operations, but live apply is
disabled by default. The environment flag must be explicitly enabled:

`HOBERADIUS_SETUP_WIZARD_LIVE_APPLY=true`

Dry-run is always allowed. Apply and rollback require:

- a generated script preview
- a successful dry-run operation queue
- no blocking safety errors
- the exact confirmation phrase shown by dry-run
- an injected write adapter

The default adapter refuses execution even when the flag is enabled, so
production wiring must be deliberate.

Rollback is tag-scoped only. It may target objects with comments matching
`HOBERADIUS_SETUP:<run_id>:<step>` and must never use broad `remove [find]`.
