# Hotspot login + companion pages — responsive / watermark fix

**Branch:** `fix/hotspot-companion-responsive` (off `origin/main` @ 38cafaa)
**Render harness:** `tools/capture_hotspot_pages.py` — generates each page via the
real services (`hotspot_templates.render` + `hotspot_companion_pages.build_all_companions`),
substitutes RouterOS `$(...)` tokens with sample values, screenshots at a true
phone (390×844, dsf3, isMobile, hasTouch) and desktop (1440×900).
Before/after PNGs: `preview/hotspot/before_*` / `after_*`.

## Fix 1 — responsive + loop-safe on ALL companion pages
`_doc()` in `hotspot_companion_pages.py` now runs the SAME two injectors the
login page uses, on every visual companion (alogin, status, logout, error,
radvert): `_inject_vertical_motif` (background watermark) + `_inject_responsive_safety`
(viewport meta + mobile card-width + ≥44px touch targets + 16px inputs). The
loop-safe `alogin` (no auto-resubmit of credentials; redirects to `$(link-orig)`)
was already in place and is now guarded by a test. Protocol pages (rlogin,
redirect) stay minimal — no card, no injection. No native `alert()` anywhere.

## Fix 2 — watermark glyphs no longer vertically stretched
Root cause: the watermark was an inline `<svg>` (no viewBox) with a
`<rect 100%>`/`<pattern userSpaceOnUse>`; under a tall mobile viewport at high
DPR the browser scaled the SVG content **non-uniformly** → tall/elongated cups &
beans (visible only on phones, not desktop — confirmed by comparing plain-390 vs
mobile-390 captures). Fix: render the tile as a self-contained **square** SVG
(`viewBox="0 0 220 220"`) and apply it as a CSS `background-image` with an
explicit **square** `background-size:220px 220px` + `background-repeat:repeat`.
That guarantees a 1:1 tile at any width/DPR. `currentColor` (not inherited by
background-images) is baked to the accent hex in the tile.

## Fix 3 — watermark sits BEHIND an opaque card (no bleed-through)
Root cause: the watermark layer was `position:fixed; z-index:0`, which paints
**above** a statically-positioned card, so the 0.30-opacity icons showed over the
form fields / «دخول» button / sessions list. Fix: the watermark layer stays at a
low `z-index:0`, and every card container (`_RESPONSIVE_CARD_SELECTORS` +
`.hr-card` + `.hr-sessions`) is lifted to `position:relative; z-index:1`. Cards are
already solid `#fff`, so the watermark is now visible **only in the page margins
around the card**, never inside it.

## Evidence (before → after, 390px)
- `before_login_classic_390.png` → `after_login_classic_390.png`: stretched glyphs
  over the card → square glyphs, clean opaque card.
- `before_status_390.png` → `after_status_390.png`: companion had NO watermark and a
  plain card → square watermark in the margins behind an opaque, responsive card.

## Files
- `app/radius/services/hotspot_templates.py` — watermark → square CSS background
  tile + card-lift CSS; `.hr-card`/`.hr-sessions` added to shared card selectors.
- `app/radius/services/hotspot_companion_pages.py` — `_doc` applies watermark +
  responsive-safety injectors to visual companions.
- Tests: `tests/test_hotspot_companion_responsive_watermark.py` (new) +
  updated `tests/test_hotspot_vertical_motif.py` (3 assertions → new contract).
- Harness: `tools/capture_hotspot_pages.py`.
