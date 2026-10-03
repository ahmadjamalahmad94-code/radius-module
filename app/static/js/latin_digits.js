/* ───── أرقام إنجليزية (0123456789) في كل الواجهة — عرضًا وكتابةً ─────
 * مصدرٌ واحد تحمّله لوحة الإدارة (_admin_layout.html) والصفحات المستقلّة
 * (الدخول، بوّابات المشترك/البطاقة/الموزّع، صفحة انتهاء الاشتراك…) — كي لا
 * تبقى صفحةٌ تعرض ٠١٢٣ لأنّها خارج القالب الرئيسيّ. العربيّة واتّجاه RTL
 * كما هما؛ نغيّر نظام الأرقام وحده.
 *
 * أربع جبهات يظهر منها الرقم الهنديّ (٠١٢٣) رغم أن القوالب لاتينية:
 *   1) المتصفح يرسم أرقام حقول number/date/time هنديةً مع <html lang="ar">
 *      → نختم lang="en" على كل حقول الإدخال (يغيّر نظام الأرقام فقط).
 *   2) قيَم مخزَّنة/نصوص معروضة تحوي محارف ٠-٩ أو ۰-۹ فعليّة (كتبها مستخدم
 *      بلوحة عربية سابقًا أو بناها JS) → نطبّع كل العُقد النصّية وقيَم
 *      الحقول عند التحميل وعند أيّ حقن ديناميكيّ (childList+characterData).
 *   3) نصوص السمات التي يرسمها المتصفح بنفسه: placeholder وtitle (التلميح)
 *      وaria-label وdata-hint… → تُطبَّع كذلك، وتُراقَب عند تغيّرها.
 *   4) الكتابة الحيّة بلوحة مفاتيح عربية → مستمع input يحوّل فورًا مع
 *      الحفاظ على موضع المؤشّر (التحويل 1:1 فلا ينزاح). ما يُحفَظ بعدها
 *      للسيرفر يكون لاتينيًّا، فتتنظّف البيانات القديمة تدريجيًّا مع كل حفظ.
 * كلمات المرور لا تُلمَس (قيمتها سرّ يطابق ما في قاعدة الرديوس حرفيًّا).
 */
