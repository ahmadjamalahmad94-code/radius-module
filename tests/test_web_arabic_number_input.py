# -*- coding: utf-8 -*-
"""Arabic keyboard numbers on the web (R12 N1, HIGH) + Arabic AJAX errors (R12 N4).

R12 N1: an Arabic phone keyboard types the decimal point as «٫» (U+066B).
``latin_digits.js`` deleted it, so ٣٥٫٥ became 355 (payment ×10, +152 days),
٥٥٫٥ → 555, ١٫٥ GB → 1, ٣٫٥ → 35. Fixed on both sides:

* JS (``static/js/latin_digits.js``): «٫» → «.», «٬» (thousands) → removed,
  Persian ۰-۹ → 0-9, «−» → «-». Node is not installed on the dev box, so the JS
  is tested two ways:
    1. **Real execution** — the pure ``/*<hr-num-clean>*/`` block of the shipped
       file is extracted and run by Windows Script Host (``cscript``, JScript/ES3).
       Skipped where cscript does not exist (non-Windows CI).
    2. **Regex port** — the regex literals of ``numSeps``/``numClean`` are pulled
       out of the JS source and applied with Python ``re`` (always runs), so the
       shipped character classes themselves are checked, not a hand copy.
* Server (``core/numbers.py`` + ``core/form_numbers.py``): every shared parser
  (``strict_float``/``finite_float``/``money_float``/``finite_int``) reads «٫»
  and Arabic-Indic/Persian digits, and a before-request hook rewrites any web
  form field that is a pure Arabic-script number («٣٥٫٥» → «35.5») so routes
  that pass the raw text to a service get it right even when the JS never ran.
  «1,5» is still refused (ambiguous) — never a silent ×10.

R12 N4 (display layer only): ``static/js/hr_ajax_errors.js`` normalises failed
JSON bodies so modals show the server's Arabic ``message`` instead of a code or
«[object Object]».

Run this file alone (per-file isolation).
"""
from __future__ import annotations

import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from uuid import uuid4

import pytest

ROOT = os.path.join(os.path.dirname(__file__), "..")
LATIN_JS = os.path.join(ROOT, "app", "static", "js", "latin_digits.js")
AJAX_JS = os.path.join(ROOT, "app", "static", "js", "hr_ajax_errors.js")
LAYOUT = os.path.join(ROOT, "app", "templates", "admin", "_admin_layout.html")

TOKEN = "webinput-token"
AUTH = {"Authorization": "Bearer " + TOKEN}
FETCH = {"X-Requested-With": "fetch"}

AR_35_5 = "٣٥٫٥"          # ٣٥٫٥
AR_55_5 = "٥٥٫٥"          # ٥٥٫٥
AR_1_5 = "١٫٥"                 # ١٫٥
AR_3_5 = "٣٫٥"                 # ٣٫٥
AR_1000 = "١٬٠٠٠"    # ١٬٠٠٠
FA_35_5 = "۳۵٫۵"          # ۳۵٫۵ (Persian digits)


def _read(path):
    with io.open(path, encoding="utf-8") as fh:
        return fh.read()


def _block(src, tag):
    m = re.search(r"/\*<%s>\*/(.*?)/\*</%s>\*/" % (tag, tag), src, re.S)
    assert m, tag
    return m.group(1)


# ═══════════════════════ JS: real execution via cscript ═══════════════════════

_JS_ESC = r"""
function __esc(s) {
  var o = '', i, c; s = String(s);
  for (i = 0; i < s.length; i++) {
    c = s.charCodeAt(i);
    if (c < 128 && c !== 92 && c !== 34) o += s.charAt(i);
    else o += '\\u' + ('000' + c.toString(16)).slice(-4);
  }
  return '"' + o + '"';
}
function __out(k, v) { WScript.Echo(k + '\t' + __esc(v)); }
"""


def _run_jscript(body: str) -> dict:
    """Run ES3 ``body`` under cscript; returns {key: value} from ``__out``."""
    if os.name != "nt" or not shutil.which("cscript"):
        pytest.skip("cscript (Windows Script Host) not available")
    tmp = tempfile.mkdtemp(prefix="hr_js_")
    path = os.path.join(tmp, "t.js")
    with io.open(path, "w", encoding="utf-16") as fh:   # BOM → cscript reads Unicode
        fh.write(_JS_ESC + body)
    res = subprocess.run(["cscript", "//nologo", "//E:JScript", path],
                         capture_output=True, timeout=60)
    out = res.stdout.decode("ascii", "replace")
    assert res.returncode == 0, out + res.stderr.decode("ascii", "replace")
    got = {}
    for line in out.splitlines():
        if "\t" in line:
            k, v = line.split("\t", 1)
            got[k] = json.loads(v)
    return got


