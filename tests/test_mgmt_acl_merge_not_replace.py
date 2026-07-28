"""MT74 — سكربت التهيئة لا يَقطع WinBox عن الفنّيّ.

شكوى المالك (2026-07-28): «السكربت بيقفل اتصال المايكروتيك من وين بوكس
ع ايبيهات معينة فبصير الشخص مش قادر يفوت من وين بوكس عن طريق الاي بي».

السبب: ``/ip service set <svc> address=`` **يَستبدل** القائمة، والقائمة
المُولَّدة تحوي بوّابات النفق وحدها — فتُمحى عناوين الفنّيّ.

العقد الآن (قرار المالك: خيار «دمج ما هو مسموحٌ حاليًّا»):
  • دمجٌ لا استبدال — كلّ عنوانٍ مسموحٍ سلفًا يبقى.
  • **قائمةٌ فارغة تعني «للجميع» في RouterOS** ⇒ تُترك كما هي، وإلّا
    حوّلنا المفتوح إلى مقيَّد وطردنا الجميع (نفس العطب بوجهٍ آخر).
  • idempotent: لا يُضاف عنوانٌ موجود.
"""
from __future__ import annotations

from app.radius.services.mgmt_acl import MGMT_SERVICES, service_lockdown_lines

GW = "10.10.10.1"
WG = "10.20.0.0/24"


def _script():
    return "\n".join(service_lockdown_lines(sstp_gateway_ip=GW, wg_subnet=WG))


def test_never_emits_a_bare_replacing_set():
    """🔴 جوهر العطب: `address=<قائمة ثابتة>` يَمحو عناوين الفنّيّ."""
    for line in _script().splitlines():
        stripped = line.strip()
        if stripped.startswith("/ip service set"):
            assert "address=$c" in stripped, (
                "استبدالٌ صريح ما زال موجودًا: " + stripped)


def test_reads_current_value_before_writing():
    s = _script()
    for svc in MGMT_SERVICES:
        assert f"[/ip service get {svc} address]" in s, svc


def test_empty_list_is_left_untouched():
    """فارغة = مسموحٌ للجميع ⇒ لا نُقيّدها فنطرد الجميع."""
    s = _script()
    for svc in MGMT_SERVICES:
        assert f"[/ip service get {svc} address]; :if ([:len $c{svc[:3]}] > 0)" in s, svc


def test_addition_is_idempotent():
    """لا يُضاف عنوانٌ موجودٌ سلفًا (تشغيلٌ متكرّر لا يُضخّم القائمة)."""
    s = _script()
    assert s.count(':typeof [:find $c') >= len(MGMT_SERVICES)
    assert 'do={ :set c' in s


def test_both_gateways_are_still_granted():
    """لا انحدار أمنيّ: بوّابتا الإدارة ما زالتا تُمنحان."""
    s = _script()
    assert GW in s and WG in s


def test_covers_every_management_service():
    s = _script()
    for svc in MGMT_SERVICES:
        assert f"/ip service set {svc} address=$c{svc[:3]}" in s, svc
