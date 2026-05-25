# HobeRadius Business OS - Execution Rules

These rules apply to the Business OS prompt sequence and future implementation
work inside `radius-module`.

## Repository Rules

- Work inside `radius-module` unless a prompt explicitly says otherwise.
- Do not touch `radius-module-admin`.
- Do not touch Flutter or `radius-module-app` unless a prompt explicitly
  requests it.
- Do not use `git add .`.
- Stage explicit files only.
- Report unrelated dirty files and leave them unstaged.
- Keep commits focused and readable.
- Push after each successful prompt commit when remote access is available.
- Create a handoff under `docs/handoffs/` for every prompt.

## Protected Systems

- Existing RADIUS authentication must not be rewritten.
- Existing RADIUS accounting behavior must not be broken.
- Do not re-enable FreeRADIUS SQL auth.
- Do not break existing `/api/v1` contracts.
- Do not introduce live MikroTik or server mutation unless a prompt explicitly
  asks for it and safety gates exist.
- Financial records must not be hard-deleted.
- Ledger records must be append-only.
- Secrets must not be committed.

## Test Rules

- Inspect before editing.
- Run prompt-specific tests.
- At minimum for each prompt:
  - `python -m compileall app`
  - relevant pytest suite or documented reason if docs-only
  - `git diff --check`
  - `git status --short`
- Do not claim a test passed unless it was actually run.
- If a test fails, inspect the root cause, fix within scope, and rerun.
- Do not skip, xfail, or weaken tests to make a prompt pass.
- If a failure is out of scope and cannot be safely fixed, stop and document the
  blocker. Do not commit broken partial work.

## Prompt Sequence

- Execute one prompt at a time.
- Do not start the next prompt automatically.
- Each prompt must read the previous handoff files requested by that prompt.
- Each prompt must preserve prior safety decisions unless explicitly superseded.
- The current prompt result determines readiness for the next prompt.

## Commit Naming Convention

Use the commit message specified in each prompt. If a prompt does not specify a
message, use:

```text
<type>: <short business-os change>
```

Preferred types:

- `docs`
- `feat`
- `fix`
- `test`
- `chore`

## Failure Handling

When a failure occurs:

1. Capture the exact command and error.
2. Identify whether it is in scope.
3. If in scope, patch and rerun.
4. If out of scope, document the blocker in the handoff.
5. Do not hide the failure in the final report.
6. Do not commit a known-broken state.

## Financial Safety Rules

- Use immutable ledger entries.
- Use price snapshots for revenue-producing actions.
- Use reversals/corrections instead of editing history.
- No hard delete for financial data.
- Wallet balance changes must record before/after balances.
- Financial actions must record actor, target, reference, and event metadata.

## Access And Scope Rules

- Backend services must resolve actor scope before returning or mutating data.
- Managers and distributors see only permitted scopes.
- Subscriber and card-user portal routes are self-scoped only.
- Admin/global access must still be audited.

## Client Boundary Rules

- Backend is the source of truth.
- Web UI and Flutter clients display backend decisions.
- Clients do not calculate ledger, wallet, revenue, or permission truth.

## Handoff Requirements

Each handoff must include:

- prompt name,
- what changed,
- files changed,
- tests and exact results,
- commit hash,
- push status,
- limitations,
- next prompt readiness.
