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

  const actionFormEl = root.querySelector("[data-mt-action-form]");
  const actionOutEl  = root.querySelector("[data-mt-action-output]");
  const actionButtons = {
    backup:   root.querySelector("[data-mt-action-backup]"),
    reboot:   root.querySelector("[data-mt-action-reboot]"),
    ping:     root.querySelector("[data-mt-action-ping]"),
    identity: root.querySelector("[data-mt-action-identity]"),
  };

  function clearActiveButtons() {
    for (const k in actionButtons) {
      if (actionButtons[k]) actionButtons[k].classList.remove("is-active");
    }
  }

  function writeOutput(payload, ok) {
    if (!actionOutEl) return;
    actionOutEl.textContent = JSON.stringify(payload, null, 2);
    actionOutEl.classList.toggle("is-ok", !!ok);
    actionOutEl.classList.toggle("is-fail", !ok);
  }

  function closeForm() {
    if (!actionFormEl) return;
    actionFormEl.hidden = true;
    actionFormEl.textContent = "";
    clearActiveButtons();
  }

  function openForm(key, html) {
    if (!actionFormEl) return;
    actionFormEl.hidden = false;
    actionFormEl.innerHTML = html;
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
})();
