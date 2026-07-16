/* routers_live.js — شريط حيّ لأجهزة TR-069 التي «لا تستجيب» (مفصولة عن ACS
 * أو بلا إنترنت خلفها)، بلا تحديث للصفحة. يَستطلع نقطة health.json دوريًّا
 * ويعرض شريطًا سفليًّا مع زرّ «إعادة اختبار» لكل جهاز (يطلب اتصالًا فوريًّا).
 * محصّن: أي فشل شبكة يُتجاهَل بصمت (لا يكسر الصفحة). */
(function () {
  "use strict";
  var host = document.getElementById("rtr-live");
  if (!host) return;
  var pollUrl = host.getAttribute("data-poll");
  var actionTpl = host.getAttribute("data-action"); // .../routers/__ID__/action
  var csrf = host.getAttribute("data-csrf") || "";
  var intervalMs = parseInt(host.getAttribute("data-interval") || "20000", 10);

  var LABEL = { offline: "مفصول عن ACS", no_internet: "بلا إنترنت" };

  function el(tag, cls, txt) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (txt != null) e.textContent = txt;
    return e;
  }

  function retest(id, btn) {
    if (!actionTpl) return;
    btn.disabled = true;
    btn.textContent = "…";
    var url = actionTpl.replace("__ID__", String(id));
    var body = new URLSearchParams();
    body.set("action_type", "connection_request");
    if (csrf) body.set("_csrf_token", csrf);
    fetch(url, {
      method: "POST", credentials: "same-origin",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: body.toString()
    }).then(function () {
      btn.textContent = "أُرسل الطلب";
    }).catch(function () {
      btn.disabled = false;
      btn.textContent = "إعادة اختبار";
    });
  }

  function render(issues) {
    host.innerHTML = "";
    if (!issues || !issues.length) { host.style.display = "none"; return; }
    host.style.display = "block";
    var head = el("div", "rtr-live-head");
    head.appendChild(el("span", "rtr-live-x", "⚠"));
    head.appendChild(el("strong", null,
      "تعذّر الوصول إلى " + issues.length + " من أجهزة المشتركين:"));
    host.appendChild(head);
    var list = el("div", "rtr-live-list");
    issues.forEach(function (it) {
      var row = el("div", "rtr-live-row");
      var lbl = LABEL[it.issue] || it.issue;
      var txt = it.name + " — " + lbl + (it.ip ? " (" + it.ip + ")" : "");
      row.appendChild(el("span", "rtr-live-name", txt));
      var btn = el("button", "rtr-live-btn", "إعادة اختبار");
      btn.type = "button";
      btn.addEventListener("click", function () { retest(it.id, btn); });
      row.appendChild(btn);
      list.appendChild(row);
    });
    host.appendChild(list);
  }

  function poll() {
    fetch(pollUrl, { credentials: "same-origin" })
      .then(function (r) { return r.json(); })
      .then(function (d) { if (d && d.ok) render(d.issues); })
      .catch(function () { /* تجاهل صامت */ });
  }

  poll();
  setInterval(poll, isFinite(intervalMs) && intervalMs >= 5000 ? intervalMs : 20000);
})();
