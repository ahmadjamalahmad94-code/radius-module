# Watermark — absolute backmost layer (behind everything, no exceptions)

**Branch:** `fix/watermark-backmost` (off `origin/main` @ 03d37fe).
Reinforces the earlier watermark fix per the owner: the watermark must be the
LOWEST layer, behind ALL content including any table — «ولا فوق أي جدول».
Audit harness: `tools/audit_watermark_stacking.py` (renders themed pages with a
prayer-times table + announcements board, paints the watermark solid red, and
uses `elementsFromPoint` to detect any content element the watermark floats over).

## The gap (verified)
The earlier fix set `.hr-vm-pat{z-index:0}` (fixed) + lifted card selectors to
`z-index:1`. But a `position:fixed; z-index:0` layer paints ABOVE static
(non-lifted) content — so widgets/tables NOT in the card list bled under it.
Audit (before): `wmZ=0 → BLEEDS over 3` on every themed page —
`.hr-season`, `.hr-board` (announcements), **`.hr-pray` (prayer-times table)**.

## The fix
`.hr-vm-pat{z-index:-1}` — the absolute backmost paint group: above the page
background (body color propagated to the canvas) but **below every in-flow
element** (card, fields, «دخول» button, sessions list, any table, any widget),
with NO exceptions. Belt-and-suspenders:
- `html{background:transparent}` so the body color still reaches the canvas and
  the `z-index:-1` layer stays visible above it (and behind content).
- Explicitly lift content/tables/widgets too (`table`, `form`,
  `.hr-prelogin-extras` + `> *`, `.hr-pray`, `.hr-board`, `.hr-season`,
  `.hr-weather`, `.hr-carousel`, `.hr-ticker`, … + the existing card selectors)
  to `position:relative; z-index:1` — above the watermark even in edge cases.

Companion pages inherit it automatically (they use `_inject_vertical_motif`).

## Verification
- Audit (after): `wmZ=-1 → OK (backmost)`, **0 elements** bled over — RESULT:
  ALL BACKMOST. (`after_mosque_ramadan_redwm.png` shows the red watermark layer
  strictly behind every card/table/widget; opaque content hides it, it shows only
  in the page margins.)
- Real render (`preview/hotspot_themes/after_mosque_ramadan_390.png`): the faint
  watermark is only in the background; the prayer table, announcements card, form
  and button are all clean and in front.
- Tests: `tests/test_watermark_backmost.py` (5) + updated companion test; 61
  watermark/motif/companion tests pass; 123 render-heavy hotspot tests pass.

## Files
- `app/radius/services/hotspot_templates.py` — `_inject_vertical_motif`: watermark
  `z-index:0 → -1`, `html{background:transparent}`, extended content/table lift.
- `tests/test_watermark_backmost.py` (new), `tests/test_hotspot_companion_responsive_watermark.py`
  (z-index assertion updated). Harness: `tools/audit_watermark_stacking.py`.