NUM_CLEAN_CASES = [
    (AR_35_5, "35.5"),            # the R12 payment: was 355
    (AR_55_5, "55.5"),            # custom price: was 555
    (AR_1_5, "1.5"),              # quota GB: was 1 (then 15)
    (AR_3_5, "3.5"),              # distributor settle: was 35
    (AR_1000, "1000"),            # Arabic thousands separator removed
    ("١٬٢٣٤٫٥", "1234.5"),
    (FA_35_5, "35.5"),            # Persian digits
    ("−٥", "-5"),       # typographic minus
    ("-12.75", "-12.75"),         # Latin untouched
    ("3,5", "3.5"),               # ASCII comma kept its old meaning
    ("٣،٥", "3.5"),  # Arabic comma
    ("‏" + AR_35_5, "35.5"),   # RTL mark from a paste
    ("abc٣", "3"),           # junk still stripped
]


def test_js_num_clean_runs_real_code_under_cscript():
    block = _block(_read(LATIN_JS), "hr-num-clean")
    lines = [block]
    for i, (raw, _want) in enumerate(NUM_CLEAN_CASES):
        lines.append("__out('c%d', numClean(%s));" % (i, json.dumps(raw)))
    # cursor math in setCleaned relies on prefix-stability of the mapping
    lines.append("""
      var s = '\\u0661\\u066C\\u0662\\u0663\\u0664\\u066B\\u0665x', full = numClean(s), ok = 1, k;
      for (k = 0; k <= s.length; k++) if (full.indexOf(numClean(s.slice(0, k))) !== 0) ok = 0;
      __out('prefix', ok ? 'yes' : 'no');
      __out('seps', numSeps('x\\u0661\\u066By'));
    """)
    got = _run_jscript("\n".join(lines))
    for i, (raw, want) in enumerate(NUM_CLEAN_CASES):
        assert got["c%d" % i] == want, (raw, got["c%d" % i], want)
    assert got["prefix"] == "yes"
    assert got["seps"] == "x1.y"      # decimal text fields: separators only


NUM_CHECK_CASES = [
    # value, required, min, max, step → "" (valid) or a fragment of the message
    ("35.5", False, "0", "", "0.01", ""),
    ("35.5", False, "0", "", None, ""),             # no step attr → decimals allowed
    ("35.5", False, "0", "", "any", ""),
    ("-5", False, None, None, None, ""),            # negatives where min allows
    ("-5", False, "-10", "", "1", ""),
    ("-5", False, "0", "", None, "0 أو أكثر"),       # min still enforced
    ("101", False, "0", "100", None, "100 أو أقلّ"),  # max still enforced
    ("5.", False, "0", "", None, ""),
    (".5", False, "0", "", None, ""),
    ("1.5", False, "0", "", "1", "عددًا صحيحًا"),     # explicit integer step
    ("0.25", False, "0", "", "0.01", ""),
    ("0.255", False, "0", "", "0.01", "مضاعفات 0.01"),
    ("", True, None, None, None, "مطلوب"),
    ("", False, None, None, None, ""),
    ("3.5.5", False, None, None, None, "رقمًا صحيحًا"),
    ("-", False, None, None, None, "رقمًا صحيحًا"),
]


def test_js_num_check_min_max_step_under_cscript():
    block = _block(_read(LATIN_JS), "hr-num-clean")
    lines = [block]
    for i, (v, req, mn, mx, st, _w) in enumerate(NUM_CHECK_CASES):
        lines.append("__out('k%d', numCheck(%s, %s, %s, %s, %s));" % (
            i, json.dumps(v), "true" if req else "false",
            json.dumps(mn), json.dumps(mx), json.dumps(st)))
    got = _run_jscript("\n".join(lines))
    for i, case in enumerate(NUM_CHECK_CASES):
        want = case[-1]
        msg = got["k%d" % i]
        if want == "":
            assert msg == "", (case, msg)
        else:
            assert want in msg, (case, msg)


# ═══════════════════ JS: regex port (always runs, no JS engine) ═══════════════════

def _js_class_to_py(cls: str) -> str:
    return cls  # \uXXXX, \s, \d and ranges mean the same in Python re


