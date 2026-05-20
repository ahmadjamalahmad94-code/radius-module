# Hobe Hub — Visual Identity & Design System

> **Single source of truth** for every visual decision across the
> Hobe Hub Radius admin. Apply this guide verbatim to any new screen,
> module, or component so the product reads as one coherent surface
> end-to-end.
>
> **Status**: Locked v1 — derived from the Card Checker rebuild
> (R13.A.x + sidebar v2 + bg-net layer).
> **Direction**: RTL-first (Arabic), LTR safe via logical properties.
> **Last updated**: 2026-05-20

---

## 1. Brand identity

### 1.1 Tone
- **Premium SaaS**, not consumer-flashy.
- **Calm, soft, purposeful** — every animation slow (≥3s), every shadow
  soft (alpha ≤ 0.12), every color light enough to keep cards readable.
- **Bilingual** — Arabic-first labels, English for code-like identifiers
  (MAC, IP, session-id, batch_code).

### 1.2 Voice
- **Direct labels**: «حالة البطاقة», «الوقت المتبقّي», «نقل للمحذوفات».
  No icons-only buttons that hide intent.
- **Honest empty states**: write «غير محدد», never silently hide a field
  the operator expects.
- **Confirm before destructive**: every red action opens a floating
  modal (`window.ccModal.confirm` with `dangerous: true`).

---

## 2. Color tokens

All colors live as CSS custom properties on `:root`. **Never inline a hex
that isn't listed here.** Add new shades to this table first, then ref
the variable.

### 2.1 Brand purple (primary)
| Token | Hex | Use |
|---|---|---|
| `--cc-brand` / `--hb-purple` | `#6B5AED` | Primary action, active item, focus ring |
| `--cc-brand-2` | `#8B7BF8` | Mid-gradient stop |
| `--cc-brand-3` | `#A99BF9` | Light-gradient stop, hero illustrations |
| `--cc-brand-deep` / `--hb-purple-deep` | `#5B4BD8` | Hover, pressed state |
| `--cc-brand-ink` | `#2E1F8C` | Heavy text on purple-soft surfaces |
| `--cc-brand-soft` / `--hb-purple-soft` | `#EDE9FE` | Active row bg, soft pill |
| `--cc-brand-soft2` | `#E8E1FB` | Hover bg, deeper soft |
| `--hb-purple-softer` | `#F4F1FE` | Hover wash, lightest soft |

### 2.2 Page / surface
| Token | Hex | Use |
|---|---|---|
| `--hb-bg-page` | `#EFEDF5` | Page canvas (lives on `html`, body is transparent) |
| `--hb-bg` | `#F5F3FB` | Sidebar surface |
| `--hb-bg-hover` | `#E6E2F2` | Sidebar item hover |
| `--hb-bg-icon-idle` | `#EBE7F4` | Idle icon tile in sidebar |
| `--cc-card-bg` | `#FFFFFF` | Card / panel surface |
| `--cc-tint-bg` | `#FAFAFA` | Inner panel band |

### 2.3 Text
| Token | Hex | Use |
|---|---|---|
| `--cc-text` / `--hb-text` | `#1F2937` | Primary body text |
| `--cc-text-soft` / `--hb-text-soft` | `#475569` | Secondary text, sub-items |
| `--cc-text-mute` / `--hb-text-muted` | `#94A3B8` | Muted hint, placeholder, faded values |
| `--hb-text-faint` | `#B6BCC8` | Bullets, separators |

### 2.4 Borders
| Token | Hex | Use |
|---|---|---|
| `--cc-border` / `--hb-border` | `#EEEDF3` / `#ECE9E0` | Default card border |
| `--cc-border-2` / `--hb-border-strong` | `#E2DBC7` / `#E3E1EC` | Stronger divider, hover border |

### 2.5 Semantic
| Token | Hex | Use |
|---|---|---|
| `--hb-green` / `--hr-green` | `#22C55E` / `#26B673` | Success, live session, ACK |
| `--hb-amber` / `--hr-orange` | `#F59E0B` / `#F39C12` | Warning, pending |
| `--hb-red` / `--hr-red` | `#EF4444` / `#E84A4A` | Danger, destructive, NAK |
| `--hr-blue-info` | `#3D8DD6` | Info accent |

