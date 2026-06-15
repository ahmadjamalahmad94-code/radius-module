"""feat/data-connection-oneclick — اختبارات وقت التوليد لـ«اتصال بيانات».

يُثبّت:
  * عيّنات السكربت المرجعية (v6 SSTP، v6 PPTP، v7 WireGuard) حرفيًّا؛
  * نظافة ASCII، والوجهة = النطاق الفرعي فقط، وعدم تسرّب أي عنوان داخلي
    (CHR/بروكسي/شبكات الإدارة 10.99/10.98/10.51/10.10)؛
  * حساب vps_accel يكتب reply الراديوس = Filter-Id 5 ميجابت فقط؛
  * تخصيص عنوان مجمّع WG (يتخطّى المستخدم، يبقى داخل المجمّع، خارج البادئات
    الممنوعة)؛
  * أمان الحقن (اقتباس/سطر جديد في اسم المستخدم يُرفَض).

شغّل هذا الملف وحده (عزل الاختبارات لكل ملف).
"""
from __future__ import annotations

import os

import pytest

from app.radius.services import data_connection as dc

SUBDOMAIN = "client7.hoberadius.com"


# ════════════════════════════════════════════════════════════════════════
# (1) المولّدات الخالصة — لا DB
# ════════════════════════════════════════════════════════════════════════
class TestGenerators:

    def test_v6_sstp_matches_reference(self):
        out = dc.render_sstp_client(
            host=SUBDOMAIN, username="sub1", password="pw1",
            comment="HobeRadius DATA sub1", version=6, conn_name="hobe-data-sstp",
        )
        assert out == (
            '/interface sstp-client add name="hobe-data-sstp" '
            f'connect-to={SUBDOMAIN} port=443 user="sub1" password="pw1" '
            "profile=default-encryption verify-server-certificate=yes "
            'add-default-route=no disabled=no comment="HobeRadius DATA sub1"'
        )
        assert "tls-version" not in out          # v6 لا يحمل tls-version
        assert out.isascii()
        dc.assert_no_leakage(out)

    def test_v7_sstp_adds_tls_version(self):
        out = dc.render_sstp_client(
            host=SUBDOMAIN, username="sub1", password="pw1",
            comment="x", version=7,
        )
        assert "tls-version=only-1.2" in out
        dc.assert_no_leakage(out)

    def test_v6_pptp_matches_reference(self):
        out = dc.render_pptp_client(
            host=SUBDOMAIN, username="sub1", password="pw1",
            comment="HobeRadius DATA sub1", version=6, conn_name="hobe-data-pptp",
        )
        assert out == (
            '/interface pptp-client add name="hobe-data-pptp" '
            f'connect-to={SUBDOMAIN} user="sub1" password="pw1" '
            "profile=default-encryption add-default-route=no disabled=no "
            'comment="HobeRadius DATA sub1"'
        )
        assert out.isascii()
        dc.assert_no_leakage(out)

    def test_v7_wireguard_shape(self):
        out = dc.render_wireguard_client(
            host=SUBDOMAIN, wg_port=51821,
            client_private_key="PRIVKEYpriv==", server_public_key="SRVPUBkey==",
            assigned_ip="10.60.0.5", comment="HobeRadius DATA sub1",
        )
        lines = out.splitlines()
        assert len(lines) == 3
        assert lines[0].startswith('/interface wireguard add name="hobe-data-wg"')
        assert 'public-key="SRVPUBkey=="' in lines[1]
        assert f"endpoint-address={SUBDOMAIN}" in lines[1]
        assert "endpoint-port=51821" in lines[1]
        assert "persistent-keepalive=25s" in lines[1]
        assert "address=10.60.0.5/32" in lines[2]
        assert out.isascii()
        dc.assert_no_leakage(out)

    def test_ascii_comment_strips_arabic(self):
        assert dc.ascii_comment("محمد علي", fallback="HobeRadius DATA") == "HobeRadius DATA"
        assert dc.ascii_comment("Ali (home)") == "Ali (home)"

    def test_injection_in_username_rejected(self):
        for bad in ['a"b', "a\nb", "a;b", "a\\b"]:
            with pytest.raises(dc.DataConnectionError):
                dc.render_pptp_client(host=SUBDOMAIN, username=bad,
                                      password="pw", comment="c")

    def test_non_ascii_username_rejected(self):
        with pytest.raises(dc.DataConnectionError):
            dc.render_sstp_client(host=SUBDOMAIN, username="مستخدم",
                                  password="pw", comment="c", version=6)


