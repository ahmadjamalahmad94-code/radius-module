# Hub Component Library

> Shared CSS + Jinja building blocks for every rebuilt admin page.
> The visual reference is **Card Checker** (`app/templates/radius/cards_checker_v2.html` + `app/static/css/cards_checker_v2.css`).
> This library is the portable, namespaced form of that page's look.
>
> **Status**: v1 — night build, 2026-05-20/21.
> **Owner**: Hub Radius admin.

---

## TL;DR

1. **Load the CSS.** In your page or in `admin/_admin_layout.html`:
   ```html
   <link rel="stylesheet" href="{{ url_for('static', filename='css/hub_tokens.css') }}">
   <link rel="stylesheet" href="{{ url_for('static', filename='css/hub_components.css') }}">
   ```
   `hub_components.css` itself `@import`s `hub_tokens.css`, so loading the
   components file alone is enough — but loading both via `<link>` is
   faster (parallel fetch) and helps when only tokens are needed.

2. **Import macros** from `app/templates/_components/`:
   ```jinja
   {%- from "_components/card.html"        import hub_card        -%}
   {%- from "_components/page_header.html" import hub_page_header -%}
   {%- from "_components/pill.html"        import hub_pill        -%}
   ```
   Always import directly from the source file. `_components/index.html`
   is a discovery cheatsheet only — Jinja can't transparently proxy
   `{% call %}` macros, so there's no "import all".

3. **Use them.** Every component below has a small example.

---

## File layout

```
app/static/css/
  hub_tokens.css          ← canonical CSS custom properties (--cc-*, --hb-*)
  hub_components.css      ← all .hub-* component styles
app/templates/_components/
  card.html               ← hub_card
  table.html              ← hub_table
  pill.html               ← hub_pill
  page_header.html        ← hub_page_header
  empty.html              ← hub_empty
  kpi.html                ← hub_kpi
  pagination.html         ← hub_pagination
  flash.html              ← hub_flash, hub_flash_messages
  index.html              ← discovery doc (no macros)
```

The legacy `cards_checker_v2.css` is **untouched**. It still owns its
`.cc-*` classes. Tokens are shared via the same `--cc-*` variable
namespace, so both files render identically.

---

## Tokens (hub_tokens.css)

Every value lives on `:root`. Never write a literal hex in a component.

### Brand
| Token | Value | Use |
|---|---|---|
| `--cc-brand` | `#6B5AED` | Primary action, focus ring |
| `--cc-brand-deep` | `#4A36C9` | Pressed |
| `--cc-brand-ink` | `#2E1F8C` | Heavy text on purple-soft |
| `--cc-brand-soft` | `#F2EEFE` | Soft pill bg |
| `--cc-brand-soft2` | `#E8E1FB` | Hover bg |
| `--cc-brand-softer` | `#F4F1FE` | Lightest hover wash |
| `--cc-hero-grad` | `linear-gradient(135deg,#6B5AED → #A99BF9)` | Hero, primary btn |

### Surfaces
| Token | Use |
|---|---|
| `--cc-card-bg` (`#fff`) | Card surface |
| `--hb-bg-page` (`#EFEDF5`) | Page canvas |
| `--hb-bg` (`#F5F3FB`) | Sidebar surface |
| `--cc-tint-bg` (`#FAFAFA`) | Inner panel band |

### Text / Borders
- `--cc-text` `#1F2A37` · `--cc-text-soft` `#475569` · `--cc-text-mute` `#94A0AE` · `--cc-text-faint` `#B6BCC8`
- `--cc-border` `#ECE9E0` · `--cc-border-2` `#E2DBC7` · `--cc-border-cool` `#E5E0F5` (tables)

### Radii
`--cc-r-sm` 6 · `--cc-r` 8 · `--cc-r-lg` 10 · `--cc-r-tile` 14 · `--cc-r-card` 18 · `--cc-r-hero` 22 · `--cc-r-pill` 999

