"""MT115 — اللوحة تقرأ منفذ الإدارة من الراوتر بدل افتراض القياسيّ.

الحادثة: فُتح وصولٌ بعيد لوين بوكس فلم يتّصل، ولا رسالة خطأ. السبب أنّ
منفذ وين بوكس على «الأشقر» هو 9090 وعلى «هوم» 1111 — ولا واحدٌ منهما
8291. والخانة في الواجهة كانت مملوءةً سلفًا بـ8291، فتُرسَل دائمًا ولا
يُكتشَف شيء: رابطٌ يُبنى إلى بابٍ لا أحد يسمع عنده.

القاعدة الآن:
  • خانة فارغة  ⇒ اقرأ المنفذ من ``/ip service`` واستعمله.
  • رقمٌ مكتوب  ⇒ يحكم (المشغّل يعرف أكثر، أو القراءة متعذّرة).
  • تعذّرت القراءة ⇒ القياسيّ + تنبيهٌ صريح، لا صمت. منعُ الفتح عقابٌ على
    عطبٍ لا يملكه المشغّل.
"""

import pytest

from app.radius.services import router_remote_access as ra


class _Res:
    def __init__(self, ok, data=None, error=""):
        self.ok, self.data, self.error = ok, data or [], error


@pytest.fixture()
def router(monkeypatch):
    """راوترٌ واحد (id=2) وجدولُ خدماته قابلٌ للتشكيل."""
    state = {"services": [], "ok": True}

    from app.radius.db.repos import nas_repo
    monkeypatch.setattr(nas_repo, "list_nas",
                        lambda tid: [{"id": 2, "address": "10.50.0.3",
                                      "tenant_id": tid}])

    from app.radius.services import mikrotik_admin_client as mac
    monkeypatch.setattr(
        mac, "fetch_cached",
        lambda **kw: _Res(state["ok"], state["services"],
                          "" if state["ok"] else "timed out"))
    return state


def test_reads_the_real_winbox_port(router):
    """جوهر الإصلاح: 9090 لا 8291."""
    router["services"] = [
        {"name": "www", "port": "8880", "disabled": "false"},
        {"name": "winbox", "port": "9090", "disabled": "false"},
    ]
    assert ra.detect_service_port(1, 2, "winbox") == (9090, "router")


def test_picks_the_right_service_not_the_first_row(router):
    """`www` كان يُلتقط بدل `winbox` فيُبنى الرابط إلى منفذ الويب."""
    router["services"] = [
        {"name": "www", "port": "8880", "disabled": "false"},
        {"name": "api", "port": "8728", "disabled": "false"},
        {"name": "winbox", "port": "1111", "disabled": "false"},
    ]
    assert ra.detect_service_port(1, 2, "winbox")[0] == 1111


def test_disabled_service_is_flagged_not_hidden(router):
    """منفذٌ صحيحٌ لخدمةٍ مُغلقة = رابطٌ لا يتّصل. يُقال للمشغّل."""
    router["services"] = [{"name": "winbox", "port": "9090",
                           "disabled": "true"}]
    port, source = ra.detect_service_port(1, 2, "winbox")
    assert (port, source) == (9090, "disabled")


def test_unreachable_router_falls_back_and_says_so(router):
    """الارتداد مقصود: عطبُ القراءة لا يمنع فتح وصولٍ عاجل."""
    router["ok"] = False
    assert ra.detect_service_port(1, 2, "winbox") == (8291, "default")


def test_missing_router_falls_back(router, monkeypatch):
    from app.radius.db.repos import nas_repo
    monkeypatch.setattr(nas_repo, "list_nas", lambda tid: [])
    assert ra.detect_service_port(1, 2, "winbox") == (8291, "default")


def test_out_of_range_port_is_refused(router):
    """رقمٌ مشوّه على الراوتر لا يُبنى عليه رابط."""
    router["services"] = [{"name": "winbox", "port": "0", "disabled": "false"}]
    assert ra.detect_service_port(1, 2, "winbox") == (8291, "default")


@pytest.mark.parametrize("service, ros_name, port", [
    ("ssh", "ssh", 2222),
    ("web", "www", 8880),
    ("web_ssl", "www-ssl", 4443),
])
def test_other_services_map_to_their_routeros_names(router, service,
                                                    ros_name, port):
    """`web` في نظامنا اسمه `www` في RouterOS — خطأ الاسم = ارتدادٌ صامت."""
    router["services"] = [{"name": ros_name, "port": str(port),
                           "disabled": "false"}]
    assert ra.detect_service_port(1, 2, service) == (port, "router")


def test_empty_field_means_detect_not_8291():
    """حارسٌ نصّيّ: عودة `SERVICE_PORTS[service]` هنا تُعيد الرابط الميّت."""
    import inspect
    src = inspect.getsource(ra.open_session)
    i = src.find("dst_port).strip()")
    assert i > 0
    window = src[i:i + 300]
    assert "detect_service_port" in window, "الخانة الفارغة لا تُكتشف"


def test_the_form_no_longer_prefills_8291():
    """خانةٌ مملوءةٌ سلفًا تُرسَل دائمًا فلا يُكتشَف شيء أبدًا."""
    from pathlib import Path
    root = Path(ra.__file__).resolve().parents[2] / "templates" / "radius"
    for name in ("sstp_credentials.html", "wg_details.html"):
        html = (root / name).read_text(encoding="utf-8")
        i = html.find('name="dst_port"')
        assert i > 0, name
        assert 'value="8291"' not in html[i - 200:i + 200], name
