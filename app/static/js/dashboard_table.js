/* dashboard_table — ترقيم client-side خفيف يلتزم بمعيار HobeHub. */
(function () {
  "use strict";

  function init(table) {
    if (table.dataset.dtInit === "1") return;
    table.dataset.dtInit = "1";

    var key = table.dataset.persistKey || "";
    var sizes = (table.dataset.pageSizes || "10,20,50,100")
      .split(",").map(function (s) { return parseInt(s, 10); }).filter(Boolean);
    var defaultSize = parseInt(table.dataset.pageSize || "20", 10);
    var stored = key && localStorage.getItem("dt:size:" + key);
    var size = stored ? parseInt(stored, 10) : defaultSize;
    var page = 1;

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
      o.value = n; o.textContent = n;
      if (n === size) o.selected = true;
      sel.appendChild(o);
    });

    function render() {
      var total = rows.length;
      var pages = Math.max(1, Math.ceil(total / size));
      if (page > pages) page = pages;
      var start = (page - 1) * size;
      var end = Math.min(start + size, total);

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
      var pages = Math.max(1, Math.ceil(rows.length / size));
      if (act === "first") page = 1;
      else if (act === "prev") page = Math.max(1, page - 1);
      else if (act === "next") page = Math.min(pages, page + 1);
      else if (act === "last") page = pages;
      else if (b.dataset.page) page = parseInt(b.dataset.page, 10);
      render();
    });

    sel.addEventListener("change", function () {
      size = parseInt(sel.value, 10) || defaultSize;
      page = 1;
      if (key) try { localStorage.setItem("dt:size:" + key, String(size)); } catch (e) {}
      render();
    });

    render();
  }

  document.addEventListener("DOMContentLoaded", function () {
    document.querySelectorAll("table.d-table[data-paginated]").forEach(init);
  });
})();
