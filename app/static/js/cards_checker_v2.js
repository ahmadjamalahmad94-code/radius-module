/* ╔══════════════════════════════════════════════════════════════════╗
   ║  Card Checker v2 — AJAX + live refresh (R13.A.3)                     ║
   ║                                                                       ║
   ║  Progressive enhancement on top of the server-rendered v2 page:     ║
   ║                                                                       ║
   ║   1. Intercept the search form → fetch the same /v2 URL with the    ║
   ║      new query → extract #cc-result → swap into the page with a    ║
   ║      fade transition. URL bar updates via history.pushState so the ║
   ║      back button still works and the URL is shareable.             ║
   ║                                                                       ║
   ║   2. When the current result has an active session, poll the JSON  ║
   ║      API every 10 s and refresh the live banner + the "online"     ║
   ║      stat without re-rendering the whole panel. If the session     ║
   ║      ends, swap to the off-banner and stop polling.                ║
   ║                                                                       ║
   ║   3. Skeleton overlay while a fetch is in flight — keeps the       ║
   ║      previous content visible underneath, dimmed, so the admin     ║
   ║      doesn't lose context.                                          ║
   ║                                                                       ║
   ║  No framework, no build step. Plain ES2017+; Edge 18+ / FF 60+.    ║
   ╚══════════════════════════════════════════════════════════════════╝ */

