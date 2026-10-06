"""PARITY (team a) — plan (العرض/الباقة) form: web vs API.

* opening a plan in the web edit form and saving with no change reset ~15
  fields the form does not render (allowed days/hours, daily/monthly quota,
  device count, force MAC, tier, prepaid, auto-renew, card/bulk price, PPP,
  service scope…) and dropped unknown metadata keys;
* the web currency input showed the system currency, so a save overwrote a
  per-plan currency;
* «توزيع متساوٍ» was read from the metadata root while stored under
  ``metadata.subscription`` — OFF never propagated, ON re-propagated always;
* API PATCH of ``duration_minutes`` left a stale duration_value/unit;
* API service_type was stored verbatim (PPPoE with scope=both + hotspot on);
* API lacked ``connection_schedule``.

Run this file alone (per-file isolation).
"""
from __future__ import annotations

import json
import os
from html.parser import HTMLParser

import pytest

TOKEN = "parity-a-plans-token"
AUTH = {"Authorization": "Bearer " + TOKEN}


@pytest.fixture
def app(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "paritya_plans.db")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    monkeypatch.setenv("HOBERADIUS_DB_PATH", db_file)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.setenv("HOBERADIUS_LICENSE_GATE_TEST_BYPASS", "1")
    monkeypatch.setenv("HOBERADIUS_API_TOKENS", TOKEN)
    monkeypatch.setenv("FLASK_SECRET", "parity-a-plans-secret")
    monkeypatch.setenv("HOBERADIUS_BOOTSTRAP_ADMIN_USER", "owner_pp")
    monkeypatch.setenv("HOBERADIUS_BOOTSTRAP_ADMIN_PASS", "owner-pass-pp")
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


def _web_login(client):
    res = client.post("/admin/radius/login",
                      data={"username": "owner_pp", "password": "owner-pass-pp"})
    assert res.status_code in {302, 303}, res.status_code


class _FormScraper(HTMLParser):
    """Collects what a browser would post for the plan edit ``<form>``
    (the first POST form whose action ends with ``action_suffix``)."""

    def __init__(self, action_suffix):
        super().__init__()
        self.suffix = action_suffix
        self.inside = False
        self.done = False
        self.fields: list[tuple[str, str]] = []
        self._select = None
        self._select_first = None
        self._select_chosen = None
        self._textarea = None
        self._text = ""

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if self.done:
            return
        if (tag == "form" and (a.get("method") or "").lower() == "post"
                and (a.get("action") or "").endswith(self.suffix)):
            self.inside = True
            return
        if not self.inside:
            return
        name = a.get("name")
        if tag == "input" and name and "disabled" not in a:
            typ = (a.get("type") or "text").lower()
            if typ in ("checkbox", "radio"):
                if "checked" in a:
                    self.fields.append((name, a.get("value", "on")))
            elif typ not in ("submit", "button", "file"):
                self.fields.append((name, a.get("value", "")))
        elif tag == "select" and name:
            self._select, self._select_first, self._select_chosen = name, None, None
        elif tag == "option" and self._select:
            val = a.get("value", "")
            if self._select_first is None:
                self._select_first = val
            if "selected" in a:
                self._select_chosen = val
        elif tag == "textarea" and name:
            self._textarea, self._text = name, ""

    def handle_data(self, data):
        if self._textarea:
            self._text += data

    def handle_endtag(self, tag):
        if not self.inside:
            return
        if tag == "form":
            self.inside = False
            self.done = True
        elif tag == "select" and self._select:
            chosen = (self._select_chosen if self._select_chosen is not None
                      else self._select_first)
            self.fields.append((self._select, chosen or ""))
            self._select = None
        elif tag == "textarea" and self._textarea:
            self.fields.append((self._textarea, self._text))
            self._textarea = None


def _edit_form(client, pid) -> list[tuple[str, str]]:
    html = client.get(f"/admin/radius/plans/{pid}/edit").get_data(as_text=True)
    p = _FormScraper(f"/admin/radius/plans/{pid}")
    p.feed(html)
    assert p.fields, "plan edit form not found"
    with client.session_transaction() as sess:
        csrf = sess.get("_csrf_token", "")
    return [f for f in p.fields if f[0] != "_csrf_token"] + [("_csrf_token", csrf)]


def _set(fields, name, value):
    """Replace (or add) a single-valued field; value None removes it."""
    out = [f for f in fields if f[0] != name]
    if value is not None:
        out.append((name, value))
    return out


