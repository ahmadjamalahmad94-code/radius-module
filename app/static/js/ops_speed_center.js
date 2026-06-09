/* ============================================================================
   مركز التحكم بالسرعة — اللوحات الشرطية وتجهيز حمولة الإرسال
   ----------------------------------------------------------------------------
   • الاختيار الأول (وضع جاهز / نسبة مخصصة / تخصيص لكل باقة) يُظهر لوحته فقط.
   • أسماء حقول النموذج تبقى مطابقة لعقد الخادم تمامًا:
       preset / multiplier / profile_ids / settings_json / save_policy /
       policy_key / title
   • وضع جاهز أو نسبة مخصصة → الحقول التقليدية (settings_json يبقى فارغًا).
   • تخصيص لكل باقة → settings_json بنِسَب تنزيل/رفع لكل باقة (0–300%).
   • كل الحسابات هنا للعرض فقط؛ الخادم يحفظ معاينة بدون أي تنفيذ على الشبكة.
   ========================================================================== */
(function () {
  "use strict";

  var form = document.querySelector("[data-spc]");
  if (!form) return;

  function $(sel, el) { return (el || form).querySelector(sel); }
  function $all(sel, el) { return Array.prototype.slice.call((el || form).querySelectorAll(sel)); }

  var choices = $all("[data-spc-choice]");
  var panels = $all("[data-spc-panel]");
  var multiplierInput = $("[data-spc-multiplier]");
  var customPctEcho = $("[data-spc-custom-pct]");
  var profileIdsField = $("[data-spc-profile-ids]");
  var settingsField = $("[data-spc-settings]");

  // الوضع الحالي: preset | custom | per_profile
  function currentChoice() {
    var checked = choices.filter(function (c) { return c.checked; })[0];
    return checked ? checked.getAttribute("data-spc-choice") : "preset";
  }

  // ── إظهار لوحة الوضع المختار فقط (وإخفاء الباقي) ──────────────────────
  function applyChoice() {
    var mode = currentChoice();
    panels.forEach(function (panel) {
      var key = panel.getAttribute("data-spc-panel");
      var show =
        key === mode ||
        // نطاق الباقات يخص الوضع الجاهز والنسبة المخصصة معًا
        (key === "scope" && (mode === "preset" || mode === "custom"));
      panel.hidden = !show;
    });
    // حقل المعامل يُرسَل فقط في وضع النسبة المخصصة
    // (في الوضع الجاهز يعتمد الخادم على معامل الوضع نفسه)
    if (multiplierInput) multiplierInput.disabled = mode !== "custom";
    if (mode === "preset") refreshPresetSummary();
  }

  // ── ملخص الوضع الجاهز: التسمية + الوصف + النسبة ───────────────────────
  function refreshPresetSummary() {
    var checked = choices.filter(function (c) {
      return c.checked && c.getAttribute("data-spc-choice") === "preset";
    })[0];
    if (!checked) return;
    var labelEl = $("[data-spc-summary-label]");
    var descEl = $("[data-spc-summary-desc]");
    var pctEl = $("[data-spc-summary-pct]");
    if (labelEl) labelEl.textContent = checked.getAttribute("data-label") || "";
    if (descEl) descEl.textContent = checked.getAttribute("data-desc") || "";
    if (pctEl) pctEl.textContent = Math.round(parseFloat(checked.getAttribute("data-mult") || "1") * 100);
  }

  // ── صدى النسبة المخصصة (1.25 → 125%) ──────────────────────────────────
  function refreshCustomEcho() {
    if (!multiplierInput || !customPctEcho) return;
    var v = parseFloat(multiplierInput.value);
    customPctEcho.textContent = isFinite(v) ? Math.round(v * 100) : "—";
  }

  choices.forEach(function (c) { c.addEventListener("change", applyChoice); });
  if (multiplierInput) multiplierInput.addEventListener("input", refreshCustomEcho);

  // ── تجهيز الحمولة قبل الإرسال (معاينة أو حفظ — نفس النموذج) ───────────
  form.addEventListener("submit", function () {
    var mode = currentChoice();

    if (mode === "per_profile") {
      // settings_json: نِسَب تنزيل/رفع لكل باقة مفعّلة — الخادم يحوّلها
      // إلى معاملات آمنة ويتجاهل الحقول التقليدية.
      var rows = $all("[data-spc-row]");
      var profiles = rows.map(function (row) {
        var enable = $("[data-spc-row-enable]", row);
        return {
          id: row.getAttribute("data-id"),
          enabled: !!(enable && enable.checked),
          down: clampPct($("[data-spc-row-down]", row)),
          up: clampPct($("[data-spc-row-up]", row))
        };
      });
      var enabledIds = profiles
        .filter(function (p) { return p.enabled; })
        .map(function (p) { return p.id; });
      if (settingsField) {
        settingsField.value = JSON.stringify({ mode: "per_profile", profiles: profiles });
      }
      if (profileIdsField) profileIdsField.value = enabledIds.join(",");
      return;
    }

    // وضع جاهز أو نسبة مخصصة: مسار الحقول التقليدية — settings_json فارغ
    if (settingsField) settingsField.value = "";
    if (profileIdsField) {
      var ids = $all("[data-spc-scope]")
        .filter(function (cb) { return cb.checked; })
        .map(function (cb) { return cb.value; });
      // بلا اختيار = كل الباقات (سلوك الخادم الافتراضي عند القيمة الفارغة)
      profileIdsField.value = ids.join(",");
    }
  });

  function clampPct(input) {
    if (!input) return 100;
    var v = parseFloat(input.value);
    if (!isFinite(v)) return 100;
    return Math.max(0, Math.min(300, v));
  }

  // ── تهيئة أولية ────────────────────────────────────────────────────────
  applyChoice();
  refreshCustomEcho();
})();
