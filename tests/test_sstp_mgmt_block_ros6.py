"""كتلةُ نفق الإدارة SSTP يجب أن تعمل على RouterOS 6 — لا على 7 وحدَه.

🔴 وقع حيًّا (2026-08-29): `mt_provisioner.render_sstp_mgmt_block` كان يكتب
`port=443` على `/interface sstp-client`. و**RouterOS 6 لا يملك هذه الخاصّيّة
أصلًا**، فيسقط الأمر بـ«expected end of command (line 1 column 70)» — عند
حرف `port` بالضبط. وكان الأمرُ الوحيد الفاشل في السكربت كلِّه.

والأسوأ أنّ الكتلة تكتب `remove` قبل `add` لتكون قابلةً للّصق مرارًا: فنجح
الحذفُ وفشلت الإضافة ⇒ سقط نفقُ الإدارة ولم يُعَد، وانقطع الرديوس عن مقهًى
يعمل حتى أُصلح يدويًّا. والكتلةُ نفسُها تُعنون نفسَها «RouterOS 6.x».

`connect-to=IP:PORT` يعمل على 6 و7 معًا — وهكذا يُخزّنه v6 نفسُه.
"""
from __future__ import annotations

from app.radius.services.mt_provisioner import render_sstp_mgmt_block


def _block(**kw):
    base = dict(nas_name="MT2", accel_host="187.77.70.18",
                username="rtr-MT2", password="pw123")
    base.update(kw)
    return render_sstp_mgmt_block(**base)


def test_no_bare_port_property_on_sstp_client():
    """🔴 الانحدار بعينه: `port=` على sstp-client يقتل الأمر على v6."""
    body = _block()
    add = [l for l in body.splitlines() if "sstp-client add" in l]
    assert add, body
    assert " port=" not in add[0], (
        "RouterOS 6 لا يعرف `port` على sstp-client — ادمجه في connect-to")


def test_port_is_folded_into_connect_to():
    """المنفذ يبقى محفوظًا — لكن بالصيغة التي يقبلها الطرفان."""
    assert "connect-to=187.77.70.18:443" in _block()


def test_custom_port_survives():
    body = _block(port=4443)
    assert "connect-to=187.77.70.18:4443" in body
    assert " port=" not in [l for l in body.splitlines() if "sstp-client add" in l][0]


def test_remove_precedes_add_and_names_match():
    """قابليّةُ اللصق مرارًا تعتمد على أن يحذف الاسمَ نفسَه الذي يُضيفه."""
    body = _block()
    rm = [l for l in body.splitlines() if "sstp-client remove" in l][0]
    add = [l for l in body.splitlines() if "sstp-client add" in l][0]
    assert "hr-sstp-mgmt" in rm and "name=hr-sstp-mgmt" in add


def test_anti_flap_setting_is_kept():
    """الإعدادُ الذي يمنع رفرفةَ النفق لا يسقط مع إصلاح المنفذ."""
    assert "verify-server-address-from-certificate=no" in _block()