# ════════════════════════════════════════════════════════════════════════
# (2) حارس التسرّب + الوجهة = النطاق الفرعي فقط
# ════════════════════════════════════════════════════════════════════════
class TestLeakageGuard:

    @pytest.mark.parametrize("leak", [
        "10.99.0.1", "10.98.0.1", "10.51.0.1", "10.10.0.1",
        "chr-server", "via proxy",
    ])
    def test_guard_catches_internal(self, leak):
        with pytest.raises(dc.DataConnectionError):
            dc.assert_no_leakage(f'/interface pptp-client add connect-to={leak}')

    def test_clean_script_passes(self):
        dc.assert_no_leakage(f"connect-to={SUBDOMAIN} address=10.60.0.5/32")

    def test_target_is_subdomain_only(self):
        """لا يظهر أي مضيف غير النطاق الفرعي المُهيَّأ في أي سكربت."""
        scripts = [
            dc.render_sstp_client(host=SUBDOMAIN, username="u", password="p",
                                  comment="c", version=6),
            dc.render_pptp_client(host=SUBDOMAIN, username="u", password="p",
                                  comment="c"),
            dc.render_wireguard_client(host=SUBDOMAIN, wg_port=51821,
                                       client_private_key="a==", server_public_key="b==",
                                       assigned_ip="10.60.0.5", comment="c"),
        ]
        for s in scripts:
            # المضيف الوحيد المسموح به هو النطاق الفرعي.
            assert ".hoberadius.com" in s
            assert s.count("hoberadius.com") == s.count(SUBDOMAIN)
            assert "10.99." not in s and "10.98." not in s
            assert "10.51." not in s and "10.10." not in s


# ════════════════════════════════════════════════════════════════════════
# (3) vps_accel = Filter-Id 5 ميجابت فقط (إعادة تثبيت من 2a)
# ════════════════════════════════════════════════════════════════════════
class TestAccelReplyContract:

    def test_only_filter_id_5mbit(self):
        from app.radius.core.types import Subscriber
        from app.radius.services import accel_attributes as aa
        sub = Subscriber(id=1, username="u", password="p", tenant_id=1,
                         transport="vps_accel")
        rows = aa.accel_reply_attrs(sub, None)
        assert rows == [("Filter-Id", "=", "5120")]
        names = [a for a, _o, _v in rows]
        for forbidden in ("Mikrotik-Rate-Limit", "Session-Octets-Limit",
                          "Acct-Interim-Interval"):
            assert forbidden not in names


# ════════════════════════════════════════════════════════════════════════
# (4) DB-backed — تخصيص مجمّع WG + التزويد الكامل (render-time)
# ════════════════════════════════════════════════════════════════════════
@pytest.fixture
def app_ctx(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "data_connection.db")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", db_file)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.setenv("HOBERADIUS_CLIENT_SUBDOMAIN", SUBDOMAIN)
    monkeypatch.setenv("HOBERADIUS_DATA_WG_PUBKEY", "SERVERPUBKEYbase64data00000000000000000000000=")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    from app.radius.db.connection import reset_for_tests
    reset_for_tests(db_file)
    from app import create_app
    flask_app = create_app()
    with flask_app.app_context():
        from app.radius.db.migrations_runner import run_pending_migrations
        from app.radius.db.repos import tenants_repo
        run_pending_migrations()
        tenants_repo.ensure_default_tenant()
        yield flask_app


def _make_subscriber(username="sub1", password="pw1", full_name="محمد علي"):
    from app.radius.core.types import Subscriber
    from app.radius.db.repos import subscribers_repo
    sub = Subscriber(id=None, username=username, password=password, tenant_id=1,
                     full_name=full_name, status="enabled")
    saved = subscribers_repo.upsert_subscriber(sub)
    return saved


class TestWgPoolAllocation:

    def test_allocates_in_pool_skips_used_and_server(self, app_ctx):
        from app.radius.services import data_connection_wg as dcwg
        from app.radius.db.repos import data_connection_wg_peers_repo as repo
        import ipaddress
        ip1 = dcwg.allocate_pool_ip(1)
        pool = dcwg.data_wg_pool()
        assert ipaddress.ip_address(ip1) in pool
        assert ip1 != str(pool.network_address + 1)        # يتخطّى عنوان الخادم
        # احجزه ثم تأكّد أن التالي مختلف
        repo.create_peer(tenant_id=1, subscriber_id=1, username="u",
                         public_key="k", assigned_ip=ip1,
                         endpoint_host=SUBDOMAIN, endpoint_port=51821)
        ip2 = dcwg.allocate_pool_ip(1)
        assert ip2 != ip1
        for ip in (ip1, ip2):
            assert not ip.startswith(("10.99.", "10.98.", "10.51.", "10.10."))


