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

  function setOutput(payload) {
    outputPreview.textContent = JSON.stringify(payload, null, 2);
  }

  function parseJson(text, fallback) {
    try {
      return JSON.parse(text || "");
    } catch (_) {
      return fallback;
    }
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
    const payload = parseJson(form.querySelector('[name="payload_json"]').value, {});
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
    const payload = parseJson(form.querySelector('[name="payload_json"]').value, {});
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
    const payload = parseJson(form.querySelector('[name="payload_json"]').value, {});
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
    const payload = parseJson(form.querySelector('[name="payload_json"]').value, {});
    const blocked = parseJson(form.querySelector('[name="blocked_json"]').value, []);
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
    const payload = parseJson(form.querySelector('[name="payload_json"]').value, {});
    const blocked = parseJson(form.querySelector('[name="blocked_json"]').value, []);
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
      pilotOutput.textContent = JSON.stringify(data.pilot_drill || data, null, 2);
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
      `Inventory collected: ${snapshot ? "yes - " + (snapshot.created_at || "") : "pending"}`,
      `Apply attempted: ${hasApplied ? "yes" : "blocked/not attempted"}`,
      `Verification: ${(health.health && health.health.failed_verifications) ? "attention required" : "pending or clean"}`,
      `Rollback available: ${hasRollback ? "yes" : "no"}`,
      `Warnings/failed operations: ${hasFailed ? "review required" : "none in queue"}`,
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
