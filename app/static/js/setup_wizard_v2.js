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
  const stepNames = steps.map((step) => step.dataset.swv2Step);
  let current = 0;
  let selectedSource = "dhcp";
  let currentRunId = 0;
  let internetPlanSignature = "";
  let vpnPlanGenerated = false;

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
      router_label: value("vpn_router_label", "router"),
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
    setVpnScriptLoading("تم توليد بيانات ربط فريدة لهذا الراوتر من سجل provisioning.");
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
    const text = target.textContent || "";
    navigator.clipboard?.writeText(text).then(
      () => flashButton(button, "تم النسخ"),
      () => fallbackCopy(text, button)
    );
  }

  function fallbackCopy(text, button) {
    const area = document.createElement("textarea");
    area.value = text;
    area.style.position = "fixed";
    area.style.opacity = "0";
    document.body.appendChild(area);
    area.select();
    try {
      document.execCommand("copy");
      flashButton(button, "تم النسخ");
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
      return Boolean(data.gate_unlocked || data.status === "success");
    } catch (_) {
      return false;
    }
  }

  async function analyzeOutput(kind) {
    const output = page.querySelector(`[data-swv2-verify-output="${kind}"]`);
    const diagnostics = page.querySelector(`[data-swv2-diagnostics="${kind}"]`);
    const success = page.querySelector(`[data-swv2-success="${kind}"]`);
    if (!output || !diagnostics) return;

    const valueText = output.value.toLowerCase();
    const hasPingSuccess =
      valueText.includes("received=5") ||
      valueText.includes("packet-loss=0") ||
      valueText.includes("0% packet loss");
    const hasVpnSignal = kind !== "vpn" || valueText.includes("handshake") || valueText.includes("radius");
    let ok = hasPingSuccess && hasVpnSignal;
    ok = await verifyWithBackend(kind, output.value, ok);

    diagnostics.innerHTML = "";
    const card = document.createElement("div");
    card.className = `swv2-diagnostic-card ${ok ? "is-success" : "is-failed"}`;
    const title = document.createElement("strong");
    const body = document.createElement("span");
    if (ok) {
      title.textContent = kind === "vpn" ? "تم رصد إشارات الربط بنجاح" : "نتيجة الإنترنت ناجحة";
      body.textContent = "المخرجات تحتوي على مؤشرات نجاح واضحة. أكمل للخطوة التالية.";
      if (success) success.hidden = false;
      unlockNextStep(kind);
    } else {
      title.textContent = kind === "vpn" ? "لم تكتمل إشارات الربط" : "تعذر تأكيد الإنترنت";
      body.textContent = "راجع المخرجات. نحتاج ping ناجح، وفي خطوة الربط نحتاج أيضًا handshake أو إشارات RADIUS.";
      if (success) success.hidden = true;
    }
    card.append(title, body);
    diagnostics.appendChild(card);
  }

  function unlockNextStep(kind) {
    const currentName = kind === "internet" ? "internet-verify" : "vpn-verify";
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
