# Phase 2 · Wave 1 · Template #1 — «البوابة الحيّة» (live_portal)

**Section ① شبكة عامة, #1.** Branch: `feat/template-live-portal` (off latest main).
Built one-at-a-time, bespoke (its own file `hotspot_template_live_portal.py`) — no
global/blanket edits.

## The design
A premium **live network console**: deep space-navy background with a cyan radial
glow; a **live status ribbon** («البوابة نشطة — إشارة مستقرّة وممتازة») with a
flowing sheen; a **hero meter** — a conic-gradient signal gauge (live throughput,
e.g. 87 Mbps, gently ticking) beside a tall cyan→green **animated equalizer**; three
crisp stat tiles (التوافر / زمن الوصول / الاتصال مشفّر); a dark **glass login
console** (cyan-bordered fields + a cyan energy CTA); and a dark-glass bottom nav.
Tasteful motion throughout, fully disabled under `prefers-reduced-motion`.

It **reuses the proven shell skeleton** (login form, CHAP/MD5, CSS-only tab nav, the
five views) so login + navigation work, but its look is its own (distinct palette,
bespoke hero markup + a full custom style layer) — not a color reskin.

## Self-inspection + tune (per-template)
Rendered at 390×844 (true mobile), screenshotted, reviewed, and tuned: removed the
now-redundant stock pulse card (the hero supersedes it), tightened the greeting/date
spacing and gave the username an accent, refined field/CTA styling and contrast.
Audit: watermark `z-index:-1` (backmost), bottom bar `z-index:2147483000` **visible/
uncovered**, **0 horizontal overflow**. Screenshot: `live_portal_390.png`.

## Wiring + tests
- Registered `slug="live_portal"` name «البوابة الحيّة» in `LIBRARY`, starter
  `ACCENT_COLOR=#22D3EE`, `BG_COLOR=#0A1428`.
- Placed **first** in the ① شبكة عامة section of the unified gallery.
- Tests: `tests/test_template_live_portal.py` (registered/section, bespoke hero,
  login contracts intact, watermark backmost + bar safety, no var leak). Suite green.

## Live preview for your review
`/admin/radius/mt/<NAS_ID>/login-designer/preview?template_slug=live_portal`
(or open the designer → ① شبكة عامة tab → first card «البوابة الحيّة»).
