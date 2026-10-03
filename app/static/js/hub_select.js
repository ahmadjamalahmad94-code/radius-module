/* ════════════════════════════════════════════════════════════════════
   HUB SELECT — قائمة منسدلة حديثة موحّدة لكل الموقع.
   يلفّ كل <select class="hub-select"> أو <select class="hub-input">
   تلقائيًا بواجهة مخصّصة (زر + لوحة عائمة بنمط usq-menu): زوايا ناعمة،
   ظل خفيف، تمييز بنفسجي للعنصر المحدّد، بحث فوري عند +8 خيارات،
   وتنقّل كامل بالكيبورد. الـ <select> الأصلي يبقى مخفيًا في الـ DOM
   فتستمر الفورمات والـ JS القديم (change events) بالعمل كما هي.

   استثناءات: select[multiple] أو select[data-native] تُترك أصلية.
   ════════════════════════════════════════════════════════════════════ */
var hrT = window.hrT || function (s, o) { var d = window.HR_I18N || {}; var t = Object.prototype.hasOwnProperty.call(d, s) ? d[s] : s; if (o) { for (var k in o) { t = String(t).split('{' + k + '}').join(o[k]); } } return t; };  // i18n — انظر I18N.md
(function () {
  "use strict";
  if (window.__hubSelectInit) return;
  window.__hubSelectInit = true;

  var OPEN = null; // اللوحة المفتوحة حاليًا (واحدة فقط)

  function closeOpen() {
    if (!OPEN) return;
    OPEN.panel.hidden = true;
    // أعد اللوحة لحاضنتها بعد الإغلاق (كانت portal على body أو dialog)
    if (OPEN.panel.parentNode !== OPEN.wrap) OPEN.wrap.appendChild(OPEN.panel);
    OPEN.trigger.setAttribute("aria-expanded", "false");
    OPEN.wrap.classList.remove("is-open");
    OPEN = null;
  }

  function label(opt) {
    return (opt.textContent || "").trim() || opt.value;
  }

  function enhance(sel) {
    if (sel.__hbsel || sel.multiple || sel.hasAttribute("data-native")) return;
    if (sel.classList.contains("so-period-pick")) return; // chip خاص بنظرة عامة
    if (sel.options.length === 0) return;
    sel.__hbsel = true;
    var prevWidth = sel.offsetWidth; // حافظ على عرض الحقل الأصلي داخل الفلتربار

    var wrap = document.createElement("div");
    wrap.className = "hbsel";
    // minWidth يحفظ عرضَ الحقلِ الأصليِّ داخلَ شريطِ الفلاتر، لكنّه كان
    // يُطبَّق كما هو فيدفع الواجهةَ خارجَ الشاشةِ على جوّالٍ عريضُه 360
    // (رُصد: ‎.hbsel-trigger عند left=-11..-45 في /cards/batches
    // و/reports/user_events و/reports/manager_events). نُقيّده بالمساحةِ
    // الفعليّةِ للحاضن ونمنع تجاوزَها.
    wrap.style.maxWidth = "100%";
    var hostW = (sel.parentElement && sel.parentElement.clientWidth) || 0;
    var capW = hostW > 40 ? Math.min(prevWidth, hostW) : prevWidth;
    if (capW > 40) wrap.style.minWidth = capW + "px";

    var trigger = document.createElement("button");
    trigger.type = "button";
    trigger.className = "hbsel-trigger";
    trigger.setAttribute("aria-haspopup", "listbox");
    trigger.setAttribute("aria-expanded", "false");

    var labelSpan = document.createElement("span");
    labelSpan.className = "hbsel-label";
    var caret = document.createElement("i");
    caret.className = "fa-solid fa-chevron-down hbsel-caret";
    trigger.appendChild(labelSpan);
    trigger.appendChild(caret);

    var panel = document.createElement("div");
    panel.className = "hbsel-panel";
    panel.setAttribute("role", "listbox");
    panel.hidden = true;

    var search = null;
    if (sel.options.length > 8) {
      search = document.createElement("input");
      search.type = "text";
      search.className = "hbsel-search";
      // يمكن تخصيص نص البحث لكل select عبر data-search-placeholder
      // (مثل قائمة المشتركين: "ابحث بالاسم أو اليوزر...")
      search.placeholder = sel.getAttribute("data-search-placeholder") || hrT('بحث...');
      panel.appendChild(search);
    }

    var list = document.createElement("div");
    list.className = "hbsel-list";
    panel.appendChild(list);

    function syncLabel() {
      var opt = sel.options[sel.selectedIndex];
      labelSpan.textContent = opt ? label(opt) : "";
      labelSpan.classList.toggle("is-placeholder", !!opt && opt.value === "");
    }

    function buildList(filter) {
      list.innerHTML = "";
      var needle = (filter || "").trim().toLowerCase();
      Array.prototype.forEach.call(sel.options, function (opt, i) {
        if (opt.disabled || opt.hidden) return;
        var text = label(opt);
        if (needle && text.toLowerCase().indexOf(needle) === -1) return;
        var item = document.createElement("button");
        item.type = "button";
        item.className = "hbsel-item" + (i === sel.selectedIndex ? " is-selected" : "");
        item.setAttribute("role", "option");
        item.dataset.index = String(i);
        item.innerHTML =
          '<span class="hbsel-item-text"></span>' +
          '<i class="fa-solid fa-check hbsel-check"></i>';
        item.querySelector(".hbsel-item-text").textContent = text;
        item.addEventListener("click", function () {
          sel.selectedIndex = i;
          sel.dispatchEvent(new Event("change", { bubbles: true }));
          syncLabel();
          closeOpen();
          trigger.focus();
        });
        list.appendChild(item);
      });
      if (!list.children.length) {
        var empty = document.createElement("div");
        empty.className = "hbsel-empty";
        empty.textContent = hrT('لا نتائج');
        list.appendChild(empty);
      }
    }

    function positionPanel() {
      // اللوحة تنفصل عن مكانها وتتثبت على الشاشة (portal) حتى لا يقصها أي
      // overflow:hidden في الأقسام، وتنفتح للأعلى تلقائيًا قرب أسفل الشاشة
      // (إصلاح "القائمة بتفتح بالخلفية مش بالمقدمة").
      var r = trigger.getBoundingClientRect();
      panel.style.position = "fixed";
      // z مرتفع جدًا حتى تعلو اللوحة فوق أي طبقة مودال بالموقع
      // (ff-modal=1200 / uds-modal=1000 / القوائم=900) — إصلاح "القائمة لا تفتح".
      panel.style.zIndex = "99999";
      panel.style.minWidth = r.width + "px";
      panel.style.insetInlineStart = "auto";
      panel.style.maxHeight = "";  // قِسِ الارتفاعَ الطبيعيّ لا المقيَّدَ من تموضعٍ سابق
      var pw = panel.offsetWidth || r.width;
      var ph = panel.offsetHeight || 200;
      var isRTL = (document.documentElement.dir || "rtl") !== "ltr";
      var left = isRTL ? (r.right - pw) : r.left;
      left = Math.max(8, Math.min(left, window.innerWidth - pw - 8));
      panel.style.left = left + "px";
      // سقفُ ارتفاعٍ وتمريرٌ داخليّ: بلا max-height كانت لوحةٌ بعشرةِ خيارات
      // (326px) تخرج 33px من منفذِ 640، ومع لوحةِ المفاتيح (371px) تخرج
      // 302px — فلا يرى المستخدمُ الباقاتِ ولا يستطيع تمريرَها. نحسب
      // المساحةَ من الطرفَين ونختار أوسعَهما ثمّ نُقيّد اللوحةَ بها.
      var GAP = 6, EDGE = 8;
      var vh = window.innerHeight;
      // 🔴 الشريطُ المرئيّ فعلًا: على iOS Safari وكروم أندرويد (≥108، الافتراضيّ
      //    resizes-visual) لوحةُ المفاتيح لا تُغيّر innerHeight بل تُقلّص
      //    visualViewport وحدَه فتُغطّي أسفلَ المنفذ. كان الحسابُ على innerHeight
      //    فتُرسَم اللوحةُ تحت لوحةِ المفاتيح (رُصد: bottom=613 والمرئيُّ 371 —
      //    عنصرٌ واحدٌ من تسعِ باقاتٍ ظاهر). نُقيّد بالشريطِ المرئيّ بإحداثيّاتِ
      //    المنفذِ التخطيطيّ (التي يُقاس بها position:fixed).
      var vv = window.visualViewport;
      var vTop = vv ? Math.max(0, vv.offsetTop) : 0;
      var vBot = vv ? Math.min(vh, vv.offsetTop + vv.height) : vh;
      var spaceBelow = vBot - r.bottom - GAP - EDGE;
      var spaceAbove = r.top - vTop - GAP - EDGE;
      var openUp = (spaceBelow < Math.min(ph, 160)) && (spaceAbove > spaceBelow);
      var avail = Math.max(120, openUp ? spaceAbove : spaceBelow);
      panel.style.maxHeight = avail + "px";
      panel.style.overflowY = "auto";
      var hh = Math.min(ph, avail);
      if (openUp) {
        panel.style.top = "auto";
        panel.style.bottom = Math.max(vh - vBot + EDGE, vh - r.top + GAP) + "px";
      } else {
        panel.style.bottom = "auto";
        // تثبيتٌ داخلَ المنفذ: المرساةُ قد تُصبح فوقَ الحدِّ الأعلى أو تحتَ
        // الأسفلِ (انطواءُ شريطِ العنوان، لوحةُ المفاتيح) فنُزلق اللوحةَ
        // لتبقى مرئيّةً بدلًا من أن تختفي.
        var top = r.bottom + GAP;
        top = Math.min(top, vBot - hh - EDGE);
        top = Math.max(vTop + EDGE, top);
        panel.style.top = top + "px";
      }
    }

    function open() {
      closeOpen();
      buildList("");
      if (search) search.value = "";
      // portal — فوق كل شيء. داخل <dialog> أصلي (top-layer) نلصقها
      // بالـ dialog نفسه وإلا تختفي خلفه، وفي غير ذلك بالـ body.
      var host = trigger.closest("dialog") || document.body;
      host.appendChild(panel);
      panel.hidden = false;
      positionPanel();
      trigger.setAttribute("aria-expanded", "true");
      wrap.classList.add("is-open");
      OPEN = { panel: panel, trigger: trigger, wrap: wrap,
               position: positionPanel };
      OPENED_AT = Date.now();
      var selItem = list.querySelector(".is-selected");
      if (selItem) selItem.scrollIntoView({ block: "nearest" });
      // 🔴 على الجوّال: تركيزُ خانة البحث يستدعي لوحةَ المفاتيح، وظهورُها
      //    يُقلّص ارتفاعَ النافذة فيُطلق `resize` فتُغلق اللوحةُ فورًا —
      //    فيبدو للمستخدم أنّ القائمة «تُقفل لوحدها» كلّما ضغطها.
      //    (بلاغ 2026-08-26: «اختر عرض» في توليد الحِزم من الجوّال.)
      if (search && !COARSE) search.focus();
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
      var items = Array.prototype.slice.call(list.querySelectorAll(".hbsel-item"));
      if (!items.length) return;
      var idx = items.indexOf(document.activeElement);
      if (e.key === "ArrowDown") {
        e.preventDefault();
        (items[idx + 1] || items[0]).focus();
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        (items[idx - 1] || items[items.length - 1]).focus();
      } else if (e.key === "Escape") {
        closeOpen();
        trigger.focus();
      }
    });

    if (search) {
      search.addEventListener("input", function () { buildList(search.value); });
    }

    // أي تغيير برمجي على الـ select الأصلي ينعكس على الزر
    sel.addEventListener("change", syncLabel);

    sel.classList.add("hbsel-native");
    sel.tabIndex = -1;
    sel.setAttribute("aria-hidden", "true");
    sel.parentNode.insertBefore(wrap, sel);
    wrap.appendChild(sel);
    wrap.appendChild(trigger);
    wrap.appendChild(panel);
    syncLabel();
  }

  function init() {
    // كل قوائم الموقع — مش بس hub-select — عشان ما يضل ولا select تقليدي
    var sels = document.querySelectorAll("select");
    Array.prototype.forEach.call(sels, enhance);
  }

  // جهازٌ لمسيّ؟ (مؤشّرٌ خشن = إصبع) — يُقرَّر مرّةً لا عند كلّ فتح.
  var COARSE = !!(window.matchMedia &&
                  window.matchMedia("(pointer: coarse)").matches);
  var OPENED_AT = 0;

  document.addEventListener("click", function (e) {
    if (OPEN && !OPEN.wrap.contains(e.target) && !OPEN.panel.contains(e.target)) closeOpen();
  });
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape") closeOpen();
  });
  // إغلاق أي <dialog> أصلي (زر X أو Esc) يقفل اللوحة المفتوحة بداخله
  // حتى لا تبقى عالقة عند إعادة فتح المودال.
  document.addEventListener("close", function (e) {
    if (OPEN && e.target && e.target.tagName === "DIALOG") closeOpen();
  }, true);
  // التمرير أو تغيير الحجم يقفل اللوحة (لأنها مثبتة على الشاشة) —
  // باستثناء التمرير داخل اللوحة نفسها (قائمة الخيارات الطويلة) وإلا
  // كانت تنغلق فور محاولة التصفح بين مئات المشتركين.
  var trackRaf = 0;
  window.addEventListener("scroll", function (e) {
    if (!OPEN) return;
    // نافذةُ سماحٍ قصيرةٌ بعد الفتح: ظهورُ لوحة المفاتيح يُمرّر الصفحةَ
    // تلقائيًّا، وذاك تمريرٌ لم يطلبه المستخدم فلا يُغلق قائمتَه.
    if (Date.now() - OPENED_AT < 400) return;
    if (e.target && e.target.nodeType === 1 && OPEN.panel.contains(e.target)) return;
    // 🔴 على الجوّال لا يُغلق التمريرُ شيئًا — يُعيد التموضعَ فقط.
    //    شريطُ عنوان أندرويد ينطوي من تلقائه بعد لحظةٍ فيُطلق `scroll`
    //    والمستخدمُ لم يمسّ الشاشة، فتموت القائمةُ أمام عينيه.
    //    مهلةُ الـ400ms لا تكفي: انطواءُ الشريط ومواضعةُ العرض
    //    يستغرقان أطول. (بلاغ سمير 2026-09-09: «ما بتفتح».)
    //    واللوحةُ `position: fixed` فتتبُّعُ الزرّ يُبقيها ملتصقةً به.
    if (COARSE) {
      if (trackRaf) return;
      trackRaf = requestAnimationFrame(function () {
        trackRaf = 0;
        if (!OPEN) return;
        var r = OPEN.trigger.getBoundingClientRect();
        // كان: «خرج الزرُّ من الشاشة ⇒ أُغلق». ولمسُ الزرِّ نفسِه يُركّزه
        // فيُلصقه المتصفّحُ بالحدِّ الأعلى (top=0)، فأيُّ تمريرٍ تالٍ — بل
        // انطواءُ شريطِ عنوانِ أندرويد وحده — يُخرجه فورًا فتموت القائمةُ في
        // لحظةِ فتحها («تفتح وتختفي بسرعة»). الآن نُغلق فقط إذا بَعُدت
        // المرساةُ منفذًا كاملًا، وما دونَ ذلك نُعيد التموضعَ واللوحةُ مُقيَّدة.
        var vh2 = window.innerHeight;
        if (r.bottom < -vh2 || r.top > vh2 * 2) { closeOpen(); return; }
        OPEN.position();
      });
      return;
    }
    closeOpen();
  }, true);
  // ارتفاعٌ متغيّرٌ وحدَه = لوحةُ مفاتيحَ ظهرت أو اختفت — لا دورانَ شاشةٍ ولا
  // تغييرَ حجمِ نافذة. والإغلاقُ عليه يقتل القائمةَ في لحظة فتحها ذاتِها.
  var LAST_W = window.innerWidth;
  window.addEventListener("resize", function () {
    var w = window.innerWidth;
    if (w === LAST_W) {
      // ارتفاعٌ وحدَه تغيّر = لوحةُ مفاتيح. كان المعالجُ «يعود» فلا يُغلق
      // (صحيح) ولا يُعيد التموضعَ (خطأ): اللوحةُ position:fixed فتبقى حيثُ
      // كانت، فتصير كلُّها تحتَ لوحةِ المفاتيحِ خارجَ الشاشة.
      if (OPEN) OPEN.position();
      return;
    }
    LAST_W = w;
    if (OPEN) closeOpen();
  });

  // لوحةُ المفاتيح على iOS/كروم الحديث تُغيّر visualViewport لا innerHeight —
  // فلا يصل `resize` النافذة. نُعيد التموضعَ على حدثَي الشريطِ المرئيّ أيضًا.
  if (window.visualViewport) {
    var vvTick = function () { if (OPEN) OPEN.position(); };
    window.visualViewport.addEventListener("resize", vvTick);
    window.visualViewport.addEventListener("scroll", vvTick);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }

  // القوائم المُنشأة ديناميكيًا (مثل "صفوف بالصفحة" في الجداول الموحدة)
  // تترقّى تلقائيًا فور إضافتها للصفحة.
  var mo = new MutationObserver(function (muts) {
    for (var i = 0; i < muts.length; i++) {
      var added = muts[i].addedNodes;
      for (var j = 0; j < added.length; j++) {
        var n = added[j];
        if (n.nodeType !== 1) continue;
        if (n.tagName === "SELECT") enhance(n);
        else if (n.querySelectorAll) Array.prototype.forEach.call(n.querySelectorAll("select"), enhance);
      }
    }
  });
  mo.observe(document.documentElement, { childList: true, subtree: true });
})();