### 2.6 Status icon-tile palette (action cards)
Used on `.cc-action-icon.<color>`:
```css
.purple { background: #EEE9FE; color: #2E1F8C; }
.green  { background: #DEF3E5; color: #157F4E; }
.amber  { background: #FCEFC9; color: #7A4F02; }
.cyan   { background: #DEF3F8; color: #0F627A; }
.blue   { background: #E3EDF9; color: #1E5C9F; }
.red    { background: #FDE5E5; color: #9B1C1C; }
.grey   { background: #EDEAE1; color: #5B6470; }
.teal   { background: #D2F1EC; color: #0B6E5B; }
```
**Color → meaning mapping**:
- `amber`  — disconnect / time changes
- `cyan`   — reset / refresh
- `purple` — lock / unlock MAC
- `green`  — re-enable / success
- `blue`   — disable / info / device
- `red`    — danger / delete
- `teal`   — speed
- `grey`   — neutral, less-important

---

## 3. Typography

### 3.1 Font family
```css
font-family: "Cairo", "Tajawal", "Segoe UI", -apple-system, sans-serif;
```
- Cairo is loaded from Google Fonts at weights **400, 500, 600, 700, 800, 900**.
- Monospaced text uses `ui-monospace, "JetBrains Mono", Consolas, monospace`.

### 3.2 Scale (use exactly these sizes — no in-between values)

| Token | Size | Use |
|---|---|---|
| **Display** | 30px / 900 | Hero card number |
| **H1**      | 22px / 800 | Page title |
| **H2**      | 16px / 800 | Section heading, modal title |
| **H3**      | 14px / 800 | Card heading, sidebar item |
| **Body L**  | 14px / 700 | Strong inline value (info-value) |
| **Body**    | 13px / 600 | Default text, action title |
| **Body S**  | 12.5px / 600 | Metric label, table cell |
| **Small**   | 11.5px / 700 | Table header, metric sub |
| **Tiny**    | 11px / 800 | Field label, side-pill label (uppercase letter-spacing 0.3px) |
| **Number**  | tabular-nums | Numeric columns (`font-variant-numeric: tabular-nums`) |

### 3.3 Line height
- **Tight** (1.15) — display numbers, metric values
- **Default** (1.55) — body, info values
- **Loose** (1.65) — modal body, multi-line paragraphs

---

## 4. Spacing & layout

### 4.1 Spacing scale (px)
Use these values **only**: 2, 4, 6, 8, 10, 12, 14, 16, 18, 20, 24, 28, 32.
No 5, 7, 9, 13 — those are accidents.

### 4.2 Grid containers
- **Page max-width**: `1180px`, centered (`margin: 0 auto`).
- **Sidebar**: `260px` opened, `72px` collapsed.
- **Topbar**: `50px` height.
- **cc-grid-2**: two equal columns side-by-side, gap `14-18px`.

### 4.3 Border radius scale
| Token | Value | Use |
|---|---|---|
| `--cc-r-sm` / `--hr-r-sm` | 4-6px | tiny chips, pills |
| `--cc-r` / `--hr-r` | 6-8px | inputs, small buttons |
| `--cc-r-lg` / `--hr-r-lg` | 8-10px | section headers, soft buttons |
| `--cc-r-tile` | 14px | metric tiles, action cards |
| `--cc-r-card` | 16-18px | content cards, modals |
| `--cc-r-hero` | 22px | hero card |
| `--cc-r-pill` | 999px | full pill (status badges, mode toggle) |

### 4.4 Shadow scale
| Token | Value | Use |
|---|---|---|
| `--cc-sh-card` / `--hr-shadow-sm` | `0 1px 0 rgba(15,23,42,.03), 0 6px 18px rgba(15,23,42,.05)` | Resting card |
| `--cc-sh-card2` / `--hr-shadow` | `0 1px 0 rgba(15,23,42,.03), 0 10px 26px rgba(15,23,42,.08)` | Hovered card |
| `--cc-sh-hero` | `0 14px 30px rgba(74,54,201,.22), 0 4px 10px rgba(74,54,201,.10)` | Hero card (purple-tinted) |
| `--hr-shadow-pop` | `0 8px 22px rgba(0,0,0,.12)` | Popover, modal |
| **Brand glow** | `0 4px 12px rgba(107,90,237,.30)` | Hover on primary button |

