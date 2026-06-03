/* ════════════════════════════════════════════════════════════════════
   UNIFIED DATA TABLE — one component for every table in the app.
   Companion to the .uds-table* rules in unified_design.css.

   Opt in:  wrap a normal <table> and tag the wrapper:
       <div class="uds-table-wrap" data-uds-table data-uds-page-size="10">
         <table> <thead>…</thead> <tbody>…</tbody> </table>
       </div>

   It then provides, with zero per-page code:
     • top toolbar with an «أعمدة» button to show/hide columns
     • sortable, centred headers (click to toggle asc/desc)
     • a bottom pager: rows-per-page selector (10/25/50/100) + page nav
     • no horizontal scroll (columns fit or are hidden)

   Per-header opt-outs:  data-uds-nosort  (not sortable),
                         data-uds-nocol   (cannot be hidden).
   Blank/checkbox headers are auto-excluded from both.
   All existing row markup, links and actions are preserved.
   ════════════════════════════════════════════════════════════════════ */
(function () {
  if (window.__udsTableInit) return;
  window.__udsTableInit = true;

  var SIZES = [10, 25, 50, 100];

  function toNum(v) {
    var t = (v || "").replace(/[٫]/g, ".").replace(/[^\d.\-]/g, "");
    if (t === "" || t === "-" || t === ".") return null;
    var n = parseFloat(t);
    return isNaN(n) ? null : n;
  }

  function initTable(wrap) {
    if (wrap.__udsT) return;
    var table = wrap.querySelector("table");
    if (!table || !table.tHead || !table.tBodies[0]) return;
    wrap.__udsT = true;
    table.classList.add("uds-table");

    var thead = table.tHead, tbody = table.tBodies[0];
    var ths = [].slice.call(thead.rows[0].cells);
    var rows = [].slice.call(tbody.rows);
    var nCols = ths.length;

    var size = parseInt(wrap.getAttribute("data-uds-page-size") || "10", 10);
    var sizes = SIZES.slice();
    if (sizes.indexOf(size) < 0) { sizes.push(size); sizes.sort(function (a, b) { return a - b; }); }
    var state = { page: 1, size: size, sortCol: -1, sortDir: 1, hidden: {} };
    var uid = "udst-" + Math.floor(Math.random() * 1e9);

    /* ---- top toolbar : count + أعمدة (column picker) ---- */
    var toolbar = document.createElement("div");
    toolbar.className = "uds-table-toolbar";
    var meta = document.createElement("div");
    meta.className = "uds-table-meta";
    toolbar.appendChild(meta);

    var colBtn = document.createElement("button");
    colBtn.type = "button";
    colBtn.className = "hub-btn hub-btn--secondary hub-btn--sm";
    colBtn.setAttribute("data-uds-menu-trigger", "");
    colBtn.setAttribute("data-uds-menu-target", uid + "-cols");
    colBtn.setAttribute("aria-haspopup", "true");
    colBtn.innerHTML = '<i class="fa-solid fa-table-columns"></i> أعمدة';

    var colMenu = document.createElement("div");
    colMenu.className = "uds-menu uds-table-colsmenu";
    colMenu.id = uid + "-cols";
    colMenu.hidden = true;
    colMenu.setAttribute("data-uds-keepopen", "");

    ths.forEach(function (th, i) {
      var label = (th.textContent || "").trim();
      if (!label || th.hasAttribute("data-uds-nocol")) return;
      var lab = document.createElement("label");
      lab.className = "uds-menu-item";
      var cb = document.createElement("input");
      cb.type = "checkbox"; cb.checked = true;
      cb.style.cssText = "width:15px;height:15px;accent-color:var(--hub-brand);margin:0";
      cb.addEventListener("change", function () { state.hidden[i] = !cb.checked; applyCols(); });
      var sp = document.createElement("span");
      sp.textContent = label;
      lab.appendChild(cb); lab.appendChild(sp);
      colMenu.appendChild(lab);
    });
    var colWrap = document.createElement("div");
    colWrap.className = "uds-table-cols";
    colWrap.appendChild(colBtn);
    toolbar.appendChild(colWrap);

    /* ---- sortable centred headers ---- */
    ths.forEach(function (th, i) {
      var label = (th.textContent || "").trim();
      if (!label || th.hasAttribute("data-uds-nosort")) return;
      th.classList.add("uds-sortable");
      var ind = document.createElement("span");
      ind.className = "uds-sort-ind";
      ind.innerHTML = "⇅";
      th.appendChild(ind);
      th.addEventListener("click", function () { sortBy(i); });
    });

    /* ---- bottom pager ---- */
    var pager = document.createElement("div");
    pager.className = "uds-table-pager";
    var sizeWrap = document.createElement("div");
    sizeWrap.className = "uds-pager-size";
    sizeWrap.appendChild(document.createTextNode("صفوف بالصفحة"));
    var sizeSel = document.createElement("select");
    sizes.forEach(function (s) {
      var o = document.createElement("option");
      o.value = s; o.textContent = s;
      if (s === state.size) o.selected = true;
      sizeSel.appendChild(o);
    });
    sizeSel.addEventListener("change", function () { state.size = parseInt(sizeSel.value, 10); state.page = 1; render(); });
    sizeWrap.appendChild(sizeSel);

    var info = document.createElement("div");
    info.className = "uds-pager-info";
    var nav = document.createElement("div");
    nav.className = "uds-pager-nav";

    pager.appendChild(sizeWrap);
    pager.appendChild(info);
    pager.appendChild(nav);

    /* place toolbar above the table, pager below */
    wrap.parentNode.insertBefore(toolbar, wrap);
    wrap.parentNode.insertBefore(pager, wrap.nextSibling);
    colWrap.appendChild(colMenu);

    function applyCols() {
      for (var i = 0; i < nCols; i++) {
        var hide = !!state.hidden[i];
        if (ths[i]) ths[i].style.display = hide ? "none" : "";
        rows.forEach(function (r) { if (r.cells[i]) r.cells[i].style.display = hide ? "none" : ""; });
      }
    }

    function sortBy(i) {
      if (state.sortCol === i) state.sortDir *= -1;
      else { state.sortCol = i; state.sortDir = 1; }
      ths.forEach(function (th, j) {
        var ind = th.querySelector(".uds-sort-ind");
        th.classList.toggle("uds-sorted", j === i && !!ind);
        if (ind) ind.innerHTML = (j === i) ? (state.sortDir > 0 ? "▲" : "▼") : "⇅";
      });
      rows.sort(function (a, b) {
        var x = a.cells[i] ? a.cells[i].textContent.trim() : "";
        var y = b.cells[i] ? b.cells[i].textContent.trim() : "";
        var nx = toNum(x), ny = toNum(y), r;
        if (nx !== null && ny !== null) r = nx - ny;
        else r = x.localeCompare(y, "ar");
        return r * state.sortDir;
      });
      rows.forEach(function (r) { tbody.appendChild(r); });
      state.page = 1;
      render();
    }

    function pbtn(label, pg, opts) {
      opts = opts || {};
      var b = document.createElement("button");
      b.type = "button";
      b.className = "uds-pager-btn" + (opts.active ? " is-active" : "");
      b.innerHTML = label;
      if (opts.disabled) b.disabled = true;
      else b.addEventListener("click", function () { state.page = pg; render(); });
      nav.appendChild(b);
    }
    function dots() {
      var d = document.createElement("span");
      d.className = "uds-pager-dots"; d.textContent = "…";
      nav.appendChild(d);
    }

    function render() {
      var total = rows.length;
      var pages = Math.max(1, Math.ceil(total / state.size));
      if (state.page > pages) state.page = pages;
      var start = (state.page - 1) * state.size;
      var end = Math.min(start + state.size, total);
      var vis = 0;
      rows.forEach(function (r, idx) {
        var show = idx >= start && idx < end;
        r.style.display = show ? "" : "none";
        if (show) { r.classList.toggle("uds-rowalt", vis % 2 === 1); vis++; }
      });
      meta.textContent = total + " صف";
      info.textContent = total ? (start + 1) + "–" + end + " من " + total : "0";

      nav.innerHTML = "";
      // RTL: «previous» points right (chevron-right), «next» points left.
      pbtn('<i class="fa-solid fa-chevron-right"></i>', state.page - 1, { disabled: state.page <= 1 });
      var s = Math.max(1, state.page - 2), e = Math.min(pages, state.page + 2);
      if (s > 1) { pbtn("1", 1, { active: state.page === 1 }); if (s > 2) dots(); }
      for (var p = s; p <= e; p++) pbtn(String(p), p, { active: p === state.page });
      if (e < pages) { if (e < pages - 1) dots(); pbtn(String(pages), pages, { active: state.page === pages }); }
      pbtn('<i class="fa-solid fa-chevron-left"></i>', state.page + 1, { disabled: state.page >= pages });
    }

    applyCols();
    render();
  }

  function initAll(root) {
    (root || document).querySelectorAll("[data-uds-table]").forEach(initTable);
  }
  if (document.readyState !== "loading") initAll();
  document.addEventListener("DOMContentLoaded", function () { initAll(); });
})();
