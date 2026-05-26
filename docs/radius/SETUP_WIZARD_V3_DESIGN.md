# Setup Wizard v3 — Unified Network Onboarding Wizard

**Status:** Design proposed 2026-05-26. Implementation pending user approval.
**Replaces:** v2 (13-step paste-back), legacy `/admin/radius/mt/setup`, manual NAS registration, manual ops-room entry.
**Goal:** A non-technical user goes from "I have a new router" to "router is fully online, accepting RADIUS, visible in ops room" in **≤ 5 minutes** with **≤ 3 forms** and **one paste**.

---

## Why a v3 and not a v2.1 patch

| Problem in v2 | Root cause | Why patching can't fix |
|---|---|---|
| 13 disjoint steps with paste-back per step | State machine designed around "operator validates each phase" | Cuts can only collapse 2–3 steps; underlying paradigm stays |
| Verification reads router output but never checks VPS reality | Read-only-paste-back model | Adding server probes means new APIs, new state, new UI — a v3 in disguise |
| NAS registration is a separate UI (`/admin/radius/mt/setup`) | Built before wizard existed | Wiring it into v2 step 13 requires new state + new tests; might as well design fresh |
| Ops-room entry is a separate route entirely | Built independently after wizard | Same as NAS |
| Layout broken on wizard forms (CRIT-1) | v2 template uses old grid pattern | Fixing layout = rewriting template = same effort as new template |
| Recovery panel offers no actionable next step | Recovery service emits step keys, not diagnostic codes | Requires new catalog + worker — covered by [WIZARD_DIAGNOSTICS.md](WIZARD_DIAGNOSTICS.md) |

The combined effort to patch v2 around all five issues exceeds the effort to ship v3 cleanly. v2 stays available at `/setup-wizard-v2-legacy` for 30 days post-cutover.

---

## User-facing experience (one page, three sections)

```
┌─────────────────────────────────────────────────────────────────────┐
│  معالج إضافة راوتر جديد                          [ ابدأ من جديد ]   │
├─────────────────────────────────────────────────────────────────────┤
│  ① عرّفنا على الراوتر                                     [مكتمل ✓] │
│     اسم: مقهى الزهراء       نوع الإنترنت: PPPoE                     │
│     [تعديل]                                                          │
├─────────────────────────────────────────────────────────────────────┤
│  ② الصق الأمر التالي في تيرمنال الراوتر:                            │
│     ┌───────────────────────────────────────────────────────────┐  │
│     │ /tool fetch url="https://87.77.70.18/wz/abc123.rsc" \    │  │
│     │   mode=https output=file                                  │  │
│     │ /import file=abc123.rsc                                   │  │
│     └───────────────────────────────────────────────────────────┘  │
│                                            [ نسخ الأمر ]            │
│                                                                     │
│     في انتظار الراوتر... ◐  (00:23 منذ التوليد)                     │
│       ✓ السكربت تم تنزيله                                           │
│       ◐ بانتظار تصافح WireGuard                                     │
│       ○ بانتظار اختبار RADIUS                                       │
├─────────────────────────────────────────────────────────────────────┤
│  ③ مكتمل!                                                  [قيد العمل]│
│     • تم تسجيل الراوتر في FreeRADIUS كـ NAS                         │
│     • تم إضافته إلى غرفة العمليات                                   │
│     • نوع الخدمة المعدّة: Hotspot على ether2                         │
│     [ افتح في غرفة العمليات → ]   [ أضف راوتر آخر ]                 │
└─────────────────────────────────────────────────────────────────────┘
```

**Key design choices:**

1. **Single scrollable page** — no "next/previous" step navigation. Sections light up as data accumulates.
2. **One paste** instead of three (internet + VPN + service scripts merged into one fetchable `.rsc`).
3. **Live progress indicator** — backend polls VPS + router every 3s; the user sees `◐` spin then `✓` light up. No "click verify" button.
4. **Inline help** on every field, written for a kiosk operator (not a network engineer).
5. **Diagnostics surface as cards** with the auto-fix button right there. No "see logs" or "contact support."
6. **Mobile-responsive** — fixes UI_AUDIT CRIT-1 by using a fresh CSS grid scoped to wizard pages.

---

## Two operating modes (auto-detected)

