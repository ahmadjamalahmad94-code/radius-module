# RADIUS Admin — UI Inventory Survey (Night of 2026-05-21)

Comprehensive page-level survey produced by the overnight rebuild planning pass.
Baseline standard: `cards_checker_v2.html` + `cards_checker_v2.css`.

## Totals

- **Jinja templates in `app/templates/radius/`:** 86
- **Layout partials in `app/templates/admin/`:** 2 (`_admin_layout.html`, `_sidebar.html`)
- **Flutter screens:** found at `radius-module-app/lib/features/<feature>/presentation/` (Clean Architecture).
  Features: accounting, admins, audit, auth, backups, bandwidth_schedules, cards, dashboard,
  distributors, more, nas, plans, print_templates, recycle_bin, sessions, shell, subscribers,
  system_operations. ~17 feature folders.

## Priority Distribution

| Tier | Count | Meaning |
|---|---|---|
| **P0** | 31 | Full rebuild — legacy raw UI, needs card restructuring |
| **P1** | 38 | High polish — basic modern structure, token application + minor restructure |
| **P2** | 15 | Token sweep — already modern, quick color/spacing alignment |
| **P3** | 2  | Already premium (cards_checker_v2, login) |

## Design Anchor (CSS variables, from cards_checker_v2.css)

- **Purple Brand:** `--cc-brand: #6B5AED`, soft: `#F2EEFE`
- **Semantic:** green `#22C55E`, amber `#F59E0B`, red `#EF4444`
- **Text:** `--cc-text: #1F2937`, soft: `#475569`, mute: `#94A3B8`
- **Spacing:** `--cc-gap-section: 16px`, `--cc-gap-inline: 12px`
- **Radius:** tile 14, card 18, hero 22, pill 999
- **Shadows:** `--cc-sh-card`, `--cc-sh-hero`
- **Animation:** fast 140ms, medium 240ms

## Section-by-Section Map

### 1. Dashboard (P2)
- `dashboard.html` (312L) — grid-based mini-cards. Needs unification with `cards_checker_v2.css` tokens.

### 2. Subscribers (5)
| File | Lines | Tier | Notes |
|---|---|---|---|
| `users_list.html` | 112 | P1 | Table; needs card wrapper + striped tbody. New device column already exists. |
| `users_form.html` | 512 | **P0** | Largest form (25+ fields), no sections. Highest visual impact rebuild. |
| `users_overview.html` | 78 | P1 | Mini dashboard. |
| `users_finance.html` | 185 | P1 | Per-user ledger. |

### 3. Cards Management (8)
| File | Lines | Tier | Notes |
|---|---|---|---|
| `cards_checker_v2.html` | 2265 | **P3 ✓** | GOLD STANDARD. Do not touch. |
| `cards_checker.html` | 402 | P0 | Legacy version — to be deprecated/redirected. |
| `cards_batches.html` | 308 | P0 | KPI dashboard + batches table. |
| `cards_generate.html` | 248 | P0 | Batch creation wizard. |
| `cards_batch_edit.html` | 201 | P1 | |
| `cards_list.html` | 127 | P1 | |
| `cards_overview.html` | 76 | P1 | |
| `cards_of_batch.html` | 65 | P1 | |

### 4. Plans + Bandwidth (6)
| File | Lines | Tier | Notes |
|---|---|---|---|
| `plans_form.html` | 484 | **P0** | Tier/speed rules UI. Highest rebuild ROI. |
| `bandwidth_schedules.html` | 228 | P1 | Time-based grid. |
| `plans_list.html` | 70 | P1 | |
| `plans_overview.html` | 55 | P1 | |
| `bandwidth_form.html` | 37 | P2 | Quick win. |
| `bandwidth_list.html` | 35 | P2 | Quick win. |
| `pools_*` | 28-34 | P2 | Quick wins. |