---

## 5. Components

### 5.1 Cards / panels
```html
<section class="cc-section">
  <header class="cc-section-head">
    <span class="cc-section-head-icon"><i class="fa-solid fa-..."></i></span>
    <span class="cc-section-head-title">…</span>
  </header>
  <div class="cc-section-body">…</div>
</section>
```
- White surface, 16px radius, soft shadow.
- Header has a square icon-tile (28×28, soft purple) + title + optional
  right-side link.

### 5.2 Metric tile
- White, 14px radius, 12-14px padding.
- Layout: **label (top, 11-12.5px)** + **value (18-22px, 800)** + optional **sub (10.5-11.5px, muted)** on the right; icon-tile on the left.
- Grid: 5×2 (`.cc-metrics-2rows`) or 4×1 default; tiles auto-fit between 170-190px min.

### 5.3 Action card (operations grid)
- Fixed height **78px**.
- Layout: icon-tile (42×42, colored bg) + body (title + sub) on the start side.
- Border `--cc-border`, hover translateY(-2px) + purple shadow.
- **Never** an inline form input — always opens a floating modal.

### 5.4 Pill / badge family
| Variant | Bg | Fg | Border | Use |
|---|---|---|---|---|
| `.cc-pill-green` | `#DEF3E5` | `#157F4E` | — | Live, online |
| `.cc-pill-amber` | `#FCEFC9` | `#7A4F02` | — | Warning |
| `.cc-pill-purple` | `#EEE9FE` | `#2E1F8C` | — | Brand |
| `.cc-pill-blue` | `#E3EDF9` | `#1E5C9F` | — | Info |
| `.cc-pill-grey` | `#EDEAE1` | `#5B6470` | — | Neutral, ended |
| `.cc-pill-red` | `#FDE5E5` | `#9B1C1C` | — | Danger |
| `.cc-card-side-row` | `rgba(255,255,255,.10)` | `#fff` | `rgba(255,255,255,.18)` | Frosted on hero |

### 5.5 Floating modal (`window.ccModal.*`)
- Overlay `rgba(15,23,42,.45)`, blur if available.
- Box: 460px max-width, 16px radius, big drop-shadow.
- Header: gradient icon-tile (purple by default; red for danger, amber for warn, green for success) + title + close X.
- Buttons: cancel (soft purple), confirm (solid purple, or red for `dangerous`).
- Escape + backdrop click close.
- Five flavors: `confirm`, `reasonForm`, `softDelete`, `macPicker`, `speedForm`.

### 5.6 Wide table (sessions)
- Horizontal-scroll wrapper, min-width 1180px so 12 columns never crush.
- Sticky thead, `text-align: center` on every column (headers + cells).
- Live rows: 3px green left-border + faint green tint.
- Hover row tints purple-50.
- `.mono` cells force LTR direction so IP/MAC digits group.

### 5.7 Inputs
- Default: 38-42px height, 10px radius, soft purple-soft bg, on focus white bg + 3px purple ring `0 0 0 3px rgba(107,90,237,.12)`.
- Placeholder `#94A3B8` italic.

---

## 6. Sidebar (`.hb-side`)

- Width 260px / 72px, white surface (`--hb-bg`), soft border + shadow.
- Sticky to **right** in RTL (`inset-inline-start: 0`).
- Brand block at top: 38×38 gradient Ho tile + "Hobe Hub" + sub.
- Items: 28×28 icon-tile + label, 10px radius, hover lightens, **active** = `--hb-purple-soft` bg + `--hb-purple` text + filled purple icon-tile.
- Section heads: collapsible, chevron rotates 180° when open, vertical guide line connects sub-items.
- Collapse FAB (collapsed state): 26×26 solid purple, centered under logo, white border + glow.
- localStorage keys: `hobe_sidebar_collapsed`, `hobe_sidebar_open_sections`.

