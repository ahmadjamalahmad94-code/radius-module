/* ════════════════════════════════════════════════════════════════════
   HUB HINT — بطاقة شرح عائمة موحّدة (portal) لكل أيقونات .hub-hint.
   تُبنى على document.body بموضع ثابت على الشاشة، فلا يقصّها أي
   overflow:hidden أو حافة صفحة (المشكلة المتكررة سابقًا مع pseudo).
   تفتح فوق الأيقونة إن وُجدت مساحة وإلا تحتها، وتنحصر أفقيًا داخل
   الشاشة دائمًا. تعمل بالمرور وبالتركيز (كيبورد) وعلى اللمس بالنقر.
   ════════════════════════════════════════════════════════════════════ */
(function () {
  "use strict";
  if (window.__hubHintInit) return;
  window.__hubHintInit = true;

  var pop = null;     // البطاقة الحالية
  var owner = null;   // الأيقونة صاحبة البطاقة

  function hide() {
    if (pop && pop.parentNode) pop.parentNode.removeChild(pop);
    pop = null;
    owner = null;
  }

  function show(icon) {
    if (owner === icon) return;
    hide();
    var text = icon.getAttribute("data-hint") || icon.getAttribute("title") || "";
    if (!text.trim()) return;
    owner = icon;
    pop = document.createElement("div");
    pop.className = "hub-hint-pop";
    pop.setAttribute("role", "tooltip");
    pop.textContent = text;
    document.body.appendChild(pop);

    var r = icon.getBoundingClientRect();
    var pw = pop.offsetWidth;
    var ph = pop.offsetHeight;
    var vw = window.innerWidth;
    var vh = window.innerHeight;

    /* أفقيًا: وسّط البطاقة على الأيقونة ثم احصرها داخل الشاشة */
    var left = r.left + r.width / 2 - pw / 2;
    left = Math.max(12, Math.min(left, vw - pw - 12));
    pop.style.left = left + "px";

    /* رأسيًا: فوق الأيقونة إن اتسعت المساحة، وإلا تحتها */
    if (r.top - ph - 10 >= 8) {
      pop.style.top = (r.top - ph - 10) + "px";
    } else if (r.bottom + ph + 10 <= vh - 8) {
      pop.style.top = (r.bottom + 10) + "px";
    } else {
      /* لا مساحة فوق ولا تحت — ثبّتها بمنتصف الشاشة رأسيًا */
      pop.style.top = Math.max(8, (vh - ph) / 2) + "px";
    }
  }

  function iconOf(target) {
    return target && target.closest ? target.closest(".hub-hint") : null;
  }

  document.addEventListener("mouseover", function (e) {
    var icon = iconOf(e.target);
    if (icon) show(icon);
    else if (owner) hide();
  });
  document.addEventListener("focusin", function (e) {
    var icon = iconOf(e.target);
    if (icon) show(icon);
  });
  document.addEventListener("focusout", function (e) {
    if (iconOf(e.target)) hide();
  });
  /* لمس/نقر: تبديل الإظهار */
  document.addEventListener("click", function (e) {
    var icon = iconOf(e.target);
    if (icon) {
      if (owner === icon) hide(); else show(icon);
    } else if (owner) {
      hide();
    }
  });
  window.addEventListener("scroll", hide, true);
  window.addEventListener("resize", hide);
})();