### Spacing scale (use **only** these)
`--cc-sp-1` 2 · `-2` 4 · `-3` 6 · `-4` 8 · `-5` 10 · `-6` 12 · `-7` 14 · `-8` 16 · `-9` 18 · `-10` 20 · `-11` 24 · `-12` 28 · `-13` 32

### Section rhythm (locked)
- `--cc-gap-section` `16px` — vertical step between page blocks.
- `--cc-gap-inline` `12px` — in-row tile gap.

### Type
- Family: `--cc-font` (Cairo / Tajawal / system) · `--cc-font-mono`
- Sizes: `--cc-fs-h1` 22 · `--cc-fs-h2` 16 · `--cc-fs-h3` 14 · `--cc-fs-body` 13 · `--cc-fs-body-s` 12.5 · `--cc-fs-small` 11.5 · `--cc-fs-tiny` 11 · `--cc-fs-display` 30
- Weights: `--cc-fw-regular` 500 · `-medium` 600 · `-bold` 700 · `-heavy` 800 · `-display` 900

### Motion
`--cc-anim-fast` 140ms · `--cc-anim-med` 240ms · `--cc-anim-slow` 3s · `--cc-anim-ambient` 60s

---

## Components

### `hub_card` — Section card with header

```jinja
{%- from "_components/card.html" import hub_card -%}

{% call hub_card(
    title="معلومات الجلسة",
    icon="circle-info",
    link_text="عرض الكل ←",
    link_href=url_for('admin.sessions')) %}
  <p>Body content lives here.</p>
{% endcall %}
```

Parameters: `title`, `icon`, `icon_pack="solid"`, `link_text`,
`link_href`, `link_muted=False`, `soft=False`, `flush=False`,
`extra_class=""`.

- `soft=True` swaps the white surface for the light-purple gradient.
- `flush=True` drops the body padding (use for tables that own their padding).

---

### `hub_table` — Data table

```jinja
{%- from "_components/table.html" import hub_table -%}

{{ hub_table(
    columns=[
      {"label":"الاسم", "key":"name", "align":"start"},
      {"label":"MAC",   "key":"mac",  "mono":true},
      {"label":"الجلسات","key":"sessions","numeric":true},
      {"label":"الحالة","key":"status"},
    ],
    rows=users,
    empty_message="لا توجد جلسات بعد") }}
```

Parameters: `columns`, `rows`, `wide=False`, `empty_message`,
`row_class=None`, `caption=""`.

- `columns` items are dicts (`label`, `key`, `align`, `mono`,
  `numeric`, `class`) or plain strings.
- `rows` items can be dicts (looked up by `key`) or lists/tuples
  (positional).
- `row_class` is an optional callable: `lambda row: "is-live" if row.live else ""`.
- `wide=True` forces `min-width:1180px` so wide tables scroll inside
  `.hub-table-wrap`.

To insert pre-rendered HTML inside a cell, pass `value=Markup("…")` from
the view or render the pill there.

---

### `hub_pill` — Status badge

```jinja
{%- from "_components/pill.html" import hub_pill -%}

{{ hub_pill("نشطة",  variant="green") }}
{{ hub_pill("معلقة", variant="amber", icon="hourglass-half") }}
{{ hub_pill("منتهية", variant="grey", dot=false) }}
```

Variants: `grey` (default) · `green` · `red` · `yellow` / `amber` ·
`blue` · `purple` · `cyan` · `teal`.

`dot=True` (default) shows a leading colored dot; passing `icon=…`
replaces the dot with a Font Awesome icon.

---

### `hub_page_header` — H1 + subtitle + actions

```jinja
{%- from "_components/page_header.html" import hub_page_header -%}

{{ hub_page_header(
    title="إدارة العملاء",
    subtitle="إضافة، تحرير، أو حذف الحسابات",
    actions=[
      {"label":"عميل جديد", "variant":"primary", "icon":"plus",
       "href":url_for('admin.users_new')},
      {"label":"تصدير CSV", "variant":"secondary", "icon":"download",
       "href":url_for('admin.users_export')},
    ]) }}
```

If `actions=[]` (default), the header centers and stacks (matches the
Card Checker hero header). With actions, it switches to a row layout.

