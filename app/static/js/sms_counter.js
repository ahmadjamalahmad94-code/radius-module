/* sms_counter.js — live SMS length / cost counter (Unicode-aware).
 *
 * Each SMS costs money, so messages must stay SHORT. The owner's rule: a
 * 60-character guide per SMS. Arabic is Unicode (UCS-2 → ~70 chars/segment),
 * so 60 is the safe single-segment limit. This widget shows, live:
 *   • characters used / 60
 *   • how many paid SMS segments the text consumes (the real cost)
 *   • a clear warning when the text exceeds 60 chars / spills into >1 segment.
 *
 * The segment math mirrors services/sms_segments.py EXACTLY (GSM-7 vs UCS-2,
 * 160/153 vs 70/67) so the UI and server agree.
 *
 * Usage — add `data-sms-counter` to a <textarea>/<input>. Options (attributes):
 *   data-sms-limit="60"               the soft guide (default 60)
 *   data-sms-channel-select="#id"     only emphasise SMS cost when that
 *                                     <select>'s value === "sms" (for pages
 *                                     where the channel is chosen). When the
 *                                     channel isn't SMS the counter stays muted.
 * The script self-attaches on DOMContentLoaded and is idempotent.
 */
(function () {
  "use strict";

  var GSM_SINGLE = 160, GSM_MULTI = 153, UCS2_SINGLE = 70, UCS2_MULTI = 67;
  var DEFAULT_LIMIT = 60;

  // GSM 03.38 basic alphabet (1 unit each).
  var GSM_BASIC = "@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞÆæßÉ !\"#¤%&'()*+,-./0123456789:;<=>?¡ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ§¿abcdefghijklmnopqrstuvwxyzäöñüà";
  // GSM extension table (2 units each: ESC + char).
  var GSM_EXT = "^{}\\[~]|€";
  var BASIC = {}, EXT = {};
  for (var i = 0; i < GSM_BASIC.length; i++) BASIC[GSM_BASIC[i]] = 1;
  for (var j = 0; j < GSM_EXT.length; j++) EXT[GSM_EXT[j]] = 1;

  function isGsm7(text) {
    for (var k = 0; k < text.length; k++) {
      var c = text[k];
      if (!BASIC[c] && !EXT[c]) return false;
    }
    return true;
  }

  function analyze(text) {
    text = text || "";
    var encoding, length, single, multi;
    if (isGsm7(text)) {
      encoding = "gsm";
      single = GSM_SINGLE; multi = GSM_MULTI;
      length = 0;
      for (var k = 0; k < text.length; k++) length += EXT[text[k]] ? 2 : 1;
    } else {
      encoding = "unicode";
      single = UCS2_SINGLE; multi = UCS2_MULTI;
      // UTF-16 code units: astral chars (surrogate pairs) count as 2.
      length = text.length;
    }
    var segments, per;
    if (length === 0) { segments = 0; per = single; }
    else if (length <= single) { segments = 1; per = single; }
    else { per = multi; segments = Math.ceil(length / multi); }
    return { encoding: encoding, length: length, segments: segments,
             perSegment: per, singleLimit: single };
  }

  function segmentsAr(n) {
    if (n <= 0) return "لا رسائل";
    if (n === 1) return "رسالة واحدة";
    if (n === 2) return "رسالتان (مقطعان)";
    return n + " رسائل (مقاطع)";
  }

  function injectStyles() {
    if (document.getElementById("smsc-styles")) return;
    var st = document.createElement("style");
    st.id = "smsc-styles";
    st.textContent =
      ".smsc{margin-top:6px;font-size:11.5px;line-height:1.6}" +
      ".smsc-row{display:flex;align-items:center;gap:10px;flex-wrap:wrap;color:#64748b;font-weight:700}" +
      ".smsc-count{display:inline-flex;align-items:center;gap:3px}" +
      ".smsc-len{font-variant-numeric:tabular-nums;color:#0f172a}" +
      ".smsc-seg{display:inline-flex;align-items:center;gap:5px;background:#f1f5f9;border-radius:999px;padding:2px 9px}" +
      ".smsc-seg::before{content:'\\f658';font-family:'Font Awesome 6 Free';font-weight:900;font-size:9px;color:#94a3b8}" +
      ".smsc-enc{font-size:10px;color:#94a3b8;background:#f8fafc;border:1px solid #eef2f7;border-radius:5px;padding:1px 6px;direction:ltr}" +
      ".smsc.is-over .smsc-len{color:#b45309}" +
      ".smsc.is-over .smsc-seg{background:#fef3c7;color:#92400e}" +
      ".smsc.is-multi .smsc-len{color:#b91c1c}" +
      ".smsc.is-multi .smsc-seg{background:#fee2e2;color:#b91c1c}" +
      ".smsc-warn{margin-top:5px;font-size:11.5px;font-weight:700;line-height:1.7;color:#92400e;" +
        "background:#fffbeb;border:1px solid #fde68a;border-radius:8px;padding:6px 10px}" +
      ".smsc.is-multi .smsc-warn{color:#b91c1c;background:#fef2f2;border-color:#fecaca}";
    document.head.appendChild(st);
  }

  function buildUI(field) {
    injectStyles();
    var wrap = document.createElement("div");
    wrap.className = "smsc";
    wrap.innerHTML =
      '<div class="smsc-row">' +
        '<span class="smsc-count"><b class="smsc-len">0</b> / <span class="smsc-limit"></span> ' +
          '<span class="smsc-unit">حرف</span></span>' +
        '<span class="smsc-seg"></span>' +
        '<span class="smsc-enc"></span>' +
      '</div>' +
      '<div class="smsc-warn" hidden></div>';
    field.parentNode.insertBefore(wrap, field.nextSibling);
    return wrap;
  }

  function channelIsSms(field) {
    var sel = field.getAttribute("data-sms-channel-select");
    if (!sel) return true; // no channel binding → always treat as SMS-relevant
    var el = document.querySelector(sel);
    return !!el && String(el.value || "").toLowerCase() === "sms";
  }

  function attach(field) {
    if (field.__smsc) return;
    field.__smsc = true;
    var limit = parseInt(field.getAttribute("data-sms-limit"), 10) || DEFAULT_LIMIT;
    var ui = buildUI(field);
    ui.querySelector(".smsc-limit").textContent = String(limit);
    var lenEl = ui.querySelector(".smsc-len");
    var segEl = ui.querySelector(".smsc-seg");
    var encEl = ui.querySelector(".smsc-enc");
    var warnEl = ui.querySelector(".smsc-warn");

    function render() {
      var smsMode = channelIsSms(field);
      ui.hidden = !smsMode;
      if (!smsMode) return;
      var info = analyze(field.value);
      lenEl.textContent = String(info.length);
      segEl.textContent = segmentsAr(info.segments);
      encEl.textContent = info.encoding === "unicode" ? "Unicode" : "GSM";

      var over = info.length > limit;
      var multi = info.segments > 1;
      ui.classList.toggle("is-over", over);
      ui.classList.toggle("is-multi", multi);

      if (multi) {
        warnEl.hidden = false;
        warnEl.textContent = "تنبيه: الرسالة ستُرسَل كـ " + segmentsAr(info.segments) +
          " — أي بتكلفة " + info.segments + " رسائل SMS. اختصرها إلى " + limit + " حرفًا أو أقل.";
      } else if (over) {
        warnEl.hidden = false;
        warnEl.textContent = "الرسالة تتجاوز الحدّ الموصى به (" + limit +
          " حرفًا). قد تتحوّل إلى أكثر من رسالة وتزيد التكلفة — يُفضّل اختصارها.";
      } else {
        warnEl.hidden = true;
        warnEl.textContent = "";
      }
    }

    field.addEventListener("input", render);
    field.addEventListener("change", render);
    // React to a bound channel <select> changing too.
    var selAttr = field.getAttribute("data-sms-channel-select");
    if (selAttr) {
      var selEl = document.querySelector(selAttr);
      if (selEl) selEl.addEventListener("change", render);
    }
    render();
    // Expose a manual re-render hook (e.g. after programmatic value changes).
    field.__smscRender = render;
  }

  function init() {
    var fields = document.querySelectorAll("[data-sms-counter]");
    for (var i = 0; i < fields.length; i++) attach(fields[i]);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }

  // Re-scan when other scripts insert SMS fields dynamically.
  window.SmsCounter = { init: init, analyze: analyze };
})();
