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
  const stepNames = steps.map((step) => step.dataset.swv2Step);
  let current = 0;
  let selectedSource = "dhcp";

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
  }

  function setSource(type) {
    selectedSource = type || "dhcp";
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

  function analyzeOutput(kind) {
    const output = page.querySelector(`[data-swv2-verify-output="${kind}"]`);
    const diagnostics = page.querySelector(`[data-swv2-diagnostics="${kind}"]`);
    const success = page.querySelector(`[data-swv2-success="${kind}"]`);
    if (!output || !diagnostics) return;

    const value = output.value.toLowerCase();
    const hasPingSuccess =
      value.includes("received=5") ||
      value.includes("packet-loss=0") ||
      value.includes("0% packet loss");
    const hasVpnSignal = kind !== "vpn" || value.includes("handshake") || value.includes("radius");
    const ok = hasPingSuccess && hasVpnSignal;

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
    } else if (target.matches("[data-swv2-verify]")) {
      analyzeOutput(target.dataset.swv2Verify);
    } else if (target.matches("[data-swv2-step-target]")) {
      const idx = stepNames.indexOf(target.dataset.swv2StepTarget);
      if (idx >= 0 && idx <= current) showStep(idx);
    }
  });

  setSource(selectedSource);
  showStep(0);
})();
