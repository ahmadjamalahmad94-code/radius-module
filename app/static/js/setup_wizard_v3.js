/* Setup Wizard v3 — single-page state machine driver.
 *
 * Talks to /admin/radius/setup-wizard-v3/runs/<id>/...
 * endpoints and renders the matching step's "active" state.
 * Auto-creates a run on first load. Polls /state every 2.5s
 * while the run is in a non-terminal state. */

(function () {
  "use strict";

  const BASE = "/admin/radius/setup-wizard-v3";

  // ─── DOM refs ──────────────────────────────────────────
  const page         = document.querySelector("[data-wv3-page]");
  if (!page) return;
  const csrfInput    = document.querySelector(
    'input[name="_csrf_token"]'
  );
  const stateBadge   = page.querySelector("[data-wv3-state-badge]");
  const steps        = page.querySelectorAll("[data-wv3-step]");
  const diagPanel    = page.querySelector("[data-wv3-diagnostics]");
  const diagList     = page.querySelector("[data-wv3-diagnostics-list]");
  const scriptFrame  = page.querySelector("[data-wv3-script-frame]");
  const scriptMeta   = page.querySelector("[data-wv3-script-meta]");
  const scriptBody   = page.querySelector("[data-wv3-script-body]");
  const finalGrid    = page.querySelector("[data-wv3-final-grid]");

  // ─── Run state ─────────────────────────────────────────
  let runId         = 0;
  let lastState     = null;
  let pollTimer     = null;

  const STATE_LABELS = {
    COLLECTING:           "1/5 — جمع بيانات الراوتر",
    PLANNING:             "2/5 — جاهز لتوليد السكربت",
    AWAITING_HANDSHAKE:   "3/5 — بانتظار تشغيل السكربت",
    APPLYING_SERVER_PEER: "4/5 — تطبيق إعدادات الخادم",
    VERIFYING:            "4/5 — التحقّق من الـ handshake",
    REGISTERING:          "5/5 — تسجيل الراوتر",
    COMPLETE:             "✓ اكتمل",
    BLOCKED:              "⚠ متوقّف — يحتاج تدخّل",
  };

  const ACTIVE_STEP_FOR = {
    COLLECTING:           "collecting",
    PLANNING:             "planning",
    AWAITING_HANDSHAKE:   "awaiting_handshake",
    APPLYING_SERVER_PEER: "applying_server_peer",
    VERIFYING:            "applying_server_peer",
    REGISTERING:          "registering",
    COMPLETE:             "complete",
    BLOCKED:              null,
  };

  const STEP_ORDER = [
    "collecting", "planning", "awaiting_handshake",
    "applying_server_peer", "registering", "complete",
  ];

  // ─── Helpers ───────────────────────────────────────────

  function csrf() {
    return csrfInput ? csrfInput.value : "";
  }

  async function api(method, path, body) {
    const opts = {
      method,
      credentials: "same-origin",
      headers: { "X-CSRFToken": csrf() },
    };
    if (body) {
      opts.headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(body);
    }
    const res = await fetch(BASE + path, opts);
    const text = await res.text();
    let data = {};
    try { data = JSON.parse(text); }
    catch (_) { data = { ok: false, error: text }; }
    if (!res.ok || data.ok === false) {
      throw new Error(
        data.error || `HTTP ${res.status}`
      );
    }
    return data;
  }

  function renderState(run) {
    if (!run) return;
    lastState = run;
    stateBadge.textContent =
      STATE_LABELS[run.state] || run.state;

    // Mark steps as locked/active/done based on state.
    const activeStep = ACTIVE_STEP_FOR[run.state];
    const activeIdx = activeStep
      ? STEP_ORDER.indexOf(activeStep)
      : -1;
    steps.forEach((el, i) => {
      const stepName = el.dataset.wv3Step;
      const idx = STEP_ORDER.indexOf(stepName);
      el.classList.remove("is-locked", "is-active", "is-done");
      if (idx < activeIdx) {
        el.classList.add("is-done");
      } else if (idx === activeIdx) {
        el.classList.add("is-active");
      } else {
        el.classList.add("is-locked");
      }
    });

    // Diagnostics
    if (run.diagnostics && run.diagnostics.length) {
      diagPanel.hidden = false;
      diagList.innerHTML = run.diagnostics.map(d => `
        <div class="wv3-diagnostic-item">
          <span class="wv3-diagnostic-code">[${d.code || ""}]</span>
          <span>${d.ar || ""}</span>
        </div>
      `).join("");
    } else {
      diagPanel.hidden = true;
      diagList.innerHTML = "";
    }

    // Substeps (step 4)
    const substeps = {
      "write-peer": ["APPLYING_SERVER_PEER", "VERIFYING",
                     "REGISTERING", "COMPLETE"],
      "wg-reload":  ["VERIFYING", "REGISTERING", "COMPLETE"],
      "handshake":  ["REGISTERING", "COMPLETE"],
    };
    page.querySelectorAll("[data-wv3-substep]").forEach(el => {
      const name = el.dataset.wv3Substep;
      const stateMatches = substeps[name] || [];
      const isDone = stateMatches.includes(run.state);
      el.classList.remove("is-ok", "is-failed", "is-running");
      const icon = el.querySelector(".wv3-substep-icon");
      if (isDone) {
        el.classList.add("is-ok");
        if (icon) icon.textContent = "✓";
      } else if (run.state === "APPLYING_SERVER_PEER"
                 && name === "write-peer") {
        el.classList.add("is-running");
        if (icon) icon.textContent = "◐";
      } else if (run.state === "VERIFYING"
                 && name === "handshake") {
        el.classList.add("is-running");
        if (icon) icon.textContent = "◐";
      } else {
        if (icon) icon.textContent = "○";
      }
    });

    // Final grid
    if (run.state === "COMPLETE") {
      finalGrid.innerHTML = `
        <div class="wv3-stat">
          <div class="wv3-stat-label">اسم الراوتر</div>
          <div class="wv3-stat-value">${run.router_name || "—"}</div>
        </div>
        <div class="wv3-stat">
          <div class="wv3-stat-label">عنوان VPN</div>
          <div class="wv3-stat-value">${run.router_vpn_ip || "—"}</div>
        </div>
        <div class="wv3-stat">
          <div class="wv3-stat-label">رقم NAS</div>
          <div class="wv3-stat-value">${run.nas_device_id || "—"}</div>
        </div>
        <div class="wv3-stat">
          <div class="wv3-stat-label">نوع الخدمة</div>
          <div class="wv3-stat-value">${run.router_type || "—"}</div>
        </div>
      `;
      const link = page.querySelector(
        '[data-wv3-action="open-router"]'
      );
      if (link && run.nas_device_id) {
        link.href = `/admin/radius/mt/${run.nas_device_id}/dashboard`;
      }
    }

    // Script preview
    if (run.unified_script_short_code && scriptFrame.hidden) {
      // Script was generated — fetch it lazily.
      fetch(`/admin/radius/wz/${run.unified_script_short_code}.rsc`)
        .then(r => r.text())
        .then(body => {
          scriptBody.textContent = body;
          scriptMeta.textContent =
            `code: ${run.unified_script_short_code}`;
          scriptFrame.hidden = false;
          const gActions = page.querySelector(
            "[data-wv3-generate-actions]"
          );
          if (gActions) gActions.hidden = true;
        })
        .catch(() => {});
    }

    // Schedule next poll
    if (!run.is_terminal) {
      if (pollTimer) clearTimeout(pollTimer);
      pollTimer = setTimeout(pollState, 2500);
    } else if (pollTimer) {
      clearTimeout(pollTimer);
    }
  }

  async function pollState() {
    if (!runId) return;
    try {
      const data = await api("GET", `/runs/${runId}/state`);
      renderState(data.run);
    } catch (err) {
      console.warn("v3 poll failed:", err);
      if (pollTimer) clearTimeout(pollTimer);
      pollTimer = setTimeout(pollState, 5000);
    }
  }

  async function ensureRun() {
    if (runId) return runId;
    const data = await api("POST", "/runs", {});
    runId = data.run.id;
    renderState(data.run);
    return runId;
  }

  // ─── Action handlers ───────────────────────────────────

  async function handle(action, btn) {
    if (btn) btn.classList.add("is-busy");
    try {
      await ensureRun();
      switch (action) {
        case "submit-router-info": {
          const name = (page.querySelector(
            "[data-wv3-router-name]"
          ).value || "").trim();
          const type = page.querySelector(
            "[data-wv3-router-type]"
          ).value;
          const data = await api(
            "POST",
            `/runs/${runId}/router-info`,
            { router_name: name, router_type: type }
          );
          renderState(data.run);
          break;
        }
        case "generate-script": {
          const data = await api(
            "POST",
            `/runs/${runId}/generate-script`,
            {}
          );
          renderState(data.run);
          if (data.script) {
            scriptBody.textContent = data.script;
            scriptMeta.textContent =
              `code: ${data.short_code}  ·  sha256: ${(data.sha256||"").slice(0,16)}…`;
            scriptFrame.hidden = false;
            const gActions = page.querySelector(
              "[data-wv3-generate-actions]"
            );
            if (gActions) gActions.hidden = true;
          }
          break;
        }
        case "copy-script": {
          const txt = scriptBody.textContent || "";
          await navigator.clipboard.writeText(txt);
          if (btn) {
            const orig = btn.innerHTML;
            btn.innerHTML =
              '<i class="fa-solid fa-check"></i> نُسخ';
            setTimeout(() => {
              btn.innerHTML = orig;
            }, 1500);
          }
          break;
        }
        case "submit-key": {
          const pasted = (page.querySelector(
            "[data-wv3-pasted-output]"
          ).value || "").trim();
          if (!pasted) {
            alert("ألصق مخرجات السكربت من الراوتر أوّلاً.");
            return;
          }
          let data = await api(
            "POST",
            `/runs/${runId}/submit-key`,
            { pasted_output: pasted }
          );
          renderState(data.run);
          // Immediately advance: write peer file, then mark
          // handshake observed (poll could do the latter
          // automatically later; for one-button UX we drive
          // it here).
          if (data.run.state === "APPLYING_SERVER_PEER") {
            data = await api(
              "POST",
              `/runs/${runId}/apply-server-peer`,
              {}
            );
            renderState(data.run);
          }
          // The user can mark handshake manually below if the
          // poll doesn't see it auto.
          if (data.run.state === "VERIFYING") {
            // Wait briefly and try mark-handshake — most of
            // the time, the wg-reload has triggered and the
            // handshake is moments away.
            setTimeout(async () => {
              try {
                const d = await api(
                  "POST",
                  `/runs/${runId}/mark-handshake`,
                  {}
                );
                renderState(d.run);
              } catch (_) {}
            }, 3000);
          }
          break;
        }
        case "register": {
          const apiPwd = (page.querySelector(
            "[data-wv3-api-password]"
          ).value || "");
          const data = await api(
            "POST",
            `/runs/${runId}/register`,
            { api_password: apiPwd }
          );
          renderState(data.run);
          break;
        }
      }
    } catch (err) {
      console.error(`v3 ${action} failed:`, err);
      alert("خطأ: " + (err.message || err));
    } finally {
      if (btn) btn.classList.remove("is-busy");
    }
  }

  page.addEventListener("click", e => {
    const btn = e.target.closest("[data-wv3-action]");
    if (!btn) return;
    e.preventDefault();
    handle(btn.dataset.wv3Action, btn);
  });

  // ─── SW7: phase planner panel ─────────────────────────
  // Surface the SW1-SW6 phase planners as a preview UI under
  // the main wizard. Only revealed once the run has at least
  // reached PLANNING — we need a real run_id to tag scripts.

  const phasePanel = page.querySelector("[data-wv3-phase-planner]");
  const phaseList = page.querySelector("[data-wv3-phase-list]");
  const phaseResult = page.querySelector("[data-wv3-phase-result]");
  const phaseResultMeta = page.querySelector("[data-wv3-phase-result-meta]");
  const phaseResultScript = page.querySelector("[data-wv3-phase-result-script]");
  const phaseResultValidation = page.querySelector("[data-wv3-phase-result-validation]");
  const phaseResultDiagnostics = page.querySelector("[data-wv3-phase-result-diagnostics]");
  const phaseResultDiagnosticsList = page.querySelector("[data-wv3-phase-result-diagnostics-list]");

  // Per-phase static input schemas. Each entry is the minimum
  // shape the operator sees as JSON in a textarea. Kept simple
  // for the first cut — a future slice can render proper forms.
  const PHASE_DEFAULTS = {
    internet: {
      source_type: "dhcp",
      interface: "ether1",
      nat_enabled: true,
    },
    vpn_radius: {
      router_vpn_ip: "10.10.0.5",
      vps_vpn_ip: "10.10.0.1",
      vps_public_endpoint: "187.77.70.18",
      radius_secret: "REPLACE_ME",
      server_public_key: "REPLACE_WITH_43_CHAR_KEY=",
    },
    hotspot: {
      mode: "manual",
      selected_interfaces: ["ether2"],
      subnet_base: "10.99.0.0/16",
      radius_secret: "REPLACE_ME",
      router_vpn_ip: "10.10.0.5",
    },
    broadband: {
      mode: "manual",
      selected_interfaces: ["ether3"],
      local_address: "192.168.50.1",
      remote_pool_cidr: "192.168.50.0/24",
    },
    added_services: {
      service_key: "walled_garden",
      inputs: { domains: ["allowed.example"] },
    },
  };

  async function loadPhaseList() {
    if (!phaseList) return;
    try {
      const res = await fetch(
        "/admin/radius/setup-wizard-v3/phase-planners",
        { headers: { "X-CSRFToken": csrf() } },
      );
      const data = await res.json();
      if (!data.ok) return;
      phaseList.innerHTML = data.phases
        .map(p => `
          <article class="wv3-phase-item">
            <header>
              <strong>${p.title_ar}</strong>
              <small dir="ltr">${p.phase}</small>
            </header>
            <p>${p.description_ar}</p>
            <textarea data-wv3-phase-inputs="${p.phase}" dir="ltr" rows="6">${JSON.stringify(PHASE_DEFAULTS[p.phase] || {}, null, 2)}</textarea>
            <button type="button" class="wv3-btn wv3-btn-secondary"
                    data-wv3-phase-run="${p.phase}">
              معاينة السكربت
            </button>
          </article>
        `)
        .join("");
    } catch (err) {
      console.warn("phase planner list load failed:", err);
    }
  }

  async function runPhasePlan(phase, btn) {
    if (!runId) {
      alert("ابدا جلسة معالج أولاً قبل توليد سكربتات إضافية.");
      return;
    }
    const textarea = page.querySelector(
      `[data-wv3-phase-inputs="${phase}"]`,
    );
    let inputs = {};
    try {
      inputs = textarea ? JSON.parse(textarea.value || "{}") : {};
    } catch (parseErr) {
      alert("JSON غير صالح في حقل المدخلات: " + parseErr.message);
      return;
    }
    btn.classList.add("is-busy");
    try {
      const res = await fetch(
        `/admin/radius/setup-wizard-v3/runs/${runId}/phase-plan/${phase}`,
        {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "X-CSRFToken": csrf(),
          },
          body: JSON.stringify(inputs),
        },
      );
      const data = await res.json();
      renderPhaseResult(phase, data);
    } catch (err) {
      alert("فشل توليد السكربت: " + err.message);
    } finally {
      btn.classList.remove("is-busy");
    }
  }

  function renderPhaseResult(phase, data) {
    if (!phaseResult) return;
    phaseResult.hidden = false;
    const plan = (data && data.plan) || {};
    const can = plan.can_apply ? "✅ جاهز للتطبيق" : "⛔ محجوب";
    if (phaseResultMeta) {
      phaseResultMeta.innerHTML = `
        <div><strong>المرحلة:</strong> ${phase}</div>
        <div><strong>الحالة:</strong> ${can}</div>
        <div><strong>الوسوم:</strong> ${(plan.tags || []).join(", ") || "—"}</div>
      `;
    }
    if (phaseResultScript) {
      phaseResultScript.textContent = plan.script || "(لا يوجد سكربت)";
    }
    if (phaseResultValidation) {
      phaseResultValidation.textContent =
        (plan.validation_commands || []).join("\n") || "(لا توجد)";
    }
    const diagnostics = (data && data.diagnostics) || [];
    if (phaseResultDiagnostics && phaseResultDiagnosticsList) {
      phaseResultDiagnostics.hidden = diagnostics.length === 0;
      phaseResultDiagnosticsList.innerHTML = diagnostics
        .map(d => `
          <div class="wv3-diag wv3-diag-${d.severity || "error"}">
            <strong>${d.code}</strong>
            <p>${d.ar_explanation || ""}</p>
            ${d.fix ? `<small><b>الإصلاح:</b> ${d.fix}</small>` : ""}
            ${d.inspect_command ? `<code dir="ltr">${d.inspect_command}</code>` : ""}
          </div>
        `)
        .join("");
    }
    phaseResult.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  page.addEventListener("click", e => {
    const runBtn = e.target.closest("[data-wv3-phase-run]");
    if (runBtn) {
      e.preventDefault();
      runPhasePlan(runBtn.dataset.wv3PhaseRun, runBtn);
    }
  });

  // Show the panel once we have a run id; populate it once.
  function maybeRevealPhasePanel() {
    if (phasePanel && runId && phasePanel.hidden) {
      phasePanel.hidden = false;
      loadPhaseList();
    }
  }

  // ─── Boot ──────────────────────────────────────────────
  ensureRun().then(maybeRevealPhasePanel).catch(err => {
    console.error("v3 boot failed:", err);
    alert("تعذّر بدء جلسة المعالج: " + err.message);
  });
})();
