(function () {
  "use strict";

  var busyForm = null;
  var lastUpdateAt = 0;

  function ensurePanel() {
    var panel = document.querySelector("[data-card-generate-progress]");
    if (panel) return panel;
    panel = document.createElement("div");
    panel.className = "card-generate-progress";
    panel.setAttribute("data-card-generate-progress", "");
    panel.hidden = true;
    panel.innerHTML = [
      '<div class="card-generate-progress__backdrop"></div>',
      '<section class="card-generate-progress__panel" role="status" aria-live="polite">',
      '  <div class="card-generate-progress__icon"><i class="fa-solid fa-wand-magic-sparkles"></i></div>',
      '  <h3>إنشاء الحزمة</h3>',
      '  <p data-progress-message>تجهيز طلب التوليد...</p>',
      '  <div class="card-generate-progress__bar"><span data-progress-bar></span></div>',
      '  <div class="card-generate-progress__meta">',
      '    <span data-progress-phase>بدء</span>',
      '    <strong data-progress-count>0 / 0</strong>',
      '  </div>',
      '  <small data-progress-hint>لا تغلق الصفحة حتى يكتمل إنشاء البطاقات.</small>',
      // MT84 — زرّ إغلاق يظهر **عند الخطأ فقط**: قبل هذا كانت النافذة بلا أيّ
      // مخرج، فإذا فشل التوليد بقيت الحجب فوق الصفحة وتعلّق اللوحة كلّها
      // ولا حيلة إلا إعادة التحميل. يبقى مخفيًّا أثناء العمل كي لا يُغري
      // المشغّل بإغلاق عمليّةٍ جارية.
      '  <button type="button" class="card-generate-progress__close" data-progress-close hidden>إغلاق</button>',
      '</section>'
    ].join("");
    document.body.appendChild(panel);
    panel.addEventListener("click", function (ev) {
      if (ev.target.closest("[data-progress-close]")
          || (ev.target.classList.contains("card-generate-progress__backdrop")
              && panel.classList.contains("is-error"))) {
        panel.hidden = true;
      }
    });
    document.addEventListener("keydown", function (ev) {
      if (ev.key === "Escape" && !panel.hidden && panel.classList.contains("is-error")) {
        panel.hidden = true;
      }
    });
    return panel;
  }

  function setBusy(form, busy) {
    form.dataset.generating = busy ? "1" : "";
    Array.prototype.slice.call(form.querySelectorAll('button[type="submit"]')).forEach(function (btn) {
      btn.disabled = busy;
      btn.classList.toggle("is-loading", busy);
    });
  }

  function phaseLabel(phase) {
    return {
      queued: "بالانتظار",
      validating: "فحص",
      preparing: "تجهيز",
      batch: "إنشاء الحزمة",
      generating: "توليد البطاقات",
      syncing: "تجهيز خدمة المصادقة",
      done: "اكتمل",
      error: "خطأ"
    }[phase] || "جارٍ العمل";
  }

  function updatePanel(data) {
    var panel = ensurePanel();
    panel.hidden = false;
    var total = Number(data.total || 0);
    var current = Number(data.current || data.generated || 0);
    var pct = total > 0 ? Math.max(4, Math.min(100, Math.round((current / total) * 100))) : 8;
    panel.querySelector("[data-progress-message]").textContent = data.message || "جارٍ إنشاء البطاقات...";
    panel.querySelector("[data-progress-phase]").textContent = phaseLabel(data.phase || data.status);
    panel.querySelector("[data-progress-count]").textContent = current + " / " + total;
    panel.querySelector("[data-progress-bar]").style.width = pct + "%";
    var hint = panel.querySelector("[data-progress-hint]");
    var closeBtn = panel.querySelector("[data-progress-close]");
    if (closeBtn) closeBtn.hidden = (data.status !== "error");
    if (data.status === "error") {
      panel.classList.add("is-error");
      hint.textContent = "لم يكتمل التوليد. راجع الرسالة ثم حاول مرة أخرى.";
    } else if (Date.now() - lastUpdateAt > 15000 && data.status === "running") {
      hint.textContent = "التوليد ما زال يعمل. إذا بقيت هذه الحالة طويلًا افحص الاتصال أو سجل الخادم.";
    } else {
      panel.classList.remove("is-error");
      hint.textContent = "لا تغلق الصفحة حتى يكتمل إنشاء البطاقات.";
    }
  }

  async function poll(statusUrl) {
    var res = await fetch(statusUrl, { headers: { Accept: "application/json" } });
    var data = await res.json();
    lastUpdateAt = Date.now();
    updatePanel(data);
    if (data.status === "done") {
      setTimeout(function () {
        window.location.href = data.redirect_url || window.location.href;
      }, 600);
      return;
    }
    if (data.status === "error" || data.ok === false) {
      if (busyForm) setBusy(busyForm, false);
      busyForm = null;
      return;
    }
    setTimeout(function () { poll(statusUrl).catch(handleError); }, 550);
  }

  function handleError(error) {
    updatePanel({
      status: "error",
      phase: "error",
      current: 0,
      total: 0,
      message: error && error.message ? error.message : "تعذر متابعة حالة التوليد."
    });
    if (busyForm) setBusy(busyForm, false);
    busyForm = null;
  }

  async function startGenerate(form) {
    if (form.dataset.generating === "1") return;
    busyForm = form;
    setBusy(form, true);
    lastUpdateAt = Date.now();
    updatePanel({ status: "queued", phase: "queued", current: 0, total: Number(form.elements.count && form.elements.count.value) || 0, message: "إرسال طلب التوليد..." });
    var res = await fetch(form.dataset.progressStartUrl, {
      method: "POST",
      body: new FormData(form),
      headers: { Accept: "application/json", "X-Requested-With": "fetch" }
    });
    var data = await res.json();
    if (!res.ok || data.ok === false) {
      throw new Error(data.error || data.message || "تعذر بدء التوليد.");
    }
    var statusUrl = form.dataset.progressStatusUrl.replace("__JOB_ID__", data.job_id);
    poll(statusUrl).catch(handleError);
  }

  // fix2 (R13-L3): ملخّصٌ يؤكّده المشغّل قبل كلّ توليد — «رجوع» ثمّ إرسالٌ
  // بقيمٍ مُعادة جزئيًّا أنشأ حزمة 20 بطاقة لم يطلبها أحد.
  function generateSummary(form) {
    var el = form.elements;
    var count = (el.count && el.count.value) || "?";
    var planSel = el.plan_id;
    var plan = planSel && planSel.options && planSel.selectedIndex >= 0
      ? (planSel.options[planSel.selectedIndex].text || "").trim() : "";
    var name = (el.package_name && el.package_name.value || "").trim();
    var len = (el.username_length && el.username_length.value) || "";
    var pre = (el.username_prefix && el.username_prefix.value || "").trim();
    var lines = ["توليد " + count + " بطاقة" + (plan ? " على الباقة «" + plan + "»" : "") + "؟"];
    if (name) lines.push("اسم الحزمة: " + name);
    if (len) lines.push("طول اسم الدخول: " + len + (pre ? " · البادئة: " + pre : ""));
    return lines.join("\n");
  }

  function confirmGenerate(form) {
    if (!form.hasAttribute("data-confirm-generate")) return Promise.resolve(true);
    var msg = generateSummary(form);
    if (window.UDS && typeof window.UDS.confirm === "function") {
      return window.UDS.confirm({ message: msg });
    }
    return Promise.resolve(window.confirm(msg));
  }

  document.addEventListener("submit", function (event) {
    var form = event.target.closest("[data-card-generate-form]");
    if (!form) return;
    if (form.dataset.confirmed === "1") { form.dataset.confirmed = ""; return; }
    var progress = window.fetch && form.dataset.progressStartUrl && form.dataset.progressStatusUrl;
    if (!progress && !form.hasAttribute("data-confirm-generate")) return;
    event.preventDefault();
    if (form.dataset.generating === "1") return;
    confirmGenerate(form).then(function (ok) {
      if (!ok) return;
      if (progress) {
        startGenerate(form).catch(handleError);
      } else {
        form.dataset.confirmed = "1";
        if (typeof form.requestSubmit === "function") form.requestSubmit();
        else form.submit();
      }
    });
  });

  // fix2 (R13-L3): الرجوع إلى الصفحة (bfcache أو إعادة تحميلٍ بسجلّ التنقّل)
  // يُعيد ضبط النموذج **كاملًا** ويقول ذلك صراحةً — لا استعادة جزئيّة.
  window.addEventListener("pageshow", function (event) {
    var nav = (performance.getEntriesByType && performance.getEntriesByType("navigation")[0]) || null;
    var back = event.persisted || (nav && nav.type === "back_forward");
    if (!back) return;
    Array.prototype.slice.call(document.querySelectorAll("[data-card-generate-form]")).forEach(function (form) {
      form.reset();
      setBusy(form, false);
      form.dataset.confirmed = "";
    });
    var panel = document.querySelector("[data-card-generate-progress]");
    if (panel) panel.hidden = true;
    var note = document.querySelector("[data-generate-back-notice]");
    if (note) note.hidden = false;
  });
})();
