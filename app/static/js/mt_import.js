/* mt_import.js — تدفّق «استيراد المشتركين من المايكروتيك» (الزيادة 5).
   نافذة مستقلّة (vanilla، بلا اعتماد على نظام مودال خارجي):
   اختر نوعًا → اتصال وجلب (معاينة) → ضبط خيارات التكرار → تأكيد → نتيجة.
   كل النداءات AJAX تُرسل X-CSRFToken من وسم <meta name="csrf-token">. */
(function () {
  'use strict';

  function csrf() {
    var m = document.querySelector('meta[name="csrf-token"]');
    return m ? m.getAttribute('content') : '';
  }

  function post(url, body) {
    return fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrf() },
      body: JSON.stringify(body || {}),
    }).then(function (r) { return r.json().catch(function () { return { ok: false, error: 'خطأ في الاستجابة' }; }); });
  }

  function el(tag, attrs, html) {
    var e = document.createElement(tag);
    if (attrs) Object.keys(attrs).forEach(function (k) { e.setAttribute(k, attrs[k]); });
    if (html != null) e.innerHTML = html;
    return e;
  }

  var state = { nasId: null, nasName: '', importType: 'hotspot', transport: '' };

  // ─── بناء هيكل النافذة مرّة واحدة ───
  var overlay, body;
  function ensureModal() {
    if (overlay) return;
    overlay = el('div', { id: 'mt-import-overlay' });
    overlay.style.cssText =
      'position:fixed;inset:0;background:rgba(0,0,0,.45);z-index:9999;display:none;' +
      'align-items:flex-start;justify-content:center;overflow:auto;padding:40px 16px';
    var card = el('div', { dir: 'rtl' });
    card.style.cssText =
      'background:#fff;border-radius:14px;max-width:760px;width:100%;box-shadow:0 18px 50px rgba(0,0,0,.3);' +
      'font-family:inherit;color:#1c1c1c';
    var head = el('div');
    head.style.cssText = 'display:flex;align-items:center;justify-content:space-between;' +
      'padding:16px 20px;border-bottom:1px solid #eee';
    head.appendChild(el('h3', null,
      '<i class="fa-solid fa-file-import"></i> استيراد المشتركين من الراوتر'));
    var x = el('button', { type: 'button', 'aria-label': 'إغلاق' }, '&times;');
    x.style.cssText = 'border:none;background:none;font-size:26px;cursor:pointer;color:#888;line-height:1';
    x.onclick = close;
    head.appendChild(x);
    card.appendChild(head);
    body = el('div');
    body.style.cssText = 'padding:20px';
    card.appendChild(body);
    overlay.appendChild(card);
    overlay.addEventListener('click', function (e) { if (e.target === overlay) close(); });
    document.body.appendChild(overlay);
  }

  function close() { if (overlay) overlay.style.display = 'none'; }

  function btn(label, kind) {
    var bg = kind === 'primary' ? '#f4ba2a' : (kind === 'danger' ? '#fde2e2' : '#eef2f7');
    var fg = kind === 'danger' ? '#b91c1c' : '#1c1c1c';
    var b = el('button', { type: 'button' }, label);
    b.style.cssText = 'border:none;border-radius:9px;padding:9px 16px;font-weight:700;font-size:13px;' +
      'cursor:pointer;background:' + bg + ';color:' + fg;
    return b;
  }

  function pill(text, color) {
    return '<span style="display:inline-block;padding:2px 9px;border-radius:8px;font-size:12px;' +
      'font-weight:700;background:' + color + '1f;color:' + color + '">' + text + '</span>';
  }

  // ─── الخطوة 1: اختيار النوع ───
  function renderStart() {
    body.innerHTML = '';
    body.appendChild(el('div', null,
      '<div style="color:#666;font-size:13px;margin-bottom:14px">الراوتر: <strong>' +
      escapeHtml(state.nasName) + '</strong></div>'));
    var box = el('div');
    box.style.cssText = 'display:flex;gap:10px;flex-wrap:wrap;margin-bottom:18px';
    [['hotspot', 'بوابة الدخول (Hotspot)', 'fa-wifi'],
     ['broadband', 'نطاق عريض (PPPoE)', 'fa-ethernet'],
     ['usermanager', 'User-Manager (غير مدعوم بعد)', 'fa-ban']].forEach(function (t) {
      var disabled = t[0] === 'usermanager';
      var c = el('button', { type: 'button', 'data-type': t[0] },
        '<i class="fa-solid ' + t[2] + '"></i> ' + t[1]);
      c.style.cssText = 'flex:1;min-width:200px;border:2px solid ' +
        (state.importType === t[0] ? '#f4ba2a' : '#e3e8ef') + ';border-radius:11px;padding:14px;' +
        'background:#fff;font-weight:700;font-size:13px;cursor:' + (disabled ? 'not-allowed' : 'pointer') +
        ';color:' + (disabled ? '#aaa' : '#1c1c1c');
      if (disabled) c.disabled = true;
      else c.onclick = function () { state.importType = t[0]; renderStart(); };
      box.appendChild(c);
    });
    body.appendChild(box);

    var foot = el('div');
    foot.style.cssText = 'display:flex;justify-content:space-between;align-items:center;gap:8px';
    var logsLink = el('a', { href: '#' }, '<i class="fa-solid fa-clock-rotate-left"></i> آخر العمليات');
    logsLink.style.cssText = 'font-size:12.5px;color:#1c52a8;text-decoration:none';
    logsLink.onclick = function (e) { e.preventDefault(); renderLogs(); };
    foot.appendChild(logsLink);
    var go = btn('<i class="fa-solid fa-plug"></i> اتصال وجلب', 'primary');
    go.onclick = doPreview;
    foot.appendChild(go);
    body.appendChild(foot);
  }

  function setBusy(msg) {
    body.innerHTML = '<div style="text-align:center;padding:40px;color:#666">' +
      '<i class="fa-solid fa-spinner fa-spin" style="font-size:26px"></i>' +
      '<div style="margin-top:12px;font-size:13px">' + escapeHtml(msg) + '</div></div>';
  }

  function errBox(msg) {
    return '<div style="background:#fde2e2;color:#b91c1c;border:1px solid #f5b5b5;' +
      'padding:11px 14px;border-radius:9px;font-size:13px;margin-bottom:14px">' +
      '<i class="fa-solid fa-triangle-exclamation"></i> ' + escapeHtml(msg) + '</div>';
  }

  // ─── الخطوة 2: المعاينة ───
  function doPreview() {
    setBusy('جارٍ الاتصال بالراوتر وجلب الحسابات…');
    post('/admin/radius/devices/' + state.nasId + '/import/preview',
      { import_type: state.importType }).then(function (res) {
      if (!res.ok) { renderStart(); body.insertAdjacentHTML('afterbegin', errBox(res.error || 'فشل الجلب')); return; }
      state.transport = res.transport || '';
      renderPreview(res.preview, res.transport);
    });
  }

  function renderPreview(prev, transport) {
    body.innerHTML = '';
    var c = prev.counts || {};
    var summary = el('div');
    summary.style.cssText = 'display:flex;gap:8px;flex-wrap:wrap;margin-bottom:14px;align-items:center';
    summary.innerHTML =
      pill('الإجمالي ' + prev.total, '#475569') + ' ' +
      pill('جديد ' + (c['new'] || 0), '#1f8f51') + ' ' +
      pill('مكرّر ' + (c['duplicate'] || 0), '#b45309') + ' ' +
      pill('غير صالح ' + (c['invalid'] || 0), '#b91c1c') +
      '<span style="margin-in[start]:auto;color:#888;font-size:11.5px">عبر ' +
      (transport === 'rest' ? 'REST' : 'API') + '</span>';
    body.appendChild(summary);

    if (prev.warnings && prev.warnings.length) {
      body.insertAdjacentHTML('beforeend',
        '<div style="background:#fff7e6;color:#92560a;border:1px solid #f3d488;padding:9px 13px;' +
        'border-radius:9px;font-size:12.5px;margin-bottom:12px">⚠️ ' +
        prev.warnings.map(escapeHtml).join('<br>') + '</div>');
    }

    // جدول المعاينة (أوّل 200 صفّ)
    var wrap = el('div');
    wrap.style.cssText = 'max-height:260px;overflow:auto;border:1px solid #eee;border-radius:9px;margin-bottom:14px';
    var rows = (prev.rows || []).slice(0, 200).map(function (r) {
      var sc = r.status === 'new' ? '#1f8f51' : (r.status === 'duplicate' ? '#b45309' : '#b91c1c');
      var sl = r.status === 'new' ? 'جديد' : (r.status === 'duplicate' ? 'مكرّر' : 'غير صالح');
      return '<tr style="border-top:1px solid #f0f0f0">' +
        '<td style="padding:6px 9px">' + escapeHtml(r.username || '—') + '</td>' +
        '<td style="padding:6px 9px;color:#666">' + escapeHtml(r.profile || '—') + '</td>' +
        '<td style="padding:6px 9px">' + escapeHtml(r.plan_name || '—') + '</td>' +
        '<td style="padding:6px 9px">' + pill(sl, sc) + '</td>' +
        '<td style="padding:6px 9px;color:#888;font-size:11.5px">' + escapeHtml(r.note || '') + '</td></tr>';
    }).join('');
    wrap.innerHTML = '<table style="width:100%;border-collapse:collapse;font-size:12.5px">' +
      '<thead><tr style="background:#f7f9fc;text-align:right">' +
      '<th style="padding:7px 9px">المستخدم</th><th style="padding:7px 9px">البروفايل</th>' +
      '<th style="padding:7px 9px">الخطّة</th><th style="padding:7px 9px">الحالة</th>' +
      '<th style="padding:7px 9px">ملاحظة</th></tr></thead><tbody>' + rows + '</tbody></table>';
    body.appendChild(wrap);

    // خيارات التكرار
    var opts = el('div');
    opts.style.cssText = 'background:#f7f9fc;border-radius:9px;padding:13px;margin-bottom:14px;font-size:13px';
    opts.innerHTML =
      '<div style="margin-bottom:9px"><strong>عند وجود مستخدم مكرّر:</strong></div>' +
      '<label style="margin-inline-end:14px"><input type="radio" name="mtdup" value="skip" checked> تخطٍّ</label>' +
      '<label style="margin-inline-end:14px"><input type="radio" name="mtdup" value="update"> تحديث</label>' +
      '<label style="margin-inline-end:14px"><input type="radio" name="mtdup" value="conflict"> اعتباره تعارضًا</label>' +
      '<div style="margin-top:11px;border-top:1px dashed #ddd;padding-top:11px">' +
      '<label style="display:block;margin-bottom:6px"><input type="checkbox" id="mtmkplans"> ' +
      'إنشاء خطّة تلقائيًّا للبروفايلات غير المربوطة</label>' +
      '<label style="display:block"><input type="checkbox" id="mtdry"> ' +
      'محاكاة فقط (بلا كتابة)</label></div>';
    body.appendChild(opts);

    var foot = el('div');
    foot.style.cssText = 'display:flex;justify-content:space-between;gap:8px';
    var back = btn('<i class="fa-solid fa-arrow-right"></i> رجوع', '');
    back.onclick = renderStart;
    foot.appendChild(back);
    var go = btn('<i class="fa-solid fa-check"></i> تأكيد الاستيراد', 'primary');
    go.onclick = doRun;
    foot.appendChild(go);
    body.appendChild(foot);
  }

  // ─── الخطوة 3: التنفيذ + النتيجة ───
  function doRun() {
    var mode = (document.querySelector('input[name="mtdup"]:checked') || {}).value || 'skip';
    var mkplans = !!(document.getElementById('mtmkplans') || {}).checked;
    var dry = !!(document.getElementById('mtdry') || {}).checked;
    setBusy(dry ? 'محاكاة الاستيراد…' : 'جارٍ استيراد المشتركين…');
    post('/admin/radius/devices/' + state.nasId + '/import/run', {
      import_type: state.importType, duplicate_mode: mode,
      create_missing_plans: mkplans ? '1' : '0', dry_run: dry ? '1' : '0',
    }).then(function (res) {
      if (!res.ok) { renderStart(); body.insertAdjacentHTML('afterbegin', errBox(res.error || 'فشل الاستيراد')); return; }
      renderResult(res.result);
    });
  }

  function renderResult(r) {
    body.innerHTML = '';
    var ok = r.status !== 'failed';
    body.insertAdjacentHTML('beforeend',
      '<div style="text-align:center;padding:8px 0 16px">' +
      '<i class="fa-solid ' + (ok ? 'fa-circle-check' : 'fa-circle-xmark') +
      '" style="font-size:38px;color:' + (ok ? '#1f8f51' : '#b91c1c') + '"></i>' +
      '<div style="margin-top:8px;font-weight:700">' +
      (r.dry_run ? 'انتهت المحاكاة' : (ok ? 'تمّ الاستيراد' : 'فشل الاستيراد')) + '</div></div>');
    var grid = el('div');
    grid.style.cssText = 'display:flex;gap:8px;flex-wrap:wrap;justify-content:center;margin-bottom:14px';
    grid.innerHTML =
      pill('جديد ' + r.imported, '#1f8f51') + ' ' +
      pill('محدّث ' + r.updated, '#1c52a8') + ' ' +
      pill('متخطّى ' + r.skipped, '#b45309') + ' ' +
      pill('فاشل ' + r.failed, '#b91c1c');
    body.appendChild(grid);

    if (r.created_plans && r.created_plans.length) {
      body.insertAdjacentHTML('beforeend',
        '<div style="font-size:12.5px;color:#555;margin-bottom:10px">خطط أُنشئت: ' +
        r.created_plans.map(escapeHtml).join('، ') + '</div>');
    }
    if (r.errors && r.errors.length) {
      var errs = r.errors.slice(0, 50).map(function (e) {
        return '<div style="font-size:12px;color:#b91c1c;padding:2px 0">• ' +
          escapeHtml(e.username || '—') + ': ' + escapeHtml(e.reason || '') + '</div>';
      }).join('');
      body.insertAdjacentHTML('beforeend',
        '<div style="max-height:140px;overflow:auto;background:#fff5f5;border:1px solid #f3c5c5;' +
        'border-radius:9px;padding:10px;margin-bottom:12px">' + errs + '</div>');
    }
    if (!r.dry_run) {
      body.insertAdjacentHTML('beforeend',
        '<div style="font-size:12.5px;color:#92560a;background:#fff7e6;border:1px solid #f3d488;' +
        'border-radius:9px;padding:10px;margin-bottom:14px">' +
        '💡 تذكير: تأكّد أنّ إعدادات RADIUS (السرّ المشترك + المنافذ) مضبوطة على هذا الراوتر ' +
        'كي يصادق المستوردون فعليًّا.</div>');
    }
    var foot = el('div');
    foot.style.cssText = 'display:flex;justify-content:flex-end;gap:8px';
    var done = btn('تمّ', 'primary');
    done.onclick = close;
    foot.appendChild(done);
    body.appendChild(foot);
  }

  // ─── السجلّ ───
  function renderLogs() {
    setBusy('جارٍ تحميل آخر العمليات…');
    fetch('/admin/radius/devices/' + state.nasId + '/import/logs')
      .then(function (r) { return r.json(); }).then(function (res) {
        body.innerHTML = '';
        var back = btn('<i class="fa-solid fa-arrow-right"></i> رجوع', '');
        back.onclick = renderStart;
        body.appendChild(back);
        var logs = (res.logs || []);
        if (!logs.length) {
          body.insertAdjacentHTML('beforeend',
            '<div style="text-align:center;color:#888;padding:24px;font-size:13px">لا عمليات سابقة.</div>');
          return;
        }
        var html = '<table style="width:100%;border-collapse:collapse;font-size:12.5px;margin-top:12px">' +
          '<thead><tr style="background:#f7f9fc;text-align:right">' +
          '<th style="padding:7px 9px">النوع</th><th style="padding:7px 9px">جديد</th>' +
          '<th style="padding:7px 9px">محدّث</th><th style="padding:7px 9px">متخطّى</th>' +
          '<th style="padding:7px 9px">فاشل</th><th style="padding:7px 9px">الوقت</th></tr></thead><tbody>' +
          logs.map(function (l) {
            return '<tr style="border-top:1px solid #f0f0f0">' +
              '<td style="padding:6px 9px">' + escapeHtml(l.import_type || '') + '</td>' +
              '<td style="padding:6px 9px">' + (l.imported_count || 0) + '</td>' +
              '<td style="padding:6px 9px">' + (l.updated_count || 0) + '</td>' +
              '<td style="padding:6px 9px">' + (l.skipped_count || 0) + '</td>' +
              '<td style="padding:6px 9px">' + (l.failed_count || 0) + '</td>' +
              '<td style="padding:6px 9px;color:#888">' + escapeHtml((l.finished_at || '').slice(0, 16)) + '</td></tr>';
          }).join('') + '</tbody></table>';
        body.insertAdjacentHTML('beforeend', html);
      });
  }

  function escapeHtml(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (m) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[m];
    });
  }

  // ─── الواجهة العامة ───
  window.mtImportOpen = function (nasId, nasName) {
    ensureModal();
    state.nasId = nasId;
    state.nasName = nasName || ('#' + nasId);
    state.importType = 'hotspot';
    state.transport = '';
    overlay.style.display = 'flex';
    renderStart();
  };

  // ربط الأزرار التي تحمل data-mt-import
  document.addEventListener('click', function (e) {
    var b = e.target.closest('[data-mt-import]');
    if (!b) return;
    e.preventDefault();
    window.mtImportOpen(b.getAttribute('data-mt-import'), b.getAttribute('data-mt-name') || '');
  });
})();