def _py_num_seps_from_source():
    src = _read(LATIN_JS)
    body = re.search(r"function numSeps\(s\) \{(.*?)\n\s*\}", src.replace("\r\n", "\n"), re.S).group(1)
    steps = re.findall(r"\.replace\(/(\[[^/]*\])/g, '([^']*)'\)", body)
    assert len(steps) == 3, steps          # «٫،,»→. · minus→- · «٬»/marks/space→''
    clean = re.search(r"function numClean\(s\) \{[^\n]*\n\s*return numSeps\(s\)\.replace\(/(\[[^/]*\])/g, ''\)",
                      src).group(1)

    def to_latin(s):
        return "".join(str(int(ch)) if ("٠" <= ch <= "٩" or "۰" <= ch <= "۹")
                       else ch for ch in s)

    def num_seps(s):
        s = to_latin(s)
        for cls, rep in steps:
            s = re.sub(_js_class_to_py(cls), rep, s)
        return s

    def num_clean(s):
        return re.sub(_js_class_to_py(clean), "", num_seps(s))

    return steps, num_seps, num_clean


def test_js_regex_port_maps_arabic_separators():
    steps, _seps, num_clean = _py_num_seps_from_source()
    classes = {rep: cls for cls, rep in steps}
    assert "\\u066B" in classes["."]        # ٫ → .
    assert "\\u066C" in classes[""]         # ٬ → removed
    assert "\\u2212" in classes["-"]        # − → -
    for raw, want in NUM_CLEAN_CASES:
        assert num_clean(raw) == want, (raw, num_clean(raw), want)


def test_js_wiring_uses_the_cleaners():
    src = _read(LATIN_JS)
    # number fields: cleaner with caret kept; decimal text fields: separators only
    # F08-L (fix wave 3): a number field no longer strips unknown characters
    # («1e9» became «19»): separators/digits are normalised, anything else is
    # marked invalid with a message (markNum) and refused on submit.
    assert "setCleaned(t, numClean)" not in src
    assert "markNum(t)" in src and "NUM_BAD_MSG" in src
    assert "setCleaned(t, numSeps)" in src and "isDecimalText(t)" in src
    assert "inputmode') === 'decimal'" in src and "ui-value" in src
    # submit-time validation goes through the shared checker (min/max/step)
    assert "numCheck(numSeps(el.value" in src
    # the old «strip everything but digits» line that deleted «٫» is gone
    assert ".replace(/,/g, '.').replace(/[^\\d.\\-]/g, '')" not in src
    # the layout cache-busts the fixed file (nginx max-age=3600)
    assert "asset_v('js/latin_digits.js')" in _read(LAYOUT)


# ═══════════════════════ JS: AJAX error display layer ═══════════════════════

def test_ajax_error_normaliser_under_cscript():
    block = _block(_read(AJAX_JS), "hr-ajax-err")
    body = block + r"""
      var a = hrNormalizeErrorPayload({ok: false, error: 'validation_error', message: 'المبلغ'}, false);
      __out('a_err', a.error); __out('a_raw', a.error_raw);
      var b = hrNormalizeErrorPayload({ok: false, error: 'not_found'}, false);
      __out('b_err', b.error); __out('b_code', b.error_code);
      var c = hrNormalizeErrorPayload({ok: false, error: {code: 'x_code', message: 'رسالة'}}, false);
      __out('c_str', '' + c.error); __out('c_code', c.error.code); __out('c_msg', c.message);
      var d = hrNormalizeErrorPayload({ok: false, error: 'خطأ'}, false);
      __out('d_err', d.error);
      var e = hrNormalizeErrorPayload({ok: true, error: 'validation_error'}, true);
      __out('e_err', e.error);
      var f = hrNormalizeErrorPayload({ok: false, code: 'busy', message: 'مشغول'}, true);
      __out('f_err', f.error);
      var g = hrNormalizeErrorPayload({error: 'validation_error', message: 'المبلغ'}, false);
      __out('g_err', g.error);
      var h = hrNormalizeErrorPayload({ok: false, error: 'minutes > 0 required'}, false);
      __out('h_err', h.error);
      var i2 = hrNormalizeErrorPayload({ok: false, error: {code: 'only_code'}}, false);
      __out('i_str', '' + i2.error);
      __out('m1', hrErrMsg('validation_error', 'FB'));
      __out('m2', hrErrMsg({ok: false, error: {message: 'رسالة'}}, 'FB'));
      __out('m3', hrErrMsg(null));
    """
    got = _run_jscript(body)
    assert got["a_err"] == "المبلغ" and got["a_raw"] == "validation_error"
    assert got["b_err"] == "" and got["b_code"] == "not_found"   # → modal's Arabic fallback
    assert got["c_str"] == "رسالة" and got["c_code"] == "x_code" and got["c_msg"] == "رسالة"
    assert got["d_err"] == "خطأ"                                   # Arabic untouched
    assert got["e_err"] == "validation_error"                      # success bodies untouched
    assert got["f_err"] == "مشغول"                                 # {code, message} shape
    assert got["g_err"] == "المبلغ"                                # HTTP error without ok flag
    assert got["h_err"] == "minutes > 0 required"                  # English sentence: source fix
    assert got["i_str"] == "only_code"                             # never «[object Object]»
    assert got["m1"] == "FB" and got["m2"] == "رسالة"
    assert got["m3"] == "تعذّر تنفيذ العملية."


