/* ════════════════════════════════════════════════════════════════════
   Reusable script/code card behaviour — companion to
   _partials/code_card.html + the .hcode-* CSS in unified_design.css.

   Loaded once globally; pure delegation so it works for any number of
   cards on a page and for cards injected after load. No framework.

   Data-attribute contract:
     • Master copy:    [data-cc-copy="<id>"]      → copies #<id>-full text
     • Live copy:      [data-cc-copy-live="<elId>"]→ copies #<elId> text
                       (for dynamic / JS-filled previews)
     • Per-section copy:[data-cc-seccopy="<id>"][data-cc-sec="<n>"]
                       → copies #<id>-sec-<n> text
     • Jump+highlight: [data-cc-jump="<id>"]
                       [data-cc-jump-start="N"][data-cc-jump-end="M"]
                       → scrolls [data-cc-code="<id>"] to line N, flashes N..M
   Copies always surface a design-system toast (window.UDS.toast) — never
   a native alert. RTL-page / LTR-code agnostic.
   ════════════════════════════════════════════════════════════════════ */
(function () {
  "use strict";
  if (window.__hcodeInit) return;
  window.__hcodeInit = true;

  function toast(msg, kind) {
    try { if (window.UDS && window.UDS.toast) window.UDS.toast(msg, kind || "ok"); }
    catch (e) { /* a copy toast must never break the page */ }
  }
  function copyText(text, ok) {
    var done = function () { if (ok) ok(); };
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(done, done);
    } else {
      var ta = document.createElement("textarea");
      ta.value = text; document.body.appendChild(ta); ta.select();
      try { document.execCommand("copy"); } catch (e) {}
      document.body.removeChild(ta); done();
    }
  }
  function flashBtn(btn) {
    if (!btn) return;
    var old = btn.innerHTML;
    btn.classList.add("is-ok");
    btn.innerHTML = '<i class="fa-solid fa-check"></i> ' + (btn.getAttribute("data-cc-ok") || "تم النسخ");
    setTimeout(function () { btn.innerHTML = old; btn.classList.remove("is-ok"); }, 1700);
  }
  function flashIcon(btn) {
    if (!btn) return;
    var i = btn.querySelector("i"); if (!i) return;
    var old = i.className; i.className = "fa-solid fa-check";
    setTimeout(function () { i.className = old; }, 1400);
  }
  var MSG = (window.__hcodeMsg = window.__hcodeMsg || {
    full: "نُسخ إلى الحافظة", section: "نُسخ القسم إلى الحافظة",
  });

  document.addEventListener("click", function (e) {
    // ── master copy (static card: #<id>-full holds the verbatim text) ──
    var copyBtn = e.target.closest("[data-cc-copy]");
    if (copyBtn) {
      var node = document.getElementById(copyBtn.getAttribute("data-cc-copy") + "-full");
      if (node) copyText(node.textContent, function () { flashBtn(copyBtn); toast(MSG.full, "ok"); });
      return;
    }
    // ── live copy (dynamic preview: copy the <pre>'s current text) ──
    var liveBtn = e.target.closest("[data-cc-copy-live]");
    if (liveBtn) {
      var live = document.getElementById(liveBtn.getAttribute("data-cc-copy-live"));
      if (live) copyText(live.textContent, function () { flashBtn(liveBtn); toast(MSG.full, "ok"); });
      return;
    }
    // ── per-section copy ──
    var secBtn = e.target.closest("[data-cc-seccopy]");
    if (secBtn) {
      e.stopPropagation();
      var sn = document.getElementById(
        secBtn.getAttribute("data-cc-seccopy") + "-sec-" + secBtn.getAttribute("data-cc-sec"));
      if (sn) copyText(sn.textContent, function () { flashIcon(secBtn); toast(MSG.section, "ok"); });
      return;
    }
    // ── jump to a section + flash its lines ──
    var jumpEl = e.target.closest("[data-cc-jump]");
    if (jumpEl) doJump(jumpEl);
  });

  // keyboard activation for chips (role=button)
  document.addEventListener("keydown", function (e) {
    if (e.key !== "Enter" && e.key !== " ") return;
    var jumpEl = e.target.closest("[data-cc-jump]");
    if (jumpEl) { e.preventDefault(); doJump(jumpEl); }
  });

  function doJump(jumpEl) {
    var id = jumpEl.getAttribute("data-cc-jump");
    var start = parseInt(jumpEl.getAttribute("data-cc-jump-start"), 10) || 1;
    var end = parseInt(jumpEl.getAttribute("data-cc-jump-end"), 10) || start;
    var code = document.querySelector('[data-cc-code="' + id + '"]');
    if (!code) return;
    var lns = code.querySelectorAll(".hcode-ln");
    if (!lns.length) return;
    var target = lns[Math.max(0, start - 1)];
    if (target) code.scrollTo({ top: target.offsetTop - code.offsetTop - 6, behavior: "smooth" });
    for (var i = start - 1; i < Math.min(end, lns.length); i++) {
      (function (el) {
        el.classList.add("is-hot");
        setTimeout(function () { el.classList.remove("is-hot"); }, 1500);
      })(lns[i]);
    }
  }
})();
