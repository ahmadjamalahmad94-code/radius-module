/* Card Checker v2 progressive enhancement.
   The page remains fully server-rendered when JavaScript is unavailable. */
(function () {
  'use strict';

  const PAGE_URL = '/admin/radius/cards/checker/v2';
  const API_URL = '/admin/radius/cards/checker/api/lookup';
  const LIVE_POLL_MS = 10000;
  const FADE_IN_MS = 220;

  const $ = (selector, root) => (root || document).querySelector(selector);

  let livePollTimer = null;
  let inflightFetch = null;

  document.addEventListener('DOMContentLoaded', init);

  function init() {
    const form = $('#cc-search-form');
    if (!form) return;

    form.addEventListener('submit', onSearchSubmit);
    window.addEventListener('popstate', onPopState);
    maybeStartLivePoll();
  }

  async function onSearchSubmit(event) {
    event.preventDefault();
    const input = $('#cc-search-input');
    const query = (input?.value || '').trim();
    if (!query) return;
    await loadResultFor(query, { pushHistory: true });
  }

  function onPopState() {
    const params = new URLSearchParams(window.location.search);
    const query = params.get('query') || params.get('q') || '';
    const input = $('#cc-search-input');
    if (input) input.value = query;
    loadResultFor(query, { pushHistory: false });
  }

  async function loadResultFor(query, { pushHistory }) {
    cancelLivePoll();
    if (inflightFetch) inflightFetch.abort();

    const resultEl = $('#cc-result');
    if (!resultEl) return;
    resultEl.classList.add('cc-loading');

    const url = PAGE_URL + '?query=' + encodeURIComponent(query);
    if (pushHistory) {
      window.history.pushState({ query }, '', url);
    }

    try {
      const controller = new AbortController();
      inflightFetch = controller;
      const response = await fetch(url, {
        signal: controller.signal,
        headers: { 'X-Requested-With': 'cards-checker-v2' },
        credentials: 'same-origin',
      });
      if (!response.ok) throw new Error('HTTP ' + response.status);

      const html = await response.text();
      const doc = new DOMParser().parseFromString(html, 'text/html');
      const fresh = doc.getElementById('cc-result');
      if (!fresh) throw new Error('Missing #cc-result in response');

      resultEl.innerHTML = fresh.innerHTML;
      resultEl.classList.remove('cc-loading');
      animateFadeIn(resultEl);
      maybeStartLivePoll();
    } catch (error) {
      if (error.name === 'AbortError') return;
      resultEl.classList.remove('cc-loading');
      resultEl.innerHTML = `
        <div class="cc-state cc-state-error">
          <div class="cc-state-icon"><i class="fa-solid fa-triangle-exclamation"></i></div>
          <h2>تعذر جلب بيانات البطاقة</h2>
          <div class="cc-state-hint">${escapeHtml(error.message || 'خطأ غير معروف')}</div>
        </div>`;
    } finally {
      inflightFetch = null;
    }
  }

  function animateFadeIn(element) {
    element.style.transition = 'none';
    element.style.opacity = '0';
    element.style.transform = 'translateY(4px)';
    requestAnimationFrame(() => {
      element.style.transition =
        `opacity ${FADE_IN_MS}ms ease, transform ${FADE_IN_MS}ms ease`;
      element.style.opacity = '1';
      element.style.transform = 'translateY(0)';
    });
  }

  function maybeStartLivePoll() {
    cancelLivePoll();
    const banner = document.querySelector(
      '.cc-live-banner:not(.cc-live-off):not(.cc-live-error)'
    );
    if (!banner) return;

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
      const response = await fetch(
        API_URL + '?q=' + encodeURIComponent(username),
        { credentials: 'same-origin' }
      );
      if (!response.ok) throw new Error('HTTP ' + response.status);

      const json = await response.json();
      if (!json.ok || !json.result || !json.result.exists) return;
      applyLiveUpdate(json.result);
    } catch (_error) {
      // Keep the current panel visible and retry on the next interval.
    } finally {
      livePollTimer = setTimeout(() => liveTick(username), LIVE_POLL_MS);
    }
  }

  function applyLiveUpdate(card) {
    const banner = document.querySelector('.cc-live-banner');
    if (!banner) return;

    if (card.active_session) {
      banner.classList.remove('cc-live-off', 'cc-live-error');
      setIfPresent('[data-cc-field="mac_address"]', card.mac_address || '-', 'mono');
      setIfPresent('[data-cc-field="ip_address"]', card.ip_address || '-', 'mono');
    } else {
      banner.classList.add('cc-live-off');
      const content = banner.querySelector('div');
      if (content) {
        content.textContent =
          'انتهت الجلسة. آخر ظهور: ' + (card.last_seen_at || '-');
      }
      cancelLivePoll();
    }

    setIfPresent(
      '[data-cc-field="stats.online_sessions"] .cc-stat-value',
      String((card.accounting_summary || {}).online_sessions || 0)
    );
  }

  function readCurrentUsername() {
    const el = document.querySelector('[data-cc-field="username"]');
    return el ? el.textContent.trim() : '';
  }

  function setIfPresent(selector, text, monoClass) {
    const el = document.querySelector(selector);
    if (!el) return;
    el.textContent = text;
    if (monoClass) el.classList.add(monoClass);
  }

  function escapeHtml(value) {
    return String(value).replace(/[&<>"']/g, (char) => ({
      '&': '&amp;',
      '<': '&lt;',
      '>': '&gt;',
      '"': '&quot;',
      "'": '&#39;',
    }[char]));
  }
})();
