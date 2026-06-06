/* ════════════════════════════════════════════════════════════════════
   HUB DATE — منتقي تاريخ عربي موحّد لكل الموقع (بديل تقويم المتصفح).
   يلفّ كل <input type="date"> و <input type="datetime-local"> تلقائيًا
   بواجهة مخصّصة بنمط hbsel: زر يعرض التاريخ بصيغة عربية واضحة
   («05 حزيران 2026») + لوحة تقويم عائمة بأسماء الأشهر الشامية
   (كانون الثاني… كانون الأول) مع رقم الشهر الميلادي، أيام الأسبوع
   بالعربية والأسبوع يبدأ بالأحد، تمييز بنفسجي لليوم المحدّد وإطار
   لليوم الحالي — كله بخط Cairo وبهوية الموقع.

   الحقل الأصلي يبقى مخفيًا في الـ DOM وهو حامل القيمة الفعلي للفورم:
   اختيار يوم يكتب ISO ‏yyyy-mm-dd (أو yyyy-mm-ddTHH:MM للـ
   datetime-local) في الحقل ويطلق حدثَي input + change فتستمر كل
   الفورمات (GET/POST) والـ JS القديم بالعمل كما هي بلا أي تعديل.

   اللوحة portal على body — أو على أقرب <dialog> أصلي (إصلاح طبقة
   top-layer: لولاه تختفي اللوحة خلف المودال) — بـ z-index 99999،
   وتنقلب للأعلى تلقائيًا قرب أسفل الشاشة. min/max على الحقل محترمة
   (الأيام خارج المدى معطّلة). المراقب MutationObserver يلتقط الحقول
   المُنشأة ديناميكيًا (مودالات الـ JS مثل لقطة السنابشوت).

   استثناء: input[data-native-date] يُترك على تقويم المتصفح الأصلي.
   ════════════════════════════════════════════════════════════════════ */
