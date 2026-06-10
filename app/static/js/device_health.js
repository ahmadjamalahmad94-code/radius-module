/* تتبع حالة الأجهزة — device_health.js
 * Client-side filtering + CRUD over the JSON API. CSRF via X-CSRFToken header
 * (these routes live under /admin/… so the global guard enforces it).
 * NO live MikroTik mutation: «معاينة الخطة»/«مزامنة»/«فحص» are read-only. */
(function () {
  "use strict";

  var CFG = window.__DH__ || {};
  var API = CFG.api || "";          // …/device-health/api/devices
  var PLAN = CFG.planUrl || "";     // …/device-health/api/plan

  function $(sel, root) { return (root || document).querySelector(sel); }
  function $all(sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); }

  function api(path) { return API + path; }

  function request(url, method, body) {
    var opts = {
      method: method,
      headers: { "X-CSRFToken": CFG.csrf || "" },
      credentials: "same-origin"
    };
    if (body !== undefined) {
      opts.headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(body);
    }
    return fetch(url, opts).then(function (r) {
      return r.json().catch(function () { return { ok: false, error: "استجابة غير صالحة" }; })
        .then(function (data) { return { status: r.status, data: data }; });
    });
  }

  function toast(msg, kind) {
    var el = document.createElement("div");
    el.className = "dh-toast dh-toast--" + (kind || "info");
    el.textContent = msg;
    el.style.cssText = "position:fixed;inset-block-end:20px;inset-inline-start:50%;" +
      "transform:translateX(-50%);z-index:9999;padding:10px 16px;border-radius:10px;" +
      "font-size:13px;color:#fff;box-shadow:0 6px 20px rgba(0,0,0,.18);background:" +
      (kind === "error" ? "#DC2626" : kind === "success" ? "#16A34A" : "#334155");
    document.body.appendChild(el);
    setTimeout(function () { el.remove(); }, 3200);
  }

  /* ── status pill + live row update (shared by per-row check + Check-All) ── */
  var STATUS_META = {
    up: { v: "green", label: "متصل" }, down: { v: "red", label: "مفصول" },
    timeout: { v: "red", label: "انتهت المهلة" }, high_latency: { v: "amber", label: "بنج عالٍ" },
    disabled: { v: "grey", label: "معطّل" }, apply_failed: { v: "red", label: "فشل التطبيق" },
    unknown: { v: "grey", label: "غير معروف" }
  };
  function statusPillHTML(status) {
    var m = STATUS_META[status] || STATUS_META.unknown;
    return '<span class="hub-pill hub-pill--' + m.v + '"><span class="dot"></span>' + m.label + '</span>';
  }
  function rowById(id) { return document.querySelector('[data-dh-row][data-id="' + id + '"]'); }

  function setRowChecking(row) {
    if (!row) return;
    row.classList.add("is-checking");
    var st = row.querySelector(".dh-status");
    if (st) st.innerHTML = '<span class="dh-checking"><i class="fa-solid fa-spinner fa-spin"></i> يفحص…</span>';
    var lat = row.querySelector(".dh-latency");
    if (lat) lat.classList.add("dh-checking");
  }

  function updateRowStatus(row, status, latency) {
    if (!row) return;
    row.classList.remove("is-checking");
    var st = row.querySelector(".dh-status");
    if (st) st.innerHTML = statusPillHTML(status);
    var lat = row.querySelector(".dh-latency");
    if (lat) { lat.classList.remove("dh-checking"); lat.textContent = (latency != null) ? (latency + " ms") : "—"; }
    var chk = row.querySelector(".dh-checked");
    if (chk) chk.textContent = "الآن";
    row.setAttribute("data-status", status);
    try {
      var d = JSON.parse(row.getAttribute("data-json"));
      d.status = status; d.last_latency_ms = latency;
      row.setAttribute("data-json", JSON.stringify(d));
    } catch (e) {}
    row.classList.add("dh-flash");
    setTimeout(function () { row.classList.remove("dh-flash"); }, 1000);
    applyFilters();
  }

  /* ── Check-All progress bar ── */
  var progEl = document.getElementById("dh-progress");
  var progBar = document.getElementById("dh-progress-bar");
  var progLabel = document.getElementById("dh-progress-label");
  var progCount = document.getElementById("dh-progress-count");

  function progShow(indeterminate) {
    if (!progEl) return;
    progEl.hidden = false;
    progEl.classList.remove("is-done");
    progEl.classList.toggle("is-indeterminate", !!indeterminate);
    if (progBar) progBar.style.width = indeterminate ? "" : "0%";
    if (progLabel) progLabel.innerHTML = '<i class="fa-solid fa-satellite-dish fa-fade"></i> جارٍ فحص الكل…';
    if (progCount) progCount.textContent = "";
  }
  function progUpdate(index, total, deviceName) {
    if (!progEl) return;
    progEl.classList.remove("is-indeterminate");
    var pct = total ? Math.round(index / total * 100) : 0;
    if (progBar) progBar.style.width = pct + "%";
    if (progLabel) progLabel.innerHTML = '<i class="fa-solid fa-satellite-dish fa-fade"></i> يفحص: ' + esc(deviceName || "");
    if (progCount) progCount.textContent = index + " / " + total;
  }
  function progDone(summary) {
    if (!progEl) return;
    progEl.classList.remove("is-indeterminate");
    progEl.classList.add("is-done");
    if (progBar) progBar.style.width = "100%";
    var s = summary || {};
    if (progLabel) progLabel.innerHTML = '<i class="fa-solid fa-circle-check"></i> اكتمل';
    if (progCount) progCount.textContent = "متصل " + (s.up || 0) + " · مفصول " + (s.down || 0) +
      (s.high_latency ? (" · بنج عالٍ " + s.high_latency) : "") +
      (s.unknown ? (" · غير معروف " + s.unknown) : "");
    setTimeout(function () { if (progEl) progEl.hidden = true; }, 6000);
  }

  function handlePollEvent(ev) {
    if (ev.type === "start") {
      progUpdate(0, ev.total || 0, "");
      if ((ev.total || 0) === 0) progDone({});
    } else if (ev.type === "progress") {
      progUpdate(ev.index, ev.total, ev.device);
      updateRowStatus(rowById(ev.device_id), ev.status, ev.latency_ms);
    } else if (ev.type === "done") {
      progDone(ev.summary);
      toast("اكتمل الفحص.", "success");
      refreshChecks();  // السجل والإحصائيات يلتقطان الدورة الجديدة فورًا
    } else if (ev.type === "error") {
      if (progEl) progEl.hidden = true;
      toast(ev.error || "تعذّر الفحص", "error");
    }
  }

  function streamPollAll(btn) {
    var orig = btn ? btn.innerHTML : "";
    if (btn) { btn.disabled = true; btn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> جارٍ الفحص…'; }
    progShow(true);
    fetch(CFG.pollStreamUrl, {
      method: "POST", credentials: "same-origin",
      headers: { "X-CSRFToken": CFG.csrf || "" }
    }).then(function (resp) {
      if (!resp.ok || !resp.body || !window.ReadableStream) throw new Error("no-stream");
      var reader = resp.body.getReader();
      var dec = new TextDecoder();
      var buf = "";
      function pump() {
        return reader.read().then(function (r) {
          if (r.done) return;
          buf += dec.decode(r.value, { stream: true });
          var lines = buf.split("\n");
          buf = lines.pop();
          lines.forEach(function (line) {
            line = line.trim(); if (!line) return;
            var ev; try { ev = JSON.parse(line); } catch (e) { return; }
            handlePollEvent(ev);
          });
          return pump();
        });
      }
      return pump();
    }).then(function () {
      if (btn) { btn.disabled = false; btn.innerHTML = orig; }
    }).catch(function () {
      // Fallback: single-request poll with an indeterminate bar + reload.
      progShow(true);
      request(CFG.pollUrl || ((CFG.base || "") + "/api/poll"), "POST", {}).then(function (res) {
        if (btn) { btn.disabled = false; btn.innerHTML = orig; }
        var d = res.data || {};
        if (d.ok) { progDone(d.summary); refreshChecks(); toast("اكتمل الفحص.", "success"); setTimeout(function () { location.reload(); }, 1200); }
        else { if (progEl) progEl.hidden = true; toast("تعذّر الفحص", "error"); }
      });
    });
  }

  /* ── filtering ── */
  function applyFilters() {
    var q = ($("#dh-search") && $("#dh-search").value || "").trim().toLowerCase();
    var router = $("#dh-filter-router") && $("#dh-filter-router").value || "";
    var type = $("#dh-filter-type") && $("#dh-filter-type").value || "";
    var status = $("#dh-filter-status") && $("#dh-filter-status").value || "";
    var visible = 0;
    $all("[data-dh-row]").forEach(function (row) {
      var ok = true;
      if (q && (row.getAttribute("data-search") || "").indexOf(q) === -1) ok = false;
      if (router && row.getAttribute("data-router") !== router) ok = false;
      if (type && row.getAttribute("data-type") !== type) ok = false;
      if (status && row.getAttribute("data-status") !== status) ok = false;
      row.classList.toggle("is-hidden", !ok);
      if (ok) visible++;
    });
    var emptyEl = $(".dh-empty-filtered");
    if (emptyEl) emptyEl.hidden = !($all("[data-dh-row]").length > 0 && visible === 0);
  }

  ["dh-search", "dh-filter-router", "dh-filter-type", "dh-filter-status"].forEach(function (id) {
    var el = document.getElementById(id);
    if (el) el.addEventListener("input", applyFilters);
    if (el) el.addEventListener("change", applyFilters);
  });

  /* ── live-apply panel toggle (controls real router writes) ── */
  var liveToggle = document.getElementById("dh-live-apply-toggle");
  if (liveToggle && CFG.liveApplyUrl) {
    liveToggle.addEventListener("change", function () {
      var on = liveToggle.checked;
      liveToggle.disabled = true;
      request(CFG.liveApplyUrl, "POST", { enabled: on }).then(function (res) {
        liveToggle.disabled = false;
        var d = res.data || {};
        if (d.ok) {
          var strip = document.getElementById("dh-liveapply");
          if (strip) strip.classList.toggle("is-on", !!d.enabled);
          if (d.enabled && d.effective === false) {
            toast("حُفظ التفعيل، لكنه مُعطَّل قسريًّا من إعداد الخادم.", "info");
          } else if (d.enabled) {
            toast("⚠️ التطبيق الحي مُفعّل — سيكتب النظام على الراوترات الحقيقية عند الضغط «تطبيق».", "info");
          } else {
            toast("أُطفئ التطبيق الحي — وضع المعاينة (dry-run) فقط.", "success");
          }
        } else {
          liveToggle.checked = !on;
          toast((d && d.error) || "تعذّر حفظ الإعداد", "error");
        }
      }).catch(function () {
        liveToggle.disabled = false; liveToggle.checked = !on;
        toast("تعذّر حفظ الإعداد", "error");
      });
    });
  }

  /* ── add / edit modal ── */
  var form = $("#dh-device-form");
  var modalTitle = function () {
    var m = $("#dh-device-modal");
    return m && m.querySelector(".uds-modal-head h3");
  };

  function resetForm() {
    if (!form) return;
    form.reset();
    form.querySelector("[name=device_id]").value = "";
    var err = $("#dh-form-error"); if (err) { err.hidden = true; err.textContent = ""; }
    var prev = $("#dh-plan-preview"); if (prev) prev.innerHTML = "";
    var t = modalTitle(); if (t) t.innerHTML = '<i class="fa-solid fa-tower-broadcast"></i> إضافة جهاز';
    ifaceUseFreeText();  // each open starts as free-text until a router is picked
  }

  function fillForm(d) {
    if (!form) return;
    resetForm();
    var t = modalTitle(); if (t) t.innerHTML = '<i class="fa-solid fa-pen"></i> تعديل: ' + (d.name || "");
    var map = ["device_id:id", "name", "device_type", "router_id", "interface_name",
      "ip_address", "location", "subnet_prefix", "gateway_last_octet",
      "ping_threshold_ms", "netwatch_interval_sec", "netwatch_timeout_sec", "alert_channel"];
    map.forEach(function (pair) {
      var parts = pair.split(":");
      var field = parts[0], key = parts[1] || parts[0];
      var input = form.querySelector("[name=" + field + "]");
      if (input) input.value = d[key] != null ? d[key] : "";
    });
    var chk = form.querySelector("[name=monitoring_enabled]");
    if (chk) chk.checked = !!d.monitoring_enabled;
    // Make advanced section visible when it holds edited values.
    var adv = form.querySelector(".dh-advanced"); if (adv) adv.open = true;
    // Load the router's interfaces, preselecting the saved one.
    loadInterfaces(d.router_id, d.interface_name);
  }

  $all("[data-dh-add]").forEach(function (b) { b.addEventListener("click", resetForm); });

  function collectForm() {
    var fd = new FormData(form);
    var obj = {};
    fd.forEach(function (v, k) { obj[k] = v; });
    obj.monitoring_enabled = !!form.querySelector("[name=monitoring_enabled]").checked;
    return obj;
  }

  if (form) {
    form.addEventListener("submit", function (e) {
      e.preventDefault();
      var data = collectForm();
      var id = (data.device_id || "").trim();
      delete data.device_id;
      var url = id ? api("/" + id) : API;
      var method = id ? "PATCH" : "POST";
      request(url, method, data).then(function (res) {
        if (res.data && res.data.ok) {
          if (res.data.warnings && res.data.warnings.length) toast(res.data.warnings[0], "info");
          if (id) {
            toast("تم حفظ التعديلات.", "success");
            setTimeout(function () { location.reload(); }, 500);
            return;
          }
          // إضافة جديدة: لا نكتفي بالتسجيل — نركّب الإعدادات على
          // المايكروتيك فورًا مع تقدم لكل أمر (الأمر الفعلي + نتيجته).
          toast("أُضيف الجهاز — جارٍ تركيب الإعدادات على المايكروتيك…", "success");
          var dev = res.data.device || {};
          runInstallSequence(dev.id);
        } else {
          var emsg = (res.data && res.data.error) || "تعذّر الحفظ.";
          // Design-system toast for the block (e.g. duplicate range on the same
          // interface) + keep the inline error box. Never a native alert.
          toast(emsg, "error");
          var err = $("#dh-form-error");
          if (err) { err.hidden = false; err.textContent = emsg; }
        }
      });
    });
  }

  /* ── تركيب إعدادات الجهاز على المايكروتيك بعد الإضافة ──
     يقرأ خطة المزامنة الحية (العناصر + أوامرها الفعلية) ثم يدفع كل
     عنصر وحده (actions:[kind]) فيظهر تقدم صادق: ما الأمر الذي رُفع،
     ماذا نجح، ماذا كان موجودًا، وماذا فشل ولماذا. البوابة المقفلة
     (التطبيق الحي مُطفأ) تُعرض قفلًا واضحًا بدل فشل صامت. */
  var INSTALL_KIND_LABELS = {
    ip_address: "عنوان البوابة على المدخل",
    ip_binding: "تجاوز Hotspot للشبكة",
    netwatch: "مراقبة Netwatch للجهاز"
  };

  function renderInstallSummary(ok, text) {
    var summary = $("#dh-install-summary");
    var doneBtn = $("#dh-install-done");
    if (summary) {
      summary.hidden = false;
      summary.textContent = text;
      summary.style.background = ok ? "#ECFDF5" : "#FFFBEB";
      summary.style.borderColor = ok ? "#A7F3D0" : "#FDE68A";
      summary.style.color = ok ? "#166534" : "#92400E";
    }
    if (doneBtn) doneBtn.hidden = false;
  }

  function runInstallSequence(deviceId) {
    var box = $("#dh-install-progress");
    var items = $("#dh-install-items");
    var bar = $("#dh-install-bar");
    var count = $("#dh-install-count");
    if (!box || !items || !deviceId) {
      setTimeout(function () { location.reload(); }, 600);
      return;
    }
    box.hidden = false;
    var summary = $("#dh-install-summary"); if (summary) summary.hidden = true;
    var doneBtn = $("#dh-install-done"); if (doneBtn) doneBtn.hidden = true;
    if (bar) bar.style.width = "0%";
    var saveBtn = document.querySelector('button[form="dh-device-form"]');
    if (saveBtn) saveBtn.disabled = true;
    box.scrollIntoView({ behavior: "smooth", block: "nearest" });

    items.innerHTML = '<div class="dh-plan-net"><i class="fa-solid fa-spinner fa-spin"></i> ' +
      'جارٍ قراءة حالة المايكروتيك وبناء خطة التركيب…</div>';
    request(api("/" + deviceId + "/sync"), "POST").then(function (res) {
      var d = res.data || {};
      if (!d.ok || !d.plan || !d.plan.valid) {
        items.innerHTML = "";
        renderInstallSummary(false,
          (d.error || "تعذّر قراءة المايكروتيك الآن.") +
          " الجهاز سُجّل — استخدم زر «مزامنة» في صفّ الجهاز لاحقًا لتركيب الإعدادات.");
        return;
      }
      var kinds = d.plan.items || [];
      items.innerHTML = "";
      var rows = {};
      kinds.forEach(function (it) {
        var el = document.createElement("div");
        el.style.cssText = "border:1px solid #EAECF0;border-radius:10px;padding:8px 12px";
        el.innerHTML = '<div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">' +
          '<span data-st style="flex:0 0 auto;color:#94A3B8"><i class="fa-regular fa-clock"></i></span>' +
          '<strong style="font-size:12.5px">' + esc(it.title || INSTALL_KIND_LABELS[it.kind] || it.kind) + '</strong>' +
          '<span data-msg style="font-size:11.5px;color:#64748B;margin-inline-start:auto"></span></div>' +
          '<code style="display:block;direction:ltr;text-align:left;font-size:11px;color:#475569;' +
          'background:#F8FAFC;border-radius:7px;padding:5px 8px;margin-top:6px;overflow-x:auto">' +
          esc(it.command || "") + '</code>';
        items.appendChild(el);
        rows[it.kind] = el;
      });
      var queue = kinds.slice();
      var done = 0, applied = 0, present = 0, gatedAll = false;
      var failures = [];

      function finish(row, ic, color, text) {
        var st = row.querySelector("[data-st]");
        var msg = row.querySelector("[data-msg]");
        if (st) st.innerHTML = '<i class="fa-solid ' + ic + '" style="color:' + color + '"></i>';
        if (msg) msg.textContent = text || "";
        done++;
        if (bar) bar.style.width = Math.round(done * 100 / kinds.length) + "%";
        if (count) count.textContent = done + " / " + kinds.length;
        step();
      }

      function step() {
        if (!queue.length) {
          if (gatedAll) {
            renderInstallSummary(false,
              "الجهاز سُجّل لكن لم يُدفع شيء للمايكروتيك: «التطبيق الحي على الراوترات» مُطفأ. " +
              "فعّل المفتاح أعلى الصفحة ثم اضغط «مزامنة» في صفّ الجهاز لتركيب الإعدادات.");
          } else if (failures.length) {
            renderInstallSummary(false,
              "اكتمل التركيب مع أخطاء: نجح " + applied + " · موجود مسبقًا " + present +
              " · فشل " + failures.length + " — راجع الأسباب أعلاه ثم أعد «مزامنة».");
          } else {
            renderInstallSummary(true,
              "اكتمل التركيب على المايكروتيك: أُضيف " + applied + " عنصر · " +
              present + " كان موجودًا مسبقًا.");
          }
          return;
        }
        var it = queue.shift();
        var row = rows[it.kind];
        if (it.action === "already_present") {
          present++;
          finish(row, "fa-circle-check", "#16A34A", "موجود مسبقًا على المايكروتيك");
          return;
        }
        if (gatedAll) {
          finish(row, "fa-lock", "#D97706", "لم يُدفع — التطبيق الحي مُطفأ");
          return;
        }
        var st = row.querySelector("[data-st]");
        var msg = row.querySelector("[data-msg]");
        if (st) st.innerHTML = '<i class="fa-solid fa-spinner fa-spin" style="color:#6366F1"></i>';
        if (msg) msg.textContent = "جارٍ الدفع للمايكروتيك…";
        request(api("/" + deviceId + "/apply"), "POST", { actions: [it.kind] }).then(function (r) {
          var a = r.data || {};
          if (a.gated) {
            gatedAll = true;
            finish(row, "fa-lock", "#D97706", "لم يُدفع — التطبيق الحي مُطفأ");
            return;
          }
          if ((a.applied || []).indexOf(it.kind) !== -1) {
            applied++;
            finish(row, "fa-circle-check", "#16A34A", "تم الدفع للمايكروتيك");
            return;
          }
          if ((a.already_present || []).indexOf(it.kind) !== -1) {
            present++;
            finish(row, "fa-circle-check", "#16A34A", "موجود مسبقًا على المايكروتيك");
            return;
          }
          var fmsg = ((a.failed || [])[0] || {}).error || a.error || "فشل غير معروف";
          failures.push(it.kind);
          finish(row, "fa-circle-xmark", "#DC2626", "فشل: " + fmsg);
        }).catch(function () {
          failures.push(it.kind);
          finish(row, "fa-circle-xmark", "#DC2626", "تعذّر الاتصال بالخادم");
        });
      }
      if (count) count.textContent = "0 / " + kinds.length;
      step();
    });
  }

  /* ── إعدادات الفحص الدوري ── */
  var pollSave = document.getElementById("dh-poll-save");
  if (pollSave && CFG.pollSettingsUrl) {
    pollSave.addEventListener("click", function () {
      var enabled = !!(document.getElementById("dh-poll-enabled") || {}).checked;
      var minutes = parseInt((document.getElementById("dh-poll-minutes") || {}).value, 10) || 5;
      pollSave.disabled = true;
      request(CFG.pollSettingsUrl, "POST", { enabled: enabled, minutes: minutes }).then(function (res) {
        pollSave.disabled = false;
        var d = res.data || {};
        if (d.ok) {
          toast(d.enabled ? ("الفحص الدوري مفعّل — كل " + d.minutes + " دقيقة.")
                          : "أُوقف الفحص الدوري.", "success");
        } else {
          toast((d && d.error) || "تعذّر حفظ الإعداد", "error");
        }
      }).catch(function () {
        pollSave.disabled = false;
        toast("تعذّر حفظ الإعداد", "error");
      });
    });
  }

  /* ── سجل الفحوصات: تحديث حي بعد «فحص الكل» + نافذة التفاصيل ── */
  function checkResultPill(c) {
    if (!c.ok) return '<span class="hub-pill hub-pill--red">تعذّر الفحص</span>';
    if (c.down_count) return '<span class="hub-pill hub-pill--red">انقطاعات</span>';
    if (c.high_latency) return '<span class="hub-pill hub-pill--amber">بنج عالٍ</span>';
    return '<span class="hub-pill hub-pill--green">سليم</span>';
  }

  function renderChecks(checks) {
    var body = document.getElementById("dh-checks-body");
    var empty = document.getElementById("dh-checks-empty");
    if (!body) return;
    if (empty) empty.hidden = checks.length > 0;
    body.innerHTML = checks.map(function (c) {
      var src = c.source === "poller"
        ? '<span class="hub-pill hub-pill--brand">دوري</span>'
        : '<span class="hub-pill hub-pill--grey">يدوي</span>';
      return '<tr data-dh-check="' + c.id + '">' +
        '<td class="mono" style="direction:ltr;font-size:12px">' + esc((c.created_at || "").slice(0, 16).replace("T", " ")) + '</td>' +
        '<td>' + src + '</td>' +
        '<td>' + (c.scanned || 0) + '</td>' +
        '<td style="color:#16A34A;font-weight:800">' + (c.up_count || 0) + '</td>' +
        '<td style="color:' + (c.down_count ? "#DC2626" : "#94A3B8") + ';font-weight:800">' + (c.down_count || 0) + '</td>' +
        '<td style="color:' + (c.high_latency ? "#D97706" : "#94A3B8") + ';font-weight:800">' + (c.high_latency || 0) + '</td>' +
        '<td>' + (c.changed || 0) + '</td>' +
        '<td class="mono" style="font-size:12px">' + ((c.duration_ms || 0) / 1000).toFixed(1) + 's</td>' +
        '<td>' + checkResultPill(c) + '</td>' +
        '<td><button type="button" class="hub-btn hub-btn--ghost hub-btn--sm" data-dh-check-details' +
        " data-details='" + esc(JSON.stringify(c.details || [])) + "'>" +
        '<i class="fa-solid fa-list"></i> المزيد</button></td></tr>';
    }).join("");
  }

  function renderCheckStats(s) {
    var wrap = document.getElementById("dh-checks-stats");
    if (!wrap) return;
    var vals = [s.checks || 0, s.downs || 0, s.changes || 0, s.alerts || 0];
    vals.forEach(function (v, i) {
      var el = wrap.querySelector('[data-dh-stat="' + i + '"]');
      if (el) el.textContent = v;
    });
    var last = wrap.querySelector('[data-dh-stat="last"]');
    if (last) last.textContent = (s.last_at || "—").slice(0, 16).replace("T", " ");
  }

  function refreshChecks() {
    if (!CFG.checksUrl) return;
    fetch(CFG.checksUrl, { credentials: "same-origin" })
      .then(function (r) { return r.json(); })
      .then(function (res) {
        if (!res || !res.ok) return;
        renderChecks(res.checks || []);
        renderCheckStats(res.stats || {});
      }).catch(function () {});
  }

  function openCheckModal(details) {
    var modal = $("#dh-check-modal");
    var body = $("#dh-check-body");
    if (!modal || !body) return;
    if (!details.length) {
      body.innerHTML = '<div class="dh-empty-mini">لا تفاصيل لهذه الدورة.</div>';
    } else {
      var html = '<ul class="dh-timeline">';
      details.forEach(function (d) {
        var m = STATUS_META[d.status] || STATUS_META.unknown;
        var color = m.v === "green" ? "#16A34A" : m.v === "red" ? "#DC2626"
          : m.v === "amber" ? "#D97706" : "#94A3B8";
        var lat = d.latency_ms != null ? " · " + d.latency_ms + " ms" : "";
        html += '<li class="dh-tl-item"><span class="dh-tl-dot" style="background:' + color + '"></span>' +
          '<div class="dh-tl-body"><span class="dh-tl-title" style="color:' + color + '">' +
          esc(d.name || ("#" + d.device_id)) + ' — ' + esc(m.label) + lat + '</span></div></li>';
      });
      body.innerHTML = html + '</ul>';
    }
    modal.hidden = false;
    modal.classList.add("is-open");
  }

  /* ── live plan preview inside the add/edit modal ── */
  var planTimer = null;
  function previewPlan() {
    if (!form) return;
    var iface = (form.querySelector("[name=interface_name]").value || "").trim();
    var ip = (form.querySelector("[name=ip_address]").value || "").trim();
    var box = $("#dh-plan-preview");
    if (!box) return;
    if (!iface || !ip) { box.innerHTML = ""; return; }
    var qs = "?interface=" + encodeURIComponent(iface) + "&ip=" + encodeURIComponent(ip) +
      "&subnet_prefix=" + encodeURIComponent(form.querySelector("[name=subnet_prefix]").value || 24) +
      "&gateway_last_octet=" + encodeURIComponent(form.querySelector("[name=gateway_last_octet]").value || 254);
    fetch(PLAN + qs, { credentials: "same-origin" })
      .then(function (r) { return r.json(); })
      .then(function (res) { box.innerHTML = renderPlan(res.plan, true); })
      .catch(function () { box.innerHTML = ""; });
  }
  if (form) {
    ["interface_name", "ip_address", "subnet_prefix", "gateway_last_octet"].forEach(function (n) {
      var el = form.querySelector("[name=" + n + "]");
      if (el) el.addEventListener("input", function () {
        clearTimeout(planTimer); planTimer = setTimeout(previewPlan, 350);
      });
    });
  }

  /* ── dependent interface dropdown (loads the router's LAN interfaces) ── */
  var ifaceSelect = $("#dh-iface-select");
  var ifaceInput = $("#dh-iface-input");
  var ifaceHint = $("#dh-iface-hint");
  var IFACE_DEFAULT_HINT = ifaceHint ? ifaceHint.textContent : "";

  function schedulePreview() { clearTimeout(planTimer); planTimer = setTimeout(previewPlan, 350); }

  function ifaceUseFreeText(msg) {
    if (ifaceSelect) { ifaceSelect.hidden = true; ifaceSelect.removeAttribute("name"); ifaceSelect.required = false; }
    if (ifaceInput) { ifaceInput.hidden = false; ifaceInput.setAttribute("name", "interface_name"); ifaceInput.required = true; }
    if (ifaceHint) ifaceHint.textContent = msg || IFACE_DEFAULT_HINT;
  }

  function ifaceUseSelect(list, current) {
    if (!ifaceSelect) return;
    var cur = current || (ifaceInput ? ifaceInput.value : "") || "";
    ifaceSelect.innerHTML = "";
    var ph = document.createElement("option");
    ph.value = ""; ph.textContent = "اختر المدخل…"; ifaceSelect.appendChild(ph);
    var matched = false;
    list.forEach(function (n) {
      var o = document.createElement("option");
      o.value = n; o.textContent = n;
      if (n === cur) { o.selected = true; matched = true; }
      ifaceSelect.appendChild(o);
    });
    if (cur && !matched) {  // keep a saved value even if it's now filtered out
      var o2 = document.createElement("option");
      o2.value = cur; o2.textContent = cur + " (محفوظ)"; o2.selected = true;
      ifaceSelect.appendChild(o2);
    }
    ifaceSelect.hidden = false; ifaceSelect.setAttribute("name", "interface_name"); ifaceSelect.required = true;
    if (ifaceInput) { ifaceInput.hidden = true; ifaceInput.removeAttribute("name"); ifaceInput.required = false; ifaceInput.value = ""; }
    if (ifaceHint) ifaceHint.textContent = "مداخل هذا المايكروتيك / السيرفر (مداخل دخول النت والأنفاق مستبعدة).";
    ifaceSelect.onchange = schedulePreview;
  }

  function loadInterfaces(routerId, current) {
    if (!CFG.ifacesUrl) { ifaceUseFreeText(); return; }
    if (!routerId) { ifaceUseFreeText(); return; }
    if (ifaceHint) ifaceHint.textContent = "جارٍ جلب مداخل المايكروتيك / السيرفر…";
    fetch(CFG.ifacesUrl + "?router_id=" + encodeURIComponent(routerId), { credentials: "same-origin" })
      .then(function (r) { return r.json(); })
      .then(function (res) {
        if (res && res.ok && res.online && res.interfaces && res.interfaces.length) {
          ifaceUseSelect(res.interfaces, current);
        } else if (res && res.ok && res.online) {
          ifaceUseFreeText("لا توجد مداخل LAN صالحة على هذا المايكروتيك / السيرفر — اكتب الاسم يدويًا.");
        } else {
          ifaceUseFreeText("تعذّر جلب المداخل (المايكروتيك / السيرفر غير متصل) — اكتب اسم المدخل يدويًا.");
        }
        schedulePreview();
      })
      .catch(function () { ifaceUseFreeText("تعذّر جلب المداخل — اكتب اسم المدخل يدويًا."); });
  }

  if (form) {
    var routerSel = form.querySelector("[name=router_id]");
    if (routerSel) routerSel.addEventListener("change", function () { loadInterfaces(routerSel.value); });
  }

  /* ── plan rendering ── */
  function actionMeta(action) {
    if (action === "already_present") return { cls: "is-present", ic: "fa-circle-check", color: "#16A34A", label: "موجود مسبقًا", variant: "green" };
    if (action === "create") return { cls: "is-create", ic: "fa-circle-plus", color: "#6366F1", label: "سيُضاف", variant: "brand" };
    return { cls: "is-planned", ic: "fa-circle-dot", color: "#94A3B8", label: "مُخطّط", variant: "grey" };
  }

  function esc(s) { var d = document.createElement("div"); d.textContent = s == null ? "" : s; return d.innerHTML; }

  function renderPlan(plan, compact) {
    if (!plan) return "";
    if (!plan.valid) return '<div class="dh-plan-warn">' + esc(plan.error || "خطة غير صالحة") + '</div>';
    var html = "";
    if (plan.network && !compact) {
      var n = plan.network;
      html += '<div class="dh-plan-net">الشبكة: <code>' + esc(n.network_cidr) +
        '</code> · البوابة: <code>' + esc(n.gateway_address) + '</code></div>';
    } else if (plan.network) {
      html += '<div class="dh-plan-net">الشبكة <code>' + esc(plan.network.network_cidr) +
        '</code> · البوابة <code>' + esc(plan.network.gateway_address) + '</code></div>';
    }
    (plan.items || []).forEach(function (it) {
      var m = actionMeta(it.action);
      html += '<div class="dh-plan-item ' + m.cls + '">' +
        '<i class="fa-solid ' + m.ic + ' dh-plan-ic" style="color:' + m.color + '"></i>' +
        '<div style="flex:1"><span class="dh-plan-title">' + esc(it.title) + '</span>' +
        '<code class="dh-plan-cmd">' + esc(it.command) + '</code></div>' +
        '<span class="dh-plan-badge" style="color:' + m.color + '">' + m.label + '</span></div>';
    });
    (plan.warnings || []).forEach(function (w) {
      html += '<div class="dh-plan-warn"><i class="fa-solid fa-triangle-exclamation"></i> ' + esc(w) + '</div>';
    });
    if (!plan.live) {
      html += '<div class="dh-plan-warn" style="background:#F1F5F9;color:#475569;border-color:#E2E8F0">' +
        'معاينة محسوبة — اضغط «مزامنة» للتحقق من حالة المايكروتيك / السيرفر الفعلية.</div>';
    }
    return html;
  }

  function openPlanModal(html) {
    var body = $("#dh-plan-body");
    if (body) body.innerHTML = html;
    var modal = $("#dh-plan-modal");
    if (modal) { modal.hidden = false; modal.classList.add("is-open"); }
  }

  /* ── row actions ── */
  function rowData(btn) {
    var row = btn.closest("[data-dh-row]");
    try { return { row: row, d: JSON.parse(row.getAttribute("data-json")) }; }
    catch (e) { return { row: row, d: {} }; }
  }

  document.addEventListener("click", function (e) {
    var btn = e.target.closest("button");
    if (!btn) return;

    if (btn.hasAttribute("data-dh-poll")) {
      streamPollAll(btn);  // live progress bar + per-row updates
      return;
    }

    if (btn.hasAttribute("data-dh-edit")) { fillForm(rowData(btn).d); return; }

    if (btn.hasAttribute("data-dh-plan")) {
      var d = rowData(btn).d;
      var qs = "?interface=" + encodeURIComponent(d.interface_name) + "&ip=" + encodeURIComponent(d.ip_address) +
        "&subnet_prefix=" + encodeURIComponent(d.subnet_prefix) +
        "&gateway_last_octet=" + encodeURIComponent(d.gateway_last_octet);
      openPlanModal('<div class="dh-plan-net">جارٍ الحساب…</div>');
      fetch(PLAN + qs, { credentials: "same-origin" }).then(function (r) { return r.json(); })
        .then(function (res) { openPlanModal(renderPlan(res.plan, false)); });
      return;
    }

    if (btn.hasAttribute("data-dh-sync")) {
      var id = rowData(btn).d.id;
      openPlanModal('<div class="dh-plan-net">جارٍ قراءة المايكروتيك / السيرفر…</div>');
      request(api("/" + id + "/sync"), "POST").then(function (res) {
        if (res.data && res.data.ok) {
          var extra = res.data.router_state_ok ? "" :
            '<div class="dh-plan-warn">تعذّر قراءة بعض موارد المايكروتيك / السيرفر — الخطة تقديرية.</div>';
          var hasCreate = (res.data.plan.items || []).some(function (it) { return it.action === "create"; });
          var applyBtn = hasCreate ?
            '<div class="dh-plan-apply"><button class="hub-btn hub-btn--primary" data-dh-apply data-id="' + id +
            '"><i class="fa-solid fa-cloud-arrow-up"></i> تطبيق العناصر المفقودة على المايكروتيك / السيرفر</button>' +
            '<span class="dh-apply-hint">يتطلّب تفعيل التطبيق الحيّ — لا يحذف أي إعداد قائم.</span></div>' : "";
          openPlanModal(extra + renderPlan(res.data.plan, false) + applyBtn);
        } else {
          openPlanModal('<div class="dh-plan-warn">' + esc((res.data && res.data.error) || "تعذّرت المزامنة") + '</div>');
        }
      });
      return;
    }

    if (btn.hasAttribute("data-dh-apply")) {
      var aid = btn.getAttribute("data-id");
      btn.disabled = true;
      btn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> جارٍ التطبيق…';
      request(api("/" + aid + "/apply"), "POST", {}).then(function (res) {
        var d = res.data || {};
        if (d.gated) {
          toast("التطبيق الحيّ معطّل — فعّل HOBERADIUS_DEVICE_HEALTH_LIVE_APPLY.", "info");
          btn.disabled = false; btn.innerHTML = '<i class="fa-solid fa-cloud-arrow-up"></i> تطبيق العناصر المفقودة على المايكروتيك / السيرفر';
          return;
        }
        if (d.ok) {
          toast("تم التطبيق: " + (d.applied || []).length + " عنصر.", "success");
          setTimeout(function () { location.reload(); }, 900);
        } else {
          var msg = (d.failed && d.failed.length) ? d.failed.map(function (f) { return f.kind + ": " + f.error; }).join(" · ")
            : (d.error || "تعذّر التطبيق");
          toast(msg, "error");
          btn.disabled = false; btn.innerHTML = '<i class="fa-solid fa-cloud-arrow-up"></i> إعادة المحاولة';
        }
      });
      return;
    }

    if (btn.hasAttribute("data-dh-ping")) {
      var pinfo = rowData(btn);
      var pid = pinfo.d.id, prow = pinfo.row;
      btn.disabled = true;
      var pico = btn.innerHTML; btn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i>';
      setRowChecking(prow);  // inline «يفحص…» on the row
      request(api("/" + pid + "/test-ping"), "POST").then(function (res) {
        btn.disabled = false; btn.innerHTML = pico;
        var d = res.data || {};
        if (d.ok) {
          updateRowStatus(prow, d.status, d.latency_ms);  // live row update, no reload
          var lab = (STATUS_META[d.status] || {}).label || d.status;
          var lat = d.latency_ms != null ? (d.latency_ms + " ms") : "—";
          toast("النتيجة: " + lab + " · " + lat, d.status === "up" ? "success" : "info");
        } else {
          updateRowStatus(prow, pinfo.d.status, pinfo.d.last_latency_ms);  // restore
          toast(d.error || "تعذّر الفحص", "error");
        }
      }).catch(function () {
        btn.disabled = false; btn.innerHTML = pico;
        updateRowStatus(prow, pinfo.d.status, pinfo.d.last_latency_ms);
        toast("تعذّر الفحص", "error");
      });
      return;
    }

    if (btn.hasAttribute("data-dh-toggle")) {
      var tid = rowData(btn).d.id;
      var enabled = btn.getAttribute("data-enabled") === "1";
      request(api("/" + tid + (enabled ? "/disable" : "/enable")), "POST").then(function (res) {
        if (res.data && res.data.ok) location.reload();
        else toast((res.data && res.data.error) || "تعذّر التغيير", "error");
      });
      return;
    }

    if (btn.hasAttribute("data-dh-events")) {
      var ed = rowData(btn).d;
      openEventsModal(ed);
      return;
    }

    if (btn.hasAttribute("data-dh-tab")) {
      var modal = $("#dh-events-modal");
      $all(".dh-tab", modal).forEach(function (t) { t.classList.toggle("is-active", t === btn); });
      loadEventsTab(modal.getAttribute("data-device-id"), btn.getAttribute("data-dh-tab"));
      return;
    }

    if (btn.hasAttribute("data-dh-check-details")) {
      var det;
      try { det = JSON.parse(btn.getAttribute("data-details") || "[]"); }
      catch (err) { det = []; }
      openCheckModal(det);
      return;
    }

    if (btn.hasAttribute("data-dh-delete")) {
      var info = rowData(btn);
      if (!window.confirm("حذف الجهاز «" + (info.d.name || "") + "»؟")) return;
      request(api("/" + info.d.id + "/delete"), "POST").then(function (res) {
        if (res.data && res.data.ok) { info.row.remove(); toast("حُذف الجهاز.", "success"); applyFilters(); }
        else toast((res.data && res.data.error) || "تعذّر الحذف", "error");
      });
      return;
    }
  });

  /* ── event history + alerts modal ── */
  var EVENT_LABELS = {
    up: "متصل", down: "مفصول", timeout: "انتهت المهلة", high_latency: "بنج عالٍ",
    unknown: "غير معروف", disabled: "معطّل", apply_failed: "فشل التطبيق",
    created: "تسجيل", updated: "تحديث", recovered: "تعافى", recovery: "تعافى"
  };
  var EVENT_COLOR = {
    up: "#16A34A", recovery: "#16A34A", down: "#DC2626", timeout: "#DC2626",
    high_latency: "#D97706", apply_failed: "#DC2626", created: "#6366F1", updated: "#6366F1"
  };
  var ALERT_STATUS = { sent: "أُرسل", skipped: "مُتجاوز (تهدئة)", failed: "فشل الإرسال" };

  function openEventsModal(d) {
    var modal = $("#dh-events-modal");
    if (!modal) return;
    modal.setAttribute("data-device-id", d.id);
    $all(".dh-tab", modal).forEach(function (t) { t.classList.toggle("is-active", t.getAttribute("data-dh-tab") === "events"); });
    var head = modal.querySelector(".uds-modal-head h3");
    if (head) head.innerHTML = '<i class="fa-solid fa-clock-rotate-left"></i> سجل «' + esc(d.name || "") + '»';
    modal.hidden = false; modal.classList.add("is-open");
    loadEventsTab(d.id, "events");
  }

  function loadEventsTab(deviceId, tab) {
    var body = $("#dh-events-body");
    if (!body) return;
    body.innerHTML = '<div class="dh-plan-net">جارٍ التحميل…</div>';
    var url = api("/" + deviceId + "/" + (tab === "alerts" ? "alerts" : "events"));
    fetch(url, { credentials: "same-origin" }).then(function (r) { return r.json(); })
      .then(function (res) {
        if (tab === "alerts") body.innerHTML = renderAlerts(res.alerts || []);
        else body.innerHTML = renderEvents(res.events || []);
      }).catch(function () { body.innerHTML = '<div class="dh-plan-warn">تعذّر التحميل</div>'; });
  }

  function renderEvents(events) {
    if (!events.length) return '<div class="dh-empty-mini">لا أحداث بعد.</div>';
    var html = '<ul class="dh-timeline">';
    events.forEach(function (e) {
      var color = EVENT_COLOR[e.event_type] || "#94A3B8";
      var lbl = EVENT_LABELS[e.new_status] || EVENT_LABELS[e.event_type] || e.event_type;
      var lat = e.latency_ms != null ? ' · ' + e.latency_ms + ' ms' : '';
      html += '<li class="dh-tl-item"><span class="dh-tl-dot" style="background:' + color + '"></span>' +
        '<div class="dh-tl-body"><span class="dh-tl-title" style="color:' + color + '">' + esc(lbl) + lat + '</span>' +
        '<span class="dh-tl-msg">' + esc(e.message || "") + '</span>' +
        '<span class="dh-tl-time">' + esc(e.created_at || "") + '</span></div></li>';
    });
    return html + '</ul>';
  }

  function renderAlerts(alerts) {
    if (!alerts.length) return '<div class="dh-empty-mini">لا تنبيهات بعد.</div>';
    var html = '<ul class="dh-timeline">';
    alerts.forEach(function (a) {
      var color = a.status === "sent" ? "#16A34A" : a.status === "failed" ? "#DC2626" : "#94A3B8";
      var atype = EVENT_LABELS[a.alert_type] || a.alert_type;
      html += '<li class="dh-tl-item"><span class="dh-tl-dot" style="background:' + color + '"></span>' +
        '<div class="dh-tl-body"><span class="dh-tl-title" style="color:' + color + '">' + esc(atype) +
        ' · ' + esc(ALERT_STATUS[a.status] || a.status) + ' · ' + esc(a.channel || "") + '</span>' +
        '<span class="dh-tl-time">' + esc(a.created_at || "") + '</span></div></li>';
    });
    return html + '</ul>';
  }

  applyFilters();
})();
