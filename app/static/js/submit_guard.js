/* ════════════════════════════════════════════════════════════════════
   SUBMIT GUARD — حارسٌ عامّ ضدّ النقر المزدوج على نماذج الإجراء الفوريّة
   (مثل «إضافة وقت» أو «تطبيق» في فاحص البطاقات). على الجوّال خصوصًا،
   نقرتان سريعتان قبل استجابة الخادم تُنفّذان العمليّة مرّتين (60 دقيقة
   تصبح 120، أو شحن مضاعَف في وضع «مدفوع»).

   يعمل على طبقتين:
   1) كل <form> عاديّ في الموقع: حدث submit يُعطَّل زرّ الإرسال ويُمنع أيّ
      submit ثانٍ حتى انتقال الصفحة أو انقضاء مهلة أمان (لو فشل التحقّق
      صامتًا فلا يبقى الزرّ معطَّلًا للأبد).
   2) form.submit() المباشر عبر JS لا يُطلق حدث submit إطلاقًا (خاصيّة
      معروفة في المتصفح)، فتُستخدم قوالب مثل فاحص البطاقات الدالّة
      window.hbGuardedSubmit(form) بدل استدعاء .submit() مباشرة.

   إضافيًّا: data-hb-guard على أيّ زرّ إجراء (ليس بالضرورة submit، مثل
   زرّ يُنفّذ fetch/AJAX) يمنحه نفس الحماية من النقر المزدوج.
   ════════════════════════════════════════════════════════════════════ */
(function () {
  "use strict";
  if (window.__hubSubmitGuardInit) return;
  window.__hubSubmitGuardInit = true;

  // شبكة أمان: لو لم يحدث تنقّل صفحة (مثلًا رفض المتصفح الإرسال بصمت
  // بسبب حقل غير صالح) تُعاد الأزرار تلقائيًا بدل أن تبقى معطَّلة للأبد.
  var GUARD_MS = 4000;

  function submitButtons(form) {
    return form.querySelectorAll('button[type="submit"], input[type="submit"]');
  }
  function disableAll(btns) {
    Array.prototype.forEach.call(btns, function (b) { b.disabled = true; });
  }
  function release(form, btns) {
    form.dataset.hbSubmitting = "";
    Array.prototype.forEach.call(btns, function (b) { b.disabled = false; });
  }

  document.addEventListener(
    "submit",
    function (e) {
      var form = e.target;
      if (!form || form.tagName !== "FORM") return;
      if (form.hasAttribute("data-hb-no-guard")) return;
      if (form.dataset.hbSubmitting === "1") {
        // نقرة ثانية قبل انتقال الصفحة — تُوقَف هنا تمامًا.
        e.preventDefault();
        e.stopImmediatePropagation();
        return;
      }
      form.dataset.hbSubmitting = "1";
      var btns = submitButtons(form);
      disableAll(btns);
      setTimeout(function () { release(form, btns); }, GUARD_MS);
    },
    true
  );

  window.hbGuardedSubmit = function (form) {
    if (!form || form.dataset.hbSubmitting === "1") return false;
    form.dataset.hbSubmitting = "1";
    var btns = submitButtons(form);
    disableAll(btns);
    setTimeout(function () { release(form, btns); }, GUARD_MS);
    form.submit();
    return true;
  };

  document.addEventListener(
    "click",
    function (e) {
      var el = e.target.closest("[data-hb-guard]");
      if (!el) return;
      if (el.__hbBusy) {
        e.preventDefault();
        e.stopImmediatePropagation();
        return;
      }
      el.__hbBusy = true;
      var wasDisabled = el.disabled;
      el.disabled = true;
      setTimeout(function () {
        el.__hbBusy = false;
        el.disabled = wasDisabled;
      }, GUARD_MS);
    },
    true
  );
})();