def _post_form(client, pid, fields):
    from werkzeug.datastructures import MultiDict
    res = client.post(f"/admin/radius/plans/{pid}", data=MultiDict(fields))
    assert res.status_code in {302, 303}, res.get_data(as_text=True)[:400]
    return res


def _api_get(client, pid):
    res = client.get(f"/api/v1/profiles/{pid}", headers=AUTH)
    assert res.status_code == 200
    return res.get_json()["data"]


APP_BODY = {
    "name": "AppPlan", "code": "AP1", "plan_type": "time", "service_type": "Both",
    "description": "d", "color": "#2BAACC", "enabled": True, "priority": 3,
    "duration_minutes": 600, "validity_days": 30, "idle_timeout_sec": 300,
    "quota_daily_mb": 500, "quota_monthly_mb": 9000,
    "speed_down_kbps": 4000, "speed_up_kbps": 1000, "speed_control_enabled": True,
    "concurrent_sessions": 2, "ipv6_pool": "v6pool", "bind_mac": True, "bind_ip": True,
    "allowed_days": ["mon", "tue"], "allowed_hours_from": "08:00",
    "allowed_hours_to": "20:00",
    "price": 25, "price_card": 7, "price_bulk": 5, "currency": "USD",
    "plan_tier": "Business", "prepaid": False, "auto_renew": True,
    "loan_enabled": True, "max_loan_minutes": 60, "speed_override_allowed": True,
    "allowed_devices_count": 3, "force_mac_address": True,
    "metadata": {"subscription": {"x_custom": "1"}, "app_only": {"k": "v"}},
}


def _create_api(client, **over):
    body = dict(APP_BODY)
    body.update(over)
    res = client.post("/api/v1/profiles", json=body, headers=AUTH)
    assert res.status_code == 201, res.get_json()
    return res.get_json()["data"]


# ── 1. web open+save without changes keeps every field ─────────────────────

def test_web_resave_without_changes_keeps_unrendered_fields(client):
    before = _create_api(client)
    pid = before["id"]
    _web_login(client)
    _post_form(client, pid, _edit_form(client, pid))
    after = _api_get(client, pid)
    diff = {k: (before.get(k), after.get(k)) for k in set(before) | set(after)
            if before.get(k) != after.get(k) and k != "updated_at"}
    assert diff == {}
    # the specific fields the audit saw wiped
    assert after["allowed_days"] == ["mon", "tue"]
    assert (after["allowed_hours_from"], after["allowed_hours_to"]) == ("08:00", "20:00")
    assert (after["quota_daily_mb"], after["quota_monthly_mb"]) == (500, 9000)
    # allowed_devices_count / force_mac_address / plan_tier / auto_renew: removed
    # from every form (owner 2026-10-06) — the API ignores them; whatever is
    # stored survives a web re-save untouched.
    for k in ("allowed_devices_count", "force_mac_address", "plan_tier",
              "auto_renew", "auto_renew_mode"):
        assert after[k] == before[k], k
    assert after["prepaid"] is False
    assert (after["price_card"], after["price_bulk"]) == (7, 5)
    assert after["ppp_enabled"] is True and after["service_scope"] == "both"
    assert (after["duration_value"], after["duration_unit"]) == (10, "Hrs")
    assert after["currency"] == "USD"
    assert after["metadata"]["subscription"]["x_custom"] == "1"
    assert after["metadata"]["app_only"] == {"k": "v"}


def test_web_form_edits_still_apply_and_meta_field_can_be_cleared(client):
    pid = _create_api(client)["id"]
    _web_login(client)
    fields = _edit_form(client, pid)
    fields = _set(fields, "primary_dns_ppp", "1.1.1.1")
    fields = _set(fields, "speed_down_kbps", "8000")
    _post_form(client, pid, fields)
    d = _api_get(client, pid)
    assert d["speed_down_kbps"] == 8000
    assert d["metadata"]["general"]["primary_dns_ppp"] == "1.1.1.1"
    # clear it again from the form → removed, unknown keys kept
    _post_form(client, pid, _set(_edit_form(client, pid), "primary_dns_ppp", ""))
    d = _api_get(client, pid)
    assert "primary_dns_ppp" not in d["metadata"].get("general", {})
    assert d["metadata"]["subscription"]["x_custom"] == "1"
    assert d["metadata"]["app_only"] == {"k": "v"}
    # service cards: untick «هوت سبوت» → PPPoE, derived flags follow
    fields = [f for f in _edit_form(client, pid)
              if not (f[0] == "service_type" and f[1] == "Hotspot")]
    _post_form(client, pid, fields)
    d = _api_get(client, pid)
    assert (d["service_type"], d["service_scope"]) == ("PPPoE", "broadband")
    assert d["hotspot_enabled"] is False and d["ppp_enabled"] is True


