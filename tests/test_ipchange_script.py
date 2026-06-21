# -*- coding: utf-8 -*-
"""مولّد سكربت «تغيير الـIP» لمايكروتيك (Phase 3/4): SSTP + إعادة توجيه كامل
+ تنظيف + حماية + تحقّق + تراجع + استبعاد فيديو CDN + حارس عدم تسرّب
توكِن قالب خام/بنية داخليّة. وحدات نقيّة (بلا تطبيق). شغّل الملف وحده."""
from __future__ import annotations

import pytest

from app.radius.services import ip_change_script as ics
from app.radius.services import data_connection as dc


def _gen(**over):
    base = dict(server_host="vpn-7.hoberadius.net", public_ip="203.0.113.50",
               username="ipc_5521", password="Pw#Secret9", version=7,
               reference="r1", exclude_video=False, speed_mbps=100)
    base.update(over)
    return ics.generate(**base)


def test_apply_has_sstp_reroute_cleanup_protection_verify():
    ap = _gen()["apply_script"]
    # SSTP client إلى الخادم المُزوَّد
    assert '/interface sstp-client add' in ap and 'connect-to=vpn-7.hoberadius.net' in ap
    assert 'user="ipc_5521"' in ap
    # إعادة توجيه كامل الحركة عبر النفق + masquerade
    assert 'dst-address=0.0.0.0/0 gateway="hobe-ipchange-sstp"' in ap
    assert 'action=masquerade' in ap and 'out-interface="hobe-ipchange-sstp"' in ap
    # تنظيف الآثار السابقة أوّلًا
    assert 'sstp-client remove [find where name="hobe-ipchange-sstp"]' in ap
    # حماية: مسار مضادّ للحلقة لعنوان الخادم + رفع المسار الأصليّ + فيلسيف
    assert 'anti-loop' in ap and '203.0.113.50/32' in ap
    assert 'distance=2' in ap                      # المسار الأصليّ صار failsafe
    assert 'hobe-ipchange-failsafe' in ap and 'scheduler add' in ap
    # تحقّق: قياس IP خارجي قبل/بعد + مقارنة بالـIP المتوقَّع
    assert ics.IP_ECHO_URL in ap and 'hobeIpBefore' in ap and 'hobeIpAfter' in ap
    assert '$hobeIpAfter = "203.0.113.50"' in ap   # مقارنة بالـIP الجديد
    # استكشاف أخطاء
    assert '/ping' in ap and 'print detail' in ap


def test_rollback_removes_and_restores():
    rb = _gen()["rollback_script"]
    assert 'sstp-client remove [find where name="hobe-ipchange-sstp"]' in rb
    assert 'scheduler remove' in rb
    assert 'mangle remove' in rb and 'nat remove' in rb and 'route remove' in rb
    assert 'address-list remove' in rb
    # يُعيد مسافة المسار الأصليّ إلى 1
    assert 'distance=1' in rb


def test_video_exclusion_routes_only_cdn_hosts():
    ap = _gen(exclude_video=True)["apply_script"]
    # كل مضيف CDN فيديو في address-list
    for host, _svc in ics.VIDEO_CDN_HOSTS:
        assert f'address-list add list="hobe-ipchange-video" address={host}' in ap
    # mark-routing لتجاوز النفق مبنيّ على address-list الفيديو فقط
    assert 'dst-address-list="hobe-ipchange-video"' in ap
    assert 'action=mark-routing' in ap and 'new-routing-mark="hobe-ipchange-bypass"' in ap
    # مسار التجاوز عبر WAN لحركة الفيديو فقط (routing-mark)، مع بقاء المسار
    # الافتراضيّ العامّ عبر النفق (بقيّة الحركة تُنفَّق)
    assert 'routing-mark="hobe-ipchange-bypass"' in ap
    assert 'dst-address=0.0.0.0/0 gateway="hobe-ipchange-sstp"' in ap


def test_no_video_block_when_disabled():
    ap = _gen(exclude_video=False)["apply_script"]
    # لا «إضافة» لقائمة الفيديو (الإزالة في التنظيف مسموحة فقط)
    assert 'address-list add list="hobe-ipchange-video"' not in ap
    assert 'dst-address-list="hobe-ipchange-video"' not in ap
    assert 'new-routing-mark="hobe-ipchange-bypass"' not in ap


def test_ipv4_host_disables_cert_verify():
    ap = _gen(server_host="198.51.100.7")["apply_script"]
    assert 'verify-server-certificate=no' in ap
    # اسم نطاق → يبقى التحقّق من الشهادة
    ap2 = _gen(server_host="vpn.hoberadius.net")["apply_script"]
    assert 'verify-server-certificate=yes' in ap2


def test_no_raw_template_token_leak():
    out = _gen(exclude_video=True)
    # الحارس يمرّ على السكربتين دون استثناء (وإلا generate يرفع)
    ics.assert_no_raw_token(out["apply_script"])
    ics.assert_no_raw_token(out["rollback_script"])
    # حسّاسيّة الحارس: توكِن خام يُكتَشف
    with pytest.raises(dc.DataConnectionError):
        ics.assert_no_raw_token('/ip route add gateway={server_ip}')
    with pytest.raises(dc.DataConnectionError):
        ics.assert_no_raw_token('connect-to=<host>')
    # لا يُنذر زورًا على بناء RouterOS الشرعيّ (do={ ... } / on-error={})
    ics.assert_no_raw_token(':if (true) do={\n  :put "ok"\n} on-error={}')


def test_no_internal_leakage():
    out = _gen()
    dc.assert_no_leakage(out["apply_script"])
    dc.assert_no_leakage(out["rollback_script"])


def test_injection_in_credentials_rejected():
    for bad in ['a"b', "a\nb", "a;b", "a\\b"]:
        with pytest.raises(dc.DataConnectionError):
            _gen(username=bad)
        with pytest.raises(dc.DataConnectionError):
            _gen(password=bad)


def test_missing_provision_raises():
    with pytest.raises(dc.DataConnectionError):
        ics.generate(server_host="", public_ip="", username="", password="")
