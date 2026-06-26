# Hotspot login designer — quality fixes (upload button + theme contrast/overflow)

**Branch:** `fix/hotspot-designer-quality` (off `origin/main` @ 3080e13)
Harnesses: `tools/capture_hotspot_themes.py` (gallery themes), the earlier
`tools/capture_hotspot_pages.py`. Before/after PNGs in `preview/hotspot_themes/`.

## Fix A — "upload attachments" button did nothing
**Broken:** the custom-design upload drop zone in `mt_login_designer.html`
(«اسحب ملف التصميم هنا أو انقر للاختيار» + «اختيار ملف») used a `<span>` button
and a `hidden` `<input type=file>`, but **no JS wired the click to open the file
picker** (only the logo upload was wired). Clicking did nothing; the route
(`mt_login_designer_custom_upload`) was fine — the front end never fired.
**Fix:** wired the drop zone — click on the zone/button → `input.click()`; file
`change` → shows the filename; plus drag-&-drop and keyboard (Enter/Space). Upload
still submits the existing form to the existing route (no backend change). The
video/font asset uploads and logo upload were already working (visible inputs).

## Fix C1 — low-contrast text on saturated/dark themes (systemic)
**Root cause:** theme-override addons (`theme_seasonal`, `theme_dark`) repaint the
card with a dark/translucent background and set `body{color:#fff}`, but the base
template's *direct* rules `h1{color:<accent>}` and `.card .welcome{color:#64748B}`
win over inheritance — so the title stayed accent-green and the subtitle stayed
gray on a green/dark card → unreadable (the reported Ramadan/mosque screenshot).
**Fix:** both theme addons now emit *direct* `!important` rules forcing the
heading, subtitle, labels, helper text and links to readable light colors
(`#fff` / `rgba(255,255,255,.92)` for seasonal; `#f1f5f9` / `#cbd5e1` for dark),
plus a soft text-shadow and lighter input placeholders. Applies to every template
these themes are layered on (audited via the gallery render harness).

## Fix C2 — clipped/overlapping side widgets (systemic)
**Root cause:** most templates center the card with `body{display:flex}`. The
injected pre-login fragments (prayer times `.hr-pray`, announcements `.hr-board`,
seasonal badge `.hr-season`, etc.) became **flex-row siblings** of the card, so a
row of card + fixed-max-width widgets overflowed and clipped off-screen (9–13
elements off the viewport on mobile).
**Fix (single point in `render_login_surface`):** wrap all pre-login fragments in
one `.hr-prelogin-extras` container that is `flex:0 0 100%` (so it wraps to its
own full-width line under the card) and stacks its children in a centered column;
`body{flex-wrap:wrap}` lets it drop below the card. Widgets keep their own
`max-width`, so they're contained and reflow **below** the card on every width —
no overflow, no overlap. After-render probe: off-screen elements 9/13/6 → **0**.

## Item B — «عرض منتصف الأسبوع» / «إعلانات المسجد» editability
Investigated: these are **not hardcoded-uneditable**. «إعلانات المسجد» is the
`announcements` addon's `title`/`body`; «عرض منتصف الأسبوع» is the
`scheduled_content` addon's `message`. Both are editable in the designer's
**«الإضافات الاختيارية»** panel (toggle + labeled fields) — the gallery preset
just fills sample values, fully overridable or removable (disable the addon).
The gap is discoverability, not a missing control. (Test asserts the
announcements `title`/`body` fields exist and drive the output.)

## Evidence (before → after, 390px)
- `before_mosque_ramadan_390.png` → `after_mosque_ramadan_390.png`: green-on-green
  title/subtitle + side widgets clipped left → white readable text + widgets
  stacked, contained below the card.
- `before_restaurant_ramadan_390.png` → `after_restaurant_ramadan_390.png`: same.

## Files
- `app/templates/radius/mt_login_designer.html` — wire custom-upload drop zone JS.
- `app/radius/services/hotspot_addons_themes.py` — readable-text overrides for
  `theme_seasonal` + `theme_dark`.
- `app/radius/services/hotspot_surfaces.py` — wrap pre-login fragments for
  containment/stacking.
- Tests: `tests/test_hotspot_designer_quality.py`. Harness:
  `tools/capture_hotspot_themes.py`.
