/* Operations assistant (experimental) — web chat over the deterministic executor.
   ONE chat implementation used by:
     • the full page      templates/radius/ops_assistant.html ([data-ops-root="page"] + #ops-config)
     • the floating panel templates/radius/_ops_widget_panel.html, fetched on the first click
       of the bubble rendered by admin/_admin_layout.html (#ops-fab) — see OpsWidget below.
   Markup + translated strings: templates/radius/_ops_chat.html. Styles: css/ops_assistant.css.
   Server: routes/ops_assistant.py. Nothing executes without the «confirm» click.
   Every POST carries X-CSRFToken. All dynamic text goes through textContent (no innerHTML):
   the model's `message` is shown as plain text, never parsed as HTML.
   Reply types: assistant (message) · choices (list, or an empty-state line) · result
   (read-only INFO answer from the system) · proposal (confirmation card) · error.
   The conversation id is kept per tenant+admin in sessionStorage, so the page and the
   floating panel continue the same conversation across navigations (text history via
   GET …/history; cards are never replayed). */
(function () {
  "use strict";
  if (window.OpsChat) return;   // loaded once (page script tag or the bubble's lazy loader)

  function storeGet(k) { try { return window.sessionStorage.getItem(k); } catch (e) { return null; } }
  function storeSet(k, v) {
    try {
      if (v === null) window.sessionStorage.removeItem(k);
      else window.sessionStorage.setItem(k, v);
    } catch (e) { /* private mode / blocked storage: the chat works, it just won't resume */ }
  }

  function csrf() {
    var m = document.querySelector('meta[name="csrf-token"]');
    return m ? m.content : "";
  }

  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined && text !== null) e.textContent = String(text);
    return e;
  }

  function post(url, body) {
    return fetch(url, {
      method: "POST", credentials: "same-origin",
      headers: { "Content-Type": "application/json", "Accept": "application/json",
                 "X-CSRFToken": csrf(), "X-Requested-With": "XMLHttpRequest" },
      body: JSON.stringify(body || {})
    }).then(function (r) {
      return r.json().catch(function () { return { ok: false }; });
    });
  }

  /* mount(root, CFG, opts) — wires one chat inside `root`
     ([data-ops=log|form|input|send|new|events]).
     opts.secretModal  id of the one-time password modal
     opts.storageKey   tenant+admin suffix → the conversation id survives navigations
     opts.onActivity   called when an assistant reply is rendered (unread dot) */
  function mount(root, CFG, opts) {
    opts = opts || {};
    var T = CFG.t || {}, L = CFG.labels || {}, V = CFG.values || {}, U = CFG.urls || {};
    function q(name) { return root.querySelector('[data-ops="' + name + '"]'); }
    var log = q("log");
    var form = q("form");
    var input = q("input");
    var sendBtn = q("send");
    var storageKey = opts.storageKey ? "hr_ops_cid:" + opts.storageKey : null;
    var conversationId = storageKey ? storeGet(storageKey) : null;
    var busy = false;
    var replaying = false;
    var titles = {};   // action -> title_ar from the executor's cards (for the result card)

    function setCid(cid) {
      conversationId = cid || null;
      if (storageKey) storeSet(storageKey, conversationId);
    }

    function scroll() { log.scrollTop = log.scrollHeight; }

    function add(node) {
      log.appendChild(node);
      scroll();
      if (!replaying && opts.onActivity && !/\b(is-user|is-wait)\b/.test(node.className)) {
        opts.onActivity();
      }
      return node;
    }

    function say(text, kind) { return add(el("div", "ops-msg " + (kind || "is-bot"), text)); }

    function setBusy(b) {
      busy = b;
      sendBtn.disabled = b;
      input.disabled = b;
    }

    function fmtValue(key, v) {
      if (v === null || v === undefined || v === "") return "-";
      if (typeof v === "boolean") return v ? V["true"] : V["false"];
      if (typeof v === "object") return JSON.stringify(v);
      var s = String(v);
      if (/_local$/.test(key) && /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}/.test(s)) s = s.replace("T", " ");
      var dm = /^(?:(\d+)d)?\s*(?:(\d+)h)?\s*(?:(\d+)m)?$/.exec(s);
      if ((key === "remaining" || key === "card_time" || key === "used_time" || key === "elapsed") && dm && (dm[1] || dm[2] || dm[3])) {
        var parts = [];
        if (dm[1]) parts.push(dm[1] + " " + T.day_unit);
        if (dm[2]) parts.push(dm[2] + " " + T.hour_unit);
        if (dm[3] || !parts.length) parts.push((dm[3] || 0) + " " + T.minutes_unit);
        return parts.join(T.and_sep);
      }
      if (key === "expire_local" && s === "no_expiry") return T.no_expiry;
      if (key === "expire_local" && s.indexOf("server_default:") === 0) return T.server_default;
      if (Object.prototype.hasOwnProperty.call(V, s) &&
          (key === "charge_mode" || key === "policy" || key === "mode" || key === "status" ||
           key === "user_type" || key === "counting")) return V[s];
      return s;
    }

    function kv(rows) {
      var dl = el("dl", "ops-kv");
      rows.forEach(function (r) {
        dl.appendChild(el("dt", "", r[0]));
        dl.appendChild(el("dd", "", r[1]));
      });
      return dl;
    }

    /* ── cards ─────────────────────────────────────────────────── */

    function itemParts(it, keys) {
      var parts = [];
      keys.forEach(function (k) {
        if (it[k] === undefined || it[k] === null || it[k] === "") return;
        parts.push(k === "status" || k === "outcome" ? fmtValue("status", it[k]) : String(it[k]));
      });
      return parts;
    }

    var CHOICE_KEYS = ["name", "username", "full_name", "plan_name", "plan", "label_ar", "status",
                       "expires_local", "price", "currency"];

    function choicesCard(rep) {
      var fuzzy = rep.match === "fuzzy";
      var card = el("div", "ops-card" + (fuzzy ? " is-suggest" : ""));
      card.appendChild(el("h4", "", fuzzy ? T.suggest_title : T.choices_title));
      if (rep.match === "card") card.appendChild(el("div", "ops-hint", T.card_match));
      var ul = el("ul", "ops-choices");
      var buttons = [];
      (rep.items || []).forEach(function (it) {
        var li = el("li");
        var parts = itemParts(it, CHOICE_KEYS);
        if (fuzzy) {
          /* «هل تقصد…؟» — a suggestion is usable only after THIS click (the server
             resolves it exactly and only then the assistant may act on it) */
          if (it.matched) parts.push((T["m_" + it.matched] || it.matched) +
            (it.mobile ? ": " + it.mobile : it.national_id_tail ? ": " + it.national_id_tail : ""));
          if (it.created_local) parts.push(T.created + " " + it.created_local);
          var b = el("button", "hub-btn hub-btn--ghost hub-btn--sm ops-pick",
                     (it.n || "") + ". " + parts.join(" · "));
          b.type = "button";
          b.addEventListener("click", function () { pickChoice(it, parts[0] || "", buttons); });
          buttons.push(b);
          li.appendChild(b);
        } else {
          li.appendChild(el("b", "", (it.n || "") + "."));
          li.appendChild(document.createTextNode(" " + parts.join(" · ")));
        }
        ul.appendChild(li);
      });
      card.appendChild(ul);
      if (fuzzy) card.appendChild(el("div", "ops-hint", T.suggest_hint));
      if (rep.truncated) card.appendChild(el("div", "ops-hint", T.choices_more));
      return card;
    }

    function pickChoice(it, label, buttons) {
      if (busy || !conversationId) return;
      buttons.forEach(function (b) { b.disabled = true; });
      setBusy(true);
      say(T.pick_said + " " + label, "is-user");
      var wait = say(T.thinking, "is-wait");
      post(U.pick, { conversation_id: conversationId, n: it.n })
        .then(function (res) {
          wait.remove();
          if (!res || !res.ok) { say((res && res.error) || T.network, "is-err"); return; }
          render(res.replies);
        })
        .catch(function () { wait.remove(); say(T.network, "is-err"); })
        .then(function () { setBusy(false); input.focus(); });
    }

    /* read-only answer from the system (INFO action) — the model's message follows as a bubble */
    function infoCard(rep) {
      var card = el("div", "ops-card");
      card.appendChild(el("h4", "", T["info_" + rep.source] || T.info_title));
      if (rep.error) {
        card.appendChild(el("div", "ops-note is-danger", T["ie_" + rep.error] || T.ie_unavailable));
        return card;
      }
      var d = rep.data || {}, rows = [];
      Object.keys(d).forEach(function (k) {
        if (k === "items" || k === "query" || k === "truncated" || k === "mine") return;
        // the batch card shows its id in the title; a single card shows which batch it is in
        if (k === "batch_id" && rep.source !== "card_info") return;
        rows.push([L[k] || k, fmtValue(k, d[k])]);
      });
      if (rows.length) card.appendChild(kv(rows));
      if (d.items && d.items.length) {
        var ul = el("ul", "ops-choices");
        d.items.forEach(function (it) {
          var li = el("li");
          li.appendChild(el("b", "", (it.n || "") + "."));
          var parts = rep.source === "online_sessions"
            ? [it.username, fmtValue("user_type", it.user_type), it.started_local]
              .filter(function (x) { return x !== undefined && x !== null && x !== "" && x !== "-"; })
            : itemParts(it, ["when_local", "action", "target", "name", "username", "full_name",
                             "plan_name", "plan", "status", "outcome", "created_local",
                             "expires_local", "available_count"]);
          li.appendChild(document.createTextNode(" " + parts.join(" · ")));
          ul.appendChild(li);
        });
        card.appendChild(ul);
      }
      if (d.truncated) card.appendChild(el("div", "ops-hint", T.info_more));
      return card;
    }

    function stepBlock(st, multi) {
      var box = el("div", "ops-step");
      var title = (multi ? T.step + " " + st.n + ": " : "") + (st.title_ar || st.action);
      box.appendChild(el("div", "", title)).style.fontWeight = "800";
      var rows = [];
      var vals = st.values || {}, names = st.names || {}, disp = st.display || {};
      if (st.action && st.title_ar) titles[st.action] = st.title_ar;
      Object.keys(vals).forEach(function (k) {
        if (k === "expire_at" && disp.expire_local) return;   // the local time is shown below
        var shown = fmtValue(k, vals[k]);
        if (names[k]) shown = names[k] + " (#" + vals[k] + ")";
        rows.push([L[k] || k, shown]);
      });
      Object.keys(disp).forEach(function (k) {
        rows.push([L[k] || k, fmtValue(k, disp[k])]);
      });
      Object.keys(st.pending_refs || {}).forEach(function (k) {
        var ref = String(st.pending_refs[k]).replace(/^\$step(\d+)\..*$/, "$1");
        rows.push([L[k] || k, T.from_step + " " + ref]);
      });
      if (rows.length) box.appendChild(kv(rows));
      if (st.password) box.appendChild(el("div", "ops-note", T.password_note));
      if (st.danger === "L3") box.appendChild(el("div", "ops-note is-danger", T.danger));
      if (st.executable === false) box.appendChild(el("div", "ops-note is-danger", T.not_exec));
      return box;
    }

    function proposalCard(rep) {
      var p = rep.proposal || {};
      var steps = p.steps || [];
      var card = el("div", "ops-card");
      card.appendChild(el("h4", "", steps.length > 1 ? T.plan_title : T.proposal_title));
      steps.forEach(function (st) { card.appendChild(stepBlock(st, steps.length > 1)); });
      var actions = el("div", "ops-actions");
      var ok = el("button", "hub-btn hub-btn--primary", T.confirm);
      var no = el("button", "hub-btn hub-btn--ghost", T.cancel);
      ok.type = "button"; no.type = "button";
      actions.appendChild(ok); actions.appendChild(no);
      card.appendChild(actions);
      var cid = conversationId;   // the card belongs to THIS conversation
      function lock() { ok.disabled = true; no.disabled = true; }
      ok.addEventListener("click", function () {
        if (busy) return;
        lock(); setBusy(true);
        var wait = say(T.thinking, "is-wait");
        post(U.confirm, { conversation_id: cid, proposal_id: p.proposal_id,
                          proposal_hash: p.proposal_hash })
          .then(function (res) {
            wait.remove();
            if (!res || !res.ok) { say((res && res.error) || T.network, "is-err"); return; }
            add(resultCard(res.report || {}));
            if (res.show_once) showOnce(res.show_once);
          })
          .catch(function () { wait.remove(); say(T.network, "is-err"); })
          .then(function () { setBusy(false); });
      });
      no.addEventListener("click", function () {
        if (busy) return;
        lock();
        post(U.cancel, { conversation_id: cid }).then(function () {
          say(T.cancelled);
        }).catch(function () { say(T.cancelled); });
      });
      return card;
    }

    function resultCard(report) {
      var card = el("div", "ops-card");
      var label = { executed: T.rs_executed, failed: T.rs_failed, partial: T.rs_partial }[report.status]
        || report.status;
      card.appendChild(el("h4", "", T.result_title + " — " + label));
      if (report.replayed) card.appendChild(el("div", "ops-note", T.replayed));
      (report.steps || []).forEach(function (s) {
        var row = el("div", "ops-step");
        var head = el("div");
        head.appendChild(el("span", "ops-status " + (s.status || ""),
          { done: T.st_done, failed: T.st_failed, not_run: T.st_not_run }[s.status] || s.status));
        head.appendChild(document.createTextNode(" " + T.step + " " + s.n + " · " +
                                                 (titles[s.action] || s.action || "")));
        row.appendChild(head);
        if (s.error && (s.error.message || s.error.code)) {
          row.appendChild(el("div", "ops-hint", s.error.message || s.error.code));
        }
        card.appendChild(row);
      });
      return card;
    }

    function showOnce(so) {
      var modalId = opts.secretModal || "ops-secret-modal";
      var modal = document.getElementById(modalId);
      var list = modal ? modal.querySelector('[data-ops="secret-list"]') : null;
      if (!list || !modal) return;
      list.textContent = "";
      (so.subscriber_passwords || []).forEach(function (it) {
        var row = el("div", "ops-secret");
        var info = el("div");
        info.appendChild(el("div", "", it.username || ""));
        info.appendChild(el("code", "", it.password || ""));
        var btn = el("button", "hub-btn hub-btn--ghost", T.copy);
        btn.type = "button";
        btn.addEventListener("click", function () {
          var txt = it.password || "";
          var done = function () { btn.textContent = T.copied; };
          if (navigator.clipboard && navigator.clipboard.writeText) {
            navigator.clipboard.writeText(txt).then(done, function () {});
          }
        });
        row.appendChild(info); row.appendChild(btn);
        list.appendChild(row);
      });
      if (window.UDS && window.UDS.openModal) window.UDS.openModal(modalId);
      else modal.hidden = false;
      // the secret leaves the DOM as soon as the modal is closed
      var obs = new MutationObserver(function () {
        if (modal.hidden) { list.textContent = ""; obs.disconnect(); }
      });
      obs.observe(modal, { attributes: true, attributeFilter: ["hidden"] });
    }

    function render(replies) {
      (replies || []).forEach(function (r) {
        if (r.type === "error") { say(r.text, "is-err"); return; }
        if (r.type === "choices") {
          if (r.text) say(r.text);
          if (!(r.items || []).length) { say(r.empty || T.choices_empty, "is-bot is-empty"); return; }
          add(choicesCard(r));
          return;
        }
        if (r.type === "result") {
          if (r.text) say(r.text);
          add(infoCard(r));
          return;
        }
        if (r.empty) { say(r.text || T.choices_empty, "is-bot is-empty"); return; }
        if (r.type === "proposal") {
          if (r.text) say(r.text);
          add(proposalCard(r));
          return;
        }
        if (r.text) say(r.text);
      });
    }

    function send(text) {
      if (busy) return;
      setBusy(true);
      say(text, "is-user");
      var wait = say(T.thinking, "is-wait");
      post(U.message, { conversation_id: conversationId, text: text })
        .then(function (res) {
          wait.remove();
          if (!res || !res.ok) {
            if (res && res.code === "not_found") setCid(null);
            say((res && res.error) || T.network, "is-err");
            return;
          }
          setCid(res.conversation_id || conversationId);
          render(res.replies);
        })
        .catch(function () { wait.remove(); say(T.network, "is-err"); })
        .then(function () { setBusy(false); input.focus(); });
    }

    function clearLog() {
      while (log.children.length > 1) log.removeChild(log.lastChild);   // keep the greeting
    }

    function reset() {
      if (busy) return false;
      setCid(null);
      clearLog();
      return true;
    }

    /* delegated on the chat root — one listener each, independent of re-rendered children */
    root.addEventListener("submit", function (e) {
      if (e.target !== form) return;
      e.preventDefault();
      var text = (input.value || "").trim();
      if (!text) { say(T.empty, "is-err"); return; }
      input.value = "";
      send(text);
    });
    root.addEventListener("keydown", function (e) {
      if (e.target !== input || e.key !== "Enter" || e.shiftKey || e.isComposing) return;
      e.preventDefault();
      if (form.requestSubmit) form.requestSubmit();
      else form.dispatchEvent(new Event("submit", { cancelable: true, bubbles: true }));
    });
    root.addEventListener("click", function (e) {
      var b = e.target.closest ? e.target.closest('[data-ops="new"]') : null;
      if (!b || !root.contains(b)) return;
      if (reset()) input.focus();
    });

    /* ── resume after a navigation (text only; cards are never replayed) ── */

    function loadHistory() {
      if (!conversationId || !U.history) return;
      var cid = conversationId;
      fetch(U.history + "?conversation_id=" + encodeURIComponent(cid),
            { credentials: "same-origin", headers: { "Accept": "application/json" } })
        .then(function (r) { return r.json().catch(function () { return { ok: false }; }); })
        .then(function (res) {
          if (cid !== conversationId) return;          // a new chat started meanwhile
          if (!res || !res.ok) { if (res && res.code === "not_found") setCid(null); return; }
          if (!(res.items || []).length) return;
          var frag = document.createDocumentFragment();
          frag.appendChild(el("div", "ops-hint", T.resumed));
          if (res.truncated) frag.appendChild(el("div", "ops-hint", T.history_more));
          res.items.forEach(function (it) {
            frag.appendChild(el("div", "ops-msg " + (it.role === "user" ? "is-user" : "is-bot"),
                                it.text));
          });
          replaying = true;
          log.insertBefore(frag, log.children[1] || null);
          replaying = false;
          scroll();
        })
        .catch(function () { /* resume is best-effort */ });
    }

    /* ── level-4 suggestions (full page only) ───────────────────── */

    function loadEvents() {
      var box = q("events");
      if (!box) return;
      fetch(U.events, { credentials: "same-origin", headers: { "Accept": "application/json" } })
        .then(function (r) { return r.json(); })
        .then(function (res) {
          box.textContent = "";
          if (!res || !res.ok) {
            box.appendChild(el("li", "ops-hint", (res && res.error) || T.events_error));
            return;
          }
          if (!(res.items || []).length) { box.appendChild(el("li", "ops-hint", T.events_empty)); return; }
          res.items.forEach(function (it) {
            var li = el("li");
            li.appendChild(el("div", "t", it.title));
            li.appendChild(el("div", "d", it.text));
            var b = el("button", "hub-btn hub-btn--ghost hub-btn--sm", T.start);
            b.type = "button";
            b.addEventListener("click", function () { startEvent(it); });
            li.appendChild(b);
            box.appendChild(li);
          });
        })
        .catch(function () { box.textContent = ""; box.appendChild(el("li", "ops-hint", T.events_error)); });
    }

    function startEvent(it) {
      if (busy) return;
      setBusy(true);
      setCid(null);
      clearLog();
      say(T.event_started + " " + it.title + ": " + it.text, "is-user");
      var wait = say(T.thinking, "is-wait");
      post(U.start_event, { event_type: it.event_type, index: it.index })
        .then(function (res) {
          wait.remove();
          if (!res || !res.ok) { say((res && res.error) || T.network, "is-err"); return; }
          setCid(res.conversation_id);
          render(res.replies);
        })
        .catch(function () { wait.remove(); say(T.network, "is-err"); })
        .then(function () { setBusy(false); });
    }

    loadEvents();
    loadHistory();
    return {
      reset: reset,
      scroll: scroll,
      focus: function () { if (!input.disabled) input.focus(); }
    };
  }

  window.OpsChat = { mount: mount };

  /* ── the full page ─────────────────────────────────────────── */
  var pageRoot = document.querySelector('[data-ops-root="page"]');
  var pageCfg = document.getElementById("ops-config");
  if (pageRoot && pageCfg) {
    mount(pageRoot, JSON.parse(pageCfg.textContent || "{}"),
          { secretModal: "ops-secret-modal", storageKey: pageRoot.getAttribute("data-ops-key") });
  }

  /* ── the floating panel (OpsWidget) ─────────────────────────
     The bubble (#ops-fab, admin/_admin_layout.html) loads this file + css on its first
     click, then calls OpsWidget.init(fab) and OpsWidget.toggle(). The panel fragment
     (GET …/ops-assistant/widget, same gate as the page) is fetched once. */
  var W = { fab: null, panel: null, chat: null, loading: false, queued: null };

  function fabKey(name) {
    return "hr_ops_w_" + name + ":" + ((W.fab && W.fab.getAttribute("data-key")) || "");
  }
  function isMobile() {
    return !!(window.matchMedia && window.matchMedia("(max-width: 640px)").matches);
  }

  function setUnread(on) {
    if (!W.fab) return;
    W.fab.classList.toggle("has-unread", !!on);
    storeSet(fabKey("unread"), on ? "1" : null);
  }

  function ensurePanel(cb) {
    if (W.panel) { cb(); return; }
    W.queued = cb;
    if (W.loading) return;
    W.loading = true;
    W.fab.classList.add("is-loading");
    fetch(W.fab.getAttribute("data-panel"),
          { credentials: "same-origin", headers: { "Accept": "text/html" } })
      .then(function (r) {
        if (!r.ok) throw new Error("panel " + r.status);
        return r.text();
      })
      .then(function (html) {
        /* our own server-rendered fragment (static markup + JSON config, never model
           text); parsed inert by DOMParser (no script runs), then adopted */
        var doc = new DOMParser().parseFromString(html, "text/html");
        var box = el("div");
        box.id = "ops-fab-root";
        while (doc.body.firstChild) box.appendChild(document.adoptNode(doc.body.firstChild));
        document.body.appendChild(box);
        W.panel = box.querySelector(".ops-fab-panel");
        var cfgEl = W.panel.querySelector('[data-ops="config"]');
        W.chat = mount(W.panel, JSON.parse((cfgEl && cfgEl.textContent) || "{}"), {
          secretModal: "ops-w-secret-modal",
          storageKey: W.fab.getAttribute("data-key"),
          onActivity: function () { if (W.panel.hidden) setUnread(true); }
        });
        var go = W.queued;
        W.queued = null;
        if (go) go();
      })
      .catch(function () {
        W.queued = null;
        var msg = W.fab.getAttribute("data-error") || "";
        if (msg && window.UDS && window.UDS.toast) window.UDS.toast(msg);
      })
      .then(function () { W.loading = false; W.fab.classList.remove("is-loading"); });
  }

  function open() {
    ensurePanel(function () {
      W.panel.hidden = false;
      W.fab.setAttribute("aria-expanded", "true");
      W.fab.classList.add("is-open");
      document.documentElement.classList.toggle("ops-fab-lock", isMobile());
      setUnread(false);
      storeSet(fabKey("open"), "1");
      W.chat.scroll();
      if (!isMobile()) W.chat.focus();
    });
  }

  function hide() {
    if (W.panel) W.panel.hidden = true;
    W.fab.setAttribute("aria-expanded", "false");
    W.fab.classList.remove("is-open");
    document.documentElement.classList.remove("ops-fab-lock");
    storeSet(fabKey("open"), null);
  }

  function toggle() {
    if (W.panel && !W.panel.hidden) hide(); else open();
  }

  /* delegated on document: AJAX page swaps never kill these */
  document.addEventListener("click", function (e) {
    var b = e.target.closest ? e.target.closest("[data-ops-w]") : null;
    if (!b || !W.panel || !W.panel.contains(b)) return;
    var act = b.getAttribute("data-ops-w");
    if (act === "min") { e.preventDefault(); hide(); W.fab.focus(); }
    else if (act === "close") { e.preventDefault(); W.chat.reset(); hide(); W.fab.focus(); }
  });
  document.addEventListener("keydown", function (e) {
    if (e.key !== "Escape" || !W.panel || W.panel.hidden) return;
    if (document.querySelector(".uds-modal:not([hidden])")) return;   // the modal closes first
    hide();
    W.fab.focus();
  });

  window.OpsWidget = {
    init: function (fab) {
      if (W.fab) return;
      W.fab = fab;
      if (storeGet(fabKey("unread")) === "1") fab.classList.add("has-unread");
    },
    open: open,
    hide: hide,
    toggle: toggle
  };
})();
