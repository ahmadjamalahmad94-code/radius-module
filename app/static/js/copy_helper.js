/**
 * copy_helper.js — clipboard fallback for HTTP deployments.
 *
 * The modern Clipboard API (navigator.clipboard.writeText) only works
 * on HTTPS or localhost. Our production deployment is on plain HTTP
 * (http://187.77.70.18) so `navigator.clipboard` is undefined and
 * every copy button silently fails.
 *
 * This helper tries the modern API first, then falls back to the
 * legacy `document.execCommand('copy')` flow with a hidden textarea —
 * which works on HTTP since it's been around since IE.
 *
 * Usage:
 *   await HobeCopy.copy("text to copy");        // returns boolean
 *   HobeCopy.wireButton(btnEl, "text", onDone); // attaches click handler
 *   HobeCopy.wireAll(rootEl);                   // wires every
 *                                                  // [data-rh-inv-copy]
 *                                                  // button under root
 *
 * Buttons opt in by carrying a `data-rh-inv-copy="..."` attribute with
 * the text to copy. On success the button briefly flashes a green
 * checkmark; on failure a red X.
 */
(function () {
  "use strict";

  /** Try the modern API. Returns true on success, false otherwise. */
  async function tryModern(text) {
    if (!navigator.clipboard || !navigator.clipboard.writeText) return false;
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch (_err) {
      return false;
    }
  }

  /** Legacy fallback — works on HTTP. */
  function tryLegacy(text) {
    try {
      const ta = document.createElement("textarea");
      // Off-screen but still focus-able (display:none blocks select).
      ta.value = text;
      ta.style.position = "fixed";
      ta.style.left = "-9999px";
      ta.style.top = "0";
      ta.setAttribute("readonly", "");
      ta.setAttribute("aria-hidden", "true");
      document.body.appendChild(ta);
      // iOS Safari needs both .focus() and .setSelectionRange(...) to
      // pick up the selection — desktop browsers tolerate .select().
      ta.focus();
      ta.select();
      ta.setSelectionRange(0, ta.value.length);
      const ok = document.execCommand("copy");
      document.body.removeChild(ta);
      return !!ok;
    } catch (_err) {
      return false;
    }
  }

  async function copy(text) {
    const s = String(text == null ? "" : text);
    if (!s) return false;
    if (await tryModern(s)) return true;
    return tryLegacy(s);
  }

  /**
   * Attach a click handler to a single button. `text` may be a string
   * or a `() => string` lazy getter (useful when the value is computed
   * at click time). `onDone(ok)` is optional — called after the
   * visual feedback animation completes.
   */
  function wireButton(btn, text, onDone) {
    if (!btn || btn.dataset.hobeCopyWired === "1") return;
    btn.dataset.hobeCopyWired = "1";
    btn.addEventListener("click", async (ev) => {
      ev.preventDefault();
      ev.stopPropagation();
      const val = (typeof text === "function") ? text() : text;
      const orig = btn.innerHTML;
      const ok = await copy(val);
      btn.innerHTML = ok
        ? '<i class="fa-solid fa-check"></i>'
        : '<i class="fa-solid fa-xmark"></i>';
      btn.classList.toggle("is-ok", ok);
      btn.classList.toggle("is-fail", !ok);
      setTimeout(() => {
        btn.innerHTML = orig;
        btn.classList.remove("is-ok", "is-fail");
        if (typeof onDone === "function") onDone(ok);
      }, 1200);
    });
  }

  /** Wire every [data-rh-inv-copy] button under `root` (default: doc). */
  function wireAll(root) {
    const scope = root || document;
    scope.querySelectorAll("[data-rh-inv-copy]").forEach((btn) => {
      wireButton(btn, () => btn.dataset.rhInvCopy || "");
    });
  }

  window.HobeCopy = { copy, wireButton, wireAll };
})();
