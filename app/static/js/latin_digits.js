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
(function () {
  'use strict';
  if (window.__hrLatinDigits) return;            // حُمِّل مرّتين؟ مرّة تكفي
  window.__hrLatinDigits = true;

  var WIDGET_SEL = 'input,textarea,select';
  var ATTRS = ['placeholder', 'title', 'aria-label', 'alt', 'data-hint', 'data-tip', 'data-title'];
  var ATTR_SEL = '[' + ATTRS.join('],[') + ']';
  var RX = /[٠-٩۰-۹]/;          // فحص (بلا /g — لا lastIndex)
  var RXG = /[٠-٩۰-۹]/g;        // استبدال
  function toLatin(s) {
    return s.replace(RXG, function (d) {
      var c = d.charCodeAt(0);
      return String.fromCharCode(48 + ((c >= 0x06F0) ? c - 0x06F0 : c - 0x0660));
    });
  }
  window.hrLatinDigits = toLatin;

  function stampWidgets(root) {
    if (!root || !root.querySelectorAll) return;
    var els = root.querySelectorAll(WIDGET_SEL);
    for (var i = 0; i < els.length; i++) els[i].setAttribute('lang', 'en');
    if (root.matches && root.matches(WIDGET_SEL)) root.setAttribute('lang', 'en');
  }
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
  function normalizeText(root) {
    if (!root) return;
    if (root.nodeType === 3) {                        // عقدة نصّية مباشرة
      if (RX.test(root.nodeValue)) root.nodeValue = toLatin(root.nodeValue);
      return;
    }
    if (!root.querySelectorAll && root.nodeType !== 1 && root.nodeType !== 9) return;
    var walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, null);
    var n, p;
    while ((n = walker.nextNode())) {
      p = n.parentNode && n.parentNode.nodeName;
      if (p === 'SCRIPT' || p === 'STYLE' || p === 'TEXTAREA') continue;
      if (RX.test(n.nodeValue)) n.nodeValue = toLatin(n.nodeValue);
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
