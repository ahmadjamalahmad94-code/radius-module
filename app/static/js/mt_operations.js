/* O2 — Operations Center live counters.
 *
 * For every <tr data-mt-row-counters data-mt-router-id="X">, poll
 * /api/v1/mikrotik/X/counters every 10s and fill the four cells
 * (status pill, hotspot count, ppp count, traffic). The poll
 * cadence matches TTL_ACTIVE_USERS on the server, so any extra
 * polls land on a warm cache instead of hammering routers.
 *
 * No fake data: each cell stays em-dash until the FIRST response
 * lands. A failed call paints the status pill red with the
 * envelope's `error` text in the title attribute (for tooltip).
 */
(function () {
  "use strict";

  const table = document.querySelector("[data-mt-ops-table]");
  if (!table) return;

  const CFG = {
    apiBase:  table.dataset.mtApiBase || "/api/v1",
    apiToken: table.dataset.mtApiToken || "",
    liveUrl:  table.dataset.mtLiveUrl || "",
    pollMs:   10_000,
  };

  // ── Lazy radacct seed (no API token needed) ──────────────────
  // الأداء: تُرسَم الصفحة فورًا بحالة «جارٍ فحص الاتصال…»، ثم نملأ «متصل»
  // من radacct (المصدر الموثوق) بعد اكتمال الرسم — بدل فحص متزامن أثناء
  // التحميل. مستقلّ عن رمز الـAPI، فيعمل حتى بلا token.
  function seedRadacct() {
    if (!CFG.liveUrl) return Promise.resolve();
    return fetch(CFG.liveUrl, {
      headers: { Accept: "application/json" }, credentials: "same-origin",
    })
      .then(function (r) { return r.json(); })
      .then(function (body) {
        if (!body || body.ok === false || !body.routers) return;
        const map = body.routers;
        document.querySelectorAll("tr[data-mt-router-id]").forEach(function (row) {
          const info = map[row.dataset.mtRouterId];
          if (!info) return;
          row.dataset.mtRadacctOnline = info.online ? "1" : "0";
          row.dataset.mtRadacctActive = String(info.active || 0);
          if (info.online) {
            const pill = row.querySelector("[data-mt-row-status]");
            const lbl  = row.querySelector("[data-mt-row-status-label]");
            if (pill) pill.setAttribute("data-mt-state", "ok");
            if (lbl)  lbl.textContent = "متصل";
          }
        });
        const card = document.querySelector('[data-mt-fleet="connected"]');
        if (card) {
          card.dataset.mtRadacctConnected = String(body.connected || 0);
          const v = card.querySelector("[data-mt-fleet-value]");
          if (v) v.textContent = String(body.connected || 0);
        }
      })
      .catch(function () { /* radacct seed best-effort — never breaks the page */ });
  }
  // شغّل البذرة بعد اكتمال أوّل رسم للصفحة.
  if (document.readyState === "complete") { seedRadacct(); }
  else { window.addEventListener("load", function () { seedRadacct(); }, { once: true }); }

  if (!CFG.apiToken) {
    // No token = nothing useful we can do. Show a one-time banner
    // and bail; users still see the static-rendered data.
    const note = document.createElement("div");
    note.className = "hub-pill hub-pill--amber";
    note.style.margin = "10px 0";
    note.textContent =
      "رمز واجهة الربط غير مهيّأ — لن تتحدّث الأعمدة الحيّة. " +
      "اضبط رمز واجهة الربط في إعدادات البيئة.";
    table.parentNode.insertBefore(note, table);
    return;
  }

  const allRows = Array.from(
    table.querySelectorAll("tr[data-mt-row-counters]")
  );

  // O3 — bulk selection wiring. Lives in this same JS file so we
  // don't ship a second <script>. Always wire even when allRows
  // is empty (the page still renders an empty bulk bar in that
  // case).
  //
  // إعادة التصميم: الشريط صار عائمًا لاصقًا أسفل الشاشة ويظهر فقط
  // عندما يُختار صف واحد على الأقل (صنف is-active) — لا مساحة ميتة
  // أعلى الجدول بعد اليوم.
  (function wireBulk() {
    const bulkForm = document.getElementById("mt-bulk-form");
    if (!bulkForm) return;
    const countEl   = bulkForm.querySelector("[data-mt-bulk-count]");
    const actionBtns = Array.from(
      bulkForm.querySelectorAll("[data-mt-bulk-action]")
    );
    const clearBtn  = bulkForm.querySelector("[data-mt-bulk-clear]");
    const rowSelects = Array.from(
      table.querySelectorAll("[data-mt-row-select]")
    );
    const selectAll  = table.querySelector("[data-mt-bulk-toggle-all]");

    function refreshState() {
      const selected = rowSelects.filter(cb => cb.checked).length;
      if (countEl) countEl.textContent = String(selected);
      const disabled = selected === 0;
      actionBtns.forEach(b => { b.disabled = disabled; });
      // إظهار/إخفاء الشريط العائم حسب الاختيار.
      bulkForm.classList.toggle("is-active", selected > 0);
      // Header checkbox tri-state for clarity.
      if (selectAll) {
        if (selected === 0) {
          selectAll.checked = false;
          selectAll.indeterminate = false;
        } else if (selected === rowSelects.length) {
          selectAll.checked = true;
          selectAll.indeterminate = false;
        } else {
          selectAll.checked = false;
          selectAll.indeterminate = true;
        }
      }
    }

    rowSelects.forEach(cb => cb.addEventListener("change", refreshState));
    if (selectAll) {
      selectAll.addEventListener("change", () => {
        rowSelects.forEach(cb => { cb.checked = selectAll.checked; });
        refreshState();
      });
    }
    if (clearBtn) {
      clearBtn.addEventListener("click", () => {
        rowSelects.forEach(cb => { cb.checked = false; });
        refreshState();
      });
    }
    refreshState();
  })();

  // Only ENABLED rows are polled. Disabled rows are shown with a
  // static "معطّل" badge by the server-render path; polling them
  // would be wasted API calls + might confuse operators if a
  // disabled-but-still-reachable router lit up green.
  const rows = allRows.filter(
    r => (r.dataset.mtEnabled || "true") === "true"
  );
  if (!rows.length) return;

  // ── humanise byte counters ───────────────────────────────────
  function bytesHuman(v) {
    const n = Number(v);
    if (!Number.isFinite(n) || n < 0) return "—";
    if (n < 1024) return n + " B";
    const units = ["KB", "MB", "GB", "TB"];
    let val = n / 1024, idx = 0;
    while (val >= 1024 && idx < units.length - 1) { val /= 1024; idx++; }
    return val.toFixed(val >= 10 ? 0 : 1) + " " + units[idx];
  }

  function setStatus(row, state, label) {
    // radacct (جلسات RADIUS نشطة) هو المصدر الموثوق: راوتر له جلسات حيّة لا
    // يُخفَّض إلى «غير متصل» مهما فشل استطلاع الـAPI (قد يكون RADIUS-only أو
    // بلا API token). الـAPI يُرقّيه/يُفصّله فقط، لا يُسقطه.
    if (row.dataset.mtRadacctOnline === "1" && state === "error") {
      state = "ok"; label = "متصل";
    }
    const pill  = row.querySelector("[data-mt-row-status]");
    const text  = row.querySelector("[data-mt-row-status-label]");
    if (pill) pill.setAttribute("data-mt-state", state);
    if (text) text.textContent = label;
  }

  // ── شارة نفق الإدارة (الدرع + الشارة النصّية) ─────────────────
  // تُحدَّث من نفس استطلاع /counters الذي يضبط عمود «الحالة»، فلا
  // يحدث أبدًا تناقض «متصل + النفق متوقف». «حيّ متصل» (أو جزئي =
  // وصلنا الراوتر فالنفق يحمل الحركة) ⇒ «نفق فعّال»؛ لا استجابة ⇒
  // «النفق متوقف».
  const _HINT_TONES = ["green", "red", "amber", "grey"];
  const _PILL_TONES = ["green", "red", "amber", "grey", "brand"];
  function setMgmt(row, live, reason) {
    const shield = row.querySelector("[data-mt-mgmt-state]");
    const pill   = row.querySelector("[data-mt-mgmt-label]");
    const isUp   = live === "connected";
    const tone   = isUp ? "green" : "red";
    const label  = isUp ? "نفق فعّال" : "النفق متوقف";
    if (shield) {
      _HINT_TONES.forEach(t => shield.classList.remove("mt-sys-hint--" + t));
      shield.classList.add("mt-sys-hint--" + tone);
      shield.setAttribute("data-mt-mgmt-state", isUp ? "active" : "down");
      if (reason) shield.setAttribute("data-hint", reason);
    }
    if (pill) {
      _PILL_TONES.forEach(t => pill.classList.remove("hub-pill--" + t));
      pill.classList.add("hub-pill--" + tone);
      pill.textContent = "";
      if (isUp) {
        const dot = document.createElement("span");
        dot.className = "dot";
        pill.appendChild(dot);
      }
      pill.appendChild(document.createTextNode(label));
    }
  }

  async function refreshRow(row) {
    const id = row.dataset.mtRouterId;
    let res, body;
    // مهلة قصوى للطلب نفسه: بدونها يبقى الصف عالقًا على «جارٍ الفحص…»
    // إلى الأبد إذا علّق الطلب (راوتر لا يرد + خادم ينتظر) — الآن
    // ينقلب الصف إلى «غير متصل» بعد 12 ثانية كحد أقصى.
    const ctl = (typeof AbortController !== "undefined")
      ? new AbortController() : null;
    const timer = ctl
      ? window.setTimeout(() => ctl.abort(), 12_000) : null;
    try {
      res = await fetch(`${CFG.apiBase}/mikrotik/${id}/counters`, {
        headers: { "Authorization": "Bearer " + CFG.apiToken },
        signal: ctl ? ctl.signal : undefined,
      });
      try { body = await res.json(); } catch (_) { body = null; }
    } catch (e) {
      const timedOut = e && e.name === "AbortError";
      setStatus(row, "error", timedOut ? "غير متصل" : "خطأ شبكة");
      const pill = row.querySelector("[data-mt-row-status]");
      if (pill) pill.title = timedOut ? "انتهت مهلة الفحص (12 ثانية)" : String(e);
      setMgmt(row, "down", timedOut
        ? "لا استجابة حيّة من الراوتر عبر نفق الإدارة (انتهت مهلة الفحص)."
        : "تعذّر الوصول إلى الراوتر عبر نفق الإدارة (خطأ شبكة).");
      return;
    } finally {
      if (timer) window.clearTimeout(timer);
    }

    if (!res.ok || !body || body.ok === false) {
      const msg = (body && body.error && body.error.message)
        ? body.error.message
        : ("HTTP " + res.status);
      setStatus(row, "error", "غير متصل");
      const pill = row.querySelector("[data-mt-row-status]");
      if (pill) pill.title = msg;
      setMgmt(row, "down",
        "لا استجابة حيّة من الراوتر عبر نفق الإدارة الآن (" + msg + ").");
      return;
    }

    // Envelope inside envelope: top {ok, data}, then data is the
    // MtResult-shape {ok, data:NasCounters, error, ...}.
    const env = body.data || {};
    const d = env.data || {};

    // Fill counter cells. Even if the envelope is partial (env.ok
    // === false but env.data is present) we still paint what we
    // have — the badge just goes amber instead of green.
    const setText = (sel, val) => {
      const el = row.querySelector(sel);
      if (el) el.textContent = (val == null ? "—" : String(val));
    };
    setText("[data-mt-row-hotspot]", d.hotspot_active);
    setText("[data-mt-row-ppp]",     d.ppp_active);
    setText("[data-mt-row-rx]",      bytesHuman(d.rx_bytes_total));
    setText("[data-mt-row-tx]",      bytesHuman(d.tx_bytes_total));

    if (env.ok) {
      setStatus(row, "ok", "متصل");
      setMgmt(row, "connected",
        "نفق الإدارة فعّال — اللوحة تتواصل مع الراوتر الآن (حركة حيّة عبر النفق).");
    } else {
      setStatus(row, "partial", "جزئي");
      const pill = row.querySelector("[data-mt-row-status]");
      if (pill) pill.title = env.error || "";
      // استجابة جزئية = وصلنا الراوتر فعلًا عبر النفق ⇒ النفق فعّال
      // (نقص في بعض القيم فقط)، فلا نعرض «متوقف» ونتناقض مع العمود.
      setMgmt(row, "connected",
        "نفق الإدارة فعّال — وصلت اللوحة إلى الراوتر عبر النفق (استجابة جزئية للعدّادات).");
    }
  }

  // O4 — fleet summary aggregator. After each batch of row
  // refreshes, walk every row's status pill and recount the
  // متصل / غير متصل / جزئي cards. The "معطَّل" card is
  // server-rendered once + never changes per poll.
  function updateFleetSummary() {
    const counts = { connected: 0, unreachable: 0, partial: 0 };
    for (const r of allRows) {
      if ((r.dataset.mtEnabled || "true") !== "true") continue;
      const pill = r.querySelector("[data-mt-row-status]");
      const state = pill ? pill.getAttribute("data-mt-state") : null;
      // radacct موثوق: راوتر له جلسات RADIUS نشطة يُحتسب «متصلاً» دائمًا، ولا
      // يُحتسب «غير متصل» حتى لو فشل استطلاع الـAPI.
      if (state === "ok" || r.dataset.mtRadacctOnline === "1") counts.connected++;
      else if (state === "error") counts.unreachable++;
      else if (state === "partial") counts.partial++;
    }
    for (const key of Object.keys(counts)) {
      const card = document.querySelector(`[data-mt-fleet="${key}"]`);
      if (!card) continue;
      const val = card.querySelector("[data-mt-fleet-value]");
      if (val) val.textContent = String(counts[key]);
    }
  }

  let inFlight = false;
  async function refreshAll() {
    // حارس تداخل: لا نطلق دورة جديدة بينما السابقة ما تزال تنتظر
    // راوترات بطيئة — يمنع تراكم الطلبات على الخادم.
    if (inFlight) return;
    inFlight = true;
    try {
      // Fan-out — each row is independent, so we don't await in
      // series. A slow router doesn't block the others.
      await Promise.all(rows.map(refreshRow));
      updateFleetSummary();
    } finally {
      inFlight = false;
    }
  }

  // أكمل التحميل أوّلًا ثم افحص الاتصال (طلب المالك) — لا نُطلق فحص
  // العدّادات أثناء الرسم بل بعد اكتمال تحميل الصفحة.
  if (document.readyState === "complete") { refreshAll(); }
  else { window.addEventListener("load", function () { refreshAll(); }, { once: true }); }
  setInterval(refreshAll, CFG.pollMs);
})();
