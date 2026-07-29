"""MT89 — كتلة الجدار في سكربت الربط: سماحاتٌ فقط، وبلا قاعدةٍ زائدة.

طلب المالك: «أيّ فيروول فلتر مش ضروري ولا أساسي نشيله خوفًا من مشاكل بالربط».

الخلفيّة التي تجعل هذا حسّاسًا: كتلتنا تُرفَع إلى **رأس** كلّ سلسلة، أي فوق
قواعد الزبون وفوق قواعد الهوت سبوت الديناميكيّة. فكلّ قاعدةٍ نضيفها هنا
تتقدّم على ما كتبه الزبون بنفسه — لذا كلّ قاعدةٍ يجب أن تكون ضروريّةً
بالبرهان لا بالاحتياط.

أُزيلت اثنتان:
  • «06 ICMP diag»       — بِنجٌ مفتوح للإنترنت كلّه، وتشخيصنا مُغطًّى مرّتين
                            (02 يقبل كلّ شيء من النفق، 03 من خادم RADIUS).
  • «13 to RADIUS server» — مكرّرة: radius_ip يُضاف دائمًا للحديقة المسوّرة
                            و11 تقبل كلّ ما يقصدها.

وأُبقيت البقيّة عمدًا: نزعها هو ما يُحدث «مشاكل بالربط» فعلًا.
"""

import pytest

from app.radius.services.router_onboarding_script import (
    OnboardingParams, build_onboarding_script, firewall_rule_order, FW_TAG,
)


def _script(**over) -> str:
    base = dict(
        router_name="cafe", router_id=3, accel_host="187.77.70.18",
        sstp_port=443, tunnel_user="rtr-cafe", tunnel_password="Uniq-Pw-abc1",
        tunnel_ip="10.50.0.5", radius_ip="10.50.0.1",
        radius_secret="per-nas-secret-9931", api_user="hobe-api",
        api_password="apipw", walled_garden=[],
        block_page_url="http://203.0.113.9/p/expired")
    base.update(over)
    return build_onboarding_script(OnboardingParams(**base))


def _our_rules(script: str) -> list:
    return [ln for ln in script.splitlines()
            if "/ip firewall filter add" in ln and FW_TAG in ln]


def test_every_rule_we_add_is_an_accept():
    """الكتلة لا تحجب شيئًا — لا drop ولا reject ولا tarpit.

    قاعدة الحجب الوحيدة التي كانت هنا («expired pool reject») كسرت البوّابة
    الأسيرة فعلًا: تُرفَع فوق قواعد الهوت سبوت فتُسقط حركة العميل قبل أن
    تعترضه صفحة الدخول. الانتهاء يُنفَّذ عبر RADIUS لا عبر النار.
    """
    rules = _our_rules(_script())
    assert rules, "لم تُولَّد أيّ قاعدة — تغيّر شيءٌ جوهريّ"
    for ln in rules:
        assert "action=accept" in ln, ln
        for bad in ("action=drop", "action=reject", "action=tarpit"):
            assert bad not in ln, f"حجبٌ تسلّل إلى كتلة السماحات: {ln}"


def test_no_blanket_icmp_accept():
    """بِنجٌ من الإنترنت كلّه = تعرّضٌ بلا مقابل، وتشخيصنا مُغطًّى مرّتين."""
    for ln in _our_rules(_script()):
        if "protocol=icmp" in ln:
            assert ("in-interface=" in ln or "src-address=" in ln), \
                f"ICMP مقبول من أيّ مصدر: {ln}"


def test_no_duplicate_radius_forward_rule():
    """`dst-address=<radius>` في forward يُطابق ما طابقته الحديقة المسوّرة."""
    order = firewall_rule_order(_script())
    assert not any("to RADIUS server" in c for c in order), \
        "قاعدة RADIUS المكرّرة عادت إلى الكتلة"


def test_radius_ip_is_still_reachable_via_walled_garden():
    """شرط سلامة الحذف السابق: الحديقة المسوّرة تحوي خادم RADIUS دائمًا."""
    s = _script(radius_ip="10.50.0.1")
    assert 'list="hr-walled-garden" address=10.50.0.1' in s
    assert any("walled-garden allow" in c for c in firewall_rule_order(s))


@pytest.mark.parametrize("essential", [
    "01 established", "02 mgmt SSTP iface", "03 from RADIUS",
    "04 DNS to router", "10 established", "11 walled-garden allow",
    "12 mgmt tunnel", "14 DNS forward",
])
def test_essential_rules_are_never_dropped(essential):
    """حارسٌ ضدّ حماسٍ زائد في التنظيف: نزع أيٍّ من هذه يَقطع الربط فعلًا —
    مسار الإدارة، أو صفحة التجديد، أو DNS العملاء."""
    assert any(essential in c for c in firewall_rule_order(_script())), \
        f"قاعدةٌ أساسيّة اختفت: {essential}"


def test_managed_block_is_removed_before_rebuild():
    """إعادة اللصق لا تُراكم قواعد — وإلّا امتلأ رأس السلسلة بنسخٍ مكرّرة."""
    s = _script()
    assert f'/ip firewall filter remove [find comment~"^{FW_TAG}"]' in s


# ── MT96: عطبان أبلغ عنهما المالك في راوترٍ حقيقيّ ────────────────────

def test_managed_services_are_enabled_not_only_restricted():
    """«الراوتر متصل لكنّ اللوحة لا تُديره» — لأنّ api كانت **معطَّلة**.

    `/ip service set <svc> address=…` تضبط القائمة على خدمةٍ معطَّلة بلا أن
    تُفعّلها، فيخرج السكربت ناجحًا والعطب لا يظهر إلا حين تحاول الإدارة.
    التقييد أدناه يَقصر الخدمة على النفق، فالتفعيل هنا لا يفتح شيئًا.
    """
    s = _script()
    assert "/ip service enable" in s, "الخدمات المُدارة تُقيَّد ولا تُفعَّل"
    enable_line = next(l for l in s.splitlines() if l.startswith("/ip service enable"))
    for svc in ("winbox", "api", "www"):
        assert svc in enable_line, f"{svc} غير مُفعَّلة: {enable_line}"


def test_hotspot_state_is_printed_before_flipping_use_radius():
    """قلبُ مصادقة راوترٍ يخدم زبائن بلا إظهار ما يُمَسّ = مفاجأةٌ صامتة."""
    s = _script()
    lines = s.splitlines()
    flip = next(i for i, l in enumerate(lines)
                if "use-radius=yes" in l and "hotspot profile" in l)
    before = "\n".join(lines[:flip])
    assert "/ip hotspot user find" in before, "لا جرد للمستخدمين المحلّيّين قبل القلب"
    assert "/ppp secret find" in before, "لا جرد لأسرار PPP قبل القلب"
    assert ":put" in before


def test_inventory_lines_survive_a_line_by_line_paste():
    """كلّ سطرٍ يُنفَّذ في نطاقه: لا `:local` في أسطر الجرد."""
    for line in _script().splitlines():
        if ":put (\"[hr]" in line:
            assert ":local" not in line, f"متغيّرٌ يخرج عن نطاقه: {line[:70]}"