(function () {
  'use strict';

  const PAGE_URL = '/admin/radius/cards/checker/v2';
  const API_URL  = '/admin/radius/cards/checker/api/lookup';
  const LIVE_POLL_MS = 10000;          // 10 s — same as MT acct interim
  const FADE_IN_MS   = 220;             // matches --cc-anim-med in CSS

  const $ = (sel, root) => (root || document).querySelector(sel);

  // ─────────── state ───────────
  let livePollTimer = null;            // setTimeout handle for live refresh
  let inflightFetch = null;            // AbortController of pending request

  // ─────────── boot ───────────
  document.addEventListener('DOMContentLoaded', init);

  function init() {
    const form = $('#cc-search-form');
    if (!form) return;          // not on the checker page

    // Intercept the search form
    form.addEventListener('submit', onSearchSubmit);

    // Wire browser back/forward
    window.addEventListener('popstate', onPopState);

    // If we landed already showing a result, start live polling
    maybeStartLivePoll();
  }

  // ─────────── search submit ───────────
  async function onSearchSubmit(e) {
    e.preventDefault();
    const input = $('#cc-search-input');
    const q = (input?.value || '').trim();
    if (!q) return;
    await loadResultFor(q, { pushHistory: true });
  }

  function onPopState() {
    const params = new URLSearchParams(window.location.search);
    const q = params.get('query') || params.get('q') || '';
    const input = $('#cc-search-input');
    if (input) input.value = q;
    loadResultFor(q, { pushHistory: false });
  }

  // ─────────── core: fetch v2 page, swap #cc-result ───────────
  async function loadResultFor(query, { pushHistory }) {
    cancelLivePoll();
    if (inflightFetch) inflightFetch.abort();

    const resultEl = $('#cc-result');
    if (!resultEl) return;
    resultEl.classList.add('cc-loading');

    const url = PAGE_URL + '?query=' + encodeURIComponent(query);
    if (pushHistory) {
      const adminUrl = '/admin/radius/cards/checker?query=' + encodeURIComponent(query);
      window.history.pushState({ q: query }, '', adminUrl.replace(
        '/admin/radius/cards/checker',
        PAGE_URL,
      ));
    }

    try {
      const ctrl = new AbortController();
      inflightFetch = ctrl;
      const resp = await fetch(url, {
        signal: ctrl.signal,
        headers: { 'X-Requested-With': 'cards-checker-v2' },
        credentials: 'same-origin',
      });
      if (!resp.ok) throw new Error('http ' + resp.status);
      const html = await resp.text();

      // parse the response and lift its #cc-result
      const doc = new DOMParser().parseFromString(html, 'text/html');
      const fresh = doc.getElementById('cc-result');
      if (!fresh) throw new Error('no #cc-result in response');

      // swap with fade
      resultEl.innerHTML = fresh.innerHTML;
      resultEl.classList.remove('cc-loading');
      animateFadeIn(resultEl);

      // restart live polling if applicable
      maybeStartLivePoll();
    } catch (err) {
      if (err.name === 'AbortError') return;   // cancelled — silent
      resultEl.classList.remove('cc-loading');
      resultEl.innerHTML = `
        <div class="cc-state cc-state-error">
          <div class="cc-state-icon"><i class="fa-solid fa-triangle-exclamation"></i></div>
          <h2>تعذّر جلب البيانات</h2>
          <div class="cc-state-hint">${escapeHtml(err.message || 'خطأ غير معروف')}</div>
        </div>`;
    } finally {
      inflightFetch = null;
    }
  }

  // ─────────── fade-in animation ───────────
  function animateFadeIn(el) {
    el.style.transition = 'none';
    el.style.opacity = '0';
    el.style.transform = 'translateY(4px)';
    requestAnimationFrame(() => {
      el.style.transition =
        `opacity ${FADE_IN_MS}ms ease, transform ${FADE_IN_MS}ms ease`;
      el.style.opacity = '1';
      el.style.transform = 'translateY(0)';
    });
  }

  // ─────────── live refresh (active session only) ───────────
  function maybeStartLivePoll() {
    cancelLivePoll();
    const banner = document.querySelector(
      '.cc-live-banner:not(.cc-live-off):not(.cc-live-error)'
    );
    if (!banner) return;  // not an active-session view
    const username = readCurrentUsername();
    if (!username) return;
    livePollTimer = setTimeout(() => liveTick(username), LIVE_POLL_MS);
  }

  function cancelLivePoll() {
    if (livePollTimer) {
      clearTimeout(livePollTimer);
      livePollTimer = null;
    }
  }

  async function liveTick(username) {
    try {
      const resp = await fetch(
        API_URL + '?q=' + encodeURIComponent(username),
        { credentials: 'same-origin' },
      );
      if (!resp.ok) throw new Error('http ' + resp.status);
      const json = await resp.json();
      if (!json.ok || !json.result || !json.result.exists) return;
      applyLiveUpdate(json.result);
    } catch (_e) {
      // fall through — try again next tick
    } finally {
      livePollTimer = setTimeout(() => liveTick(username), LIVE_POLL_MS);
    }
  }

  // ─────────── partial DOM updates from live tick ───────────
  function applyLiveUpdate(card) {
    // active session → keep banner live; session ended → swap to off
    const banner = document.querySelector('.cc-live-banner');
    if (!banner) return;

    if (card.active_session) {
      banner.classList.remove('cc-live-off', 'cc-live-error');
      // update IP/MAC/last seen if shown — careful, may be hidden by ACL in C
      setIfPresent('[data-cc-field="mac_address"]', card.mac_address || '—', 'mono');
      setIfPresent('[data-cc-field="ip_address"]', card.ip_address || '—', 'mono');
    } else {
      // ended since last tick — swap visuals, stop polling
      banner.classList.add('cc-live-off');
      banner.querySelector('div').textContent =
        'انتهت الجلسة — آخر ظهور: ' + (card.last_seen_at || '—');
      cancelLivePoll();
    }

    // update online count stat without re-rendering the whole grid
    setIfPresent('[data-cc-field="stats.online_sessions"] .cc-stat-value',
      String((card.accounting_summary || {}).online_sessions || 0));
  }

  // ─────────── helpers ───────────
  function readCurrentUsername() {
    const el = document.querySelector('[data-cc-field="username"]');
    return el ? el.textContent.trim() : '';
  }
  function setIfPresent(selector, text, monoClass) {
    const el = document.querySelector(selector);
    if (!el) return;       // hidden by ACL or not in current section — fine
    el.textContent = text;
    if (monoClass) el.classList.add(monoClass);
  }
  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, c => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;',
      '"': '&quot;', "'": '&#39;'
    }[c]));
  }
})();
