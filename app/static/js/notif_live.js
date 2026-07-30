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
  // MT95 — نتتبّع **معرّف** أحدث إشعار لا عدد غير المقروء وحده. العدّاد
  // زنادٌ خادع: افتح الجرس فتُعلَّم إشعاراتٌ مقروءة (العدّاد ينقص)، ثمّ يصل
  // إشعارٌ جديد فيعود العدّاد لنفس الرقم ⇒ «لا جديد» ⇒ صمتٌ تامّ. وهذا
  // بالضبط ما يجعل الصوت يعمل مرّةً بعد كلّ تحديثٍ للصفحة ثمّ يسكت.
  var last = { alerts: intOr(CFG.alerts, 0), notif: intOr(CFG.notif, 0), notifId: 0 };
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
  // MT103 — كلّ إيماءةٍ يقبلها المتصفّح كإذنٍ للصوت. كانت ثلاثًا فقط،
  // فمن يتنقّل بالنموذج (إرسال ← إعادة تحميل) قد لا يُطلق أيًّا منها على
  // الصفحة الجديدة فيبقى الصوت محبوسًا. الالتقاط في مرحلة capture كي لا
  // يبتلعها معالجٌ يُوقف الانتشار.
  ["pointerdown", "mousedown", "click", "keydown", "touchstart",
   "touchend", "submit", "scroll"].forEach(function (ev) {
    window.addEventListener(ev, unlock, { once: false, passive: true,
                                          capture: true });
  });
  // ومحاولةٌ فورية عند العودة للتبويب: المتصفّح يقبلها أحيانًا كإيماءة.
  document.addEventListener("visibilitychange", function () {
    if (!document.hidden) unlock();
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

  function playCustom(evt, ntype, done) {
    if (!soundOn()) return false;
    var key = (evt || "") + "|" + (ntype || "");
    if (soundMissing[key]) return false;   // معروفٌ أنّه بلا صوت ⇒ نغمة
    var c = ensureCtx();
    if (!c) return false;
    if (c.state === "suspended") { c.resume().catch(function () {}); }

    function play(buf, onDone) {
      try {
        var src = c.createBufferSource();
        src.buffer = buf;
        src.connect(c.destination);
        // MT102 — الطابور الحقيقيّ: التالي يبدأ حين **ينتهي** السابق، لا
        // بعد مهلةٍ ثابتة. المهلة الثابتة كانت تخمينًا يفشل في الطرفين:
        // تسجيلٌ أطول منها يتراكب، وأقصر يترك فجوة. `onended` يعرف المدّة
        // الحقيقيّة بلا تخمين.
        if (onDone) src.onended = onDone;
        src.start(0);
        return true;
      } catch (e) { if (onDone) onDone(); return false; }
    }
    if (soundBuffers[key]) { play(soundBuffers[key], done); return true; }

    var url = SOUND_URL + "?event=" + encodeURIComponent(evt || "")
            + "&type=" + encodeURIComponent(ntype || "");
    fetch(url, { credentials: "same-origin" }).then(function (r) {
      if (r.status === 404) { soundMissing[key] = true; chime(); throw 0; }
      if (!r.ok) throw 0;
      return r.arrayBuffer();
    }).then(function (ab) {
      c.decodeAudioData(ab, function (buf) {
        soundBuffers[key] = buf;
        play(buf, done);
      }, function () { chime(); if (done) done(); });
    }).catch(function () {
      // 404 أو تعذّر: النغمة عُزفت أعلاه؛ نُحرّر الطابور بمدّتها التقريبيّة.
      if (done) setTimeout(done, 900);
    });
    return true;
  }

  /* ─────── MT102: طابور أصوات الإشعارات ───────
     إشعاراتٌ تصل معًا يجب أن تُسمَع كلّها، واحدًا إثر واحد. التشغيل
     المتزامن ضجيجٌ لا معلومة، والمهلة الثابتة تخمينٌ يفشل في الطرفين.

     • الطابور واحدٌ للصفحة كلّها؛ لا تشغيلَ متوازيًا أبدًا.
     • البند التالي يبدأ من `onended` — أي بعد المدّة الحقيقيّة تمامًا.
     • سقفٌ ٨ عناصر: دفعةٌ أكبر تعني نصف دقيقةٍ من الكلام المتّصل، وهو
       إزعاجٌ لا إفادة. ما يُسقَط يُسجَّل في الطابور لا يُبتلع صامتًا.
     • حارسٌ زمنيّ (٦ث) لكلّ عنصر: لو لم يصل `onended` (فشل فكّ ترميز،
       أو سياقٌ أُوقف) لا يتجمّد الطابور إلى الأبد. */
  var soundQueue = [];
  var queueBusy = false;
  var dropped = 0;              // ما فاض عن السقف — يُسجَّل لا يُبتلع
  var QUEUE_MAX = 8;
  var ITEM_TIMEOUT = 6000;

  function enqueueSound(evt, ntype) {
    if (soundQueue.length >= QUEUE_MAX) {
      dropped++;
      return;
    }
    soundQueue.push({ evt: evt || "", type: ntype || "" });
    pumpQueue();
  }

  /* MT103 — 🔴 جذر «الشارة فوريّة والصوت بعد دقيقتين».
     المتصفّح يُنشئ AudioContext **موقوفًا** حتى أوّل إيماءة على الصفحة.
     و`start()` على سياقٍ موقوف **لا يفشل ولا يُهمَل** — بل يُجدوِل الصوت
     فيُعزف لحظة استئناف السياق. فيُضيف المشغّل سلفةً، تُعاد الصفحة (فيولد
     سياقٌ جديد موقوف)، ويقف ينتظر بلا نقر — والصوت ينتظر معه حتى ينقر
     أوّل مرّة بعد دقيقتين، فينفجر كلّ ما تراكم دفعةً.

     الحلّ: لا نُشغّل شيئًا على سياقٍ موقوف. نُبقيه في الطابور ونستأنف،
     ونُخبر المشغّل مرّةً واحدة أنّ نقرةً تلزم — بدل صمتٍ لا يُفسَّر. */
  var audioBlockedNotified = false;

  function ctxReady(cb) {
    var c = ensureCtx();
    if (!c) { cb(false); return; }
    if (c.state === "running") { cb(true); return; }
    c.resume().then(function () { cb(c.state === "running"); })
              .catch(function () { cb(false); });
  }

  function pumpQueue() {
    if (queueBusy || !soundQueue.length) return;
    var ctx = ensureCtx();
    if (ctx && ctx.state !== "running") {
      // موقوف: لا نُجدوِل شيئًا (وإلّا انفجر لاحقًا دفعةً). نستأنف ونُعاود.
      ctx.resume().catch(function () {});
      if (!audioBlockedNotified) {
        audioBlockedNotified = true;
        try {
          toast("notif", "🔇 المتصفّح يمنع الصوت حتى تنقر في الصفحة — انقر مرّة.");
        } catch (e) {}
      }
      setTimeout(pumpQueue, 700);          // أعِد المحاولة حتى يُستأنف
      return;
    }
    audioBlockedNotified = false;
    queueBusy = true;
    var item = soundQueue.shift();
    var settled = false;
    function next() {
      if (settled) return;              // onended + المهلة قد يتسابقان
      settled = true;
      queueBusy = false;
      pumpQueue();
    }
    setTimeout(next, ITEM_TIMEOUT);     // حارسٌ ضدّ تجمّد الطابور
    if (!playCustom(item.evt, item.type, next)) { chime(); setTimeout(next, 900); }
  }

  /* الإشعار الأحدث يُملي الصوت: هو ما وصل للتوّ. */
  function alertSound(data) {
    var items = (data && data.notif && data.notif.items) || [];
    var top = items[0] || {};
    enqueueSound(top.event || "", top.type || "");
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
    // أحدث معرّف وصل في هذه الجولة. الصفر يعني قائمةً فارغة (لا نُطلق).
    var topId = intOr(data.notif && data.notif.items && data.notif.items[0]
                      && data.notif.items[0].id, 0);
    // أوّل جولة بعد تحميل الصفحة تُثبّت المرجع فقط — لا تُطلق صوتًا لما
    // كان موجودًا قبل أن تفتح اللوحة.
    var firstRound = (last.notifId === 0);
    var newNotif = firstRound ? (nCount > last.notif) : (topId > last.notifId);
    // MT101 — كم إشعارًا جديدًا فاتنا في هذه الجولة؟ إشعاران يصلان بين
    // استطلاعين يُطويان في مشاهدةٍ واحدة، فيُسمَع صوتٌ واحد ويظنّ المشغّل
    // أنّ الثاني «ما وصل». نعدّ الجدد ونُشغّل صوت كلٍّ منهم بالتتابع.
    var fresh = [];
    if (newNotif && !firstRound) {
      var items = (data.notif && data.notif.items) || [];
      for (var i = items.length - 1; i >= 0; i--) {     // الأقدم أوّلًا
        if (intOr(items[i] && items[i].id, 0) > last.notifId) fresh.push(items[i]);
      }
    }
    if (topId) last.notifId = Math.max(last.notifId, topId);

    setBadge("bell-toggle", aCount);
    setBadge("notif-toggle", nCount);

    if (newAlert || newNotif) {
      // كلّ إشعارٍ جديد يدخل الطابور — واحدًا كان أو دفعة.
      if (fresh.length) fresh.forEach(function (it) {
        enqueueSound(it.event || "", it.type || "");
      });
      else alertSound(data);
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

  // MT120 — 401/403 يعنيان بابًا مُغلقًا لا عطبًا عابرًا: انتهت الجلسة، أو
  // الصفحة تخصّ شبكةً ليست شبكة المستخدم. الاستمرار في الطلب يُنتج ضربةً
  // كلّ ثوانٍ على الخادم وسطرًا في السجلّ لا يُصلح شيئًا. نتوقّف نهائيًّا.
  var pollStopped = false;
  function stopPolling() {
    pollStopped = true;
    if (window.__hrPollTimers) {
      window.__hrPollTimers.forEach(function (t) { clearInterval(t); });
      window.__hrPollTimers = [];
    }
  }

  function poll() {
    if (pollStopped) return;
    fetch(CFG.pollUrl, { headers: { "X-Requested-With": "fetch" }, credentials: "same-origin" })
      .then(function (r) {
        if (r.status === 401 || r.status === 403) { stopPolling(); return null; }
        return r.ok ? r.json() : null;
      })
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
    // MT101 — «مو دايمًا بيصل، ولمّا يصل بعد دقيقة». الدقيقة هي بالضبط حدّ
    // خنق المتصفّح للتبويب المخفيّ؛ لا حيلة فيه. لكنّ التبويب **الظاهر**
    // كان ينتظر ٢٠ ثانية بلا داعٍ — والمشغّل يقف أمام اللوحة ينتظر صوتًا.
    // فاصلان: ٦ ثوانٍ وأنت ناظر، والفاصل الكامل حين تُخفي (وهو مخنوقٌ
    // أصلًا فلا فرق). الطلب رخيص: عدّادان وستّة عناوين.
    var FAST = 6000;
    // تُحفظ المؤقّتات كي يستطيع stopPolling إيقافها فعلًا عند 401/403.
    window.__hrPollTimers = [
      setInterval(function () { if (!document.hidden) poll(); }, FAST),
      setInterval(function () { if (document.hidden) poll(); }, interval),
    ];

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
