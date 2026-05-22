/* L4 — Setup-wizard frontend.
 *
 * Currently does one thing: the copy-button on the script page.
 * Kept tiny on purpose; the wizard's actual flow is server-side
 * (form POST → DB + redirect → script render). No fake feedback —
 * the button label flips to ✓ only when navigator.clipboard
 * actually resolves.
 */
(function () {
  "use strict";

  const root = document.querySelector("[data-mt-setup-script]");
  if (!root) return;

  const button = root.querySelector("[data-mt-setup-copy]");
  const block = root.querySelector("[data-mt-setup-script-text]");
  if (!button || !block) return;

  // Two paths: modern Clipboard API (HTTPS / localhost) and a
  // fallback using a hidden <textarea> + execCommand. The fallback
  // is what keeps the wizard usable on the operator's plain-HTTP
  // VPS (where the Clipboard API is gated by secure-context).
  async function copyText(text) {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
      return;
    }
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.style.position = "fixed";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.select();
    try {
      const ok = document.execCommand("copy");
      if (!ok) throw new Error("execCommand returned false");
    } finally {
      document.body.removeChild(ta);
    }
  }

  button.addEventListener("click", async () => {
    const text = block.textContent || "";
    try {
      await copyText(text);
      const original = button.innerHTML;
      button.classList.add("is-copied");
      button.innerHTML = '<i class="fa-solid fa-check"></i> تم النسخ';
      setTimeout(() => {
        button.classList.remove("is-copied");
        button.innerHTML = original;
      }, 1800);
    } catch (e) {
      // Honest failure path: tell the operator they need to copy
      // manually, don't silently pretend it worked.
      button.classList.remove("is-copied");
      button.innerHTML =
        '<i class="fa-solid fa-triangle-exclamation"></i> اضغط Ctrl+C يدويًّا';
      block.focus();
      // The pre is not natively selectable on every browser; mark
      // it so the operator can drag-select the whole block.
      const sel = window.getSelection();
      if (sel) {
        const range = document.createRange();
        range.selectNodeContents(block);
        sel.removeAllRanges();
        sel.addRange(range);
      }
    }
  });
})();
