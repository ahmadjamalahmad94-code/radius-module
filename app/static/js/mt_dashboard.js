/* K9 — MikroTik per-router dashboard.
 *
 * Single entrypoint. Reads config from the [data-mt-dashboard] root:
 *   data-mt-router-id    — NAS id (used in every API URL)
 *   data-mt-api-base     — "/api/v1"
 *   data-mt-api-token    — Bearer token (env-derived, empty in
 *                          unconfigured prod → fatal UI banner)
 *
 * K9.1 ships: KPI strip refresh from /system/overview.
 * K9.2 adds : SSE live traffic + active-users panels.
 * K9.3 adds : quick actions strip + confirmation modals.
 */
(function () {
  "use strict";

  const root = document.querySelector("[data-mt-dashboard]");
  if (!root) return;

  const CFG = {
    routerId: root.dataset.mtRouterId,
    apiBase: root.dataset.mtApiBase || "/api/v1",
    apiToken: root.dataset.mtApiToken || "",
    overviewIntervalMs: 10_000,
  };

  // ── P1 — Tabs ───────────────────────────────────────────────────
  //
  // The hash format is "#tab-<slug>". We strip the prefix on read
  // and add it on write so the URL stays readable and we don't
  // clash with anchor fragments inside a panel.
  (function initTabs() {
    const tabsNav = root.querySelector("[data-mt-tabs]");
    if (!tabsNav) return;
    const tabs   = Array.from(tabsNav.querySelectorAll("[data-mt-tab]"));
    const panels = Array.from(root.querySelectorAll("[data-mt-tab-panel]"));
    if (!tabs.length || !panels.length) return;

    const known = new Set(tabs.map(t => t.dataset.mtTab));

    function show(slug) {
      if (!known.has(slug)) slug = "overview";
      tabs.forEach(t => {
        const on = t.dataset.mtTab === slug;
        t.classList.toggle("is-active", on);
        t.setAttribute("aria-selected", on ? "true" : "false");
      });
      panels.forEach(p => {
        const on = p.dataset.mtTabPanel === slug;
        p.classList.toggle("is-active", on);
        if (on) p.removeAttribute("hidden");
        else    p.setAttribute("hidden", "");
      });
      // Per-tab modules listen for this event so they can lazy-load
      // and pause polling when their panel is off-screen.
      root.dispatchEvent(new CustomEvent("mt:tab-change",
                                         { detail: { slug } }));
    }

    function fromHash() {
      const h = (location.hash || "").replace(/^#/, "");
      if (h.startsWith("tab-")) return h.slice(4);
      return "overview";
    }

    tabs.forEach(t => {
      t.addEventListener("click", () => {
        const slug = t.dataset.mtTab;
        show(slug);
        // Update hash without scrolling the page to top.
        const newHash = "#tab-" + slug;
        if (location.hash !== newHash) {
          history.replaceState(null, "", newHash);
        }
      });
    });

    window.addEventListener("hashchange", () => show(fromHash()));
    show(fromHash());
  })();

  // ── Status pill helpers ─────────────────────────────────────────
  const statusRow = root.querySelector("[data-mt-status]");
  const statusLabel = root.querySelector("[data-mt-status-label]");
  const statusMeta = root.querySelector("[data-mt-status-meta]");

  function setStatus(state, label, meta) {
    if (!statusRow) return;
    statusRow.setAttribute("data-mt-status-state", state);
    if (statusLabel) statusLabel.textContent = label;
    if (statusMeta && meta != null) statusMeta.textContent = meta;
  }

  if (!CFG.apiToken) {
    setStatus(
      "error",
      "لا يوجد API token مُهيَّأ",
      "اضبط HOBERADIUS_API_TOKENS في البيئة ثم أعد تحميل الصفحة.",
    );
    return; // Without a token every fetch will 401 — bail loudly.
  }

  // ── Tiny fetch wrapper ──────────────────────────────────────────
  async function api(path, opts) {
    const init = opts || {};
    init.headers = Object.assign(
      { Authorization: "Bearer " + CFG.apiToken },
      init.headers || {},
    );
    const res = await fetch(CFG.apiBase + path, init);
    let body = null;
    try { body = await res.json(); } catch (_) { /* non-JSON */ }
    return { res, body };
  }

  // ── KPI strip refresh ───────────────────────────────────────────

  // Light helpers that gracefully handle missing fields. RouterOS
  // returns strings for everything; we coerce where it helps.
  function asNumber(v) {
    const n = parseFloat(v);
    return Number.isFinite(n) ? n : null;
  }
  function bytesHuman(v) {
    const n = asNumber(v);
    if (n == null) return null;
    if (n < 1024) return n + " B";
    const units = ["KB", "MB", "GB", "TB"];
    let val = n / 1024, idx = 0;
    while (val >= 1024 && idx < units.length - 1) { val /= 1024; idx++; }
    return val.toFixed(1) + " " + units[idx];
  }
  function clampPercent(v) {
    const n = asNumber(v);
    if (n == null) return 0;
    return Math.max(0, Math.min(100, n));
  }
  function pctFromUsed(total, free) {
    const totalN = asNumber(total);
    const freeN = asNumber(free);
    if (!totalN || freeN == null || totalN <= 0) return null;
    return clampPercent(((totalN - freeN) / totalN) * 100);
  }
  function pctLabel(v) {
    const n = clampPercent(v);
    return (Math.round(n * 10) / 10).toString().replace(/\.0$/, "") + "%";
  }
  function setKpi(kind, value, sub, opts) {
    const card = root.querySelector(`[data-mt-kpi="${kind}"]`);
    if (!card) return;
    const valueEl = card.querySelector("[data-mt-kpi-value]");
    const subEl = card.querySelector("[data-mt-kpi-sub]");
    if (valueEl) valueEl.textContent = value != null ? value : "—";
    if (subEl && sub != null) subEl.textContent = sub;
    if (opts && opts.progress != null) {
      card.style.setProperty("--kpi-progress", clampPercent(opts.progress) + "%");
    }
  }

  function renderOverview(payload) {
    const sections = (payload && payload.sections) || {};
    const resource = (sections.resource && sections.resource.data) || [];
    const health   = (sections.health   && sections.health.data)   || [];
    const router_b = (sections.routerboard && sections.routerboard.data) || [];
    const clock    = (sections.clock && sections.clock.data) || [];

    const resourceRow = resource[0] || {};
    const healthRow   = health[0]   || {};
    const boardRow    = router_b[0] || {};
    const clockRow    = clock[0] || {};

    setKpi("uptime", resourceRow["uptime"] || "—",
           resourceRow["build-time"] ? "بُني " + resourceRow["build-time"] : null);

    const cpu = resourceRow["cpu-load"];
    if (cpu != null) {
      setKpi("cpu", cpu + "%", resourceRow["cpu"] ? resourceRow["cpu"] : null, { progress: cpu });
    }
    setKpi("cpu", cpu != null ? cpu + "%" : "—",
           resourceRow["cpu"] ? resourceRow["cpu"] : null);

    const memFreeRaw = resourceRow["free-memory"];
    const memTotalRaw = resourceRow["total-memory"];
    const free = bytesHuman(memFreeRaw);
    const total = bytesHuman(memTotalRaw);
    const memUsedPct = pctFromUsed(memTotalRaw, memFreeRaw);
    setKpi("memory",
           memUsedPct != null ? pctLabel(memUsedPct) : "\u2014",
           (free && total) ? (free + " \u0645\u062a\u0627\u062d \u0645\u0646 " + total) : "\u0645\u062a\u0627\u062d / \u0625\u062c\u0645\u0627\u0644\u064a",
           { progress: memUsedPct });

    // Disk \u2014 try every name RouterOS uses across versions / boards.
    // CCR / RB devices that boot off NAND expose `free-hdd-space`;
    // some flavours rename it to `free-storage` or omit it entirely
    // and expose flash-only counters.
    const diskFreeRaw = resourceRow["free-hdd-space"]
                     || resourceRow["free-hdd"]
                     || resourceRow["free-storage"];
    const diskTotalRaw = resourceRow["total-hdd-space"]
                      || resourceRow["total-hdd"]
                      || resourceRow["total-storage"];
    const diskFree = bytesHuman(diskFreeRaw);
    const diskTotal = bytesHuman(diskTotalRaw);
    const diskUsedPct = pctFromUsed(diskTotalRaw, diskFreeRaw);
    setKpi("disk",
           diskUsedPct != null ? pctLabel(diskUsedPct) : "\u2014",
           (diskFree && diskTotal) ? (diskFree + " \u0645\u062a\u0627\u062d \u0645\u0646 " + diskTotal) : "\u0645\u0633\u062a\u062e\u062f\u0645 / \u0625\u062c\u0645\u0627\u0644\u064a",
           { progress: diskUsedPct });
    if (window.console && diskUsedPct == null) {
      console.log("[overview] disk: no HDD fields in /system/resource \u2014 keys:",
                  Object.keys(resourceRow).join(", "));
    }

    // Health rows may not exist on every RouterOS variant.
    let temp = null;
    if (Array.isArray(health)) {
      for (const h of health) {
        const n = (h.name || "").toLowerCase();
        if (n.includes("cpu") && n.includes("temperature")) { temp = h.value; break; }
        if (n === "temperature" && temp == null) temp = h.value;
      }
    }
    setKpi("temperature", temp != null ? temp + "°C" : "—",
           "من /system/health");

    // Clock — RouterOS 7 exposes `time` + `date` from
    // /system/clock/print; older versions sometimes use
    // `current-time` / `current-date`. Try both shapes.
    const ctime = clockRow.time || clockRow["current-time"];
    const cdate = clockRow.date || clockRow["current-date"];
    setKpi("clock", ctime || "—", cdate || "وقت الراوتر الحالي");
    if (window.console && !ctime) {
      console.log("[overview] clock: no time field — section.clock =",
                  sections.clock,
                  " row keys:", Object.keys(clockRow).join(", "));
    }

    setKpi("version", resourceRow["version"] || "—",
           boardRow["board-name"] || boardRow["model"] || "—");

    const dialed = (payload && payload.connection && payload.connection.address) || "—";
    const mode = (payload && payload.connection && payload.connection.mode) || "—";
    setKpi("dialed", dialed, mode === "vpn" ? "عبر WireGuard" : "اتصال مباشر");
  }

  async function refreshOverview() {
    setStatus("pending", "جارٍ التحديث…", "");
    try {
      const { res, body } = await api("/mikrotik/" + CFG.routerId + "/system/overview");
      if (!res.ok || !body || body.ok === false) {
        const msg = body && body.error && body.error.message
          ? body.error.message
          : ("HTTP " + res.status);
        setStatus("error", "تعذّر الاتصال بالـ API", msg);
        return;
      }
      const data = body.data || {};
      renderOverview(data);
      if (data.any_ok === false) {
        setStatus("error", "الراوتر غير قابل للوصول",
                  data.connection ? data.connection.address : "");
      } else if (data.all_ok === false) {
        setStatus("pending", "بعض الأقسام غير متاحة",
                  data.connection ? data.connection.address : "");
      } else {
        setStatus("ok", "الراوتر متصل",
                  data.connection ? data.connection.address : "");
      }
    } catch (e) {
      setStatus("error", "خطأ في الشبكة", String(e));
    }
  }

  // Kick off + poll. The /system/overview endpoint already caches
  // each sub-call for 5-60 s server-side, so a 10 s UI poll never
  // touches the router more than once per cycle.
  refreshOverview();
  setInterval(refreshOverview, CFG.overviewIntervalMs);

  // ── K9.2 — live traffic ────────────────────────────────────────
  //
  // The /interfaces/<name>/sse server endpoint exists but the
  // browser EventSource API doesn't allow custom Authorization
  // headers, and we don't want to leak Bearer tokens in URL query
  // params. So the UI polls /interfaces/<name>/traffic at the
  // same 2 s cadence the SSE generator uses server-side — the wire
  // pattern is identical, only the transport differs.

  const TRAFFIC_POLL_MS = 2000;
  const TRAFFIC_HISTORY = 60;          // 2 minutes of samples
  const ifaceSelect = root.querySelector("[data-mt-interface-select]");
  const trafficMsg  = root.querySelector("[data-mt-traffic-msg]");
  const rxLabel     = root.querySelector("[data-mt-traffic-rx]");
  const txLabel     = root.querySelector("[data-mt-traffic-tx]");
  const sparkRx     = root.querySelector("[data-mt-spark-rx]");
  const sparkTx     = root.querySelector("[data-mt-spark-tx]");

  let trafficTimer = null;
  let rxHistory = [];
  let txHistory = [];

  function bpsHuman(bps) {
    const n = asNumber(bps);
    if (n == null) return "—";
    if (n < 1000) return n.toFixed(0) + " bps";
    const units = ["kbps", "Mbps", "Gbps"];
    let v = n / 1000, i = 0;
    while (v >= 1000 && i < units.length - 1) { v /= 1000; i++; }
    return v.toFixed(v >= 10 ? 0 : 1) + " " + units[i];
  }

  function drawSparkline(el, points) {
    if (!el) return;
    if (!points.length) { el.setAttribute("d", ""); return; }
    const max = Math.max(1, ...points);
    const w = 300, h = 80, padY = 4;
    const stepX = w / Math.max(1, TRAFFIC_HISTORY - 1);
    const parts = [];
    for (let i = 0; i < points.length; i++) {
      const x = (i + (TRAFFIC_HISTORY - points.length)) * stepX;
      const y = h - padY - ((points[i] / max) * (h - 2 * padY));
      parts.push((i === 0 ? "M" : "L") + x.toFixed(1) + " " + y.toFixed(1));
    }
    el.setAttribute("d", parts.join(" "));
  }

  async function populateInterfaceList() {
    const { res, body } = await api("/mikrotik/" + CFG.routerId + "/interfaces");
    if (!res.ok || !body || body.ok === false) return;
    const env = body.data || {};
    if (env.ok === false) {
      trafficMsg.textContent = env.error || "تعذّر جلب قائمة الواجهات.";
      return;
    }
    const rows = env.data || [];
    if (!rows.length) {
      trafficMsg.textContent = "لا توجد واجهات على هذا الراوتر.";
      return;
    }
    // Preserve the placeholder option, then append real names.
    for (const r of rows) {
      const name = r.name || r["default-name"];
      if (!name) continue;
      const opt = document.createElement("option");
      opt.value = name;
      opt.textContent = name + (r.type ? "  ·  " + r.type : "");
      ifaceSelect.appendChild(opt);
    }
    trafficMsg.textContent = "اختر واجهة لعرض الحركة الحيّة.";
  }

  async function pollTraffic(ifaceName) {
    const { res, body } = await api(
      "/mikrotik/" + CFG.routerId
      + "/interfaces/" + encodeURIComponent(ifaceName) + "/traffic"
    );
    if (!res.ok || !body || body.ok === false) {
      trafficMsg.textContent = "تعذّر القراءة (HTTP " + res.status + ").";
      return;
    }
    const env = body.data || {};
    if (env.ok === false) {
      trafficMsg.textContent = env.error || "تعذّر القراءة من الراوتر.";
      return;
    }
    const sample = (env.data && env.data[0]) || {};
    const rx = asNumber(sample["rx-bits-per-second"]);
    const tx = asNumber(sample["tx-bits-per-second"]);
    if (rx == null && tx == null) {
      trafficMsg.textContent = "الراوتر لم يُرجع بيانات قياس.";
      return;
    }
    trafficMsg.textContent = "";
    rxLabel.textContent = bpsHuman(rx);
    txLabel.textContent = bpsHuman(tx);
    if (rx != null) {
      rxHistory.push(rx);
      if (rxHistory.length > TRAFFIC_HISTORY) rxHistory.shift();
    }
    if (tx != null) {
      txHistory.push(tx);
      if (txHistory.length > TRAFFIC_HISTORY) txHistory.shift();
    }
    drawSparkline(sparkRx, rxHistory);
    drawSparkline(sparkTx, txHistory);
  }

  if (ifaceSelect) {
    populateInterfaceList();
    ifaceSelect.addEventListener("change", () => {
      const name = ifaceSelect.value;
      if (trafficTimer) { clearInterval(trafficTimer); trafficTimer = null; }
      rxHistory = []; txHistory = [];
      drawSparkline(sparkRx, rxHistory);
      drawSparkline(sparkTx, txHistory);
      rxLabel.textContent = "—";
      txLabel.textContent = "—";
      if (!name) {
        trafficMsg.textContent = "اختر واجهة لعرض الحركة الحيّة.";
        return;
      }
      trafficMsg.textContent = "جارٍ القراءة…";
      pollTraffic(name);
      trafficTimer = setInterval(() => pollTraffic(name), TRAFFIC_POLL_MS);
    });
  }

  // ── K9.2 — active users ─────────────────────────────────────────

  const ACTIVE_POLL_MS = 10_000;
  const hotspotCountEl = root.querySelector("[data-mt-hotspot-count]");
  const pppCountEl     = root.querySelector("[data-mt-ppp-count]");
  const usersEmpty     = root.querySelector("[data-mt-active-users-empty]");
  const usersTable     = root.querySelector("[data-mt-active-users-table]");
  const usersRows      = root.querySelector("[data-mt-active-users-rows]");

  function renderUsers(hotspotRows, pppRows) {
    const all = [];
    for (const r of (hotspotRows || []).slice(0, 10)) {
      all.push({
        type: "hotspot",
        name: r.user || "?",
        address: r.address || "",
        uptime: r.uptime || "",
      });
    }
    for (const r of (pppRows || []).slice(0, 10)) {
      all.push({
        type: "ppp",
        name: r.name || r.user || "?",
        address: r.address || r["remote-address"] || "",
        uptime: r.uptime || "",
      });
    }
    if (!all.length) {
      usersTable.hidden = true;
      usersEmpty.hidden = false;
      usersEmpty.textContent = "لا يوجد مستخدمون متصلون الآن.";
      return;
    }
    usersEmpty.hidden = true;
    usersTable.hidden = false;
    // Wipe + rebuild — list size is small, no need for diffing.
    usersRows.textContent = "";
    for (const u of all) {
      const tr = document.createElement("tr");
      const cls = u.type === "ppp" ? " mt-user-type--ppp" : "";
      tr.innerHTML = `
        <td><span class="mt-user-type${cls}">${u.type}</span></td>
        <td></td><td></td><td></td>`;
      tr.children[1].textContent = u.name;
      tr.children[2].textContent = u.address;
      tr.children[3].textContent = u.uptime;
      usersRows.appendChild(tr);
    }
  }

  async function refreshActiveUsers() {
    const [a, b] = await Promise.all([
      api("/mikrotik/" + CFG.routerId + "/hotspot/active"),
      api("/mikrotik/" + CFG.routerId + "/ppp/active"),
    ]);
    let hot = [], ppp = [];
    let hotCount = "—", pppCount = "—";
    if (a.res.ok && a.body && a.body.ok && a.body.data) {
      const env = a.body.data;
      if (env.ok) { hot = env.data || []; hotCount = hot.length; }
    }
    if (b.res.ok && b.body && b.body.ok && b.body.data) {
      const env = b.body.data;
      if (env.ok) { ppp = env.data || []; pppCount = ppp.length; }
    }
    if (hotspotCountEl) hotspotCountEl.textContent = hotCount;
    if (pppCountEl)     pppCountEl.textContent = pppCount;
    renderUsers(hot, ppp);
  }

  if (hotspotCountEl) {
    refreshActiveUsers();
    setInterval(refreshActiveUsers, ACTIVE_POLL_MS);
  }

  // ── K9.3 — quick actions ───────────────────────────────────────
  //
  // Each action opens a small inline form. The Submit button posts
  // the JSON body the K8 endpoints expect (confirm: true for
  // destructive ones). The output area prints the actual response
  // verbatim — no fake "success" toast. Destructive actions
  // additionally require the operator to tick a checkbox before
  // the Submit button enables.

  const actionFormEl  = root.querySelector("[data-mt-action-form]");
  const actionOutEl   = root.querySelector("[data-mt-action-output]");
  const actionResEl   = root.querySelector("[data-mt-action-result]");
  const actionRawWrap = root.querySelector("[data-mt-action-raw-wrap]");
  const actionButtons = {
    backup:      root.querySelector("[data-mt-action-backup]"),
    reboot:      root.querySelector("[data-mt-action-reboot]"),
    ping:        root.querySelector("[data-mt-action-ping]"),
    identity:    root.querySelector("[data-mt-action-identity]"),
    // New (2026-05): info + maintenance actions.
    traceroute:  root.querySelector("[data-mt-action-traceroute]"),
    // dns-resolve removed 2026-05 — RouterOS 7 rejects /resolve
    // with «unknown parameter name» and the operator asked to
    // drop the tile rather than chase more shape fixes. The
    // backend route /tools/dns-resolve is left intact in case a
    // future revision lands a working path.
    "dns-flush":   root.querySelector("[data-mt-action-dns-flush]"),
    "clock-sync":  root.querySelector("[data-mt-action-clock-sync]"),
  };
  let currentActionKind = "";

  function clearActiveButtons() {
    for (const k in actionButtons) {
      if (actionButtons[k]) actionButtons[k].classList.remove("is-active");
    }
  }

  function safeHtml(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }

  function writeOutput(payload, ok) {
    // Raw JSON dump — always populated for the «عرض الاستجابة
    // الخام» disclosure. Stays hidden until the operator expands.
    if (actionOutEl) {
      actionOutEl.textContent = JSON.stringify(payload, null, 2);
      actionOutEl.classList.toggle("is-ok", !!ok);
      actionOutEl.classList.toggle("is-fail", !ok);
    }
    if (actionRawWrap) actionRawWrap.hidden = false;
    // Friendly card — built per action kind.
    if (actionResEl) {
      actionResEl.innerHTML = renderResultCard(currentActionKind, payload, ok);
      actionResEl.classList.toggle("is-ok", !!ok);
      actionResEl.classList.toggle("is-fail", !ok);
      actionResEl.hidden = false;
    }
  }

  /**
   * Build a friendly HTML card for the operator instead of dumping
   * raw JSON. Handles known action kinds (ping, traceroute, dns,
   * backup, reboot, identity, clock); falls back to a one-line OK /
   * fail summary for anything else.
   */
  function renderResultCard(kind, payload, ok) {
    const data = (payload && typeof payload === "object")
      ? (payload.data || payload) : {};
    const errMsg = (payload && (payload.error || payload.message)) || "";
    const headIcon = ok
      ? '<i class="fa-solid fa-check"></i>'
      : '<i class="fa-solid fa-xmark"></i>';
    function head(title, meta) {
      return `
        <div class="mt-action-result-head">
          <span class="mt-action-result-icon">${headIcon}</span>
          <span class="mt-action-result-title">${safeHtml(title)}</span>
          ${meta ? `<span class="mt-action-result-meta">${safeHtml(meta)}</span>` : ""}
        </div>`;
    }
    function body(inner) {
      return `<div class="mt-action-result-body">${inner}</div>`;
    }
    function failBody() {
      return body(`
        <div class="mt-action-result-summary">
          ${safeHtml(errMsg || "تعذّر تنفيذ العملية على الراوتر.")}
        </div>`);
    }

    // ── PING ──────────────────────────────────────────────
    // RouterOS shape:
    //   { data: { cached, count, data: [
    //       { host, seq, size, time, ttl,
    //         sent, received, "packet-loss",
    //         "min-rtt", "avg-rtt", "max-rtt", ... },
    //       ...
    //     ] } }
    // Each entry is one sequence (ping iteration). The cumulative
    // counters (sent / received / packet-loss / avg-rtt) update on
    // each row — the LAST row carries the final totals.
    if (kind === "ping") {
      if (!ok) return head("فشل اختبار Ping", "/tools/ping") + failBody();
      // The API wraps the array in `data.data` (envelope has its own
      // `data` key, the inner row-set is also called `data`). Try
      // both shapes so a future API change doesn't break us.
      const replies = Array.isArray(data.data) ? data.data
                    : Array.isArray(data.replies) ? data.replies
                    : Array.isArray(data) ? data
                    : [];
      const target = data.target || data.host
                  || (replies[0] && replies[0].host) || "";
      let tableHtml = "";
      if (replies.length) {
        tableHtml = `
          <table>
            <thead>
              <tr><th>#</th><th>السعة</th><th>TTL</th><th>الزمن</th><th>الحالة</th></tr>
            </thead>
            <tbody>
              ${replies.map((r, i) => {
                const arrived = (r.time != null && r.time !== "")
                              || (r["received"] && Number(r["received"]) > Number(r["sent"] || 0) - 1 && r.time);
                return `
                <tr>
                  <td>${safeHtml(r.seq != null ? Number(r.seq) + 1 : i + 1)}</td>
                  <td>${safeHtml(r.size != null ? r.size + " B" : "—")}</td>
                  <td>${safeHtml(r.ttl != null ? r.ttl : "—")}</td>
                  <td>${safeHtml(r.time != null && r.time !== "" ? r.time : "—")}</td>
                  <td>${arrived
                    ? '<span style="color:#10B981">✓ وصل</span>'
                    : '<span style="color:#DC2626">✗ ضاع</span>'}</td>
                </tr>`;
              }).join("")}
            </tbody>
          </table>
        `;
      }
      // Final cumulative counters live on the LAST row.
      const last = replies[replies.length - 1] || {};
      const sent   = Number(last.sent     != null ? last.sent     : replies.length);
      const recv   = Number(last.received != null ? last.received : replies.filter(r => r.time).length);
      const lossRaw = last["packet-loss"];
      const loss   = lossRaw != null && lossRaw !== ""
                    ? String(lossRaw).replace(/%$/, "")
                    : (sent > 0 ? Math.round(((sent - recv) / sent) * 100) : 0);
      const avgRtt = last["avg-rtt"] || last.avg_rtt || last.time || "—";
      const minRtt = last["min-rtt"] || "—";
      const maxRtt = last["max-rtt"] || "—";
      return head(`Ping إلى ${target || "—"}`, "/tools/ping") + body(`
        ${tableHtml}
        <div class="mt-action-result-summary">
          مُرسَل: <strong>${sent}</strong> ·
          مُستلَم: <strong>${recv}</strong> ·
          فاقد: <strong>${loss}%</strong> ·
          متوسط: <strong>${safeHtml(avgRtt)}</strong> ·
          أدنى: <strong>${safeHtml(minRtt)}</strong> ·
          أعلى: <strong>${safeHtml(maxRtt)}</strong>
        </div>
      `);
    }

    // ── TRACEROUTE ────────────────────────────────────────
    // RouterOS shape:
    //   { data: { data: [
    //       { address, status, sent, last, avg, best, worst,
    //         loss, "std-dev", ... }, ... ] } }
    // Each row is one hop. RouterOS uses different field names than
    // I assumed — address (not host), last/avg/best/worst (not rtt
    // or time). The traceroute may also stream results — we get the
    // final accumulated table back from the API endpoint.
    if (kind === "traceroute") {
      if (!ok) return head("فشل Traceroute", "/tools/traceroute") + failBody();
      const hops = Array.isArray(data.data)    ? data.data
                 : Array.isArray(data.hops)    ? data.hops
                 : Array.isArray(data.replies) ? data.replies
                 : Array.isArray(data)         ? data
                 : [];
      const target = data.target || data.host || "";
      const rows = hops.map((h, i) => {
        const addr = h.address || h.host || "*";
        const last = h.last || h.rtt || h.time || "—";
        const avg  = h.avg || "—";
        const lossRaw = h.loss != null ? h.loss : h["packet-loss"];
        const loss = lossRaw != null && lossRaw !== ""
                   ? String(lossRaw).replace(/%$/, "") + "%" : "—";
        const status = h.status || "";
        const dim = (addr === "*" || /timeout|unreachable/i.test(status));
        return `
        <tr${dim ? ' style="opacity:.6"' : ""}>
          <td>${i + 1}</td>
          <td>${safeHtml(addr)}</td>
          <td>${safeHtml(last)}${avg !== "—" ? ` <span style="color:#94A3B8">/${safeHtml(avg)}</span>` : ""}</td>
          <td>${safeHtml(loss)}</td>
        </tr>`;
      }).join("");
      return head(`Traceroute إلى ${target || "—"}`, "/tools/traceroute") + body(`
        <table>
          <thead>
            <tr><th>القفزة</th><th>العنوان</th><th>آخر/متوسط</th><th>الفقد</th></tr>
          </thead>
          <tbody>${rows || '<tr><td colspan="4" style="text-align:center;color:#94A3B8">لا توجد قفزات</td></tr>'}</tbody>
        </table>
        <div class="mt-action-result-summary">
          عدد القفزات: <strong>${hops.length}</strong>
          ${hops[hops.length-1] && hops[hops.length-1].status
            ? ' · الحالة الأخيرة: <strong>' + safeHtml(hops[hops.length-1].status) + '</strong>'
            : ''}
        </div>
      `);
    }

    // ── DNS RESOLVE ───────────────────────────────────────
    // RouterOS shape varies by version. The backend now always
    // injects an aggregator row at index 0 with addresses[] so the
    // shape lookup is deterministic. We still defensively handle
    // every legacy shape.
    if (kind === "dns-resolve") {
      if (!ok) return head("فشل حلّ النطاق", "/resolve") + failBody();
      const rows = Array.isArray(data.data) ? data.data : [];
      const first = rows[0] || {};
      const name = data.name || data.host || data.query || first.name || "";
      // Collect addresses from any of the known shapes.
      const seen = new Set();
      const addrs = [];
      const push = (v) => {
        if (!v) return;
        // Handle comma-separated address-list strings.
        String(v).split(",").map(s => s.trim()).filter(Boolean)
          .forEach(a => { if (!seen.has(a)) { seen.add(a); addrs.push(a); } });
      };
      if (Array.isArray(data.addresses)) data.addresses.forEach(push);
      if (Array.isArray(data.ips))       data.ips.forEach(push);
      if (data.address)                  push(data.address);
      rows.forEach(r => {
        if (typeof r === "string")     return push(r);
        if (Array.isArray(r.addresses)) r.addresses.forEach(push);
        push(r.address);
        push(r.ipv6);
        push(r.address6);
        push(r["address-list"]);
        push(r.host);
      });
      const list = addrs.map(a => `<dd>${safeHtml(a)}</dd>`).join("");
      return head(`نتيجة الحلّ — ${name}`, "/resolve") + body(`
        <dl class="mt-kv">
          <dt>النطاق</dt><dd>${safeHtml(name)}</dd>
          ${list
            ? `<dt>العناوين</dt><dd>${addrs.length} نتيجة</dd>${list}`
            : '<dt>العناوين</dt><dd>— (لم يُحَلّ)</dd>'}
        </dl>
        ${list ? `
          <div class="mt-action-result-summary">
            ✓ النطاق متاح. هذي IPs اللي يحلّها الراوتر حالياً.
          </div>` : `
          <div class="mt-action-result-summary"
               style="background:#FEF3C7;border-color:#FCD34D;color:#92400E">
            ⚠ الراوتر استلم الطلب لكن لم يُرجع أي IP. الأسباب الشائعة:
            <ol style="margin:6px 14px 0;padding:0;font-size:12px">
              <li>الـ DNS غير مُهيَّأ على الراوتر — تحقّق من
                  <code>/ip dns set servers=…</code></li>
              <li>النطاق غير موجود (NXDOMAIN)</li>
              <li>الـ tunnel لا يصل لخادم DNS الذي يستعمله الراوتر</li>
            </ol>
          </div>`}
      `);
    }

    // ── DNS FLUSH ─────────────────────────────────────────
    if (kind === "dns-flush") {
      if (!ok) return head("فشل مسح كاش DNS", "/ip/dns/cache/flush") + failBody();
      return head("تم مسح كاش DNS بنجاح", "/ip/dns/cache/flush") + body(`
        <div class="mt-action-result-summary">
          ✓ كاش DNS فارغ الآن. ستُعاد الاستعلامات من المصدر عند الطلب التالي.
        </div>
      `);
    }

    // ── CLOCK SYNC ────────────────────────────────────────
    if (kind === "clock-sync") {
      if (!ok) return head("فشل مزامنة الوقت", "/system/ntp") + failBody();
      return head("تمت مزامنة الوقت", "/system/ntp/client") + body(`
        <dl class="mt-kv">
          ${data.time     ? `<dt>الوقت الآن</dt><dd>${safeHtml(data.time)}</dd>` : ""}
          ${data.ntp_peer ? `<dt>خادم NTP</dt><dd>${safeHtml(data.ntp_peer)}</dd>` : ""}
        </dl>
        <div class="mt-action-result-summary">
          ✓ ساعة الراوتر مضبوطة بنجاح من خادم NTP.
        </div>
      `);
    }

    // ── BACKUP ────────────────────────────────────────────
    if (kind === "backup") {
      if (!ok) return head("فشل حفظ النسخة الاحتياطية", "/system/backup/save") + failBody();
      return head("تم حفظ النسخة الاحتياطية", "/system/backup/save") + body(`
        <dl class="mt-kv">
          ${data.name ? `<dt>اسم الملف</dt><dd>${safeHtml(data.name)}</dd>` : ""}
          ${data.size ? `<dt>الحجم</dt><dd>${safeHtml(data.size)}</dd>` : ""}
        </dl>
        <div class="mt-action-result-summary">
          ✓ ملف backup محفوظ على الراوتر. يمكنك تنزيله من File List في Winbox.
        </div>
      `);
    }

    // ── REBOOT ────────────────────────────────────────────
    if (kind === "reboot") {
      if (!ok) return head("فشل إعادة التشغيل", "/system/reboot") + failBody();
      return head("تمت إعادة التشغيل", "/system/reboot") + body(`
        <div class="mt-action-result-summary">
          ⏳ الراوتر يُعيد التشغيل الآن. سيُقطع الاتصال لدقيقة تقريباً.
          أعد تحميل الصفحة بعد دقيقة.
        </div>
      `);
    }

    // ── IDENTITY ──────────────────────────────────────────
    if (kind === "identity") {
      if (!ok) return head("فشل تعديل الاسم", "/system/identity") + failBody();
      return head("تم تحديث اسم الراوتر", "/system/identity") + body(`
        <dl class="mt-kv">
          ${data.name ? `<dt>الاسم الجديد</dt><dd>${safeHtml(data.name)}</dd>` : ""}
        </dl>
      `);
    }

    // ── DEFAULT (unknown action) ──────────────────────────
    return head(
      ok ? "تمّ تنفيذ العملية" : "فشلت العملية",
      ""
    ) + (ok
      ? body('<div class="mt-action-result-summary">✓ راجع «عرض الاستجابة الخام» للتفاصيل.</div>')
      : failBody());
  }

  function closeForm() {
    if (!actionFormEl) return;
    actionFormEl.hidden = true;
    actionFormEl.textContent = "";
    if (actionResEl)   { actionResEl.hidden = true; actionResEl.innerHTML = ""; }
    if (actionRawWrap) { actionRawWrap.hidden = true; actionRawWrap.open = false; }
    currentActionKind = "";
    clearActiveButtons();
  }

  function openForm(key, html) {
    if (!actionFormEl) return;
    actionFormEl.hidden = false;
    actionFormEl.innerHTML = html;
    if (actionResEl)   { actionResEl.hidden = true; actionResEl.innerHTML = ""; }
    if (actionRawWrap) { actionRawWrap.hidden = true; actionRawWrap.open = false; }
    currentActionKind = key;
    clearActiveButtons();
    if (actionButtons[key]) actionButtons[key].classList.add("is-active");
    const cancel = actionFormEl.querySelector(".mt-cancel");
    if (cancel) cancel.addEventListener("click", closeForm);
  }

  /**
   * Per-action stage scripts. Each stage is what the operator sees
   * happening; we fake the timing client-side (we don't have real
   * server-streamed events), but the durations are calibrated to
   * typical observed wall-clock for each command type so the stage
   * doesn't run ahead of the actual server response.
   *
   * Each stage: { label, hint, duration_ms }.
   */
  const PROGRESS_STAGES = {
    ping: [
      { label: "الاتصال بالراوتر عبر VPN",     hint: "إنشاء قناة آمنة عبر WireGuard", t: 400 },
      { label: "إرسال حزم Ping من الراوتر",    hint: "نطلب من الراوتر أن يُرسل الحزم للهدف", t: 1200 },
      { label: "انتظار الردود من الهدف",        hint: "كل حزمة تنتظر TTL لإلتقاط ردّها", t: 1600 },
      { label: "جلب النتيجة من الراوتر",        hint: "جمع الإحصائيات النهائية", t: 400 },
    ],
    traceroute: [
      { label: "الاتصال بالراوتر عبر VPN",     hint: "إنشاء قناة آمنة عبر WireGuard", t: 400 },
      { label: "تنفيذ traceroute على الراوتر",  hint: "خطوة-بخطوة عبر شبكة الـ ISP", t: 4000 },
      { label: "جمع القفزات",                   hint: "بعض القفزات قد تتأخّر — لا تقلق", t: 3000 },
      { label: "إرسال النتيجة",                 hint: "جلب الجدول النهائي", t: 400 },
    ],
    "dns-resolve": [
      { label: "الاتصال بالراوتر",              hint: "قناة API آمنة", t: 300 },
      { label: "استعلام DNS",                    hint: "سؤال خادم DNS عن الاسم", t: 800 },
      { label: "جلب النتيجة",                    hint: "العناوين IP المُحلَّلة", t: 200 },
    ],
    "dns-flush": [
      { label: "الاتصال بالراوتر",              hint: "قناة API آمنة", t: 300 },
      { label: "تنفيذ /ip/dns/cache/flush",      hint: "إفراغ كاش الـ DNS", t: 500 },
      { label: "تأكيد العملية",                  hint: "الكاش فارغ الآن", t: 200 },
    ],
    "clock-sync": [
      { label: "الاتصال بالراوتر",              hint: "قناة API آمنة", t: 300 },
      { label: "إيقاف عميل NTP مؤقّتاً",         hint: "خطوة تحضيرية", t: 400 },
      { label: "إعادة تفعيله للمزامنة",          hint: "العميل يربط مع pool خوادم NTP", t: 1000 },
      { label: "قراءة الساعة الجديدة",           hint: "للتحقّق", t: 300 },
    ],
    backup: [
      { label: "الاتصال بالراوتر",              hint: "قناة API آمنة", t: 400 },
      { label: "تنفيذ /system/backup/save",       hint: "الراوتر يكتب الملف على الذاكرة الداخلية", t: 1500 },
      { label: "التحقق من إنشاء الملف",          hint: "قائمة /file بعد الحفظ", t: 600 },
    ],
    reboot: [
      { label: "الاتصال بالراوتر",              hint: "قناة API آمنة", t: 300 },
      { label: "إرسال أمر /system/reboot",        hint: "الراوتر يبدأ الإقلاع", t: 500 },
    ],
    identity: [
      { label: "الاتصال بالراوتر",              hint: "قناة API آمنة", t: 300 },
      { label: "تعديل /system/identity",          hint: "تطبيق الاسم الجديد", t: 400 },
      { label: "تأكيد التغيير",                   hint: "قراءة الاسم بعد التعديل", t: 300 },
    ],
    disconnect: [
      { label: "الاتصال بالراوتر",              hint: "قناة API آمنة", t: 300 },
      { label: "إزالة الجلسة من /active",        hint: "كَيك الجلسة", t: 400 },
    ],
  };

  /**
   * Show an in-progress card with sequential STAGES so the operator
   * sees exactly what's happening + where we are. Each stage flips
   * to «✓ مكتمل» when its synthetic timer expires; the LAST stage
   * waits for the real fetch to return (its timer pauses near the
   * end, so we don't claim completion before the server responds).
   */
  function showProgress(kind) {
    if (!actionResEl) return () => {};
    const stages = PROGRESS_STAGES[kind] || [
      { label: "الاتصال بالراوتر",        hint: "قناة API آمنة", t: 400 },
      { label: "تنفيذ الأمر",              hint: "يعمل…", t: 1500 },
      { label: "جلب النتيجة",              hint: "نهائي", t: 400 },
    ];
    actionResEl.classList.remove("is-ok", "is-fail");
    actionResEl.classList.add("is-progress");
    const titleMap = {
      ping:          "اختبار Ping",
      traceroute:    "Traceroute",
      "dns-resolve": "حلّ نطاق DNS",
      "dns-flush":   "مسح كاش DNS",
      "clock-sync":  "مزامنة الوقت",
      backup:        "حفظ نسخة احتياطية",
      reboot:        "إعادة تشغيل",
      identity:      "تعديل اسم الراوتر",
      disconnect:    "قطع الاتصال",
    };
    actionResEl.innerHTML = `
      <div class="mt-action-result-head">
        <span class="mt-action-result-icon">
          <i class="fa-solid fa-spinner fa-spin"></i>
        </span>
        <span class="mt-action-result-title">
          ${safeHtml(titleMap[kind] || "تنفيذ العملية")} — جارٍ المعالجة…
        </span>
        <span class="mt-action-result-meta" data-mt-progress-tick>0.0s</span>
      </div>
      <div class="mt-action-result-body">
        <ol class="mt-action-stages" aria-label="مراحل التنفيذ">
          ${stages.map((s, i) => `
            <li class="mt-action-stage" data-stage-index="${i}">
              <span class="mt-action-stage-bullet">
                <i class="fa-solid fa-circle"></i>
              </span>
              <div class="mt-action-stage-body">
                <span class="mt-action-stage-label">${safeHtml(s.label)}</span>
                <span class="mt-action-stage-hint">${safeHtml(s.hint || "")}</span>
              </div>
              <span class="mt-action-stage-check">
                <i class="fa-solid fa-check"></i>
              </span>
            </li>
          `).join("")}
        </ol>
        <div class="mt-action-progress-bar"
             aria-label="جارٍ التنفيذ" role="progressbar">
          <div class="mt-action-progress-bar-fill"></div>
        </div>
        <div class="mt-action-result-summary">
          ⚡ نُنفّذ الأمر على الراوتر عبر الـ VPN. لا تُغلق الصفحة.
        </div>
      </div>
    `;
    actionResEl.hidden = false;
    if (actionRawWrap) actionRawWrap.hidden = true;

    // Live elapsed counter.
    const tickEl = actionResEl.querySelector("[data-mt-progress-tick]");
    const started = Date.now();
    const tickTimer = setInterval(() => {
      if (!tickEl || !tickEl.isConnected) return;
      const elapsed = (Date.now() - started) / 1000;
      tickEl.textContent = elapsed.toFixed(1) + "s";
    }, 100);

    // Walk through stages sequentially. The first N-1 stages auto-
    // complete on their timer; the LAST stage stays «active» until
    // the postJson() finishes and we explicitly mark it done. This
    // way the stage progress always converges on real server time.
    const stageEls = actionResEl.querySelectorAll(".mt-action-stage");
    if (stageEls.length === 0) return () => clearInterval(tickTimer);
    let currentIdx = 0;
    const setActive = (idx) => {
      stageEls.forEach((el, i) => {
        el.classList.toggle("is-active", i === idx);
        el.classList.toggle("is-done", i < idx);
      });
    };
    setActive(0);
    const stageTimers = [];
    for (let i = 0; i < stages.length - 1; i++) {
      const acc = stages.slice(0, i + 1).reduce((s, x) => s + (x.t || 600), 0);
      stageTimers.push(setTimeout(() => {
        currentIdx = Math.max(currentIdx, i + 1);
        setActive(currentIdx);
      }, acc));
    }

    // Returned cleanup ALSO marks the final stage done — called by
    // postJson() in the finally{} branch.
    return function finishProgress() {
      clearInterval(tickTimer);
      stageTimers.forEach(clearTimeout);
      stageEls.forEach((el) => {
        el.classList.add("is-done");
        el.classList.remove("is-active");
      });
    };
  }

  async function postJson(path, body) {
    const stopTick = showProgress(currentActionKind);
    try {
      const { res, body: env } = await api(path, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body || {}),
      });
      actionResEl && actionResEl.classList.remove("is-progress");
      // The API wraps results in a two-layer envelope:
      //   env.ok          — HTTP-level success (always true on 2xx)
      //   env.data.ok     — business-level result (router-side fail
      //                     surfaces here even when HTTP returned 200)
      //   env.data.error  — human-readable Arabic error from MT
      // The renderer needs the INNER ok so a router-side failure
      // doesn't render as a green «تم بنجاح» — which is what
      // happened for the «test 1» backup name (space rejected by
      // _sanitize_backup_name but UI still flashed success).
      const inner = (env && env.data) || {};
      const innerOk = !(inner && inner.ok === false);
      const finalOk = !!(res.ok && env && env.ok !== false && innerOk);
      // Promote inner.error to the top level so renderResultCard's
      // failBody() can pick it up without changing its lookup path.
      const surfaced = env || { status: res.status };
      if (!finalOk && inner && inner.error && !surfaced.error) {
        surfaced.error = inner.error;
      }
      writeOutput(surfaced, finalOk);
      return { res, env };
    } catch (err) {
      actionResEl && actionResEl.classList.remove("is-progress");
      writeOutput({ error: String(err && err.message || err) }, false);
      return { res: null, env: null };
    } finally {
      if (typeof stopTick === "function") stopTick();
    }
  }

  // ── Backup ──
  if (actionButtons.backup) {
    actionButtons.backup.addEventListener("click", () => {
      openForm("backup", `
        <label>اسم النسخة (اختياري — افتراضي backup-YYYYMMDD-HHMMSS)
          <input type="text" name="name" placeholder="weekly-1"
                 maxlength="64" data-mt-backup-name>
        </label>
        <div class="mt-action-row">
          <button type="submit">حفظ الآن</button>
          <button type="button" class="mt-cancel">إلغاء</button>
        </div>
      `);
      const submit = actionFormEl.querySelector("button[type=submit]");
      submit.addEventListener("click", async (e) => {
        e.preventDefault();
        submit.disabled = true;
        const name = actionFormEl.querySelector("[data-mt-backup-name]").value.trim();
        await postJson(
          "/mikrotik/" + CFG.routerId + "/system/backup/save",
          name ? { name } : {},
        );
        submit.disabled = false;
      });
    });
  }

  // ── Ping ──
  if (actionButtons.ping) {
    actionButtons.ping.addEventListener("click", () => {
      openForm("ping", `
        <label>الهدف
          <input type="text" name="target" placeholder="8.8.8.8"
                 data-mt-ping-target required>
        </label>
        <label>عدد الحزم (1-20)
          <input type="number" name="count" min="1" max="20" value="4"
                 data-mt-ping-count>
        </label>
        <div class="mt-action-row">
          <button type="submit">شغّل ping</button>
          <button type="button" class="mt-cancel">إلغاء</button>
        </div>
      `);
      const submit = actionFormEl.querySelector("button[type=submit]");
      submit.addEventListener("click", async (e) => {
        e.preventDefault();
        const target = actionFormEl.querySelector("[data-mt-ping-target]").value.trim();
        if (!target) { writeOutput({ error: "أدخل عنوان الهدف" }, false); return; }
        const countRaw = actionFormEl.querySelector("[data-mt-ping-count]").value;
        const count = Math.max(1, Math.min(20, parseInt(countRaw, 10) || 4));
        submit.disabled = true;
        await postJson(
          "/mikrotik/" + CFG.routerId + "/tools/ping",
          { target, count },
        );
        submit.disabled = false;
      });
    });
  }

  // ── Reboot (destructive) ──
  if (actionButtons.reboot) {
    actionButtons.reboot.addEventListener("click", () => {
      openForm("reboot", `
        <label>سبب (اختياري — يُسجَّل في audit)
          <input type="text" name="reason" placeholder="kernel panic" data-mt-reboot-reason>
        </label>
        <label class="mt-action-confirm">
          <input type="checkbox" data-mt-reboot-confirm>
          أؤكد إعادة تشغيل الراوتر — سيُقطع الاتصال لدقيقة
        </label>
        <div class="mt-action-row">
          <button type="submit" disabled>إعادة التشغيل</button>
          <button type="button" class="mt-cancel">إلغاء</button>
        </div>
      `);
      const cb = actionFormEl.querySelector("[data-mt-reboot-confirm]");
      const submit = actionFormEl.querySelector("button[type=submit]");
      cb.addEventListener("change", () => { submit.disabled = !cb.checked; });
      submit.addEventListener("click", async (e) => {
        e.preventDefault();
        if (!cb.checked) return; // belt + suspenders
        const reason = actionFormEl.querySelector("[data-mt-reboot-reason]").value.trim();
        submit.disabled = true;
        await postJson(
          "/mikrotik/" + CFG.routerId + "/system/reboot",
          { confirm: true, reason },
        );
      });
    });
  }

  // ── Identity set (destructive) ──
  if (actionButtons.identity) {
    actionButtons.identity.addEventListener("click", () => {
      openForm("identity", `
        <label>الاسم الجديد ([A-Za-z0-9._-] حتى 32 حرفًا)
          <input type="text" name="name" maxlength="32"
                 pattern="[A-Za-z0-9._\\-]{1,32}"
                 placeholder="main-gw" data-mt-identity-name required>
        </label>
        <label>سبب (اختياري)
          <input type="text" name="reason" placeholder="rename for clarity" data-mt-identity-reason>
        </label>
        <label class="mt-action-confirm">
          <input type="checkbox" data-mt-identity-confirm>
          أؤكد تغيير اسم الراوتر
        </label>
        <div class="mt-action-row">
          <button type="submit" disabled>تغيير الاسم</button>
          <button type="button" class="mt-cancel">إلغاء</button>
        </div>
      `);
      const cb = actionFormEl.querySelector("[data-mt-identity-confirm]");
      const submit = actionFormEl.querySelector("button[type=submit]");
      cb.addEventListener("change", () => { submit.disabled = !cb.checked; });
      submit.addEventListener("click", async (e) => {
        e.preventDefault();
        if (!cb.checked) return;
        const name = actionFormEl.querySelector("[data-mt-identity-name]").value.trim();
        if (!name) { writeOutput({ error: "أدخل الاسم الجديد" }, false); return; }
        const reason = actionFormEl.querySelector("[data-mt-identity-reason]").value.trim();
        submit.disabled = true;
        await postJson(
          "/mikrotik/" + CFG.routerId + "/system/identity/set",
          { confirm: true, name, reason },
        );
        submit.disabled = false;
      });
    });
  }

  // ── Traceroute (read-only) ──
  if (actionButtons.traceroute) {
    actionButtons.traceroute.addEventListener("click", () => {
      openForm("traceroute", `
        <label>الهدف
          <input type="text" name="target" placeholder="8.8.8.8 أو example.com"
                 data-mt-trace-target required>
        </label>
        <label>الحدّ الأقصى للقفزات (5-30)
          <input type="number" name="max-hops" min="5" max="30" value="15"
                 data-mt-trace-hops>
        </label>
        <div class="mt-action-row">
          <button type="submit">شغّل Traceroute</button>
          <button type="button" class="mt-cancel">إلغاء</button>
        </div>
      `);
      const submit = actionFormEl.querySelector("button[type=submit]");
      submit.addEventListener("click", async (e) => {
        e.preventDefault();
        const target = actionFormEl.querySelector("[data-mt-trace-target]").value.trim();
        if (!target) { writeOutput({ error: "أدخل عنوان الهدف" }, false); return; }
        const raw = actionFormEl.querySelector("[data-mt-trace-hops]").value;
        const maxHops = Math.max(5, Math.min(30, parseInt(raw, 10) || 15));
        submit.disabled = true;
        await postJson(
          "/mikrotik/" + CFG.routerId + "/tools/traceroute",
          { target, "max-hops": maxHops },
        );
        submit.disabled = false;
      });
    });
  }

  // DNS resolve action removed (2026-05). RouterOS 7's API rejects
  // /resolve with «unknown parameter name» across every shape we
  // tried (name=, server=, server-address=, positional). Operator
  // asked to drop the feature instead of more shape-chasing. The
  // backend /tools/dns-resolve route + renderer branch are kept as
  // dead code — easy to revive when a working API path lands.

  // ── DNS flush ──
  if (actionButtons["dns-flush"]) {
    actionButtons["dns-flush"].addEventListener("click", () => {
      openForm("dns-flush", `
        <p style="margin:0 0 12px;color:#475569;font-size:13px;line-height:1.6">
          سنُفرغ كاش DNS على الراوتر. هذا غير مدمّر —
          الراوتر سيستعلم عن الأسماء من جديد عند الطلب.
        </p>
        <div class="mt-action-row">
          <button type="submit">مسح الآن</button>
          <button type="button" class="mt-cancel">إلغاء</button>
        </div>
      `);
      const submit = actionFormEl.querySelector("button[type=submit]");
      submit.addEventListener("click", async (e) => {
        e.preventDefault();
        submit.disabled = true;
        await postJson(
          "/mikrotik/" + CFG.routerId + "/ip/dns/cache/flush",
          {},
        );
        submit.disabled = false;
      });
    });
  }

  // ── Clock sync (NTP) ──
  if (actionButtons["clock-sync"]) {
    actionButtons["clock-sync"].addEventListener("click", () => {
      openForm("clock-sync", `
        <p style="margin:0 0 12px;color:#475569;font-size:13px;line-height:1.6">
          إعادة مزامنة الوقت من خادم NTP. مفيد عند الإقلاع البارد
          أو لو ساعة الراوتر منحرفة.
        </p>
        <div class="mt-action-row">
          <button type="submit">مزامنة الآن</button>
          <button type="button" class="mt-cancel">إلغاء</button>
        </div>
      `);
      const submit = actionFormEl.querySelector("button[type=submit]");
      submit.addEventListener("click", async (e) => {
        e.preventDefault();
        submit.disabled = true;
        await postJson(
          "/mikrotik/" + CFG.routerId + "/system/ntp/sync",
          {},
        );
        submit.disabled = false;
      });
    });
  }

  // ── P2 — Interfaces tab ─────────────────────────────────────────
  //
  // Lazy load: first activation triggers a fetch. While the tab is
  // open we re-poll every INTERFACES_POLL_MS so byte counters + the
  // running-flag column stay live. Switching away clears the timer
  // — no point hammering the router for a panel the operator
  // isn't looking at.
  (function initInterfacesTab() {
    /* Faster poll so the live RX/TX speed columns feel close to
       real-time. 5 s gives a "alive" feel without hammering the
       router. */
    const INTERFACES_POLL_MS = 5_000;
    const card  = root.querySelector("[data-mt-interfaces-card]");
    if (!card) return;
    const msg   = card.querySelector("[data-mt-interfaces-msg]");
    const wrap  = card.querySelector("[data-mt-interfaces-wrap]");
    const rows  = card.querySelector("[data-mt-interfaces-rows]");
    const count = card.querySelector("[data-mt-interfaces-count]");
    const refreshBtn = card.querySelector("[data-mt-interfaces-refresh]");

    let timer = null;
    let inflight = false;
    /* Map of interface-name → { rxByte, txByte, t } from the previous
       poll. We diff the byte counters between polls and divide by
       the elapsed time to compute live bps. First poll has no prior
       sample, so speeds show "—" until the next tick. */
    const prevByName = new Map();

    function setMsg(text) {
      if (!msg) return;
      msg.textContent = text || "";
      msg.hidden = !text;
    }

    function bytesHumanLocal(v) {
      const n = parseFloat(v);
      if (!Number.isFinite(n)) return "—";
      if (n < 1024) return n.toFixed(0) + " B";
      const units = ["KB", "MB", "GB", "TB", "PB"];
      let val = n / 1024, i = 0;
      while (val >= 1024 && i < units.length - 1) { val /= 1024; i++; }
      return val.toFixed(val >= 100 ? 0 : 1) + " " + units[i];
    }

    /* Bits-per-second formatter for live speed.
       Inputs come in as bps (already × 8 from the caller). */
    function bpsHuman(bps) {
      if (!Number.isFinite(bps) || bps < 0) return "—";
      if (bps < 1) return "0";
      if (bps < 1_000) return bps.toFixed(0) + " bps";
      if (bps < 1_000_000) return (bps / 1_000).toFixed(bps < 10_000 ? 1 : 0) + " Kbps";
      if (bps < 1_000_000_000) return (bps / 1_000_000).toFixed(bps < 10_000_000 ? 2 : 1) + " Mbps";
      return (bps / 1_000_000_000).toFixed(2) + " Gbps";
    }

    /* The negotiated link rate from /interface/ethernet/print —
       collapse the verbose values RouterOS returns into the short
       label the operator wanted (10 / 100 / 1G / 10G). */
    function rateHuman(r) {
      const s = String(r || "").toLowerCase().trim();
      if (!s) return "—";
      if (s.includes("10gbps") || s.includes("10g"))  return "10 G";
      if (s.includes("2.5g"))                          return "2.5 G";
      if (s.includes("1gbps")  || s === "1000mbps")    return "1 G";
      if (s.includes("100mbps"))                       return "100 M";
      if (s.includes("10mbps"))                        return "10 M";
      // fall through with original value if RouterOS gave us
      // something unexpected
      return String(r);
    }

    function statusCell(row) {
      const disabled = String(row["disabled"]) === "true";
      const running  = String(row["running"])  === "true";
      if (disabled) {
        return ['<span class="mt-iface-state mt-iface-state--off">',
                'معطّلة</span>'].join("");
      }
      if (running) {
        return ['<span class="mt-iface-state mt-iface-state--up">',
                'متصلة</span>'].join("");
      }
      return ['<span class="mt-iface-state mt-iface-state--down">',
              'غير متصلة</span>'].join("");
    }

    function escapeText(v) {
      return String(v == null ? "" : v).replace(/[<>&"]/g, ch => ({
        "<": "&lt;", ">": "&gt;", "&": "&amp;", '"': "&quot;",
      }[ch]));
    }

    function renderRows(list) {
      const now = Date.now();
      const html = list.map(r => {
        const rxErr = parseFloat(r["rx-error"]) || 0;
        const txErr = parseFloat(r["tx-error"]) || 0;
        const errs  = rxErr + txErr;
        const errCell = errs > 0
          ? '<span class="mt-iface-errors">' + errs + '</span>'
          : '<span class="mt-iface-errors mt-iface-errors--ok">0</span>';

        // Live speed via byte-counter diff against the previous poll.
        const rxByte = parseFloat(r["rx-byte"]) || 0;
        const txByte = parseFloat(r["tx-byte"]) || 0;
        const prev = prevByName.get(r.name);
        let rxBps = null, txBps = null;
        if (prev && now > prev.t) {
          const dt = (now - prev.t) / 1000;
          if (dt > 0 && dt < 60) { // 60 s sanity cap
            rxBps = Math.max(0, (rxByte - prev.rxByte) * 8 / dt);
            txBps = Math.max(0, (txByte - prev.txByte) * 8 / dt);
          }
        }
        prevByName.set(r.name, { rxByte, txByte, t: now });

        return [
          '<tr data-mt-iface-row="', escapeText(r.name || ""), '">',
          '<td class="mt-iface-name">', escapeText(r.name || "—"), '</td>',
          '<td>', escapeText(r.type || "—"), '</td>',
          '<td class="mt-iface-rate">', escapeText(rateHuman(r.rate)), '</td>',
          '<td class="mt-iface-mac">', escapeText(r["mac-address"] || "—"), '</td>',
          '<td>', escapeText(r.mtu || "—"), '</td>',
          '<td>', statusCell(r), '</td>',
          '<td class="mt-iface-bps mt-iface-bps--rx" dir="ltr">',
          rxBps == null ? '—' : bpsHuman(rxBps), '</td>',
          '<td class="mt-iface-bps mt-iface-bps--tx" dir="ltr">',
          txBps == null ? '—' : bpsHuman(txBps), '</td>',
          '<td class="mt-iface-total" dir="ltr">',
          bytesHumanLocal(rxByte), ' / ', bytesHumanLocal(txByte), '</td>',
          '<td>', errCell, '</td>',
          '</tr>',
        ].join("");
      }).join("");
      rows.innerHTML = html;
    }

    async function load() {
      if (inflight) return;
      inflight = true;
      try {
        const { res, body } = await api(
          "/mikrotik/" + CFG.routerId + "/interfaces");
        if (!res.ok || !body || body.ok === false) {
          setMsg("تعذّر التحميل (HTTP " + res.status + ").");
          wrap.hidden = true;
          if (count) count.textContent = "—";
          return;
        }
        const env = body.data || {};
        if (env.ok === false) {
          setMsg(env.error
                 || "الراوتر لم يرد على /interface/print.");
          wrap.hidden = true;
          if (count) count.textContent = "—";
          return;
        }
        const list = Array.isArray(env.data) ? env.data : [];
        if (!list.length) {
          setMsg("لا توجد واجهات معروضة.");
          wrap.hidden = true;
          if (count) count.textContent = "0";
          return;
        }
        renderRows(list);
        if (count) count.textContent = String(list.length);
        setMsg("");
        wrap.hidden = false;
      } catch (e) {
        setMsg("خطأ في الشبكة: " + String(e));
        wrap.hidden = true;
      } finally {
        inflight = false;
      }
    }

    function start() {
      load();
      if (timer == null) {
        timer = setInterval(load, INTERFACES_POLL_MS);
      }
    }
    function stop() {
      if (timer != null) { clearInterval(timer); timer = null; }
    }

    root.addEventListener("mt:tab-change", (e) => {
      if (e.detail && e.detail.slug === "interfaces") start();
      else stop();
    });
    if (refreshBtn) {
      refreshBtn.addEventListener("click", () => load());
    }
    // If the page loads with #tab-interfaces already in the URL the
    // tab module fires the event before this listener attaches; cover
    // that case by checking the current hash on init.
    if ((location.hash || "").replace(/^#/, "") === "tab-interfaces") {
      start();
    }
  })();

  // ── P3 — Table-backed tabs (IPs + routes) ──────────────────────
  //
  // Both panels are read-only RouterOS lists rendered as tables.
  // The lifecycle (lazy-load on tab activation + interval refresh
  // while open + cleanup on tab change) is identical to P2, so
  // factor it into a small spec-driven helper instead of two
  // near-duplicate modules.
  function escapeText(v) {
    return String(v == null ? "" : v).replace(/[<>&"]/g, ch => ({
      "<": "&lt;", ">": "&gt;", "&": "&amp;", '"': "&quot;",
    }[ch]));
  }

  function initTableTab(spec) {
    const card = root.querySelector(spec.cardSel);
    if (!card) return;
    const msg   = card.querySelector(spec.msgSel);
    const wrap  = card.querySelector(spec.wrapSel);
    const rows  = card.querySelector(spec.rowsSel);
    const count = card.querySelector(spec.countSel);
    const refreshBtn = card.querySelector(spec.refreshSel);

    let timer = null;
    let inflight = false;

    function setMsg(text) {
      if (!msg) return;
      msg.textContent = text || "";
      msg.hidden = !text;
    }

    async function load() {
      if (inflight) return;
      inflight = true;
      try {
        const { res, body } = await api(
          "/mikrotik/" + CFG.routerId + spec.path);
        if (!res.ok || !body || body.ok === false) {
          setMsg("تعذّر التحميل (HTTP " + res.status + ").");
          wrap.hidden = true;
          if (count) count.textContent = "—";
          return;
        }
        const env = body.data || {};
        if (env.ok === false) {
          setMsg(env.error || spec.errorFallback);
          wrap.hidden = true;
          if (count) count.textContent = "—";
          return;
        }
        let list = Array.isArray(env.data) ? env.data : [];
        // Optional per-row predicate (e.g. card vs user split on the
        // same /ip/hotspot/active feed). Skipped if not supplied.
        if (typeof spec.filter === "function") {
          list = list.filter(spec.filter);
        }
        if (!list.length) {
          setMsg(spec.emptyMsg);
          wrap.hidden = true;
          if (count) count.textContent = "0";
          return;
        }
        rows.innerHTML = list.map(spec.row).join("");
        if (count) count.textContent = String(list.length);
        setMsg("");
        wrap.hidden = false;
      } catch (e) {
        setMsg("خطأ في الشبكة: " + String(e));
        wrap.hidden = true;
      } finally {
        inflight = false;
      }
    }

    function start() {
      load();
      if (timer == null) {
        timer = setInterval(load, spec.pollMs);
      }
    }
    function stop() {
      if (timer != null) { clearInterval(timer); timer = null; }
    }

    root.addEventListener("mt:tab-change", (e) => {
      if (e.detail && e.detail.slug === spec.slug) start();
      else stop();
    });
    if (refreshBtn) {
      refreshBtn.addEventListener("click", () => load());
    }
    if ((location.hash || "").replace(/^#/, "") === "tab-" + spec.slug) {
      start();
    }
  }

  // ─── P3.A — IP addresses ──────────────────────────────────────
  initTableTab({
    slug: "ips",
    path: "/ip/addresses",
    pollMs: 30_000,
    cardSel: "[data-mt-ips-card]",
    msgSel: "[data-mt-ips-msg]",
    wrapSel: "[data-mt-ips-wrap]",
    rowsSel: "[data-mt-ips-rows]",
    countSel: "[data-mt-ips-count]",
    refreshSel: "[data-mt-ips-refresh]",
    emptyMsg: "لا توجد عناوين IP على هذا الراوتر.",
    errorFallback: "الراوتر لم يرد على /ip/address/print.",
    row: function (r) {
      const disabled = String(r["disabled"]) === "true";
      const dyn      = String(r["dynamic"])  === "true";
      const stateHtml = disabled
        ? '<span class="mt-iface-state mt-iface-state--off">معطّل</span>'
        : (dyn
            ? '<span class="mt-iface-state mt-iface-state--down">ديناميكي</span>'
            : '<span class="mt-iface-state mt-iface-state--up">ثابت</span>');
      return [
        '<tr>',
        '<td class="mt-iface-name">', escapeText(r.address || "—"), '</td>',
        '<td class="mt-iface-mac">', escapeText(r.network || "—"), '</td>',
        '<td>', escapeText(r.interface || "—"), '</td>',
        '<td>', stateHtml, '</td>',
        '<td>', escapeText(r.comment || ""), '</td>',
        '</tr>',
      ].join("");
    },
  });

  // ─── P7 — Diagnostics (risk-signal scan) ──────────────────────
  //
  // Hits /api/v1/.../health, which on the backend re-uses the
  // cached K4 readers — so polling here doesn't cost extra
  // RouterOS calls. Each signal renders as a severity-tinted row
  // with an expandable evidence block.
  (function initHealthTab() {
    const POLL_MS = 30_000;
    const card = root.querySelector("[data-mt-health-card]");
    if (!card) return;
    const msg = card.querySelector("[data-mt-health-msg]");
    const list = card.querySelector("[data-mt-health-list]");
    const refresh = card.querySelector("[data-mt-health-refresh]");
    const critEl = card.querySelector("[data-mt-health-summary-critical]");
    const warnEl = card.querySelector("[data-mt-health-summary-warning]");
    const okEl   = card.querySelector("[data-mt-health-summary-ok]");

    let timer = null;
    let inflight = false;

    function setMsg(text) {
      if (!msg) return;
      msg.textContent = text || "";
      msg.hidden = !text;
    }

    function escapeText(v) {
      return String(v == null ? "" : v).replace(/[<>&"]/g, ch => ({
        "<": "&lt;", ">": "&gt;", "&": "&amp;", '"': "&quot;",
      }[ch]));
    }

    function evidenceHtml(ev) {
      if (!Array.isArray(ev) || !ev.length) return "";
      const pretty = JSON.stringify(ev, null, 2);
      return ['<details class="mt-health-evidence">',
              '<summary>عرض الدليل (', String(ev.length), ')</summary>',
              '<pre>', escapeText(pretty), '</pre>',
              '</details>'].join("");
    }

    function severityChip(sev) {
      if (sev === "critical")
        return '<span class="mt-iface-state mt-iface-state--off">حرجة</span>';
      if (sev === "warning")
        return '<span class="mt-iface-state mt-iface-state--down">تحذير</span>';
      return '<span class="mt-iface-state mt-iface-state--up">سليمة</span>';
    }

    function render(report) {
      const signals = (report && report.signals) || [];
      const summary = (report && report.summary) || {};
      if (critEl) critEl.textContent = (summary.critical || 0) + " حرجة";
      if (warnEl) warnEl.textContent = (summary.warning  || 0) + " تحذير";
      if (okEl)   okEl.textContent   = (summary.ok       || 0) + " سليمة";

      if (!signals.length) {
        list.innerHTML = "";
        setMsg("لا توجد إشارات للفحص.");
        return;
      }
      list.innerHTML = signals.map(s => {
        const klass = "mt-health-row mt-health-row--" + (s.severity || "ok");
        return [
          '<li class="', klass, '" data-mt-health-kind="',
          escapeText(s.kind || ""), '" data-mt-health-severity="',
          escapeText(s.severity || ""), '">',
          '<div class="mt-health-row-head">',
            severityChip(s.severity),
            '<span class="mt-health-row-msg">',
              escapeText(s.message || s.kind || "—"),
            '</span>',
          '</div>',
          evidenceHtml(s.evidence),
          '</li>',
        ].join("");
      }).join("");
      setMsg("");
    }

    async function load() {
      if (inflight) return;
      inflight = true;
      try {
        const { res, body } = await api(
          "/mikrotik/" + CFG.routerId + "/health");
        if (!res.ok || !body || body.ok === false) {
          setMsg("تعذّر التحميل (HTTP " + res.status + ").");
          return;
        }
        const env = body.data || {};
        if (env.ok === false) {
          setMsg(env.error || "فشل فحص الإشارات.");
          return;
        }
        render(env);
      } catch (e) {
        setMsg("خطأ في الشبكة: " + String(e));
      } finally {
        inflight = false;
      }
    }

    function start() { load(); if (timer == null) timer = setInterval(load, POLL_MS); }
    function stop()  { if (timer != null) { clearInterval(timer); timer = null; } }

    root.addEventListener("mt:tab-change", (e) => {
      if (e.detail && e.detail.slug === "diagnostics") start();
      else stop();
    });
    if (refresh) refresh.addEventListener("click", () => load());
    if ((location.hash || "").replace(/^#/, "") === "tab-diagnostics") start();
  })();

  // ─── P6 — Sessions (hotspot cards + hotspot users + ppp) ─────
  //
  // Operator asked for the Hotspot session list to be split into
  // two stacked tables — one for CARDS (timed/credit-style logins,
  // usually purely-numeric or short alphanumeric usernames generated
  // by the cards engine) and one for REGULAR USERS. We use a simple
  // frontend heuristic to bucket each /ip/hotspot/active row:
  //   - purely-digit username           → card
  //   - 6-12 char alphanumeric, mixed   → card
  //   - everything else                 → user
  // The card filter is permissive — false positives bucket as cards
  // (acceptable: the operator still sees them, just under a slightly
  // wrong heading). A future commit can swap this for a backend
  // lookup of cards_repo.get_card_by_username.
  function isCardUsername(u) {
    if (!u) return false;
    if (/^\d+$/.test(u)) return true;                // all digits
    if (/^[A-Za-z0-9]{4,12}$/.test(u) && /\d/.test(u)
        && !/^\w+@\w+\./.test(u)) {
      return true;                                    // short alphanum w/ digits
    }
    return false;
  }

  // Disconnect button + row builder shared by both Hotspot tables.
  // The action posts to /mikrotik/<id>/hotspot/disconnect — the row
  // is identified by either RouterOS `.id` or `user` (whichever the
  // active row carries). Optimistic UI: row dims while in flight,
  // gets reloaded on success.
  function hotspotSessionRow(r) {
    const id = String(r[".id"] || "");
    const user = String(r.user || "");
    return [
      '<tr data-mt-session-row',
      id   ? ' data-mt-session-id="'  + escapeText(id)   + '"' : '',
      user ? ' data-mt-session-user="' + escapeText(user) + '"' : '',
      '>',
      '<td class="mt-iface-name">', escapeText(user || "—"), '</td>',
      '<td class="mt-iface-mac">', escapeText(r.address || "—"), '</td>',
      '<td class="mt-iface-mac">', escapeText(r["mac-address"] || "—"), '</td>',
      '<td>', escapeText(r.uptime || "—"), '</td>',
      '<td>', escapeText(r["bytes-in"] || "—"), '</td>',
      '<td>', escapeText(r["bytes-out"] || "—"), '</td>',
      '<td>', escapeText(r.comment || ""), '</td>',
      '<td><button type="button" class="mt-row-disconnect"',
      ' data-mt-disconnect="hotspot"',
      ' title="قطع الاتصال">',
      '<i class="fa-solid fa-link-slash"></i> قطع',
      '</button></td>',
      '</tr>',
    ].join("");
  }

  // ── Hotspot CARDS ──
  initTableTab({
    slug: "sessions",
    path: "/hotspot/active",
    pollMs: 10_000,
    cardSel: "[data-mt-hotspot-cards-card]",
    msgSel: "[data-mt-hotspot-cards-msg]",
    wrapSel: "[data-mt-hotspot-cards-wrap]",
    rowsSel: "[data-mt-hotspot-cards-rows]",
    countSel: "[data-mt-hotspot-cards-count]",
    refreshSel: "[data-mt-hotspot-cards-refresh]",
    emptyMsg: "لا توجد جلسات كروت نشطة الآن.",
    errorFallback: "الراوتر لم يرد على /ip/hotspot/active.",
    // Custom filter: bucket only card-pattern usernames into this
    // table. The Users table below filters the inverse so each
    // row appears in exactly one table.
    filter: function (r) { return isCardUsername(r.user); },
    row: hotspotSessionRow,
  });

  // ── Hotspot USERS ──
  initTableTab({
    slug: "sessions",
    path: "/hotspot/active",
    pollMs: 10_000,
    cardSel: "[data-mt-hotspot-users-card]",
    msgSel: "[data-mt-hotspot-users-msg]",
    wrapSel: "[data-mt-hotspot-users-wrap]",
    rowsSel: "[data-mt-hotspot-users-rows]",
    countSel: "[data-mt-hotspot-users-count]",
    refreshSel: "[data-mt-hotspot-users-refresh]",
    emptyMsg: "لا توجد جلسات يوزرات نشطة الآن.",
    errorFallback: "الراوتر لم يرد على /ip/hotspot/active.",
    filter: function (r) { return !isCardUsername(r.user); },
    row: hotspotSessionRow,
  });

  // ── PPP sessions (single table, with disconnect) ──
  initTableTab({
    slug: "sessions",
    path: "/ppp/active",
    pollMs: 10_000,
    cardSel: "[data-mt-ppp-card]",
    msgSel: "[data-mt-ppp-sessions-msg]",
    wrapSel: "[data-mt-ppp-sessions-wrap]",
    rowsSel: "[data-mt-ppp-sessions-rows]",
    countSel: "[data-mt-ppp-sessions-count]",
    refreshSel: "[data-mt-ppp-sessions-refresh]",
    emptyMsg: "لا توجد جلسات PPP نشطة الآن.",
    errorFallback: "الراوتر لم يرد على /ppp/active.",
    row: function (r) {
      const id   = String(r[".id"] || "");
      const name = String(r.name  || "");
      return [
        '<tr data-mt-session-row',
        id   ? ' data-mt-session-id="'   + escapeText(id)   + '"' : '',
        name ? ' data-mt-session-user="' + escapeText(name) + '"' : '',
        '>',
        '<td class="mt-iface-name">', escapeText(name || "—"), '</td>',
        '<td>', escapeText(r.service || "—"), '</td>',
        '<td class="mt-iface-mac">', escapeText(r.address || "—"), '</td>',
        '<td class="mt-iface-mac">', escapeText(r["caller-id"] || "—"), '</td>',
        '<td>', escapeText(r.uptime || "—"), '</td>',
        '<td><button type="button" class="mt-row-disconnect"',
        ' data-mt-disconnect="ppp"',
        ' title="قطع الاتصال">',
        '<i class="fa-solid fa-link-slash"></i> قطع',
        '</button></td>',
        '</tr>',
      ].join("");
    },
  });

  // ── Disconnect wiring (event delegation, one listener for all
  //    three tables) ──
  // The router's actual disconnect endpoint depends on the session
  // kind (`hotspot` vs `ppp`). Both endpoints expect either the
  // RouterOS .id (preferred — exact match) or the username
  // (fallback — kicks all sessions for that user).
  document.addEventListener("click", async (e) => {
    const btn = e.target.closest("[data-mt-disconnect]");
    if (!btn) return;
    const kind = btn.dataset.mtDisconnect;
    const tr   = btn.closest("[data-mt-session-row]");
    if (!tr) return;
    const id    = tr.dataset.mtSessionId   || "";
    const user  = tr.dataset.mtSessionUser || "";
    if (!id && !user) return;
    // Inline confirm — no modal needed for read+kick. Keeps the row
    // visible the whole time.
    const label = user || id;
    if (!window.confirm(`قطع اتصال «${label}»؟`)) return;
    btn.disabled = true;
    tr.style.opacity = "0.5";
    const origHtml = btn.innerHTML;
    btn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> ...';
    try {
      const path = kind === "ppp" ? "/ppp/disconnect" : "/hotspot/disconnect";
      const body = id ? { id } : { user };
      await api(
        "/mikrotik/" + CFG.routerId + path,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        },
      );
      // Optimistically remove the row; the next poll will confirm.
      tr.style.transition = "opacity .25s, transform .25s";
      tr.style.transform = "translateX(40px)";
      setTimeout(() => tr.remove(), 250);
    } catch (err) {
      btn.disabled = false;
      tr.style.opacity = "1";
      btn.innerHTML = origHtml;
      alert("تعذّر قطع الاتصال: " + (err && err.message || err));
    }
  });

  // ─── P5 — Logs viewer ─────────────────────────────────────────
  //
  // Tails /api/v1/.../log with a topic filter. RouterOS stores
  // topics as a CSV string on each row, and the backend just
  // substring-matches whatever ?topics= we send. Multi-select on
  // the chip strip is therefore an OR-join: any chip's slug present
  // in the row keeps it.
  (function initLogsTab() {
    const POLL_MS = 5_000;
    const LIMIT   = 250;
    const card  = root.querySelector("[data-mt-logs-card]");
    if (!card) return;
    const msg     = card.querySelector("[data-mt-logs-msg]");
    const output  = card.querySelector("[data-mt-logs-output]");
    const count   = card.querySelector("[data-mt-logs-count]");
    const refresh = card.querySelector("[data-mt-logs-refresh]");
    const pauseCb = card.querySelector("[data-mt-logs-pause]");
    const topicsBar = card.querySelector("[data-mt-logs-topics]");
    const topicBtns = Array.from(
      topicsBar.querySelectorAll("[data-mt-logs-topic]"));

    // Severity legend pills (real counts only — derived from the same
    // topic-substring logic used to colour rows). All optional: if the
    // template ever drops the legend block these stay null and the
    // code keeps working.
    const statCrit = card.querySelector("[data-mt-logs-stat-critical]");
    const statErr  = card.querySelector("[data-mt-logs-stat-error]");
    const statWarn = card.querySelector("[data-mt-logs-stat-warn]");
    const statInfo = card.querySelector("[data-mt-logs-stat-info]");
    const updated  = card.querySelector("[data-mt-logs-updated]");

    let timer = null;
    let inflight = false;
    // The empty-string slug is "show all". An empty `selected` set
    // is treated the same way.
    const selected = new Set();

    function setMsg(text) {
      if (!msg) return;
      msg.textContent = text || "";
      msg.hidden = !text;
    }

    function activeTopicsCsv() {
      const real = Array.from(selected).filter(s => s !== "");
      return real.join(",");
    }

    function setActiveChips() {
      topicBtns.forEach(b => {
        const slug = b.dataset.mtLogsTopic;
        const on = (slug === "" && selected.size === 0)
                || selected.has(slug);
        b.classList.toggle("is-active", on);
      });
    }

    function rowLine(r) {
      const time   = r.time || r["time"] || "";
      const topics = r.topics || "";
      const text   = r.message || "";
      return time + "  [" + topics + "]  " + text;
    }

    function severityClass(topics) {
      const t = (topics || "").toLowerCase();
      if (t.includes("critical")) return "mt-logs-line--critical";
      if (t.includes("error"))    return "mt-logs-line--error";
      if (t.includes("warning"))  return "mt-logs-line--warn";
      return "";
    }

    function updateSeverityStats(list) {
      let crit = 0, err = 0, warn = 0, info = 0;
      for (const r of (list || [])) {
        const t = (r && r.topics ? r.topics : "").toLowerCase();
        if      (t.includes("critical")) crit++;
        else if (t.includes("error"))    err++;
        else if (t.includes("warning"))  warn++;
        else                              info++;
      }
      if (statCrit) statCrit.textContent = String(crit);
      if (statErr)  statErr.textContent  = String(err);
      if (statWarn) statWarn.textContent = String(warn);
      if (statInfo) statInfo.textContent = String(info);
    }

    function setUpdatedNow() {
      if (!updated) return;
      const d = new Date();
      const pad = (n) => (n < 10 ? "0" + n : String(n));
      updated.textContent = pad(d.getHours()) + ":"
                          + pad(d.getMinutes()) + ":"
                          + pad(d.getSeconds());
    }

    function escapeText(v) {
      return String(v == null ? "" : v).replace(/[<>&"]/g, ch => ({
        "<": "&lt;", ">": "&gt;", "&": "&amp;", '"': "&quot;",
      }[ch]));
    }

    async function load() {
      if (inflight) return;
      if (pauseCb && pauseCb.checked) return;
      inflight = true;
      try {
        const topicsCsv = activeTopicsCsv();
        const qs = new URLSearchParams({ limit: String(LIMIT) });
        if (topicsCsv) qs.set("topics", topicsCsv);
        const { res, body } = await api(
          "/mikrotik/" + CFG.routerId + "/log?" + qs.toString());
        if (!res.ok || !body || body.ok === false) {
          setMsg("تعذّر التحميل (HTTP " + res.status + ").");
          return;
        }
        const env = body.data || {};
        if (env.ok === false) {
          setMsg(env.error || "الراوتر لم يرد على /log/print.");
          return;
        }
        const list = Array.isArray(env.data) ? env.data : [];
        if (!list.length) {
          setMsg("لا توجد سطور تطابق الفلتر الحالي.");
          output.innerHTML = "";
          if (count) count.textContent = "0";
          updateSeverityStats([]);
          setUpdatedNow();
          return;
        }
        const html = list.map(r => {
          const klass = severityClass(r.topics);
          const cls = klass ? ' class="mt-logs-line ' + klass + '"'
                            : ' class="mt-logs-line"';
          return '<div' + cls + '>' + escapeText(rowLine(r)) + '</div>';
        }).join("");
        output.innerHTML = html;
        if (count) count.textContent = String(list.length);
        updateSeverityStats(list);
        setUpdatedNow();
        setMsg("");
        // Auto-scroll to bottom (newest entries).
        output.scrollTop = output.scrollHeight;
      } catch (e) {
        setMsg("خطأ في الشبكة: " + String(e));
      } finally {
        inflight = false;
      }
    }

    function start() { load(); if (timer == null) timer = setInterval(load, POLL_MS); }
    function stop()  { if (timer != null) { clearInterval(timer); timer = null; } }

    root.addEventListener("mt:tab-change", (e) => {
      if (e.detail && e.detail.slug === "logs") start();
      else stop();
    });
    if (refresh) refresh.addEventListener("click", () => load());
    topicBtns.forEach(b => {
      b.addEventListener("click", () => {
        const slug = b.dataset.mtLogsTopic;
        if (slug === "") {
          selected.clear();
        } else {
          if (selected.has(slug)) selected.delete(slug);
          else selected.add(slug);
        }
        setActiveChips();
        load();
      });
    });
    if ((location.hash || "").replace(/^#/, "") === "tab-logs") start();
  })();

  // ─── P4 — Neighbors (MNDP/CDP/LLDP) ───────────────────────────
  initTableTab({
    slug: "neighbors",
    path: "/neighbors",
    pollMs: 30_000,
    cardSel: "[data-mt-neighbors-card]",
    msgSel: "[data-mt-neighbors-msg]",
    wrapSel: "[data-mt-neighbors-wrap]",
    rowsSel: "[data-mt-neighbors-rows]",
    countSel: "[data-mt-neighbors-count]",
    refreshSel: "[data-mt-neighbors-refresh]",
    emptyMsg: ("لم يكتشف الراوتر أيّ جيران بعد. "
               + "تأكد من تفعيل MNDP/CDP/LLDP على الواجهة."),
    errorFallback: "الراوتر لم يرد على /ip/neighbor/print.",
    row: function (r) {
      // RouterOS exposes a `discovered-by` field listing the
      // protocols that saw this neighbor. Not all builds carry it,
      // so fall back to a sensible default.
      const identity = r.identity || r["system-description"]
                       || r["mac-address"] || "—";
      return [
        '<tr>',
        '<td class="mt-iface-name">', escapeText(identity), '</td>',
        '<td class="mt-iface-mac">', escapeText(r["mac-address"] || "—"), '</td>',
        '<td>', escapeText(r.address || r["address4"] || "—"), '</td>',
        '<td>', escapeText(r.interface || "—"), '</td>',
        '<td>', escapeText(r.platform || "—"), '</td>',
        '<td>', escapeText(r.board || "—"), '</td>',
        '<td>', escapeText(r.version || "—"), '</td>',
        '</tr>',
      ].join("");
    },
  });

  // ─── P3.B — Routes ────────────────────────────────────────────
  initTableTab({
    slug: "routes",
    path: "/routes",
    pollMs: 30_000,
    cardSel: "[data-mt-routes-card]",
    msgSel: "[data-mt-routes-msg]",
    wrapSel: "[data-mt-routes-wrap]",
    rowsSel: "[data-mt-routes-rows]",
    countSel: "[data-mt-routes-count]",
    refreshSel: "[data-mt-routes-refresh]",
    emptyMsg: "لا توجد مسارات على هذا الراوتر.",
    errorFallback: "الراوتر لم يرد على /ip/route/print.",
    row: function (r) {
      const active   = String(r["active"])   === "true";
      const disabled = String(r["disabled"]) === "true";
      const stateHtml = disabled
        ? '<span class="mt-iface-state mt-iface-state--off">معطّل</span>'
        : (active
            ? '<span class="mt-iface-state mt-iface-state--up">نشط</span>'
            : '<span class="mt-iface-state mt-iface-state--down">خامل</span>');
      // RouterOS exposes a "static / dynamic / connect / dhcp / bgp"
      // family on every route — surface it so the operator can tell
      // a hand-built static route from one a DHCP lease installed.
      let kind = "—";
      if (String(r["static"])  === "true") kind = "static";
      else if (String(r["dynamic"]) === "true") kind = "dynamic";
      else if (String(r["connect"]) === "true") kind = "connected";
      else if (String(r["dhcp"])    === "true") kind = "dhcp";
      else if (String(r["bgp"])     === "true") kind = "bgp";
      else if (String(r["ospf"])    === "true") kind = "ospf";
      return [
        '<tr>',
        '<td class="mt-iface-name">', escapeText(r["dst-address"] || "—"), '</td>',
        '<td class="mt-iface-mac">', escapeText(r.gateway || "—"), '</td>',
        '<td>', escapeText(r.distance || "—"), '</td>',
        '<td>', stateHtml, '</td>',
        '<td>', escapeText(kind), '</td>',
        '<td>', escapeText(r.comment || ""), '</td>',
        '</tr>',
      ].join("");
    },
  });
})();
