# Services Cookbook — Canonical Service Reference

> **Status: CANONICAL.** This document is the **single source of truth** for
> every operator-facing service in the project — today and going forward.
> Every pattern below is **production-tested** and matches what's live in
> the codebase. When you need to add the same action to another page
> (subscribers, sessions, etc.), copy from here — do not re-invent.
>
> ## 🔒 Maintenance protocol (READ FIRST)
>
> 1. **Before building or editing any service-bearing page**, read the
>    relevant section here FIRST. Copy the canonical pattern — never
>    re-derive a pattern that already exists in this file.
>
> 2. **Whenever a new service / operation is added** to ANY page, append
>    a new section to this file in the SAME commit that ships the code.
>    Follow the 9-field template (see "How to use" below). The cookbook
>    is a living document; if it goes out of sync with the code it loses
>    its value.
>
> 3. **When you discover a gotcha** (the kind that took an iteration to
>    fix), add a one-liner row to **§E. Common gotchas** so the next
>    person doesn't repeat the mistake.
>
> 4. **When code and cookbook disagree**, the cookbook is what we
>    *intend*. Either fix the code to match, or update the cookbook in
>    the same commit. Never let drift persist.
>
> 5. **Commit messages** that touch services should reference the
>    cookbook section, e.g.:
>    > `Subscribers — add per-user disconnect (see SERVICES_COOKBOOK §1)`
>
> Why this rule exists: it took many hours of trial-and-error to learn
> the right way to do per-session disconnect / scoped radacct close /
> can_disconnect derivation / multi-MAC enforcement / etc. The cookbook
> captures all of it so we never repeat that pain.
>
> ## How to use
>
> Each service section follows the same template:
>
> **How to use.** Each service section follows the same template:
>
> 1. **What it does** (1-line).
> 2. **Route + action key** (the URL + the `op=` form value).
> 3. **Service method** (the canonical entry point on `CardsService`).
> 4. **Adapter helper** (CoA layer / DB layer, where relevant).
> 5. **Template button** (the exact HTML snippet, copy-pasteable).
> 6. **JS pattern** (modal call, picker, etc.).
> 7. **Flash message format** (what the route emits on success/error).
> 8. **Edge cases** (gotchas we discovered the hard way).
> 9. **Reusable in** (which other pages should adopt this).
>
> All snippets use the `hub-v2` design system. Backend code is Python 3.11+
> with `dataclasses` + Flask. Frontend JS is vanilla — no framework.
>
> ---
>
> **Where this file lives:** repo root (`/SERVICES_COOKBOOK.md`).
> **Companion files:**
> - `DESIGN_SYSTEM.md` — visual identity rules (colors, spacing, typography)
> - `SURVEY.md` — page-level inventory + priority tiers
> - `app/static/css/hub_v2.css` + `app/templates/_partials/hub.html` — the
>   actual design system components referenced throughout this cookbook

---

## Table of contents