def test_web_quota_daily_monthly_inputs_render_and_save(client):
    pid = _create_api(client)["id"]
    _web_login(client)
    html = client.get(f"/admin/radius/plans/{pid}/edit").get_data(as_text=True)
    assert 'name="quota_daily_mb"' in html and 'name="quota_monthly_mb"' in html
    assert "كوتا يومية" in html and "كوتا شهرية" in html
    assert "قائمة عناوين (Address-List)" in html
    assert "نطاق عناوين الشبكة" not in html
    fields = _edit_form(client, pid)
    fields = _set(fields, "quota_daily_mb", "1024")
    fields = _set(fields, "quota_monthly_mb", "0")
    _post_form(client, pid, fields)
    d = _api_get(client, pid)
    assert (d["quota_daily_mb"], d["quota_monthly_mb"]) == (1024, 0)


def test_web_currency_shows_plan_currency(client):
    from app.radius.core.system_config import default_currency
    assert default_currency() != "USD"
    pid = _create_api(client, currency="USD")["id"]
    _web_login(client)
    fields = _edit_form(client, pid)
    assert dict(fields)["currency"] == "USD"
    _post_form(client, pid, fields)
    assert _api_get(client, pid)["currency"] == "USD"


# ── 3. «توزيع متساوٍ» propagates only on a real change, both directions ────

def test_equal_split_flag_read_from_grouped_metadata_and_propagates_on_change(
        client, monkeypatch):
    from app.radius.routes import plans as plans_routes
    calls = []
    monkeypatch.setattr(plans_routes, "_propagate_plan_split",
                        lambda pid, ed, eu: calls.append((pid, ed, eu)))
    pid = _create_api(client)["id"]
    _web_login(client)
    assert plans_routes._plan_split_flags_by_id(pid) == (False, False)

    _post_form(client, pid, _edit_form(client, pid))          # unchanged
    assert calls == []

    _post_form(client, pid, _set(_edit_form(client, pid), "equal_download_speed", "1"))
    assert calls == [(pid, True, False)]
    assert plans_routes._plan_split_flags_by_id(pid) == (True, False)
    assert _api_get(client, pid)["metadata"]["subscription"]["equal_download_speed"] == "1"

    _post_form(client, pid, _edit_form(client, pid))          # unchanged, ON
    assert calls == [(pid, True, False)]                     # no re-propagation

    _post_form(client, pid, _set(_edit_form(client, pid), "equal_download_speed", None))
    assert calls == [(pid, True, False), (pid, False, False)]
    assert plans_routes._plan_split_flags_by_id(pid) == (False, False)


# ── 4. API PATCH duration_minutes re-derives duration_value/unit ───────────

def test_api_patch_duration_minutes_rederives_value_unit(client):
    d = _create_api(client, duration_minutes=480)
    pid = d["id"]
    assert (d["duration_value"], d["duration_unit"]) == (8, "Hrs")
    for minutes, expect in ((1440, (1, "Days")), (90, (90, "Mins")),
                            (120, (2, "Hrs")), (0, (0, "Mins"))):
        res = client.patch(f"/api/v1/profiles/{pid}",
                           json={"duration_minutes": minutes}, headers=AUTH)
        assert res.status_code == 200, res.get_json()
        g = _api_get(client, pid)
        assert (g["duration_minutes"], g["duration_value"], g["duration_unit"]) == (
            minutes, *expect)


# ── 5. API service_type → canonical + derived scope/flags ─────────────────

@pytest.mark.parametrize("sent,canon,scope,hs,ppp", [
    ("PPPoE", "PPPoE", "broadband", False, True),
    ("pppoe", "PPPoE", "broadband", False, True),
    ("HOTSPOT", "Hotspot", "hotspot", True, False),
    ("both", "Both", "both", True, True),
])
def test_api_service_type_derives_scope_and_flags(client, sent, canon, scope, hs, ppp):
    d = _create_api(client, service_type=sent, service_scope="both",
                    hotspot_enabled=True, ppp_enabled=False)
    assert (d["service_type"], d["service_scope"]) == (canon, scope)
    assert (d["hotspot_enabled"], d["ppp_enabled"]) == (hs, ppp)


