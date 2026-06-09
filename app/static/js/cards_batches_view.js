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
  var bulkBar = root.querySelector("[data-bulk-bar]");
  var bulkCount = root.querySelector("[data-bulk-count]");
  var bulkInputs = root.querySelector("[data-bulk-selected-inputs]");
  var bulkSubmit = root.querySelector("[data-bulk-submit]");
  var bulkControls = bulkBar ? Array.prototype.slice.call(bulkBar.querySelectorAll("select, input:not([type='hidden'])")) : [];

  function batchChecks() {
    return Array.prototype.slice.call(root.querySelectorAll(".batch-check"));
  }

  function selectedBatchIds() {
    var seen = {};
    return batchChecks().filter(function (box) { return box.checked; }).map(function (box) {
      return box.value;
    }).filter(function (value) {
      if (!value || seen[value]) return false;
      seen[value] = true;
      return true;
    });
  }

  function syncMatchingChecks(source) {
    if (!source || !source.value) return;
    batchChecks().forEach(function (box) {
      if (box !== source && box.value === source.value) box.checked = source.checked;
    });
  }

  function updateBulkBar() {
    var ids = selectedBatchIds();
    if (bulkBar) bulkBar.classList.toggle("is-active", ids.length > 0);
    if (bulkCount) bulkCount.textContent = String(ids.length);
    if (bulkSubmit) bulkSubmit.disabled = ids.length === 0;
    bulkControls.forEach(function (control) {
      control.disabled = ids.length === 0;
    });
    if (bulkInputs) {
      bulkInputs.innerHTML = "";
      ids.forEach(function (id) {
        var input = document.createElement("input");
        input.type = "hidden";
        input.name = "batch_ids";
        input.value = id;
        bulkInputs.appendChild(input);
      });
    }
    if (all) {
      var tableChecks = batchChecks().filter(function (box) { return box.name === "batch_ids"; });
      var selectedTableChecks = tableChecks.filter(function (box) { return box.checked; });
      all.checked = tableChecks.length > 0 && selectedTableChecks.length === tableChecks.length;
      all.indeterminate = selectedTableChecks.length > 0 && selectedTableChecks.length < tableChecks.length;
    }
  }

  batchChecks().forEach(function (box) {
    box.addEventListener("change", function () {
      syncMatchingChecks(box);
      updateBulkBar();
    });
  });

  if (all) {
    all.addEventListener("change", function () {
      batchChecks().forEach(function (box) {
        box.checked = all.checked;
      });
      updateBulkBar();
    });
  }

  updateBulkBar();

  document.querySelectorAll("[data-batch-archive-form]").forEach(function (form) {
    form.addEventListener("submit", function (event) {
      if (!window.confirm("سيتم نقل الحزمة إلى الأرشيف بدون حذف البطاقات. هل تريد المتابعة؟")) {
        event.preventDefault();
      }
    });
  });

  var quickModal = root.parentElement ? root.parentElement.querySelector("[data-quick-batch-modal]") : document.querySelector("[data-quick-batch-modal]");
  var quickOpeners = Array.prototype.slice.call(document.querySelectorAll("[data-quick-batch-open]"));
  if (quickModal) {
    var accountingMode = quickModal.querySelector("[data-quick-accounting-mode]");
    var countBySeconds = quickModal.querySelector("[data-quick-count-by-seconds]");
    var countFromFirst = quickModal.querySelector("[data-quick-count-from-first]");
    var validityField = quickModal.querySelector("[data-quick-validity-field]");
    var validityValue = quickModal.querySelector("[data-quick-validity-value]");
    var validityUnit = quickModal.querySelector("[data-quick-validity-unit]");
    var validityDays = quickModal.querySelector("[data-quick-validity-days]");
    var quotaValue = quickModal.querySelector("[data-quick-quota-value]");
    var quotaUnit = quickModal.querySelector("[data-quick-quota-unit]");
    var quotaMb = quickModal.querySelector("[data-quick-quota-mb]");

    function validityToDays() {
      var value = Number(validityValue ? validityValue.value : 0) || 0;
      var unit = validityUnit ? validityUnit.value : "days";
      var minutes = value;
      if (unit === "hours") minutes = value * 60;
      if (unit === "days") minutes = value * 1440;
      return value > 0 ? String(Math.max(1, Math.ceil(minutes / 1440))) : "";
    }

    function syncQuickUnits() {
      var quota = Number(quotaValue ? quotaValue.value : 0) || 0;
      var unit = quotaUnit ? quotaUnit.value : "mb";
      if (quotaMb) quotaMb.value = String(unit === "gb" ? Math.round(quota * 1024) : Math.round(quota));
      if (validityDays) validityDays.value = validityToDays();
    }

    function syncQuickAccountingMode() {
      var isSeconds = accountingMode && accountingMode.value === "seconds";
      if (countBySeconds) countBySeconds.value = isSeconds ? "1" : "";
      if (countFromFirst) countFromFirst.value = "1";
      if (validityField) validityField.hidden = !isSeconds;
      [validityValue, validityUnit].forEach(function (control) {
        if (!control) return;
        control.disabled = !isSeconds;
      });
      if (validityValue) {
        validityValue.required = isSeconds;
        if (!isSeconds) validityValue.value = "";
      }
      syncQuickUnits();
    }

    function closeQuickModal() {
      quickModal.hidden = true;
    }
    function openQuickModal() {
      syncQuickAccountingMode();
      quickModal.hidden = false;
      var first = quickModal.querySelector("input, select, button, a");
      if (first && first.focus) first.focus();
    }
    if (accountingMode) {
      accountingMode.addEventListener("change", syncQuickAccountingMode);
      syncQuickAccountingMode();
    }
    [validityValue, validityUnit, quotaValue, quotaUnit].forEach(function (control) {
      if (control) control.addEventListener("input", syncQuickUnits);
      if (control) control.addEventListener("change", syncQuickUnits);
    });
    quickOpeners.forEach(function (button) {
      button.addEventListener("click", openQuickModal);
    });
    quickModal.querySelectorAll("[data-quick-batch-close]").forEach(function (button) {
      button.addEventListener("click", closeQuickModal);
    });
    document.addEventListener("keydown", function (event) {
      if (event.key === "Escape" && !quickModal.hidden) closeQuickModal();
    });
  }

  function normalizeCellValue(value, type) {
    var clean = String(value || "").replace(/\s+/g, " ").trim();
    if (type === "number") {
      var number = Number(clean.replace(/[^\d.-]/g, ""));
      return Number.isFinite(number) ? number : 0;
    }
    if (type === "date") {
      if (!clean || clean === "—") return 0;
      var parsed = Date.parse(clean);
      return Number.isNaN(parsed) ? 0 : parsed;
    }
    return clean.toLocaleLowerCase("ar");
  }

  function setupSmartTable(table) {
    if (!table || table.dataset.smartReady === "1") return;
    table.dataset.smartReady = "1";

    var tableKey = table.getAttribute("data-table-key") || "default";
    var visibilityKey = "hoberadius.tableColumns." + tableKey;
    var visibilityMigrationKey = visibilityKey + ".schema.v2";
    var sortKey = "hoberadius.tableSort." + tableKey;
    var headers = Array.prototype.slice.call(table.querySelectorAll("thead th[data-column-key]"));
    var menu = table.closest("[data-table-view]")?.querySelector("[data-column-menu]");
    var panel = menu ? menu.querySelector("[data-column-menu-panel]") : null;
    var trigger = menu ? menu.querySelector("[data-column-menu-toggle]") : null;
    var savedVisibility = {};
    var savedSort = null;

    try { savedVisibility = JSON.parse(window.localStorage.getItem(visibilityKey) || "{}") || {}; } catch (err) { savedVisibility = {}; }
    try { savedSort = JSON.parse(window.localStorage.getItem(sortKey) || "null"); } catch (err) { savedSort = null; }
    try {
      if (window.localStorage.getItem(visibilityMigrationKey) !== "1") {
        savedVisibility.usage = false;
        window.localStorage.setItem(visibilityKey, JSON.stringify(savedVisibility));
        window.localStorage.setItem(visibilityMigrationKey, "1");
      }
    } catch (err) {}

    function columnCells(key) {
      return Array.prototype.slice.call(table.querySelectorAll('[data-column-key="' + key + '"]'));
    }

    function isColumnVisible(header) {
      var key = header.getAttribute("data-column-key");
      if (Object.prototype.hasOwnProperty.call(savedVisibility, key)) return savedVisibility[key] !== false;
      return !header.hasAttribute("data-column-optional");
    }

    function setColumnVisible(key, visible) {
      columnCells(key).forEach(function (cell) {
        cell.hidden = !visible;
        cell.classList.toggle("is-column-hidden", !visible);
      });
      savedVisibility[key] = visible;
      try { window.localStorage.setItem(visibilityKey, JSON.stringify(savedVisibility)); } catch (err) {}
    }

    function refreshSortIndicators(activeKey, direction) {
      headers.forEach(function (header) {
        var key = header.getAttribute("data-column-key");
        var active = key === activeKey;
        header.classList.toggle("is-sorted", active);
        header.setAttribute("aria-sort", active ? (direction === "asc" ? "ascending" : "descending") : "none");
        var indicator = header.querySelector(".smart-sort-indicator");
        if (indicator) indicator.textContent = active ? (direction === "asc" ? "↑" : "↓") : "↕";
      });
    }

    function sortByColumn(header, requestedDirection) {
      if (!header || header.hasAttribute("data-sort-disabled") || header.hidden) return;
      var key = header.getAttribute("data-column-key");
      var type = header.getAttribute("data-sort-type") || "text";
      var tbody = table.tBodies[0];
      if (!tbody) return;
      var currentDirection = table.getAttribute("data-sort-key") === key ? table.getAttribute("data-sort-direction") : "";
      var direction = requestedDirection || (currentDirection === "asc" ? "desc" : "asc");
      var rows = Array.prototype.slice.call(tbody.rows);
      rows.sort(function (a, b) {
        var aCell = a.querySelector('[data-column-key="' + key + '"]');
        var bCell = b.querySelector('[data-column-key="' + key + '"]');
        var aValue = normalizeCellValue(aCell?.getAttribute("data-sort-value") || aCell?.textContent, type);
        var bValue = normalizeCellValue(bCell?.getAttribute("data-sort-value") || bCell?.textContent, type);
        if (aValue < bValue) return direction === "asc" ? -1 : 1;
        if (aValue > bValue) return direction === "asc" ? 1 : -1;
        return 0;
      });
      rows.forEach(function (row) { tbody.appendChild(row); });
      table.setAttribute("data-sort-key", key);
      table.setAttribute("data-sort-direction", direction);
      refreshSortIndicators(key, direction);
      try { window.localStorage.setItem(sortKey, JSON.stringify({ key: key, direction: direction })); } catch (err) {}
    }

    headers.forEach(function (header) {
      var key = header.getAttribute("data-column-key");
      var label = header.getAttribute("data-column-label") || header.textContent.trim() || key;
      var sortable = !header.hasAttribute("data-sort-disabled");
      var visible = isColumnVisible(header);

      setColumnVisible(key, visible);

      if (sortable) {
        header.classList.add("is-sortable");
        header.setAttribute("tabindex", "0");
        header.setAttribute("aria-sort", "none");
        if (!header.querySelector(".smart-sort-indicator")) {
          var indicator = document.createElement("span");
          indicator.className = "smart-sort-indicator";
          indicator.textContent = "↕";
          header.appendChild(indicator);
        }
        header.addEventListener("click", function (event) {
          if (event.target.closest("input,button,a,label")) return;
          sortByColumn(header);
        });
        header.addEventListener("keydown", function (event) {
          if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            sortByColumn(header);
          }
        });
      }

      if (panel && key !== "select" && key !== "actions") {
        var item = document.createElement("label");
        item.className = "bops-column-option";
        var checkbox = document.createElement("input");
        checkbox.type = "checkbox";
        checkbox.checked = visible;
        checkbox.addEventListener("change", function () {
          setColumnVisible(key, checkbox.checked);
        });
        var text = document.createElement("span");
        text.textContent = label;
        item.appendChild(checkbox);
        item.appendChild(text);
        panel.appendChild(item);
      }
    });

    if (trigger && panel) {
      trigger.addEventListener("click", function () {
        var open = panel.hidden;
        panel.hidden = !open;
        trigger.setAttribute("aria-expanded", open ? "true" : "false");
      });
      document.addEventListener("click", function (event) {
        if (!menu.contains(event.target)) {
          panel.hidden = true;
          trigger.setAttribute("aria-expanded", "false");
        }
      });
    }

    if (savedSort && savedSort.key) {
      var savedHeader = table.querySelector('thead th[data-column-key="' + savedSort.key + '"]');
      if (savedHeader) sortByColumn(savedHeader, savedSort.direction === "desc" ? "desc" : "asc");
    } else {
      refreshSortIndicators("", "");
    }
  }

  Array.prototype.slice.call(root.querySelectorAll("[data-smart-table]")).forEach(setupSmartTable);

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
