/* page_guide_collapse.js
 * App-wide behaviour for the "كيف تقرأ …" help/explanation panels.
 *
 * These panels are authored per-page with different class names
 * (e.g. .audit-guide), so instead of patching every template we
 * detect them globally by their heading text ("كيف تقر…") and make
 * each one collapsible — COLLAPSED BY DEFAULT, expand on click.
 * State is remembered per panel title in localStorage.
 */
(function () {
  'use strict';

  // Match every help/info-panel heading opener, collapsed by default:
  //   "كيف …" (how to read/use/work), "ماذا تشاهد/ماذا …" (what you see),
  //   "ما وظيفة …" (what this page does), "شرح …", "دليل …".
  // These are explanation boxes; real page titles don't start this way.
  var PREFIXES = ['كيف ', 'ماذا ', 'ما وظيفة', 'شرح ', 'دليل '];
  function isGuideHeading(txt) {
    for (var i = 0; i < PREFIXES.length; i++) {
      if (txt.indexOf(PREFIXES[i]) === 0) return true;
    }
    return false;
  }

  function initGuides() {
    var headings = document.querySelectorAll('h1, h2, h3, h4, h5');
    Array.prototype.forEach.call(headings, function (h) {
      var txt = (h.textContent || '').trim();
      if (!isGuideHeading(txt)) return;
      if (h.getAttribute('data-guide-bound') === '1') return;
      h.setAttribute('data-guide-bound', '1');

      var panel = h.parentElement;
      if (!panel) return;

      // Everything after the heading (within the same panel) collapses;
      // anything before it (e.g. an icon) stays visible.
      var collapsibles = [];
      var sib = h.nextElementSibling;
      while (sib) { collapsibles.push(sib); sib = sib.nextElementSibling; }
      if (!collapsibles.length) return;

      var key = 'guide.collapsed.' + txt.replace(/\s+/g, '_').slice(0, 60);
      var stored = null;
      try { stored = localStorage.getItem(key); } catch (e) {}
      // Default = collapsed.
      var collapsed = (stored === null) ? true : (stored === '1');

      var chevron = document.createElement('span');
      chevron.className = 'guide-chevron';
      chevron.setAttribute('aria-hidden', 'true');
      chevron.style.marginInlineStart = '8px';
      chevron.style.fontSize = '0.8em';
      chevron.style.transition = 'transform .15s ease';
      h.appendChild(chevron);

      h.style.cursor = 'pointer';
      h.style.userSelect = 'none';
      h.setAttribute('role', 'button');
      h.setAttribute('tabindex', '0');

      function apply() {
        for (var i = 0; i < collapsibles.length; i++) {
          collapsibles[i].style.display = collapsed ? 'none' : '';
        }
        h.setAttribute('aria-expanded', collapsed ? 'false' : 'true');
        chevron.textContent = collapsed ? '▸' : '▾';
        if (collapsed) { panel.classList.add('guide-collapsed'); }
        else { panel.classList.remove('guide-collapsed'); }
      }

      function toggle() {
        collapsed = !collapsed;
        try { localStorage.setItem(key, collapsed ? '1' : '0'); } catch (e) {}
        apply();
      }

      h.addEventListener('click', toggle);
      h.addEventListener('keydown', function (e) {
        if (e.key === 'Enter' || e.key === ' ' || e.key === 'Spacebar') {
          e.preventDefault();
          toggle();
        }
      });

      apply();
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initGuides);
  } else {
    initGuides();
  }
})();
