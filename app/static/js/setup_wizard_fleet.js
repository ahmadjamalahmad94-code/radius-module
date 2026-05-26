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

  function ttlCountdown(isoTimestamp) {
    if (!isoTimestamp) return "";
    const target = new Date(isoTimestamp);
    if (isNaN(target.getTime())) return "";
    const diffMs = target.getTime() - Date.now();
    if (diffMs <= 0) return "انتهت";
    const minutes = Math.floor(diffMs / 60000);
    if (minutes < 1) return "< 1 دقيقة";
    if (minutes < 60) return `${minutes} دقيقة`;
    const hours = Math.floor(minutes / 60);
    return `${hours} ساعة و ${minutes % 60} دقيقة`;
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
      const tentativeBadge = router.is_tentative
        ? `<br><span class="swfleet-chip swfleet-chip-warn" title="هذه محاولة قيد التنفيذ — تنتهي تلقائياً عند ${text(router.tentative_expires_at)}">⏳ مؤقّت — ${ttlCountdown(router.tentative_expires_at)}</span>`
        : "";
      const reclaimedBadge = router.is_reclaimed
        ? `<br><span class="swfleet-chip swfleet-chip-muted" title="تم تفريغها تلقائياً (${text(router.tentative_reclaim_reason)})">🧹 ملغاة</span>`
        : "";
      const cancelBtn = router.is_tentative
        ? `<button type="button" class="swfleet-btn swfleet-btn-warn" data-router-cancel-tentative="${router.id}" title="إلغاء يدوي للمحاولة وتحرير الـ IP فوراً">إلغاء المحاولة</button>`
        : "";
      tr.innerHTML = `
        <td><strong>${text(router.router_label, "Router")}</strong><br><small>${text(router.router_identity)}</small>${tentativeBadge}${reclaimedBadge}</td>
        <td>${text(router.router_vpn_ip)}</td>
        <td>${text(router.wireguard_peer_name)}</td>
        <td>${text(router.lifecycle_state)}</td>
        <td><span class="swfleet-chip ${chipClass(health.status)}">${text(health.label_ar || health.status)}</span></td>
        <td>${text(router.last_verification && router.last_verification.step_key)}</td>
        <td>${text(router.next_action)}</td>
        <td class="swfleet-actions">
          <button type="button" class="swfleet-btn swfleet-btn-ghost" data-router-detail="${router.id}">تفاصيل</button>
          ${cancelBtn}
        </td>
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
    } else if (target.matches("[data-router-cancel-tentative]")) {
      cancelTentative(target.dataset.routerCancelTentative, target);
    } else if (target.matches("[data-swfleet-reclaim-expired]")) {
      reclaimAllExpired(target);
    } else if (target.matches("[data-swfleet-close]")) {
      if (drawer) drawer.hidden = true;
    }
  });

  async function cancelTentative(registryId, btn) {
    if (!registryId) return;
    if (!window.confirm("هل تريد إلغاء هذه المحاولة وتحرير الـ IP فوراً؟")) {
      return;
    }
    btn.disabled = true;
    btn.textContent = "...";
    try {
      const res = await fetch(
        `/admin/radius/setup-wizard/fleet/router/${registryId}/cancel-tentative`,
        {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "X-CSRFToken": token(),
          },
          body: JSON.stringify({}),
        },
      );
      const data = await res.json().catch(() => ({ ok: false }));
      if (!res.ok || data.ok === false) {
        throw new Error(data.error || `HTTP ${res.status}`);
      }
      await loadFleet();
    } catch (error) {
      window.alert("فشل إلغاء المحاولة: " + error.message);
      btn.disabled = false;
      btn.textContent = "إلغاء المحاولة";
    }
  }

  async function reclaimAllExpired(btn) {
    btn.disabled = true;
    const originalText = btn.textContent;
    btn.textContent = "جارٍ التنظيف...";
    try {
      const res = await fetch(
        "/admin/radius/setup-wizard/fleet/reclaim-expired",
        {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "X-CSRFToken": token(),
          },
          body: JSON.stringify({}),
        },
      );
      const data = await res.json().catch(() => ({ ok: false }));
      if (!res.ok || data.ok === false) {
        throw new Error(data.error || `HTTP ${res.status}`);
      }
      const sweep = data.sweep || {};
      window.alert(
        `تم تنظيف ${sweep.reclaimed_count || 0} محاولة منتهية ` +
        `(تم فحص ${sweep.scanned || 0}).`
      );
      await loadFleet();
    } catch (error) {
      window.alert("فشل التنظيف: " + error.message);
    } finally {
      btn.disabled = false;
      btn.textContent = originalText;
    }
  }

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

  // ─── Emergency reset ────────────────────────────────────
  const emergencyBtn = page.querySelector("[data-swfleet-emergency-reset]");
  const emergencyModal = page.querySelector("[data-swfleet-emergency-modal]");
  const emergencyClose = page.querySelector("[data-swfleet-emergency-close]");
  const emergencyCancel = page.querySelector("[data-swfleet-emergency-cancel]");
  const emergencyExecute = page.querySelector("[data-swfleet-emergency-execute]");
  const emergencyInput = page.querySelector("[data-swfleet-emergency-input]");
  const emergencyFiles = page.querySelector("[data-swfleet-emergency-files]");
  const emergencyCounts = page.querySelector("[data-swfleet-emergency-counts]");
  const emergencyResult = page.querySelector("[data-swfleet-emergency-result]");
  const CONFIRM_PHRASE = "RESET-WIZARD-FLEET";

  function openEmergencyModal() {
    if (!emergencyModal) return;
    emergencyModal.hidden = false;
    if (emergencyInput) emergencyInput.value = "";
    if (emergencyExecute) {
      emergencyExecute.disabled = true;
      emergencyExecute.textContent = "نعم، نفّذ التفريغ";
    }
    if (emergencyResult) {
      emergencyResult.hidden = true;
      emergencyResult.textContent = "";
    }
    if (emergencyCounts) {
      emergencyCounts.innerHTML = "<p>جاري حساب ما سيتم حذفه...</p>";
    }
    getJson("/admin/radius/setup-wizard/fleet/emergency-reset/preview")
      .then((data) => {
        const p = data.preview || {};
        const counts = p.row_counts || {};
        const rowsHtml = Object.entries(counts)
          .map(([t, c]) => `<tr><td>${t}</td><td>${c}</td></tr>`)
          .join("");
        const peerFilesLine = p.peer_files_count
          ? `<p>سيتم حذف ${p.peer_files_count} ملف من <code dir="ltr">${p.peers_dir}</code> أيضاً.</p>`
          : `<p>لم يتم العثور على ملفات نظراء في <code dir="ltr">${p.peers_dir}</code>.</p>`;
        emergencyCounts.innerHTML = `
          <table><thead><tr><th>الجدول</th><th>عدد الصفوف</th></tr></thead>
          <tbody>${rowsHtml}</tbody></table>
          <p style="margin-top:8px"><b>الإجمالي: ${p.total_rows || 0} صف</b></p>
          ${peerFilesLine}
        `;
      })
      .catch((error) => {
        emergencyCounts.innerHTML = `<p style="color:#dc2626">فشل التحميل: ${error.message}</p>`;
      });
  }

  function closeEmergencyModal() {
    if (emergencyModal) emergencyModal.hidden = true;
  }

  if (emergencyBtn) emergencyBtn.addEventListener("click", openEmergencyModal);
  if (emergencyClose) emergencyClose.addEventListener("click", closeEmergencyModal);
  if (emergencyCancel) emergencyCancel.addEventListener("click", closeEmergencyModal);
  if (emergencyInput && emergencyExecute) {
    emergencyInput.addEventListener("input", () => {
      emergencyExecute.disabled = emergencyInput.value.trim() !== CONFIRM_PHRASE;
    });
  }
  if (emergencyExecute) {
    emergencyExecute.addEventListener("click", async () => {
      emergencyExecute.disabled = true;
      emergencyExecute.textContent = "جارٍ التنفيذ...";
      try {
        const res = await fetch("/admin/radius/setup-wizard/fleet/emergency-reset", {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "X-CSRFToken": token(),
          },
          body: JSON.stringify({
            confirm: emergencyInput ? emergencyInput.value.trim() : "",
            clear_peer_files: emergencyFiles ? emergencyFiles.checked : true,
          }),
        });
        const data = await res.json().catch(() => ({ ok: false, error: "invalid_json" }));
        if (!res.ok || data.ok === false) {
          throw new Error(data.error || `HTTP ${res.status}`);
        }
        if (emergencyResult) {
          emergencyResult.hidden = false;
          emergencyResult.textContent = JSON.stringify(data.reset, null, 2);
        }
        emergencyExecute.textContent = "تم التفريغ ✓";
        loadFleet().catch(() => {});
        window.setTimeout(closeEmergencyModal, 2500);
      } catch (error) {
        if (emergencyResult) {
          emergencyResult.hidden = false;
          emergencyResult.style.background = "#fef2f2";
          emergencyResult.style.borderColor = "#fca5a5";
          emergencyResult.textContent = "فشل: " + error.message;
        }
        emergencyExecute.disabled = false;
        emergencyExecute.textContent = "نعم، نفّذ التفريغ";
      }
    });
  }
  if (emergencyModal) {
    emergencyModal.addEventListener("click", (event) => {
      if (event.target === emergencyModal) closeEmergencyModal();
    });
  }
})();