---

## 7. Topbar (`.topbar`)

- 50px height, white bg, soft bottom border.
- Sits BESIDE the sidebar (`inset-inline-start: var(--hb-side-current-w)`), not above it.
- `flex-direction: row-reverse` → brand-area (or empty in checker) on visual RIGHT, account pills on LEFT.
- Tenant + user pill: soft purple bg, purple gradient avatar.

---

## 8. Animations

### 8.1 Durations
- **Fast** (140-180ms) — hover, button press, pill toggle.
- **Medium** (200-240ms) — modal open, sidebar collapse.
- **Slow** (3-5s) — node pulse, packet drift.
- **Ambient** (60-75s) — background orb drift.

### 8.2 Easing
- `ease` for UI feedback.
- `ease-out` for entrances.
- `ease-in-out` for ambient back-and-forth.

### 8.3 Background-net layer (`.hb-bg-net`)
- z-index `-1`, lives between html paint and content cards.
- 5 layers: 2 drifting orbs, 6 WiFi rings (2 corners × 3 staggered), 5 pulsing nodes, 3 drifting packets, dot-grid texture.
- Honors `prefers-reduced-motion: reduce` → all motion stops.
- Hidden on print.

### 8.4 Reduced motion
**Always** wrap non-essential animations in:
```css
@media (prefers-reduced-motion: reduce){
  .my-animation{ animation: none !important; }
}
```

---

## 9. Responsive breakpoints

Use container queries (`@container cc-page (max-width: …)`) where the
page hosts a `container-type: inline-size`, fallback to media queries
elsewhere.

| Breakpoint | Behavior |
|---|---|
| > 1200px | Full 5×2 metrics grid, 4-col actions, sidebar opened |
| ≤ 1200px | 4-col actions still, metrics may shrink slightly |
| ≤ 1100px | Actions → 3 columns |
| ≤ 980px | — |
| ≤ 900px | **Sidebar becomes drawer**, hamburger appears in topbar |
| ≤ 760px | Actions → 2 columns |
| ≤ 720px | Hero search wraps, metrics → 2 columns |
| ≤ 640px | Metrics → 2 cols (5 rows of 2) |
| ≤ 540px | Hero back-button label hides, icon-only |
| ≤ 480px | Metrics → 1 column |
| ≤ 460px | Actions → 1 column |
| ≤ 380px | All grids stack |

---

## 10. RTL & directionality

- Set `<html lang="ar" dir="rtl">` at the top.
- **Logical properties only** for layout-affecting CSS:
  `inset-inline-start/end`, `margin-inline-start/end`, `padding-inline-start/end`, `border-inline-end`.
- **Physical properties** OK for:
  - `transform` (always physical)
  - `text-align` (centered preferred for tables)
  - Coordinates in SVG / canvas
- Numeric / Latin identifiers (IP, MAC, batch_code, timestamps) MUST be
  wrapped in `dir="ltr"` to read correctly.
- Icons: `fa-chevron-right/left` are physical — flip via CSS
  (`transform: rotate(180deg)`) in RTL contexts when needed.

---

## 11. Accessibility checklist

- All actionable elements reachable by keyboard.
- `aria-label` on icon-only buttons (mobile close, collapse, copy).
- `role="dialog" aria-modal="true"` on modals.
- `aria-hidden="true"` on purely decorative elements (`.hb-bg-net`).
- Focus ring: visible 3px purple ring on form controls.
- Color contrast: every text/bg pair tested against WCAG AA (4.5:1 body, 3:1 large).
- `prefers-reduced-motion: reduce` honored.
- `@media print` hides ambient layers and animations.

---

## 12. Hard rules — don't break these

1. **No naked hex** in any new CSS — variable first.
2. **No `!important`** unless overriding a known-deeper selector from
   a legacy CSS file (annotate with a comment).
3. **No inline `style="…"`** on components in templates beyond what's
   already established (use a utility class or add to the component CSS).