var hrT = window.hrT || function (s, o) { var d = window.HR_I18N || {}; var t = Object.prototype.hasOwnProperty.call(d, s) ? d[s] : s; if (o) { for (var k in o) { t = String(t).split('{' + k + '}').join(o[k]); } } return t; };  // i18n — انظر I18N.md
(function () {
  'use strict';
  if (window.__hrLatinDigits) return;            // حُمِّل مرّتين؟ مرّة تكفي
  window.__hrLatinDigits = true;

  var WIDGET_SEL = 'input,textarea,select';
  var ATTRS = ['placeholder', 'title', 'aria-label', 'alt', 'data-hint', 'data-tip', 'data-title'];
  var ATTR_SEL = '[' + ATTRS.join('],[') + ']';
  /*<hr-num-clean>*/
  // ⚠️ هذه الكتلة تُستخرَج وتُشغَّل حرفيًّا في tests/test_web_arabic_number_input.py
  // (cscript/JScript) — أبقِها ES3 نقيّة (var/function، بلا DOM).
  // i18n: hrT العامّة في المتصفّح؛ وبديل هويّة ES3 حين تُشغَّل الكتلة وحدها (اختبار cscript).
  var hrT = (typeof window !== 'undefined' && window.hrT) || function (s, o) { if (o) { for (var k in o) { s = String(s).split('{' + k + '}').join(o[k]); } } return s; };
  var RX = /[٠-٩۰-۹]/;    // فحص (بلا /g — لا lastIndex): عربيّة-هنديّة + فارسيّة
  var RXG = /[٠-٩۰-۹]/g;  // استبدال
  // 🔴 فواصل الأرقام العربيّة (R12 N1): لوحة الجوّال العربيّة تكتب الفاصلة
  // العشريّة «٫» (U+066B)، وكان المنظِّف يحذفها كمحرفٍ غريب فيصير ٣٥٫٥ ⇒ 355
  // (دفعة ×10 و+152 يومًا بصمت). الآن: «٫» ⇒ «.»، فاصل الآلاف «٬» (U+066C)
  // ⇒ يُحذف، الفاصلة «،»/«,» ⇒ «.» كما كانت اللاتينيّة، وعلامة الناقص
  // الطباعيّة «−» ⇒ «-». ومحارف الاتّجاه الخفيّة/المسافات تُحذف.
  function toLatin(s) {
    return s.replace(RXG, function (d) {
      var c = d.charCodeAt(0);
      return String.fromCharCode(48 + ((c >= 0x06F0) ? c - 0x06F0 : c - 0x0660));
    });
  }
  function numSeps(s) {
    return toLatin(String(s))
      .replace(/[\u066B\u060C,]/g, '.')
      .replace(/[\u2212\uFE63\uFF0D]/g, '-')
      .replace(/[\u066C\u200E\u200F\u061C\u00A0\u202F\s]/g, '');
  }
  function numClean(s) {                 // حقل رقميّ صِرف: أرقام و«.» و«-» فقط
    return numSeps(s).replace(/[^\d.\-]/g, '');
  }
  // رسالة تحقّق حقلٍ رقميّ (بديل تحقّق type=number): مطلوب/صيغة/min/max/step.
  // step يُفرَض فقط إن كُتب صراحةً (غير any) — لا نحجب كسورًا في حقلٍ لم يحدّد خطوته.
  function numCheck(v, required, mn, mx, st) {
    v = String(v == null ? '' : v).replace(/^\s+|\s+$/g, '');
    if (!v) return required ? hrT('هذا الحقل مطلوب.') : '';
    if (!/^-?(\d+(\.\d*)?|\.\d+)$/.test(v)) return hrT('أدخل رقمًا صحيحًا.');
    var n = parseFloat(v);
    if (mn !== null && mn !== undefined && mn !== '' && isFinite(+mn) && n < +mn) return hrT('القيمة يجب أن تكون ') + mn + hrT(' أو أكثر.');
    if (mx !== null && mx !== undefined && mx !== '' && isFinite(+mx) && n > +mx) return hrT('القيمة يجب أن تكون ') + mx + hrT(' أو أقلّ.');
    if (st && st !== 'any' && isFinite(+st) && +st > 0) {
      var base = (mn !== null && mn !== undefined && mn !== '' && isFinite(+mn)) ? +mn : 0;
      var k = (n - base) / (+st);
      if (Math.abs(k - Math.round(k)) > 1e-7) {
        return (+st >= 1 && Math.round(+st) === +st && base === Math.round(base))
          ? hrT('أدخل عددًا صحيحًا بلا كسور.')
          : (hrT('القيمة يجب أن تكون من مضاعفات ') + st + '.');
      }
    }
    return '';
  }
  /*</hr-num-clean>*/
  var NUM_BAD_MSG = hrT('أدخل رقمًا صحيحًا فقط — لا حروف ولا «e».');
  window.hrLatinDigits = toLatin;
  window.hrNumClean = numClean;
  window.hrNumSeps = numSeps;
  // حقلٌ نصّيّ عشريّ (inputmode=decimal أو منتقي الوحدات .ui-value) — تُطبَّع
  // فواصله فقط دون حذف بقيّة المحارف (قد يملك منطقه الخاصّ).
  function isDecimalText(t) {
    if (!t || !t.getAttribute || t.type === 'password') return false;
    if (t.hasAttribute('data-hr-num') || t.hasAttribute('data-hr-fmt')) return false;
    return t.getAttribute('inputmode') === 'decimal' ||
      (t.classList && t.classList.contains('ui-value'));
  }
  // استبدالٌ يحفظ موضع المؤشّر: التحويل محرفًا بمحرف (0 أو 1)، فموضعه الجديد
  // = طول تحويل ما قبله.
  function setCleaned(t, fn) {
    var v = t.value, c = fn(v);
    if (c === v) return;
    var pos = null;
    try { pos = t.selectionStart; } catch (_) {}
    t.value = c;
    if (pos !== null && pos !== undefined) {
      var p = fn(v.slice(0, pos)).length;
      try { t.setSelectionRange(p, p); } catch (_) {}
    }
  }

  // 🔴 حقل type=number يرسمه Chrome بأرقام **لغة المتصفّح** ويتجاهل lang على
  // العنصر: متصفّحٌ عربيّ يعرض «٠» و«٦» في كل حقل رقميّ (لقطة حيّة على
  // client20 بمتصفّح ar). فنحوّله حقلًا نصّيًّا inputmode=decimal يعرض القيمة
  // حرفيًّا — نفس علاج unit_input.html — ونحفظ علامته data-hr-num لتبقى أنماطه
  // (:is([type=number],[data-hr-num])) ويبقى تحقّق min/max عند الإرسال.
  // وحقول الوقت/الشهر/الأسبوع (ومثلها التاريخ حيث لا يوجد hub_date.js) —
  // Chrome يرسمها «٠٢:٣٥ م» و«٢٥/٠٩/٢٠٢٦» بمتصفّح عربيّ حتى مع lang=en.
  // تصير نصّيّةً بنفس صيغة القيمة الأصليّة (HH:MM · YYYY-MM · YYYY-Www ·
  // YYYY-MM-DD) فلا يتغيّر ما يصل الخادم، مع تحقّق الصيغة عند الإرسال.
  var FMT = {
    time:  { ph: '14:30',            re: /^([01]\d|2[0-3]):[0-5]\d(:[0-5]\d)?$/,  keep: /[^\d:]/g },
    month: { ph: '2026-09',          re: /^\d{4}-(0[1-9]|1[0-2])$/,              keep: /[^\d-]/g },
    week:  { ph: '2026-W39',         re: /^\d{4}-W(0[1-9]|[1-4]\d|5[0-3])$/,     keep: /[^\dW-]/gi },
    date:  { ph: '2026-09-25',       re: /^\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$/, keep: /[^\d-]/g },
    'datetime-local': { ph: '2026-09-25T14:30',
             re: /^\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])T([01]\d|2[0-3]):[0-5]\d$/, keep: /[^\dT:-]/gi }
  };
  function fmtToText(el) {
    var t = el.type;
    if (!FMT[t] || el.hasAttribute('data-native-date')) return;
    // التاريخ يلفّه hub_date.js بمنتقٍ عربيّ بأرقام لاتينيّة ويُبقي الحقل
    // الأصليّ حاملًا مخفيًّا — لا نلمسه حيث المنتقي محمَّل.
    if ((t === 'date' || t === 'datetime-local') && window.__hubDateInit) return;
    try {
      var v = el.value;
      el.setAttribute('data-hr-fmt', t);
      if (!el.getAttribute('placeholder')) el.setAttribute('placeholder', FMT[t].ph);
      el.setAttribute('inputmode', 'numeric');
      el.type = 'text';
      el.value = v;
      el.setAttribute('dir', 'ltr');
      el.setAttribute('autocomplete', 'off');
    } catch (_) {}
  }
  function numToText(el) {
    fmtToText(el);
    if (el.type !== 'number') return;
    try {
      el.setAttribute('data-hr-num', '1');
      if (!el.getAttribute('inputmode')) {
        var st = el.getAttribute('step'), mn0 = el.getAttribute('min');
        // لوحة «numeric» في الجوّال بلا «-»: نختارها فقط لعددٍ صحيح لا يقبل سالبًا.
        var nonNeg = mn0 !== null && mn0 !== '' && isFinite(+mn0) && +mn0 >= 0;
        el.setAttribute('inputmode', (st && st !== 'any' && st.indexOf('.') < 0 && +st >= 1 && nonNeg) ? 'numeric' : 'decimal');
      }
      el.type = 'text';
      el.setAttribute('dir', 'ltr');
      el.setAttribute('autocomplete', 'off');
    } catch (_) {}
  }
  function stampWidgets(root) {
    if (!root || !root.querySelectorAll) return;
    var els = root.querySelectorAll(WIDGET_SEL);
    for (var i = 0; i < els.length; i++) { els[i].setAttribute('lang', 'en'); numToText(els[i]); }
    if (root.matches && root.matches(WIDGET_SEL)) { root.setAttribute('lang', 'en'); numToText(root); }
  }
  // بدائلُ ما كان يفعله type=number: لا محارف غير رقميّة أثناء الكتابة، وتحقّق
  // min/max قبل الإرسال برسالة المتصفّح نفسها.
  function numMsg(el) {
    return numCheck(numSeps(el.value || ''), el.required, el.getAttribute('min'),
                    el.getAttribute('max'), el.getAttribute('step'));
  }
  function fmtMsg(el) {
    var v = String(el.value || '').trim(), f = FMT[el.getAttribute('data-hr-fmt')];
    if (!v) return el.required ? hrT('هذا الحقل مطلوب.') : '';
    return (f && !f.re.test(v)) ? (hrT('الصيغة المطلوبة مثل: ') + f.ph) : '';
  }
  document.addEventListener('input', function (e) {
    var t = e.target;
    if (t && t.hasAttribute && t.hasAttribute('data-hr-fmt')) {
      var f = FMT[t.getAttribute('data-hr-fmt')];
      var c = toLatin(t.value).replace(f.keep, '');
      if (t.getAttribute('data-hr-fmt') === 'week') c = c.replace(/w/g, 'W');
      if (c !== t.value) t.value = c;
      if (t.setCustomValidity) t.setCustomValidity('');
      return;
    }
    if (isDecimalText(t)) { setCleaned(t, numSeps); markNum(t); return; }
    if (!t || !t.hasAttribute || !t.hasAttribute('data-hr-num')) return;
    // F08-L: لا نحذف محارف غريبة بصمت — «1e9» كانت تصير «19» (دفعة 19 ₪ بدل
    // رفض). نطبّع الأرقام العربيّة والفواصل فقط، وأيّ محرف آخر يُعلَّم خطأً
    // برسالة ظاهرة ويُرفض الإرسال (numCheck).
    setCleaned(t, numSeps);
    markNum(t);
  }, true);
  function markNum(t) {
    var bad = /[^\d.\-]/.test(t.value || '');
    var msg = bad ? NUM_BAD_MSG : '';
    if (t.setCustomValidity) t.setCustomValidity(msg);
    if (bad) { t.setAttribute('aria-invalid', 'true'); t.classList.add('hr-num-invalid'); t.title = msg; }
    else if (t.getAttribute('aria-invalid') === 'true') {
      t.removeAttribute('aria-invalid'); t.classList.remove('hr-num-invalid');
      if (t.title === NUM_BAD_MSG) t.removeAttribute('title');
    }
  }
  // وقتٌ مكتوب «930» أو «9:30» ⇒ «09:30» عند مغادرة الحقل (كما يقبله type=time).
  document.addEventListener('change', function (e) {
    var t = e.target;
    // F08-L: حقل رقميّ فيه محرف غير رقميّ — نُظهر الرسالة عند مغادرته (لا صمت).
    if (t && t.hasAttribute && (t.hasAttribute('data-hr-num') || isDecimalText(t)) && t.getAttribute('aria-invalid') === 'true') {
      try { t.reportValidity(); } catch (_) {}
      return;
    }
    if (!t || !t.getAttribute || t.getAttribute('data-hr-fmt') !== 'time') return;
    var m = /^(\d{1,2}):?(\d{2})$/.exec(String(t.value || '').trim());
    if (m && +m[1] < 24 && +m[2] < 60) t.value = (m[1].length < 2 ? '0' : '') + m[1] + ':' + m[2];
  }, true);
  document.addEventListener('submit', function (e) {
    var f = e.target, bad = null;
    if (!f || !f.querySelectorAll || f.noValidate) return;
    var nums = f.querySelectorAll('[data-hr-num],[data-hr-fmt]');
    for (var i = 0; i < nums.length; i++) {
      if (nums[i].disabled) continue;
      // قيمة لصقها JS أو الإكمال التلقائيّ دون حدث input: نطبّع أرقامها وفواصلها
      // فقط قبل الإرسال — لا حذف (F08-L: «1e9» تُرفض برسالة لا تصير «19»).
      if (nums[i].hasAttribute('data-hr-num')) {
        var cv = numSeps(nums[i].value || '');
        if (cv !== nums[i].value) nums[i].value = cv;
      }
      var m = nums[i].hasAttribute('data-hr-fmt') ? fmtMsg(nums[i]) : numMsg(nums[i]);
      if (nums[i].setCustomValidity) nums[i].setCustomValidity(m);
      if (m && !bad) bad = nums[i];
    }
    if (bad) {
      e.preventDefault(); e.stopImmediatePropagation();
      try { bad.reportValidity(); } catch (_) { alert(bad.validationMessage || numMsg(bad)); }
    }
  }, true);
  function normalizeValues(root) {
    if (!root || !root.querySelectorAll) return;
    var els = root.querySelectorAll('input,textarea');
    for (var i = 0; i < els.length; i++) {
      var el = els[i];
      if (el.type === 'password') continue;          // لا نلمس كلمات المرور
      if (typeof el.value === 'string' && RX.test(el.value)) el.value = toLatin(el.value);
    }
  }
  function normalizeAttrsOf(el) {
    for (var a = 0; a < ATTRS.length; a++) {
      var v = el.getAttribute(ATTRS[a]);
      if (v && RX.test(v)) el.setAttribute(ATTRS[a], toLatin(v));
    }
  }
  function normalizeAttrs(root) {
    if (!root || !root.querySelectorAll) return;
    if (root.matches && root.matches(ATTR_SEL)) normalizeAttrsOf(root);
    var els = root.querySelectorAll(ATTR_SEL);
    for (var i = 0; i < els.length; i++) normalizeAttrsOf(els[i]);
  }
  // 🔴 «رقم + وحدة لاتينيّة» (0 B · 32.3 GB · 20 Mbps · 300 ms) داخلَ سياقٍ RTL:
  // الوحدةُ حرفٌ لاتينيٌّ قويّ والرقمُ ضعيف، فترتّبهما خوارزميّةُ bidi «GB 32.3» —
  // مسحُ r6ui وجد 53 موضعًا في 11 صفحة (لوحة التحكم، الحزم، بطاقات الحزمة، تقرير
  // الاستهلاك، التحكم بالسرعة، الملف…) تُبنى بقوالبَ وماكروهاتٍ وJS متفرّقة.
  // نعزل المقطعَ بـLRI…PDI في العقدة النصّيّة نفسِها (مرّةً واحدة؛ المعزولُ يُترك).
  var UNIT_RX = /(^|[^⁦\d.,])(\d[\d.,]*\s?(?:[KMGT]i?B|[kKMGT]bps|bps|[KMG]b|kB|B|ms)\b)(?!⁩)/g;
  var UNIT_TEST = /\d\s?(?:[KMGT]i?B|[kKMGT]bps|bps|[KMG]b|kB|B|ms)\b/;
  function isolateUnits(n) {
    var v = n.nodeValue;
    if (!v || !UNIT_TEST.test(v)) return;
    var p = n.parentNode;
    if (p && p.closest && p.closest('code,pre,kbd,samp,[dir=ltr],.mono')) return;
    var w = v.replace(UNIT_RX, function (_m, pre, run) { return pre + '⁦' + run + '⁩'; });
    if (w !== v) n.nodeValue = w;
  }
  function normalizeText(root) {
    if (!root) return;
    if (root.nodeType === 3) {                        // عقدة نصّية مباشرة
      if (RX.test(root.nodeValue)) root.nodeValue = toLatin(root.nodeValue);
      var pn = root.parentNode && root.parentNode.nodeName;
      if (pn !== 'SCRIPT' && pn !== 'STYLE' && pn !== 'TEXTAREA') isolateUnits(root);
      return;
    }
    if (!root.querySelectorAll && root.nodeType !== 1 && root.nodeType !== 9) return;
    var walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, null);
    var n, p;
    while ((n = walker.nextNode())) {
      p = n.parentNode && n.parentNode.nodeName;
      if (p === 'SCRIPT' || p === 'STYLE' || p === 'TEXTAREA') continue;
      if (RX.test(n.nodeValue)) n.nodeValue = toLatin(n.nodeValue);
      isolateUnits(n);
    }
  }
  function sweep(root) {
    stampWidgets(root); normalizeValues(root); normalizeAttrs(root); normalizeText(root);
  }

  function start() {
    sweep(document.body || document);
    // الكتابة الحيّة: تحويل فوريّ 1:1 مع إبقاء المؤشّر مكانه.
    document.addEventListener('input', function (e) {
      var t = e.target;
      if (!t || typeof t.value !== 'string' || t.type === 'password') return;
      if (!RX.test(t.value)) return;
      var pos = null;
      try { pos = t.selectionStart; } catch (_) {}
      t.value = toLatin(t.value);
      if (pos !== null) { try { t.setSelectionRange(pos, pos); } catch (_) {} }
    }, true);
    try {
      new MutationObserver(function (muts) {
        for (var m = 0; m < muts.length; m++) {
          var mu = muts[m];
          if (mu.type === 'characterData') { normalizeText(mu.target); continue; }
          if (mu.type === 'attributes') {
            var v = mu.target.getAttribute(mu.attributeName);
            if (v && RX.test(v)) mu.target.setAttribute(mu.attributeName, toLatin(v));
            continue;
          }
          var added = mu.addedNodes;
          for (var n = 0; n < added.length; n++) {
            if (added[n].nodeType === 1) sweep(added[n]);
            else if (added[n].nodeType === 3) normalizeText(added[n]);
          }
        }
      }).observe(document.documentElement, {
        childList: true, subtree: true, characterData: true,
        attributes: true, attributeFilter: ATTRS
      });
    } catch (e) { /* متصفح قديم بلا Observer — التمريرة الأولى مغطاة */ }
  }

  if (document.body) start();
  else document.addEventListener('DOMContentLoaded', start);
})();