The wizard probes for MikroTik API reachability over the VPN once the
handshake completes. Based on the result:

### Mode A — API-pull (preferred when reachable)

Used when VPS can reach router at `10.10.0.x:8728` over the VPN.

- Wizard pulls `/interface/wireguard/print`, `/radius/print`, etc. directly.
- No paste-back at all after the initial fetch.
- Full automation: wizard creates RADIUS users, hotspot profiles, walled-garden rules via API.

### Mode B — Push-only (fallback)

Used when ISP firewalls inbound API or operator disables `/ip service api`.

- Wizard generates the .rsc file as before.
- Auto-verification still works because VPS can `ping 10.10.0.x` and
  observe `wg show wg0` for handshake liveness — these don't require API.
- For deeper state (RADIUS user list, hotspot active sessions), wizard
  falls back to "paste output here" with structured parser.

The mode is detected by attempting a TCP connect to `10.10.0.x:8728`
after handshake. Result stored in `setup_wizard_runs.api_mode`.

---

## State machine (replaces 13-step)

```
┌──────────────┐
│  COLLECTING  │  user filling form (site name, ISP, service type)
└──────┬───────┘
       │ submits form
       ▼
┌──────────────┐
│   PLANNING   │  backend reserves IPs, generates credentials, builds .rsc
└──────┬───────┘
       │ .rsc generated (atomic; if fails → blocked with code)
       ▼
┌──────────────┐
│   AWAITING   │  user sees fetch URL; wait for first WG handshake
│  HANDSHAKE   │  (background: poll `wg show wg0` every 3s, timeout 5min)
└──────┬───────┘
       │ handshake observed on VPS-side
       ▼
┌──────────────┐
│  PROBING_API │  TCP connect 10.10.0.x:8728 — sets api_mode
└──────┬───────┘
       │ (regardless of result; mode is informational)
       ▼
┌─────────────────┐
│  REGISTERING_   │  writes to nas_devices + clients.conf + reload freeradius
│  NAS_AND_OPS    │  + insert into mt_operations_routers
└──────┬──────────┘
       │ atomic (rollback all if any step fails)
       ▼
┌──────────────┐
│   APPLYING   │  if API mode: push RADIUS users / hotspot config via API
│   SERVICE    │  if push mode: prompt user to paste service-script output
└──────┬───────┘
       │
       ▼
┌──────────────┐
│ VERIFYING_E2E│  end-to-end RADIUS test-auth, ping from VPS, ping to router
└──────┬───────┘
       │ all green
       ▼
┌──────────────┐
│   COMPLETE   │  router fully onboarded; show summary
└──────────────┘

Any state can transition to:
┌──────────────┐
│   BLOCKED    │  with diagnostic code from WIZARD_DIAGNOSTICS.md
└──────┬───────┘
       │ user clicks "fix automatically" OR fixes manually + clicks "retry"
       ▼
       (returns to state that was BLOCKED, re-runs check)
```

**Comparison to v2's 13 steps:**
- v2: welcome, source, details, internet-script, internet-verify, vpn-script, vpn-verify, service-path, interface-picker, hotspot-flow OR broadband-flow, added-services, final-summary
- v3: collecting → planning → awaiting-handshake → probing-api → registering-nas-and-ops → applying-service → verifying-e2e → complete

v2's "welcome / source / details" collapse into one form. "internet-script + vpn-script + service-script" become a single .rsc. The two verify steps become automated probes. NAS+ops registration is new (was missing).

---

## Database changes

### Migration 075 (proposed): `075_setup_wizard_v3_unified.sql`

