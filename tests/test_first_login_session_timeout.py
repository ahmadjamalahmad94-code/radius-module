"""أوّلُ دخولٍ لبطاقةٍ يجب أن يحمل ``Session-Timeout``.

بلاغُ «عبد أبو هاشم» 2026-08-26: «البطاقات تتجاوز وقتها المسموح».

السلسلة: `authorize` يبني سماتِ الردّ **ثمّ** يختم نافذة البطاقة
(`_update_login_timestamps`). ومصدرُ `Session-Timeout` الوحيد لبطاقةٍ زمنيّةٍ
هو `expire_at` (باقاتُ الكروت المُرحَّلة بلا `session_timeout_sec` ولا
`duration_minutes`) — وهو **فارغٌ لحظةَ بناء الردّ** في أوّل دخول.

فيخرج القبولُ الأوّل بلا سقفٍ للجلسة، **فلا يقطع الراوترُ الزبونَ عند انتهاء
بطاقته**؛ يبقى متّصلًا حتى ينقطع من نفسه. والدخولُ الثاني يُصحَّح لأنّ
النافذةَ تكون قد خُتمت — ولذلك بدا العطبُ عشوائيًّا.
"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


def _fresh_app():
    tmp = tempfile.mkdtemp(prefix="hr_sto_")
    os.environ["HOBERADIUS_DB_PATH"] = os.path.join(tmp, "test.db")
    os.environ["HOBERADIUS_NO_WORKER"] = "1"
    os.environ["HOBERADIUS_NO_SEED"] = "1"
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]
    from app import create_app
    return create_app()


def _seed_card(username: str, password: str, hours: int) -> None:
    """بطاقةٌ **لم تُستعمل بعد** في حزمةٍ نافذتُها ``hours`` من أوّل اتّصال،
    وباقةٍ بلا سقفِ جلسةٍ صريح — كحال كلّ الباقات المُرحَّلة من adv."""
    from app.radius.db.connection import db
    db().execute(
        "INSERT INTO access_plans (id, tenant_id, name, speed_down_kbps, "
        "speed_up_kbps, session_timeout_sec, duration_minutes, created_at) "
        "VALUES (900, 1, 'ADV-Like', 2000, 2000, 0, 0, '2026-01-01T00:00:00Z')")
    db().execute(
        "INSERT INTO card_batches (id, tenant_id, batch_code, package_name, "
        "plan_id, time_value, time_unit, count_from_first_connect, "
        "count_by_seconds, created_at) "
        "VALUES (900, 1, 'B900', 'ساعاتٌ من أوّل اتّصال', 900, ?, 'hours', 1, 0, "
        "'2026-01-01T00:00:00Z')", (hours,))
    db().execute(
        "INSERT INTO cards (tenant_id, batch_id, username, password, plan_id, "
        "used, first_used_at, expire_at, created_at) "
        "VALUES (1, 900, ?, ?, 900, 0, NULL, NULL, '2026-01-01T00:00:00Z')",
        (username, password))
    db().commit()


def test_first_login_carries_session_timeout():
    """🔴 الانحدار: كان الردّ الأوّل بلا سقفٍ فلا يُقطع الزبونُ أبدًا."""
    app = _fresh_app()
    with app.app_context():
        from app.radius.services.policy_engine import AuthRequest, authorize
        _seed_card("CARD-STO", "pw-sto", hours=8)

        d = authorize(AuthRequest(username="CARD-STO", password="pw-sto",
                                  tenant_id=1,
                                  calling_station_id="AA:BB:CC:DD:EE:FF"))
        assert d.ok is True, d.reason
        sto = (d.reply_attrs or {}).get("Session-Timeout")
        assert sto, "أوّلُ دخولٍ بلا Session-Timeout ⇒ الراوترُ لن يقطعه"
        secs = int(sto)
        # النافذةُ ثماني ساعاتٍ تُختم الآن — نسمح بانحرافٍ دقيقتين.
        assert 8 * 3600 - 120 <= secs <= 8 * 3600 + 120, secs


def test_second_login_still_carries_remaining_time():
    """السلوكُ القائم محفوظ: الدخولُ التالي يحمل ما تبقّى لا المدّةَ كاملة."""
    app = _fresh_app()
    with app.app_context():
        from app.radius.services.policy_engine import AuthRequest, authorize
        _seed_card("CARD-STO2", "pw2", hours=8)
        req = AuthRequest(username="CARD-STO2", password="pw2", tenant_id=1,
                          calling_station_id="AA:BB:CC:DD:EE:01")
        first = authorize(req)
        assert first.ok is True
        second = authorize(req)
        assert second.ok is True, second.reason
        sto = int((second.reply_attrs or {}).get("Session-Timeout") or 0)
        assert 0 < sto <= 8 * 3600 + 120, sto
