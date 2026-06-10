/* ════════════════════════════════════════════════════════════════════
   SECTION SEARCH — one prominent search bar that live-filters every
   unified data-table (`[data-uds-table]`) inside its scope.

   Built on top of uds_table.js's filter API:
       wrap.__udsApi.setFilter(fn)   // fn(<tr>) -> boolean, respects paging

   Markup:
       <input data-fc-search
              data-fc-search-scope="#some-container"   (optional; defaults
                                                         to the closest
                                                         [data-fc-section])
              data-fc-search-count="#counter"          (optional element to
                                                         show "N نتيجة")>

   Matching is Arabic-friendly (alef/ya/ta-marbuta normalised, tatweel +
   diacritics stripped) and token-AND: every whitespace-separated token must
   appear somewhere in the row's text. Empty query clears the filter.

   Must load AFTER uds_table.js so `__udsApi` already exists.
   ════════════════════════════════════════════════════════════════════ */
(function () {
  if (window.__fcSearchInit) return;
  window.__fcSearchInit = true;

  function normalize(s) {
    return (s == null ? "" : String(s))
      .toLowerCase()
      .replace(/ـ/g, "")                 // tatweel ـ
      .replace(/[ً-ْ]/g, "")        // harakat / diacritics
      .replace(/[أإآٱ]/g, "ا") // أ إ آ ٱ → ا
      .replace(/ى/g, "ي")           // ى → ي
      .replace(/ة/g, "ه")           // ة → ه
      .replace(/[٫٬،]/g, " ")  // arabic decimal/thousands/comma
      .replace(/\s+/g, " ")
      .trim();
  }

  function rowText(tr) {
    // textContent of the whole row covers every meaningful column
    // (owner name, id, balance, status, source, dates …).
    return normalize(tr.textContent || "");
  }

  function wire(input) {
    if (input.__fcWired) return;
    input.__fcWired = true;

    var scopeSel = input.getAttribute("data-fc-search-scope");
    var scope = (scopeSel && document.querySelector(scopeSel)) ||
                input.closest("[data-fc-section]") || document;
    var countSel = input.getAttribute("data-fc-search-count");
    var countEl = countSel ? document.querySelector(countSel) : null;

    function predicate(tokens) {
      return function (tr) {
        if (!tokens.length) return true;
        var t = rowText(tr);
        for (var i = 0; i < tokens.length; i++) {
          if (t.indexOf(tokens[i]) === -1) return false;
        }
        return true;
      };
    }

    function apply() {
      var q = normalize(input.value);
      var tokens = q ? q.split(" ").filter(Boolean) : [];
      var pred = predicate(tokens);
      var wraps = scope.querySelectorAll("[data-uds-table]");
      var totalMatches = 0;
      var totalRows = 0;
      var groupHits = new Map();  // [data-fw-group] -> matched rows

      wraps.forEach(function (w) {
        var bodyRows = w.querySelectorAll("tbody tr");
        var matches = 0;
        bodyRows.forEach(function (tr) { if (pred(tr)) matches++; });
        totalMatches += matches;
        totalRows += bodyRows.length;

        if (w.__udsApi && typeof w.__udsApi.setFilter === "function") {
          w.__udsApi.setFilter(tokens.length ? pred : null);
        } else {
          // Fallback when uds hasn't initialised this table: hide rows directly.
          bodyRows.forEach(function (tr) {
            tr.style.display = (tokens.length && !pred(tr)) ? "none" : "";
          });
        }

        var group = w.closest("[data-fw-group]");
        if (group) groupHits.set(group, (groupHits.get(group) || 0) + matches);
      });

      // Group visibility: while searching, show only owner-type groups that
      // actually have a matching row — table-less empty groups and zero-match
      // groups collapse, so the user sees just the matching section(s).
      scope.querySelectorAll("[data-fw-group]").forEach(function (group) {
        if (!tokens.length) { group.style.display = ""; return; }
        group.style.display = (groupHits.get(group) || 0) > 0 ? "" : "none";
      });

      if (countEl) {
        if (!tokens.length) {
          countEl.textContent = "";
        } else {
          countEl.textContent = totalMatches + " نتيجة من " + totalRows;
        }
      }
      input.classList.toggle("fc-search--empty", !!tokens.length && totalMatches === 0);
    }

    var timer = null;
    input.addEventListener("input", function () {
      clearTimeout(timer);
      timer = setTimeout(apply, 110);
    });
    // Escape clears the search.
    input.addEventListener("keydown", function (e) {
      if (e.key === "Escape") { input.value = ""; apply(); }
    });

    // Run once in case the field was pre-filled (e.g. browser restore).
    apply();
  }

  function init() {
    document.querySelectorAll("[data-fc-search]").forEach(wire);
  }

  if (document.readyState !== "loading") init();
  else document.addEventListener("DOMContentLoaded", init);
})();
