# Login designer — organize & clarify the three design sources

**Branch:** `fix/designer-section-clarity` (off `origin/main` @ 03c3290).
Kept the sections separate (not merged) — just ordered into a workflow and
clarified with accurate one-liners. Screenshots: `preview/designer_clarity/`.

## New top-to-bottom order
1. **① «قوالب جاهزة حسب نوع منشأتك»** (bundle) — *start here*.
2. **② «معرض التصاميم»** (library / layout).
3. **③ colors + the rest of the fields** (inside «تخصيص التصميم» → «الهوية» tab).
4. **«الأصول المستضافة (فيديو/خط)»** moved DOWN (advanced, below the workflow,
   next to «رفع تصميم خاص») — it used to sit between ① and ②, breaking the flow.

## Accurate help under each (distinguishes them, no false claims)
- **Bundle (①):** «نقطة البداية. حُزمة كاملة جاهزة حسب نشاطك — قالب وألوان وإضافات
  معًا. زرّ «استخدم هذا القالب» **يَستبدل التصميم بالكامل بما فيه الألوان**…
  (اسمك وشعارك ودعمك تبقى كما هي).»
- **Gallery (②):** «**بدّل التخطيط واحتفظ بألوانك.** هذا المعرض يُغيّر القالب/الهيكل
  فقط — **ألوانك الحالية تبقى كما هي**، وكل مصغّرة تعرض القالب بألوانك أنت.»
- **Colors (③):** a callout above the color pickers in the «الهوية» tab:
  «③ الألوان فقط. الحقلان التاليان يُغيّران ألوان التصميم فقط — لا يبدّلان القالب
  ولا الإضافات.»

## «الفرق؟» inline hint (links the three)
A shared `_diff_hint` chip (defined once, reused under all three) shows on hover:
«الفرق بين الثلاثة — «قوالب جاهزة حسب نوعك»: حُزمة كاملة تستبدل التصميم والألوان
والإضافات معًا (ابدأ منها). «معرض التصاميم»: يبدّل القالب/التخطيط فقط ويُبقي ألوانك
الحالية. «اللون الرئيسي/الثانوي»: يُغيّر الألوان فقط دون تبديل القالب.»
Rendered via the existing floating `hub-hint` (never clipped).

## Polish
Section-meta step markers ① ② ③; design-system styling (`mtld-lead`,
`hub-hint`, brand-soft callout); no native alerts; consistent spacing.

## Files
- `app/templates/radius/mt_login_designer.html` — reorder + leads + `_diff_hint`
  + colors note + step markers.
- `app/static/css/mt_login_designer.css` — `.mtld-diff-hint` chip, `.mtld-colors-note`
  callout, `.mtld-lead strong`.
- `tests/test_designer_section_clarity.py` (8).
