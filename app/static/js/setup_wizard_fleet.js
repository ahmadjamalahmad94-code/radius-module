(function () {
  "use strict";

  const page = document.querySelector("[data-setup-wizard-fleet]");
  if (!page) return;

  const rows = page.querySelector("[data-swfleet-rows]");
  const actionList = page.querySelector("[data-swfleet-action-needed]");
  const drawer = page.querySelector("[data-swfleet-drawer]");
  const detailJson = page.querySelector("[data-swfleet-detail-json]");
  const search = page.querySelector("[data-swfleet-search]");
  const statusFilter = page.querySelector("[data-swfleet-status]");
  const lifecycleFilter = page.querySelector("[data-swfleet-lifecycle]");
  const includeRetired = page.querySelector("[data-swfleet-include-retired]");

  function token() {
    const input = page.querySelector('input[name="_csrf_token"]');
    return input ? input.value : "";
  }

  async function getJson(url) {
    const res = await fetch(url, { headers: { "X-CSRFToken": token() } });
    const data = await res.json().catch(() => ({ ok: false, error: "invalid_json" }));
    if (!res.ok || data.ok === false) {
      throw new Error(data.error || `HTTP ${res.status}`);
    }
    return data;
  }

  function chipClass(status) {
    if (status === "healthy") return "is-healthy";
    if (status === "stale") return "is-danger";
    return "is-warning";
  }

  function text(value, fallback) {
    return value == null || value === "" ? fallback || "--" : String(value);
  }

  function setKpi(name, value) {
    const node = page.querySelector(`[data-swfleet-kpi="${name}"]`);
    if (node) node.textContent = String(value || 0);
  }

  function setPool(name, value) {
    const node = page.querySelector(`[data-swfleet-pool="${name}"]`);
    if (node) node.textContent = text(value);
  }

  function renderMetrics(fleet) {
    const metrics = fleet.metrics || {};
    Object.keys(metrics).forEach((key) => setKpi(key, metrics[key]));
    const usage = fleet.allocation_usage || {};
    setPool("cidr", usage.vpn_pool_cidr);
    setPool("used", usage.used);
    setPool("remaining", usage.remaining);
    setPool("next", usage.next_available);
    const bar = page.querySelector("[data-swfleet-pool-bar]");
    if (bar) {
      const capacity = Number(usage.capacity || 0);
      const used = Number(usage.used || 0);
      const pct = capacity > 0 ? Math.min(100, Math.round((used / capacity) * 100)) : 0;
      bar.style.width = `${pct}%`;
    }
  }

  function renderActionNeeded(items) {
    if (!actionList) return;
    actionList.innerHTML = "";
    if (!items || !items.length) {
      actionList.innerHTML = "<div class=\"swfleet-action-card\"><strong>لا توجد إجراءات عاجلة</strong><span>الأسطول هادئ حاليًا.</span></div>";
      return;
    }
    items.forEach((router) => {
      const card = document.createElement("button");
      card.type = "button";
      card.className = "swfleet-action-card";
      card.dataset.routerId = router.id;
      card.innerHTML = `<strong>${text(router.router_label, "Router")}</strong><span>${text(router.next_action)} · ${text(router.router_vpn_ip)}</span>`;
      actionList.appendChild(card);
    });
  }

  function renderRows(routers) {
    if (!rows) return;
    rows.innerHTML = "";
    if (!routers || !routers.length) {
      rows.innerHTML = "<tr><td colspan=\"8\">لا توجد راوترات بعد.</td></tr>";
      return;
    }
    routers.forEach((router) => {
      const health = router.health || {};
      const tr = document.createElement("tr");
      tr.innerHTML = `
        <td><strong>${text(router.router_label, "Router")}</strong><br><small>${text(router.router_identity)}</small></td>
        <td>${text(router.router_vpn_ip)}</td>
        <td>${text(router.wireguard_peer_name)}</td>
        <td>${text(router.lifecycle_state)}</td>
        <td><span class="swfleet-chip ${chipClass(health.status)}">${text(health.label_ar || health.status)}</span></td>
        <td>${text(router.last_verification && router.last_verification.step_key)}</td>
        <td>${text(router.next_action)}</td>
        <td class="swfleet-actions"><button type="button" class="swfleet-btn swfleet-btn-ghost" data-router-detail="${router.id}">تفاصيل</button></td>
      `;
      rows.appendChild(tr);
    });
  }

  function queryString() {
    const params = new URLSearchParams();
    if (search && search.value) params.set("q", search.value.trim());
    if (statusFilter && statusFilter.value) params.set("status", statusFilter.value);
    if (lifecycleFilter && lifecycleFilter.value) params.set("lifecycle_state", lifecycleFilter.value);
    if (includeRetired && !includeRetired.checked) params.set("include_retired", "0");
    const value = params.toString();
    return value ? `?${value}` : "";
  }

  async function loadFleet() {
    if (rows) rows.innerHTML = "<tr><td colspan=\"8\">يتم التحميل...</td></tr>";
    const data = await getJson(`/admin/radius/setup-wizard/fleet/data${queryString()}`);
    const fleet = data.fleet || {};
    renderMetrics(fleet);
    renderActionNeeded(fleet.action_needed || []);
    renderRows(fleet.routers || []);
  }

  async function openDetail(id) {
    const data = await getJson(`/admin/radius/setup-wizard/fleet/router/${id}`);
    const detail = data.detail || {};
    const router = detail.router || {};
    page.querySelector('[data-swfleet-detail="title"]').textContent = text(router.router_label, "Router");
    page.querySelector('[data-swfleet-detail="vpn_ip"]').textContent = text(router.router_vpn_ip);
    page.querySelector('[data-swfleet-detail="peer"]').textContent = text(router.wireguard_peer_name);
    page.querySelector('[data-swfleet-detail="lifecycle"]').textContent = text(router.lifecycle_state);
    page.querySelector('[data-swfleet-detail="health"]').textContent = text(router.health && router.health.status);
    if (detailJson) detailJson.textContent = JSON.stringify(detail, null, 2);
    if (drawer) drawer.hidden = false;
  }

  page.addEventListener("click", (event) => {
    const target = event.target.closest("button");
    if (!target) return;
    if (target.matches("[data-swfleet-refresh]")) {
      loadFleet();
    } else if (target.matches("[data-router-detail]")) {
      openDetail(target.dataset.routerDetail);
    } else if (target.matches("[data-router-id]")) {
      openDetail(target.dataset.routerId);
    } else if (target.matches("[data-swfleet-close]")) {
      if (drawer) drawer.hidden = true;
    }
  });

  let searchTimer = 0;
  [statusFilter, lifecycleFilter, includeRetired].forEach((node) => {
    if (!node) return;
    node.addEventListener("change", loadFleet);
  });
  if (search) {
    search.addEventListener("input", () => {
      window.clearTimeout(searchTimer);
      searchTimer = window.setTimeout(loadFleet, 250);
    });
  }

  loadFleet().catch((error) => {
    if (rows) rows.innerHTML = `<tr><td colspan="8">${error.message}</td></tr>`;
  });
})();
