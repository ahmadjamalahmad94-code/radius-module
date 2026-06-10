/* ══════════════════════════════════════════════════════════════════════
   STYLE UNIFICATION — global behaviour bridge.

   Loaded site-wide AFTER unified_design.js. Three jobs:

     1. Replace native alert() with a non-blocking, design-system toast
        widget — `window.UDS.toast(msg, kind)`. Kind ∈ {info, ok, err}.
        Pages already using their own toast (rcp-toast, cc-toast, …) are
        untouched; this is for the default `alert()` callsite that hasn't
        been migrated yet.

     2. Replace inline `onsubmit="return confirm('…')"` and
        `onclick="return confirm('…')"` patterns at DOM-ready with the
        unified confirm modal (data-confirm) baked into the layout via
        `_confirm_modal.html`. The original handler's flow is preserved
        exactly — the confirm modal re-clicks the element after the user
        approves, so formaction/formmethod/submit semantics are intact.

     3. Expose `window.UDS.confirm({ title, message, danger }).then(ok)`
        for new code that wants the async pattern without the legacy
        `data-confirm` shortcut.

   The script is idempotent (re-loading a partial template doesn't double
   bind) and silent — never throws into a page.
   ══════════════════════════════════════════════════════════════════════ */
(function () {
  "use strict";
  if (window.__udsStyleUnifyInit) return;
  window.__udsStyleUnifyInit = true;

  var UDS = window.UDS = window.UDS || {};

  /* ── Toast ─────────────────────────────────────────────────────────── */
  function ensureToast() {
    var el = document.getElementById("udsToast");
    if (el) return el;
    el = document.createElement("div");
    el.id = "udsToast";
    el.className = "uds-toast";
    el.setAttribute("role", "status");
    el.setAttribute("aria-live", "polite");
    el.innerHTML = '<i class="fa-solid fa-circle-info"></i><span class="uds-toast-msg"></span>';
    (document.body || document.documentElement).appendChild(el);
    return el;
  }
  var _toastTimer = null;
  UDS.toast = function (msg, kind) {
    if (!msg) return;
    var el = ensureToast();
    var span = el.querySelector(".uds-toast-msg");
    var icon = el.querySelector("i");
    span.textContent = String(msg);
    el.classList.remove("is-ok", "is-err");
    if (kind === "ok") {
      el.classList.add("is-ok");
      if (icon) icon.className = "fa-solid fa-circle-check";
    } else if (kind === "err" || kind === "error") {
      el.classList.add("is-err");
      if (icon) icon.className = "fa-solid fa-circle-exclamation";
    } else {
      if (icon) icon.className = "fa-solid fa-circle-info";
    }
    el.classList.add("is-on");
    if (_toastTimer) clearTimeout(_toastTimer);
    _toastTimer = setTimeout(function () { el.classList.remove("is-on"); }, 4200);
  };

  /* Replace native alert with a toast — but keep alert reachable via
     `window.__nativeAlert` for the rare case a page genuinely needs the
     blocking browser dialog (debugging). Pages with their own toast keep
     calling it directly; this only kicks in on `alert(…)`. */
  if (!window.__nativeAlert) window.__nativeAlert = window.alert.bind(window);
  window.alert = function (msg) {
    try { UDS.toast(msg, "info"); } catch (e) { window.__nativeAlert(msg); }
  };

  /* ── Confirm: async wrapper around the layout-baked modal ────────── */
  // We use the same #cfmModal element that `_confirm_modal.html` ships in
  // the layout (data-confirm intercept). Here we expose an async API for
  // explicit JS calls without rebuilding the markup.
  UDS.confirm = function (opts) {
    var message = (opts && opts.message) || (opts && opts.msg) || "هل تريد المتابعة؟";
    return new Promise(function (resolve) {
      var ov = document.getElementById("cfmModal");
      if (!ov) {
        // No modal baked in (e.g. partial template testing) — fall back to
        // a non-blocking toast and resolve(true) so the flow proceeds.
        UDS.toast(message, "info");
        resolve(true);
        return;
      }
      var msgEl = document.getElementById("cfmMsg");
      var okBtn = document.getElementById("cfmOk");
      var caBtn = document.getElementById("cfmCancel");
      if (msgEl) msgEl.textContent = message;
      ov.hidden = false;
      function done(val) {
        ov.hidden = true;
        okBtn.removeEventListener("click", onOk, true);
        caBtn.removeEventListener("click", onCa, true);
        ov.removeEventListener("click", onOv, true);
        document.removeEventListener("keydown", onKey, true);
        resolve(val);
      }
      function onOk(e) { e.preventDefault(); e.stopImmediatePropagation(); done(true); }
      function onCa(e) { e.preventDefault(); e.stopImmediatePropagation(); done(false); }
      function onOv(e) { if (e.target === ov) done(false); }
      function onKey(e) { if (e.key === "Escape") done(false); }
      // capture phase so we win against the layout's own click handler
      okBtn.addEventListener("click", onOk, true);
      caBtn.addEventListener("click", onCa, true);
      ov.addEventListener("click", onOv, true);
      document.addEventListener("keydown", onKey, true);
    });
  };

  /* ── Inline-handler migration ────────────────────────────────────────
     Scan once at DOM-ready: any element with
       onsubmit="return confirm('…')"  (form)
       onclick="return confirm('…')"   (button/link)
     gets the inline handler removed and the message attached as
     `data-confirm`, which the _confirm_modal.html handler intercepts
     on the way to the original action. */
  function migrateInlineConfirms(root) {
    if (!root) return;
    var rx = /(?:return\s+)?(?:window\.)?confirm\(\s*(['"])([\s\S]*?)\1\s*\)\s*;?/;
    // forms with onsubmit confirm
    root.querySelectorAll('form[onsubmit*="confirm("]').forEach(function (f) {
      var h = f.getAttribute("onsubmit") || "";
      var m = h.match(rx);
      if (!m) return;
      var msg = m[2].replace(/\\'/g, "'").replace(/\\"/g, '"');
      // Apply data-confirm to the submit trigger so _confirm_modal's
      // capture-phase intercept picks it up before the submit fires.
      var btn = f.querySelector('button[type="submit"], input[type="submit"]');
      if (btn) {
        if (!btn.hasAttribute("data-confirm")) btn.setAttribute("data-confirm", msg);
        f.removeAttribute("onsubmit");
      } else {
        // Form without an explicit submit (uncommon) — attach data-confirm
        // to the form and let _confirm_modal intercept on its bubbled click.
        if (!f.hasAttribute("data-confirm")) f.setAttribute("data-confirm", msg);
        f.removeAttribute("onsubmit");
      }
    });
    // buttons/links with onclick="return confirm('…')"
    root.querySelectorAll('[onclick*="confirm("]').forEach(function (el) {
      var h = el.getAttribute("onclick") || "";
      // skip ccModal.confirm / UDS.confirm / window.UDS.confirm
      if (/(?:ccModal|UDS)\.confirm/.test(h)) return;
      var m = h.match(rx);
      if (!m) return;
      var msg = m[2].replace(/\\'/g, "'").replace(/\\"/g, '"');
      if (!el.hasAttribute("data-confirm")) el.setAttribute("data-confirm", msg);
      // Only strip the onclick if confirm() is the *only* thing it does
      // (i.e. `return confirm('…')` or `confirm('…')` with no other code).
      var stripped = h.replace(rx, "").replace(/^\s*return\s*;?\s*$/, "").trim();
      if (!stripped) el.removeAttribute("onclick");
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", function () { migrateInlineConfirms(document); });
  } else {
    migrateInlineConfirms(document);
  }

  // Re-run after any HTMX-style swap, table re-render, etc. Pages that
  // inject markup dynamically can call `UDS.rescan(scope)` afterwards.
  UDS.rescan = function (scope) { try { migrateInlineConfirms(scope || document); } catch (e) {} };
})();
