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
   ║    hobe_sidebar_collapsed       "0" | "1"                            ║
   ║    hobe_sidebar_open_sections   JSON array of section ids            ║
   ║                                                                      ║
   ║  State driver: <body data-hb-side="opened|collapsed|mobile-open">    ║
   ╚════════════════════════════════════════════════════════════════════╝ */

(function(){
  'use strict';

  var LS_COLLAPSED = 'hobe_sidebar_collapsed';
  var LS_OPEN_SECS = 'hobe_sidebar_open_sections';
  var MOBILE_BP    = 900;

  // ─── small storage helpers (swallow exceptions in private-mode browsers) ───
  function lsGet(k){ try { return localStorage.getItem(k); } catch(_){ return null; } }
  function lsSet(k,v){ try { localStorage.setItem(k,v); } catch(_){} }

  function readOpenSections(){
    var raw = lsGet(LS_OPEN_SECS);
    if (!raw) return [];
    try {
      var arr = JSON.parse(raw);
      return Array.isArray(arr) ? arr.filter(function(x){ return typeof x === 'string'; }) : [];
    } catch(_){ return []; }
  }
  function writeOpenSections(list){
    lsSet(LS_OPEN_SECS, JSON.stringify(list));
  }

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

    // 8) Window resize — leave mobile-open state if user grows the window.
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
