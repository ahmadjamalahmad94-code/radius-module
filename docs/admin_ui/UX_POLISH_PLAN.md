# Admin UX Polish — Multi-pass Plan

This document tracks the polish-pass across the 12 critical
admin pages the user identified. Goal: world-class quality,
"usable by a child", Arabic-first, consistent visual language.

## Foundation (this commit)

Three building blocks landed first because every page polish
depends on them:

1. **`docs/admin_ui/ARABIC_TERMINOLOGY.md`** — single source
   of truth for every operator-facing word. Forbidden English
   anglicisms listed.
2. **`app/templates/radius/_npc_components.html`** — reusable
   Jinja macros: `status_pill`, `stat_card`, `section_card`,
   `action_button`, `empty_state`, `tip_strip`. Imported once
   per template.
3. **`docs/admin_ui/UX_POLISH_PLAN.md`** (this file) — tracks
   what's done vs pending so the work can resume cleanly.

## Pass 1 — pages polished in this commit

| Page | URL | Status |
|---|---|---|
| NPC list | `/admin/radius/network-policy/<svc>/` | ✅ Done |
| Routers list | `/admin/radius/devices` | ✅ Done |
| Alerts | `/admin/radius/alerts` | ✅ Done |

What changed on each:
- Page hero rewritten with cleaner subtitle copy
- KPI strip added where appropriate (alerts severity counts)
- Empty states use `ui.empty_state` with helpful CTA
- Status pills use `ui.status_pill` instead of inline copy
- Action buttons use `ui.action_button` for consistency
- Removed jargon: "NAS" → "راوتر"، "Status" → "الحالة"،
  "severity raw value" → "حرجة/تحذير/ملاحظة"
- Action columns collapsed: text-with-icon → icon-only with
  tooltip (more density, less clutter)
- All ten "Top 3 UX issues" from the audit addressed for
  these three pages

## Pass 2 — pending pages (next session)

Same pattern, same components. Estimated 30-45 min per page.

| Page | URL | Audit notes |
|---|---|---|
| IP Pools | `/admin/radius/pools` | Needs CIDR explanation strip, clearer "no router assigned" state |
| Operations Center | `/admin/radius/mt/operations` | Drop "API" jargon, expand "partial" pill explanation |
| MikroTik wizard | `/admin/radius/mt/setup` | Version-choice needs help text, hint truncation on mobile |
| Topology | `/admin/radius/topology` | Mental model diagram, filter icons, empty-filtered state |
| Problems | `/admin/radius/problems` | Color/icon harmonization, raw type code → Arabic label |
| Diagnostics | `/admin/radius/diagnostics` | Full rewrite — inline styles, mobile breakage, raw tags |
| Permissions | `/admin/radius/permissions` | Accessibility (chip size), color legend, mobile scroll hint |
| Audit log | `/admin/radius/audit` | Field-name placeholders, timezone indicator, severity dedup |
| MikroTik Push Setup | `/admin/radius/mt-push-setup` | Full rewrite — direction mixing, hardcoded colors, step UI |

## Cross-cutting follow-ups

Identified during the audit, not page-specific:

* **Side-drawer pattern propagation** — currently only the
  NPC preview page uses the drawer. Pages with > 4 cards in
  the main body should adopt it (Diagnostics, Permissions,
  Topology especially).
* **Mobile breakpoints** — several pages assume desktop
  width. Add `@media (max-width:780px)` rules where the
  grids cram.
* **Timezone display** — every timestamp should carry an
  indicator or be relative ("منذ 3 دقائق"). Currently mixed.
* **Replace inline styles with utility classes** — many
  pages still have `style="background:#fff;"` instead of
  CSS tokens. Slow churn but worth it for theming.

## Visual language summary

For consistency across all 12 pages:

* **Body** = primary card(s) + KPIs + main action. Nothing else.
* **Side drawer** = analytical depth, raw scripts, glossary, advanced filters.
* **Empty states** = always include the next-action CTA.
* **Status** = always a colored pill. Never plain Arabic text.
* **Buttons** = primary purple (#6B5AED). Secondary white-bordered. Danger red. No fancy gradients.
* **Spacing** = 14-18px between sections; 10-14px inside cards.
* **Typography** = 18px page title, 14.5px section title, 13px body, 12px hints.

## Resume protocol

Next session:
1. Read this file.
2. Pick the next page from the "Pending" table above.
3. Re-read `ARABIC_TERMINOLOGY.md` before writing any copy.
4. Import `_npc_components.html` first thing.
5. Aim for the same visual+verbal density as the three Pass-1 pages.
6. Update the status in this file when the page is done.