4. **No `confirm()`/`prompt()`/`alert()`** — use `window.ccModal.*`.
5. **No card password in API responses** — `has_password: bool` only.
6. **No destructive action without a modal** — every red button opens
   a danger modal first.
7. **No `display: none` to "hide" empty data** — show "غير محدد" with
   the `.cc-card-side-empty` / `.cc-card-price-empty` muted style.
8. **No new font weight/size** outside the scale in §3.
9. **No new color** outside the tokens in §2.
10. **No animation faster than 140ms or slower than 75s.**
11. **Every page** extends `admin/_admin_layout.html` and gets the
    background-net + sidebar + topbar for free — don't duplicate them.
12. **Soft-delete is the default** — `delete_permanent` is recycle-bin-only.

---

## 13. File ownership

| Concern | File |
|---|---|
| Page chrome (layout, topbar, footer, bg-net) | `app/static/css/admin_layout.css` |
| Sidebar v2 | `app/static/css/sidebar_v2.css` |
| Card Checker + its modals/tables | `app/static/css/cards_checker_v2.css` |
| Sidebar JS (collapse, drawer, sections) | `app/static/js/sidebar_v2.js` |
| Card Checker JS (search, mode, modals) | `app/static/js/cards_checker_v2.js` |
| Layout template | `app/templates/admin/_admin_layout.html` |
| Sidebar partial | `app/templates/admin/_sidebar.html` |
| Card Checker page | `app/templates/radius/cards_checker_v2.html` |

When in doubt: **extend the existing file**, don't fork a new one.

---

## 14. Component examples (copy-paste starter)

### 14.1 Card with header
```html
<section class="cc-section" data-cc-section="my-section">
  <header class="cc-section-head">
    <span class="cc-section-head-icon"><i class="fa-solid fa-…"></i></span>
    <span class="cc-section-head-title">عنوان البطاقة</span>
    <span class="cc-section-head-link">إجراء جانبي ←</span>
  </header>
  <div class="cc-section-body">
    <!-- content -->
  </div>
</section>
```

### 14.2 Action button row (4 buttons)
```html
<div class="cc-actions" style="grid-template-columns:repeat(4,minmax(0,1fr))">
  <form method="post" action="…">
    <button class="cc-action" type="button"
            onclick="window.ccModal.confirm({
              icon:'warn', title:'…', body:'…',
              confirmText:'تأكيد',
              onConfirm: () => this.closest('form').submit()
            });">
      <span class="cc-action-icon amber"><i class="fa-solid fa-…"></i></span>
      <div class="cc-action-body">
        <div class="cc-action-title">العنوان</div>
        <div class="cc-action-sub">وصف فرعي</div>
      </div>
    </button>
  </form>
  …
</div>
```

### 14.3 Metric tile
```html
{{ metric('عنوان',
          '42',
          'icon-name',     {# FA without the 'fa-' #}
          'purple',        {# color: purple|green|amber|cyan|blue|red|teal|grey #}
          'نص فرعي',
          'data.field') }}
```

### 14.4 Confirm modal call
```js
window.ccModal.confirm({
  icon: 'warn',           // 'info' | 'warn' | 'danger' | 'success'
  title: 'عنوان قصير',
  body: 'الـ <strong>HTML</strong> مسموح هنا.',
  confirmText: 'تأكيد',
  cancelText: 'إلغاء',
  dangerous: false,       // true → red confirm button
  onConfirm: () => { /* … */ }
});
```

---

## 15. Future scope (do NOT add yet)

These are explicitly **out of scope** for v1; revisit only when a real
use-case lands:

- Dark mode (token table assumes light surfaces).
- Multi-tenant theming (variable overrides per `body[data-tenant=X]`).
- Iconography swap to outline / duotone variants.
- Right-side action drawer (currently every action opens a modal).
- Bulk actions on tables (sessions table is read-only).
- Mobile-specific layouts beyond the existing drawer/wrap rules.

---

**Maintenance**: When you add a token, component, or break a rule —
update this file in the same commit. If the file gets out of sync with
the CSS, the rule is: **the CSS wins, the file gets fixed next.**
