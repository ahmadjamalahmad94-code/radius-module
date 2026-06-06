/* ╔════════════════════════════════════════════════════════════════════╗
   ║  HobeHub — Sidebar Visual Rebuild (behaviour)                        ║
   ║                                                                      ║
   ║  Responsibilities:                                                   ║
   ║    1. Restore collapsed / open-sections state from localStorage      ║
   ║    2. Toggle collapsed (desktop) — persist                           ║
   ║    3. Toggle individual sections — persist                           ║
   ║    4. Mobile drawer open/close + overlay click                       ║
   ║    5. Auto-open the section that contains the active route          ║
   ║                                                                      ║
   ║  localStorage keys:                                                  ║
   ║    hobe_sidebar_collapsed        "0" | "1"                           ║
   ║    hobe_sidebar_open_sections    JSON array of section ids           ║
   ║    hobe_sidebar_open_subgroups   JSON array of sub-group ids         ║
   ║      (عوائل التقارير القابلة للطي — نفس آلية الأقسام بمفتاح مستقل)   ║
   ║                                                                      ║
   ║  State driver: <body data-hb-side="opened|collapsed|mobile-open">    ║
   ╚════════════════════════════════════════════════════════════════════╝ */

(function(){
  'use strict';

  var LS_COLLAPSED = 'hobe_sidebar_collapsed';
  var LS_OPEN_SECS = 'hobe_sidebar_open_sections';
  var LS_OPEN_SUBS = 'hobe_sidebar_open_subgroups'; // عوائل فرعية قابلة للطي (التقارير)
  var MOBILE_BP    = 900;

  // ─── small storage helpers (swallow exceptions in private-mode browsers) ───
  function lsGet(k){ try { return localStorage.getItem(k); } catch(_){ return null; } }
  function lsSet(k,v){ try { localStorage.setItem(k,v); } catch(_){} }

  function readOpenList(key){
    var raw = lsGet(key);
    if (!raw) return [];
    try {
      var arr = JSON.parse(raw);
      return Array.isArray(arr) ? arr.filter(function(x){ return typeof x === 'string'; }) : [];
    } catch(_){ return []; }
  }
  function writeOpenList(key, list){
    lsSet(key, JSON.stringify(list));
  }
  // أسماء متوافقة مع الكود القديم — الأقسام الرئيسية
  function readOpenSections(){ return readOpenList(LS_OPEN_SECS); }
  function writeOpenSections(list){ writeOpenList(LS_OPEN_SECS, list); }

  function isMobile(){ return window.matchMedia('(max-width: ' + MOBILE_BP + 'px)').matches; }

  // ─── apply state to body ───
  function setSideMode(mode){
    document.body.setAttribute('data-hb-side', mode);
  }
  function currentMode(){
    return document.body.getAttribute('data-hb-side') || 'opened';
  }

  // ─── INITIALISE ON DOM READY ───
  function init(){
    var side = document.getElementById('hb-side');
    if (!side) return; // sidebar not on this page — nothing to wire

    // 1) Initial mode: respect localStorage on desktop, ignore on mobile.
    if (isMobile()){
      setSideMode('opened'); // base state, drawer is closed by default
    } else {
      var collapsed = lsGet(LS_COLLAPSED) === '1';
      setSideMode(collapsed ? 'collapsed' : 'opened');
    }

    // 2) Restore open sections.
    // The template marks `is-open` on sections containing the active route
    // (server-side, via has-active). We layer user-persisted choices on top:
    //   • Any section the user explicitly opened → open it.
    //   • Active section stays open regardless of saved state.
    var savedOpen = readOpenSections();
    var sections = side.querySelectorAll('.hb-side-section');
    sections.forEach(function(sec){
      var id = sec.getAttribute('data-hb-section');
      if (!id) return;
      var hasActive = sec.classList.contains('has-active');
      if (hasActive){
        sec.classList.add('is-open'); // force-open active-bearing section
      } else {
        sec.classList.toggle('is-open', savedOpen.indexOf(id) !== -1);
      }
    });

    // 2-ب) استرجاع حالة العوائل الفرعية (عوائل التقارير القابلة للطي):
    //    نفس عقد الأقسام — مغلقة افتراضيًا، العائلة التي تحمل الصفحة
    //    النشطة (has-active من الخادم) تبقى مفتوحة دائمًا، وما فتحه
    //    المستخدم يدويًا يُستعاد من localStorage.
    var savedSubOpen = readOpenList(LS_OPEN_SUBS);
    side.querySelectorAll('.hb-side-subgroup').forEach(function(grp){
      var gid = grp.getAttribute('data-hb-subgroup');
      if (!gid) return;
      if (grp.classList.contains('has-active')){
        grp.classList.add('is-open'); // عائلة الصفحة النشطة تبقى مفتوحة
      } else {
        grp.classList.toggle('is-open', savedSubOpen.indexOf(gid) !== -1);
      }
    });

    // 3) Wire the collapse button (desktop only — hidden on mobile via CSS).
    side.addEventListener('click', function(e){
      var t = e.target.closest('[data-hb-toggle]');
      if (!t) return;
      var which = t.getAttribute('data-hb-toggle');

      if (which === 'collapse'){
        if (isMobile()) return; // CSS already hides it, but be safe
        var next = currentMode() === 'collapsed' ? 'opened' : 'collapsed';
        setSideMode(next);
        lsSet(LS_COLLAPSED, next === 'collapsed' ? '1' : '0');
        e.preventDefault();
        return;
      }

      if (which === 'mobile-close'){
        setSideMode(isMobile() ? 'opened' : (lsGet(LS_COLLAPSED) === '1' ? 'collapsed' : 'opened'));
        e.preventDefault();
        return;
      }
    });

    // 4) Section toggles.
    side.addEventListener('click', function(e){
      var head = e.target.closest('[data-hb-section-toggle]');
      if (!head) return;
      var sec = head.closest('.hb-side-section');
      if (!sec) return;
      // When collapsed on desktop, clicking a section head should expand
      // the sidebar first (sections aren't really clickable when icons-only)
      if (currentMode() === 'collapsed' && !isMobile()){
        setSideMode('opened');
        lsSet(LS_COLLAPSED, '0');
      }
      sec.classList.toggle('is-open');

      // persist (excluding the active-bearing one — it stays open regardless)
      var openIds = [];
      side.querySelectorAll('.hb-side-section.is-open').forEach(function(s){
        var id = s.getAttribute('data-hb-section');
        if (id) openIds.push(id);
      });
      writeOpenSections(openIds);

      e.preventDefault();
    });

    // 4-ب) تبديل العوائل الفرعية (التقارير): نقرة على رأس العائلة
    //    تفتح/تغلق أبناءها، ثم نحفظ قائمة المفتوح في localStorage —
    //    نفس نمط الأقسام الرئيسية لكن بمفتاح مستقل LS_OPEN_SUBS.
    side.addEventListener('click', function(e){
      var subHead = e.target.closest('[data-hb-subgroup-toggle]');
      if (!subHead) return;
      var grp = subHead.closest('.hb-side-subgroup');
      if (!grp) return;
      grp.classList.toggle('is-open');

      var openSubIds = [];
      side.querySelectorAll('.hb-side-subgroup.is-open').forEach(function(g){
        var gid = g.getAttribute('data-hb-subgroup');
        if (gid) openSubIds.push(gid);
      });
      writeOpenList(LS_OPEN_SUBS, openSubIds);

      e.preventDefault();
    });

    // 5) Overlay click closes mobile drawer.
    var overlay = document.querySelector('.hb-side-overlay');
    if (overlay){
      overlay.addEventListener('click', function(){
        if (isMobile()) setSideMode('opened');
      });
    }

    // 6) Hamburger button (rendered in topbar) opens mobile drawer.
    document.addEventListener('click', function(e){
      var burger = e.target.closest('[data-hb-mobile-open]');
      if (!burger) return;
      if (!isMobile()) return;
      setSideMode('mobile-open');
      e.preventDefault();
    });

    // 7) Esc closes mobile drawer.
    document.addEventListener('keydown', function(e){
      if (e.key !== 'Escape') return;
      if (currentMode() === 'mobile-open') setSideMode('opened');
    });

    // 8) حفظ موضع تمرير السايدبار بين الصفحات:
    //    نخزّن scrollTop في sessionStorage عند التمرير وعند الضغط على أي رابط،
    //    ونعيده عند تحميل الصفحة — حتى ما يرجع السايدبار لفوق بعد التنقل.
    var SS_SCROLL = 'hobe_sidebar_scroll';
    var nav = side.querySelector('.hb-side-nav');
    if (nav){
      // استرجاع الموضع المحفوظ (إن وجد)
      try {
        var savedScroll = sessionStorage.getItem(SS_SCROLL);
        if (savedScroll !== null) nav.scrollTop = parseInt(savedScroll, 10) || 0;
      } catch(_){}

      // حفظ الموضع أثناء التمرير (بدون إغراق التخزين — نكتفي بآخر قيمة)
      var scrollSaveTimer = null;
      nav.addEventListener('scroll', function(){
        if (scrollSaveTimer) clearTimeout(scrollSaveTimer);
        scrollSaveTimer = setTimeout(function(){
          try { sessionStorage.setItem(SS_SCROLL, String(nav.scrollTop)); } catch(_){}
        }, 80);
      }, { passive: true });

      // حفظ فوري عند الضغط على رابط (قبل مغادرة الصفحة)
      nav.addEventListener('click', function(e){
        if (!e.target.closest('a')) return;
        try { sessionStorage.setItem(SS_SCROLL, String(nav.scrollTop)); } catch(_){}
      });
    }

    // 9) Window resize — leave mobile-open state if user grows the window.
    var lastIsMobile = isMobile();
    window.addEventListener('resize', function(){
      var nowMobile = isMobile();
      if (nowMobile === lastIsMobile) return;
      lastIsMobile = nowMobile;
      if (nowMobile){
        // entering mobile: ensure drawer is closed
        setSideMode('opened');
      } else {
        // entering desktop: restore collapsed preference
        setSideMode(lsGet(LS_COLLAPSED) === '1' ? 'collapsed' : 'opened');
      }
    });
  }

  if (document.readyState === 'loading'){
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