```sql
-- v3 adds a unified state machine on top of existing setup_wizard_runs.
-- Backwards compatible: v2 rows keep working; v3 detected by api_mode IS NOT NULL.

ALTER TABLE setup_wizard_runs ADD COLUMN v3_state TEXT;
ALTER TABLE setup_wizard_runs ADD COLUMN v3_diagnostics_json TEXT DEFAULT '[]';
ALTER TABLE setup_wizard_runs ADD COLUMN api_mode TEXT;  -- 'pull' | 'push' | NULL
ALTER TABLE setup_wizard_runs ADD COLUMN nas_device_id INTEGER;  -- FK nas_devices.id
ALTER TABLE setup_wizard_runs ADD COLUMN ops_room_router_id INTEGER;  -- FK mt_operations_routers.id
ALTER TABLE setup_wizard_runs ADD COLUMN unified_script_path TEXT;
ALTER TABLE setup_wizard_runs ADD COLUMN unified_script_sha256 TEXT;
ALTER TABLE setup_wizard_runs ADD COLUMN handshake_first_seen_at TEXT;
ALTER TABLE setup_wizard_runs ADD COLUMN handshake_last_seen_at TEXT;
ALTER TABLE setup_wizard_runs ADD COLUMN v3_completed_at TEXT;

CREATE INDEX IF NOT EXISTS ix_setup_wizard_runs_v3_state ON setup_wizard_runs(v3_state) WHERE v3_state IS NOT NULL;

CREATE TABLE IF NOT EXISTS setup_wizard_v3_unified_scripts (
    id INTEGER PRIMARY KEY,
    tenant_id INTEGER NOT NULL,
    wizard_run_id INTEGER NOT NULL,
    short_code TEXT NOT NULL UNIQUE,           -- the abc123 in /wz/abc123.rsc
    script_body TEXT NOT NULL,
    script_sha256 TEXT NOT NULL,
    expires_at TEXT NOT NULL,                  -- TTL: 30 min after generation
    fetched_at TEXT,                            -- when router hit /wz/<code>.rsc
    fetched_user_agent TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (wizard_run_id) REFERENCES setup_wizard_runs(id)
);

CREATE INDEX IF NOT EXISTS ix_unified_scripts_code ON setup_wizard_v3_unified_scripts(short_code);
CREATE INDEX IF NOT EXISTS ix_unified_scripts_run ON setup_wizard_v3_unified_scripts(wizard_run_id);
```

---

## API surface (new endpoints, prefixed `/setup-wizard-v3/`)

| Method | Endpoint | Purpose |
|---|---|---|
| `GET` | `/setup-wizard-v3` | Single-page UI |
| `POST` | `/setup-wizard-v3/runs` | Create v3 run, returns run_id |
| `GET` | `/setup-wizard-v3/runs/<id>/state` | Poll for current state + diagnostics (frontend hits every 3s) |
| `POST` | `/setup-wizard-v3/runs/<id>/collect` | Submit collected form data (site name, ISP, etc.) |
| `POST` | `/setup-wizard-v3/runs/<id>/plan` | Trigger planning phase (generates .rsc) |
| `GET` | `/wz/<short_code>.rsc` | Public unsigned endpoint the router fetches |
| `POST` | `/setup-wizard-v3/runs/<id>/auto-fix` | Apply auto-fix for a diagnostic code |
| `POST` | `/setup-wizard-v3/runs/<id>/paste-verify` | Push-mode paste-back fallback |
| `GET` | `/setup-wizard-v3/runs/<id>/summary` | Final summary with links to ops room + NAS |
| `POST` | `/setup-wizard-v3/runs/<id>/retire` | Roll back everything (delete NAS, remove peer, etc.) |

Total: **10 endpoints** (v2 has **49** for the same job).

---

## Background worker

`setup_wizard_v3_auto_verify_worker.py` — runs continuously while any
v3 run is in `AWAITING_HANDSHAKE`, `PROBING_API`, or `VERIFYING_E2E`.

```python
def tick(run_id: int):
    run = load_run(run_id)
    if run.v3_state == "AWAITING_HANDSHAKE":
        observed = wg_show_peer(run.router_public_key)
        if observed and observed.latest_handshake_age_s < 60:
            run.handshake_first_seen_at = now()
            advance(run, "PROBING_API")
        elif age(run.planning_completed_at) > timedelta(minutes=5):
            emit_diagnostic(run, "wg_handshake_never")
            advance(run, "BLOCKED")
    elif run.v3_state == "PROBING_API":
        api_mode = tcp_check(run.router_vpn_ip, 8728)
        run.api_mode = "pull" if api_mode else "push"
        advance(run, "REGISTERING_NAS_AND_OPS")
    elif run.v3_state == "VERIFYING_E2E":
        # ... etc
```

Triggered by a lightweight scheduler (existing
`HOBERADIUS_BACKGROUND_TICK_SECONDS` knob).

---

## File inventory (what to write / what to delete)

### New files

