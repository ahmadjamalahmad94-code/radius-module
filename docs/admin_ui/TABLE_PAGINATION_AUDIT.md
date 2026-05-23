# Admin Table Pagination Audit

This audit tracks the web admin table hygiene pass. The goal is visual consistency, scroll containment, and safe pagination controls without changing business logic or database schema.

## Updated In This Pass

| Template | Route/Page | Pagination | Page size selector | Table shell applied | Backend pagination added | Notes |
|---|---|---:|---:|---:|---:|---|
| `app/templates/radius/users_list.html` | `/admin/radius/users` | yes, client-side | yes | yes | no | Existing `data-paginated` table now uses the shared shell styles and the shared pager JS recognizes `hub-table`. |
| `app/templates/radius/sessions_list.html` | `/admin/radius/online` | yes, client-side | yes | yes | no | Live sessions table remains action-safe; disconnect forms unchanged. |
| `app/templates/radius/devices_list.html` | `/admin/radius/devices` | yes, client-side | yes | yes | no | NAS test/edit/archive actions unchanged. |
| `app/templates/radius/audit_log_index.html` | `/admin/radius/audit-log` | yes, client-side | yes | yes | no | Uses existing loaded dashboard table script; no audit query changes. |
| `app/templates/radius/cards_list.html` | `/admin/radius/cards` | yes, server-side | yes | yes | no | Existing `page` and `per_page` controls preserved. |
| `app/templates/radius/recycle_bin.html` | `/admin/recycle-bin` | no | no | visual shell only | no | Spacing, stats grid, and table containment normalized; backend pagination intentionally not added in this UI-only pass. |

## Already Has Server-Side Pagination Or Specialized Controls

| Template | Route/Page | Status | Notes |
|---|---|---|---|
| `app/templates/radius/cards_batches.html` | `/admin/radius/cards/batches` | already has server-side controls | Not edited in this pass because the file had pre-existing print/export work in progress. |
| `app/templates/radius/cards_of_batch.html` | batch cards detail | specialized | Should keep batch-specific controls; apply shared table shell in a later scoped pass if needed. |
| `app/templates/radius/print_templates.html` | `/admin/radius/print-templates` | specialized designer/export UI | Out of scope for this spacing pass. |

## Needs Future Backend Pagination Review

| Template/Page family | Risk | Recommendation |
|---|---|---|
| Reports pages with summary/detail tables | medium | Add route-level `page` and `per_page` only after confirming export/report totals remain stable. |
| Recycle bin | low-medium | Safe candidate for server-side pagination later if archived rows grow. Preserve entity filters. |
| Webhook deliveries and sync queues | medium | Many are operational logs; prefer backend pagination plus newest-first indexes before visual-only pagination. |
| Financial ledgers | high | Do not add client-only pagination if totals/exports depend on complete result sets. Use backend pagination with immutable summary calculations. |

## Shared Foundation

- `app/static/css/admin_design_system.css` defines spacing variables, shared admin panels, toolbar rhythm, stat grids, table scroll containment, and pager styling.
- `app/static/js/dashboard_table.js` now initializes client-side pagination for `d-table`, `hub-table`, and `hr-data-table` tables that opt in with `data-paginated`.