class TestProvisionRenderTime:

    def test_v6_sstp_provision(self, app_ctx):
        from app.radius.services import data_connection_provision as dcp
        from app.radius.db.repos import freeradius_repo, subscribers_repo
        sub = _make_subscriber()
        res = dcp.provision_data_connection(tenant_id=1, subscriber_id=int(sub.id),
                                            version=6, protocol="sstp")
        assert res.version == 6 and res.protocol == "sstp"
        assert res.target_host == SUBDOMAIN
        assert res.script.isascii()
        assert "sstp-client" in res.script and SUBDOMAIN in res.script
        dc.assert_no_leakage(res.script)
        # الحساب صار vps_accel و reply = Filter-Id فقط
        reloaded = subscribers_repo.get_subscriber(1, "sub1")
        assert reloaded.transport == "vps_accel"
        reply = freeradius_repo.list_user_reply(1, "sub1")
        attrs = [(r["attribute"], r["value"]) for r in reply]
        assert ("Filter-Id", "5120") in attrs
        assert all(a != "Mikrotik-Rate-Limit" for a, _v in attrs)

    def test_v6_pptp_provision(self, app_ctx):
        from app.radius.services import data_connection_provision as dcp
        sub = _make_subscriber(username="sub2")
        res = dcp.provision_data_connection(tenant_id=1, subscriber_id=int(sub.id),
                                            version=6, protocol="pptp")
        assert res.protocol == "pptp"
        assert "pptp-client" in res.script and SUBDOMAIN in res.script
        assert res.script.isascii()
        dc.assert_no_leakage(res.script)

    def test_v7_wireguard_provision(self, app_ctx):
        from app.radius.services import data_connection_provision as dcp
        from app.radius.db.repos import data_connection_wg_peers_repo as repo
        sub = _make_subscriber(username="sub3")
        res = dcp.provision_data_connection(tenant_id=1, subscriber_id=int(sub.id),
                                            version=7)
        assert res.version == 7 and res.protocol == "wireguard"
        assert res.script.isascii()
        assert f"endpoint-address={SUBDOMAIN}" in res.script
        dc.assert_no_leakage(res.script)
        peers = repo.list_peers(1, subscriber_id=int(sub.id))
        assert len(peers) == 1
        # المفتاح الخاص للعميل لا يُخزَّن — العمود غير موجود أصلًا
        assert "private" not in peers[0]
        assert peers[0]["applied_to_vps"] == 0    # LAB-PENDING
        assert peers[0]["queue_applied"] == 0     # LAB-PENDING

    def test_v6_requires_protocol(self, app_ctx):
        from app.radius.services import data_connection_provision as dcp
        sub = _make_subscriber(username="sub4")
        with pytest.raises(dc.DataConnectionError):
            dcp.provision_data_connection(tenant_id=1, subscriber_id=int(sub.id),
                                          version=6, protocol="")

    def test_missing_subdomain_errors(self, app_ctx, monkeypatch):
        from app.radius.services import data_connection_provision as dcp
        monkeypatch.delenv("HOBERADIUS_CLIENT_SUBDOMAIN", raising=False)
        sub = _make_subscriber(username="sub5")
        with pytest.raises(dc.DataConnectionError):
            dcp.provision_data_connection(tenant_id=1, subscriber_id=int(sub.id),
                                          version=6, protocol="sstp")

    def test_v7_requires_server_pubkey(self, app_ctx, monkeypatch):
        from app.radius.services import data_connection_provision as dcp
        monkeypatch.delenv("HOBERADIUS_DATA_WG_PUBKEY", raising=False)
        sub = _make_subscriber(username="sub6")
        with pytest.raises(dc.DataConnectionError):
            dcp.provision_data_connection(tenant_id=1, subscriber_id=int(sub.id),
                                          version=7)


class TestPortalRenderTime:
    """يرندر صفحة المشترك فعليًّا عبر test client ويتحقّق من ظهور التبويب
    والنموذج، ثم من توليد السكربت عبر POST وتنزيله."""

    def test_tab_and_form_render(self, app_ctx):
        sub = _make_subscriber(username="subr")
        client = app_ctx.test_client()
        with client.session_transaction() as s:
            s["portal_tenant_id"] = 1
            s["portal_subscriber_id"] = int(sub.id)
        html = client.get("/portal/subscriber").get_data(as_text=True)
        assert 'data-tab="data"' in html
        assert 'data-testid="dc-form"' in html
        assert "اتصال بيانات" in html

    def test_post_generates_and_downloads(self, app_ctx):
        sub = _make_subscriber(username="subp")
        client = app_ctx.test_client()
        with client.session_transaction() as s:
            s["portal_tenant_id"] = 1
            s["portal_subscriber_id"] = int(sub.id)
            s["_csrf_token"] = "testtoken"
        # توليد → redirect إلى #pane-data (نمرّر توكِن CSRF المطابق)
        r = client.post("/portal/subscriber/data-connection",
                        data={"version": "6", "protocol": "sstp",
                              "_csrf_token": "testtoken"})
        assert r.status_code in (302, 303)
        # الصفحة تعرض السكربت بعد العودة
        html = client.get("/portal/subscriber").get_data(as_text=True)
        assert 'data-testid="dc-script"' in html
        assert "sstp-client" in html
        # التنزيل يعيد ملف .rsc يحوي السكربت
        dl = client.get("/portal/subscriber/data-connection/download")
        assert dl.status_code == 200
        assert "attachment" in dl.headers.get("Content-Disposition", "")
        body = dl.get_data(as_text=True)
        assert "sstp-client" in body and SUBDOMAIN in body
        dc.assert_no_leakage(body)
