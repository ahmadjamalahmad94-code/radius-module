# -*- coding: utf-8 -*-
"""تحصين إقلاع خيوط الخلفية (_start_workers).

العَرَض في الإنتاج: التشخيص عند الطلب يكشف انقطاع ccr3، لكنّ المراقب الخلفي لا
يُحدّث الحالة ولا يُطلق تنبيهًا. الجذر المُحتمَل: كانت كل بدايات الـworkers ملفوفة
بـtry/except واحد جامع، فإن فشل بدء worker سابق (config/شبكة على خادم الإنتاج)
قفز التنفيذ خارج الكتلة ومنع device_health_poll_worker (الذي كان متأخّرًا) من
البدء أصلًا → لا كنس → حالة قديمة + لا تنبيه.

يثبّت أنّ فشل بدء worker واحد لا يمنع device_health من البدء.
"""
from __future__ import annotations

import os
import sys
import tempfile

import pytest


@pytest.fixture()
def app():
    tmp = tempfile.mkdtemp(prefix="hr_worker_boot_")
    os.environ.update(
        HOBERADIUS_DB_PATH=os.path.join(tmp, "t.db"),
        HOBERADIUS_NO_WORKER="1", HOBERADIUS_NO_SEED="1", FLASK_SECRET="k")
    for key in list(sys.modules):
        if key.startswith("app."):
            del sys.modules[key]
    from app.radius.db.connection import reset_for_tests
    reset_for_tests(os.environ["HOBERADIUS_DB_PATH"])
    from app import create_app
    created = create_app()           # NO_WORKER=1 ⇒ الإنشاء لا يبدأ خيوطًا
    yield created
    for key in list(sys.modules):
        if key.startswith("app."):
            del sys.modules[key]


# أسماء كل بدايات الـworkers التي يلمسها _start_workers (تُرقَّع لتفادي خيوط حقيقية).
_WORKER_STARTS = [
    "start_sync_worker", "start_accounting_puller", "start_stale_session_reaper",
    "start_device_fingerprint_worker", "start_lifecycle_worker",
    "start_admin_bridge_sync_worker", "start_mt_reconciler",
    "start_backup_scheduler_worker", "start_dunning_worker",
    "start_temp_speed_expiry", "start_loop_probe_poller",
    "start_store_chat_reminder_worker",
]


def test_one_failing_worker_does_not_block_device_health(app, monkeypatch):
    import app.workers as workers
    import app.webhooks.queue_worker as qw
    import app.workers.setup_wizard_tentative_reclaimer_worker as sw1
    import app.workers.setup_wizard_radius_reconciler_worker as sw2

    calls: list[str] = []
    # رقّع كل البدايات إلى لا-عمل كي لا تُشغّل خيوطًا حقيقية أثناء الاختبار.
    for name in _WORKER_STARTS:
        monkeypatch.setattr(workers, name, lambda *a, **k: None, raising=False)
    monkeypatch.setattr(qw, "start_worker_once", lambda *a, **k: None, raising=False)
    monkeypatch.setattr(sw1, "start_setup_wizard_tentative_reclaimer",
                        lambda *a, **k: None, raising=False)
    monkeypatch.setattr(sw2, "start_setup_wizard_radius_reconciler",
                        lambda *a, **k: None, raising=False)

    # worker سابق يفشل عند البدء (يحاكي عطل config/شبكة على الإنتاج).
    def _boom(*a, **k):
        raise RuntimeError("simulated prod worker start failure")
    monkeypatch.setattr(workers, "start_mt_reconciler", _boom, raising=False)
    # device_health يُسجّل أنّه بدأ.
    monkeypatch.setattr(workers, "start_device_health_poll_worker",
                        lambda *a, **k: calls.append("device_health"), raising=False)

    # ارفع بوّابتي التخطّي كي يَجري _start_workers فعلًا داخل الاختبار.
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.delenv("HOBERADIUS_NO_WORKER", raising=False)

    from app import _start_workers
    _start_workers(app)              # يجب ألّا يرفع، ويجب أن يبدأ device_health

    assert "device_health" in calls, (
        "device_health_poll_worker لم يبدأ رغم فشل worker آخر — التحصين لم يعمل")


def test_device_health_starts_before_other_workers(app, monkeypatch):
    """أولوية تشغيلية: مراقبة انقطاع الراوترات تبدأ أوّلًا (لا يؤخّرها/يمنعها غيرها)."""
    import app.workers as workers
    import app.webhooks.queue_worker as qw
    import app.workers.setup_wizard_tentative_reclaimer_worker as sw1
    import app.workers.setup_wizard_radius_reconciler_worker as sw2

    order: list[str] = []
    for name in _WORKER_STARTS:
        monkeypatch.setattr(workers, name,
                            (lambda nm: (lambda *a, **k: order.append(nm)))(name),
                            raising=False)
    monkeypatch.setattr(qw, "start_worker_once", lambda *a, **k: None, raising=False)
    monkeypatch.setattr(sw1, "start_setup_wizard_tentative_reclaimer",
                        lambda *a, **k: None, raising=False)
    monkeypatch.setattr(sw2, "start_setup_wizard_radius_reconciler",
                        lambda *a, **k: None, raising=False)
    monkeypatch.setattr(workers, "start_device_health_poll_worker",
                        lambda *a, **k: order.append("device_health"), raising=False)
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.delenv("HOBERADIUS_NO_WORKER", raising=False)

    from app import _start_workers
    _start_workers(app)
    assert order and order[0] == "device_health"
