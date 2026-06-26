# Phase 1 — unified templates gallery + designer IA restructure

**Branch:** `feat/designer-unified-gallery` (off `origin/main` @ be97e37).

## What changed
**One unified gallery** — merged «معرض التصاميم» (library / `template_slug`) and
«قوالب جاهزة حسب نوع منشأتك» (`gallery_by_vertical` bundles) into a SINGLE gallery,
one concept, one place. The two old gallery blocks (and their dead CSS:
`.mtld-vgrid`/`.mtld-vthumb`/…, the `الفرق؟` hint) are removed.

**Top tab strip = the 7 sections (exact order the owner specified):**
شبكة عامة · كافي شوب · مساحة عمل حر · شركة · مؤسسة تعليمية · مطعم · متاجر وتسوّق.
- Horizontal pill tab strip at the top; **only ONE section's templates show at a
  time** (others get `hidden`) — they never stack on top of each other.
- Clicking a tab shows only that section; default tab = the section of the
  currently-active design.
- **4–5 template thumbnails per section** (route enforces it; a test asserts 4–5).

**Designer IA, clean and labeled:** ① التصاميم (pick design) → ② تخصيص التصميم —
الألوان والخطوط (restyle) → ③ الإضافات — محتوى التصميم (edit content blocks).

**③ Addons surfaced (un-buried).** The «الإضافات الاختيارية» panel was orphaned —
`data-mtld-sec="addons"` with **no matching tab**, so it never displayed (literally
the owner's "buried" complaint). Removed the dead gating → it's now an always-visible
③ section with a clear heading + top divider. The panel + its JS are unchanged and
keep working.

**Nothing broke:** selecting a template still drives the single live preview, colors
apply, addons edit, save/deploy unchanged (existing tests pass).

## How sections are populated (Phase 1)
Each section maps to 4–5 of the existing ~21 base designs (route
`_TEMPLATE_SECTIONS`), e.g. مطعم → photo_backdrop / gilded_hospitality /
crimson_luxe / food_cobrand. These are placeholders wired into the right sections;
the **30 premium rebuilds come in waves** (Phase 2), each hand-built and reviewed.

## Verification
- Render 200; `data-mtld-gtabs` present; 7 `data-mtld-gsec` panels, **6 hidden /
  1 visible**; 34 `template_slug` cards; addons section visible.
- Screenshot: `gallery_restaurant_tab.png` (مطعم tab active → only its 4 designs;
  ② colors + ③ addons below).
- Tests: `tests/test_designer_unified_gallery.py` (12) + updated thumb/quality
  tests; 57 designer tests pass.

## Files
- `app/radius/routes/mt_login_designer.py` — `_TEMPLATE_SECTIONS` + `_template_sections()`.
- `app/templates/radius/mt_login_designer.html` — unified tabbed gallery, IA labels,
  ③ addons un-gated, old galleries removed, gallery-tab JS.
- `app/static/css/mt_login_designer.css` — `.mtld-gtabs`/`.mtld-gsec`/`.mtld-addons-section`.
