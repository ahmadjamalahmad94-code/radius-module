# Login-designer preview thumbnails — compact sizing

**Branch:** `fix/designer-thumb-compact` (off `origin/main` @ 2d7db37).
Only the **designer preview miniatures** shrink — the real captive-portal pages
are untouched. Harness: `tools/capture_designer_thumbs.py` (boots the app, seeds
a router + DB super-admin, renders `/mt/<id>/login-designer`, measures + shots the
two thumbnail grids). Before/after in `preview/designer_thumbs/`.

## What changed (two thumbnail systems, shared `.mtld-mock`)
Owner ask: height −30%, width −20%, condensed inner content, no aspect distortion.

**Library picker** (`app/static/css/mt_login_designer.css`)
- `.mtld-gallery` grid `minmax(180px→144px)` (−20% width) → more per row.
- `.mtld-thumb` `aspect-ratio: 3/4 → 6/7` (box ~30% shorter relative to width).
- `.mtld-thumb-frame` live-preview `scale .52 → .42` (content scaled down, not cropped).

**P4 gallery** (`app/templates/radius/mt_login_designer.html`)
- `.mtld-vgrid` `minmax(190px→152px)` (−20% width).
- `.mtld-vthumb` `height 176px → 123px` (−30%); iframe `scale .45 → .315` to match.
- `.mtld-vbody` padding tightened.

**Shared mock content (condensed, both systems)**
- padding `20/14/14 → 12/9/9`, gap `7 → 5`, logo `44px → 32px` (font `18 → 13`),
  name `11.5 → 10px`, skeleton line `9 → 6px`, button `13 → 9px`, top bar `6 → 5px`.
- card meta padding/fonts lightly tightened.

Scaling is uniform `scale()` (no `scaleX/scaleY`), so frames shrink without stretch.

## Measured (1280px viewport)
| | before | after | Δ |
|---|---|---|---|
| library thumb | 178×238 (5 cols) | 148×173 (6 cols) | W −17%, H −27% |
| P4 gallery thumb | 225×176 (4 cols) | 178×123 (5 cols) | W −21%, H −30% |

Both grids fit one more column per row; content stays legible and proportionate
(`before_library_grid.png`/`after_library_grid.png`,
`before_gallery_grid.png`/`after_gallery_grid.png`).
Test: `tests/test_designer_thumb_compact.py` (6).
