/* Setup Wizard v3 — operator-friendly multi-step driver.
 *
 * No JSON inputs. No raw code fields. Each step is a clean
 * form; the JS collects values, calls the appropriate backend
 * endpoint, and shows the generated script with a one-click
 * copy button. Step 5 is optional per-service cards.
 *
 * Backend endpoints used:
 *   POST /admin/radius/setup-wizard-v3/runs
 *   POST /admin/radius/setup-wizard-v3/runs/<id>/router-info
 *   POST /admin/radius/setup-wizard-v3/runs/<id>/phase-plan/<phase>
 *   POST /admin/radius/setup-wizard-v3/runs/<id>/generate-script
 *   POST /admin/radius/setup-wizard-v3/runs/<id>/submit-key
 *   POST /admin/radius/setup-wizard-v3/runs/<id>/apply-server-peer
 *   POST /admin/radius/setup-wizard-v3/runs/<id>/mark-handshake
 *   POST /admin/radius/setup-wizard-v3/runs/<id>/register
 */
(function () {
  "use strict";

  const root = document.querySelector("[data-swz]");
  if (!root) return;

  const state = {
    runId: 0,
    currentStep: 1,
    scripts: {},   // step => script text
  };

  // ─── Helpers ─────────────────────────────────────────

  function csrf() {
    const node = document.querySelector('input[name="_csrf_token"]');
    return node ? node.value : "";
  }

  function toast(message, kind = "info") {
    const node = root.querySelector("[data-swz-toast]");
    if (!node) return;
    node.hidden = false;
    node.textContent = message;
    node.className = `swz-toast swz-toast-${kind}`;
    if (kind !== "error") {
      window.setTimeout(() => (node.hidden = true), 4000);
    }
  }

  async function api(method, path, body) {
    const res = await fetch(
      `/admin/radius/setup-wizard-v3${path}`,
      {
        method,
        headers: {
          "Content-Type": "application/json",
          "X-CSRFToken": csrf(),
        },
        body: body ? JSON.stringify(body) : undefined,
      },
    );
    const data = await res.json().catch(() => ({ ok: false, error: "invalid_json" }));
    if (!res.ok || data.ok === false) {
      const message = data.error || `HTTP ${res.status}`;
      throw new Error(message);
    }
    return data;
  }

  function showStep(n) {
    state.currentStep = n;
    root.querySelectorAll("[data-swz-step]").forEach((sec) => {
      sec.classList.toggle(
        "is-active",
        Number(sec.dataset.swzStep) === n,
      );
    });
    root.querySelectorAll("[data-swz-rail-step]").forEach((li) => {
      const step = Number(li.dataset.swzRailStep);
      li.classList.toggle("is-current", step === n);
      li.classList.toggle("is-done", step < n);
    });
    // On Step 6, auto-fill the API user/password from the
    // credentials the unified script baked into the router.
    // Operator just presses 'register'.
    if (n === 6 && state.apiUser) {
      const u = root.querySelector("[data-swz-api-user]");
      const p = root.querySelector("[data-swz-api-pass]");
      if (u) u.value = state.apiUser;
      if (p) p.value = state.apiPassword || "";
    }
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  function setBusy(btn, busy, busyText) {
    if (!btn) return;
    if (busy) {
      btn.dataset._origText = btn.textContent;
      btn.textContent = busyText || "جارٍ المعالجة...";
      btn.disabled = true;
    } else {
      btn.textContent = btn.dataset._origText || btn.textContent;
      btn.disabled = false;
    }
  }

  function getValue(selector) {
    const el = root.querySelector(selector);
    if (!el) return "";
    if (el.type === "checkbox") return el.checked;
    return (el.value || "").trim();
  }

  function getChecked(name) {
    const el = root.querySelector(`input[name="${name}"]:checked`);
    return el ? el.value : "";
  }

  async function ensureRun() {
    if (state.runId) return state.runId;
    const data = await api("POST", "/runs", {});
    state.runId = Number(data.run.id);
    return state.runId;
  }

  function showScript(target, body) {
    const card = root.querySelector(`[data-swz-${target}-script]`);
    const pre = root.querySelector(`[data-swz-${target}-script-body]`);
    if (card) card.hidden = false;
    if (pre) pre.textContent = body || "";
    state.scripts[target] = body || "";
  }

  function renderClientsConfSnippet(secret, routerIp, runId) {
    const card = root.querySelector("[data-swz-radius-secret]");
    const pre = root.querySelector("[data-swz-clients-conf]");
    if (!card || !pre) return;
    const snippet =
      `client router-${runId} {\n` +
      `    ipaddr = ${routerIp || "10.10.0.X"}\n` +
      `    secret = ${secret}\n` +
      `    require_message_authenticator = no\n` +
      `    nas_type = mikrotik\n` +
      `}`;
    pre.textContent = snippet;
    state.scripts["clients_conf"] = snippet;
    card.hidden = false;
  }

  async function copyScript(target) {
    const text = state.scripts[target] || "";
    if (!text) {
      toast("لا يوجد سكربت لنسخه بعد.", "error");
      return;
    }
    try {
      await navigator.clipboard.writeText(text);
      toast("✅ تم النسخ — الصقه في MikroTik Terminal", "ok");
    } catch (err) {
      // Fallback for older browsers
      const ta = document.createElement("textarea");
      ta.value = text;
      ta.style.position = "fixed";
      ta.style.opacity = "0";
      document.body.appendChild(ta);
      ta.select();
      try {
        document.execCommand("copy");
        toast("✅ تم النسخ", "ok");
      } catch (e) {
        toast("تعذّر النسخ — انسخ يدوياً من السكربت أدناه.", "error");
      }
      document.body.removeChild(ta);
    }
  }

  // ─── Dynamic field visibility ────────────────────────

  function syncSourceFields() {
    const source = getChecked("net_source");
    root.querySelectorAll("[data-swz-source-fields]").forEach((sec) => {
      sec.hidden = sec.dataset.swzSourceFields !== source;
    });
    const vlanMode = getValue("[data-swz-vlan-mode]");
    const staticBox = root.querySelector(".swz-vlan-static");
    if (staticBox) staticBox.hidden = vlanMode !== "static";
  }

  function syncServiceCards() {
    root.querySelectorAll("[data-swz-service-toggle]").forEach((cb) => {
      const body = cb.closest(".swz-service-card")
        .querySelector(".swz-service-body");
      if (body) body.hidden = !cb.checked;
      // Auto-fill the RADIUS secret on first enable. Prefer
      // the secret allocated server-side at Step 3 (so router
      // + hotspot + server clients.conf all share the SAME
      // secret). Fall back to a fresh random one only when
      // Step 3 hasn't run yet (operator skipped ahead).
      if (cb.checked && cb.dataset.swzServiceToggle === "hotspot") {
        const secretField = root.querySelector(
          "[data-swz-hotspot-secret]",
        );
        if (secretField && !secretField.value) {
          secretField.value = state.radiusSecret || generateSecret();
        }
      }
    });
  }

  function generateSecret() {
    // 32 hex chars = 128 bits of entropy. Crypto API preferred,
    // fall back to Math.random for older browsers.
    const out = new Uint8Array(16);
    if (window.crypto && window.crypto.getRandomValues) {
      window.crypto.getRandomValues(out);
    } else {
      for (let i = 0; i < out.length; i++) {
        out[i] = Math.floor(Math.random() * 256);
      }
    }
    return Array.from(out)
      .map((b) => b.toString(16).padStart(2, "0"))
      .join("");
  }

  // Cache the run state so service-card builders can read the
  // real router_vpn_ip instead of hard-coding a placeholder.
  async function fetchRunState() {
    if (!state.runId) return null;
    try {
      const data = await api("GET", `/runs/${state.runId}/state`);
      state.runData = data.run || {};
      return state.runData;
    } catch (err) {
      return null;
    }
  }

  // ─── Step actions ────────────────────────────────────

  async function submitRouterInfo() {
    const name = getValue("[data-swz-router-name]");
    if (!name) {
      toast("الرجاء إدخال اسم للراوتر.", "error");
      return false;
    }
    const type = getChecked("router_type") || "hotspot";
    await ensureRun();
    await api("POST", `/runs/${state.runId}/router-info`, {
      router_name: name,
      router_type: type,
    });
    return true;
  }

  function buildInternetInputs() {
    const source = getChecked("net_source");
    if (!source) return null;
    if (source === "dhcp") {
      return {
        source_type: "dhcp",
        interface: getValue("[data-swz-iface]"),
        nat_enabled: getValue("[data-swz-nat]"),
        use_peer_dns: true,
      };
    }
    if (source === "static") {
      return {
        source_type: "static",
        interface: getValue("[data-swz-static-iface]"),
        address_cidr: getValue("[data-swz-static-cidr]"),
        gateway: getValue("[data-swz-static-gateway]"),
        dns_servers: getValue("[data-swz-static-dns]"),
        nat_enabled: getValue("[data-swz-static-nat]"),
      };
    }
    if (source === "pppoe") {
      return {
        source_type: "pppoe",
        interface: getValue("[data-swz-ppp-iface]"),
        username: getValue("[data-swz-ppp-user]"),
        password: getValue("[data-swz-ppp-pass]"),
        nat_enabled: getValue("[data-swz-ppp-nat]"),
      };
    }
    if (source === "vlan") {
      const mode = getValue("[data-swz-vlan-mode]") || "dhcp";
      const base = {
        source_type: "vlan",
        parent_interface: getValue("[data-swz-vlan-parent]"),
        vlan_id: Number(getValue("[data-swz-vlan-id]") || 0),
        address_mode: mode,
        nat_enabled: getValue("[data-swz-vlan-nat]"),
      };
      if (mode === "static") {
        base.address_cidr = getValue("[data-swz-vlan-cidr]");
        base.gateway = getValue("[data-swz-vlan-gateway]");
      }
      return base;
    }
    return null;
  }

  async function generateInternetScript(btn) {
    setBusy(btn, true);
    try {
      await ensureRun();
      const inputs = buildInternetInputs();
      if (!inputs) {
        toast("اختر نوع الاتصال أوّلاً.", "error");
        return;
      }
      const data = await api(
        "POST",
        `/runs/${state.runId}/phase-plan/internet`,
        inputs,
      );
      const plan = data.plan || {};
      if (!plan.can_apply) {
        const diags = data.diagnostics || [];
        const msg = diags.length
          ? diags.map((d) => `• ${d.ar_explanation || d.code}`).join("\n")
          : "تعذّر توليد السكربت. تأكّد من المدخلات.";
        toast(msg, "error");
        return;
      }
      // Remember which physical interface is the WAN — later
      // steps (hotspot, broadband, discovery) must NEVER pick
      // it. Kept in client state; passed explicitly to the
      // discovery endpoint as a blocked_iface hint.
      state.routerWanInterface =
        inputs.interface
        || inputs.parent_interface
        || "";
      showScript("step2", plan.script);
      const nextBtn = root.querySelector('[data-swz-next="2"]');
      if (nextBtn) nextBtn.hidden = false;
      toast("✅ السكربت جاهز. انسخه والصقه في MikroTik.", "ok");
    } catch (err) {
      toast("خطأ: " + err.message, "error");
    } finally {
      setBusy(btn, false);
    }
  }

  async function generateVpnScript(btn) {
    setBusy(btn, true);
    try {
      await ensureRun();
      const data = await api(
        "POST",
        `/runs/${state.runId}/generate-script`,
        {},
      );
      // The service returns the .rsc text under `script`. Some
      // older callers used `script_body` — accept both.
      const scriptText = data.script || data.script_body;
      if (!scriptText) {
        toast("تعذّر توليد سكربت الربط.", "error");
        return;
      }
      showScript("step3", scriptText);
      // Render the short fetch+import command — far more
      // reliable than pasting the full script (RouterOS
      // Terminal truncates lines >200 chars, dropping the
      // critical /user add and /radius add lines).
      // NOTE: the path is /admin/radius/wz/<code>.rsc because
      // the Flask blueprint has url_prefix=/admin/radius. The
      // endpoint is whitelisted in _PUBLIC_ENDPOINTS so the
      // router doesn't need a session cookie — the secret
      // short code in the URL IS the authentication.
      const shortCode = data.short_code || "";
      if (shortCode) {
        const host = window.location.host;
        const fetchImport =
          `/tool fetch url="http://${host}/admin/radius/wz/${shortCode}.rsc" mode=http dst-path="hr-setup.rsc"\n` +
          `/import file-name="hr-setup.rsc"`;
        const pre = root.querySelector("[data-swz-fetch-import]");
        if (pre) pre.textContent = fetchImport;
        state.scripts["fetch_import"] = fetchImport;
      }
      // Surface the RADIUS secret + clients.conf snippet so
      // the operator knows what to paste on the server side.
      const secret = data.radius_secret || "";
      const routerVpnIp =
        (data.run && data.run.router_vpn_ip) || "";
      if (secret) {
        state.radiusSecret = secret;  // cached for Step 5
        renderClientsConfSnippet(secret, routerVpnIp, state.runId);
      }
      // Cache the API credentials the unified script baked
      // into the router — Step 6 auto-fills these so the
      // operator doesn't have to type anything.
      if (data.api_user) state.apiUser = data.api_user;
      if (data.api_password) state.apiPassword = data.api_password;
      const pasteBox = root.querySelector("[data-swz-step3-paste]");
      if (pasteBox) pasteBox.hidden = false;
      const genBtn = root.querySelector(
        '[data-swz-action="generate-vpn"]',
      );
      const submitBtn = root.querySelector(
        '[data-swz-action="submit-key"]',
      );
      if (genBtn) genBtn.hidden = true;
      if (submitBtn) submitBtn.hidden = false;
      toast("✅ سكربت الربط جاهز. الصقه في MikroTik ثم انسخ الإخراج هنا.", "ok");
    } catch (err) {
      toast("خطأ: " + err.message, "error");
    } finally {
      setBusy(btn, false);
    }
  }

  async function submitRouterKey(btn) {
    setBusy(btn, true);
    try {
      const output = getValue("[data-swz-step3-output]");
      if (!output) {
        toast("الصق إخراج MikroTik أوّلاً.", "error");
        return;
      }
      await api(
        "POST",
        `/runs/${state.runId}/submit-key`,
        { pasted_output: output },
      );
      // Apply server peer immediately — operator doesn't need
      // to know there are two steps under the hood.
      await api(
        "POST",
        `/runs/${state.runId}/apply-server-peer`,
        {},
      );
      toast("✅ تم إنشاء peer على الخادم.", "ok");
      showStep(4);
      startHandshakePolling();
    } catch (err) {
      toast("خطأ: " + err.message, "error");
    } finally {
      setBusy(btn, false);
    }
  }

  let handshakePoll = null;

  function startHandshakePolling() {
    if (handshakePoll) clearInterval(handshakePoll);
    handshakePoll = setInterval(async () => {
      try {
        const data = await api("GET", `/runs/${state.runId}/state`);
        const run = data.run || {};
        if (run.v3_state === "verifying" || run.v3_state === "registering"
            || run.handshake_first_seen_at) {
          clearInterval(handshakePoll);
          handshakePoll = null;
          const status = root.querySelector("[data-swz-verify-status]");
          if (status) {
            status.innerHTML = `
              <div class="swz-verify-spinner" style="font-size:48px">✅</div>
              <strong>تم اكتشاف Handshake!</strong>
              <p>الاتصال يعمل — تابع للخطوة التالية.</p>
            `;
          }
          // Auto-advance after 1.5 seconds.
          setTimeout(() => showStep(5), 1500);
        }
      } catch (err) {
        // Silent — polling errors shouldn't toast on every tick.
      }
    }, 3000);
  }

  async function markHandshakeManually(btn) {
    setBusy(btn, true);
    try {
      await api(
        "POST", `/runs/${state.runId}/mark-handshake`, {},
      );
      if (handshakePoll) clearInterval(handshakePoll);
      toast("✅ تم تأكيد الاتصال يدوياً.", "ok");
      showStep(5);
    } catch (err) {
      toast("خطأ: " + err.message, "error");
    } finally {
      setBusy(btn, false);
    }
  }

  // ─── Optional services (step 5) ──────────────────────

  function renderDiscoveredInterfaces(interfaces) {
    const grid = root.querySelector("[data-swz-hotspot-ifaces]");
    if (!grid) return;
    if (!interfaces || !interfaces.length) {
      toast("لم يُعثر على منافذ صالحة.", "error");
      return;
    }
    // Replace static defaults with what the router actually has.
    grid.innerHTML = interfaces
      .map((it) => {
        const stateLabel = it.disabled
          ? " · معطّل"
          : it.running ? " · ✓" : "";
        const checked = it.recommended ? "checked" : "";
        return `
          <label class="swz-iface-check">
            <input type="checkbox" value="${it.name}" ${checked}>
            <span>${it.name}<small style="opacity:.6;font-size:11px">
              ${it.type}${stateLabel}
            </small></span>
          </label>
        `;
      })
      .join("");
    toast(
      `✅ تم اكتشاف ${interfaces.length} منفذ على الراوتر.`,
      "ok",
    );
  }

  async function applyServerRadius(btn) {
    setBusy(btn, true, "جارٍ التطبيق على الخادم...");
    try {
      const data = await api(
        "POST",
        `/runs/${state.runId}/configure-server-radius`,
        {},
      );
      const appliedNote = root.querySelector(
        "[data-swz-radius-applied]",
      );
      if (appliedNote) appliedNote.hidden = false;
      toast(
        "✅ تم تطبيق clients.conf على الخادم. سيتم تحميله "
        + "خلال ~5 ثوانٍ.",
        "ok",
      );
    } catch (err) {
      toast(
        "تعذّر التطبيق التلقائي: " + err.message + ". "
        + "يمكنك دائماً النسخ يدوياً.",
        "error",
      );
    } finally {
      setBusy(btn, false);
    }
  }

  async function discoverViaApi(btn) {
    const form = root.querySelector(
      "[data-swz-discover-api-form]",
    );
    const userField = root.querySelector(
      "[data-swz-discover-api-user]",
    );
    const passField = root.querySelector(
      "[data-swz-discover-api-pass]",
    );
    // Pre-fill from the credentials the unified script baked
    // into the router during Step 3. Always overwrite an empty
    // field, AND overwrite a stale placeholder/admin value if
    // we now have a real run-specific user. Operator can still
    // edit the values after seeing them.
    if (userField && state.apiUser) {
      const current = (userField.value || "").trim();
      const stale = current === "" || current === "admin";
      if (stale) userField.value = state.apiUser;
    }
    if (passField && state.apiPassword) {
      if (!passField.value) passField.value = state.apiPassword;
    }
    if (form && form.hidden) {
      // First click reveals the credentials form.
      form.hidden = false;
      const haveCreds = state.apiUser && state.apiPassword;
      toast(
        haveCreds
          ? "تم تعبئة بيانات API تلقائياً من سكربت الخطوة 3. "
            + "اضغط الزر مرّة أخرى للاكتشاف."
          : "أدخل بيانات API للراوتر ثم اضغط الزر مرّة أخرى.",
        "info",
      );
      return;
    }
    setBusy(btn, true);
    try {
      const data = await api(
        "POST",
        `/runs/${state.runId}/discover-interfaces`,
        {
          mode: "api",
          api_user: getValue("[data-swz-discover-api-user]") || "admin",
          api_password: getValue("[data-swz-discover-api-pass]"),
          blocked_interfaces: state.routerWanInterface
            ? [state.routerWanInterface]
            : [],
        },
      );
      renderDiscoveredInterfaces(data.interfaces);
    } catch (err) {
      toast("خطأ: " + err.message, "error");
    } finally {
      setBusy(btn, false);
    }
  }

  async function discoverViaPaste(btn) {
    setBusy(btn, true);
    try {
      const data = await api(
        "POST",
        `/runs/${state.runId}/discover-interfaces`,
        {
          mode: "paste",
          pasted_output: getValue("[data-swz-discover-paste]"),
          blocked_interfaces: state.routerWanInterface
            ? [state.routerWanInterface]
            : [],
        },
      );
      renderDiscoveredInterfaces(data.interfaces);
    } catch (err) {
      toast("خطأ: " + err.message, "error");
    } finally {
      setBusy(btn, false);
    }
  }

  function collectHotspotInterfaces() {
    const checked = Array.from(
      root.querySelectorAll(
        "[data-swz-hotspot-ifaces] input[type=checkbox]:checked",
      ),
    ).map((cb) => cb.value);
    const custom = (
      getValue("[data-swz-hotspot-iface-custom]") || ""
    )
      .split(/[,\s]+/)
      .map((s) => s.trim())
      .filter(Boolean);
    // De-duplicate while preserving order.
    return Array.from(new Set([...checked, ...custom]));
  }

  async function generateHotspotScript(btn) {
    setBusy(btn, true);
    try {
      // Always pull the latest run state — router_vpn_ip is
      // only set after Step 3 generates the unified script.
      const run = await fetchRunState();
      const routerVpnIp = (run && run.router_vpn_ip) || "";
      if (!routerVpnIp) {
        toast(
          "أكمل الخطوة 3 (الربط بالخادم) أوّلاً — نحتاج "
          + "إلى عنوان VPN للراوتر.",
          "error",
        );
        return;
      }
      const interfaces = collectHotspotInterfaces();
      if (!interfaces.length) {
        toast(
          "اختر على الأقل منفذاً واحداً للـ Hotspot.",
          "error",
        );
        return;
      }
      const wanIface = getValue("[data-swz-hotspot-wan]") || "ether1";
      let secret = getValue("[data-swz-hotspot-secret]");
      if (!secret) {
        secret = generateSecret();
        const fld = root.querySelector("[data-swz-hotspot-secret]");
        if (fld) fld.value = secret;
      }
      const data = await api(
        "POST",
        `/runs/${state.runId}/phase-plan/hotspot`,
        {
          mode: "manual",
          selected_interfaces: interfaces,
          subnet_base:
            getValue("[data-swz-hotspot-subnet]") || "10.99.0.0/16",
          radius_secret: secret,
          router_vpn_ip: routerVpnIp,
          wan_interface: wanIface,
          blocked_interfaces: [
            wanIface,
            state.routerWanInterface,
            "hr-wg",
          ].filter(Boolean),
        },
      );
      const plan = data.plan || {};
      if (!plan.can_apply) {
        toast("تعذّر توليد سكربت Hotspot. راجع المدخلات.", "error");
        return;
      }
      showScript("hotspot", plan.script);
      const ifaceList = interfaces.join(", ");
      toast(
        `✅ سكربت Hotspot جاهز لـ ${interfaces.length} منفذ `
        + `(${ifaceList}). ⚠️ السكربت سيحذف أي إعدادات Hotspot `
        + `سابقة لـ HobeRadius على نفس المنافذ.`,
        "ok",
      );
    } catch (err) {
      toast("خطأ: " + err.message, "error");
    } finally {
      setBusy(btn, false);
    }
  }

  function collectBroadbandInterfaces() {
    const checked = Array.from(
      root.querySelectorAll(
        "[data-swz-bb-ifaces] input[type=checkbox]:checked",
      ),
    ).map((cb) => cb.value);
    const custom = (
      getValue("[data-swz-bb-iface-custom]") || ""
    )
      .split(/[,\s]+/)
      .map((s) => s.trim())
      .filter(Boolean);
    return Array.from(new Set([...checked, ...custom]));
  }

  async function generateBroadbandScript(btn) {
    setBusy(btn, true);
    try {
      const interfaces = collectBroadbandInterfaces();
      if (!interfaces.length) {
        toast(
          "اختر على الأقل منفذاً واحداً لـ PPPoE.",
          "error",
        );
        return;
      }
      const data = await api(
        "POST",
        `/runs/${state.runId}/phase-plan/broadband`,
        {
          mode: "manual",
          selected_interfaces: interfaces,
          local_address: getValue("[data-swz-bb-local]"),
          remote_pool_cidr: getValue("[data-swz-bb-pool]"),
          blocked_interfaces: [
            state.routerWanInterface,
            "hr-wg",
          ].filter(Boolean),
        },
      );
      const plan = data.plan || {};
      if (!plan.can_apply) {
        toast("تعذّر توليد سكربت Broadband. راجع المدخلات.", "error");
        return;
      }
      showScript("broadband", plan.script);
      toast(
        `✅ سكربت Broadband جاهز لـ ${interfaces.length} منفذ. `
        + `⚠️ السكربت سيحذف أي إعدادات PPPoE سابقة لـ HobeRadius.`,
        "ok",
      );
    } catch (err) {
      toast("خطأ: " + err.message, "error");
    } finally {
      setBusy(btn, false);
    }
  }

  async function generateAddedService(btn, kind) {
    setBusy(btn, true);
    try {
      const domainsRaw = kind === "walled_garden"
        ? getValue("[data-swz-wg-domains]")
        : getValue("[data-swz-bs-domains]");
      const domains = (domainsRaw || "")
        .split(/[\n,]+/)
        .map((s) => s.trim())
        .filter(Boolean);
      if (!domains.length) {
        toast("أضف دومين واحد على الأقل.", "error");
        return;
      }
      const data = await api(
        "POST",
        `/runs/${state.runId}/phase-plan/added_services`,
        {
          service_key: kind,
          inputs: { domains },
        },
      );
      const plan = data.plan || {};
      if (!plan.can_apply) {
        toast("تعذّر توليد السكربت.", "error");
        return;
      }
      const target = kind === "walled_garden" ? "wg" : "bs";
      showScript(target, plan.script);
      toast("✅ السكربت جاهز.", "ok");
    } catch (err) {
      toast("خطأ: " + err.message, "error");
    } finally {
      setBusy(btn, false);
    }
  }

  async function registerRouter(btn) {
    setBusy(btn, true);
    try {
      await api(
        "POST",
        `/runs/${state.runId}/register`,
        {
          api_user: getValue("[data-swz-api-user]") || "admin",
          api_password: getValue("[data-swz-api-pass]"),
        },
      );
      toast("🎉 تم تسجيل الراوتر بنجاح!", "ok");
      showStep(7);
    } catch (err) {
      toast("خطأ: " + err.message, "error");
    } finally {
      setBusy(btn, false);
    }
  }

  // ─── Event delegation ────────────────────────────────

  root.addEventListener("change", (e) => {
    if (e.target.matches("[data-swz-net-source]")
        || e.target.matches("[data-swz-vlan-mode]")) {
      syncSourceFields();
    } else if (e.target.matches("[data-swz-service-toggle]")) {
      syncServiceCards();
    }
  });

  root.addEventListener("click", async (e) => {
    const btn = e.target.closest("button, [data-swz-next], [data-swz-back]");
    if (!btn) return;

    // Copy script buttons
    const copyTarget = btn.dataset.swzCopyScript;
    if (copyTarget) {
      copyScript(copyTarget);
      return;
    }
    if (btn.matches("[data-swz-copy-clients-conf]")) {
      copyScript("clients_conf");
      return;
    }
    if (btn.matches("[data-swz-toggle-paste-discover]")) {
      const f = root.querySelector("[data-swz-discover-paste-form]");
      if (f) f.hidden = !f.hidden;
      return;
    }

    // Step transitions
    if (btn.dataset.swzNext) {
      const from = Number(btn.dataset.swzNext);
      if (from === 1) {
        try {
          if (!(await submitRouterInfo())) return;
          showStep(2);
        } catch (err) {
          toast("خطأ: " + err.message, "error");
        }
        return;
      }
      showStep(from + 1);
      return;
    }
    if (btn.dataset.swzBack) {
      const from = Number(btn.dataset.swzBack);
      showStep(from - 1);
      return;
    }

    // Step actions
    switch (btn.dataset.swzAction) {
      case "generate-internet":
        await generateInternetScript(btn); break;
      case "generate-vpn":
        await generateVpnScript(btn); break;
      case "submit-key":
        await submitRouterKey(btn); break;
      case "mark-handshake":
        await markHandshakeManually(btn); break;
      case "discover-via-api":
        await discoverViaApi(btn); break;
      case "discover-via-paste":
        await discoverViaPaste(btn); break;
      case "apply-server-radius":
        await applyServerRadius(btn); break;
      case "generate-hotspot":
        await generateHotspotScript(btn); break;
      case "generate-broadband":
        await generateBroadbandScript(btn); break;
      case "generate-walled-garden":
        await generateAddedService(btn, "walled_garden"); break;
      case "generate-block-sites":
        await generateAddedService(btn, "block_sites"); break;
      case "register":
        await registerRouter(btn); break;
    }
  });

  // ─── Init ────────────────────────────────────────────

  syncSourceFields();
  syncServiceCards();
  showStep(1);
})();