| # | Service | Type | Key file(s) |
|---|---|---|---|
| 1 | [Disconnect (per-session picker)](#1-disconnect-per-session-picker) | CoA-Disconnect | `radius_coa.disconnect_user` |
| 2 | [Lock MAC (multi-select + enforce)](#2-lock-mac-multi-select--enforce) | DB + CoA enforce | `CardsService.lock_card_mac` |
| 3 | [Manage MAC lock (edit vs full unlock)](#3-manage-mac-lock-edit-vs-full-unlock) | DB | `lock_card_mac` / `unlock_card_mac` |
| 4 | [Speed change (CoA Rate-Limit)](#4-speed-change-coa-rate-limit) | DB + CoA push | `CardsService.set_card_speed` |
| 5 | [Time adjust (CoA Session-Timeout)](#5-time-adjust-coa-session-timeout) | DB + CoA push | `CardsService.adjust_card_time` |
| 6 | [Disable / Freeze (snapshot + kick)](#6-disable--freeze-snapshot--kick) | DB + CoA broadcast | `CardsService.disable_card` |
| 7 | [Enable (restore from snapshot)](#7-enable-restore-from-snapshot) | DB | `CardsService.enable_card` |
| 8 | [Reset usage](#8-reset-usage) | DB | `CardsService.reset_card_usage` |
| 9 | [Soft delete (recycle bin)](#9-soft-delete-recycle-bin) | DB | `CardsService.soft_delete_card` |
| 10 | [Permanent delete](#10-permanent-delete) | DB | `CardsService.delete_card_permanently` |
| 11 | [Reveal password (audited)](#11-reveal-password-audited) | Separate endpoint | `cards_checker_api_reveal_password` |
| 12 | [DHCP sync (on-demand)](#12-dhcp-sync-on-demand) | MT pull | `device_fingerprint_sync.sync_tenant` |
| 13 | [Sessions table — pagination + auto-refresh](#13-sessions-table--pagination--auto-refresh) | UI only | JS in template |
| A | [Foundations — ccModal API](#a-foundations--ccmodal-api) | Infrastructure | template |
| B | [Foundations — CoA layer contracts](#b-foundations--coa-layer-contracts) | Infrastructure | `radius_coa.py` |
| C | [Foundations — derived state (`can_disconnect`)](#c-foundations--derived-state-can_disconnect) | Service | `card_checker.py` |

---

## 1. Disconnect (per-session picker)

**What it does.** Kicks one, many, or all active devices for a card via
RFC-5176 CoA-Disconnect, then closes the corresponding `radacct` rows.
The operator picks specific sessions; other active sessions stay
connected.

### 1.1 Route + action key

```python
# app/radius/routes/cards.py
elif action == "disconnect":
    ids_raw = _form_str("session_ids")              # CSV from picker
    ids = [s.strip() for s in ids_raw.split(",") if s.strip()] if ids_raw else None
    svc.disconnect_card(
        actor=_actor(),
        username=username,
        session_id=_form_str("session_id"),         # legacy single-id
        session_ids=ids,                            # new multi-id
    )
```

Form action: `POST /admin/radius/cards/checker`, `op=disconnect`.

### 1.2 Service method

```python
# app/radius/services/cards.py
def disconnect_card(self, *, actor: str, username: str,
                      session_id: str = "",
                      session_ids: list[str] | None = None) -> None:
    """Selection rules (most-specific wins):
       • session_ids non-empty → kick exactly those.
       • session_id given      → kick that single one (legacy path).
       • neither               → kick every active session ('all').
    """
    ids = list(session_ids) if session_ids else (
        [session_id] if session_id else None
    )
    self._adapter.disconnect(username, session_ids=ids)
    self._audit.record(actor=actor, action="card.disconnect",
                       target_type="card", target_id=username,
                       payload={"session_ids": ids or "all",
                                "count": len(ids) if ids else None})
```

### 1.3 Adapter helper

```python
# app/radius/integration/sqlite_adapter.py — SqliteAdapter.disconnect
def disconnect(self, username: str, *,
                session_id: Optional[str] = None,
                session_ids: Optional[list[str]] = None) -> None:
    ids = list(session_ids) if session_ids else (
        [session_id] if session_id else None
    )
    res = disconnect_user(_tid(), username, session_ids=ids)
    if not res.ok:
        raise RadiusError(res.reply_message or f"تعذّر قطع {username}")
    # Close ONLY the kicked rows in radacct (scoped close):
    with transaction() as c:
        if ids:
            placeholders = ",".join("?" for _ in ids)
            c.execute(
                f"UPDATE radacct SET acctstoptime=datetime('now'), "
                f"  acctterminatecause='Admin-Reset' "
                f"WHERE tenant_id=? AND username=? "
                f"  AND acctstoptime IS NULL "
                f"  AND acctsessionid IN ({placeholders})",
                (_tid(), username, *ids),
            )
        else:
            c.execute(
                "UPDATE radacct SET acctstoptime=datetime('now'), "
                "  acctterminatecause='Admin-Reset' "
                "WHERE tenant_id=? AND username=? AND acctstoptime IS NULL",
                (_tid(), username),
            )
```

### 1.4 Template button

```html
{# 1. Build the active-sessions payload ONCE at the top of the page
   so simple+advanced mode buttons share it. #}
{% set _active_sessions = [] %}
{% for ses in (s.latest_sessions or []) if ses.online %}
  {% set _ = _active_sessions.append({
    'id':       ses.session_id or (ses.id|string),
    'mac':      ses.mac_address or '',
    'ip':       ses.ip_address or '',
    'duration': ses.duration_seconds or 0,
    'started':  ses.started_at or '',
    'device':   (ses.dhcp_device.label if ses.dhcp_device and ses.dhcp_device.label else ''),
  }) %}
{% endfor %}

{# 2. The button — picker fires from its data-cc-active-sessions attribute. #}
<form method="post" action="{{ url_for('radius.cards_checker') }}"
      data-cc-form="disconnect">
  <input type="hidden" name="op" value="disconnect">
  <input type="hidden" name="session_ids" data-cc-disc-field>
  {{ hidden_ops(card, query) }}
  <button class="hub-action" type="button"
          {% if not ops.can_disconnect %}disabled{% endif %}
          data-cc-op="disconnect"
          data-cc-active-sessions="{{ _active_sessions | tojson }}">
    <span class="hub-action-icon hub-action-icon--amber"><i class="fa-solid fa-plug-circle-xmark"></i></span>
    <div class="hub-action-body">
      <div class="hub-action-title">قطع الجلسة</div>
      <div class="hub-action-sub">
        {% if _active_sessions %}{{ _active_sessions|length }} جهاز متّصل الآن
        {% else %}لا أجهزة الآن{% endif %}
      </div>
    </div>
  </button>
</form>
```

### 1.5 JS pattern

```js
// Bootstrap once per page. querySelectorAll → handles simple+advanced both.
(function(){
  'use strict';
  var btns = document.querySelectorAll(
    '[data-cc-op="disconnect"][data-cc-active-sessions]'
  );
  if (!btns.length) return;

  function fromAttribute(btn){
    try {
      var s = JSON.parse(btn.getAttribute('data-cc-active-sessions') || '[]');
      return Array.isArray(s) ? s : [];
    } catch(_){ return []; }
  }
  function fromDom(){    // fallback when attr empty (stale cache)
    var rows = document.querySelectorAll('.cc-sessions-table tr.is-live[data-cc-session]');
    var out = [];
    rows.forEach(function(tr){
      var cells = tr.querySelectorAll('td');
      out.push({
        id:  tr.getAttribute('data-cc-session') || '',
        mac: (cells[2]?.textContent || '').trim(),
        ip:  (cells[1]?.textContent || '').trim(),
        duration: 0, started: '', device: '',
      });
    });
    return out.filter(function(s){ return s.id; });
  }

  btns.forEach(function(btn){
    btn.addEventListener('click', function(){
      if (!window.ccModal || btn.disabled) return;
      var sessions = fromAttribute(btn);
      if (!sessions.length) sessions = fromDom();
      if (!sessions.length){ alert('لا أجهزة متّصلة الآن.'); return; }
      window.ccModal.sessionsPicker({
        title: 'قطع الجلسة',
        sessions: sessions,
        onConfirm: function(idsCsv){
          var f = btn.closest('form');
          if (!f) return;
          f.querySelector('[data-cc-disc-field]').value = idsCsv;
          f.submit();
        },
      });
    });
  });
})();
```

### 1.6 Flash message

- ≥1 id selected → `تم إرسال أمر قطع لـ N جلسة [مختارة]` (warning)
- empty list → `تم إرسال أمر قطع لكل الجلسات النشطة.` (warning)

### 1.7 Edge cases & gotchas

1. **`data-cc-session` must carry `acctsessionid`, NOT radacct row id**.
   The CoA filter compares against `acctsessionid`; mismatched ids → `no_active_session`.
   Template fragment: `<tr data-cc-session="{{ ses.session_id or ses.id }}">`.
2. **Scoped close, not broadcast.** Closing all rows for a username
   after kicking one session breaks multi-device cards. The adapter's
   `UPDATE … AND acctsessionid IN (…)` is mandatory.
3. **`can_disconnect`** must be derived from `online_sessions` count,
   not from the latest radacct row (see Foundations §C).
4. **DOM fallback** is real-world necessary — stale page caches can
   serve an empty `data-cc-active-sessions` even when sessions exist.

### 1.8 Reusable in

- Subscribers page (per-user disconnect)
- `/admin/radius/online` (per-row kick)
- Anywhere with a `username` and known active sessions.

---

## 2. Lock MAC (multi-select + enforce)

**What it does.** Locks the card to 1+ allowed MACs (CSV in DB), then
**immediately kicks** any active session whose MAC is outside that list.

### 2.1 Route + action key

```python
elif action == "lock_mac":
    res = svc.lock_card_mac(
        actor=_actor(), card_id=card_id, mac=_form_str("mac"),
    ) or {}
    macs_locked = res.get("macs") or []
    kicked = len(res.get("kicked") or [])
    kept = res.get("kept") or 0
```

### 2.2 Service method

```python
# app/radius/services/cards.py
def lock_card_mac(self, *, actor: str, card_id: int, mac: str) -> dict:
    """Lock to one or more MACs, then enforce by kicking offenders."""
    # 1. Parse + validate (accepts comma/semicolon/newline separators)
    raw = (mac or "").replace(";", ",").replace("\n", ",")
    macs = sorted({m.strip().upper().replace("-", ":")
                   for m in raw.split(",") if m.strip()})
    if not macs:
        raise RadiusValidationError("MAC مطلوب")
    for m in macs:
        hex_only = m.replace(":", "")
        if len(hex_only) != 12 or any(c not in "0123456789ABCDEF" for c in hex_only):
            raise RadiusValidationError(f"عنوان MAC غير صالح: {m}")

    # 2. Persist
    tenant_id = self._store_tenant_id()
    joined = ",".join(macs)
    if not cards_repo.set_card_locked_mac(tenant_id, card_id, joined, actor=actor):
        raise RadiusValidationError("تعذر تثبيت MAC")

    # 3. Enforce — kick offenders
    kicked: list[str] = []
    kept = 0
    try:
        card = cards_repo.get_card(tenant_id, card_id)
        username = getattr(card, "username", None)
        if username:
            allowed = {m.upper() for m in macs}
            rows = cards_repo.list_card_accounting(tenant_id, username, limit=100)
            offenders = []
            for row in rows:
                if row.get("acctstoptime"):
                    continue
                sess_mac = (row.get("callingstationid") or "").strip().upper()
                sid = row.get("acctsessionid") or ""
                if not sid:
                    continue
                if sess_mac and sess_mac in allowed:
                    kept += 1
                else:
                    offenders.append(sid)
            if offenders:
                try:
                    self._adapter.disconnect(username, session_ids=offenders)
                    kicked.extend(offenders)
                except TypeError:
                    self._adapter.disconnect(username)  # legacy fallback
                    kicked.extend(offenders)
    except Exception:
        logging.getLogger(__name__).warning(
            "lock_card_mac enforcement failed", exc_info=True)

    self._audit.record(actor=actor, action="card.lock_mac",
                       target_type="card", target_id=str(card_id),
                       payload={"macs": macs, "count": len(macs),
                                "kicked_count": len(kicked),
                                "kept_count":   kept})
    return {"macs": macs, "kicked": kicked, "kept": kept}
```

### 2.3 Template button (lock flow — when not yet locked)

```html
{# Build a meta map { MAC_UPPER: 'Redmi-Note-12-Pro · Android 11' }
   so the picker shows device labels next to each MAC. #}
{% set _mac_meta = {} %}
{% for _m in (s.macs or []) %}
  {% if _m.mac and _m.dhcp_device and _m.dhcp_device.label %}
    {% set _ = _mac_meta.update({_m.mac.upper(): _m.dhcp_device.label}) %}
  {% endif %}
{% endfor %}

<form method="post" action="{{ url_for('radius.cards_checker') }}" data-cc-form="lock-mac">
  <input type="hidden" name="op" value="lock_mac">
  <input type="hidden" name="mac" data-cc-prompt-field>
  {{ hidden_ops(card, query) }}
  <button class="hub-action" type="button"
          data-cc-op="lock-mac"
          data-cc-current-mac="{{ card.mac_address or '' }}"
          data-cc-recent-macs="{{ ((s.macs or []) | map(attribute='mac') | select | list) | tojson }}"
          data-cc-recent-macs-meta="{{ _mac_meta | tojson }}">
    <span class="hub-action-icon hub-action-icon--brand"><i class="fa-solid fa-lock"></i></span>
    <div class="hub-action-body">
      <div class="hub-action-title">قفل MAC</div>
      <div class="hub-action-sub">تثبيت على عنوان واحد أو أكثر</div>
    </div>
  </button>
</form>
```

### 2.4 JS pattern

```js
(function(){
  'use strict';
  var btn = document.querySelector('[data-cc-op="lock-mac"][data-cc-recent-macs]');
  if (!btn) return;
  btn.addEventListener('click', function(){
    var recent = JSON.parse(btn.getAttribute('data-cc-recent-macs') || '[]');
    var meta   = JSON.parse(btn.getAttribute('data-cc-recent-macs-meta') || '{}');
    // DOM fallback when the dedicated MAC query came back empty
    if (!recent.length){
      var seen = {};
      document.querySelectorAll('.cc-sessions-table tbody tr td:nth-child(3)').forEach(function(td){
        var v = (td.textContent || '').trim().toUpperCase();
        if (v && v !== '—' && !seen[v]){ seen[v]=true; recent.push(v); }
      });
    }
    window.ccModal.macPicker({
      title: 'قفل MAC',
      currentMac:     btn.getAttribute('data-cc-current-mac') || '',
      recentMacs:     recent,
      recentMacsMeta: meta,
      onConfirm: function(macCsv){
        var f = btn.closest('form');
        f.querySelector('[data-cc-prompt-field]').value = macCsv;
        f.submit();
      },
    });
  });
})();
```

### 2.5 Flash message

```
kicked > 0:  تم تثبيت N عنوان MAC، وقطع K جلسة لأجهزة غير مطابقة. M جلسة مطابقة بقيت متّصلة.
kicked = 0:  تم تثبيت N عنوان MAC على البطاقة. [M جلسة نشطة كانت مطابقة بالفعل.]
```

### 2.6 Edge cases

1. **Multi-MAC storage format:** CSV in `cards.locked_mac` column.
   FreeRADIUS-side: regex match in `radcheck` via
   `Calling-Station-Id =~ "^(AA:..|BB:..)$"` — see
   `freeradius_translator._mac_lock_regex`.
2. **Case insensitivity:** Always `.upper()` both stored and incoming
   MACs. `list_card_macs` SQL uses `UPPER(callingstationid)` for the
   distinct grouping.
3. **Empty CSV → error.** The picker JS validates 12-hex-char MACs
   before submit, so backend should never see junk — but the service
   re-validates anyway.

### 2.7 Reusable in

- Subscribers form (lock subscriber to specific devices)
- Bandwidth schedules (target by MAC list)

---

## 3. Manage MAC lock (edit vs full unlock)

**What it does.** When a card is already locked, the operator gets
a chooser modal: edit the locked-MAC list (re-opens the picker with
current MACs pre-checked) OR fully unlock the card.

### 3.1 Route + action key

Two actions share this flow:
- `op=lock_mac` (re-uses §2) for the edit path
- `op=unlock_mac` for the full-unlock path:

```python
elif action == "unlock_mac":
    svc.unlock_card_mac(actor=_actor(), card_id=card_id)
    flash("تم إلغاء تثبيت MAC عن البطاقة.", "success")
```

```python
def unlock_card_mac(self, *, actor: str, card_id: int) -> None:
    if not cards_repo.set_card_locked_mac(self._store_tenant_id(), card_id, "", actor=actor):
        raise RadiusValidationError("تعذر إلغاء تثبيت MAC")
    self._audit.record(actor=actor, action="card.unlock_mac",
                       target_type="card", target_id=str(card_id))
```

### 3.2 Template (when card.locked_mac is set)

```html
{# Build meta + locked + union sets. _picker_macs is union of
   locked + recent session MACs so locked MACs always show. #}
{% set _locked_macs = (card.locked_mac or '')|trim
                        |replace(';', ',')|replace(' ', '')|split(',') %}
{% set _locked_macs_clean = [] %}
{% for _lm in _locked_macs %}
  {% if _lm %}{% set _ = _locked_macs_clean.append(_lm.upper()) %}{% endif %}
{% endfor %}
{% set _recent_session_macs = (s.macs or []) | map(attribute='mac') | select | list %}
{% set _picker_macs = (_locked_macs_clean + _recent_session_macs) | unique | list %}

<form method="post" action="{{ url_for('radius.cards_checker') }}"
      data-cc-form="unlock-mac" id="cc-unlock-mac-form">
  <input type="hidden" name="op" value="unlock_mac">
  {{ hidden_ops(card, query) }}
</form>
<form method="post" action="{{ url_for('radius.cards_checker') }}"
      data-cc-form="edit-lock-mac" id="cc-edit-lock-mac-form">
  <input type="hidden" name="op" value="lock_mac">
  <input type="hidden" name="mac" data-cc-edit-mac-field>
  {{ hidden_ops(card, query) }}
</form>

<button class="hub-action" type="button"
        data-cc-op="manage-lock-mac"
        data-cc-locked-macs="{{ _locked_macs_clean | tojson }}"
        data-cc-recent-macs="{{ _picker_macs | tojson }}"
        data-cc-recent-macs-meta="{{ _mac_meta | tojson }}">
  <span class="hub-action-icon hub-action-icon--brand"><i class="fa-solid fa-lock-open"></i></span>
  <div class="hub-action-body">
    <div class="hub-action-title">إدارة قفل MAC</div>
    <div class="hub-action-sub">{{ card.locked_mac }}</div>
  </div>
</button>
```

### 3.3 JS pattern — chooser modal that opens picker OR unlocks

```js
(function(){
  'use strict';
  var btn = document.querySelector('[data-cc-op="manage-lock-mac"]');
  if (!btn) return;
  btn.addEventListener('click', function(){
    var locked = JSON.parse(btn.getAttribute('data-cc-locked-macs') || '[]');
    var recent = JSON.parse(btn.getAttribute('data-cc-recent-macs') || '[]');
    var meta   = JSON.parse(btn.getAttribute('data-cc-recent-macs-meta') || '{}');

    window.ccModal.confirm({
      icon: 'info', title: 'إدارة قفل MAC',
      body: 'البطاقة مقفولة حالياً على <strong>' + locked.length + ' عنوان</strong>.',
      confirmText: 'تعديل القائمة',
      cancelText:  'إغلاق',
      onConfirm: function(){
        // Open picker pre-populated with locked + recent
        window.ccModal.macPicker({
          title:          'تعديل قفل MAC',
          recentMacs:     recent,
          recentMacsMeta: meta,
          checkedMacs:    locked,                  // PRE-CHECK the locked ones
          lockedMacLabel: 'مقفول حاليًا',
          onConfirm: function(macCsv){
            var f = document.getElementById('cc-edit-lock-mac-form');
            f.querySelector('[data-cc-edit-mac-field]').value = macCsv;
            f.submit();
          },
        });
      },
    });
    // Inject a third "فك القفل تماماً" button into the modal's actions row
    setTimeout(function(){
      var actions = document.querySelector('.cc-gmodal-actions');
      if (!actions || actions.querySelector('[data-cc-full-unlock]')) return;
      var btn3 = document.createElement('button');
      btn3.type = 'button';
      btn3.className = 'cc-gmodal-btn cc-gmodal-btn-confirm danger';
      btn3.setAttribute('data-cc-full-unlock', '1');
      btn3.innerHTML = '<i class="fa-solid fa-lock-open"></i> فك القفل تماماً';
      btn3.addEventListener('click', function(){
        document.getElementById('cc-unlock-mac-form').submit();
      });
      actions.appendChild(btn3);
    }, 30);
  });
})();
```

### 3.4 Edge cases

1. **Pre-checked items in picker:** macPicker accepts a `checkedMacs`
   array that marks rows as `checked + .is-active` in the UI.
2. **Union before passing:** Always merge `locked + recent_session_macs`
   so currently locked MACs show even if they're not in the session
   history.
3. **Two hidden forms:** One for unlock, one for re-lock — modal
   buttons just submit the right one.

---

## 4. Speed change (CoA Rate-Limit)

**What it does.** Persists a per-card Mikrotik-Rate-Limit override,
re-syncs FreeRADIUS `radreply`, and pushes CoA-Request to live MT
sessions so the new rate takes effect without disconnect.

### 4.1 Route + action key

```python
elif action == "set_speed":
    down = _form_int("speed_down_kbps")
    up   = _form_int("speed_up_kbps")
    if down < 0 or up < 0:
        flash("قيم السرعة يجب ألا تكون سالبة.", "error")
    else:
        result = svc.set_card_speed(
            actor=_actor(), card_id=card_id,
            down_kbps=down, up_kbps=up, username=username,
        )
        # ... flash with CoA result note
```

### 4.2 Service method

Full body in `app/radius/services/cards.py:501-596`. Three steps:

1. **DB persist** via `cards_repo.set_card_speed_override`.
2. **FreeRADIUS re-sync** via `freeradius_translator.sync_subscriber`
   so `radreply` carries the new value at next Access-Request.
3. **CoA push** via `self._adapter.push_rate_limit` — best-effort,
   never raises on no-active-session.

```python
# CoA-Request format for MT: "{up}k/{down}k", e.g. "512k/2048k"
rate = (f"{up}k/{down}k" if not clearing else "")
coa_result = push_coa(username=username, new_rate_limit=rate)
```

### 4.3 Adapter helper

```python
# sqlite_adapter.py
def push_rate_limit(self, *, username: str, new_rate_limit: str):
    from .radius_coa import change_user_rate
    try:
        return change_user_rate(_tid(), username, new_rate_limit=new_rate_limit)
    except Exception as e:
        _LOG.warning("push_rate_limit error for %s: %s", username, e)
        return CoaResult(ok=False, code=0, code_name="exception",
                         reply_message=str(e))
```

### 4.4 Template + Modal

The button opens `cc-speed-modal` (custom floating modal with two
number inputs). Modal owns the form fields; on confirm it stamps the
hidden form and submits.

```html
<form method="post" action="{{ url_for('radius.cards_checker') }}" id="cc-speed-form">
  <input type="hidden" name="op" value="set_speed">
  <input type="hidden" name="speed_down_kbps" id="cc-speed-down-field">
  <input type="hidden" name="speed_up_kbps"   id="cc-speed-up-field">
  {{ hidden_ops(card, query) }}
  <button class="hub-action" type="button" data-cc-op="set-speed"
          onclick="window.__ccOpenSpeedModal && window.__ccOpenSpeedModal();">
    <span class="hub-action-icon hub-action-icon--green"><i class="fa-solid fa-gauge-high"></i></span>
    <div class="hub-action-body">
      <div class="hub-action-title">تغيير السرعة</div>
      <div class="hub-action-sub">للبطاقة فقط (يطبَّق فورًا)</div>
    </div>
  </button>
</form>
```

### 4.5 Flash message

```
success + CoA-ACK:   تم تعيين سرعة البطاقة: تنزيل D kbps / رفع U kbps — وصل التحديث للـ MikroTik (CoA-ACK).
no-active-session:   تم تعيين سرعة البطاقة: ... — لا جلسة نشطة، سيُطبَّق في الجلسة التالية.
CoA NAK / network:   تم تعيين سرعة البطاقة: ... — لم يصل التحديث الفوري للـ MikroTik (no_secret/timeout/…).
clearing (0,0):      تم إلغاء تخصيص السرعة على البطاقة — ترجع لسرعة الحزمة.{coa_note}
```

### 4.6 Edge cases

1. **Pass `0, 0` to CLEAR the override.** Mixing 0 + nonzero is rejected
   because MT requires both halves of `up/down` together.
2. **CoA failure must NOT roll back the DB write.** The DB persist is
   the source of truth; CoA is best-effort.
3. **Three layers in sequence:** DB → FreeRADIUS sync → CoA. Each can
   fail independently. Service surfaces all failures via
   `result["fr_synced"]` and `result["coa_result"]`.

### 4.7 Reusable in

- Subscribers form (`subscribers.bandwidth_control_enabled` + speed kbps)
- Bandwidth schedules (time-windowed overrides)

---

## 5. Time adjust (CoA Session-Timeout)

**What it does.** Shifts the card's `expire_at` by ±N seconds and
(best-effort) pushes CoA Session-Timeout so the new timeout takes
effect without disconnecting.

### 5.1 Route + action key

```python
elif action == "set_time":
    unit_map = {"minutes": 60, "hours": 3600, "days": 86400}
    amount = _form_int("time_amount")
    unit   = (_form_str("time_unit") or "").strip().lower()
    op     = (_form_str("time_op")   or "").strip().lower()
    if amount <= 0 or unit not in unit_map or op not in ("add", "subtract"):
        flash("بيانات التعديل غير مكتملة. حدّد المدّة والوحدة والعملية.", "error")
    else:
        delta = amount * unit_map[unit] * (-1 if op == "subtract" else 1)
        result = svc.adjust_card_time(
            actor=_actor(), card_id=card_id,
            delta_seconds=delta, username=username,
        )
```

### 5.2 Service method

```python
def adjust_card_time(self, *, actor: str, card_id: int,
                      delta_seconds: int, username: str = "") -> dict:
    if delta_seconds == 0:
        raise RadiusValidationError("لا يوجد تعديل لتطبيقه")
    tenant_id = self._store_tenant_id()
    result = cards_repo.adjust_card_expire_at(tenant_id, card_id, delta_seconds)
    if result is None:
        raise RadiusValidationError(
            "تعذر تعديل وقت البطاقة — تأكد أنها مفعّلة (لها وقت انتهاء)."
        )
    # Best-effort CoA push — Session-Timeout = remaining seconds
    coa_result = None
    try:
        push_coa = getattr(self._adapter, "push_session_timeout", None)
        if callable(push_coa) and username:
            coa_result = push_coa(username=username,
                                   session_timeout=result["remaining_seconds"])
    except Exception:
        logging.getLogger(__name__).warning(
            "CoA push_session_timeout failed", exc_info=True)
    self._audit.record(actor=actor, action="card.adjust_time",
                       target_type="card", target_id=str(card_id),
                       payload={"delta_seconds": delta_seconds, ...})
    return {**result, "coa_result": coa_result}
```

### 5.3 Adapter helper

```python
def push_session_timeout(self, *, username: str, session_timeout: int):
    from .radius_coa import change_user_session_timeout
    try:
        return change_user_session_timeout(
            _tid(), username, session_timeout=int(session_timeout))
    except Exception as e:
        _LOG.warning("push_session_timeout error: %s", e)
        return CoaResult(ok=False, code=0, code_name="exception",
                         reply_message=str(e))
```

### 5.4 Modal pattern

A custom `cc-time-modal` with op-pills (add/subtract) + unit-pills
(min/hr/day) + number input. Three hidden fields submit on confirm:
`time_amount`, `time_unit`, `time_op`.

### 5.5 Flash message

```
{op_label} {amount} {unit_label} من وقت البطاقة. المتبقي الآن: {h}س {m}د.{coa_note}
e.g.  تمت إضافة 30 دقيقة من وقت البطاقة. المتبقي الآن: 2 ساعة و 15 دقيقة. — وصل التحديث (CoA-ACK).
```

### 5.6 Reusable in

- Subscribers form (extend / reduce subscription)
- Vouchers (add bonus time)
- Tickets (auto-extend on resolution)

---

## 6. Disable / Freeze (snapshot + kick)

**What it does.** Disables a card AND freezes its remaining time
(snapshot of seconds-to-expire). When re-enabled, the same number of
seconds is restored from "now". Also broadcasts CoA-Disconnect to
every active session so already-online devices can't keep using the
network.

### 6.1 Route + action key

```python
elif action == "disable":
    res = svc.disable_card(
        actor=_actor(), card_id=card_id, reason=_form_str("reason"),
    )
    frozen = int((res or {}).get("frozen_remaining_seconds") or 0)
    suffix = " وتم قطع كل الجلسات النشطة."
    if frozen > 0:
        h, m = divmod(frozen // 60, 60)
        flash(f"تم تعطيل البطاقة وتجميد الوقت المتبقي ({h} ساعة و {m} دقيقة). "
              "سيعود نفس الوقت عند إعادة التفعيل." + suffix, "warning")
```

### 6.2 Service method

Three things happen atomically (from operator POV):

```python
def disable_card(self, *, actor: str, card_id: int, reason: str = "") -> dict:
    tenant_id = self._store_tenant_id()
    # 1. Snapshot remaining time + revoke
    result = cards_repo.freeze_card_time(
        tenant_id, card_id, actor=actor, reason=reason,
    )
    if result is None:
        raise RadiusValidationError("تعذر تعطيل البطاقة")
    # 2. Best-effort: kick every active session
    kicked = 0
    try:
        card = cards_repo.get_card(tenant_id, card_id)
        username = getattr(card, "username", None)
        if username:
            self._adapter.disconnect(username)   # broadcast — no session_ids
            kicked = -1   # -1 = "best-effort dispatched, no count"
    except Exception:
        logging.getLogger(__name__).warning(
            "disable_card: CoA kick failed", exc_info=True)
    result["kicked_sessions"] = kicked
    self._audit.record(actor=actor, action="card.disable", ...)
    return result
```

### 6.3 Why this design

| Concern | Why |
|---|---|
| Why snapshot time? | If we just disable, the real-world clock keeps burning the user's quota. Freeze ensures re-enable restores the same seconds. |
| Why kick on freeze? | A revoked card with a connected device is a contradiction — without CoA-Disconnect, MT keeps the session until its keepalive expires (~minutes). |
| Why best-effort? | CoA failure (router down, no_secret) must not roll back the freeze. Admin's intent is the contract. |

### 6.4 Reusable in

- Subscribers form (suspend account)
- Vouchers (revoke unused voucher)

---

## 7. Enable (restore from snapshot)

**What it does.** Re-enables a previously-frozen card and restores
its `expire_at` to `now + frozen_remaining_seconds`.

### 7.1 Service method

```python
def enable_card(self, *, actor: str, card_id: int) -> dict:
    tenant_id = self._store_tenant_id()
    result = cards_repo.thaw_card_time(tenant_id, card_id)
    if result is None:
        raise RadiusValidationError("تعذر تفعيل البطاقة")
    self._audit.record(actor=actor, action="card.enable", ...)
    return result   # contains restored_seconds + expire_at_new
```

### 7.2 Flash message

```
restored > 0:  تم تفعيل البطاقة. تمت استعادة الوقت المجمَّد ({h} ساعة و {m} دقيقة).
restored = 0:  تم تفعيل البطاقة.
```

---

## 8. Reset usage

**What it does.** Zeros out the card's consumption counters and the
"started" timestamp so the next session starts from a clean slate.

### 8.1 Service method

```python
def reset_card_usage(self, *, actor: str, card_id: int) -> None:
    if not cards_repo.reset_card_usage(self._store_tenant_id(), card_id):
        raise RadiusValidationError("تعذر تصفير استخدام البطاقة")
    self._audit.record(actor=actor, action="card.reset_usage",
                       target_type="card", target_id=str(card_id))
```

### 8.2 Confirm modal (no picker, just a confirm)

```js
window.ccModal.confirm({
  icon: 'warn', title: 'إعادة ضبط الاستخدام',
  body: 'تصفير الوقت المستهلَك لـ <strong>{{ card.username }}</strong>.',
  confirmText: 'إعادة الضبط',
  onConfirm: () => this.closest('form').submit()
});
```

---

## 9. Soft delete (recycle bin)

**What it does.** Moves the card to the recycle bin (`deleted_at`
stamped) — does NOT free the username. Reversible from the recycle
bin screen.

### 9.1 Service method

```python
def soft_delete_card(self, *, actor: str, card_id: int, reason: str = "") -> None:
    if not cards_repo.soft_delete_card(
        self._store_tenant_id(), card_id, actor=actor, reason=reason,
    ):
        raise RadiusValidationError("تعذر نقل البطاقة إلى سلة المحذوفات")
    self._audit.record(actor=actor, action="card.soft_delete",
                       target_type="card", target_id=str(card_id),
                       payload={"reason": reason})
```

### 9.2 Reason form (collects optional text)

```js
window.ccModal.softDelete({
  title: 'نقل البطاقة إلى المحذوفات',
  body:  'البطاقة <strong>{{ card.username }}</strong> سيتم نقلها إلى سلة المحذوفات ويمكنك استعادتها لاحقاً.',
  label: 'سبب الحذف (اختياري)',
  onConfirm: (reason) => {
    var f = this.closest('form');
    f.querySelector('[data-cc-delete-reason]').value = reason || '';
    f.submit();
  }
});
```

---

## 10. Permanent delete

**What it does.** Hard-deletes the card row. Only used from the
recycle bin screen — requires typing `DELETE` literally.

```python
elif action == "delete_permanent":
    if _form_str("confirm_delete") != "DELETE":
        flash("للحذف النهائي اكتب DELETE في خانة التأكيد.", "error")
    else:
        svc.delete_card_permanently(actor=_actor(), card_id=card_id)
        flash("تم حذف البطاقة نهائيًا. لا يظهر هذا الخيار في التشغيل اليومي إلا بحذر.", "warning")
```

---

## 11. Reveal password (audited)

**What it does.** On-demand reveal of the stored card password,
via a separate endpoint so the value never lives in the default
page payload.

### 11.1 Endpoint

```python
# POST /admin/radius/cards/checker/api/reveal-password
def cards_checker_api_reveal_password():
    card_id = _form_int("card_id")
    row = db().execute(
        "SELECT username, password FROM cards "
        "WHERE tenant_id = ? AND id = ? AND deleted_at IS NULL",
        (_tid(), card_id),
    ).fetchone()
    if row is None:
        return jsonify({"ok": False, "error": "البطاقة غير موجودة"}), 404
    if not row["password"]:
        return jsonify({"ok": False, "error": "هذه البطاقة بدون كلمة مرور"}), 404
    # Audit before returning the value
    get_audit_service().record(
        actor=_actor(), action="card.password_reveal",
        target_type="card", target_id=str(card_id),
        payload={"username": row["username"]},
    )
    return jsonify({"ok": True, "card_id": card_id, "password": row["password"]})
```

### 11.2 Why this design

- **Default Checker payload has `has_password: bool` only** — no plaintext.
- Reveal goes through a separate POST → audit row per reveal.
- Frontend auto-re-masks after 8s so a forgotten-open page doesn't leak.

### 11.3 JS pattern

```js
// On eye click: fetch reveal endpoint, swap masked → plain, start 8s timer.
fetch('{{ url_for("radius.cards_checker_api_reveal_password") }}', {
  method: 'POST',
  credentials: 'same-origin',
  headers: {'Content-Type': 'application/x-www-form-urlencoded',
            'X-CSRFToken': csrfToken},
  body: 'card_id=' + cardId + '&_csrf_token=' + csrfToken,
})
  .then(r => r.json())
  .then(j => {
    if (!j.ok) throw new Error(j.error);
    revealed = j.password;
    displayEl.textContent = revealed;
    // Auto re-mask after 8 seconds
    hideTimer = setTimeout(setMasked, 8000);
  });
```

### 11.4 Reusable in

- Subscribers form (same pattern for `subscribers.password`)
- Any other secret field that shouldn't sit in the default payload.

---

## 12. DHCP sync (on-demand)

**What it does.** Fire-and-forget pull of DHCP leases from every
enabled MT router for this tenant. Used by the "تحديث DHCP" button
when the operator doesn't want to wait the 120s background tick.

### 12.1 Route + action key

```python
elif action == "sync_dhcp":
    try:
        from ..services import device_fingerprint_sync
        seen = device_fingerprint_sync.sync_tenant(_tid())
        flash(f"تم تحديث بيانات DHCP من المايكروتيك — {seen} عنوان MAC.",
              "success")
    except Exception as e:
        flash(f"تعذّر التحديث الفوري للـ DHCP: {e}", "error")
```

### 12.2 Template button

```html
<form method="post" action="{{ url_for('radius.cards_checker') }}"
      style="display:inline-flex;margin-inline-start:auto">
  <input type="hidden" name="op" value="sync_dhcp">
  {{ hidden_ops(card, query) }}
  <button type="submit" class="cc-mini-action" title="تحديث بيانات DHCP الآن من المايكروتيك">
    <i class="fa-solid fa-arrows-rotate"></i>
    <span>تحديث DHCP</span>
  </button>
</form>
```

### 12.3 Reusable in

- Subscribers list (manual refresh of device column)
- Sessions list (refresh active device fingerprints)
- Cards-of-batch (verify devices for whole batch)

---

## 13. Sessions table — pagination + auto-refresh

**What it does.** Client-side pagination (10/25/50/100/all) + 5-second
auto-refresh via existing `/api/lookup` endpoint. Pauses while a modal
is open or the tab is hidden.

### 13.1 Foundation requirement

The page must have:
- A wrapper div: `data-cc-sessions-region data-cc-query="<card_username>"`
- A table: `class="cc-sessions-table"` with `<tr data-cc-session="<id>">`
- A footer with the page controls (see §13.2)

### 13.2 Footer HTML

```html
<div class="cc-sessions-foot" data-cc-sessions-foot>
  <div class="cc-sessions-foot-left">
    <label class="cc-sessions-foot-label">
      صفوف:
      <select data-cc-page-size class="cc-sessions-foot-select">
        <option value="10" selected>10</option>
        <option value="25">25</option>
        <option value="50">50</option>
        <option value="100">100</option>
        <option value="0">الكل</option>
      </select>
    </label>
    <span class="cc-sessions-foot-info" data-cc-page-info>—</span>
  </div>
  <div class="cc-sessions-foot-mid">
    <button type="button" class="cc-sessions-foot-nav" data-cc-page-prev>‹</button>
    <span class="cc-sessions-foot-page" data-cc-page-current>1 / 1</span>
    <button type="button" class="cc-sessions-foot-nav" data-cc-page-next>›</button>
  </div>
  <div class="cc-sessions-foot-right">
    <span class="cc-sessions-foot-stale" data-cc-refresh-stale>الآن</span>
    <label class="cc-sessions-foot-label cc-sessions-foot-toggle">
      <input type="checkbox" data-cc-autorefresh checked>
      <span>تحديث تلقائي</span>
    </label>
  </div>
</div>
```

### 13.3 JS pattern (full implementation in `cards_checker_v2.html`)

Key behaviors:
- **Pause when modal open** — checks `#cc-gmodal.is-open`, `#cc-time-modal.is-open`, `#cc-speed-modal.is-open`.
- **Pause when tab hidden** — `document.hidden`.
- **Stale indicator** — 3s green "محدّث الآن", 3-15s grey "تحديث منذ Xث", >15s amber "قديم — Xث".
- **Re-render via `buildRow(ses)`** — hand-built from the JSON, NOT a fragment fetch. Faster + simpler.

### 13.4 Reusable in

- Online users page (live polling of `/sessions/online`)
- Sync queue inspector (poll for new jobs)
- Audit log viewer (live tail)

---

## 14. Subscriber 360° profile page

**What it does.** One premium read-mostly page per subscriber:
hero (avatar + name + phone + status pill + quick actions), KPI strip
(speed up/down, used / quota / remaining, balance), then 10 tabs
(info / events / invoices / recharges / sessions / daily usage /
manager events / bandwidth / used cards / ledger).

**Pattern: the route owns DATA aggregation, not mutations.** All write
actions on the page (edit, finance, disable…) go to the existing routes
(`users_edit`, `users_finance`, `users_toggle`, etc.) — never invent a
new mutation route here.

### 14.1 Route + endpoint

```python
# app/radius/routes/users.py
bp.add_url_rule(
    "/users/<username>/profile", "users_profile",
    users_profile, methods=["GET"],
)
```

URL: `/admin/radius/users/<username>/profile`.

### 14.2 Data slices the view collects

| Slice | Source | Filter |
|---|---|---|
| Subscriber DTO | `subscribers_repo.get_subscriber(tid, username)` | 404 if missing |
| Plan | `plans_repo.get_plan(tid, sub.plan_id)` | optional |
| Sessions | `cards_repo.list_card_accounting(tid, username, limit=200)` | per-user |
| Audit events | `audit_repo.recent(tid, limit=500)` → in-memory filter | `target_type in ("subscriber","card") and target==username` |
| Invoices | `invoices_repo.list_all(tid, limit=200)` → filter | `subscriber_id == sub.id or username == username` |
| Used cards | direct SQL on `cards` table | `used_by_subscriber_id = sub.id` |
| Payments | `accounting_repo.list_payments(tid, subscriber_id=sub.id)` | per-user |
| Loans | `accounting_repo.list_loans(tid, subscriber_id=sub.id)` | per-user |
| KPI aggregate | direct SQL on `radacct`, `SUM(in+out) / SUM(time) / COUNT(*) / SUM(online)` | per-user |

The aggregate is one SQL hit, NOT a loop over `session_rows`. Loops
double the cost when the user has 100+ sessions.

### 14.3 Template structure (`radius/users_profile.html`)

Built on hub-v2 only — no card-checker classes:

1. **Hero** — custom `.p360-hero` (avatar + name + uname chip + status
   + "متصل الآن" pill if `online_now > 0`, plus quick-action buttons).
2. **KPI strip** — `hub.kpi` × 6 (speed-down, speed-up, quota, used,
   remaining, balance).
3. **Tab nav** — `.p360-tabs` with 10 buttons; one is `.is-active`.
4. **Tab panes** — `.p360-pane` siblings; only one has `.is-active`.
5. **Tiny JS** — toggles `.is-active` on tab+pane on click; also reads
   `location.hash` to deep-link a tab.

Formatting helpers are local Jinja macros at the top of the template
(`fmt_bytes_mb`, `fmt_speed`, `fmt_dur`, `status_pill`) — no global
Jinja filters needed.

### 14.4 Entry point

The username column in `users_list.html` links to
`url_for('radius.users_profile', username=u.username)`; row actions
also include an "id-badge" primary button. This is the canonical entry —
do NOT add a separate sidebar item (one row per subscriber, not a
global section).

### 14.5 Edge cases

- Subscriber missing → `abort(404)`.
- Each data slice is wrapped in `try/except` and falls back to `[]`/`{}`
  so a broken sub-repo never 500s the whole page.
- Quota fallback: subscriber override → plan quota → 0.
- `pct = used / quota * 100` guarded with `if quota_total_mb`.
- Hash-based deep-link uses `replaceState` so back-button still works.

### 14.6 Reusable in

- Distributor 360 (`distributors_detail.html` — same hero/KPI/tabs shape).
- Card-batch 360 (per-batch summary page).
- Plan 360 (per-plan stats: subscribers, revenue, churn).

The CSS namespace `.p360-*` is intentionally generic — any future "360"
page can adopt it.

---

## A. Foundations — ccModal API

A single `#cc-gmodal` element + a global `window.ccModal` namespace.
Five flavors:

| Method | Use case |
|---|---|
| `ccModal.confirm({title, body, confirmText, dangerous?, icon?, onConfirm})` | Simple yes/no |
| `ccModal.reasonForm({title, body, label, placeholder, confirmText, onConfirm(reason)})` | Need a one-line text input |
| `ccModal.softDelete({title, body, label, onConfirm(reason)})` | Pre-styled red soft-delete |
| `ccModal.macPicker({recentMacs, recentMacsMeta?, currentMac?, checkedMacs?, lockedMacLabel?, onConfirm(csv)})` | Multi-select MAC picker with optional pre-checks |
| `ccModal.sessionsPicker({sessions: [{id, mac, ip, duration, started, device?}], onConfirm(idsCsv)})` | Multi-select session picker |

All flavors:
- Auto-close on confirm or backdrop click or `Escape`.
- Lock body scroll while open.
- Mark `.is-open` so other code can detect (auto-refresh pauses).

**Adding a new flavor** = add a `function newPicker(opts){...}` inside
the closure that defines `box.innerHTML`, wires buttons, and calls
`show()`. Then expose via `window.ccModal.newPicker = newPicker;`.

---

## B. Foundations — CoA layer contracts

All CoA helpers live in `app/radius/integration/radius_coa.py`.
Standard return shape:

```python
@dataclass
class CoaResult:
    ok: bool
    code: int                # 41/42/43/44/45 per RFC 5176
    code_name: str           # 'CoA-ACK', 'no_active_session', 'missing_nas_secret', 'timeout', 'exception'
    reply_message: str = ""
```

**Three high-level helpers** (always return `CoaResult`, never raise):

```python
def disconnect_user(tenant_id: int, username: str, *,
                     session_ids: list[str] | None = None) -> CoaResult:
    """Empty session_ids → kick all. Non-empty → kick those only."""

def change_user_rate(tenant_id: int, username: str, *,
                      new_rate_limit: str) -> CoaResult:
    """Empty rate → skip (empty_rate code). Otherwise broadcasts."""

def change_user_session_timeout(tenant_id: int, username: str, *,
                                 session_timeout: int) -> CoaResult:
    """seconds; broadcasts to every active session."""
```

**Multi-session safety:** `disconnect_user` filters by `acctsessionid`
when `session_ids` is given. The rate-change and timeout-change helpers
broadcast (because the change is global to the card anyway). All three
return a `_broadcast`-collapsed `CoaResult` that summarizes the per-session
outcomes into one code (`all_ok` / `partial` / `all_failed` /
`no_active_session`).

---

## C. Foundations — derived state (`can_disconnect`)

The `operations.can_*` dict drives button enabled/disabled state in
the template. Derive from aggregate state, NOT from the latest single
row.

```python
# app/radius/services/card_checker.py
"operations": {
    # WRONG: bool((acct or {}).get("acctstoptime") is None and acct)
    # — after kicking one of 3 sessions, the kicked row becomes the
    #   newest, so 'latest_row.acctstoptime IS NOT NULL' → button greys
    #   out even though 2 devices are still online.
    "can_disconnect": int(accounting_summary.get("online_sessions") or 0) > 0,
    "can_lock_mac":   bool(mac_address),
    "can_reset_usage": True,
    "can_disable":   not bool(record.get("card_revoked")),
    "can_enable":     bool(record.get("card_revoked")),
    "can_delete_permanently": True,
},
```

**Rule of thumb:** for any "can do X to any active thing" predicate,
compute it from a COUNT, not from the latest row.

---

## D. Boilerplate — adding a new card-level operation

Follow this checklist to add a new operation that the operator can
trigger from the Card Checker:

1. **Backend service method** in `CardsService`:
   ```python
   def my_new_op(self, *, actor: str, card_id: int, **params) -> dict:
       # 1. Validate inputs
       # 2. DB write
       # 3. Best-effort side effects (CoA / sync / webhook)
       # 4. Audit
       # 5. Return dict the route can inspect
   ```

2. **Route handler branch** in `_handle_card_operation()`:
   ```python
   elif action == "my_new_op":
       res = svc.my_new_op(actor=_actor(), card_id=card_id, ...)
       flash("...", "success")
   ```

3. **Template button** in the `cc-actions` grid:
   ```html
   <form method="post" action="{{ url_for('radius.cards_checker') }}">
     <input type="hidden" name="op" value="my_new_op">
     {{ hidden_ops(card, query) }}
     <button class="hub-action" type="button" data-cc-op="my-new-op"
             onclick="window.ccModal && window.ccModal.confirm({
               icon:'warn', title:'...', body:'...',
               confirmText:'تأكيد',
               onConfirm: () => this.closest('form').submit()
             });">
       <span class="hub-action-icon hub-action-icon--brand"><i class="fa-solid fa-..."></i></span>
       <div class="hub-action-body">
         <div class="hub-action-title">…</div>
         <div class="hub-action-sub">…</div>
       </div>
     </button>
   </form>
   ```

4. **If it needs a picker:** define a new `ccModal.xPicker()` flavor
   in the modal script block. Wire via `data-cc-*` attributes (NEVER
   inline onclick with JSON — escaping nightmare).

5. **If it touches active sessions:** Use `session_ids` parameter
   pattern (see §1). Scope the radacct close to those IDs.

6. **Audit row format:**
   ```python
   self._audit.record(
       actor=actor, action="card.my_new_op",
       target_type="card", target_id=str(card_id),
       payload={"key": "value"},
   )
   ```

7. **Flash message:** success in `success`, warning when something
   destructive happened, error on validation failure. Include the
   CoA-result note when relevant (`— وصل التحديث (CoA-ACK).` etc).

---

## E. Common gotchas (one-liners)

| Gotcha | Fix |
|---|---|
| `data-cc-session` exposes radacct row id, but CoA filters by acctsessionid | Template: `data-cc-session="{{ ses.session_id or ses.id }}"` |
| Scoped close UPDATE catches no rows | acctsessionid mismatch — check the IDs you're passing in `IN (...)` are the same ones returned by `find_all_nas_for_sessions` |
| Modal opens, picker is empty | DOM fallback isn't wired — see §1.5 `fromDom()` |
| CoA returns `no_active_session` for a session you can see | The session_id sent doesn't match acctsessionid in radacct — see §1.7 #1 |
| MAC lock saves but devices keep streaming | enforcement step missing — see §2.2 step 3 |
| Speed change "succeeded" but live session unchanged | CoA NAK or no_secret — check `result["coa_result"].code_name`. The DB write still happened; users will get new rate on next re-auth. |
| Frozen card stays online | `disable_card` must call `self._adapter.disconnect(username)` after the freeze — see §6.2 |
| Reveal password leaks to anyone | Use the separate endpoint (§11) — never include in default `check_card` payload |
| Auto-refresh wipes picker state | Pause while modal open (`document.querySelector('.cc-gmodal.is-open')`) |

---

*Generated 2026-05-21. Source of truth: cards_checker_v2.html + services/cards.py + integration/radius_coa.py. Last verified working in commit `cf71213` and after.*
