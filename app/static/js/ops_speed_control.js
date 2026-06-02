/* ============================================================================
   Bandwidth Monitoring / Speed Control — interactive engine
   Vanilla IIFE. Two modes (unified % / separate download+upload %).
   Smooth rAF sliders, live effective-speed computation, dry-run save via the
   hidden form (CSRF carried by the form's _csrf_token field).
   All calculations are client-side previews; nothing touches a router until an
   explicit dry-run policy is saved on the server.
   ========================================================================== */
(function () {
  "use strict";

  var root = document.querySelector("[data-speed-control]");
  if (!root) return;

  var THUMB = 20; // px, matches CSS thumb width
  var RING_C = 2 * Math.PI * 52; // donut circumference (r=52)

  // ── helpers ──────────────────────────────────────────────────────────
  function $(sel, el) { return (el || root).querySelector(sel); }
  function $all(sel, el) { return Array.prototype.slice.call((el || root).querySelectorAll(sel)); }
  function clamp(n, lo, hi) { return Math.max(lo, Math.min(hi, n)); }
  function mb(kbps) { return (kbps / 1000).toFixed(2); }
  function avg(list) { return list.length ? list.reduce(function (a, b) { return a + b; }, 0) / list.length : 0; }

  // Boost zones (ring label colour): ≤100% base, 100–200% amber, 200–300% red.
  function applyZone(el, v) {
    if (!el) return;
    el.classList.toggle("is-warn", v > 100 && v <= 200);
    el.classList.toggle("is-danger", v > 200);
  }

  // Live colour interpolation: base → amber (100–200%) → red (200–300%), so the
  // slider colour builds up gradually as the value climbs (no hard switch).
  var C_PURPLE = [107, 90, 237], C_TEAL = [13, 148, 136], C_AMBER = [245, 158, 11], C_RED = [220, 38, 38];
  function lerp(a, b, t) {
    return [Math.round(a[0] + (b[0] - a[0]) * t), Math.round(a[1] + (b[1] - a[1]) * t), Math.round(a[2] + (b[2] - a[2]) * t)];
  }
  function rgb(c) { return "rgb(" + c[0] + "," + c[1] + "," + c[2] + ")"; }
  function valueColor(val, base) {
    if (val <= 100) return base;
    if (val <= 200) return lerp(base, C_AMBER, (val - 100) / 100);
    return lerp(C_AMBER, C_RED, clamp((val - 200) / 100, 0, 1));
  }

  // ── slider registry (for accurate px positioning + resize) ─────────────
  var sliders = [];
  function bindSlider(input, onInput) {
    var wrap = input.closest("[data-slider-wrap]");
    var rail = input.parentElement; // .spdx-slider__rail
    if (!wrap || !rail) return null;
    var fill = $("[data-fill]", wrap);
    var bubble = $("[data-bubble]", wrap);
    var base = input.classList.contains("spdx-slider__input--teal") ? C_TEAL : C_PURPLE;
    var raf = null, lastColorVal = -1;

    function render() {
      var min = +input.min || 0, max = +input.max || 100, val = +input.value;
      var frac = (val - min) / (max - min || 1);
      var w = rail.clientWidth || 0;
      var center = THUMB / 2 + frac * (w - THUMB);
      if (fill) fill.style.width = center + "px";
      if (bubble) { bubble.style.left = center + "px"; bubble.textContent = Math.round(val) + "%"; }
      // Gradual colour build-up — solid colour (cheap to repaint) refreshed only
      // when the rounded value changes, so dragging stays smooth.
      var rv = Math.round(val);
      if (rv !== lastColorVal) {
        lastColorVal = rv;
        var c = rgb(valueColor(val, base));
        if (fill) fill.style.background = c;
        wrap.style.setProperty("--bubble-c", c);
        input.style.setProperty("--thumb-c", c);
      }
    }
    function schedule() { if (raf) return; raf = requestAnimationFrame(function () { raf = null; render(); }); }

    input.addEventListener("input", function () {
      wrap.classList.add("is-dragging");
      schedule();
      if (onInput) onInput(+input.value);
    });
    var stop = function () { wrap.classList.remove("is-dragging"); };
    input.addEventListener("change", stop);
    input.addEventListener("pointerup", stop);
    input.addEventListener("blur", stop);

    var rec = { render: render, input: input };
    input._spdxRender = render; // O(1) lookup for setSlider (avoids array scans)
    sliders.push(rec);
    // Re-measure whenever the rail's width changes — covers window resize AND
    // sidebar collapse/expand (which reflows the layout without a resize event),
    // so the px-positioned fill/bubble never drift over neighbouring elements.
    if (window.ResizeObserver) {
      var ro = new ResizeObserver(function () { render(); });
      ro.observe(rail);
    }
    render();
    return rec;
  }
  window.addEventListener("resize", function () {
    sliders.forEach(function (s) { s.render(); });
  });

  function setSlider(input, value) {
    if (!input) return;
    input.value = value;
    if (input._spdxRender) input._spdxRender();
  }

  // ── model ──────────────────────────────────────────────────────────────
  var state = { mode: "unified", controlledOnly: true, globalUni: 100, globalDown: 100, globalUp: 100 };

  var profiles = $all("[data-profile]").map(function (el) {
    var p = {
      el: el,
      id: el.getAttribute("data-id"),
      name: el.getAttribute("data-name"),
      down: +el.getAttribute("data-down") || 0,
      up: +el.getAttribute("data-up") || 0,
      isDefault: el.getAttribute("data-default") === "1",
      enabled: !!$("[data-enable]", el) && $("[data-enable]", el).checked,
      uni: 100, dn: 100, upp: 100,
      effDownEl: $("[data-eff-down]", el),
      effUpEl: $("[data-eff-up]", el),
      statusEl: $("[data-status]", el)
    };
    return p;
  });

  // ── live computation ─────────────────────────────────────────────────
  function recompute() {
    var enabled = profiles.filter(function (p) { return p.enabled; });

    // per-profile effective speeds — always show both directions. In unified
    // mode the single % applies to download AND upload; in separate mode each
    // direction uses its own %.
    profiles.forEach(function (p) {
      var fd = (state.mode === "unified" ? p.uni : p.dn) / 100;
      var fu = (state.mode === "unified" ? p.uni : p.upp) / 100;
      if (p.effDownEl) p.effDownEl.textContent = mb(p.down * fd);
      if (p.effUpEl) p.effUpEl.textContent = mb(p.up * fu);
    });

    // KPIs
    setText("[data-kpi-total]", profiles.length);
    setText("[data-kpi-controlled]", enabled.length);
    setText("[data-kpi-uncontrolled]", profiles.length - enabled.length);

    if (state.mode === "unified") {
      setText("[data-kpi-avg]", Math.round(avg(enabled.map(function (p) { return p.uni; }))) || 0);
      // donut follows the unified master
      renderRing(state.globalUni);
      renderImpact(enabled);
    } else {
      setText("[data-kpi-avg]", Math.round(avg(enabled.map(function (p) { return p.dn; }))) || 0);
      setText("[data-kpi-avg2]", Math.round(avg(enabled.map(function (p) { return p.upp; }))) || 0);
      setText("[data-global-down-pct]", state.globalDown);
      setText("[data-global-up-pct]", state.globalUp);
      setText("[data-global-down-echo]", state.globalDown + "%");
      setText("[data-global-up-echo]", state.globalUp + "%");
    }
  }

  // rAF-throttle recompute so rapid slider input coalesces to one update/frame.
  var recomputeRaf = null;
  function scheduleRecompute() {
    if (recomputeRaf) return;
    recomputeRaf = requestAnimationFrame(function () { recomputeRaf = null; recompute(); });
  }

  function renderRing(pct) {
    // Three laps: each 100% completes a full circle and the next colour layers on
    // top, so 150% = full purple + half amber, 250% = full amber + half red.
    setLap(1, clamp(pct, 0, 100) / 100);
    setLap(2, clamp(pct - 100, 0, 100) / 100);
    setLap(3, clamp(pct - 200, 0, 100) / 100);
    setText("[data-ring-value]", Math.round(pct));
    applyZone($(".spdx-ring"), pct);
  }
  function setLap(n, frac) {
    var c = $('[data-ring-lap="' + n + '"]');
    if (c) { c.style.strokeDasharray = RING_C; c.style.strokeDashoffset = RING_C * (1 - frac); }
  }

  function renderImpact(enabled) {
    // Totals cover download + upload (the unified % applies to both directions).
    var base = enabled.reduce(function (s, p) { return s + p.down + p.up; }, 0);
    var eff = enabled.reduce(function (s, p) { return s + (p.down + p.up) * (p.uni / 100); }, 0);
    var note = $("[data-impact-note]");
    if (note) note.innerHTML = "سيتم تطبيق <b>" + Math.round(state.globalUni) + "%</b> على <b>" + enabled.length + "</b> باقات.";
    setText("[data-impact-base]", mb(base) + " Mb");
    setText("[data-impact-eff]", mb(eff) + " Mb");
    var delta = eff - base;
    var pct = base > 0 ? Math.round((delta / base) * 100) : 0;
    var deltaEl = $("[data-impact-delta]");
    if (deltaEl) {
      var down = delta <= 0;
      deltaEl.classList.toggle("is-zero", Math.abs(delta) < 0.005);
      deltaEl.innerHTML = '<i class="fa-solid fa-arrow-' + (down ? "down" : "up") + '" aria-hidden="true"></i> ' +
        mb(Math.abs(delta)) + " Mb (" + pct + "%)";
    }
  }

  function setText(sel, val) { var el = $(sel); if (el) el.textContent = val; }

  // ── bind global sliders ────────────────────────────────────────────────
  // A profile receives the global push when it's enabled, OR when the
  // "controlled-only" guard is off (then the global applies to every profile).
  function receivesGlobal(p) { return p.enabled || !state.controlledOnly; }

  var gUni = $("[data-global-uni]");
  if (gUni) bindSlider(gUni, function (v) {
    state.globalUni = v;
    profiles.forEach(function (p) { if (receivesGlobal(p)) { p.uni = v; setSlider($("[data-row-uni]", p.el), v); } });
    scheduleRecompute();
  });
  var gDown = $("[data-global-down]");
  if (gDown) bindSlider(gDown, function (v) {
    state.globalDown = v;
    profiles.forEach(function (p) { if (receivesGlobal(p)) { p.dn = v; setSlider($("[data-row-down]", p.el), v); } });
    scheduleRecompute();
  });
  var gUp = $("[data-global-up]");
  if (gUp) bindSlider(gUp, function (v) {
    state.globalUp = v;
    profiles.forEach(function (p) { if (receivesGlobal(p)) { p.upp = v; setSlider($("[data-row-up]", p.el), v); } });
    scheduleRecompute();
  });

  // ── bind per-profile controls ──────────────────────────────────────────
  profiles.forEach(function (p) {
    var rUni = $("[data-row-uni]", p.el);
    var rDown = $("[data-row-down]", p.el);
    var rUp = $("[data-row-up]", p.el);
    if (rUni) bindSlider(rUni, function (v) { p.uni = v; scheduleRecompute(); });
    if (rDown) bindSlider(rDown, function (v) { p.dn = v; scheduleRecompute(); });
    if (rUp) bindSlider(rUp, function (v) { p.upp = v; scheduleRecompute(); });

    var enable = $("[data-enable]", p.el);
    if (enable) enable.addEventListener("change", function () {
      p.enabled = enable.checked;
      p.el.classList.toggle("is-off", !p.enabled);
      if (p.statusEl) p.statusEl.textContent = p.enabled ? "مفعّل" : "متوقّف";
      recompute();
    });

    var restoreOne = $("[data-action='restore-one']", p.el);
    if (restoreOne) restoreOne.addEventListener("click", function () {
      p.uni = p.dn = p.upp = 100;
      if (rUni) setSlider(rUni, 100);
      if (rDown) setSlider(rDown, 100);
      if (rUp) setSlider(rUp, 100);
      recompute();
    });
  });

  // controlled-only toggle
  var ctrlOnly = $("[data-controlled-only]");
  if (ctrlOnly) ctrlOnly.addEventListener("change", function () { state.controlledOnly = ctrlOnly.checked; recompute(); });

  // ── mode switch ──────────────────────────────────────────────────────
  function setMode(mode) {
    state.mode = mode;
    root.setAttribute("data-mode", mode);
    $all("[data-mode-btn]").forEach(function (b) {
      var on = b.getAttribute("data-mode-btn") === mode;
      b.classList.toggle("is-active", on);
      b.setAttribute("aria-pressed", on ? "true" : "false");
    });
    // section views
    $all("[data-view]").forEach(function (v) { v.hidden = v.getAttribute("data-view") !== mode; });
    // control blocks + speed columns
    $all("[data-ctl]").forEach(function (c) { c.hidden = c.getAttribute("data-ctl") !== mode; });
    // lede
    $all("[data-lede]").forEach(function (l) { l.hidden = l.getAttribute("data-lede") !== mode; });
    // KPI card differences
    var avg2 = $("[data-kpi-avg2-card]"); if (avg2) avg2.hidden = mode !== "separate";
    var unc = $("[data-kpi-uncontrolled-card]"); if (unc) unc.hidden = mode !== "unified";
    setText("[data-kpi-avg-label]", mode === "separate" ? "متوسط التحميل الحالي" : "متوسط النسبة الحالية");
    var ctrlLabel = $(".spdx-kpi--green .spdx-kpi__label");
    if (ctrlLabel) ctrlLabel.textContent = mode === "separate" ? "الباقات المتأثرة" : "المتحكَّم بها";
    // re-render sliders that just became visible
    requestAnimationFrame(function () { sliders.forEach(function (s) { s.render(); }); recompute(); });
  }
  $all("[data-mode-btn]").forEach(function (b) {
    b.addEventListener("click", function () { setMode(b.getAttribute("data-mode-btn")); });
  });

  // ── action bar ─────────────────────────────────────────────────────────
  function restoreAll() {
    state.globalUni = state.globalDown = state.globalUp = 100;
    if (gUni) setSlider(gUni, 100);
    if (gDown) setSlider(gDown, 100);
    if (gUp) setSlider(gUp, 100);
    profiles.forEach(function (p) {
      p.uni = p.dn = p.upp = 100;
      var a = $("[data-row-uni]", p.el), b = $("[data-row-down]", p.el), c = $("[data-row-up]", p.el);
      if (a) setSlider(a, 100); if (b) setSlider(b, 100); if (c) setSlider(c, 100);
    });
    recompute();
  }

  bindClick("[data-action='restore-all']", function () { restoreAll(); toast("تمت استعادة جميع الباقات إلى السرعة الأصلية."); });
  bindClick("[data-action='refresh']", function (btn) {
    btn.classList.add("is-spinning");
    setTimeout(function () { btn.classList.remove("is-spinning"); }, 600);
    restoreAll();
  });
  bindClick("[data-action='preview']", function () {
    var enabled = profiles.filter(function (p) { return p.enabled; });
    if (state.mode === "unified") {
      var base = enabled.reduce(function (s, p) { return s + p.down; }, 0);
      var eff = enabled.reduce(function (s, p) { return s + p.down * (p.uni / 100); }, 0);
      toast("معاينة آمنة: " + enabled.length + " باقات ستُقيَّد من " + mb(base) + " إلى " + mb(eff) + " ميجابت — بدون أي تنفيذ على الراوترات.");
    } else {
      toast("معاينة آمنة: " + enabled.length + " باقات — متوسط التحميل " +
        Math.round(avg(enabled.map(function (p) { return p.dn; }))) + "% والرفع " +
        Math.round(avg(enabled.map(function (p) { return p.upp; }))) + "% — بدون تنفيذ مباشر.");
    }
  });
  bindClick("[data-action='save']", function () { save(); });

  function bindClick(sel, fn) {
    $all(sel).forEach(function (el) { el.addEventListener("click", function () { fn(el); }); });
  }

  // ── save: populate hidden form + submit (server persists a dry-run policy)
  function save() {
    var form = $("[data-save-form]");
    if (!form) return;
    var enabled = profiles.filter(function (p) { return p.enabled; });
    var payload = {
      mode: state.mode,
      global: { down: state.mode === "unified" ? state.globalUni : state.globalDown, up: state.mode === "unified" ? state.globalUni : state.globalUp },
      profiles: profiles.map(function (p) {
        return {
          id: p.id, enabled: p.enabled,
          down: state.mode === "unified" ? p.uni : p.dn,
          up: state.mode === "unified" ? p.uni : p.upp
        };
      })
    };
    // representative multiplier stored alongside the exact per-profile overrides:
    // unified → the master %, separate → average of all enabled down+up factors.
    var rep = state.mode === "unified"
      ? state.globalUni
      : (Math.round(avg(enabled.reduce(function (a, p) { return a.concat([p.dn, p.upp]); }, [])) ) || 100);
    form.elements["settings_json"].value = JSON.stringify(payload);
    form.elements["profile_ids"].value = enabled.map(function (p) { return p.id; }).join(",");
    form.elements["multiplier"].value = (clamp(rep, 0, 100) / 100).toFixed(3);
    form.elements["preset"].value = "normal";
    form.elements["policy_key"].value = "ui-" + state.mode + "-" + Math.round(performance.now());
    form.elements["title"].value = (state.mode === "unified" ? "تحكّم موحّد" : "تحكّم منفصل") + " — مراقبة الباندويث";
    form.submit();
  }

  // ── toast ────────────────────────────────────────────────────────────
  var toastTimer = null;
  function toast(msg, isError) {
    var el = $("[data-toast]");
    if (!el) return;
    el.textContent = msg;
    el.classList.toggle("spdx-toast--error", !!isError);
    el.hidden = false;
    if (toastTimer) clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { el.hidden = true; }, 5000);
  }

  // ── init ──────────────────────────────────────────────────────────────
  setMode("unified");
  recompute();
})();