```
radius-module/
├── app/
│   ├── radius/
│   │   ├── db/migrations/075_setup_wizard_v3_unified.sql
│   │   ├── routes/setup_wizard_v3.py                       (~250 LOC)
│   │   ├── services/
│   │   │   ├── setup_wizard_v3.py                          (orchestrator, ~400 LOC)
│   │   │   ├── setup_wizard_v3_unified_script.py           (script builder, ~300 LOC)
│   │   │   ├── setup_wizard_v3_auto_verify.py              (verification worker, ~500 LOC)
│   │   │   ├── setup_wizard_v3_auto_fix.py                 (auto-fix dispatcher, ~250 LOC)
│   │   │   ├── setup_wizard_v3_nas_registrar.py            (~200 LOC)
│   │   │   ├── setup_wizard_v3_ops_room_registrar.py       (~150 LOC)
│   │   │   └── setup_wizard_v3_diagnostics.py              (catalog loader, ~150 LOC)
│   │   └── i18n/wizard_diagnostics.json                    (AR + EN strings)
│   ├── templates/radius/setup_wizard_v3.html               (~400 lines, fixes CRIT-1)
│   └── static/
│       ├── css/setup_wizard_v3.css                         (~200 lines, scoped)
│       └── js/setup_wizard_v3.js                           (~600 LOC)
└── tests/
    ├── test_setup_wizard_v3_state_machine.py
    ├── test_setup_wizard_v3_unified_script.py
    ├── test_setup_wizard_v3_auto_verify.py
    ├── test_setup_wizard_v3_auto_fix.py
    ├── test_setup_wizard_v3_nas_registrar.py
    ├── test_setup_wizard_v3_ops_room_registrar.py
    ├── test_setup_wizard_v3_diagnostics_catalog.py
    └── test_setup_wizard_v3_end_to_end.py
```

### Modified files

```
app/radius/routes/blueprint.py            register v3 routes
app/templates/admin/_sidebar.html         link "إضافة راوتر" → v3
deploy/freeradius/clients.conf            template-based; v3 appends entries
deploy/wg-reload.sh                       no change (already perfect for v3)
```

### Files marked for deletion (after 30-day grace period)

```
app/radius/services/setup_wizard_recovery.py      → replaced by v3 diagnostics
app/templates/radius/setup_wizard_v2.html         → replaced by v3 template
app/static/js/setup_wizard_v2.js                  → replaced by v3 js
# Plus the 49 v2 route handlers in routes/setup_wizard.py (kept reachable at /v2-legacy)
```

---

## Test strategy

1. **State machine** — every transition + every BLOCKED edge.
2. **Unified script** — golden file per (ISP type × service type) combination.
3. **Auto-verify** — mock `wg show` outputs covering: never_handshaked, stale_handshake, healthy, multiple_peers.
4. **Auto-fix** — each diagnostic with `auto_fix_available=true` has a roundtrip test.
5. **NAS registrar** — atomicity: if freeradius reload fails, nas_devices row is rolled back.
6. **Ops-room registrar** — orphan cleanup, idempotency on re-run.
7. **End-to-end** — fake router stub completes full flow in test in <5s.

Existing v2 tests stay green throughout (v2 code unchanged).

---

## Rollout

| Day | Action |
|---|---|
| Day 0 | Ship v3 alongside v2; default sidebar still points to v2 |
| Day 1 | Internal team uses v3 for next 3 onboardings |
| Day 3 | If green, flip sidebar to point at v3; banner on v2 route says "moved to v3" |
| Day 30 | Delete v2 code |

Rollback: if v3 breaks, sidebar reverts to v2 in one PR.

---

## Estimated effort

| Track | Hours | Owner |
|---|---|---|
| Backend services (7 new files) | 60h | one engineer |
| Frontend (template + CSS + JS) | 35h | one engineer (parallel) |
| Tests (8 files) | 35h | follows backend |
| Manual QA + iteration | 20h | + product |
| Migration + cutover | 10h | |
| **Total** | **~160h** | ~4 weeks at 1 FTE |

This is a meaningful investment. It pays back when:
- Average onboarding time drops from ~18 min (v2) to ~5 min (v3 target)
- Support tickets about "wizard finished but router doesn't work" drop to zero
- Operators stop needing to know about NAS / ops-room as separate concepts