def test_ajax_error_helper_wraps_fetch_json_and_ships_in_head():
    src = _read(AJAX_JS)
    assert "Response.prototype" in src and "RP.json = wrapped" in src
    assert "sameOrigin(res.url)" in src
    layout = _read(LAYOUT)
    head = layout.split("</head>", 1)[0]
    assert "js/hr_ajax_errors.js" in head       # before any page script calls fetch


# ═══════════════════════ server: shared parsers ═══════════════════════

def test_shared_number_parsers_accept_arabic_decimal():
    from app.radius.core.numbers import (NonFiniteNumber, finite_float, finite_int,
                                         money_float, normalize_number_text,
                                         strict_float)
    assert strict_float(AR_35_5) == 35.5
    assert strict_float(FA_35_5) == 35.5
    assert finite_float(AR_1000 + "٫٢٥") == 1000.25
    assert money_float(AR_3_5) == 3.5
    assert finite_int("٤٢", field="days") == 42
    assert strict_float("−٥") == -5.0
    assert strict_float("‏" + AR_55_5 + " ") == 55.5
    assert normalize_number_text(7) == 7
    # ambiguous comma: refused with an error, never a silent ×10 / ÷1000
    for bad in ("1,5", "3،5", AR_35_5 + "٫٥"):
        with pytest.raises(ValueError):
            strict_float(bad)
    with pytest.raises(NonFiniteNumber) as ei:
        money_float("١٫٢٫٣")
    assert "رقم" in ei.value.message


def test_form_normaliser_only_touches_pure_numbers_and_never_secrets():
    from werkzeug.datastructures import ImmutableMultiDict
    from app.radius.core.form_numbers import normalize_form_numbers
    form = ImmutableMultiDict([
        ("amount", AR_35_5), ("amount", AR_1000), ("price", FA_35_5),
        ("notes", "دفعة " + AR_35_5), ("password", "١٢٣٤"),
        ("radius_secret", "١٢"), ("comma", "1,5"), ("latin", "12.5"),
        ("mobile", "٠٥٩٩"),
    ])
    out = normalize_form_numbers(form)
    assert out.getlist("amount") == ["35.5", "1000"]
    assert out["price"] == "35.5"
    assert out["notes"] == "دفعة " + AR_35_5                       # text untouched
    assert out["password"] == "١٢٣٤"          # secrets untouched
    assert out["radius_secret"] == "١٢"
    assert out["comma"] == "1,5" and out["latin"] == "12.5"
    assert out["mobile"] == "0599"                                 # same as the JS does live
    assert normalize_form_numbers(ImmutableMultiDict([("a", "1.5"), ("b", "x")])) is None


# ═══════════════════════ server: web routes end-to-end ═══════════════════════

@pytest.fixture
def app(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "webinput.db")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    monkeypatch.setenv("HOBERADIUS_DB_PATH", db_file)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.setenv("HOBERADIUS_LICENSE_GATE_TEST_BYPASS", "1")
    monkeypatch.setenv("HOBERADIUS_API_TOKENS", TOKEN)
    monkeypatch.setenv("FLASK_SECRET", "webinput-secret")
    monkeypatch.setenv("HOBERADIUS_BOOTSTRAP_ADMIN_USER", "owner_wi")
    monkeypatch.setenv("HOBERADIUS_BOOTSTRAP_ADMIN_PASS", "owner-pass")
    from app.radius.db.connection import reset_for_tests
    reset_for_tests(db_file)
    from app import create_app
    application = create_app()
    with application.app_context():
        from app.radius.db.migrations_runner import run_pending_migrations
        from app.radius.db.repos import admins_repo, tenants_repo
        run_pending_migrations()
        tenants_repo.ensure_default_tenant()
        admins_repo.ensure_default_roles()
        yield application


