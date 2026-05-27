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
  function setKpi(kind, value, sub) {
    const card = root.querySelector(`[data-mt-kpi="${kind}"]`);
    if (!card) return;
    const valueEl = card.querySelector("[data-mt-kpi-value]");
    const subEl = card.querySelector("[data-mt-kpi-sub]");
    if (valueEl) valueEl.textContent = value != null ? value : "—";
    if (subEl && sub != null) subEl.textContent = sub;
  }

  function renderOverview(payload) {
    const sections = (payload && payload.sections) || {};
    const resource = (sections.resource && sections.resource.data) || [];
    const health   = (sections.health   && sections.health.data)   || [];
    const router_b = (sections.routerboard && sections.routerboard.data) || [];

    const resourceRow = resource[0] || {};
    const healthRow   = health[0]   || {};
    const boardRow    = router_b[0] || {};

    setKpi("uptime", resourceRow["uptime"] || "—",
           resourceRow["build-time"] ? "بُني " + resourceRow["build-time"] : null);

    const cpu = resourceRow["cpu-load"];
    setKpi("cpu", cpu != null ? cpu + "%" : "—",
           resourceRow["cpu"] ? resourceRow["cpu"] : null);

    const free = bytesHuman(resourceRow["free-memory"]);
    const total = bytesHuman(resourceRow["total-memory"]);
    setKpi("memory",
           (free && total) ? (free + " / " + total) : "—",
           "متاح / إجمالي");

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
    "dns-resolve": root.querySelector("[data-mt-action-dns-resolve]"),
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
    if (kind === "ping") {
      if (!ok) return head("فشل اختبار Ping", "/tools/ping") + failBody();
      const replies = Array.isArray(data.replies) ? data.replies : [];
      const summary = data.summary || {};
      const target = data.target || data.host || "";
      let tableHtml = "";
      if (replies.length) {
        tableHtml = `
          <table>
            <thead>
              <tr><th>#</th><th>السعة</th><th>TTL</th><th>الزمن</th><th>الحالة</th></tr>
            </thead>
            <tbody>
              ${replies.map((r, i) => `
                <tr>
                  <td>${i + 1}</td>
                  <td>${safeHtml(r.size != null ? r.size + " B" : "—")}</td>
                  <td>${safeHtml(r.ttl != null ? r.ttl : "—")}</td>
                  <td>${safeHtml(r.time != null ? r.time : (r["avg-rtt"] || "—"))}</td>
                  <td>${r.status === "ok" || r.received
                    ? '<span style="color:#10B981">✓ وصل</span>'
                    : '<span style="color:#DC2626">✗ ضاع</span>'}</td>
                </tr>
              `).join("")}
            </tbody>
          </table>
        `;
      }
      const sent   = summary.sent     != null ? summary.sent     : replies.length;
      const recv   = summary.received != null ? summary.received : replies.filter(r => r.status === "ok" || r.received).length;
      const loss   = sent > 0 ? Math.round(((sent - recv) / sent) * 100) : 0;
      const avgRtt = summary["avg-rtt"] || summary.avg_rtt || "—";
      return head(`Ping إلى ${target || "—"}`, "/tools/ping") + body(`
        ${tableHtml}
        <div class="mt-action-result-summary">
          مُرسَل: <strong>${sent}</strong> ·
          مُستلَم: <strong>${recv}</strong> ·
          فاقد: <strong>${loss}%</strong> ·
          متوسط زمن الذهاب-والإياب: <strong>${safeHtml(avgRtt)}</strong>
        </div>
      `);
    }

    // ── TRACEROUTE ────────────────────────────────────────
    if (kind === "traceroute") {
      if (!ok) return head("فشل Traceroute", "/tools/traceroute") + failBody();
      const hops = Array.isArray(data.hops) ? data.hops : [];
      const target = data.target || data.host || "";
      const rows = hops.map((h, i) => `
        <tr>
          <td>${i + 1}</td>
          <td>${safeHtml(h.address || h.host || "*")}</td>
          <td>${safeHtml(h.rtt || h.time || "—")}</td>
          <td>${safeHtml(h.loss != null ? h.loss + "%" : "—")}</td>
        </tr>`).join("");
      return head(`Traceroute إلى ${target || "—"}`, "/tools/traceroute") + body(`
        <table>
          <thead><tr><th>القفزة</th><th>العنوان</th><th>RTT</th><th>الفقد</th></tr></thead>
          <tbody>${rows || '<tr><td colspan="4">لا توجد قفزات</td></tr>'}</tbody>
        </table>
        <div class="mt-action-result-summary">
          عدد القفزات: <strong>${hops.length}</strong>
        </div>
      `);
    }

    // ── DNS RESOLVE ───────────────────────────────────────
    if (kind === "dns-resolve") {
      if (!ok) return head("فشل حلّ النطاق", "/ip/dns/cache/lookup") + failBody();
      const name = data.name || data.host || data.query || "";
      const addrs = data.addresses || data.address || data.ips || [];
      const list = (Array.isArray(addrs) ? addrs : [addrs])
        .filter(Boolean)
        .map(a => `<dd>${safeHtml(a)}</dd>`).join("");
      return head(`نتيجة الحلّ — ${name}`, "DNS") + body(`
        <dl class="mt-kv">
          <dt>النطاق</dt><dd>${safeHtml(name)}</dd>
          <dt>العناوين</dt>
          ${list ? `<div>${list}</div>` : '<dd>— (لم يُحَلّ)</dd>'}
        </dl>
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

  async function postJson(path, body) {
    const { res, body: env } = await api(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {}),
    });
    writeOutput(env || { status: res.status }, res.ok && env && env.ok !== false);
    return { res, env };
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

  // ── DNS resolve (read-only) ──
  if (actionButtons["dns-resolve"]) {
    actionButtons["dns-resolve"].addEventListener("click", () => {
      openForm("dns-resolve", `
        <label>اسم النطاق
          <input type="text" name="name" placeholder="example.com"
                 data-mt-dns-name required>
        </label>
        <div class="mt-action-row">
          <button type="submit">حلّ الاسم</button>
          <button type="button" class="mt-cancel">إلغاء</button>
        </div>
      `);
      const submit = actionFormEl.querySelector("button[type=submit]");
      submit.addEventListener("click", async (e) => {
        e.preventDefault();
        const name = actionFormEl.querySelector("[data-mt-dns-name]").value.trim();
        if (!name) { writeOutput({ error: "أدخل اسم النطاق" }, false); return; }
        submit.disabled = true;
        await postJson(
          "/mikrotik/" + CFG.routerId + "/ip/dns/resolve",
          { name },
        );
        submit.disabled = false;
      });
    });
  }

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
    const INTERFACES_POLL_MS = 15_000;
    const card  = root.querySelector("[data-mt-interfaces-card]");
    if (!card) return;
    const msg   = card.querySelector("[data-mt-interfaces-msg]");
    const wrap  = card.querySelector("[data-mt-interfaces-wrap]");
    const rows  = card.querySelector("[data-mt-interfaces-rows]");
    const count = card.querySelector("[data-mt-interfaces-count]");
    const refreshBtn = card.querySelector("[data-mt-interfaces-refresh]");

    let timer = null;
    let inflight = false;

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
      const html = list.map(r => {
        const rxErr = parseFloat(r["rx-error"]) || 0;
        const txErr = parseFloat(r["tx-error"]) || 0;
        const errs  = rxErr + txErr;
        const errCell = errs > 0
          ? '<span class="mt-iface-errors">' + errs + '</span>'
          : '<span class="mt-iface-errors mt-iface-errors--ok">0</span>';
        return [
          '<tr data-mt-iface-row="', escapeText(r.name || ""), '">',
          '<td class="mt-iface-name">', escapeText(r.name || "—"), '</td>',
          '<td>', escapeText(r.type || "—"), '</td>',
          '<td class="mt-iface-mac">', escapeText(r["mac-address"] || "—"), '</td>',
          '<td>', escapeText(r.mtu || "—"), '</td>',
          '<td>', statusCell(r), '</td>',
          '<td>', bytesHumanLocal(r["rx-byte"]), '</td>',
          '<td>', bytesHumanLocal(r["tx-byte"]), '</td>',
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
        const list = Array.isArray(env.data) ? env.data : [];
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

  // ─── P6 — Sessions (hotspot + ppp, read-only) ─────────────────
  //
  // Both sub-cards live inside the `sessions` panel — they share
  // the tab-change start/stop hook (same slug) but hit different
  // endpoints so they can render side-by-side as the operator scrolls.
  initTableTab({
    slug: "sessions",
    path: "/hotspot/active",
    pollMs: 10_000,
    cardSel: "[data-mt-hotspot-card]",
    msgSel: "[data-mt-hotspot-sessions-msg]",
    wrapSel: "[data-mt-hotspot-sessions-wrap]",
    rowsSel: "[data-mt-hotspot-sessions-rows]",
    countSel: "[data-mt-hotspot-sessions-count]",
    refreshSel: "[data-mt-hotspot-sessions-refresh]",
    emptyMsg: "لا توجد جلسات Hotspot نشطة الآن.",
    errorFallback: "الراوتر لم يرد على /ip/hotspot/active.",
    row: function (r) {
      return [
        '<tr>',
        '<td class="mt-iface-name">', escapeText(r.user || "—"), '</td>',
        '<td class="mt-iface-mac">', escapeText(r.address || "—"), '</td>',
        '<td class="mt-iface-mac">', escapeText(r["mac-address"] || "—"), '</td>',
        '<td>', escapeText(r.uptime || "—"), '</td>',
        '<td>', escapeText(r["bytes-in"] || "—"), '</td>',
        '<td>', escapeText(r["bytes-out"] || "—"), '</td>',
        '<td>', escapeText(r.comment || ""), '</td>',
        '</tr>',
      ].join("");
    },
  });

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
      return [
        '<tr>',
        '<td class="mt-iface-name">', escapeText(r.name || "—"), '</td>',
        '<td>', escapeText(r.service || "—"), '</td>',
        '<td class="mt-iface-mac">', escapeText(r.address || "—"), '</td>',
        '<td class="mt-iface-mac">', escapeText(r["caller-id"] || "—"), '</td>',
        '<td>', escapeText(r.uptime || "—"), '</td>',
        '</tr>',
      ].join("");
    },
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
