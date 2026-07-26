"""MT66 — «مدير الشبكة» يَملك نطاق مايكروتيك، لا قائمة اللوحة وحدها.

العلّة: الصلاحيات مقسومة على **قائمتين** مستقلّتين — قائمة اللوحة في
``core.constants.ALL_PERMISSIONS`` ونطاق مايكروتيك في
``services.mt_permissions.ALL_PERMISSIONS`` (يُفحص بـ``requires_perm``).
كان الدور يأخذ الأولى فقط، فكانت صفحاتٌ **ظاهرةٌ في واجهة الزبون**
(التنبيهات، سجلّ مايكروتيك، أخطاء الهوتسبوت، مركز المشاكل، خريطة الشبكة،
مصفوفة الصلاحيات) تَرُدّ 403 عند النقر — أوّل انطباعٍ مكسور.

يَقفل هذا الاختبار الحدّين معًا: ما يجب أن يُمنَح، وما يجب ألّا يُمنَح.
"""
from __future__ import annotations

import os
import sys
import tempfile

import pytest


@pytest.fixture()
def app():
    d = tempfile.mkdtemp(prefix="mt66-")
    os.environ["HOBERADIUS_DB_PATH"] = os.path.join(d, "t.db")
    sys.path.insert(0, os.getcwd())
    from app import create_app
    return create_app()


def test_network_admin_gets_mikrotik_domain():
    """المفتاح الجذر: mikrotik.admin — ومنه تُشتقّ بقيّة صلاحيات النطاق."""
    from app.radius.db.repos.admins_repo import _network_admin_permissions
    from app.radius.services import mt_permissions as mp

    perms = _network_admin_permissions()
    assert mp.PERM_ADMIN in perms, "بلا mikrotik.admin تُرَدّ صفحات الراوتر 403"
    # سياسات الشبكة على راوتره هو — ولا دور فوقه داخل شبكته ليمنحه إيّاها.
    for p in (mp.PERM_NPC_REMOTE_ACCESS_APPLY, mp.PERM_NPC_WALLED_GARDEN_APPLY,
              mp.PERM_NPC_WEB_BLOCK_APPLY):
        assert p in perms, p


def test_provider_infrastructure_stays_out():
    """مخرج الـVPS بنيةُ المزوّد — لا يُمنَح لمدير شبكةٍ مستضافة."""
    from app.radius.db.repos.admins_repo import _network_admin_permissions
    from app.radius.services import mt_permissions as mp

    perms = _network_admin_permissions()
    for p in (mp.PERM_SITE_EXIT_APPLY,
              mp.PERM_SITE_EXIT_OVERRIDE_BACKUP_WARNING,
              mp.PERM_SITE_EXIT_ENABLE_RISKY_GROUPS):
        assert p not in perms, f"{p} يجب أن تبقى للمزوّد"


def test_panel_permissions_not_lost():
    """لا انحدار: كل قائمة اللوحة ما زالت ممنوحة."""
    from app.radius.core.constants import ALL_PERMISSIONS
    from app.radius.db.repos.admins_repo import _network_admin_permissions

    perms = set(_network_admin_permissions())
    missing = [p for p in ALL_PERMISSIONS if p not in perms]
    assert not missing, f"سقطت صلاحيات لوحة: {missing[:5]}"
    assert len(perms) == len(set(perms)), "تكرار في القائمة"


def test_seeded_role_resolves_to_real_mikrotik_access(app):
    """الاختبار الحقيقيّ: الدور المبذور في القاعدة يُنتج صلاحيات فعليّة."""
    from app.radius.core.constants import ROLE_NETWORK_ADMIN
    from app.radius.db.repos import admins_repo
    from app.radius.services import mt_permissions as mp

    with app.app_context():
        admins_repo.ensure_network_admin_role()
        role = admins_repo.get_role_by_name(ROLE_NETWORK_ADMIN)
        assert role is not None
        held = set(role.permissions or ())
        assert mp.PERM_ADMIN in held

        class _Admin:                     # DTO خفيف: ما يقرأه mt_permissions
            id = 4242
            username = "netadmin"
            role_id = role.id
            is_super_admin = False

        resolved = mp.admin_permissions(_Admin())
        # الصفحات التي كانت تَرُدّ 403 على الزبون الجديد:
        for p in (mp.PERM_VIEW,            # خريطة الشبكة
                  mp.PERM_DIAGNOSTICS,     # التنبيهات + مركز المشاكل
                  mp.PERM_AUDIT_VIEW):     # سجلّ مايكروتيك + مصفوفة الصلاحيات
            assert p in resolved, f"{p} غير محلولة رغم mikrotik.admin"
