(function () {
  "use strict";

  const page = document.querySelector("[data-setup-wizard-v2]");
  if (!page) return;

  const steps = Array.from(page.querySelectorAll("[data-swv2-step]"));
  const stepperItems = Array.from(page.querySelectorAll("[data-swv2-step-target]"));
  const count = page.querySelector("[data-swv2-step-count]");
  const prev = page.querySelector("[data-swv2-prev]");
  const next = page.querySelector("[data-swv2-next]");
  const sourceCards = Array.from(page.querySelectorAll("[data-source-type]"));
  const sourceForms = Array.from(page.querySelectorAll("[data-source-form]"));
  const scriptStatus = page.querySelector("[data-swv2-script-status]");
  const vpnScriptStatus = page.querySelector("[data-swv2-vpn-script-status]");
  const internetPlanJson = page.querySelector('[data-swv2-plan-json="internet"]');
  const vpnPlanJson = page.querySelector('[data-swv2-plan-json="vpn"]');
  const internetScript = document.getElementById("internet-script-code");
  const vpnScript = document.getElementById("vpn-script-code");
  const routerKeyStatus = page.querySelector("[data-swv2-router-key-status]");
  const serverPeerResult = page.querySelector("[data-swv2-server-peer-result]");
  const serverPeerSimple = page.querySelector("[data-swv2-server-peer-simple]");
  const serverPeerStatus = page.querySelector("[data-swv2-server-peer-status]");
  const serverWgReadinessResult = page.querySelector("[data-swv2-server-wg-readiness-result]");
  const serverPeerHealthResult = page.querySelector("[data-swv2-peer-health-result]");
  const recoveryPanel = page.querySelector("[data-swv2-recovery-panel]");
  const recoveryProblems = page.querySelector("[data-swv2-recovery-problems]");
  const recoveryJson = page.querySelector("[data-swv2-recovery-json]");
  const recoverySupport = page.querySelector("[data-swv2-recovery-support]");
  const stepNames = steps.map((step) => step.dataset.swv2Step);
  let current = 0;
  let selectedSource = "dhcp";
  let currentRunId = 0;
  let internetPlanSignature = "";
  let vpnPlanGenerated = false;
  let routerPublicKeySubmitted = false;
  let vpnVerified = false;
  let selectedServicePath = "";
  let selectedInterfaces = [];
  let addedServicesCatalog = null;
  let selectedAddedService = "walled_garden";
  let selectedAddedInputs = {};
  const serviceModes = { hotspot: "smart", broadband: "smart" };
  const servicePlans = { hotspot: null, broadband: null };

  function token() {
    const input = page.querySelector('input[name="_csrf_token"]');
    return input ? input.value : "";
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
      throw new Error((data && (data.error || data.message)) || `HTTP ${res.status}`);
    }
    return data;
  }

  function friendlyWizardError(message) {
    const raw = String(message || "").trim();
    const lower = raw.toLowerCase();
    if (!raw) return "حدث خطأ غير واضح. أعد المحاولة أو ارجع خطوة واحدة.";
    if (lower.includes("vpn/radius verification is required first")) {
      return "لم يكتمل فحص ربط الراوتر بالخادم بعد. ارجع إلى خطوة تحقق الربط، الصق مخرجات MikroTik، ثم اضغط تحليل المخرجات.";
    }
    if (lower.includes("generated script is required before dry-run")) {
      return "ولّد السكربت أولًا قبل تشغيل المراجعة الجافة.";
    }
    if (lower.includes("internet verification is required first")) {
      return "أكمل فحص الإنترنت أولًا قبل المتابعة.";
    }
    if (lower.includes("router public key is required")) {
      return "لم نلتقط مفتاح الراوتر بعد. الصق مخرجات WireGuard من MikroTik في خطوة تحقق الربط.";
    }
    if (lower.includes("duplicate wireguard public key")) {
      return "هذا الراوتر ظاهر على الخادم مسبقًا بنفس مفتاح WireGuard. إذا كان ping و handshake ناجحين، أكمل للخطوة التالية ولا تحتاج تجهيزًا إضافيًا.";
    }
    if (lower.includes("duplicate wireguard allowed ip")) {
      return "عنوان VPN هذا مستخدم مسبقًا على الخادم. اختر تشغيلًا جديدًا أو نظّف الحجز القديم قبل إعادة التجربة.";
    }
    if (lower.includes("dry_run_required")) {
      return "يجب تجهيز خطة آمنة أولًا قبل محاولة الربط على الخادم.";
    }
    if (lower.includes("server_wg_real_apply_flags_disabled")) {
      return "التجهيز الحقيقي على الخادم غير مفعّل إلا في وضع المختبر الداخلي. إذا كان ping ناجحًا يمكنك المتابعة بدون هذه الخطوة.";
    }
    if (lower.includes("server_wg_readiness_not_ready")) {
      return "الخادم غير جاهز لتنفيذ الربط من داخل HobeRadius الآن. تحقق من جاهزية WireGuard أو تابع إذا كان الربط يعمل فعلًا.";
    }
    if (lower.includes("at least one") && lower.includes("interface")) {
      return "اختر منفذ شبكة واحدًا على الأقل قبل توليد السكربت.";
    }
    return raw;
  }

  async function getJson(url) {
    const res = await fetch(url, {
      method: "GET",
      headers: { "X-CSRFToken": token() },
    });
    const data = await res.json().catch(() => ({ ok: false, error: "invalid_json" }));
    if (!res.ok || data.ok === false) {
      throw new Error((data && (data.error || data.message)) || `HTTP ${res.status}`);
    }
    return data;
  }

  function renderRecovery(recovery) {
    if (!recoveryPanel) return;
    const state = recovery?.recovery_state || "clean_resume";
    recoveryPanel.hidden = state === "clean_resume";
    if (recoverySupport && currentRunId) {
      recoverySupport.href = `/admin/radius/setup-wizard/runs/${currentRunId}/support-bundle`;
    }
    if (recoveryProblems) {
      recoveryProblems.innerHTML = "";
      (recovery?.problems || []).forEach((problem) => {
        const card = document.createElement("div");
        card.className = "swv2-diagnostic-card";
        const title = document.createElement("strong");
        title.textContent = problem.title_ar || problem.code || "ملاحظة";
        const body = document.createElement("span");
        body.textContent = problem.explanation_ar || "";
        card.append(title, body);
        recoveryProblems.appendChild(card);
      });
    }
    if (recoveryJson) recoveryJson.textContent = JSON.stringify(recovery || {}, null, 2);
  }

  async function checkRecovery() {
    const runId = await ensureRun();
    const data = await getJson(`/admin/radius/setup-wizard/runs/${runId}/recovery`);
    renderRecovery(data.recovery || {});
    return data.recovery || {};
  }

  async function resumeRecovery() {
    const runId = await ensureRun();
    const data = await postJson(`/admin/radius/setup-wizard/runs/${runId}/recovery/resume`, {});
    renderRecovery(data.analysis || data.recovery || {});
    const nextStep = data.next_safe_step || data.analysis?.next_safe_step || "";
    const idx = stepNames.indexOf(nextStep);
    if (idx >= 0) showStep(idx);
  }

  async function retryRecoveryVerification() {
    const runId = await ensureRun();
    const data = await postJson(`/admin/radius/setup-wizard/runs/${runId}/recovery/retry-verification`, {
      mode: "manual_contract",
      checks: {},
    });
    if (recoveryJson) recoveryJson.textContent = JSON.stringify(data || {}, null, 2);
    await checkRecovery();
  }

  async function regenerateRecoveryScript() {
    const runId = await ensureRun();
    const data = await postJson(`/admin/radius/setup-wizard/runs/${runId}/recovery/regenerate-script`, {
      step_key: "vpn_radius",
    });
    if (recoveryJson) recoveryJson.textContent = JSON.stringify(data || {}, null, 2);
    await checkRecovery();
  }

  async function abandonRecoveryStep() {
    const runId = await ensureRun();
    const reason = page.querySelector("[data-swv2-recovery-abandon-reason]")?.value || "";
    const data = await postJson(`/admin/radius/setup-wizard/runs/${runId}/recovery/abandon-step`, {
      step_key: stepNames[current] || "current_step",
      reason,
    });
    if (recoveryJson) recoveryJson.textContent = JSON.stringify(data || {}, null, 2);
  }

  async function retireRecoveryRouter() {
    const runId = await ensureRun();
    const reason = page.querySelector("[data-swv2-recovery-abandon-reason]")?.value || "";
    const data = await postJson(`/admin/radius/setup-wizard/runs/${runId}/recovery/retire-router`, {
      reason,
    });
    if (recoveryJson) recoveryJson.textContent = JSON.stringify(data || {}, null, 2);
    await checkRecovery();
  }

  async function ensureRun() {
    if (currentRunId) return currentRunId;
    const data = await postJson("/admin/radius/setup-wizard/runs", {});
    currentRunId = Number(data.run && data.run.id) || 0;
    if (!currentRunId) throw new Error("تعذر إنشاء جلسة إعداد جديدة.");
    return currentRunId;
  }

  function field(name) {
    return page.querySelector(`[name="${name}"]`);
  }

  function value(name, fallback) {
    const input = field(name);
    const raw = input ? String(input.value || "").trim() : "";
    return raw || fallback || "";
  }

  function checked(name, fallback) {
    const input = field(name);
    return input ? Boolean(input.checked) : Boolean(fallback);
  }

  function buildInternetPayload() {
    if (selectedSource === "pppoe") {
      const payload = {
        interface: value("pppoe_interface", "ether1"),
        username: value("pppoe_username", ""),
        password: value("pppoe_password", ""),
        service_name: value("pppoe_service_name", ""),
        fixed_ip: value("pppoe_fixed_ip", ""),
        add_default_route: checked("pppoe_add_default_route", true),
        use_peer_dns: checked("pppoe_use_peer_dns", true),
        nat_enabled: checked("pppoe_nat_enabled", true),
      };
      return {
        source_type: "pppoe",
        selected_wan_interface: payload.interface,
        payload,
      };
    }

    if (selectedSource === "static") {
      const payload = {
        interface: value("static_interface", "ether1"),
        address_cidr: value("static_cidr", ""),
        gateway: value("static_gateway", ""),
        dns_servers: value("static_dns", ""),
        nat_enabled: checked("static_nat_enabled", true),
      };
      return {
        source_type: "static",
        selected_wan_interface: payload.interface,
        payload,
      };
    }

    if (selectedSource === "vlan") {
      const addressMode = value("vlan_mode", "dhcp");
      const payload = {
        parent_interface: value("vlan_parent", "ether1"),
        vlan_id: value("vlan_id", ""),
        vlan_name: value("vlan_name", ""),
        address_mode: addressMode,
        dns_servers: value("vlan_dns", ""),
        nat_enabled: checked("vlan_nat_enabled", true),
        add_default_route: checked("vlan_add_default_route", true),
        use_peer_dns: checked("vlan_use_peer_dns", true),
      };
      if (addressMode === "static") {
        payload.address_cidr = value("vlan_static_cidr", "");
        payload.gateway = value("vlan_gateway", "");
      }
      return {
        source_type: "vlan",
        selected_wan_interface: payload.parent_interface,
        payload,
      };
    }

    const payload = {
      interface: value("dhcp_interface", "ether1"),
      add_default_route: checked("dhcp_add_default_route", true),
      use_peer_dns: checked("dhcp_use_peer_dns", true),
      nat_enabled: checked("dhcp_nat_enabled", true),
    };
    return {
      source_type: "dhcp",
      selected_wan_interface: payload.interface,
      payload,
    };
  }

  function signatureFor(request) {
    return JSON.stringify(request);
  }

  function setScriptLoading(message) {
    if (scriptStatus) scriptStatus.textContent = message;
  }

  function setVpnScriptLoading(message) {
    if (vpnScriptStatus) vpnScriptStatus.textContent = message;
  }

  function renderInternetPlan(plan, request) {
    if (internetScript) {
      internetScript.textContent = plan.script_text || "-- لم يرجع الخادم سكربت --";
    }
    if (internetPlanJson) {
      internetPlanJson.textContent = JSON.stringify(
        {
          source_type: request.source_type,
          selected_wan_interface: request.selected_wan_interface,
          warnings: plan.warnings || [],
          generated_objects: plan.generated_objects || [],
          masked_sensitive_values: plan.masked_sensitive_values || {},
        },
        null,
        2
      );
    }
    setScriptLoading(`تم توليد سكربت ${request.source_type} من المحرك الحقيقي.`);
  }

  async function generateInternetScript(force) {
    const request = buildInternetPayload();
    const nextSignature = signatureFor(request);
    if (!force && internetPlanSignature === nextSignature && internetScript?.textContent.trim()) {
      return;
    }
    setScriptLoading("جاري تجهيز السكربت من محرك HobeRadius...");
    try {
      const runId = await ensureRun();
      const data = await postJson(`/admin/radius/setup-wizard/runs/${runId}/generate-internet-script`, request);
      internetPlanSignature = nextSignature;
      renderInternetPlan(data.plan || {}, request);
    } catch (error) {
      setScriptLoading(`فشل توليد السكربت: ${error.message}`);
      if (internetScript) internetScript.textContent = `-- ${error.message} --`;
    }
  }

  function buildVpnPayload() {
    return {
      router_label: value("vpn_router_label", "راوتر جديد"),
      router_identity: value("vpn_router_identity", ""),
      vps_public_endpoint: value("vpn_vps_endpoint", "187.77.70.18"),
      endpoint_port: Number(value("vpn_endpoint_port", "51820")) || 51820,
    };
  }

  function writeProvisioningValue(name, valueText) {
    const node = page.querySelector(`[data-swv2-provisioning="${name}"]`);
    if (node) node.textContent = valueText || "--";
  }

  function renderVpnPlan(plan) {
    const provisioning = plan.router_provisioning || {};
    const lifecycle = plan.provisioning_lifecycle || {};
    const peer = plan.prepared_wireguard_peer || lifecycle.prepared_wireguard_peer || {};
    if (vpnScript) {
      vpnScript.textContent = plan.script_text || "-- no VPN/RADIUS script returned --";
    }
    if (vpnPlanJson) {
      vpnPlanJson.textContent = JSON.stringify(
        {
          router_provisioning: provisioning,
          warnings: plan.warnings || [],
          generated_objects: plan.generated_objects || [],
          masked_sensitive_values: plan.masked_sensitive_values || {},
        },
        null,
        2
      );
    }
    writeProvisioningValue("router_vpn_ip", provisioning.router_vpn_ip);
    writeProvisioningValue("server_vpn_ip", provisioning.server_vpn_ip);
    writeProvisioningValue("peer_name", provisioning.wireguard_peer_name);
    writeProvisioningValue("api_username", provisioning.api_username);
    writeProvisioningValue("radius_secret", provisioning.masked_sensitive_values?.radius_secret || "***");
    writeProvisioningValue("registry_id", provisioning.id ? `#${provisioning.id}` : "--");
    writeProvisioningValue("lifecycle_state", lifecycle.current_state || provisioning.lifecycle_state || "script_generated");
    writeProvisioningValue("peer_status", peer.status || "waiting_router_key");
    if (routerKeyStatus) {
      routerKeyStatus.textContent = peer.status === "ready_to_apply"
        ? "تم التقاط مفتاح الربط وتجهيز خطة السيرفر."
        : "بعد تنفيذ السكربت، الصق المخرجات في خطوة التحقق وسنلتقط مفتاح الربط تلقائيًا.";
    }
    const warnings = plan.warnings || [];
    const missingServerKey = warnings.some((item) => String(item || "").includes("HOBERADIUS_WG_SERVER_PUBKEY"));
    setVpnScriptLoading(missingServerKey
      ? "مفتاح السيرفر غير مضبوط؛ لن يتم إنشاء peer حتى تضبط إعدادات WireGuard على الخادم."
      : "تم تجهيز بيانات الربط تلقائيًا لهذا الراوتر.");
  }

  async function generateVpnRadiusScript(force) {
    if (vpnPlanGenerated && !force && vpnScript?.textContent.trim()) return;
    setVpnScriptLoading("جاري حجز IP وبيانات ربط فريدة للراوتر...");
    try {
      const runId = await ensureRun();
      const data = await postJson(`/admin/radius/setup-wizard/runs/${runId}/generate-vpn-radius-script`, {
        payload: buildVpnPayload(),
      });
      vpnPlanGenerated = true;
      renderVpnPlan(data.plan || {});
    } catch (error) {
      setVpnScriptLoading(`فشل توليد سكربت VPN/RADIUS: ${error.message}`);
      if (vpnScript) vpnScript.textContent = `-- ${error.message} --`;
    }
  }

  function extractWireGuardPublicKey(outputText) {
    const text = String(outputText || "");
    const matches = text.match(/[A-Za-z0-9+/]{43}=/g) || [];
    return matches.find((item) => !/^A{20,}=?$/.test(item)) || "";
  }

  function isReservationMissing(error) {
    return String(error?.message || error || "").toLowerCase().includes("reservation not found");
  }

  async function submitRouterPublicKey(publicKeyOverride, retrying) {
    const input = page.querySelector("[data-swv2-router-public-key]");
    const publicKey = String(publicKeyOverride || (input ? input.value : "") || "").trim();
    if (!publicKey) {
      if (routerKeyStatus) routerKeyStatus.textContent = "لا تحتاج لإدخال مفتاح الآن. الصق مخرجات سكربت الربط في خطوة التحقق.";
      return;
    }
    if (routerPublicKeySubmitted && !retrying) return;
    if (!vpnPlanGenerated) {
      await generateVpnRadiusScript(true);
    }
    if (routerKeyStatus) routerKeyStatus.textContent = "جاري التقاط مفتاح الربط وتجهيز الخطة...";
    try {
      const runId = await ensureRun();
      const data = await postJson(`/admin/radius/setup-wizard/runs/${runId}/router-public-key`, {
        public_key: publicKey,
      });
      const provisioning = data.provisioning || {};
      const peer = provisioning.prepared_wireguard_peer || {};
      writeProvisioningValue("lifecycle_state", provisioning.current_state || "peer_ready");
      writeProvisioningValue("peer_status", peer.status || "ready_to_apply");
      routerPublicKeySubmitted = true;
      if (routerKeyStatus) {
        routerKeyStatus.textContent = `تم التقاط مفتاح الربط: ${peer.router_public_key_masked || "***"}`;
      }
      dryRunServerPeer();
    } catch (error) {
      if (isReservationMissing(error) && !retrying) {
        vpnPlanGenerated = false;
        await generateVpnRadiusScript(true);
        return submitRouterPublicKey(publicKey, true);
      }
      if (routerKeyStatus) {
        routerKeyStatus.textContent = "لم نتمكن من تجهيز مفتاح الربط تلقائيًا. أعد لصق مخرجات MikroTik التي تحتوي على public-key.";
      }
    }
  }

  function writeServerPeerResult(value) {
    if (serverPeerResult) {
      serverPeerResult.textContent = typeof value === "string"
        ? value
        : JSON.stringify(value || {}, null, 2);
    }
    const command = typeof value === "object" && value ? String(value.command_preview || "") : "";
    if (serverPeerSimple && command) {
      serverPeerSimple.hidden = false;
      if (serverPeerStatus) {
        serverPeerStatus.textContent = "تم تجهيز خطة الخادم. اضغط تجهيز الربط على الخادم لإكمال الخطوة من داخل HobeRadius.";
      }
    } else if (serverPeerSimple && typeof value === "string" && value.includes("تعذر")) {
      serverPeerSimple.hidden = false;
      if (serverPeerStatus) {
        serverPeerStatus.textContent = value;
      }
    }
  }

  function markServerPeerAlreadyConnected() {
    if (serverPeerSimple) serverPeerSimple.hidden = true;
    if (serverPeerStatus) {
      serverPeerStatus.textContent = "تم تأكيد الربط من مخرجات MikroTik. لا تحتاج دخول VPS أو خطوة خادم إضافية الآن.";
    }
    if (serverPeerResult) {
      serverPeerResult.textContent = "تم تأكيد الربط عبر ping/handshake. أكمل للخطوة التالية.";
    }
  }

  function readinessLabel(status) {
    if (status === "success" || status === "ready") return "جاهز";
    if (status === "warning" || status === "partial") return "ناقص";
    if (status === "disabled") return "معطل";
    return "محظور للأمان";
  }

  function updateReadinessCards(readiness) {
    const checks = readiness?.checks || {};
    page.querySelectorAll("[data-swv2-readiness-item]").forEach((node) => {
      const key = node.dataset.swv2ReadinessItem;
      const check = checks[key] || {};
      const status = check.status || readiness?.status || "disabled";
      const classStatus = status === "warning" ? "partial" : status;
      node.textContent = readinessLabel(status);
      node.classList.remove("is-ready", "is-partial", "is-blocked", "is-disabled");
      node.classList.add(classStatus === "success" ? "is-ready" : `is-${classStatus}`);
    });
    const applyButton = page.querySelector("[data-swv2-server-peer-apply]");
    if (applyButton) {
      const enabled = readiness?.status === "ready" && readiness?.flags?.all_required_for_apply === true;
      applyButton.disabled = !enabled;
      applyButton.textContent = enabled ? "Apply مختبري" : "Apply مختبري مغلق";
    }
  }

  async function checkServerWgReadiness() {
    if (serverWgReadinessResult) serverWgReadinessResult.textContent = "جاري فحص الجاهزية القراءة فقط...";
    try {
      const res = await fetch("/admin/radius/setup-wizard/server-wg/readiness", {
        headers: { "X-CSRFToken": token() },
      });
      const data = await res.json().catch(() => ({ ok: false, error: "invalid_json" }));
      if (!res.ok || data.ok === false) throw new Error(data.error || `HTTP ${res.status}`);
      updateReadinessCards(data.readiness || {});
      if (serverWgReadinessResult) {
        serverWgReadinessResult.textContent = JSON.stringify(data.readiness || {}, null, 2);
      }
    } catch (error) {
      if (serverWgReadinessResult) serverWgReadinessResult.textContent = `تعذر فحص الجاهزية: ${error.message}`;
    }
  }

  async function dryRunServerPeer() {
    writeServerPeerResult("جاري إنشاء تجربة جافة لخطة server peer...");
    try {
      const runId = await ensureRun();
      const data = await postJson(`/admin/radius/setup-wizard/runs/${runId}/server-peer/dry-run`, {});
      writeServerPeerResult({
        status: data.status,
        command_preview: data.plan?.command_preview,
        rollback_preview: data.plan?.rollback_preview,
        warnings: data.plan?.warnings || [],
      });
    } catch (error) {
      writeServerPeerResult(`تعذر إنشاء التجربة الجافة: ${error.message}`);
    }
  }

  async function verifyServerPeer() {
    const output = page.querySelector("[data-swv2-server-peer-output]");
    writeServerPeerResult("جاري تحليل wg show...");
    try {
      const runId = await ensureRun();
      const data = await postJson(`/admin/radius/setup-wizard/runs/${runId}/server-peer/verify`, {
        output: output ? output.value : "",
      });
      writeServerPeerResult(data);
      if (data.status === "success") {
        writeProvisioningValue("lifecycle_state", "vpn_verified");
      }
    } catch (error) {
      writeServerPeerResult(`تعذر التحقق: ${error.message}`);
    }
  }

  function formatBytes(value) {
    const number = Number(value || 0);
    if (!Number.isFinite(number) || number <= 0) return "--";
    if (number < 1024) return `${number} B`;
    if (number < 1024 * 1024) return `${(number / 1024).toFixed(1)} KiB`;
    return `${(number / 1024 / 1024).toFixed(1)} MiB`;
  }

  function peerHealthClass(status) {
    if (status === "healthy" || status === "verified_handshake") return "is-healthy";
    if (status === "missing_peer" || status === "allowed_ip_mismatch" || status === "duplicate_peer" || status === "misconfigured") return "is-danger";
    return "is-warning";
  }

  function writePeerHealthValue(name, valueText, status) {
    const node = page.querySelector(`[data-swv2-peer-health="${name}"]`);
    if (!node) return;
    node.textContent = valueText || "--";
    node.classList.remove("is-healthy", "is-warning", "is-danger");
    if (status) node.classList.add(peerHealthClass(status));
  }

  function renderPeerHealth(health) {
    const peer = health?.peer || {};
    const status = health?.status || "unknown";
    writePeerHealthValue("status", status, status);
    writePeerHealthValue("score", String(health?.health_score ?? "--"), status);
    writePeerHealthValue("handshake", peer.latest_handshake || "--", status);
    writePeerHealthValue("rx", formatBytes(peer.rx_bytes), status);
    writePeerHealthValue("tx", formatBytes(peer.tx_bytes), status);
    writePeerHealthValue("recommendation", health?.recommendation_ar || "--", status);
    if (serverPeerHealthResult) {
      serverPeerHealthResult.textContent = JSON.stringify(health || {}, null, 2);
    }
  }

  async function checkServerPeerHealth() {
    const output = page.querySelector("[data-swv2-server-peer-output]");
    if (serverPeerHealthResult) serverPeerHealthResult.textContent = "Checking peer health...";
    try {
      const runId = await ensureRun();
      const data = await postJson(`/admin/radius/setup-wizard/runs/${runId}/server-peer/health`, {
        output: output ? output.value : "",
      });
      renderPeerHealth(data.health || {});
    } catch (error) {
      renderPeerHealth({
        status: "unknown",
        health_score: 0,
        recommendation_ar: `تعذر فحص صحة peer: ${error.message}`,
        diagnostics: [{ code: "health_request_failed", explanation_ar: error.message }],
      });
    }
  }

  function serverPeerConfirmation() {
    const input = page.querySelector("[data-swv2-server-peer-confirmation]");
    return input ? String(input.value || "").trim() : "";
  }

  async function applyServerPeer(confirmationOverride) {
    writeServerPeerResult("جاري تجهيز الربط على الخادم عبر المسار المحروس...");
    try {
      const runId = await ensureRun();
      const data = await postJson(`/admin/radius/setup-wizard/runs/${runId}/server-peer/apply`, {
        confirmation: confirmationOverride || serverPeerConfirmation(),
      });
      writeServerPeerResult(data);
      if (serverPeerStatus) {
        serverPeerStatus.textContent = data.status === "applied_no_handshake"
          ? "تمت إضافة Peer على الخادم. انتظر handshake ثم أعد التحقق."
          : "تم تجهيز الربط على الخادم بنجاح.";
      }
      const rollbackButton = page.querySelector("[data-swv2-server-peer-rollback]");
      if (rollbackButton && data.status !== "failed_verification") rollbackButton.disabled = false;
    } catch (error) {
      const message = friendlyWizardError(error.message);
      writeServerPeerResult(`تعذر تجهيز الربط على الخادم: ${message}`);
      if (serverPeerStatus) serverPeerStatus.textContent = message;
    }
  }

  async function simpleApplyServerPeer() {
    if (serverPeerStatus) {
      serverPeerStatus.textContent = "جاري تجهيز خطة آمنة ثم تنفيذ الربط على الخادم...";
    }
    try {
      const runId = await ensureRun();
      await postJson(`/admin/radius/setup-wizard/runs/${runId}/server-peer/dry-run`, {});
    } catch (error) {
      const message = friendlyWizardError(error.message);
      writeServerPeerResult(`تعذر تجهيز خطة الخادم: ${message}`);
      if (serverPeerStatus) serverPeerStatus.textContent = message;
      return;
    }
    await applyServerPeer("APPLY SERVER PEER IN LAB");
  }

  async function rollbackServerPeer() {
    writeServerPeerResult("جاري طلب rollback drill...");
    try {
      const runId = await ensureRun();
      const data = await postJson(`/admin/radius/setup-wizard/runs/${runId}/server-peer/rollback`, {
        confirmation: serverPeerConfirmation(),
      });
      writeServerPeerResult(data);
    } catch (error) {
      writeServerPeerResult(`تم حظر rollback: ${error.message}`);
    }
  }

  function splitList(valueText) {
    return String(valueText || "")
      .split(",")
      .map((item) => item.trim())
      .filter(Boolean);
  }

  function updateServiceCards() {
    const locked = !vpnVerified;
    const lockNote = page.querySelector("[data-swv2-service-lock]");
    if (lockNote) {
      lockNote.textContent = locked
        ? "مقفلة حتى ينجح تحقق VPN/RADIUS."
        : "تم فتح اختيار الخدمات. اختر المسار المناسب.";
      lockNote.classList.toggle("is-unlocked", !locked);
    }
    page.querySelectorAll("[data-service-path]").forEach((card) => {
      card.classList.toggle("is-locked", locked);
      card.disabled = locked;
      card.classList.toggle("is-selected", card.dataset.servicePath === selectedServicePath);
    });
  }

  function setServicePath(path) {
    if (!vpnVerified) {
      updateServiceCards();
      return;
    }
    selectedServicePath = path || "";
    const label = page.querySelector("[data-swv2-selected-service]");
    if (label) {
      const names = {
        hotspot: "تم اختيار Hotspot.",
        broadband: "تم اختيار Broadband / PPPoE.",
        both: "تم اختيار Hotspot ثم Broadband بالتتابع.",
        skip: "تم تخطي الخدمات الآن.",
      };
      label.textContent = names[selectedServicePath] || "لم يتم اختيار مسار بعد.";
    }
    updateServiceCards();
    if (selectedServicePath === "skip") {
      const finalIdx = stepNames.indexOf("final-summary");
      if (finalIdx >= 0) showStep(finalIdx);
    } else {
      const pickerIdx = stepNames.indexOf("interface-picker");
      if (pickerIdx >= 0) showStep(pickerIdx);
    }
  }

  function renderInterfaces(candidates) {
    const container = page.querySelector("[data-swv2-interface-picker]");
    if (!container) return;
    const rows = normalizeInterfaceRows(Array.isArray(candidates) && candidates.length ? candidates : defaultInterfaceRows());
    container.innerHTML = "";
    rows.forEach((item) => {
      const button = document.createElement("button");
      const name = String(item.name || "");
      const unsafe = item.safe === false || item.excluded === true || ["ether1", "hr-wg"].includes(name);
      button.type = "button";
      button.className = `swv2-interface-card ${unsafe ? "is-disabled" : "is-recommended"} ${selectedInterfaces.includes(name) ? "is-selected" : ""}`;
      button.dataset.interfaceName = name;
      button.disabled = unsafe;
      const state = item.running === false ? "غير نشط" : "متصل";
      const reason = unsafe ? (item.reason_ar || item.reason || "مستبعد لحماية WAN/VPN") : (item.reason_ar || item.reason || "واجهة LAN مرشحة للخدمة");
      button.innerHTML = `<strong>${name}</strong><span>${item.kind || "ether"} · ${state}</span><small>${reason}</small>`;
      container.appendChild(button);
    });
    updateInterfaceSummary();
  }

  function defaultInterfaceRows() {
    const rows = Array.from({ length: 8 }, (_, idx) => ({
      name: `ether${idx + 1}`,
      kind: "ether",
      running: true,
      safe: idx !== 0,
      excluded: idx === 0,
      reason_ar: idx === 0 ? "مستبعد عادة لأنه منفذ الإنترنت WAN" : "واجهة LAN مرشحة للخدمة",
    }));
    rows.push({ name: "hr-wg", kind: "wireguard", running: true, safe: false, excluded: true, reason_ar: "مستبعد لأنه نفق الإدارة VPN" });
    return rows;
  }

  function normalizeInterfaceRows(rows) {
    const seen = new Set();
    return rows.filter((item) => {
      const name = String(item && item.name || "").trim();
      if (!name || seen.has(name)) return false;
      seen.add(name);
      return true;
    });
  }

  async function loadInterfaceCandidates() {
    try {
      const runId = await ensureRun();
      const data = await postJson(`/admin/radius/setup-wizard/runs/${runId}/interfaces/candidates`, {});
      renderInterfaces(data.candidates || []);
    } catch (_) {
      renderInterfaces([]);
    }
  }

  function selectInterface(name) {
    if (!name) return;
    const set = new Set(selectedInterfaces);
    if (set.has(name)) set.delete(name);
    else set.add(name);
    selectedInterfaces = Array.from(set);
    page.querySelectorAll("[data-interface-name]").forEach((card) => {
      card.classList.toggle("is-selected", selectedInterfaces.includes(card.dataset.interfaceName));
    });
    const text = selectedInterfaces.join(",");
    ["hotspot_interfaces", "broadband_interfaces"].forEach((fieldName) => {
      const input = field(fieldName);
      if (input && text) input.value = text;
    });
    updateInterfaceSummary();
  }

  function updateInterfaceSummary() {
    const summary = page.querySelector("[data-swv2-interface-summary]");
    if (!summary) return;
    if (!selectedInterfaces.length) {
      summary.textContent = "اختر منفذًا واحدًا أو أكثر. يمكنك تحديد عدة منافذ LAN للخدمة نفسها.";
      return;
    }
    summary.textContent = `تم اختيار ${selectedInterfaces.length} منفذ: ${selectedInterfaces.join(", ")}`;
  }

  function setServiceMode(service, mode) {
    serviceModes[service] = mode || "smart";
    page.querySelectorAll(`[data-mode-target="${service}"]`).forEach((button) => {
      button.classList.toggle("is-selected", button.dataset.modeValue === serviceModes[service]);
    });
  }

  function buildServicePayload(service) {
    if (service === "hotspot") {
      const names = splitList(value("hotspot_names", "hs-bridge,hs-profile,hs-server"));
      return {
        selected_interfaces: splitList(value("hotspot_interfaces", selectedInterfaces.join(",") || "ether2")),
        network_cidr: value("hotspot_network_cidr", "10.77.50.0/24"),
        pool_range: value("hotspot_pool_range", "10.77.50.20-10.77.50.220"),
        dns_name: value("hotspot_dns_name", "login.hoberadius.local"),
        bridge_name: names[0] || "hs-bridge",
        profile_name: names[1] || "hs-profile",
        server_name: names[2] || "hs-server",
        nat_enabled: checked("hotspot_nat_enabled", true),
      };
    }
    return {
      selected_interfaces: splitList(value("broadband_interfaces", selectedInterfaces.join(",") || "ether2")),
      service_name: value("broadband_service_name", "hoberadius-pppoe"),
      local_address: value("broadband_local_address", "10.88.44.1"),
      remote_pool_cidr: value("broadband_remote_pool_cidr", "10.88.44.0/24"),
      profile_name: value("broadband_profile_name", "hr-pppoe-profile"),
      dns_servers: value("broadband_dns", "1.1.1.1,8.8.8.8"),
      nat_enabled: checked("broadband_nat_enabled", true),
    };
  }

  function renderServicePlan(service, plan) {
    const script = document.getElementById(`${service}-script-code`);
    const status = page.querySelector(`[data-swv2-service-status="${service}"]`);
    const details = page.querySelector(`[data-swv2-service-json="${service}"]`);
    if (script) script.textContent = plan.script_text || "-- no script returned --";
    if (status) status.textContent = `تم توليد سكربت ${service} من المحرك الحقيقي.`;
    if (details) {
      details.textContent = JSON.stringify({
        computed: plan.computed || {},
        warnings: plan.warnings || [],
        generated_objects: plan.generated_objects || [],
      }, null, 2);
    }
  }

  async function generateServiceScript(service) {
    const status = page.querySelector(`[data-swv2-service-status="${service}"]`);
    if (status) status.textContent = "جاري توليد السكربت...";
    try {
      const runId = await ensureRun();
      const data = await postJson(`/admin/radius/setup-wizard/runs/${runId}/generate-${service}-script`, {
        mode: serviceModes[service] || "smart",
        payload: buildServicePayload(service),
        blocked_network_cidrs: ["10.10.0.0/24", "10.20.30.0/24"],
      });
      servicePlans[service] = data.plan || {};
      renderServicePlan(service, servicePlans[service]);
    } catch (error) {
      const message = friendlyWizardError(error.message);
      if (status) status.textContent = `تعذر توليد السكربت: ${message}`;
      renderServiceDiagnostics(service, null, "لا يمكن توليد السكربت الآن", message);
    }
  }

  function renderServiceDiagnostics(service, result, failedMessage, failedBody) {
    const target = page.querySelector(`[data-swv2-service-diagnostics="${service}"]`);
    if (!target) return;
    const ok = result?.status === "success" || result?.gate_unlocked === true || result?.status === "dry_run_ready";
    target.innerHTML = "";
    const card = document.createElement("div");
    card.className = `swv2-diagnostic-card ${ok ? "is-success" : "is-failed"}`;
    const title = document.createElement("strong");
    const body = document.createElement("span");
    title.textContent = ok ? `تم تجهيز ${service} بنجاح` : failedMessage;
    body.textContent = ok ? "راجع الملخص والتفاصيل المتقدمة قبل التنفيذ اليدوي." : (failedBody || "راجع التحذيرات أو ألصق مخرجات أوضح للتحقق.");
    card.append(title, body);
    target.appendChild(card);
  }

  async function dryRunService(service) {
    try {
      if (!servicePlans[service]) {
        renderServiceDiagnostics(service, null, "المراجعة الجافة غير جاهزة", "ولّد السكربت أولًا، ثم شغّل المراجعة الجافة.");
        return;
      }
      const runId = await ensureRun();
      const data = await postJson(`/admin/radius/setup-wizard/runs/${runId}/dry-run/${service}`, {});
      renderServiceDiagnostics(service, { status: data.status }, "تعذر إنشاء التجربة الجافة");
      const details = page.querySelector(`[data-swv2-service-json="${service}"]`);
      if (details) details.textContent = JSON.stringify(data, null, 2);
    } catch (error) {
      renderServiceDiagnostics(service, null, "المراجعة الجافة غير جاهزة", friendlyWizardError(error.message));
    }
  }

  async function verifyService(service) {
    const output = page.querySelector(`[data-swv2-service-output="${service}"]`);
    try {
      const runId = await ensureRun();
      const data = await postJson(`/admin/radius/setup-wizard/runs/${runId}/verify-${service}`, {
        mode: "pasted_output",
        output: output ? output.value : "",
      });
      renderServiceDiagnostics(service, data, `لم يكتمل تحقق ${service}`);
    } catch (error) {
      renderServiceDiagnostics(service, null, `تعذر التحقق: ${error.message}`);
    }
  }

  function renderAddedDiagnostics(result, fallbackTitle) {
    const target = page.querySelector("[data-swv2-added-diagnostics]");
    if (!target) return;
    target.innerHTML = "";
    const ok = ["preview", "partial", "dry_run_ready"].includes(result?.plan_status || result?.status);
    const card = document.createElement("div");
    card.className = `swv2-diagnostic-card ${ok ? "is-success" : "is-failed"}`;
    const title = document.createElement("strong");
    const body = document.createElement("span");
    title.textContent = ok ? "تم تجهيز خطة الخدمة" : fallbackTitle;
    body.textContent = ok
      ? "الخطة معاينة آمنة فقط وتستخدم المحركات الموجودة."
      : "الخدمة غير مدعومة أو تحتاج مدخلات إضافية.";
    card.append(title, body);
    target.appendChild(card);
  }

  function addedListValue() {
    const fieldEl = page.querySelector('[name="added_domains"]');
    return splitList(fieldEl ? fieldEl.value : "");
  }

  function buildAddedInputs(serviceKey) {
    const items = addedListValue();
    const wg = value("added_wg_interface", "hr-wg");
    if (serviceKey === "site_exit_public_ip") {
      return { destinations: items.length ? items : ["speedtest.net"], wireguard_interface_name: wg };
    }
    if (serviceKey === "block_sites") {
      return { domains: items.length ? items : ["example-bad-site.test"] };
    }
    if (serviceKey === "walled_garden") {
      return { domains: items.length ? items : ["hoberadius.local"] };
    }
    return {};
  }

  function updateAddedCards() {
    page.querySelectorAll("[data-added-service]").forEach((card) => {
      card.classList.toggle("is-selected", card.dataset.addedService === selectedAddedService);
    });
  }

  function renderAddedPlan(data) {
    const plan = data.plan || data;
    const preview = page.querySelector("[data-swv2-added-preview]");
    const status = page.querySelector("[data-swv2-added-status]");
    const details = page.querySelector("[data-swv2-added-json]");
    if (preview) preview.textContent = plan.script_preview || plan.rollback_notes || "-- no script generated --";
    if (status) {
      status.textContent = `${plan.service_key || selectedAddedService} · ${plan.plan_status || plan.status || "preview"}`;
    }
    if (details) details.textContent = JSON.stringify(data, null, 2);
    renderAddedDiagnostics(plan, "لم تكتمل خطة الخدمة");
  }

  async function loadAddedServicesCatalog() {
    try {
      const data = await getJson("/admin/radius/setup-wizard/added-services/catalog");
      addedServicesCatalog = data;
      const details = page.querySelector("[data-swv2-added-json]");
      if (details) details.textContent = JSON.stringify(data, null, 2);
      Object.values(data.services || {}).forEach(() => {});
      (data.services || []).forEach((service) => {
        const badge = page.querySelector(`[data-added-status="${service.key}"]`);
        if (badge) badge.textContent = service.status || (service.supported ? "supported" : "not supported");
      });
    } catch (error) {
      renderAddedDiagnostics(null, `تعذر تحميل الكتالوج: ${error.message}`);
    }
  }

  async function planAddedService() {
    try {
      const runId = await ensureRun();
      selectedAddedInputs = buildAddedInputs(selectedAddedService);
      const data = await postJson(`/admin/radius/setup-wizard/runs/${runId}/added-services/plan`, {
        service_key: selectedAddedService,
        inputs: selectedAddedInputs,
      });
      renderAddedPlan(data);
    } catch (error) {
      renderAddedDiagnostics(null, `تعذر توليد الخطة: ${error.message}`);
    }
  }

  async function dryRunAddedService() {
    try {
      const runId = await ensureRun();
      const data = await postJson(`/admin/radius/setup-wizard/runs/${runId}/added-services/dry-run`, {
        service_key: selectedAddedService,
        inputs: selectedAddedInputs && Object.keys(selectedAddedInputs).length
          ? selectedAddedInputs
          : buildAddedInputs(selectedAddedService),
      });
      renderAddedPlan(data);
    } catch (error) {
      renderAddedDiagnostics(null, `التجربة الجافة محظورة: ${error.message}`);
    }
  }

  async function verifyAddedService() {
    try {
      const runId = await ensureRun();
      const data = await postJson(`/admin/radius/setup-wizard/runs/${runId}/added-services/verify`, {
        service_key: selectedAddedService,
      });
      renderAddedPlan(data);
    } catch (error) {
      renderAddedDiagnostics(null, `تعذر جلب إرشادات التحقق: ${error.message}`);
    }
  }

  function applyAddedPreset(presetKey) {
    const preset = addedServicesCatalog?.presets?.[presetKey];
    if (!preset) {
      selectedAddedService = presetKey === "gaming_center" ? "site_exit_public_ip" : "walled_garden";
      updateAddedCards();
      return;
    }
    selectedAddedService = (preset.services || ["walled_garden"])[0] || "walled_garden";
    const presetInputs = preset.inputs?.[selectedAddedService] || {};
    selectedAddedInputs = presetInputs;
    const domainField = page.querySelector('[name="added_domains"]');
    if (domainField) {
      const values = presetInputs.domains || presetInputs.destinations || [];
      domainField.value = Array.isArray(values) ? values.join("\n") : String(values || "");
    }
    updateAddedCards();
  }

  function showStep(index) {
    current = Math.max(0, Math.min(index, steps.length - 1));
    steps.forEach((step, idx) => {
      step.classList.toggle("is-active", idx === current);
    });
    stepperItems.forEach((item, idx) => {
      item.classList.toggle("is-current", idx === current);
      item.classList.toggle("is-completed", idx < current);
      item.classList.toggle("is-locked", idx > current);
      const marker = item.querySelector(".swv2-step-index");
      if (marker) marker.textContent = idx < current ? "✓" : String(idx + 1);
    });
    if (count) count.textContent = `${current + 1} / ${steps.length}`;
    if (prev) prev.disabled = current === 0;
    if (next) next.textContent = current === steps.length - 1 ? "إنهاء" : "التالي";

    if (stepNames[current] === "internet-script") {
      generateInternetScript(false);
    } else if (stepNames[current] === "vpn-script") {
      generateVpnRadiusScript(false);
    } else if (stepNames[current] === "service-path") {
      updateServiceCards();
    } else if (stepNames[current] === "added-services") {
      updateAddedCards();
    }
  }

  function setSource(type) {
    selectedSource = type || "dhcp";
    internetPlanSignature = "";
    sourceCards.forEach((card) => {
      card.classList.toggle("is-selected", card.dataset.sourceType === selectedSource);
    });
    sourceForms.forEach((form) => {
      form.classList.toggle("is-active", form.dataset.sourceForm === selectedSource);
    });
  }

  function copyCode(id, button) {
    const target = document.getElementById(id);
    if (!target) return;
    const text = (target.innerText || target.textContent || "").trimEnd();
    if (!text) {
      flashButton(button, "لا يوجد نص");
      return;
    }
    if (navigator.clipboard && window.isSecureContext) {
      navigator.clipboard.writeText(text).then(
        () => flashButton(button, "تم النسخ"),
        () => fallbackCopy(text, button)
      );
      return;
    }
    fallbackCopy(text, button);
  }

  function fallbackCopy(text, button) {
    const area = document.createElement("textarea");
    area.value = text;
    area.setAttribute("readonly", "readonly");
    area.style.position = "fixed";
    area.style.top = "-9999px";
    area.style.opacity = "0";
    document.body.appendChild(area);
    area.focus();
    area.select();
    try {
      const copied = document.execCommand("copy");
      flashButton(button, copied ? "تم النسخ" : "تعذر النسخ");
    } catch (_) {
      flashButton(button, "تعذر النسخ");
    } finally {
      area.remove();
    }
  }

  function flashButton(button, label) {
    if (!button) return;
    const original = button.textContent;
    button.textContent = label;
    window.setTimeout(() => {
      button.textContent = original;
    }, 1200);
  }

  async function verifyWithBackend(kind, outputText, localOk) {
    if (!localOk || !currentRunId) return localOk;
    const endpoint = kind === "internet" ? "verify-internet" : "verify-vpn-radius";
    try {
      const data = await postJson(`/admin/radius/setup-wizard/runs/${currentRunId}/${endpoint}`, {
        mode: "pasted_output",
        output: outputText,
      });
      const backendOk = Boolean(data.gate_unlocked || data.status === "success");
      return backendOk || (kind === "vpn" && localOk);
    } catch (_) {
      return kind === "vpn" ? localOk : false;
    }
  }

  function hasUsefulPing(outputText) {
    const text = String(outputText || "").toLowerCase();
    const receivedMatch = text.match(/received=(\d+)/);
    if (receivedMatch) return Number(receivedMatch[1]) > 0;
    if (text.includes("packet-loss=0") || text.includes("0% packet loss")) return true;
    const unixMatch = text.match(/\b\d+\s+packets transmitted,\s*(\d+)\s+(?:packets\s+)?received\b/);
    if (unixMatch) return Number(unixMatch[1]) > 0;
    return false;
  }

  async function analyzeOutput(kind) {
    const output = page.querySelector(`[data-swv2-verify-output="${kind}"]`);
    const diagnostics = page.querySelector(`[data-swv2-diagnostics="${kind}"]`);
    const success = page.querySelector(`[data-swv2-success="${kind}"]`);
    if (!output || !diagnostics) return;

    const valueText = output.value.toLowerCase();
    const hasPingSuccess = hasUsefulPing(output.value);
    const capturedPublicKey = kind === "vpn" ? extractWireGuardPublicKey(output.value) : "";
    if (capturedPublicKey) {
      await submitRouterPublicKey(capturedPublicKey);
    }
    const hasHandshakeSuccess = kind === "vpn" && /latest[-\s]handshake\s*[:=]\s*(?!never|0\b|\(none\))/i.test(output.value);
    const hasVpnSignal = kind !== "vpn" || hasPingSuccess || hasHandshakeSuccess || valueText.includes("radius") || Boolean(capturedPublicKey);
    let ok = kind === "vpn" ? (hasPingSuccess || hasHandshakeSuccess) && hasVpnSignal : hasPingSuccess && hasVpnSignal;
    ok = await verifyWithBackend(kind, output.value, ok);

    diagnostics.innerHTML = "";
    if (success) success.hidden = !ok;
    const card = document.createElement("div");
    card.className = `swv2-diagnostic-card ${ok ? "is-success" : "is-failed"}`;
    const title = document.createElement("strong");
    const body = document.createElement("span");
    if (ok) {
      title.textContent = kind === "vpn" ? "تم رصد إشارات الربط بنجاح" : "نتيجة الإنترنت ناجحة";
      body.textContent = "المخرجات تحتوي على مؤشرات نجاح واضحة. أكمل للخطوة التالية.";
      if (success) success.hidden = false;
      if (kind === "vpn") markServerPeerAlreadyConnected();
      unlockNextStep(kind);
    } else {
      title.textContent = kind === "vpn" ? "لم تكتمل إشارات الربط" : "تعذر تأكيد الإنترنت";
      body.textContent = kind === "vpn"
        ? "الصق مخرجات سكربت الربط أو نتيجة handshake/ping. سنلتقط مفتاح الربط تلقائيًا إن كان موجودًا."
        : "راجع مخرجات ping. يكفي وصول رد واحد من الإنترنت للمتابعة، لكن انقطاع كامل أو no route يحتاج فحص الواجهة.";
    }
    card.append(title, body);
    diagnostics.appendChild(card);
  }

  function unlockNextStep(kind) {
    const currentName = kind === "internet" ? "internet-verify" : "vpn-verify";
    if (kind === "vpn") {
      vpnVerified = true;
      updateServiceCards();
    }
    const idx = stepNames.indexOf(currentName);
    if (idx >= 0 && idx + 1 < stepperItems.length) {
      stepperItems[idx + 1].classList.remove("is-locked");
    }
  }

  page.addEventListener("click", (event) => {
    const target = event.target.closest("button, a");
    if (!target) return;

    if (target.matches("[data-swv2-next]")) {
      showStep(current + 1);
    } else if (target.matches("[data-swv2-prev]")) {
      showStep(current - 1);
    } else if (target.matches("[data-swv2-go]")) {
      const stepName = target.dataset.swv2Go;
      const idx = stepNames.indexOf(stepName);
      if (idx >= 0) showStep(idx);
    } else if (target.matches("[data-source-type]")) {
      setSource(target.dataset.sourceType);
    } else if (target.matches("[data-copy-target]")) {
      copyCode(target.dataset.copyTarget, target);
    } else if (target.matches("[data-swv2-generate-internet]")) {
      generateInternetScript(true);
    } else if (target.matches("[data-swv2-generate-vpn]")) {
      generateVpnRadiusScript(true);
    } else if (target.matches("[data-swv2-submit-router-key]")) {
      submitRouterPublicKey();
    } else if (target.matches("[data-swv2-server-wg-readiness-check]")) {
      checkServerWgReadiness();
    } else if (target.matches("[data-swv2-server-peer-dry-run]")) {
      dryRunServerPeer();
    } else if (target.matches("[data-swv2-server-peer-simple-apply]")) {
      simpleApplyServerPeer();
    } else if (target.matches("[data-swv2-server-peer-verify]")) {
      verifyServerPeer();
    } else if (target.matches("[data-swv2-server-peer-health]")) {
      checkServerPeerHealth();
    } else if (target.matches("[data-swv2-server-peer-apply]")) {
      applyServerPeer();
    } else if (target.matches("[data-swv2-server-peer-rollback]")) {
      rollbackServerPeer();
    } else if (target.matches("[data-service-path]")) {
      setServicePath(target.dataset.servicePath);
    } else if (target.matches("[data-swv2-load-interfaces]")) {
      loadInterfaceCandidates();
    } else if (target.matches("[data-interface-name]")) {
      selectInterface(target.dataset.interfaceName);
    } else if (target.matches("[data-mode-target]")) {
      setServiceMode(target.dataset.modeTarget, target.dataset.modeValue);
    } else if (target.matches("[data-swv2-generate-service]")) {
      generateServiceScript(target.dataset.swv2GenerateService);
    } else if (target.matches("[data-swv2-service-dry-run]")) {
      dryRunService(target.dataset.swv2ServiceDryRun);
    } else if (target.matches("[data-swv2-service-verify]")) {
      verifyService(target.dataset.swv2ServiceVerify);
    } else if (target.matches("[data-swv2-load-added-services]")) {
      loadAddedServicesCatalog();
    } else if (target.matches("[data-added-service]")) {
      selectedAddedService = target.dataset.addedService;
      updateAddedCards();
    } else if (target.matches("[data-added-preset]")) {
      applyAddedPreset(target.dataset.addedPreset);
    } else if (target.matches("[data-swv2-plan-added-service]")) {
      planAddedService();
    } else if (target.matches("[data-swv2-added-dry-run]")) {
      dryRunAddedService();
    } else if (target.matches("[data-swv2-added-verify]")) {
      verifyAddedService();
    } else if (target.matches("[data-swv2-recovery-check]")) {
      checkRecovery();
    } else if (target.matches("[data-swv2-recovery-resume]")) {
      resumeRecovery();
    } else if (target.matches("[data-swv2-recovery-retry]")) {
      retryRecoveryVerification();
    } else if (target.matches("[data-swv2-recovery-regenerate]")) {
      regenerateRecoveryScript();
    } else if (target.matches("[data-swv2-recovery-abandon]")) {
      abandonRecoveryStep();
    } else if (target.matches("[data-swv2-recovery-retire]")) {
      retireRecoveryRouter();
    } else if (target.matches("[data-swv2-recovery-support]")) {
      if (!currentRunId) event.preventDefault();
    } else if (target.matches("[data-swv2-verify]")) {
      analyzeOutput(target.dataset.swv2Verify);
    } else if (target.matches("[data-swv2-step-target]")) {
      const idx = stepNames.indexOf(target.dataset.swv2StepTarget);
      if (idx >= 0 && idx <= current) showStep(idx);
    }
  });

  page.addEventListener("input", (event) => {
    if (event.target.closest("[data-swv2-internet-form]")) {
      internetPlanSignature = "";
    }
    if (event.target.closest("[data-swv2-vpn-form]")) {
      vpnPlanGenerated = false;
    }
  });

  setSource(selectedSource);
  showStep(0);
})();
