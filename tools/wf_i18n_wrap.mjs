export const meta = {
  name: 'i18n-wrap-templates',
  description: 'تغليف النصوص العربية الصلبة في قوالب Jinja بـ _()/trans عبر دلاء ملفات منفصلة',
  phases: [{ title: 'Wrap', detail: 'وكيل لكل دلو ملفات منفصل — لا تعارض كتابة' }],
}

const RULES = `أنت تغلّف النصوص العربية الصلبة في قوالب Jinja (Flask-Babel) للتدويل (i18n).
نموذج المصدر: **العربية هي لغة المصدر** — النص العربي نفسه يصبح msgid. مهمتك **التغليف فقط، لا الترجمة**.

لكل ملف في قائمتك:
1) اقرأ الملف كاملًا (Read).
2) غلّف **كل نص عرض عربي يقرؤه الإنسان**:
   • نص محتوى:            {{ _('النص العربي') }}
   • سمات HTML العرضية (placeholder/title/aria-label/alt/قيمة أزرار):  placeholder="{{ _('بحث...') }}"
   • فقرة/نص طويل متعدد الأسطر:   {% trans %}نص الفقرة{% endtrans %}
   • متغيّر داخل النص: استخدم صيغة gettext %(name)s — لا تجزّئ بالـJinja:
       صح:  {{ _('مرحبًا %(u)s لديك %(n)s إشعار', u=name, n=cnt) }}
       خطأ: {{ _('مرحبًا ') }}{{ name }}
   • نص JavaScript داخل <script>: مرّره عبر Jinja مغلّفًا بـ tojson:
       const MSG = {{ _('تم الحفظ')|tojson }};
3) **لا تغلّف**: المعرّفات، name="..."، url_for/href/action/المسارات، كلاسات CSS،
   أيقونات fa-*، الأرقام/التواريخ الخام، مفاتيح القواميس، قيم enum البرمجية،
   البيانات الديناميكية من DB، النص اللاتيني الخالص، وأي شيء داخل <bdi> (معرّفات/MAC/أسماء دخول تقنية تبقى كما هي).
4) **لا تكسر أي منطق Jinja** — أبقِ {% if/for/macro/include/set/call %} كما هي تمامًا.

مزالق حرجة (إلزامية):
• داخل {% trans %}...{% endtrans %} يُسمح فقط بنص حرفي + %(var)s. **ممنوع** وضع
  {{ تعبير }} أو {% وسم %} أو تعليق {# #} داخل trans. إن كان المقطع يحوي تعبير Jinja،
  استخدم _() على الأجزاء النصية فقط وأبقِ التعبير خارجها.
• لا تخلط Markup في دمج «~» لبناء HTML أزرار/إجراءات؛ غلّف **رمز النص الظاهر فقط**،
  وأدرج CSRF كنص عبر csrf_token() لا داخل _() .
• لا تضع تعليق Jinja داخل {% call ... %}.
• الاقتباس: إن احتوى النص العربي على علامة اقتباس مفردة ' استخدم اقتباسًا مزدوجًا للدالة:
  {{ _("نصٌّ فيه ' اقتباس") }} — وبالعكس. لا تُداخل نفس نوع الاقتباس داخل سمة HTML.
• لا تلمس .po/.pot/translations ولا أي ملف خارج قائمتك.

بعد تعديل كل ملف، شغّل (Bash) من جذر المشروع وتحقّق:
   python tools/i18n_extract.py --file <المسار>      ← يجب أن يقترب «المتبقّي» من 0
   python tools/i18n_jinja_check.py <المسار>          ← يجب أن يطبع ✓ (سليم نحويًا)
أصلح أي كسر نحوي قبل الانتقال. النصوص داخل script/style تُحسب منفصلة [JS/CSS] — غلّفها بـ tojson.

أعد النتيجة بصيغة الـschema المطلوبة فقط.`

const SCHEMA = {
  type: 'object',
  additionalProperties: false,
  required: ['bucket', 'files', 'issues'],
  properties: {
    bucket: { type: 'number' },
    files: {
      type: 'array',
      items: {
        type: 'object',
        additionalProperties: false,
        required: ['file', 'before', 'after', 'parses'],
        properties: {
          file: { type: 'string' },
          before: { type: 'number', description: 'المتبقّي قبل (script+remaining)' },
          after: { type: 'number', description: 'المتبقّي بعد التغليف' },
          parses: { type: 'boolean', description: 'هل اجتاز i18n_jinja_check' },
          note: { type: 'string', description: 'ملاحظة عن أي متبقٍّ مقصود (نص غير قابل للترجمة)' },
        },
      },
    },
    issues: { type: 'string', description: 'أي ملف تعذّر تغليفه كاملًا والسبب، أو "" إن لا شيء' },
  },
}

const buckets = args
log(`بدء تغليف ${buckets.length} دلوًا (${buckets.reduce((s, b) => s + b.files.length, 0)} ملفًا)`)

const results = await parallel(buckets.map((b) => () =>
  agent(
    `${RULES}\n\n— دلوك رقم ${b.id} — غلّف هذه الملفات حصريًا:\n` +
      b.files.map((f) => `  • ${f}`).join('\n'),
    { label: `wrap:b${b.id}(${b.files.length}f)`, phase: 'Wrap', schema: SCHEMA },
  ),
))

const ok = results.filter(Boolean)
const flat = ok.flatMap((r) => r.files || [])
const broken = flat.filter((f) => f && f.parses === false)
const leftover = flat.filter((f) => f && f.after > 5)
return {
  buckets_done: ok.length,
  buckets_total: buckets.length,
  files_wrapped: flat.length,
  templates_still_broken: broken.map((f) => f.file),
  files_with_leftover_gt5: leftover.map((f) => ({ file: f.file, after: f.after })),
  issues: ok.map((r) => r.issues).filter((s) => s && s.trim()),
}
