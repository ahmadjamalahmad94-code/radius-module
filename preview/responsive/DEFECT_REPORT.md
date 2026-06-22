# Responsive Layout Audit & Fix — Customer Admin Panel

**Branch:** `fix/responsive-layout-audit` (off `origin/main` @ 33f3b01)
**Method:** Real app booted locally on a seeded DB (22 subscribers, 28 cards,
sessions, 3 routers, 4 network devices, store users/deposits/withdrawals,
plans/batches) with an injected super-admin session and
`HOBERADIUS_LICENSE_GATE_TEST_BYPASS=1`. Every sidebar page walked at a **true
mobile viewport (390×844, deviceScaleFactor 3, isMobile, hasTouch)** and at
**desktop (1440×900)** via headless Chrome (Playwright).
Automated probe per page measured page horizontal overflow + per-table overflow.

Harness: `tools/capture_responsive_audit.py` (pages + table probe),
`tools/capture_responsive_menus.py` (top-bar + row-action menu interaction).
Raw machine report: `preview/responsive/_audit.json`. Screenshots:
`preview/responsive/<page>_390.png` / `_1440.png`, `before_*` / `after_*`.

---

## Defect classes found

### CLASS 1 — Dropdowns opening off-screen (top-bar menus)
On a 390px phone the top-bar **bell** and **notifications** menus opened mostly
off the right edge (inline `position:absolute; inset-inline-end:0;
min-width:300–350px`). Measured (before):

| Menu | left→right (px) | viewport | off-screen |
|------|-----------------|----------|-----------|
| bell-menu | 333 → 633 | 390 | **243px hidden** |
| notif-menu | 289 → 599 | 390 | **209px hidden** |
| lang-menu | 184 → 384 | 390 | fit (barely) |
| user-menu | 52 → 232 | 390 | fit |

Row-action «⋮» menus (`.uds-menu`, `position:fixed`) were **already** viewport-
clamped in `unified_design.js` — no defect.

**FIXED** → mobile rule re-anchors these menus as fixed, full-width-minus-margin
sheets docked under the top bar. After: all four sit at **8 → 382** (fully on
screen). Evidence: `before_menu_bell-menu_390.png` vs `after_menu_bell-menu_390.png`.

### CLASS 2 — Wide tables clipped, no horizontal scroll
The dominant table system (`.uds-table-wrap`) shipped `overflow-x:hidden`
(design relied on JS column-hiding that never auto-fits on a phone). Result: on
~13 list pages 4–11 columns were silently clipped with no way to reach them.
Intrinsic table widths vs the ~390px viewport (clipped, `overflow-x:hidden`):

| Page | table width | clipped |
|------|-------------|---------|
| subscribers overview | 432px+ | yes |
| cards overview (3 tables) | 432–434px | yes |
| e-cards users | 508px | yes |
| e-cards store support | 404px | yes |
| offers overview | 580px | yes |
| **offers list** | **940px** | yes |
| network speed-control | 432px | yes |
| ops events | 508px | yes |
| reports home | 508px | yes |
| **reports sessions** | **842px** | yes |
| reports financial | 432px | yes |
| admin operators | 432px | yes |
| subscriber usage | 499px | yes |

(Tables already on `.hub-table-wrap`/`.hr-table-scroll`/`.dh-table-wrap` —
subscribers 360, online, devices, pools, router-ops, device-health,
marketplace — already had `overflow-x:auto` and scrolled, but gave **no visible
affordance**, so they *looked* unscrollable.)

**FIXED** → (a) table wrappers scroll horizontally on ≤900px (`overflow-x:auto`
+ `min-width:max-content` so columns keep natural width instead of crushing);
(b) a thin branded scrollbar + inline-end fade make the swipe discoverable on
every table-scroll container (paints only when the table actually overflows —
a no-op on desktop). After-audit: the previously-`hidden` wrappers now report
`overflow-x:auto`.

### CLASS 3 — Overlapping sections
The only real overlap was the sticky in-page **section rail** (`.hr-page-nav`),
which floated over content as a clipped vertical pill on a phone. **FIXED** →
flows inline (static, full width) below ~900px. The hub KPI/section grids were
already auto-fitting to clean single/double columns (verified — left untouched).

### CLASS 4 — Missing spacing between stacked sections
`.main` already keeps 16px edge padding and cards keep a 12px stack gap on
mobile; no systematic gap defect was reproduced. The fade/scroll affordance and
inline rail keep blocks visually separated. (No change needed beyond Class 3.)

---

## Recent-modification render checks (390 + 1440)
- **«إحصائيات المتصلين» / connected-stats** — renders both viewports, no table
  overflow (`subs_connected_stats_390/1440.png`).
- **System Settings (lean, 7 tabs, no webhook block)** — renders both
  (`admin_system_390/1440.png`).
- **/webhooks** — renders both (`integ_webhooks_390/1440.png`).
- **/tunnels** — opens, HTTP 200, no 500 (`integ_tunnels_390/1440.png`).
- **Provider pages absent from a normal admin's sidebar** — the audit ran as
  super-admin (which *does* see super-only entries); provider operational pages
  (الجهات/تخصيص الأقسام/التحصيل/مختبر الدفع/طابور المزامنة) are gated
  `_PERM_GUARDED=__super__` + hidden via `_is_super`/`_NAV_PERM` and the sync
  queue link was retired — confirmed in `app/templates/admin/_sidebar.html`.

---

## Global vs per-page fixes
**All fixes are GLOBAL** — one stylesheet, `app/static/css/responsive_fixes.css`,
loaded last in `app/templates/admin/_admin_layout.html`. No per-page CSS was
needed because every list page reuses the same `.uds-table-wrap` /
`.hub-table-wrap` wrappers and the same top-bar partial. Desktop is untouched
(rules are no-ops when content fits, or scoped to mobile media queries).