`back_href` adds a small back-arrow icon button on the trailing side.

Each action dict: `label`, `href` *or* `onclick`, `variant` (default
`secondary`), optional `icon` and `icon_pack`.

---

### `hub_empty` — Empty state

```jinja
{%- from "_components/empty.html" import hub_empty -%}

{{ hub_empty(
    "لا يوجد عملاء بعد",
    title="ابدأ بإضافة أول عميل",
    icon="users",
    actions=[
      {"label":"إضافة عميل","variant":"primary","icon":"plus",
       "href":url_for('admin.users_new')},
    ]) }}
```

Per DESIGN_SYSTEM §1.2: we never silently hide missing data. Use this
component instead of CSS `display:none`.

---

### `hub_kpi` — Stat tile

```jinja
{%- from "_components/kpi.html" import hub_kpi -%}

<div class="hub-stat-grid">
  {{ hub_kpi("جلسات حيّة","42",   sub="آخر تحديث الآن", accent="green", icon="signal") }}
  {{ hub_kpi("اشتراكات نشطة","1,204", accent="purple", icon="id-card") }}
  {{ hub_kpi("إيراد اليوم","$315",  accent="amber",  icon="money-bill") }}
  {{ hub_kpi("أعطال","2",  sub="خلال الساعة الماضية", accent="red", icon="circle-exclamation") }}
</div>
```

Wrap your KPI tiles in `<div class="hub-stat-grid">` to get the
canonical KPI row (auto-fit `minmax(170px, 1fr)`).

Accents: `purple` (default) · `green` · `amber` · `cyan` · `blue` ·
`red` · `grey` · `teal`.

`href` makes the tile a link (subtle hover).

---

### `hub_pagination` — Pagination + page-size

```jinja
{%- from "_components/pagination.html" import hub_pagination -%}

<form method="get" action="">
  {{ hub_pagination(
      page=page,
      total_pages=total_pages,
      base_url=url_for('admin.users'),
      per_page=per_page,
      per_page_options=[10,20,50,100],
      info_text="20 من 314") }}
</form>
```

Renders a `.hub-foot` strip with prev/next nav, current/total page,
optional page-size `<select>` (submits the enclosing form), and an
optional info text.

`base_url` is the request URL **without** `?page=…`. The component
appends `?page=N` (or `&page=N` if the URL already has a query string).

---

### `hub_flash` + `hub_flash_messages` — Toasts

```jinja
{%- from "_components/flash.html" import hub_flash_messages, hub_flash -%}

{# In _admin_layout.html, near </body>: #}
{{ hub_flash_messages() }}

{# Or render an inline notice: #}
{{ hub_flash("تم الحفظ بنجاح", category="success") }}
{{ hub_flash("فشل الاتصال بـ RADIUS", category="danger",
             title="خطأ في الشبكة") }}
```

Categories: `success` · `danger`/`error` · `warning` · `info` (default).
Each picks its color stripe + icon automatically.

`hub_flash_messages()` consumes Flask's `get_flashed_messages(with_categories=True)`
and renders a fixed `.hub-flash-region` in the corner. Drop it once in
the base layout.

---

## Component CSS classes (when you can't use the macros)

If a page needs a custom layout that the macros don't cover, you can
write raw markup against the `.hub-*` classes directly. The full list:

