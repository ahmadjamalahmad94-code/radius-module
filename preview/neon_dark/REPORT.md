# Phase 2 · Wave 2 · Template #2 — «النيون الداكن» (neon_dark)

**Section ① شبكة عامة, #2.** Branch: `feat/template-neon-dark` (off latest main).
Bespoke, its own file `hotspot_template_neon_dark.py` — no global/blanket edits.

## The design (distinct from #1)
#1 «البوابة الحيّة» was a calm cyan **console** with a round signal gauge. #2 is a
deliberately different **gamer / power-network** identity:
- Near-black background with a faint **circuit grid** + a green radial energy glow.
- **Neon-green** highlights with glow throughout.
- A **gamer HUD hero**: an «A⁺» connection-grade badge, a charging **power bar**
  («مستوى الطاقة 88%»), a live «الشبكة نشطة الآن» pulse, and a 4-cell HUD strip
  (الاستجابة / السرعة / الفقد / الحماية) in monospace numerals.
- **Angular clipped corners** on the hero; a dark login panel with **neon corner
  brackets**, monospace inputs, and a glowing neon-green CTA; a neon-edged bottom nav.
- A sweeping **energy beam** + subtle pulses (all disabled under
  `prefers-reduced-motion`).

Reuses the proven shell skeleton (login form, CHAP/MD5, CSS-only tab nav, the five
views) so login + navigation work; the look is entirely its own.

## Per-template self-inspection + tune
Rendered at 390×844, reviewed, tuned: cleaned the power-bar fill (removed an
`inset`/`width` conflict → crisp 88% fill). Verified high contrast (light/neon on
near-black), good spacing/hierarchy. Audit: watermark `z-index:-1` backmost, bottom
bar `z-index:2147483000` visible/uncovered, **0 horizontal overflow**. Brand is fully
dynamic (`{{TENANT_NAME}}` in title/top-bar/greeting/footer) — no hardcoded sample.

## Wiring + tests
- `slug="neon_dark"` «النيون الداكن», starter `ACCENT_COLOR=#4ADE80`, `BG_COLOR=#050B08`.
- Placed **2nd** in the ① شبكة عامة section.
- Tests: `tests/test_template_neon_dark.py` (registration/#2 slot, bespoke HUD,
  distinct from #1, login contracts, watermark+bar safety, dynamic brand). Suite green.

## Live preview for your review
`/admin/radius/mt/<NAS_ID>/login-designer/preview?template_slug=neon_dark`
(or designer → ① شبكة عامة tab → 2nd card «النيون الداكن»).
