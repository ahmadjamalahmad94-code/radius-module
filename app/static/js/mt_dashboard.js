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
})();