| Class | Notes |
|---|---|
| `.hub-page` | Page shell (container query host) |
| `.hub-page-header[.has-actions]` | Title strip |
| `.hub-header-icon-btn` | 36×36 icon-only button |
| `.hub-card[.is-soft]` + `.hub-section-head` + `.hub-card-body[.is-flush]` | Section card |
| `.hub-section-head-icon`, `.hub-section-head-title`, `.hub-section-head-link[.is-muted]` | Header parts |
| `.hub-table-wrap` + `.hub-table[.is-wide]` | Data table |
| `.hub-table td.num/.mono/.muted`, `.hub-table tr.is-live` | Cell + row modifiers |
| `.hub-pill` + `.hub-pill--{green,red,yellow,amber,blue,purple,cyan,teal,grey}` | Pill |
| `.hub-btn` + `.hub-btn--{primary,secondary,danger,ghost}` + `.hub-btn--sm` | Button |
| `.hub-form-field` + `.hub-form-field-label[.req]` + `.hub-form-field-hint[.is-error]` | Field |
| `.hub-input` / `.hub-select` / `.hub-textarea` + `.is-invalid` | Form controls |
| `.hub-form-row` | Inline label+control row |
| `.hub-empty` + `.hub-empty-icon` + `.hub-empty-title` + `.hub-empty-message` + `.hub-empty-actions` | Empty state |
| `.hub-actions-grid` + `.hub-action[.is-danger]` + `.hub-action-icon.{purple,green,amber,cyan,blue,red,grey,teal}` + `.hub-action-body/-title/-sub` | Action cards |
| `.hub-stat-grid` + `.hub-stat-card` + `.hub-stat-body/-label/-value/-sub` + `.hub-stat-icon.{accent}` | KPI |
| `.hub-foot` + `.hub-foot-{left,mid,right,info,select,nav,page}` | Pagination strip |
| `.hub-flash-region` + `.hub-flash[.hub-flash--{success,danger,warning,info}]` + `.hub-flash-icon/-body/-title/-close` | Flash |
| Utilities: `.hub-mono`, `.hub-muted`, `.hub-soft`, `.hub-tabular`, `.hub-grid-2[.is-equal]` | |

---

## Conventions

- **Naming**: every shared component CSS class is `hub-*`. Variants use
  the double-dash BEM form (`hub-pill--green`); state classes use the
  `is-*` form (`is-live`, `is-soft`, `is-danger`, `is-flush`).
- **RTL-first**: all spacing uses logical properties
  (`inset-inline-start`, `padding-inline-end`, `margin-inline-*`). Don't
  introduce `left`/`right` for layout.
- **Spacing**: only values from `--cc-sp-*` or the named rhythm tokens
  (`--cc-gap-section`, `--cc-gap-inline`). No literal `margin-bottom: 13px`.
- **Colors**: only via `--cc-*` / `--hb-*` tokens. Add a new one to
  `hub_tokens.css` first.
- **Reduced motion**: handled centrally — components opt out of
  `transition`/`animation` under `prefers-reduced-motion`.

---

## Decisions made during the night build

These were "make a reasonable choice and document it" calls (user was
asleep — flag here for daytime review):

1. **`.hub-table` zebra rows** — Card Checker's sessions table is dense
   and didn't use zebra striping. The shared `.hub-table` adds a faint
   `#FBFAFF` zebra on `:nth-child(even)` because admin tables tend to
   be longer and zebra improves scannability. The hover wash matches.
2. **`.hub-btn--danger` background** — Card Checker uses a soft red
   wash on its danger *action cards* (not a solid red button). For the
   button family I went with the solid red gradient that matches modal
   confirm buttons (per DESIGN_SYSTEM §5.5). The soft red is still
   available via `.hub-action.is-danger`.
3. **`hub_card` vs Card Checker's `.cc-section`** — The Card Checker
   section card has a white surface, not a purple gradient. The brief
   asked for "light purple gradient", which matches the *modal header*
   tile and the page canvas more than the card itself. I added an
   opt-in `.is-soft` variant (`soft=True` to the macro) that applies
   the soft purple gradient, but the default `.hub-card` stays
   white-on-cream-border to match the reference 1:1. Pages can opt in
   per-card.
4. **Pagination layout** — Card Checker's `.cc-sessions-foot` has
   live/auto-refresh toggles. The shared `hub_pagination` drops those
   (page-only concern); the per-page selector + info text are the
   generic equivalents.
5. **Discovery import** — Jinja's `caller` doesn't survive a proxy
   macro, so `_components/index.html` is a documentation file rather
   than a re-export. Import each macro directly from its source file.

When in doubt, the rule from DESIGN_SYSTEM stands: **the CSS wins, the
docs get fixed next.**
