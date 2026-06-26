# Task A — fixed bottom nav bar must never be covered (systemic, all themes)

**Branch:** `fix/hotspot-bottombar-overlap` (off `origin/main` @ 003445e).
Audit harness: `tools/audit_bottombar.py` (renders every pro template that has a
`.bottom-nav` with a long «إعلانات الشبكة» announcements panel, scrolls to the
bottom, and uses `elementFromPoint` to detect any element covering the bar).

## Root cause (a regression from the watermark fix)
The bar (`.bottom-nav`, `position:fixed; bottom:0; z-index:1000`) lives inside
`.mobile-container`. The earlier watermark fix lifted card/layout selectors —
**including `.mobile-container`** — to `position:relative; z-index:1`, which turned
`.mobile-container` into a **stacking context**. That trapped the bar's z-index:1000
*inside* a z-index:1 context, so the body-level injected `.hr-prelogin-extras`
(announcements, also z-index:1) painted over the entire container — covering the bar.
Audit (before): `gradient_pro/royal_night/emerald → .hr-prelogin-extras[COVERS],
.hr-board[COVERS]`.

## Fix (systemic, in `hotspot_templates.py`)
1. **Un-trap the bar:** exclude root layout containers (`main`, `.wrap`,
   `.mobile-container`) from the watermark z-index lift. The watermark is already
   `z-index:-1` (backmost), so these containers don't need lifting; with no z-index
   they stop forming a stacking context, and the bar's z-index reaches the root
   stacking order above all content.
2. **Bar on top + reserve bottom space** (`_BOTTOMBAR_SAFETY_CSS`, injected only
   when a `.bottom-nav` exists): `.bottom-nav{z-index:2147483000!important}` and
   `body`/`.content-scroll`/`.hr-prelogin-extras` get
   `padding-bottom: calc(<bar> + env(safe-area-inset-bottom))` so the last content
   (announcements, footer, free-trial) scrolls fully **above** the bar, including
   the iOS home-indicator safe area. Simple templates without a bar are untouched.

The watermark stays `z-index:-1` backmost (re-verified: ALL BACKMOST).

## Verification
- Audit (after): `gradient_pro/royal_night/emerald → barZ=2147483000, visible=True,
  overlaps=0, covers=[]`. (aurora_store/fiber_glow/swift_login have no `.bottom-nav`
  — different layout, out of scope.)
- Visual: `before_gradient_pro_390.png` (announcements overlapping the bar, home icon
  half-hidden) → `after_gradient_pro_390.png` (announcements fully above a clear,
  uncovered 5-item bar).
- Watermark backmost re-audit: `wmZ=-1 → ALL BACKMOST` (no regression).
- Tests: `tests/test_bottombar_overlap.py` (6) + 34 watermark/companion + 106
  render-heavy hotspot tests pass.

## Files
- `app/radius/services/hotspot_templates.py` — `_inject_vertical_motif` (exclude
  layout containers from lift) + `_BOTTOMBAR_SAFETY_CSS` injected in
  `_inject_responsive_safety` when a bottom bar is present.
- `tests/test_bottombar_overlap.py` (new). Harness: `tools/audit_bottombar.py`.
