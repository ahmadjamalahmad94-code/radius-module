/* dashboard_table — ترقيم client-side خفيف يلتزم بمعيار HobeHub. */
(function () {
  "use strict";

  function init(table) {
    if (table.dataset.dtInit === "1") return;
    table.dataset.dtInit = "1";

    var key = table.dataset.persistKey || "";
    // المقاسات المعتمدة: 10/25/50/100/200/500 + «الكل» (عرض كل الصفوف).
    // القيمة "all" (أو "0") تعني بلا ترقيم — كل الصفوف في صفحة واحدة.
    var ALL = "all";
    var sizes = (table.dataset.pageSizes || "10,25,50,100,200,500,all")
      .split(",")
      .map(function (s) { return (s || "").trim().toLowerCase(); })
      .map(function (s) { return (s === ALL || s === "0") ? ALL : parseInt(s, 10); })
      .filter(function (s) { return s === ALL || (typeof s === "number" && s > 0); });
    var defaultSize = parseInt(table.dataset.pageSize || "20", 10) || 20;
    var stored = key && localStorage.getItem("dt:size:" + key);
    // القيمة المخزَّنة قد تكون "all" أو رقمًا؛ الأرقام القديمة غير المدرجة
    // (مثل 20 أو 1000 بعد التنقيح) تُطبَّع لأقرب مقاس مسموح.
    var size = stored ? (stored === ALL ? ALL : (parseInt(stored, 10) || defaultSize))
                      : defaultSize;
    var page = 1;

    // حجم الصفحة الفعليّ بالأرقام (all → كل الصفوف).
    function pageSizeNum() { return size === ALL ? Math.max(1, rows.length) : size; }
    function sizeLabel(s) { return s === ALL ? "الكل" : String(s); }

    var tbody = table.tBodies[0];
    if (!tbody) return;
    var rows = Array.prototype.filter.call(
      tbody.rows, function (r) { return !r.classList.contains("no-paginate"); }
    );

    var pager = document.createElement("div");
    pager.className = "dt-pager";
    pager.innerHTML =
      '<div class="dt-info"></div>' +
      '<div class="dt-controls">' +
        '<select class="dt-size"></select>' +
        '<button data-act="first" title="الأولى">⏪</button>' +
        '<button data-act="prev" title="السابقة">◀</button>' +
        '<span class="dt-pages"></span>' +
        '<button data-act="next" title="التالية">▶</button>' +
        '<button data-act="last" title="الأخيرة">⏩</button>' +
      '</div>';
    table.parentNode.insertBefore(pager, table.nextSibling);

    var sel = pager.querySelector(".dt-size");
    sizes.forEach(function (n) {
      var o = document.createElement("option");
      o.value = n; o.textContent = sizeLabel(n);
      if (n === size) o.selected = true;
      sel.appendChild(o);
    });

    function render() {
      var total = rows.length;
      var ps = pageSizeNum();
      var pages = Math.max(1, Math.ceil(total / ps));
      if (page > pages) page = pages;
      var start = (page - 1) * ps;
      var end = Math.min(start + ps, total);

      rows.forEach(function (r, i) { r.style.display = (i >= start && i < end) ? "" : "none"; });

      pager.querySelector(".dt-info").textContent =
        total === 0 ? "لا نتائج"
        : "عرض " + (start + 1) + " – " + end + " من " + total;

      var pagesHtml = "";
      var win = 5; var from = Math.max(1, page - 2); var to = Math.min(pages, from + win - 1);
      from = Math.max(1, to - win + 1);
      for (var p = from; p <= to; p++) {
        pagesHtml += '<button data-page="' + p + '"' +
          (p === page ? ' class="active"' : "") + '>' + p + '</button>';
      }
      pager.querySelector(".dt-pages").innerHTML = pagesHtml;

      pager.querySelector('[data-act="first"]').disabled = page <= 1;
      pager.querySelector('[data-act="prev"]').disabled = page <= 1;
      pager.querySelector('[data-act="next"]').disabled = page >= pages;
      pager.querySelector('[data-act="last"]').disabled = page >= pages;
    }

    pager.addEventListener("click", function (e) {
      var b = e.target.closest("button"); if (!b) return;
      var act = b.dataset.act;
      var pages = Math.max(1, Math.ceil(rows.length / pageSizeNum()));
      if (act === "first") page = 1;
      else if (act === "prev") page = Math.max(1, page - 1);
      else if (act === "next") page = Math.min(pages, page + 1);
      else if (act === "last") page = pages;
      else if (b.dataset.page) page = parseInt(b.dataset.page, 10);
      render();
    });

    sel.addEventListener("change", function () {
      size = (sel.value === ALL) ? ALL : (parseInt(sel.value, 10) || defaultSize);
      page = 1;
      if (key) try { localStorage.setItem("dt:size:" + key, String(size)); } catch (e) {}
      render();
    });

    // ── فرز اختياريّ: يُفعَّل فقط للأعمدة التي تُعلَّم رؤوسها data-sortable ──
    // (الجداول بلا هذه السمة لا تتأثّر إطلاقًا). النقر يبدّل تصاعدي/تنازلي؛
    // الأرقام (سرعة/بايت/#) تُقارَن رقميًّا، والباقي نصّيًّا (عربيّ). نرتّب مصفوفة
    // rows نفسها ثم نُعيد ترتيب DOM ليطابقها، فيَعرض الترقيم بالترتيب الصحيح.
    var thead = table.tHead;
    var headRow = thead && thead.rows[0];
    if (headRow) {
      var headCells = Array.prototype.slice.call(headRow.cells);
      var sortState = { col: -1, dir: 1 };
      headCells.forEach(function (th, ci) {
        if (!th.hasAttribute("data-sortable")) return;
        th.style.cursor = "pointer";
        if (!th.title) th.title = "اضغط للترتيب تصاعديًّا/تنازليًّا";
        var ind = document.createElement("span");
        ind.className = "dt-sort-ind";
        ind.style.cssText = "margin-inline-start:5px;opacity:.55;font-size:10px";
        th.appendChild(ind);
        th.addEventListener("click", function () {
          if (sortState.col === ci) sortState.dir = -sortState.dir;
          else { sortState.col = ci; sortState.dir = 1; }
          headCells.forEach(function (h) {
            var s = h.querySelector(".dt-sort-ind"); if (s) s.textContent = "";
          });
          ind.textContent = sortState.dir > 0 ? "▲" : "▼";
          var keyOf = function (r) {
            var cell = r.cells[ci];
            var raw = cell ? (cell.getAttribute("data-sort") ||
                              cell.textContent || "").trim() : "";
            var num = parseFloat(raw.replace(/[^0-9.\-]/g, ""));
            return { t: raw, n: (raw !== "" && !isNaN(num)) ? num : null };
          };
          rows.sort(function (a, b) {
            var ka = keyOf(a), kb = keyOf(b), cmp;
            if (ka.n !== null && kb.n !== null) cmp = ka.n - kb.n;
            else cmp = String(ka.t).localeCompare(String(kb.t), "ar");
            return cmp * sortState.dir;
          });
          var tb = table.tBodies[0];
          rows.forEach(function (r) { tb.appendChild(r); });
          page = 1;
          render();
        });
      });
    }

    render();
  }

  document.addEventListener("DOMContentLoaded", function () {
    document
      .querySelectorAll("table.d-table[data-paginated], table.hub-table[data-paginated], table.hr-data-table[data-paginated]")
      .forEach(init);
  });
})();
