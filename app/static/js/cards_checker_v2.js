/* Card Checker v2 progressive enhancement.
   The page remains fully server-rendered when JavaScript is unavailable. */
(function () {
  'use strict';

  const PAGE_URL = '/admin/radius/cards/checker';
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
    wireSectionToggles();
    wireModeToggle();
    maybeStartLivePoll();
  }

  // R13.A.7: advanced / simple mode switcher. Persists in localStorage
  // and applies the current choice on every page load (including after
  // AJAX swaps of #cc-result, which re-renders both panels).
  function wireModeToggle() {
    const MODE_KEY = 'cc-mode';
    const page = document.querySelector('.cc-page');
    if (!page) return;

    function apply(mode) {
      if (mode !== 'advanced' && mode !== 'simple') mode = 'advanced';
      page.dataset.ccCurrentMode = mode;
      document.querySelectorAll('[data-cc-mode-toggle], .cc-mode-toggle')
        .forEach((host) => {
          host.querySelectorAll('button[data-mode]').forEach((btn) => {
            const isActive = btn.dataset.mode === mode;
            btn.classList.toggle('active', isActive);
            btn.setAttribute('aria-selected', isActive ? 'true' : 'false');
          });
        });
      const label = document.querySelector('[data-cc-mode-text]');
      if (label) {
        label.textContent =
          mode === 'simple' ? 'المستوى البسيط' : 'المستوى المتقدّم';
      }
      try { localStorage.setItem(MODE_KEY, mode); } catch (_e) {}
    }

    // initial — honor saved preference, default to advanced
    let saved = 'advanced';
    try { saved = localStorage.getItem(MODE_KEY) || 'advanced'; } catch (_e) {}
    apply(saved);

    // event delegation so AJAX-swapped result panels still find handlers
    document.addEventListener('click', (e) => {
      const btn = e.target.closest('.cc-mode-toggle button[data-mode]');
      if (!btn) return;
      apply(btn.dataset.mode);
    });
  }

  // R13.A.5: collapsible sections. Click the chevron (or the header)
  // to collapse/expand the body. Event delegation so the binding survives
  // AJAX swaps of #cc-result.
  function wireSectionToggles() {
    document.addEventListener('click', (e) => {
      const toggle = e.target.closest('.cc-section-head-toggle');
      if (!toggle) return;
      const section = toggle.closest('.cc-section');
      if (!section) return;
      section.classList.toggle('cc-collapsed');
    });
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
      // The top KPI strip (الجلسات / أجهزة متّصلة / وقت البطاقة / الوقت
      // المتبقّي / إجمالي الاستهلاك) lives in the megahero OUTSIDE #cc-result,
      // so swapping the result panel alone leaves it stale — e.g. empty after
      // searching from the no-query page. Mirror it from the SAME full-page
      // response so the strip shows for EVERY looked-up card, connected or
      // disconnected, exactly like a full reload.
      syncHeroKpis(doc);
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

  // Mirror the megahero KPI strip from a fetched full-page document into the
  // live page. The strip sits in `.hub-hero-shell .uds-hero`, next to (not
  // inside) #cc-result, and holds only `.uds-hero-top` + an optional
  // `.uds-hero-kpis`. We surgically update ONLY `.uds-hero-kpis` so the
  // adjacent search bar (`.cc-hero`, with its JS-bound form/input/toggle) is
  // never touched. Handles all cases: update, insert-when-missing, and
  // remove-when-the-response-has-none (not-found / error).
  function syncHeroKpis(doc) {
    const heroSection = document.querySelector('.hub-hero-shell .uds-hero');
    if (!heroSection) return;
    const freshSection = doc.querySelector('.hub-hero-shell .uds-hero');
    const freshKpis = freshSection
      ? freshSection.querySelector('.uds-hero-kpis')
      : null;
    const liveKpis = heroSection.querySelector('.uds-hero-kpis');
    if (freshKpis) {
      if (liveKpis) {
        liveKpis.innerHTML = freshKpis.innerHTML;
      } else {
        // Insert right after the title/actions row so the strip lands in the
        // same position a server-rendered page would place it.
        const top = heroSection.querySelector('.uds-hero-top');
        heroSection.insertBefore(
          freshKpis.cloneNode(true),
          top ? top.nextSibling : heroSection.firstChild
        );
      }
    } else if (liveKpis) {
      liveKpis.remove();
    }
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
