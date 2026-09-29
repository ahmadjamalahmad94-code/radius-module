/* ───── رسائل أخطاء AJAX بالعربيّة — طبقة العرض الموحّدة (لوحة الإدارة) ─────
 * النوافذ العائمة (سلفة، دفعة، تمديد، كوتة…) تقرأ غالبًا `j.error` من ردّ JSON
 * وتعرضه كما هو. لكن الخادم يعيد الخطأ بأشكالٍ مختلفة:
 *   { ok:false, error:"نصّ عربيّ" }                       ← المسار المعتاد
 *   { ok:false, error:"validation_error", message:"…" }  ← الرمز في error!
 *   { ok:false, code:"busy", message:"…" }               ← لا error أصلًا
 *   { ok:false, error:{ code, message } }                ← غلاف /api/v1 وقوالب الطباعة
 * فكان يظهر للمشغّل رمزٌ إنجليزيّ («validation_error») أو «[object Object]»
 * بدل الرسالة العربيّة التي أرسلها الخادم فعلًا (R12 N4 / a02 M5).
 *
 * العلاج هنا — دون لمس كل قالب: نغلّف Response.prototype.json لطلبات الأصل
 * نفسه، ونطبّع جسم الخطأ (ok:false أو حالة HTTP غير ناجحة):
 *   1) إن وُجدت رسالة عربيّة في أيّ حقل (error/message/message_ar/detail…)
 *      وكان error رمزًا أو نصًّا بلا عربيّة ⇒ error = الرسالة العربيّة
 *      (الأصل يبقى في error_raw).
 *   2) error رمزٌ صِرف (snake_case) بلا بديل عربيّ ⇒ يُفرَّغ (الرمز في
 *      error_code) فتعرض النافذة رسالتها العربيّة الاحتياطيّة `j.error || '…'`.
 *   3) error كائن ⇒ يبقى كائنًا (من يقرأ error.message/code لا يتأثّر) لكن
 *      toString() يعيد رسالته العربيّة، فـ`textContent = j.error` و`new Error(j.error)`
 *      و`'فشل: ' + j.error` تعرض النصّ لا «[object Object]».
 *   4) message رمزٌ أو غائب وثمّة عربيّة في مكانٍ آخر ⇒ message = العربيّة.
 * الجمل الإنجليزيّة القادمة من الخدمات تُترجَم في مصدرها (تيّار المال) — لا
 * نخفيها هنا لأنّ بعضها تشخيصٌ مفيد (أخطاء RouterOS مثلًا).
 * window.hrErrMsg(payload, fallback) متاحةٌ للقوالب: أفضل رسالة عربيّة أو البديل.
 */
(function () {
  'use strict';
  if (window.__hrAjaxErrors) return;
  window.__hrAjaxErrors = true;

  /*<hr-ajax-err>*/
  // ⚠️ هذه الكتلة تُستخرَج وتُشغَّل حرفيًّا في tests/test_web_arabic_number_input.py
  // (cscript/JScript) — أبقِها ES3 نقيّة (var/function، بلا DOM، بلا Array.isArray).
  var HR_AR = /[؀-ۿ]/;
  var HR_CODE = /^[A-Za-z][A-Za-z0-9_.:\-]*$/;      // validation_error · subscriber.loan · not_found
  function hrIsStr(v) { return typeof v === 'string' && v.replace(/^\s+|\s+$/g, '') !== ''; }
  function hrIsAr(v) { return hrIsStr(v) && HR_AR.test(v); }
  function hrIsCode(v) { return hrIsStr(v) && HR_CODE.test(v.replace(/^\s+|\s+$/g, '')); }
  function hrIsList(v) { return Object.prototype.toString.call(v) === '[object Array]'; }
  // أوّل رسالة عربيّة في جسم الخطأ — بأيّ شكلٍ أعاده الخادم.
  function hrArabicIn(p) {
    if (!p || typeof p !== 'object') return '';
    var e = p.error, c = [], i;
    if (e && typeof e === 'object' && !hrIsList(e)) c.push(e.message_ar, e.message, e.detail, e.error);
    else c.push(e);
    c.push(p.message_ar, p.message, p.detail, p.msg, p.reason);
    if (hrIsList(p.errors) && p.errors.length) {
      c.push(p.errors[0] && typeof p.errors[0] === 'object' ? p.errors[0].message : p.errors[0]);
    }
    for (i = 0; i < c.length; i++) if (hrIsAr(c[i])) return c[i];
    return '';
  }
  function hrErrMsg(p, fallback) {
    var fb = hrIsStr(fallback) ? fallback : 'تعذّر تنفيذ العملية.';
    if (typeof p === 'string') return hrIsAr(p) ? p : fb;
    return hrArabicIn(p) || fb;
  }
  // يطبّع جسم ردّ JSON في مكانه (انظر الترويسة). يعيد الجسم نفسه.
  function hrNormalizeErrorPayload(p, httpOk) {
    if (!p || typeof p !== 'object' || hrIsList(p)) return p;
    var isErr = p.ok === false || (httpOk === false && p.ok !== true);
    if (!isErr) return p;
    var ar = hrArabicIn(p), e = p.error;
    if (e && typeof e === 'object' && !hrIsList(e)) {
      var text = ar || (hrIsStr(e.message) ? e.message : '') || (hrIsStr(e.code) ? e.code : '');
      if (text) {
        var fn = function () { return text; };
        try { Object.defineProperty(e, 'toString', { value: fn, enumerable: false, configurable: true, writable: true }); }
        catch (_) { e.toString = fn; }
      }
    } else if (!hrIsAr(e)) {
      if (ar) {
        if (hrIsStr(e)) p.error_raw = e;
        p.error = ar;
      } else if (hrIsCode(e)) {
        p.error_code = e;
        p.error = '';
      }
    }
    if (ar && (!hrIsStr(p.message) || hrIsCode(p.message))) p.message = ar;
    return p;
  }
  /*</hr-ajax-err>*/

  window.hrErrMsg = hrErrMsg;
  window.hrNormalizeErrorPayload = hrNormalizeErrorPayload;

  function sameOrigin(url) {
    if (!url) return true;
    try { return new URL(url, location.href).origin === location.origin; }
    catch (_) { return url.indexOf(location.origin) === 0; }
  }
  var RP = window.Response && window.Response.prototype;
  if (RP && typeof RP.json === 'function' && !RP.json.__hrAjaxErrors) {
    var orig = RP.json;
    var wrapped = function () {
      var res = this;
      return orig.apply(this, arguments).then(function (p) {
        try { if (sameOrigin(res.url)) hrNormalizeErrorPayload(p, res.ok); } catch (_) {}
        return p;
      });
    };
    wrapped.__hrAjaxErrors = true;
    RP.json = wrapped;
  }
})();
