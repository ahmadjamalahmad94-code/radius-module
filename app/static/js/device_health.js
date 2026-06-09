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
          toast(id ? "تم حفظ التعديلات." : "أُضيف الجهاز.", "success");
          setTimeout(function () { location.reload(); }, 500);
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
      btn.disabled = true;
      var orig = btn.innerHTML;
      btn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> جارٍ الفحص…';
      request((CFG.base || "") + "/api/poll", "POST", {}).then(function (res) {
        btn.disabled = false; btn.innerHTML = orig;
        if (res.data && res.data.ok) {
          var s = res.data.summary || {};
          toast("فُحص " + (s.scanned || 0) + " · متصل " + (s.up || 0) + " · مفصول " + (s.down || 0), "success");
          setTimeout(function () { location.reload(); }, 900);
        } else {
          toast("تعذّر الفحص", "error");
        }
      });
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
      var pid = rowData(btn).d.id;
      btn.disabled = true;
      request(api("/" + pid + "/test-ping"), "POST").then(function (res) {
        btn.disabled = false;
        if (res.data && res.data.ok) {
          var lat = res.data.latency_ms != null ? res.data.latency_ms + " ms" : "—";
          toast("النتيجة: " + res.data.status + " · " + lat, res.data.status === "up" ? "success" : "info");
          setTimeout(function () { location.reload(); }, 700);
        } else {
          toast((res.data && res.data.error) || "تعذّر الفحص", "error");
        }
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
