/* O2 — Operations Center live counters.
 *
 * For every <tr data-mt-row-counters data-mt-router-id="X">, poll
 * /api/v1/mikrotik/X/counters every 10s and fill the four cells
 * (status pill, hotspot count, ppp count, traffic). The poll
 * cadence matches TTL_ACTIVE_USERS on the server, so any extra
 * polls land on a warm cache instead of hammering routers.
 *
 * No fake data: each cell stays em-dash until the FIRST response
 * lands. A failed call paints the status pill red with the
 * envelope's `error` text in the title attribute (for tooltip).
 */
(function () {
  "use strict";

  const table = document.querySelector("[data-mt-ops-table]");
  if (!table) return;

  const CFG = {
    apiBase:  table.dataset.mtApiBase || "/api/v1",
    apiToken: table.dataset.mtApiToken || "",
    pollMs:   10_000,
  };

  if (!CFG.apiToken) {
    // No token = nothing useful we can do. Show a one-time banner
    // and bail; users still see the static-rendered data.
    const note = document.createElement("div");
    note.className = "hub-pill hub-pill--amber";
    note.style.margin = "10px 0";
    note.textContent =
      "API token غير مهيّأ — لن تتحدّث الأعمدة الحيّة. " +
      "اضبط HOBERADIUS_API_TOKENS في البيئة.";
    table.parentNode.insertBefore(note, table);
    return;
  }

  const allRows = Array.from(
    table.querySelectorAll("tr[data-mt-row-counters]")
  );

  // O3 — bulk selection wiring. Lives in this same JS file so we
  // don't ship a second <script>. Always wire even when allRows
  // is empty (the page still renders an empty bulk bar in that
  // case).
  (function wireBulk() {
    const bulkForm = document.getElementById("mt-bulk-form");
    if (!bulkForm) return;
    const countEl   = bulkForm.querySelector("[data-mt-bulk-count]");
    const actionBtns = Array.from(
      bulkForm.querySelectorAll("[data-mt-bulk-action]")
    );
    const rowSelects = Array.from(
      table.querySelectorAll("[data-mt-row-select]")
    );
    const selectAll  = table.querySelector("[data-mt-bulk-toggle-all]");

    function refreshState() {
      const selected = rowSelects.filter(cb => cb.checked).length;
      if (countEl) countEl.textContent = String(selected);
      const disabled = selected === 0;
      actionBtns.forEach(b => { b.disabled = disabled; });
      // Header checkbox tri-state for clarity.
      if (selectAll) {
        if (selected === 0) {
          selectAll.checked = false;
          selectAll.indeterminate = false;
        } else if (selected === rowSelects.length) {
          selectAll.checked = true;
          selectAll.indeterminate = false;
        } else {
          selectAll.checked = false;
          selectAll.indeterminate = true;
        }
      }
    }

    rowSelects.forEach(cb => cb.addEventListener("change", refreshState));
    if (selectAll) {
      selectAll.addEventListener("change", () => {
        rowSelects.forEach(cb => { cb.checked = selectAll.checked; });
        refreshState();
      });
    }
    refreshState();
  })();

  // Only ENABLED rows are polled. Disabled rows are shown with a
  // static "معطّل" badge by the server-render path; polling them
  // would be wasted API calls + might confuse operators if a
  // disabled-but-still-reachable router lit up green.
  const rows = allRows.filter(
    r => (r.dataset.mtEnabled || "true") === "true"
  );
  if (!rows.length) return;

  // ── humanise byte counters ───────────────────────────────────
  function bytesHuman(v) {
    const n = Number(v);
    if (!Number.isFinite(n) || n < 0) return "—";
    if (n < 1024) return n + " B";
    const units = ["KB", "MB", "GB", "TB"];
    let val = n / 1024, idx = 0;
    while (val >= 1024 && idx < units.length - 1) { val /= 1024; idx++; }
    return val.toFixed(val >= 10 ? 0 : 1) + " " + units[idx];
  }

  function setStatus(row, state, label) {
    const pill  = row.querySelector("[data-mt-row-status]");
    const text  = row.querySelector("[data-mt-row-status-label]");
    if (pill) pill.setAttribute("data-mt-state", state);
    if (text) text.textContent = label;
  }

  async function refreshRow(row) {
    const id = row.dataset.mtRouterId;
    let res, body;
    try {
      res = await fetch(`${CFG.apiBase}/mikrotik/${id}/counters`, {
        headers: { "Authorization": "Bearer " + CFG.apiToken },
      });
      try { body = await res.json(); } catch (_) { body = null; }
    } catch (e) {
      setStatus(row, "error", "خطأ شبكة");
      row.querySelector("[data-mt-row-status]").title = String(e);
      return;
    }

    if (!res.ok || !body || body.ok === false) {
      const msg = (body && body.error && body.error.message)
        ? body.error.message
        : ("HTTP " + res.status);
      setStatus(row, "error", "تعذّر");
      row.querySelector("[data-mt-row-status]").title = msg;
      return;
    }

    // Envelope inside envelope: top {ok, data}, then data is the
    // MtResult-shape {ok, data:NasCounters, error, ...}.
    const env = body.data || {};
    const d = env.data || {};

    // Fill counter cells. Even if the envelope is partial (env.ok
    // === false but env.data is present) we still paint what we
    // have — the badge just goes amber instead of green.
    const setText = (sel, val) => {
      const el = row.querySelector(sel);
      if (el) el.textContent = (val == null ? "—" : String(val));
    };
    setText("[data-mt-row-hotspot]", d.hotspot_active);
    setText("[data-mt-row-ppp]",     d.ppp_active);
    setText("[data-mt-row-rx]",      bytesHuman(d.rx_bytes_total));
    setText("[data-mt-row-tx]",      bytesHuman(d.tx_bytes_total));

    if (env.ok) {
      setStatus(row, "ok", "متصل");
    } else {
      setStatus(row, "partial", "جزئي");
      const pill = row.querySelector("[data-mt-row-status]");
      if (pill) pill.title = env.error || "";
    }
  }

  async function refreshAll() {
    // Fan-out — each row is independent, so we don't await in
    // series. A slow router doesn't block the others.
    await Promise.all(rows.map(refreshRow));
  }

  refreshAll();
  setInterval(refreshAll, CFG.pollMs);
})();
