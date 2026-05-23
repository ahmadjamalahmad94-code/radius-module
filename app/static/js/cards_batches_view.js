(function () {
  "use strict";

  var root = document.querySelector("[data-cards-batches-page]");
  if (!root) return;

  var storageKey = "hoberadius.cardsViewMode";
  var toggles = Array.prototype.slice.call(root.querySelectorAll("[data-view-toggle]"));

  function setMode(mode) {
    var next = mode === "table" ? "table" : "cards";
    root.setAttribute("data-view-mode", next);
    toggles.forEach(function (btn) {
      var active = btn.getAttribute("data-view-toggle") === next;
      btn.classList.toggle("is-active", active);
      btn.setAttribute("aria-pressed", active ? "true" : "false");
    });
    try { window.localStorage.setItem(storageKey, next); } catch (err) {}
  }

  toggles.forEach(function (btn) {
    btn.addEventListener("click", function () {
      setMode(btn.getAttribute("data-view-toggle"));
    });
  });

  var initial = "cards";
  try { initial = window.localStorage.getItem(storageKey) || initial; } catch (err) {}
  setMode(initial);

  var all = document.getElementById("select-all-batches");
  if (all) {
    all.addEventListener("change", function () {
      document.querySelectorAll(".batch-check").forEach(function (box) {
        box.checked = all.checked;
      });
    });
  }

  document.querySelectorAll("[data-batch-archive-form]").forEach(function (form) {
    form.addEventListener("submit", function (event) {
      if (!window.confirm("سيتم نقل الحزمة إلى الأرشيف بدون حذف البطاقات. هل تريد المتابعة؟")) {
        event.preventDefault();
      }
    });
  });

  var modal = document.querySelector("[data-batch-print-modal]");
  if (!modal) return;

  var batchIdInput = modal.querySelector("[data-print-batch-id]");
  var batchSummary = modal.querySelector("[data-print-batch-summary]");
  var templateButtons = Array.prototype.slice.call(modal.querySelectorAll("[data-template-id]"));
  var form = modal.querySelector("[data-batch-print-form]");
  var progressBox = modal.querySelector("[data-print-progress]");
  var progressTitle = modal.querySelector("[data-print-progress-title]");
  var progressPercent = modal.querySelector("[data-print-progress-percent]");
  var progressBar = modal.querySelector("[data-print-progress-bar]");
  var downloadLink = modal.querySelector("[data-print-download]");
  var errorBox = modal.querySelector("[data-print-error]");
  var selectedTemplate = null;
  var pollTimer = null;

  function closeModal() {
    modal.hidden = true;
    if (pollTimer) {
      window.clearTimeout(pollTimer);
      pollTimer = null;
    }
  }

  function openModal(batch) {
    if (batchIdInput) batchIdInput.value = batch.id || "";
    if (batchSummary) {
      batchSummary.textContent = (batch.name || "حزمة بطاقات") + " · " + (batch.code || "بدون كود") + " · " + (batch.total || "0") + " بطاقة";
    }
    resetProgress();
    modal.hidden = false;
    var firstSelected = templateButtons.find(function (btn) { return btn.dataset.defaultTemplate === "1"; }) || templateButtons[0];
    if (firstSelected && !selectedTemplate) selectTemplate(firstSelected);
  }

  function selectTemplate(button) {
    selectedTemplate = button;
    templateButtons.forEach(function (btn) {
      btn.classList.toggle("is-selected", btn === button);
      btn.setAttribute("aria-pressed", btn === button ? "true" : "false");
    });
  }

  function resetProgress() {
    if (!progressBox) return;
    progressBox.hidden = true;
    if (progressBar) progressBar.style.width = "0%";
    if (progressPercent) progressPercent.textContent = "0%";
    if (progressTitle) progressTitle.textContent = "بدء تجهيز ملف PDF...";
    if (downloadLink) {
      downloadLink.hidden = true;
      downloadLink.removeAttribute("href");
    }
    if (errorBox) {
      errorBox.hidden = true;
      errorBox.textContent = "";
    }
    modal.querySelectorAll("[data-print-stage]").forEach(function (item) {
      item.classList.remove("is-active", "is-done");
      var icon = item.querySelector("i");
      if (icon) icon.className = "fa-regular fa-circle";
    });
  }

  function setProgress(percent, title, stage) {
    if (!progressBox) return;
    progressBox.hidden = false;
    var clean = Math.max(0, Math.min(100, Number(percent) || 0));
    if (progressBar) progressBar.style.width = clean + "%";
    if (progressPercent) progressPercent.textContent = Math.round(clean) + "%";
    if (progressTitle && title) progressTitle.textContent = title;
    if (stage) {
      var stages = ["queued", "rendering", "pdf", "done"];
      var index = stages.indexOf(stage);
      modal.querySelectorAll("[data-print-stage]").forEach(function (item) {
        var itemStage = item.getAttribute("data-print-stage");
        var itemIndex = stages.indexOf(itemStage);
        var done = itemIndex > -1 && itemIndex < index;
        var active = itemStage === stage;
        item.classList.toggle("is-done", done);
        item.classList.toggle("is-active", active);
        var icon = item.querySelector("i");
        if (icon) {
          icon.className = done ? "fa-solid fa-circle-check" : (active ? "fa-solid fa-spinner fa-spin" : "fa-regular fa-circle");
        }
      });
    }
  }

  function showError(message) {
    setProgress(100, "فشل تجهيز الملف", "done");
    if (errorBox) {
      errorBox.textContent = message || "تعذر تجهيز ملف PDF.";
      errorBox.hidden = false;
    }
  }

  function pollJob(statusUrl, downloadUrl) {
    window.fetch(statusUrl, { headers: { "Accept": "application/json" } })
      .then(function (response) {
        if (!response.ok) throw new Error("HTTP " + response.status);
        return response.json();
      })
      .then(function (payload) {
        var job = payload.job || payload;
        var status = job.status || "";
        var progress = job.progress || 0;
        var label = job.stage_label || "تجهيز ملف PDF...";
        var stage = "rendering";
        if (status === "queued") stage = "queued";
        if (progress >= 70) stage = "pdf";
        if (status === "success" || job.download_ready) stage = "done";
        setProgress(progress, label, stage);

        if (status === "success" || job.download_ready) {
          setProgress(100, "تم تجهيز ملف PDF. بدأ التحميل.", "done");
          if (downloadLink) {
            downloadLink.href = downloadUrl;
            downloadLink.hidden = false;
          }
          var a = document.createElement("a");
          a.href = downloadUrl;
          a.style.display = "none";
          document.body.appendChild(a);
          a.click();
          a.remove();
          return;
        }
        if (status === "failed") {
          showError(job.error_message || job.message || "فشل تجهيز ملف PDF.");
          return;
        }
        pollTimer = window.setTimeout(function () { pollJob(statusUrl, downloadUrl); }, 900);
      })
      .catch(function (err) {
        showError("تعذر متابعة حالة التصدير: " + err.message);
      });
  }

  templateButtons.forEach(function (btn) {
    btn.addEventListener("click", function () { selectTemplate(btn); });
  });

  modal.querySelectorAll("[data-print-modal-close]").forEach(function (btn) {
    btn.addEventListener("click", closeModal);
  });

  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape" && !modal.hidden) closeModal();
  });

  document.querySelectorAll("[data-print-batch]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      openModal({
        id: btn.getAttribute("data-batch-id"),
        code: btn.getAttribute("data-batch-code"),
        name: btn.getAttribute("data-batch-name"),
        total: btn.getAttribute("data-batch-total")
      });
    });
  });

  if (form) {
    form.addEventListener("submit", function (event) {
      event.preventDefault();
      resetProgress();
      if (!selectedTemplate) {
        showError("اختر قالبًا محفوظًا قبل التصدير.");
        return;
      }
      var jobUrl = selectedTemplate.getAttribute("data-job-url");
      if (!jobUrl) {
        showError("لا يوجد مسار تصدير لهذا القالب.");
        return;
      }
      var body = new window.FormData(form);
      setProgress(8, "إرسال مهمة التصدير إلى الخادم...", "queued");
      window.fetch(jobUrl, {
        method: "POST",
        body: body,
        headers: { "Accept": "application/json" }
      })
        .then(function (response) {
          if (!response.ok) throw new Error("HTTP " + response.status);
          return response.json();
        })
        .then(function (payload) {
          if (!payload.ok) throw new Error(payload.message || "تعذر إنشاء المهمة.");
          setProgress(18, "تم إنشاء المهمة. يجري رسم البطاقات...", "rendering");
          pollJob(payload.status_url, payload.download_url);
        })
        .catch(function (err) {
          showError(err.message || "تعذر بدء التصدير.");
        });
    });
  }
})();
