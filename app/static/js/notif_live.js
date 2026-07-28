/* notif_live.js — إشعارات حيّة بلا تحديث الصفحة + صوت مميّز عالٍ.
 *
 * يستطلع /notifications/poll دوريًّا (مصادقة جلسة)، يُحدّث شارتَي الأعلى
 * (تنبيهات + إشعارات) حيًّا، وعند وصول جديد: يُشغّل نغمة تنبيه لافتة + يعرض
 * توست بعنوان الإشعار + (إن أُذِن) إشعار سطح المكتب. زرّ كتم يحفظ التفضيل.
 *
 * الإعداد يأتي من window.HR_NOTIF = {pollUrl, interval, alerts, notif}.
 */
(function () {
  "use strict";
  var CFG = window.HR_NOTIF || {};
  if (!CFG.pollUrl) return;

  var interval = Math.max(8000, parseInt(CFG.interval, 10) || 20000);
  var last = { alerts: intOr(CFG.alerts, 0), notif: intOr(CFG.notif, 0) };
  var started = false;

  var SOUND_URL = (function () {
    // من data-sound-url على وسم السكربت: بادئة المخطّط قد تتغيّر
    // (مسار الشبكة /<slug>/admin) فالمسار المكتوب يدويًّا يَكسر.
    var el = document.currentScript
      || document.querySelector('script[data-sound-url]');
    return (el && el.getAttribute("data-sound-url")) || "";
  })();

  function intOr(v, d) { var n = parseInt(v, 10); return isFinite(n) ? n : d; }
  function soundOn() { return localStorage.getItem("hr_notif_sound") !== "0"; }
  function setSound(on) { localStorage.setItem("hr_notif_sound", on ? "1" : "0"); }

  /* ─────────────── الصوت (WebAudio — بلا ملفّات خارجيّة) ─────────────── */
  var actx = null, unlocked = false;
  function ensureCtx() {
    if (actx) return actx;
    try {
      var AC = window.AudioContext || window.webkitAudioContext;
      if (AC) actx = new AC();
    } catch (e) { actx = null; }
    return actx;
  }
  function unlock() {
    var c = ensureCtx();
    if (c && c.state === "suspended") { c.resume().catch(function () {}); }
    unlocked = true;
  }
  ["pointerdown", "keydown", "touchstart"].forEach(function (ev) {
    window.addEventListener(ev, unlock, { once: false, passive: true });
  });

  // نغمة لافتة: ثلاث نغمات صاعدة تُعزف مرّتين — مسموعة ومميّزة.
  function chime() {
    if (!soundOn()) return;
    var c = ensureCtx();
    if (!c) return;
    if (c.state === "suspended") { c.resume().catch(function () {}); }
    var t0 = c.currentTime;
    var notes = [880, 1174.7, 1568, 0, 880, 1174.7, 1568]; // A5 D6 G6 (×2)
    var step = 0.13;
    notes.forEach(function (f, i) {
      if (!f) return;
      var t = t0 + i * step;
      var osc = c.createOscillator();
      var g = c.createGain();
      osc.type = "triangle";
      osc.frequency.setValueAtTime(f, t);
      g.gain.setValueAtTime(0.0001, t);
      g.gain.exponentialRampToValueAtTime(0.6, t + 0.02);   // عالٍ
      g.gain.exponentialRampToValueAtTime(0.0001, t + step * 0.95);
      osc.connect(g); g.connect(c.destination);
      osc.start(t); osc.stop(t + step);
    });
  }

  /* ───── MT90: الصوت المخصّص (تسجيل بدل النغمة) ─────
     يُشغَّل عبر AudioContext لا عبر new Audio().play(): الثاني تحجبه سياسة
     التشغيل التلقائيّ داخل مؤقّت الاستطلاع (لا إيماءة مستخدم في تلك اللحظة)
     فيسقط للنغمة رغم وجود الصوت — وهو العطب نفسه الذي ظهر في هوب هب.
     السياق مُفعَّل بأوّل نقرة (unlock أعلاه)، والـBufferSource لا يُحجب.

     المسار يحلّ التسلسل خادميًّا (حدث ← نوع ← عامّ)، فإن ردّ 404 فلا صوت
     مخصّص أصلًا ⇒ النغمة. الكاش بالمفتاح كي لا نُنزّل الصوت كلّ إشعار. */
  var soundBuffers = {};      // مفتاح → AudioBuffer
  var soundMissing = {};      // مفتاح → true (404، لا تُعِد الطلب)

  function playCustom(evt, ntype) {
    if (!soundOn()) return false;
    var key = (evt || "") + "|" + (ntype || "");
    if (soundMissing[key]) return false;
    var c = ensureCtx();
    if (!c) return false;
    if (c.state === "suspended") { c.resume().catch(function () {}); }

    function play(buf) {
      try {
        var src = c.createBufferSource();
        src.buffer = buf;
        src.connect(c.destination);
        src.start(0);
        return true;
      } catch (e) { return false; }
    }
    if (soundBuffers[key]) { play(soundBuffers[key]); return true; }

    var url = SOUND_URL + "?event=" + encodeURIComponent(evt || "")
            + "&type=" + encodeURIComponent(ntype || "");
    fetch(url, { credentials: "same-origin" }).then(function (r) {
      if (r.status === 404) { soundMissing[key] = true; chime(); throw 0; }
      if (!r.ok) throw 0;
      return r.arrayBuffer();
    }).then(function (ab) {
      c.decodeAudioData(ab, function (buf) {
        soundBuffers[key] = buf;
        play(buf);
      }, function () { chime(); });
    }).catch(function () { /* عولج أعلاه أو تعذّر — النغمة كافية */ });
    return true;
  }

  /* الإشعار الأحدث يُملي الصوت: هو ما وصل للتوّ. */
  function alertSound(data) {
    var items = (data && data.notif && data.notif.items) || [];
    var top = items[0] || {};
    if (!playCustom(top.event || "", top.type || "")) chime();
  }

  /* ─────────────── الشارات (نقطة العدّ فوق الأيقونة) ─────────────── */
  function setBadge(anchorId, count) {
    var a = document.getElementById(anchorId);
    if (!a) return;
    var dot = a.querySelector(".dot");
    if (count > 0) {
      if (!dot) {
        dot = document.createElement("span");
        dot.className = "dot";
        a.appendChild(dot);
      }
      dot.textContent = count < 100 ? String(count) : "99+";
      // نبضة لافتة عند التحديث
      dot.classList.remove("hr-badge-pulse");
      void dot.offsetWidth;
      dot.classList.add("hr-badge-pulse");
    } else if (dot) {
      dot.remove();
    }
    // عدّ الترويسة داخل القائمة المنسدلة
    document.querySelectorAll('[data-hr-headcount="' + anchorId + '"]').forEach(function (el) {
      el.textContent = count;
    });
  }

  /* ─────────────── التوست (بطاقة عائمة لافتة) ─────────────── */
  function toastHost() {
    var h = document.getElementById("hr-notif-toasts");
    if (!h) {
      h = document.createElement("div");
      h.id = "hr-notif-toasts";
      document.body.appendChild(h);
    }
    return h;
  }
  function toast(kind, title) {
    var host = toastHost();
    var card = document.createElement("div");
    card.className = "hr-notif-toast " + (kind === "alert" ? "is-alert" : "is-notif");
    card.innerHTML =
      '<span class="hr-nt-ic"><i class="fa-solid ' +
      (kind === "alert" ? "fa-triangle-exclamation" : "fa-bell") + '"></i></span>' +
      '<span class="hr-nt-body"><b>' +
      (kind === "alert" ? "تنبيه جديد" : "إشعار جديد") +
      "</b><span>" + escapeHtml(title || "") + "</span></span>" +
      '<button class="hr-nt-x" aria-label="إغلاق">&times;</button>';
    card.querySelector(".hr-nt-x").addEventListener("click", function () { dismiss(card); });
    card.addEventListener("click", function (e) {
      if (e.target.closest(".hr-nt-x")) return;
      var url = kind === "alert" ? CFG.alertsUrl : CFG.notifUrl;
      if (url) window.location.href = url;
    });
    host.appendChild(card);
    requestAnimationFrame(function () { card.classList.add("in"); });
    setTimeout(function () { dismiss(card); }, 7000);
  }
  function dismiss(card) {
    card.classList.remove("in");
    setTimeout(function () { if (card.parentNode) card.parentNode.removeChild(card); }, 300);
  }
  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  /* ─────────────── إشعار سطح المكتب (اختياري، إن أُذِن) ─────────────── */
  function desktopNotify(title, body) {
    try {
      if (!("Notification" in window)) return;
      if (Notification.permission === "granted") {
        new Notification(title, { body: body || "", tag: "hr-notif", renotify: true });
      }
    } catch (e) {}
  }
  function askDesktopPermissionOnce() {
    try {
      if (("Notification" in window) && Notification.permission === "default") {
        // نطلب الإذن عند أوّل تفاعل (سياسة المتصفّحات).
        window.addEventListener("pointerdown", function once() {
          window.removeEventListener("pointerdown", once);
          Notification.requestPermission().catch(function () {});
        }, { once: true });
      }
    } catch (e) {}
  }

  /* ─────────────── حلقة الاستطلاع ─────────────── */
  function apply(data) {
    if (!data || !data.ok) return;
    var aCount = intOr(data.alerts && data.alerts.count, 0);
    var nCount = intOr(data.notif && data.notif.count, 0);
    var newAlert = aCount > last.alerts;
    var newNotif = nCount > last.notif;

    setBadge("bell-toggle", aCount);
    setBadge("notif-toggle", nCount);

    if (newAlert || newNotif) {
      alertSound(data);
      if (newNotif) {
        var nt = (data.notif.items && data.notif.items[0] && data.notif.items[0].title) || "لديك إشعار جديد";
        toast("notif", nt);
        desktopNotify("إشعار جديد", nt);
      }
      if (newAlert) {
        var at = (data.alerts.items && data.alerts.items[0] && data.alerts.items[0].title) || "تنبيه جديد";
        toast("alert", at);
        desktopNotify("تنبيه جديد", at);
      }
    }
    last.alerts = aCount;
    last.notif = nCount;
  }

  function poll() {
    fetch(CFG.pollUrl, { headers: { "X-Requested-With": "fetch" }, credentials: "same-origin" })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(apply)
      .catch(function () {});
  }

  function start() {
    if (started) return;
    started = true;
    askDesktopPermissionOnce();
    // MT94 — تأخيرٌ بلغ خمس دقائق قبل أن يُسمَع الإشعار. السبب مركَّب،
    // وجزؤه الخفيّ أهمّه: المتصفّحات تَخنق setInterval في التبويب المخفيّ
    // إلى **مرّةٍ كلّ دقيقة** مهما كان الفاصل المطلوب. وكان الكود يرمي فوق
    // ذلك نردًا يتخطّى ثلثَي المحاولات ⇒ ٣ دقائق وسطيًّا وذيلٌ يبلغ ٥.
    //
    // النرد أُزيل: خنق المتصفّح وحده كافٍ لتهدئة التبويب المخفيّ، وطلبٌ
    // خفيفٌ كلّ دقيقة لا يُثقل شيئًا.
    setInterval(poll, interval);

    // والأهمّ: استطلاعٌ فوريّ لحظة عودة التبويب للظهور. من يترك اللوحة ثم
    // يعود إليها يجب أن يجد الحالة الآن لا بعد دورةٍ كاملة — وهذه هي الحالة
    // التي وقع فيها التأخير المُبلَّغ عنه.
    document.addEventListener("visibilitychange", function () {
      if (!document.hidden) poll();
    });
    // وكذلك عند العودة للنافذة نفسها (تبديل تطبيقات لا تبويبات).
    window.addEventListener("focus", poll);

    setTimeout(poll, 4000);
  }

  // زرّ الكتم (إن وُجد في الترويسة).
  document.addEventListener("click", function (e) {
    var btn = e.target.closest("[data-hr-mute-toggle]");
    if (!btn) return;
    e.preventDefault();
    e.stopPropagation();
    var on = !soundOn();
    setSound(on);
    syncMuteBtn();
    if (on) { unlock(); chime(); }   // معاينة فوريّة عند التفعيل
  });
  function syncMuteBtn() {
    document.querySelectorAll("[data-hr-mute-toggle]").forEach(function (btn) {
      var on = soundOn();
      btn.setAttribute("title", on ? "كتم صوت الإشعارات" : "تفعيل صوت الإشعارات");
      var i = btn.querySelector("i");
      if (i) i.className = "fa-solid " + (on ? "fa-volume-high" : "fa-volume-xmark");
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", function () { syncMuteBtn(); start(); });
  } else { syncMuteBtn(); start(); }
})();