def test_api_service_type_patch_and_legacy_values(client, app):
    pid = _create_api(client, service_type="Hotspot")["id"]
    res = client.patch(f"/api/v1/profiles/{pid}", json={"service_type": "both"},
                       headers=AUTH)
    assert res.status_code == 200
    g = _api_get(client, pid)
    assert (g["service_type"], g["service_scope"], g["hotspot_enabled"],
            g["ppp_enabled"]) == ("Both", "both", True, True)
    # a new legacy/unknown value is refused
    res = client.post("/api/v1/profiles", json=dict(APP_BODY, name="Leg",
                                                    service_type="Voucher"),
                      headers=AUTH)
    assert res.status_code == 422
    res = client.patch(f"/api/v1/profiles/{pid}", json={"service_type": "Voucher"},
                       headers=AUTH)
    assert res.status_code == 422
    # …but a stored legacy value survives an unrelated PATCH unchanged
    from app.radius.db.connection import transaction
    with transaction() as conn:
        conn.execute("UPDATE access_plans SET service_type='Voucher', "
                     "service_scope='both', hotspot_enabled=1, ppp_enabled=1 "
                     "WHERE id=?", (pid,))
    res = client.patch(f"/api/v1/profiles/{pid}", json={"price": 30,
                       "service_type": "Voucher"}, headers=AUTH)
    assert res.status_code == 200, res.get_json()
    g = _api_get(client, pid)
    assert (g["service_type"], g["service_scope"], g["hotspot_enabled"],
            g["ppp_enabled"]) == ("Voucher", "both", True, True)
    # and the web form resave keeps it too (rendered as «هوت سبوت»)
    _web_login(client)
    _post_form(client, pid, _edit_form(client, pid))
    assert _api_get(client, pid)["service_type"] == "Voucher"


# ── 6. API accepts+returns the enforced web-only fields ───────────────────

def test_api_enforced_fields_roundtrip(client):
    sched = {"windows": [{"days": ["mon", "fri"], "from": "08:00", "to": "12:00"}]}
    d = _create_api(client, name="Open", speed_down_kbps=0, speed_up_kbps=0,
                    speed_unlimited=True, offer_hours_from="06:00",
                    offer_hours_to="23:00", connection_schedule=sched,
                    max_daily_minutes=120, shared_single_session=True)
    pid = d["id"]
    g = _api_get(client, pid)
    assert g["speed_unlimited"] is True
    assert (g["speed_down_kbps"], g["speed_up_kbps"]) == (0, 0)
    assert (g["offer_hours_from"], g["offer_hours_to"]) == ("06:00", "23:00")
    assert json.loads(g["connection_schedule"]) == {
        "windows": [{"days": ["mon", "fri"], "from": "08:00", "to": "12:00"}]}
    assert g["max_daily_minutes"] == 120 and g["shared_single_session"] is True

    # JSON-string form, then clear
    res = client.patch(f"/api/v1/profiles/{pid}", headers=AUTH, json={
        "connection_schedule": json.dumps({"windows": [{"days": ["sun"],
                                                        "from": "", "to": ""}]}),
        "shared_single_session": False, "max_daily_minutes": 0})
    assert res.status_code == 200, res.get_json()
    g = _api_get(client, pid)
    assert json.loads(g["connection_schedule"])["windows"][0]["days"] == ["sun"]
    assert g["shared_single_session"] is False and g["max_daily_minutes"] == 0
    res = client.patch(f"/api/v1/profiles/{pid}", headers=AUTH,
                       json={"connection_schedule": ""})
    assert res.status_code == 200
    assert _api_get(client, pid)["connection_schedule"] == ""

    for bad in ({"windows": [{"days": ["xyz"]}]},
                {"windows": [{"days": ["mon"], "from": "25:00"}]},
                "{not json", 5):
        res = client.patch(f"/api/v1/profiles/{pid}", headers=AUTH,
                           json={"connection_schedule": bad})
        assert res.status_code == 422, (bad, res.get_json())
    res = client.patch(f"/api/v1/profiles/{pid}", headers=AUTH,
                       json={"offer_hours_from": "26:00"})
    assert res.status_code == 422
