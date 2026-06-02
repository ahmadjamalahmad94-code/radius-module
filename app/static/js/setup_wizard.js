(function () {
  const root = document.querySelector("[data-setup-wizard-page]");
  if (!root) return;

  const boot = window.__SETUP_WIZARD_BOOTSTRAP__ || {};
  let currentRunId = Number(root.getAttribute("data-current-run-id") || 0) || 0;
  let lastScript = "";

  const scriptPreview = root.querySelector("[data-sw-script-preview]");
  const outputPreview = root.querySelector("[data-sw-output-preview]");
  const cardsHost = root.querySelector("[data-sw-verification-cards]");
  const pilotOutput = root.querySelector("[data-sw-pilot-output]");
  const labTimeline = root.querySelector("[data-sw-lab-timeline]");

  function token() {
    const input = root.querySelector('input[name="_csrf_token"]');
    return input ? input.value : "";
  }

  function setScript(text) {
    lastScript = text || "";
    scriptPreview.textContent = lastScript || "-- لا يوجد سكربت بعد --";
  }

  const RESULT_LABELS = {
    ok: "النتيجة",
    error: "الخطأ",
    message: "الرسالة",
    code: "رمز الحالة",
    status: "الحالة",
    run_id: "رقم التشغيل",
    step: "الخطوة",
    timeline: "الخط الزمني",
    operations: "العمليات",
    health: "فحص الصحة",
    summary: "الملخص",
    verification: "التحقق",
    support_bundle: "حزمة الدعم",
    pilot_drill: "قائمة فحص التجربة",
  };

  function resultLabel(key) {
    return RESULT_LABELS[key] || String(key || "").replaceAll("_", " ");
  }

  function resultValue(value) {
    if (value === true) return "نجح";
    if (value === false) return "لم ينجح";
    if (value == null || value === "") return "لا توجد قيمة";
    if (Array.isArray(value)) {
      if (!value.length) return "لا توجد عناصر";
      if (value.every((item) => typeof item === "string")) return value.join("\n");
      return `${value.length} عنصر`;
    }
    if (typeof value === "object") {
      if (value.message || value.error || value.status || value.overall) {
        return String(value.message || value.error || value.status || value.overall);
      }
      return "تفاصيل متاحة في البطاقات المرتبطة";
    }
    return String(value);
  }

  function formatResult(payload) {
    if (!payload || typeof payload !== "object") return String(payload || "لا توجد نتائج بعد");
    const preferred = [
      "ok", "message", "error", "code", "status", "run_id", "step",
      "timeline", "verification", "health", "operations", "support_bundle",
    ];
    const keys = [
      ...preferred.filter((key) => Object.prototype.hasOwnProperty.call(payload, key)),
      ...Object.keys(payload).filter((key) => !preferred.includes(key)).slice(0, 6),
    ];
    if (!keys.length) return "لا توجد تفاصيل إضافية.";
    return keys.map((key) => `${resultLabel(key)}: ${resultValue(payload[key])}`).join("\n");
  }

  function setOutput(payload) {
    outputPreview.textContent = formatResult(payload);
  }

  function parseJson(text, fallback) {
    try {
      return JSON.parse(text || "");
    } catch (_) {
      return fallback;
    }
  }

  function formValue(form, name, fallback) {
    const input = form ? form.querySelector(`[name="${name}"]`) : null;
    const value = input ? String(input.value || "").trim() : "";
    return value || fallback || "";
  }

  function formChecked(form, name) {
    const input = form ? form.querySelector(`[name="${name}"]`) : null;
    return !!(input && input.checked);
  }

  function splitList(value) {
    return String(value || "")
      .split(/[\n,]+/)
      .map((item) => item.trim())
      .filter(Boolean);
  }

  function writeJsonField(form, name, payload) {
    const input = form ? form.querySelector(`[name="${name}"]`) : null;
    if (input) input.value = JSON.stringify(payload || {});
  }

  function syncConfigPayload(form) {
    if (!form) return;
    const kind = form.getAttribute("data-sw-form") || "";
    if (kind === "internet-source") {
      writeJsonField(form, "payload_json", {
        interface: formValue(form, "selected_wan_interface", "ether1"),
        add_default_route: formChecked(form, "add_default_route"),
        use_peer_dns: formChecked(form, "use_peer_dns"),
        nat_enabled: formChecked(form, "nat_enabled"),
      });
      return;
    }
    if (kind === "vpn-radius") {
      writeJsonField(form, "payload_json", {
        wg_interface_name: formValue(form, "wg_interface_name", "hr-wg"),
        peer_name: formValue(form, "peer_name", "vps-peer"),
        router_vpn_ip: formValue(form, "router_vpn_ip", "10.10.0.3"),
        vps_vpn_ip: formValue(form, "vps_vpn_ip", "10.10.0.1"),
        allowed_address: formValue(form, "allowed_address", "10.10.0.1/32"),
        vps_public_endpoint: formValue(form, "vps_public_endpoint", "187.77.70.18"),
        endpoint_port: Number(formValue(form, "endpoint_port", "51820")) || 51820,
        radius_server_ip: formValue(form, "radius_server_ip", "10.10.0.1"),
        radius_secret: formValue(form, "radius_secret", "CHANGE_ME"),
        api_username: formValue(form, "api_username", "hr_api_setup"),
      });
      return;
    }
    if (kind === "hotspot") {
      writeJsonField(form, "payload_json", {
        selected_interfaces: splitList(formValue(form, "hotspot_interfaces", "ether3")),
      });
      writeJsonField(form, "blocked_json", splitList(formValue(form, "blocked_networks_text", "")));
      return;
    }
    if (kind === "broadband") {
      writeJsonField(form, "payload_json", {
        selected_interfaces: splitList(formValue(form, "broadband_interfaces", "ether4")),
        local_address: formValue(form, "local_address", "10.88.44.1"),
        remote_pool_cidr: formValue(form, "remote_pool_cidr", "10.88.44.0/24"),
      });
      writeJsonField(form, "blocked_json", splitList(formValue(form, "blocked_networks_text", "")));
    }
  }

  function payloadFromForm(form) {
    syncConfigPayload(form);
    return parseJson(form.querySelector('[name="payload_json"]')?.value || "", {});
  }

  function blockedListFromForm(form) {
    syncConfigPayload(form);
    return parseJson(form.querySelector('[name="blocked_json"]')?.value || "", []);
  }

  async function postJson(url, body) {
    const res = await fetch(url, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-CSRFToken": token(),
      },
      body: JSON.stringify(body || {}),
    });
    const data = await res.json().catch(() => ({ ok: false, error: "invalid_json" }));
    if (!res.ok || data.ok === false) {
      const msg = (data && (data.error || data.message)) || ("HTTP " + res.status);
      throw new Error(msg);
    }
    return data;
  }

  async function getJson(url) {
    const res = await fetch(url, { headers: { "X-CSRFToken": token() } });
    const data = await res.json().catch(() => ({ ok: false, error: "invalid_json" }));
    if (!res.ok || data.ok === false) {
      const msg = (data && (data.error || data.message)) || ("HTTP " + res.status);
      throw new Error(msg);
    }
    return data;
  }

  function renderVerification(cards) {
    if (!cardsHost) return;
    cardsHost.innerHTML = "";
    (cards || []).forEach((card) => {
      const el = document.createElement("article");
      el.className = `sw-status-card status-${card.status || "pending"}`;
      el.innerHTML = `
        <h3>${card.title_ar || card.key}</h3>
        <div class="status-pill">${card.status || "pending"}</div>
        <p>${card.details_ar || ""}</p>
      `;
      cardsHost.appendChild(el);
    });
  }

  async function refreshSummary() {
    if (!currentRunId) return;
    const data = await getJson(`/admin/radius/setup-wizard/runs/${currentRunId}/summary`);
    renderVerification(data.verification && data.verification.cards);
    setOutput(data);
  }

  async function createRun() {
    const data = await postJson("/admin/radius/setup-wizard/runs", {});
    currentRunId = data.run && data.run.id;
    root.setAttribute("data-current-run-id", String(currentRunId || ""));
    await refreshSummary();
  }

  function requireRun() {
    if (!currentRunId) throw new Error("ابدأ تشغيل جديد أولاً");
  }

  function verificationBody(formSelector) {
    const form = root.querySelector(formSelector);
    const output = form ? String(form.querySelector('[name="verify_output"]')?.value || "") : "";
    return {
      mode: "pasted_output",
      output,
    };
  }

  function operationStep() {
    return root.querySelector("[data-sw-operation-step]")?.value || "internet";
  }

  function pilotStep() {
    return root.querySelector("[data-sw-pilot-step]")?.value || "internet";
  }

  function confirmationText() {
    return root.querySelector("[data-sw-confirmation]")?.value || "";
  }

  async function actionSetInternet() {
    requireRun();
    const form = root.querySelector('[data-sw-form="internet-source"]');
    const payload = payloadFromForm(form);
    const data = await postJson(`/admin/radius/setup-wizard/runs/${currentRunId}/internet-source`, {
      source_type: form.querySelector('[name="source_type"]').value,
      selected_wan_interface: form.querySelector('[name="selected_wan_interface"]').value,
      input_json: payload,
    });
    setOutput(data);
  }

  async function actionGenerateInternet() {
    requireRun();
    const form = root.querySelector('[data-sw-form="internet-source"]');
    const payload = payloadFromForm(form);
    const data = await postJson(`/admin/radius/setup-wizard/runs/${currentRunId}/generate-internet-script`, {
      source_type: form.querySelector('[name="source_type"]').value,
      selected_wan_interface: form.querySelector('[name="selected_wan_interface"]').value,
      payload,
    });
    setScript(data.plan && data.plan.script_text);
    await refreshSummary();
  }

  async function actionVerifyInternet() {
    requireRun();
    const data = await postJson(
      `/admin/radius/setup-wizard/runs/${currentRunId}/verify-internet`,
      verificationBody('[data-sw-form="internet-source"]')
    );
    setOutput(data);
    await refreshSummary();
  }

  async function actionGenerateVpn() {
    requireRun();
    const form = root.querySelector('[data-sw-form="vpn-radius"]');
    const payload = payloadFromForm(form);
    const data = await postJson(`/admin/radius/setup-wizard/runs/${currentRunId}/generate-vpn-radius-script`, {
      payload,
    });
    setScript(data.plan && data.plan.script_text);
    await refreshSummary();
  }

  async function actionVerifyVpn() {
    requireRun();
    const data = await postJson(
      `/admin/radius/setup-wizard/runs/${currentRunId}/verify-vpn-radius`,
      verificationBody('[data-sw-form="vpn-radius"]')
    );
    setOutput(data);
    await refreshSummary();
  }

  async function actionInterfacesCandidates() {
    requireRun();
    const data = await postJson(`/admin/radius/setup-wizard/runs/${currentRunId}/interfaces/candidates`, {
      interfaces: [
        { name: "ether1", kind: "ether", running: true },
        { name: "ether2", kind: "ether", running: true },
        { name: "ether3", kind: "ether", running: true },
        { name: "hr-wg", kind: "wireguard", running: true },
      ],
    });
    setOutput(data);
  }

  async function actionGenerateHotspot() {
    requireRun();
    const form = root.querySelector('[data-sw-form="hotspot"]');
    const payload = payloadFromForm(form);
    const blocked = blockedListFromForm(form);
    const data = await postJson(`/admin/radius/setup-wizard/runs/${currentRunId}/generate-hotspot-script`, {
      mode: form.querySelector('[name="mode"]').value,
      payload,
      blocked_network_cidrs: blocked,
    });
    setScript(data.plan && data.plan.script_text);
    await refreshSummary();
  }

  async function actionVerifyHotspot() {
    requireRun();
    const data = await postJson(
      `/admin/radius/setup-wizard/runs/${currentRunId}/verify-hotspot`,
      verificationBody('[data-sw-form="hotspot"]')
    );
    setOutput(data);
    await refreshSummary();
  }

  async function actionGenerateBroadband() {
    requireRun();
    const form = root.querySelector('[data-sw-form="broadband"]');
    const payload = payloadFromForm(form);
    const blocked = blockedListFromForm(form);
    const data = await postJson(`/admin/radius/setup-wizard/runs/${currentRunId}/generate-broadband-script`, {
      mode: form.querySelector('[name="mode"]').value,
      payload,
      blocked_network_cidrs: blocked,
    });
    setScript(data.plan && data.plan.script_text);
    await refreshSummary();
  }

  async function actionVerifyBroadband() {
    requireRun();
    const data = await postJson(
      `/admin/radius/setup-wizard/runs/${currentRunId}/verify-broadband`,
      verificationBody('[data-sw-form="broadband"]')
    );
    setOutput(data);
    await refreshSummary();
  }

  async function actionCopyScript() {
    if (!lastScript) {
      setOutput({ ok: false, message: "لا يوجد سكربت لنسخه بعد" });
      return;
    }
    await navigator.clipboard.writeText(lastScript);
    setOutput({ ok: true, message: "تم نسخ السكربت" });
  }

  async function actionDryRun() {
    requireRun();
    const step = operationStep();
    const data = await postJson(`/admin/radius/setup-wizard/runs/${currentRunId}/dry-run/${step}`, {});
    setOutput(data);
  }

  async function actionListOperations() {
    requireRun();
    const data = await getJson(`/admin/radius/setup-wizard/runs/${currentRunId}/operations`);
    setOutput(data);
  }

  async function actionApplyStep() {
    requireRun();
    const step = operationStep();
    const data = await postJson(`/admin/radius/setup-wizard/runs/${currentRunId}/apply/${step}`, {
      confirmation: confirmationText(),
    });
    setOutput(data);
    await refreshSummary();
  }

  async function actionRollbackPreview() {
    requireRun();
    const step = operationStep();
    const data = await postJson(`/admin/radius/setup-wizard/runs/${currentRunId}/rollback/${step}`, {
      preview: true,
    });
    setOutput(data);
  }

  async function actionSaveInventory() {
    requireRun();
    const output = root.querySelector("[data-sw-inventory-output]")?.value || "";
    const data = await postJson(`/admin/radius/setup-wizard/runs/${currentRunId}/inventory`, { output });
    setOutput(data);
    await refreshSummary();
  }

  async function actionHealth() {
    requireRun();
    const data = await getJson(`/admin/radius/setup-wizard/runs/${currentRunId}/health`);
    setOutput(data);
  }

  async function actionSupportBundle() {
    requireRun();
    const data = await getJson(`/admin/radius/setup-wizard/runs/${currentRunId}/support-bundle`);
    setOutput(data);
  }

  async function actionAddedCatalog() {
    const data = await getJson("/admin/radius/setup-wizard/added-services/catalog");
    setOutput(data);
  }

  async function actionPilotDrill() {
    requireRun();
    const data = await getJson(`/admin/radius/setup-wizard/runs/${currentRunId}/pilot-drill?step=${encodeURIComponent(pilotStep())}`);
    if (pilotOutput) {
      pilotOutput.textContent = formatResult(data.pilot_drill || data);
    }
    setOutput(data);
  }

  async function actionLabTimeline() {
    requireRun();
    const [summary, ops, health] = await Promise.all([
      getJson(`/admin/radius/setup-wizard/runs/${currentRunId}/summary`),
      getJson(`/admin/radius/setup-wizard/runs/${currentRunId}/operations`),
      getJson(`/admin/radius/setup-wizard/runs/${currentRunId}/health`),
    ]);
    const operations = ops.operations || [];
    const hasDry = operations.some((op) => op.status === "dry_run_ready");
    const hasApplied = operations.some((op) => op.applied_at || op.status === "applied");
    const hasRollback = operations.some((op) => op.rollback_command);
    const hasFailed = operations.some((op) => op.status === "failed");
    const snapshot = summary.latest_router_snapshot || null;
    const rows = [
      `التجربة الجافة: ${hasDry ? "مكتملة" : "معلقة"}`,
      `الجرد: ${snapshot ? "مكتمل - " + (snapshot.created_at || "") : "بانتظار التنفيذ"}`,
      `محاولة التطبيق: ${hasApplied ? "تمت" : "محظورة أو لم تبدأ"}`,
      `التحقق: ${(health.health && health.health.failed_verifications) ? "بحاجة إلى مراجعة" : "بانتظار التنفيذ أو سليم"}`,
      `التراجع: ${hasRollback ? "متاح" : "غير متاح"}`,
      `التحذيرات أو العمليات الفاشلة: ${hasFailed ? "بحاجة إلى مراجعة" : "لا يوجد شيء في الطابور"}`,
    ];
    if (labTimeline) {
      labTimeline.innerHTML = rows.map((row) => `<li>${row}</li>`).join("");
    }
    setOutput({ ok: true, timeline: rows, summary, operations, health });
  }

  const actions = {
    "create-run": createRun,
    "refresh-summary": refreshSummary,
    "set-internet-source": actionSetInternet,
    "generate-internet-script": actionGenerateInternet,
    "verify-internet": actionVerifyInternet,
    "generate-vpn-script": actionGenerateVpn,
    "verify-vpn": actionVerifyVpn,
    "interfaces-candidates": actionInterfacesCandidates,
    "generate-hotspot-script": actionGenerateHotspot,
    "verify-hotspot": actionVerifyHotspot,
    "generate-broadband-script": actionGenerateBroadband,
    "verify-broadband": actionVerifyBroadband,
    "copy-script": actionCopyScript,
    "dry-run": actionDryRun,
    "list-operations": actionListOperations,
    "apply-step": actionApplyStep,
    "rollback-preview": actionRollbackPreview,
    "save-inventory": actionSaveInventory,
    "health": actionHealth,
    "support-bundle": actionSupportBundle,
    "added-catalog": actionAddedCatalog,
    "pilot-drill": actionPilotDrill,
    "lab-timeline": actionLabTimeline,
  };

  root.querySelectorAll("[data-sw-action]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const action = btn.getAttribute("data-sw-action");
      const fn = actions[action];
      if (!fn) return;
      const prev = btn.disabled;
      btn.disabled = true;
      try {
        await fn();
      } catch (err) {
        setOutput({ ok: false, error: String((err && err.message) || err) });
      } finally {
        btn.disabled = prev;
      }
    });
  });

  if (boot.summary && boot.summary.verification && boot.summary.verification.cards) {
    renderVerification(boot.summary.verification.cards);
    setOutput(boot.summary);
  }
})();
