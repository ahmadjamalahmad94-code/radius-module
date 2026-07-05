/* row_context_menu — قائمة إجراءات سريعة بزرّ الفأرة الأيمن على صفوف الجداول.
 *
 * الفكرة: بدل قائمة المتصفّح التقليدية، النقر بالزرّ الأيمن على صفّ يحمل
 * data-rowctx يفتح قائمة تجمع إجراءات ذلك الصفّ نفسها. لا نُكرّر منطق الإجراءات
 * ولا ننقل عناصرها: كلّ بند في القائمة **يوكِّل النقر للعنصر الأصليّ في الصفّ**
 * (button/a/form-submit) — فتُحفَظ كلّ المعالجات القائمة ونطاق الصفّ (applyRow،
 * closest('tr')، مودالات، POST، تأكيدات) دون أيّ تغيير في تلك الصفحات.
 *
 * التفعيل: أضِف data-rowctx إلى <tr>. تُجمَع الإجراءات تلقائيًّا من:
 *   .urow-item (المشتركون) · .urow-actions>a (زرّ 360°) · .online-mini-actions a/button
 *   (المتصلون) · وأيّ عنصر تعلّمه data-rowctx-item.
 */
(function () {
  "use strict";

  var MENU = null;

  function close() {
    if (!MENU) return;
    MENU.remove();
    MENU = null;
    document.removeEventListener("mousedown", onDocDown, true);
  }
  function onDocDown(e) { if (MENU && !MENU.contains(e.target)) close(); }

  function labelOf(el) {
    var t = (el.textContent || "").replace(/\s+/g, " ").trim();
    if (t) return t;
    return (el.getAttribute("title") || el.getAttribute("aria-label") || "").trim();
  }
  function iconOf(el) {
    var i = el.querySelector("i[class]");
    return i ? '<i class="' + i.getAttribute("class") + '"></i>' : "";
  }
  function isDisabled(el) {
    return el.disabled === true || el.getAttribute("aria-disabled") === "true" ||
           (el.closest && el.closest("[disabled]") && el.tagName === "BUTTON" && el.disabled);
  }
  function isDanger(el) {
    return el.classList.contains("urow-item--danger") ||
           el.classList.contains("hub-btn--danger");
  }

  var GATHER = ".urow-item, .urow-actions > a[href], " +
               ".online-mini-actions a[href], .online-mini-actions button, " +
               "[data-rowctx-item]";

  // نسخ نصّ للحافظة — اللوحة قد تعمل على http (لا secure context) حيث
  // navigator.clipboard غير متاح، فنرتدّ إلى textarea + execCommand.
  function copyText(text) {
    if (navigator.clipboard && window.isSecureContext) {
      navigator.clipboard.writeText(text).catch(function () { copyFallback(text); });
      return;
    }
    copyFallback(text);
  }
  function copyFallback(text) {
    var ta = document.createElement("textarea");
    ta.value = text;
    ta.style.cssText = "position:fixed;top:-1000px;opacity:0";
    document.body.appendChild(ta);
    ta.select();
    try { document.execCommand("copy"); } catch (e) { /* لا شيء */ }
    ta.remove();
  }

  // قيمة الخليّة المنقورة (لبند «نسخ»): نصّ الخليّة مشذّبًا — نتجاهل خلايا
  // التحديد/الإجراءات (لا قيمة فيها) ونرتدّ لاسم المستخدم من صفّه.
  function cellValue(cell, row) {
    var v = "";
    if (cell && !cell.querySelector("input[type=checkbox]") &&
        !cell.querySelector(".urow-actions, .online-mini-actions")) {
      v = (cell.textContent || "").replace(/\s+/g, " ").trim();
    }
    if (!v) v = (row.dataset.username || "").trim();
    return v;
  }

  function build(row, cell, x, y) {
    close();
    var raw = Array.prototype.slice.call(row.querySelectorAll(GATHER));
    var seen = {}, acts = [];
    raw.forEach(function (el) {
      if (isDisabled(el)) return;
      var lab = labelOf(el);
      if (!lab || seen[lab]) return;
      seen[lab] = 1;
      acts.push(el);
    });
    var copyVal = cellValue(cell, row);
    if (!acts.length && !copyVal) return false;

    var m = document.createElement("div");
    m.className = "rowctx-menu";
    m.setAttribute("role", "menu");
    // اتّجاه القائمة من اتّجاه الصفحة (RTL/LTR)
    m.dir = document.documentElement.dir || "rtl";

    // ── «نسخ القيمة» أوّل بند — ينسخ نصّ الخليّة المنقورة ──
    if (copyVal) {
      var shown = copyVal.length > 26 ? copyVal.slice(0, 26) + "…" : copyVal;
      var cb = document.createElement("button");
      cb.type = "button";
      cb.className = "rowctx-item";
      cb.innerHTML = '<i class="fa-solid fa-copy"></i><span>نسخ «' +
        shown.replace(/&/g, "&amp;").replace(/</g, "&lt;") + "»</span>";
      cb.addEventListener("click", function (ev) {
        ev.preventDefault();
        copyText(copyVal);
        // إشعار خاطف بأنّ النسخ تمّ (بدل الإغلاق الصامت).
        var sp = cb.querySelector("span");
        if (sp) sp.textContent = "نُسخت ✓";
        setTimeout(close, 420);
      });
      m.appendChild(cb);
      if (acts.length) {
        var sep = document.createElement("div");
        sep.className = "rowctx-sep";
        m.appendChild(sep);
      }
    }

    acts.forEach(function (el) {
      var b = document.createElement("button");
      b.type = "button";
      b.className = "rowctx-item" + (isDanger(el) ? " is-danger" : "");
      b.innerHTML = iconOf(el) + "<span>" + labelOf(el) + "</span>";
      b.addEventListener("click", function (ev) {
        ev.preventDefault();
        close();
        // وكِّل النقر للعنصر الأصليّ — يشغّل كلّ معالجاته ونطاق صفّه.
        el.click();
      });
      m.appendChild(b);
    });

    document.body.appendChild(m);
    MENU = m;

    var mw = m.offsetWidth, mh = m.offsetHeight;
    var L = x, T = y;
    // في RTL نفتح القائمة يسار المؤشّر كي لا تخرج عن الحافة
    if (m.dir === "rtl") L = x - mw;
    if (L + mw > window.innerWidth - 8) L = window.innerWidth - mw - 8;
    if (L < 8) L = 8;
    if (T + mh > window.innerHeight - 8) T = Math.max(8, y - mh);
    if (T < 8) T = 8;
    m.style.left = L + "px";
    m.style.top = T + "px";

    setTimeout(function () {
      document.addEventListener("mousedown", onDocDown, true);
    }, 0);
    return true;
  }

  document.addEventListener("contextmenu", function (e) {
    var tgt = e.target;
    // اترك السلوك الافتراضيّ داخل حقول الإدخال (خانة التحديد/بحث…)
    if (tgt.closest("input, textarea, select")) return;
    var row = tgt.closest("tr[data-rowctx]");
    if (!row) return;
    var cell = tgt.closest("td");
    if (build(row, cell, e.clientX, e.clientY)) e.preventDefault();
  });

  document.addEventListener("keydown", function (e) { if (e.key === "Escape") close(); });
  window.addEventListener("scroll", close, true);
  window.addEventListener("resize", close);

  // أنماط القائمة (محقونة — ملفّ واحد يكفي لأيّ صفحة)
  var css =
    ".rowctx-menu{position:fixed;z-index:9999;min-width:212px;max-height:82vh;overflow:auto;" +
    "background:#fff;border:1px solid var(--hub-border-soft,#e2e8f0);border-radius:12px;padding:6px;" +
    "box-shadow:0 14px 38px rgba(15,23,42,.20);font-family:inherit;animation:rowctxIn .1s ease}" +
    "@keyframes rowctxIn{from{opacity:0;transform:translateY(-4px)}to{opacity:1;transform:none}}" +
    ".rowctx-item{display:flex;align-items:center;gap:9px;width:100%;text-align:start;" +
    "background:transparent;border:0;border-radius:8px;padding:8px 10px;font:inherit;font-size:13px;" +
    "font-weight:600;color:var(--hub-text,#1b1e2b);cursor:pointer;white-space:nowrap}" +
    ".rowctx-item:hover{background:var(--hub-bg-soft,#f5f3ff);color:var(--hub-brand-ink,#5b21b6)}" +
    ".rowctx-item i{width:16px;text-align:center;color:var(--hub-text-mute,#8b90a0);flex:0 0 auto;font-size:12px}" +
    ".rowctx-item:hover i{color:var(--hub-brand,#6b5aed)}" +
    ".rowctx-item.is-danger{color:var(--hub-red-ink,#b91c1c)}" +
    ".rowctx-item.is-danger:hover{background:var(--hub-red-soft,#fef2f2)}" +
    ".rowctx-item.is-danger i{color:var(--hub-red-ink,#b91c1c)}" +
    ".rowctx-sep{height:1px;background:var(--hub-border-soft,#e2e8f0);margin:4px 2px}";
  var s = document.createElement("style");
  s.textContent = css;
  document.head.appendChild(s);
})();
