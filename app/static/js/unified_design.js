/* ════════════════════════════════════════════════════════════════════
   UNIFIED DESIGN SYSTEM — shared behaviour for modals + dropdown menus.
   Companion to css/unified_design.css. Loaded once, globally, from the
   admin layout. No framework; idempotent; RTL-aware.

   MODAL
     • open:  any element with  data-uds-modal-open="<modalId>"
     • close: [data-uds-modal-close] inside, click on the overlay, or ESC
     • type-to-confirm: an input [data-uds-confirm-input]
       data-uds-confirm-phrase="…" enables the [data-uds-confirm-btn]
       (which starts disabled) only when the typed text matches.

   DROPDOWN MENU
     • trigger: [data-uds-menu-trigger] data-uds-menu-target="<menuId>"
       toggles the .uds-menu (position:fixed, z-index above content).
     • closes on click-outside, ESC, scroll, resize. RTL-aware placement.
   ════════════════════════════════════════════════════════════════════ */
(function () {
  if (window.__udsInit) return;
  window.__udsInit = true;

  /* ── Modals ─────────────────────────────────────────────────────── */
  function openModal(id) {
    var m = document.getElementById(id);
    if (!m) return;
    m.hidden = false;
    var f = m.querySelector("input, textarea, select, button[data-uds-confirm-btn]");
    if (f && f.focus) { try { f.focus(); } catch (e) {} }
  }
  function closeModal(m) { if (m) m.hidden = true; }
  function closeAllModals() {
    document.querySelectorAll(".uds-modal:not([hidden])").forEach(closeModal);
  }

  document.addEventListener("click", function (e) {
    var opener = e.target.closest("[data-uds-modal-open]");
    if (opener) {
      e.preventDefault();
      openModal(opener.getAttribute("data-uds-modal-open"));
      return;
    }
    var closer = e.target.closest("[data-uds-modal-close]");
    if (closer) {
      e.preventDefault();
      closeModal(closer.closest(".uds-modal"));
      return;
    }
    // click on the overlay itself (not the dialog) closes it
    if (e.target.classList && e.target.classList.contains("uds-modal")) {
      closeModal(e.target);
    }
  });

  // type-to-confirm gating
  document.addEventListener("input", function (e) {
    var inp = e.target.closest("[data-uds-confirm-input]");
    if (!inp) return;
    var modal = inp.closest(".uds-modal");
    if (!modal) return;
    var btn = modal.querySelector("[data-uds-confirm-btn]");
    if (!btn) return;
    var phrase = (inp.getAttribute("data-uds-confirm-phrase") || "").trim();
    btn.disabled = inp.value.trim() !== phrase;
  });

  /* ── Dropdown menus ─────────────────────────────────────────────── */
  function closeAllMenus() {
    document.querySelectorAll(".uds-menu:not([hidden])").forEach(function (m) { m.hidden = true; });
    document.querySelectorAll("[data-uds-menu-trigger][aria-expanded='true']")
      .forEach(function (t) { t.setAttribute("aria-expanded", "false"); });
  }
  function placeMenu(menu, trigger) {
    // Fixed positioning so no ancestor overflow can clip it. RTL: align the
    // menu's inline-end (right) edge with the trigger's right edge.
    var r = trigger.getBoundingClientRect();
    menu.hidden = false;
    menu.style.visibility = "hidden";
    var mw = menu.offsetWidth, mh = menu.offsetHeight;
    var left = r.right - mw;
    if (left < 8) left = 8;
    var maxLeft = window.innerWidth - mw - 8;
    if (left > maxLeft) left = Math.max(8, maxLeft);
    var top = r.bottom + 6;
    if (top + mh > window.innerHeight - 8) {       // flip up if no room below
      top = Math.max(8, r.top - mh - 6);
    }
    menu.style.left = left + "px";
    menu.style.top = top + "px";
    menu.style.visibility = "";
  }

  document.addEventListener("click", function (e) {
    var trigger = e.target.closest("[data-uds-menu-trigger]");
    if (trigger) {
      e.preventDefault();
      e.stopPropagation();
      var id = trigger.getAttribute("data-uds-menu-target");
      var menu = id ? document.getElementById(id) : trigger.nextElementSibling;
      if (!menu || !menu.classList.contains("uds-menu")) return;
      var isOpen = !menu.hidden;
      closeAllMenus();
      if (isOpen) return;
      placeMenu(menu, trigger);
      trigger.setAttribute("aria-expanded", "true");
      return;
    }
    // click inside an open menu: let the item act, then close — unless the
    // menu opts to stay open (e.g. a multi-select column picker).
    var insideMenu = e.target.closest(".uds-menu");
    if (insideMenu) {
      if (!insideMenu.hasAttribute("data-uds-keepopen")) setTimeout(closeAllMenus, 0);
      return;
    }
    closeAllMenus();   // click outside
  });

  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape") { closeAllMenus(); closeAllModals(); }
  });
  window.addEventListener("resize", closeAllMenus, true);
  window.addEventListener("scroll", closeAllMenus, true);

  /* ── Side-strip search filter (e.g. template picker) ─────────────── */
  document.addEventListener("input", function (e) {
    var box = e.target.closest("[data-pt-strip-search]");
    if (!box) return;
    var strip = box.closest(".pt-strip") || document;
    var q = box.value.trim().toLowerCase();
    strip.querySelectorAll(".pt-strip-item").forEach(function (it) {
      var name = (it.getAttribute("data-pt-search") || "").toLowerCase();
      it.style.display = (!q || name.indexOf(q) !== -1) ? "" : "none";
    });
  });
})();
