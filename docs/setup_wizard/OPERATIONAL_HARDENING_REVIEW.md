# Setup Wizard Operational Hardening Review

Reviewed latest operational guardrails after commit `6e8d95a`.

## Reviewed

- Repository hygiene and pre-existing dirty files.
- Migrations `049`, `050`, and `051`.
- Live apply feature flag behavior.
- Operation safety validation and rollback scoping.
- Pasted router inventory parsing and secret sanitization.
- Hotspot/Broadband snapshot-aware orchestration path.
- Added services catalog fallback behavior.
- Support bundle and health output masking.
- Setup Wizard UI operational controls.

## Confirmed Safety Guarantees

- Live apply defaults off. Without `HOBERADIUS_SETUP_WIZARD_LIVE_APPLY=true`,
  apply and rollback endpoints return blocked.
- Dry-run is allowed without the live flag and persists operation rows before
  any apply attempt.
- Apply uses a setup-wizard-specific write adapter; the default adapter refuses
  execution.
- Generated apply operations reject remove, disable, reset, import, export,
  tool fetch, broad `set [find]`, and writes missing the exact
  `HOBERADIUS_SETUP:<run_id>:<step>` tag.
- Rollback remove commands are accepted only when scoped to the exact setup
  wizard tag and never with broad `[find]`.
- Pasted inventory and support bundles mask secrets, passwords, private keys,
  and radius secrets.
- Hotspot/Broadband smart planning consumes snapshot subnets and excludes
  WAN/VPN interfaces through the risk report.

## Known Limitations

- Migration `051` uses additive `ALTER TABLE ADD COLUMN` statements. This is
  safe under the project migration runner because migrations are recorded once,
  but the SQL file is not manually re-runnable on a database where those columns
  already exist.
- Router inventory parsing is intentionally tolerant, not a full RouterOS
  grammar. Unknown lines are stored as sanitized raw records.
- No real MikroTik write adapter is wired. This is deliberate for pilot safety.
- Added Services are cataloged, but anti-sharing remains `not_supported_yet`
  until a stable planner exists.
- The UI shows structured JSON output. A richer Arabic diagnostics panel can be
  added later without changing the safety model.

## Pilot Readiness Status

Ready for an internal controlled pilot in preview, pasted inventory, dry-run,
and support-bundle modes.

Not ready for unattended live customer apply. Before first live customer pilot:

1. Confirm a fresh router backup exists.
2. Confirm a management access path that does not depend on the target
   interface.
3. Enable `HOBERADIUS_SETUP_WIZARD_LIVE_APPLY=true` only for the pilot window.
4. Inject a reviewed MikroTik write adapter explicitly.
5. Run dry-run and confirm no blocking warnings.
6. Copy the exact confirmation phrase for each step.
7. Verify after every critical step.
8. Generate and archive a support bundle with secrets masked.