@pytest.fixture
def client(app):
    return app.test_client()


def _web_login(client) -> str:
    res = client.post("/admin/radius/login",
                      data={"username": "owner_wi", "password": "owner-pass"})
    assert res.status_code in {302, 303}, res.status_code
    client.get("/admin/radius/users")
    with client.session_transaction() as sess:
        return sess.get("_csrf_token", "")


def _db():
    from app.radius.db.connection import db
    return db()


def _plan(price=30.0, days=30) -> int:
    now = datetime.utcnow().isoformat()
    cur = _db().execute(
        "INSERT INTO access_plans(tenant_id, name, duration_minutes, validity_days, "
        "price, currency, quota_total_mb, enabled, created_at, updated_at) "
        "VALUES(1,?,?,?,?,?,0,1,?,?)",
        ("p_" + uuid4().hex[:6], days * 1440, days, price, "ILS", now, now))
    return int(cur.lastrowid)


def _sub(plan_id):
    from app.radius.core.types import Subscriber
    from app.radius.db.repos import subscribers_repo
    username = "u_" + uuid4().hex[:8]
    subscribers_repo.upsert_subscriber(Subscriber(
        id=None, tenant_id=1, username=username, password="secret", plan_id=plan_id,
        full_name="WI User", mobile="0599000000", status="enabled",
        expire_at=datetime(2030, 1, 1, 12, 0, 0)))
    return username


def _payments(username):
    return [float(r[0]) for r in _db().execute(
        "SELECT amount FROM payment_transactions WHERE username=? ORDER BY id",
        (username,)).fetchall()]


def test_web_payment_arabic_decimal_is_35_5_not_355(client):
    csrf = _web_login(client)
    user = _sub(_plan())
    res = client.post(f"/admin/radius/users/{user}/payments", headers=FETCH, data={
        "_csrf_token": csrf, "amount": AR_35_5, "method": "cash", "currency": "ILS"})
    body = res.get_json()
    assert res.status_code == 200 and body["ok"] is True, body
    assert _payments(user) == [35.5]


def test_web_payment_ambiguous_comma_is_refused_not_x10(client):
    csrf = _web_login(client)
    user = _sub(_plan())
    res = client.post(f"/admin/radius/users/{user}/payments", headers=FETCH, data={
        "_csrf_token": csrf, "amount": "3,5", "method": "cash", "currency": "ILS"})
    body = res.get_json()
    assert res.status_code >= 400 and body["ok"] is False
    assert re.search("[؀-ۿ]", body["error"]), body     # Arabic message
    assert _payments(user) == []


def test_web_create_custom_price_arabic_decimal(client):
    csrf = _web_login(client)
    pid = _plan()
    name = "c_" + uuid4().hex[:8]
    res = client.post("/admin/radius/users", data={
        "_csrf_token": csrf, "username": name, "password": "pw123456",
        "full_name": "Price User", "plan_id": str(pid), "status": "enabled",
        "user_type": "subscriber", "custom_price": AR_55_5})
    assert res.status_code in {200, 302, 303}, res.status_code
    row = _db().execute("SELECT custom_price FROM subscribers WHERE username=?",
                        (name,)).fetchone()
    assert row is not None and float(row[0]) == 55.5


def test_web_distributor_settle_arabic_decimal(client):
    csrf = _web_login(client)
    res = client.post("/api/v1/distributors", headers=AUTH, json={
        "name": "wi_" + uuid4().hex[:6], "balance": 10, "credit_limit": 0})
    assert res.status_code == 201, res.get_json()
    did = res.get_json()["data"]["distributor"]["id"]
    res = client.post(f"/admin/radius/distributors/{did}/settle", data={
        "_csrf_token": csrf, "amount": AR_3_5, "direction": "credit",
        "apply_to": "balance"})
    assert res.status_code in {302, 303}
    s = client.get(f"/api/v1/distributors/{did}/summary",
                   headers=AUTH).get_json()["data"]["summary"]
    assert float(s["balance"]) == 13.5               # was 45 (٣٫٥ → 35)


def test_rendered_admin_page_ships_both_helpers(client):
    _web_login(client)
    body = client.get("/admin/radius/users").get_data(as_text=True)
    assert "js/latin_digits.js" in body and "js/hr_ajax_errors.js" in body