### 5. Network (9 + 1 partial)
| File | Lines | Tier | Notes |
|---|---|---|---|
| `mt_push_setup.html` | 353 | P0 | DHCP push wizard (already rebuilt today). |
| `mt_diagnostics.html` | 254 | P1 | Router connection testing UI (rebuilt today). |
| `devices_form.html` | 194 | P0 | NAS config form. |
| `_speed_rules_panel.html` | 181 | P1 | Reusable partial. |
| `mt_list.html`, `mt_form.html`, `pools_list.html`, `pools_form.html`, `devices_list.html` | small | P1-P2 | |

### 6. Accounting (6)
All small (25-86L): `invoices_form.html`, `invoices_list.html`, `vouchers_generate.html`,
`vouchers_list.html`, `accounting_ledger.html`, `accounting_reports.html` — **P1-P2** (batch processable).

### 7. Support (5)
| File | Lines | Tier |
|---|---|---|
| `tickets_list.html` | 54 | P1 |
| `ticket_view.html` | 65 | P1 |
| `services_list.html` | 48 | P2 |
| `services_form.html` | 43 | P2 |
| `tickets_form.html` | 31 | P2 |

### 8. Administration (10)
| File | Lines | Tier |
|---|---|---|
| `admins_form.html` | 153 | **P0** |
| `roles_form.html` | 169 | **P0** |
| `distributors_detail.html` | 158 | P1 |
| `admins_profile_summary.html` | 87 | P1 |
| `admins_list.html`, `roles_list.html`, `distributors_list.html`, `distributors_form.html` | 57-99 | P1-P2 |

### 9. Integration (3)
- `wh_settings.html` (41) — P2
- `wh_deliveries.html` (48) — P2
- `tokens_list.html` (54) — P2

### 10. Status + Reports (13 + 1 partial)
| File | Lines | Tier |
|---|---|---|
| `print_templates.html` | 213 | P1 |
| `sync_list.html` | 79 | P1 |
| `rep_*` (10 reports) | 23-43 each | P2 |
| `_status.html` | 117 | P2 (partial) |

### 11. Tools (9)
| File | Lines | Tier |
|---|---|---|
| `tool_radius_log.html` | 92 | P1 |
| `sessions_list.html` | 53 | P2 |
| `tool_*`, `backups.html`, `print_templates.html`, `recycle_bin.html` | 27-85 | P2 |

### 12. Auth + Tenants (3)
- `login.html` (93) — **P3 ✓** (premium)
- `tenants_list.html` (59) — P1
- `tenants_form.html` (78) — P1

### 13. Layouts (P3 ✓)
- `admin/_admin_layout.html` — already RTL-ready, modern.
- `admin/_sidebar.html` — modern nav, just rebuilt earlier.

## Flutter App Inventory

Project root: `C:\Users\Ahmad J Ahmad\Desktop\hub\radius-module-app`

Clean Architecture per feature. Each feature has:
- `data/` — datasources, models, repository impls
- `domain/` — entities, use cases, repositories
- `presentation/` — pages, widgets, providers

Features:
- accounting, admins, audit, auth, backups, bandwidth_schedules,
  cards, dashboard, distributors, more, nas, plans, print_templates,
  recycle_bin, sessions, shell, subscribers, system_operations

Rebuild scope: shared widgets + theme + presentation/pages of each feature.

## Phasing (overnight realistic targets)

| Phase | Targets | Status |
|---|---|---|
| 1. Component library | tokens.css, components.css, Jinja macros | spawned bg agent |
| 2. P0 web pages | users_form, plans_form, cards_batches, cards_generate, devices_form, admins_form, roles_form, cards_checker (legacy redirect) | in progress |
| 3. P1 web pages | lists, overviews, reports | batched |
| 4. P2 web pages | small wins | global token sweep |
| 5. Button + field audit | all rebuilt pages | follow-up pass |
| 6. Flutter theme + screens | theme + features that exist | follow-up |
| 7. Morning report | MORNING_REPORT.md | last |