(function () {
  "use strict";
  if (window.__hubDateInit) return;
  window.__hubDateInit = true;

  var OPEN = null; // اللوحة المفتوحة حاليًا (واحدة فقط — نفس نمط hub_select)

  /* أسماء الأشهر الشامية + رقم الشهر الميلادي يُعرض صغيرًا بجانبها */
  var MONTHS = [
    "كانون الثاني", "شباط", "آذار", "نيسان", "أيار", "حزيران",
    "تموز", "آب", "أيلول", "تشرين الأول", "تشرين الثاني", "كانون الأول"
  ];
  /* اختصارات أيام الأسبوع — الأسبوع يبدأ بالأحد (getDay()===0) */
  var WEEKDAYS = ["أحد", "إثن", "ثلا", "أرب", "خمي", "جمع", "سبت"];

  function pad2(n) { return (n < 10 ? "0" : "") + n; }

  /* ── تحويلات ISO ↔ أجزاء ─────────────────────────────────────────── */

  // يفكّك قيمة الحقل: { y, m(1-12), d, hh, mm } أو null لو فارغة/فاسدة
  function parseValue(v) {
    if (!v) return null;
    var m = /^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2}))?/.exec(v);
    if (!m) return null;
    return {
      y: +m[1], m: +m[2], d: +m[3],
      hh: m[4] != null ? +m[4] : 0,
      mm: m[5] != null ? +m[5] : 0
    };
  }

  // مفتاح يوم رقمي قابل للمقارنة (20260605) — لفحص min/max بلا كائنات Date
  function dayKey(y, m, d) { return y * 10000 + m * 100 + d; }

  function daysInMonth(y, m) { return new Date(y, m, 0).getDate(); } // m هنا 1-12

  // الصيغة العربية المعروضة على الزر: «05 حزيران 2026» (+ الوقت للـ datetime)
  function formatArabic(p, withTime) {
    if (!p) return "";
    var s = pad2(p.d) + " " + MONTHS[p.m - 1] + " " + p.y;
    if (withTime) s += " — " + pad2(p.hh) + ":" + pad2(p.mm);
    return s;
  }

  /* ── الترقية: حقل واحد ───────────────────────────────────────────── */

  function enhance(inp) {
    if (inp.__hbdate) return;
    var type = inp.getAttribute("type");
    if (type !== "date" && type !== "datetime-local") return;
    if (inp.hasAttribute("data-native-date")) return; // مخرج اختياري للأصلي
    inp.__hbdate = true;

    var withTime = type === "datetime-local";
    var prevWidth = inp.offsetWidth; // حافظ على عرض الحقل داخل الفلتربار

    var wrap = document.createElement("div");
    wrap.className = "hbdate";
    if (prevWidth > 40) wrap.style.minWidth = prevWidth + "px";

    var trigger = document.createElement("button");
    trigger.type = "button";
    trigger.className = "hbdate-trigger";
    trigger.setAttribute("aria-haspopup", "dialog");
    trigger.setAttribute("aria-expanded", "false");
    trigger.innerHTML =
      '<i class="fa-regular fa-calendar hbdate-ic"></i>' +
      '<span class="hbdate-label"></span>' +
      '<i class="fa-solid fa-chevron-down hbdate-caret"></i>';
    var labelSpan = trigger.querySelector(".hbdate-label");

    var panel = document.createElement("div");
    panel.className = "hbdate-panel";
    panel.setAttribute("role", "dialog");
    panel.setAttribute("aria-label", "اختيار التاريخ");
    panel.hidden = true;

    /* حالة العرض الحالية للوحة (الشهر/السنة المعروضان + الوقت المؤقت) */
    var view = { y: 0, m: 1 };      // الشهر المعروض (m: 1-12)
    var timeState = { hh: 0, mm: 0 }; // وقت الـ datetime قبل التأكيد

    // قيود min/max من سمات الحقل (تُقرأ عند كل فتح — قد تتغير برمجيًا)
    var limits = { min: null, max: null };
    function readLimits() {
      limits.min = parseValue(inp.getAttribute("min"));
      limits.max = parseValue(inp.getAttribute("max"));
    }
    function inRange(y, m, d) {
      var k = dayKey(y, m, d);
      if (limits.min && k < dayKey(limits.min.y, limits.min.m, limits.min.d)) return false;
      if (limits.max && k > dayKey(limits.max.y, limits.max.m, limits.max.d)) return false;
      return true;
    }

    // مزامنة نص الزر مع قيمة الحقل (تعمل أيضًا عند التغيير البرمجي)
    function syncLabel() {
      var p = parseValue(inp.value);
      if (p) {
        labelSpan.textContent = formatArabic(p, withTime);
        labelSpan.classList.remove("is-placeholder");
      } else {
        labelSpan.textContent = inp.getAttribute("data-placeholder") ||
          (withTime ? "اختر التاريخ والوقت" : "اختر التاريخ");
        labelSpan.classList.add("is-placeholder");
      }
    }

    // كتابة القيمة النهائية بصيغة ISO في الحقل الأصلي + إطلاق الأحداث
    // حتى تعمل الفلاتر/التحقق/الـ JS القديم المستمع لـ change كما كان.
    function commit(y, m, d) {
      var v = y + "-" + pad2(m) + "-" + pad2(d);
      if (withTime) v += "T" + pad2(timeState.hh) + ":" + pad2(timeState.mm);
      inp.value = v;
      inp.dispatchEvent(new Event("input", { bubbles: true }));
      inp.dispatchEvent(new Event("change", { bubbles: true }));
      syncLabel();
    }

    /* ── بناء اللوحة ─────────────────────────────────────────────── */

    function build() {
      panel.innerHTML = "";

      /* الرأس: سهم الشهر السابق/التالي + اسم الشهر + منتقي سنة */
      var head = document.createElement("div");
      head.className = "hbdate-head";

      // بالواجهة RTL: «السابق» يتجه يمينًا (chevron-right) و«التالي» يسارًا
      var prevBtn = navBtn("fa-chevron-right", "الشهر السابق", -1);
      var nextBtn = navBtn("fa-chevron-left", "الشهر التالي", +1);

      var title = document.createElement("div");
      title.className = "hbdate-title";
      title.innerHTML =
        '<span class="hbdate-month"></span>' +
        '<span class="hbdate-mnum"></span>';
      title.querySelector(".hbdate-month").textContent = MONTHS[view.m - 1];
      title.querySelector(".hbdate-mnum").textContent = "(" + view.m + ")";

      // منتقي السنة: زرّا ± حول رقم السنة — أبسط وأسرع من قائمة طويلة
      var yearBox = document.createElement("div");
      yearBox.className = "hbdate-year";
      var yDown = smallBtn("fa-minus", "سنة أقل");
      var yLabel = document.createElement("span");
      yLabel.className = "hbdate-year-num";
      yLabel.textContent = String(view.y);
      var yUp = smallBtn("fa-plus", "سنة أكثر");
      yDown.addEventListener("click", function () { view.y--; build(); });
      yUp.addEventListener("click", function () { view.y++; build(); });
      yearBox.appendChild(yDown);
      yearBox.appendChild(yLabel);
      yearBox.appendChild(yUp);

      head.appendChild(prevBtn);
      head.appendChild(title);
      head.appendChild(yearBox);
      head.appendChild(nextBtn);
      panel.appendChild(head);

      /* صف أسماء الأيام */
      var wk = document.createElement("div");
      wk.className = "hbdate-week";
      WEEKDAYS.forEach(function (w) {
        var c = document.createElement("span");
        c.textContent = w;
        wk.appendChild(c);
      });
      panel.appendChild(wk);

      /* شبكة الأيام — 6 أسطر × 7 أعمدة، الأحد أول عمود (RTL: أقصى اليمين) */
      var grid = document.createElement("div");
      grid.className = "hbdate-grid";

      var sel = parseValue(inp.value);
      var now = new Date();
      var todayK = dayKey(now.getFullYear(), now.getMonth() + 1, now.getDate());
      var selK = sel ? dayKey(sel.y, sel.m, sel.d) : -1;

      var firstDow = new Date(view.y, view.m - 1, 1).getDay(); // 0=أحد
      var dim = daysInMonth(view.y, view.m);

      // خانات فارغة قبل أول يوم بالشهر (محاذاة عمود اليوم الصحيح)
      for (var b = 0; b < firstDow; b++) {
        var blank = document.createElement("span");
        blank.className = "hbdate-blank";
        grid.appendChild(blank);
      }
      for (var d = 1; d <= dim; d++) {
        (function (day) {
          var k = dayKey(view.y, view.m, day);
          var cell = document.createElement("button");
          cell.type = "button";
          cell.className = "hbdate-day" +
            (k === selK ? " is-selected" : "") +
            (k === todayK ? " is-today" : "");
          cell.textContent = String(day);
          if (!inRange(view.y, view.m, day)) {
            cell.disabled = true;
            cell.classList.add("is-disabled");
          } else {
            cell.addEventListener("click", function () {
              commit(view.y, view.m, day);
              if (!withTime) { // مع الوقت نُبقي اللوحة مفتوحة لضبط الساعة
                closeOpen();
                trigger.focus();
              } else {
                build(); // أعد الرسم لإظهار التمييز على اليوم الجديد
              }
            });
          }
          grid.appendChild(cell);
        })(d);
      }
      panel.appendChild(grid);

      /* صف الوقت — للـ datetime-local فقط: ساعة/دقيقة */
      if (withTime) {
        var trow = document.createElement("div");
        trow.className = "hbdate-time";
        trow.innerHTML =
          '<i class="fa-regular fa-clock"></i>' +
          '<span class="hbdate-time-label">الوقت</span>';
        var hhIn = timeInput(23, timeState.hh);
        var sep = document.createElement("span");
        sep.className = "hbdate-time-sep";
        sep.textContent = ":";
        var mmIn = timeInput(59, timeState.mm);
        // عرض LTR ‏HH:MM داخل الصف العربي
        var tWrap = document.createElement("span");
        tWrap.className = "hbdate-time-fields";
        tWrap.appendChild(hhIn);
        tWrap.appendChild(sep);
        tWrap.appendChild(mmIn);
        trow.appendChild(tWrap);

        function onTime() {
          timeState.hh = Math.min(23, Math.max(0, parseInt(hhIn.value, 10) || 0));
          timeState.mm = Math.min(59, Math.max(0, parseInt(mmIn.value, 10) || 0));
          var p = parseValue(inp.value);
          if (p) commit(p.y, p.m, p.d); // حدّث الوقت على التاريخ المختار فورًا
        }
        hhIn.addEventListener("change", onTime);
        mmIn.addEventListener("change", onTime);
        trow.__hh = hhIn; trow.__mm = mmIn;
        panel.appendChild(trow);
      }

      /* التذييل: «اليوم» يقفز للتاريخ الحالي · «مسح» يفرّغ الحقل */
      var foot = document.createElement("div");
      foot.className = "hbdate-foot";

      var todayBtn = document.createElement("button");
      todayBtn.type = "button";
      todayBtn.className = "hbdate-foot-btn hbdate-today-btn";
      todayBtn.innerHTML = '<i class="fa-solid fa-calendar-day"></i> اليوم';
      todayBtn.addEventListener("click", function () {
        var n = new Date();
        if (!inRange(n.getFullYear(), n.getMonth() + 1, n.getDate())) return;
        view.y = n.getFullYear(); view.m = n.getMonth() + 1;
        commit(view.y, view.m, n.getDate());
        if (withTime) build();
        else { closeOpen(); trigger.focus(); }
      });

      var clearBtn = document.createElement("button");
      clearBtn.type = "button";
      clearBtn.className = "hbdate-foot-btn hbdate-clear-btn";
      clearBtn.innerHTML = '<i class="fa-regular fa-circle-xmark"></i> مسح';
      clearBtn.addEventListener("click", function () {
        inp.value = "";
        inp.dispatchEvent(new Event("input", { bubbles: true }));
        inp.dispatchEvent(new Event("change", { bubbles: true }));
        syncLabel();
        closeOpen();
        trigger.focus();
      });

      if (withTime) {
        // زر تأكيد صريح لإغلاق اللوحة بعد ضبط الوقت
        var okBtn = document.createElement("button");
        okBtn.type = "button";
        okBtn.className = "hbdate-foot-btn hbdate-ok-btn";
        okBtn.innerHTML = '<i class="fa-solid fa-check"></i> تم';
        okBtn.addEventListener("click", function () {
          closeOpen();
          trigger.focus();
        });
        foot.appendChild(okBtn);
      }
      foot.appendChild(todayBtn);
      foot.appendChild(clearBtn);
      panel.appendChild(foot);

      // إعادة الرسم أثناء الفتح (تنقّل بين الأشهر) تتطلب إعادة تموضع
      // لأن ارتفاع الشبكة قد يتغير (4-6 أسطر)
      if (!panel.hidden) positionPanel();
    }

    function navBtn(icon, label, delta) {
      var b = document.createElement("button");
      b.type = "button";
      b.className = "hbdate-nav";
      b.setAttribute("aria-label", label);
      b.innerHTML = '<i class="fa-solid ' + icon + '"></i>';
      b.addEventListener("click", function () {
        view.m += delta;
        if (view.m < 1) { view.m = 12; view.y--; }
        if (view.m > 12) { view.m = 1; view.y++; }
        build();
      });
      return b;
    }

    function smallBtn(icon, label) {
      var b = document.createElement("button");
      b.type = "button";
      b.className = "hbdate-ybtn";
      b.setAttribute("aria-label", label);
      b.innerHTML = '<i class="fa-solid ' + icon + '"></i>';
      return b;
    }

    function timeInput(max, val) {
      var t = document.createElement("input");
      t.type = "number";
      t.min = "0"; t.max = String(max);
      t.value = pad2(val);
      t.className = "hbdate-time-in";
      t.setAttribute("inputmode", "numeric");
      return t;
    }

    /* ── التموضع والفتح/الإغلاق (نفس منطق hub_select حرفيًا) ─────── */

    function positionPanel() {
      // اللوحة portal مثبتة على الشاشة حتى لا يقصها أي overflow:hidden،
      // وتنقلب للأعلى تلقائيًا قرب أسفل الشاشة
      var r = trigger.getBoundingClientRect();
      panel.style.position = "fixed";
      // z مرتفع جدًا حتى تعلو فوق أي طبقة مودال بالموقع
      panel.style.zIndex = "99999";
      panel.style.insetInlineStart = "auto";
      var pw = panel.offsetWidth || 280;
      var ph = panel.offsetHeight || 320;
      var isRTL = (document.documentElement.dir || "rtl") !== "ltr";
      var left = isRTL ? (r.right - pw) : r.left;
      left = Math.max(8, Math.min(left, window.innerWidth - pw - 8));
      panel.style.left = left + "px";
      var spaceBelow = window.innerHeight - r.bottom;
      if (spaceBelow < ph + 12 && r.top > ph + 12) {
        panel.style.top = "auto";
        panel.style.bottom = (window.innerHeight - r.top + 6) + "px";
      } else {
        panel.style.bottom = "auto";
        panel.style.top = (r.bottom + 6) + "px";
      }
    }

    function open() {
      closeOpen();
      readLimits();
      // الشهر المعروض: قيمة الحقل إن وجدت، وإلا اليوم (مقصوصًا على المدى)
      var p = parseValue(inp.value);
      var n = new Date();
      if (p) {
        view.y = p.y; view.m = p.m;
        timeState.hh = p.hh; timeState.mm = p.mm;
      } else {
        view.y = n.getFullYear(); view.m = n.getMonth() + 1;
        if (limits.min && dayKey(view.y, view.m, daysInMonth(view.y, view.m)) <
            dayKey(limits.min.y, limits.min.m, limits.min.d)) {
          view.y = limits.min.y; view.m = limits.min.m;
        }
        if (limits.max && dayKey(view.y, view.m, 1) >
            dayKey(limits.max.y, limits.max.m, limits.max.d)) {
          view.y = limits.max.y; view.m = limits.max.m;
        }
        timeState.hh = n.getHours(); timeState.mm = 0;
      }
      build();
      // portal — فوق كل شيء. داخل <dialog> أصلي (top-layer) نلصقها
      // بالـ dialog نفسه وإلا تختفي خلفه، وفي غير ذلك بالـ body.
      var host = trigger.closest("dialog") || document.body;
      host.appendChild(panel);
      panel.hidden = false;
      positionPanel();
      trigger.setAttribute("aria-expanded", "true");
      wrap.classList.add("is-open");
      OPEN = { panel: panel, trigger: trigger, wrap: wrap };
    }

    trigger.addEventListener("click", function () {
      panel.hidden ? open() : closeOpen();
    });
    trigger.addEventListener("keydown", function (e) {
      if (e.key === "ArrowDown" || e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        if (panel.hidden) open();
      }
    });
    panel.addEventListener("keydown", function (e) {
      if (e.key === "Escape") {
        closeOpen();
        trigger.focus();
      }
    });

    // أي تغيير برمجي على الحقل الأصلي (JS قديم يعبّئ from/to مثلًا)
    // ينعكس على نص الزر فورًا
    inp.addEventListener("change", syncLabel);
    inp.addEventListener("input", syncLabel);

    // فشل تحقق الفورم (required على الحقل المخفي) → ركّز الزر بدل
    // خطأ "not focusable" الصامت
    inp.addEventListener("invalid", function (e) {
      e.preventDefault();
      trigger.focus();
      wrap.classList.add("is-invalid");
      setTimeout(function () { wrap.classList.remove("is-invalid"); }, 1200);
    });

    // الحقل الأصلي: مخفي بصريًا فقط (يبقى حامل القيمة للفورم) + كل
    // طرق فتح التقويم الأصلي معطّلة احتياطًا
    inp.classList.add("hbdate-native");
    inp.tabIndex = -1;
    inp.setAttribute("aria-hidden", "true");
    inp.setAttribute("inputmode", "none");
    inp.addEventListener("mousedown", function (e) { e.preventDefault(); });
    inp.addEventListener("focus", function () { trigger.focus(); });
    try { inp.showPicker = function () {}; } catch (_) {}

    inp.parentNode.insertBefore(wrap, inp);
    wrap.appendChild(inp);
    wrap.appendChild(trigger);
    wrap.appendChild(panel);
    syncLabel();
  }

  /* ── الإغلاق العام + المستمعون على مستوى الصفحة ──────────────────── */

  function closeOpen() {
    if (!OPEN) return;
    OPEN.panel.hidden = true;
    // أعد اللوحة لحاضنتها بعد الإغلاق (كانت portal على body أو dialog)
    if (OPEN.panel.parentNode !== OPEN.wrap) OPEN.wrap.appendChild(OPEN.panel);
    OPEN.trigger.setAttribute("aria-expanded", "false");
    OPEN.wrap.classList.remove("is-open");
    OPEN = null;
  }

  document.addEventListener("click", function (e) {
    if (!OPEN) return;
    // أزرار التنقل/الأيام تعيد بناء اللوحة (innerHTML) قبل وصول الحدث
    // هنا، فيصبح الهدف منفصلًا عن الـ DOM — لا تعتبره نقرة خارجية
    // وإلا انغلقت اللوحة عند كل تنقّل بين الأشهر أو اختيار يوم مع وقت.
    if (e.target && e.target.nodeType === 1 && !e.target.isConnected) return;
    if (!OPEN.wrap.contains(e.target) && !OPEN.panel.contains(e.target)) closeOpen();
  });
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape") closeOpen();
  });
  // إغلاق أي <dialog> أصلي يقفل اللوحة المفتوحة بداخله حتى لا تبقى
  // عالقة عند إعادة فتح المودال
  document.addEventListener("close", function (e) {
    if (OPEN && e.target && e.target.tagName === "DIALOG") closeOpen();
  }, true);
  // التمرير خارج اللوحة يقفلها (لأنها مثبتة على الشاشة) — التمرير
  // داخلها مسموح
  window.addEventListener("scroll", function (e) {
    if (!OPEN) return;
    if (e.target && e.target.nodeType === 1 && OPEN.panel.contains(e.target)) return;
    closeOpen();
  }, true);
  window.addEventListener("resize", function () { if (OPEN) closeOpen(); });

  function init() {
    var inputs = document.querySelectorAll('input[type="date"], input[type="datetime-local"]');
    Array.prototype.forEach.call(inputs, enhance);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }

  // الحقول المُنشأة ديناميكيًا (مودالات JS مثل لقطة السنابشوت)
  // تترقّى تلقائيًا فور إضافتها للصفحة
  var mo = new MutationObserver(function (muts) {
    for (var i = 0; i < muts.length; i++) {
      var added = muts[i].addedNodes;
      for (var j = 0; j < added.length; j++) {
        var n = added[j];
        if (n.nodeType !== 1) continue;
        if (n.tagName === "INPUT") enhance(n);
        else if (n.querySelectorAll) {
          Array.prototype.forEach.call(
            n.querySelectorAll('input[type="date"], input[type="datetime-local"]'),
            enhance
          );
        }
      }
    }
  });
  mo.observe(document.documentElement, { childList: true, subtree: true });
})();
