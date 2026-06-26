"""port_script_services routes — خدمات السكربت المبنيّة على المنافذ.

تتبع حرفيًا نمط mt_programming (برمجة الهوتسبوت/البرودباند):
  GET  /admin/radius/mt/<id>/port-services
        → قائمة الخدمات + اكتشاف المنافذ + نموذج اختيار المنافذ.
  POST /admin/radius/mt/<id>/port-services/<slug>/plan
        → يُولّد سكربت الخدمة من المنافذ المختارة (للمراجعة).
  POST /admin/radius/mt/<id>/port-services/<slug>/apply
        → (Q2) يطبّق السكربت على الراوتر. معطّل ما دام القالب مبدئيًا.

الخدمتان المُسجَّلتان حاليًا (bt_wifi_block, loop_detect) تحملان قوالب
مبدئية — راجع app/radius/services/port_script_services.py: REGISTRY.
"""
from __future__ import annotations

import uuid

from flask import (
    Blueprint,
    abort,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)

from ..core.tenant import DEFAULT_TENANT_ID
from ..db.connection import db
from ..db.repos import (
    router_loop_checks_repo,
    router_loop_probes_repo,
    tenants_repo,
)
from ..services import mikrotik_admin_client as mac
from ..services import mt_programming as mtp
from ..services import port_script_services as pss
from ..services.audit import get_audit_service
from ..services.mt_permissions import PERM_PROGRAM, requires_perm
from ..services.nas_connection import resolve_connection_address


def _tid() -> int:
    return int(getattr(g, "tenant_id", DEFAULT_TENANT_ID))


# ─── حالة الخدمة المحفوظة (مفعّلة على مداخل X,Y / غير مفعّلة) ──────
#
# نُخزّنها في إعدادات المستأجر (tenant_settings) بمفتاح يجمع الراوتر
# والخدمة — لا حاجة لجدول جديد. لكل (راوتر، خدمة) مفتاحان:
#   pss.<nas_id>.<slug>.enabled  → "1"/"0"
#   pss.<nas_id>.<slug>.ports    → "ether2,ether3"
_STATE_TRUE = ("1", "true", "t", "on", "yes")


def _state_key(nas_id: int, slug: str, field: str) -> str:
    return f"pss.{nas_id}.{slug}.{field}"


# خدمات نظافة الشبكة دائمة الإتاحة (يونيو 2026، طلب المالك):
#   bt_wifi_block (منع البث) + loop_detect (تتبّع اللوب) لا تَخضعان لحارس
#   عامّ on/off — نَموذج «شبكة عامّة فقط» يَجعلهما مُلائمتين دومًا. التحكّم
#   الحقيقي per-interface (المداخل المُختارة) لا master toggle. لذا
#   ‎enabled لهاتين دائمًا True — أيّ قارئ خادمي (إن وُجد) يَراهما متاحتين
#   فينفّذ apply/deploy حسب المداخل. الـUI يُلوّن البطاقة من ports|length
#   لا من هذا العَلَم.
_ALWAYS_AVAILABLE_SLUGS = frozenset({"bt_wifi_block", "loop_detect"})


def _get_state(nas_id: int, slug: str) -> dict:
    ports_raw = tenants_repo.get_setting(
        _tid(), _state_key(nas_id, slug, "ports"), "")
    ports = [p for p in (ports_raw or "").split(",") if p]
    if slug in _ALWAYS_AVAILABLE_SLUGS:
        # دائمة الإتاحة — تَجاهل المخزَّن واعتَبرها enabled. الـports تَبقى
        # هي مَصدر حقيقة «هل يَنشر سكربتًا فعلًا؟».
        enabled = True
    else:
        enabled_raw = tenants_repo.get_setting(
            _tid(), _state_key(nas_id, slug, "enabled"), "0")
        enabled = str(enabled_raw or "").strip().lower() in _STATE_TRUE
    return {"enabled": enabled, "ports": ports}


def _set_state(nas_id: int, slug: str, *, enabled: bool,
               ports: list[str]) -> None:
    by = int(getattr(g, "admin_id", 0) or 0)
    tenants_repo.set_setting(
        _tid(), _state_key(nas_id, slug, "enabled"),
        "1" if enabled else "0", by=by)
    tenants_repo.set_setting(
        _tid(), _state_key(nas_id, slug, "ports"),
        ",".join(ports), by=by)


# ─── إعدادات الفحص الدوري للوب (لكل راوتر) ─────────────────────────
#
# نفس مخزن tenant_settings وبنفس عائلة المفاتيح pss.<nas>.<slug>.*:
#   pss.<nas>.loop_detect.poll_enabled  → "1"/"0" (افتراضي مفعّل)
#   pss.<nas>.loop_detect.poll_minutes  → فترة الفحص بالدقائق (افتراضي 5)
# يقرؤها loop_probe_poller كل دورة فيحترم تعطيل/فترة كل راوتر.
# الحد الأدنى 5 دقائق = دورة الـpoller نفسها (300s) — فترة أقصر لن
# تُنفَّذ أسرع من دقّة الدورة فلا نوهم المشغّل بدقة غير موجودة.
_POLL_MIN_MINUTES = 5
_POLL_MAX_MINUTES = 24 * 60
_POLL_DEFAULT_MINUTES = 5


def _loop_poll_settings(nas_id: int, slug: str = "loop_detect") -> dict:
    enabled_raw = tenants_repo.get_setting(
        _tid(), _state_key(nas_id, slug, "poll_enabled"), "1")
    minutes_raw = tenants_repo.get_setting(
        _tid(), _state_key(nas_id, slug, "poll_minutes"),
        str(_POLL_DEFAULT_MINUTES))
    try:
        minutes = int(str(minutes_raw or "").strip() or _POLL_DEFAULT_MINUTES)
    except ValueError:
        minutes = _POLL_DEFAULT_MINUTES
    minutes = max(_POLL_MIN_MINUTES, min(_POLL_MAX_MINUTES, minutes))
    return {
        "enabled": str(enabled_raw or "").strip().lower() in _STATE_TRUE,
        "minutes": minutes,
    }


def _set_loop_poll_settings(nas_id: int, slug: str, *, enabled: bool,
                            minutes: int) -> None:
    by = int(getattr(g, "admin_id", 0) or 0)
    minutes = max(_POLL_MIN_MINUTES, min(_POLL_MAX_MINUTES, int(minutes)))
    tenants_repo.set_setting(
        _tid(), _state_key(nas_id, slug, "poll_enabled"),
        "1" if enabled else "0", by=by)
    tenants_repo.set_setting(
        _tid(), _state_key(nas_id, slug, "poll_minutes"),
        str(minutes), by=by)


def _load_nas(nas_id: int) -> dict | None:
    row = db().execute(
        "SELECT id, name, address, api_port, api_user, api_password, "
        "       api_use_tls, enabled, connection_mode, vpn_peer_address "
        "FROM nas_devices "
        "WHERE id=? AND tenant_id=? "
        "  AND (deleted_at IS NULL OR deleted_at='')",
        (nas_id, _tid()),
    ).fetchone()
    return dict(row) if row else None


def _nas_for_mac(nas: dict) -> dict:
    return {
        "id":          nas["id"],
        "name":        nas["name"],
        "host":        resolve_connection_address(nas),
        "port":        int(nas.get("api_port") or 8728),
        "username":    nas.get("api_user") or "admin",
        "password":    nas.get("api_password") or "",
        "use_tls":     bool(nas.get("api_use_tls")),
        "verify_tls":  True,
        "timeout_sec": 10,
    }


def register_port_script_services_routes(bp: Blueprint) -> None:
    bp.add_url_rule(
        "/mt/<int:nas_id>/port-services",
        "mt_port_services_form",
        requires_perm(PERM_PROGRAM)(mt_port_services_form),
        methods=["GET"],
    )
    bp.add_url_rule(
        "/mt/<int:nas_id>/port-services/<slug>/plan",
        "mt_port_services_plan",
        requires_perm(PERM_PROGRAM)(mt_port_services_plan),
        methods=["POST"],
    )
    bp.add_url_rule(
        "/mt/<int:nas_id>/port-services/<slug>/apply",
        "mt_port_services_apply",
        requires_perm(PERM_PROGRAM)(mt_port_services_apply),
        methods=["POST"],
    )
    bp.add_url_rule(
        "/mt/<int:nas_id>/port-services/<slug>/remove",
        "mt_port_services_remove",
        requires_perm(PERM_PROGRAM)(mt_port_services_remove),
        methods=["POST"],
    )
    # فحص اللوب الحيّ — يقرأ /ip dhcp-client للإدخالات الموسومة
    # HR-LoopDetect ويعرض حالة كل منفذ (لخدمة loop_detect).
    bp.add_url_rule(
        "/mt/<int:nas_id>/port-services/<slug>/loop-check",
        "mt_port_services_loop_check",
        requires_perm(PERM_PROGRAM)(mt_port_services_loop_check),
        # GET مسموح: الفحص قراءة فقط (/ip dhcp-client الموسوم HR-LoopDetect،
        # بلا تعديل) — فيُصلح 405 عند تحديث الصفحة أو فتح الرابط مباشرة بعد
        # ضغط «فحص اللوب». بلا نموذج (GET) يشتقّ المنافذ من الحالة المحفوظة.
        methods=["GET", "POST"],
    )
    # تطبيق/إزالة الخدمة على منفذ واحد (JSON) — تستهلكه واجهة التقدم
    # منفذًا منفذًا، فيُعرف بالضبط أيّ منفذ نجح وأيّ منفذ فشل ولماذا.
    bp.add_url_rule(
        "/mt/<int:nas_id>/port-services/<slug>/apply-port",
        "mt_port_services_apply_port",
        requires_perm(PERM_PROGRAM)(mt_port_services_apply_port),
        methods=["POST"],
    )
    # حفظ إعدادات الفحص الدوري للوب (تفعيل/فترة بالدقائق).
    bp.add_url_rule(
        "/mt/<int:nas_id>/port-services/<slug>/loop-settings",
        "mt_port_services_loop_settings",
        requires_perm(PERM_PROGRAM)(mt_port_services_loop_settings),
        methods=["POST"],
    )


def _discover(nas: dict) -> list[dict]:
    """يكتشف واجهات الراوتر عبر mikrotik_admin_client. غير قاتل."""
    return pss.discover_interfaces(_nas_for_mac(nas), mac.interface_list)


def _resolve_wan_iface(nas: dict) -> str:
    """يستخرج اسم واجهة WAN لهذا الراوتر من معالج الإعداد إن سُجِّل،
    وإلا يُرجع ''. يُستخدم كمدخل لـpss.filter_lan_ports فتُستبعد الـWAN
    من قائمة المنافذ الصالحة لخدمتَي loop_detect وbt_wifi_block.

    لا نرمي حدث الخطأ — البحث best-effort، فإن لم نجد قيمة محفوظة نُعيد
    سلسلة فارغة فيقع المرشّح على احتراز ether1 الافتراضي.
    """
    try:
        row = db().execute(
            "SELECT selected_wan_interface "
            "FROM setup_wizard_runs "
            "WHERE tenant_id=? AND router_id=? "
            "  AND selected_wan_interface != '' "
            "ORDER BY id DESC LIMIT 1",
            (_tid(), int(nas["id"])),
        ).fetchone()
    except Exception:  # noqa: BLE001
        return ""
    if not row:
        return ""
    return str(dict(row).get("selected_wan_interface") or "").strip()


def _interface_names(interfaces: list[dict]) -> list[str]:
    names = []
    for r in interfaces:
        n = (r.get("name") or "").strip()
        if n:
            names.append(n)
    return names


def _port_rows(interfaces: list[dict], *, wan_iface: str = "") -> list[dict]:
    """صفوف اختيار المنافذ — *منافذ LAN فقط* لخدمتَي loop_detect/
    bt_wifi_block. تركيب dhcp-client على WAN يكسر التوجيه وتثبيت TTL=1
    على نفق VPN يقطع الاتصال المركزي، فنستبعد الاثنين هنا قبل عرض المربّعات.

    المرشّح المُشترَك pss.filter_lan_ports يُستبعد:
      • واجهة WAN (wan_iface المُمرَّر من معالج الإعداد، أو ether1 افتراضًا).
      • أنفاق VPN/PPPoE/PPTP/L2TP/SSTP/OVPN/IPsec/WireGuard/GRE/IPIP/EoIP
        بحسب type (الفحص البنيوي).
      • أنفاق مُسمّاة (hr-wg, hobe-vpn, lo) وكل ما يبدأ بـhr-pppoe-/
        pppoe-/pptp-/l2tp-/sstp-/ovpn-/ipsec-/wg-/wireguard- (احتراز اسم).
    """
    safe = pss.filter_lan_ports(
        [{"name": (r.get("name") or "").strip(),
          "type": (r.get("type") or "").strip(),
          "running": bool(r.get("running")),
          "disabled": str(r.get("disabled") or "").lower() in ("yes", "true", "1")}
         for r in interfaces if (r.get("name") or "").strip()],
        wan_iface=wan_iface,
    )
    # نُعيد بناء الصفوف على نفس بنية النموذج (name/running/disabled/type)
    # حتى لا يلاحظ القالب أي تغيير.
    return [
        {"name": r["name"], "running": r["running"],
         "disabled": r["disabled"], "type": r["type"]}
        for r in safe
    ]


def _validate_lan_ports(nas: dict, ports: list[str]) -> tuple[list[str], str]:
    """يُتحقّق أنّ كل منفذ مُختار من المشغّل هو منفذ LAN (يجتاز نفس مرشّح
    العرض). يردّ (clean, error): clean = المنافذ المسموحة بنفس ترتيبها،
    وerror = رسالة عربية إن وُجد منفذ ممنوع (WAN/نفق) فنوقف العملية.

    نستخدمه قبل apply/plan/loop_check بحيث لا يستطيع المشغّل تجاوز المرشّح
    عبر طلب POST مُصاغ يدويًا. الإزالة (remove) لا تستدعيه — تنظيف بقايا
    قاعدة قديمة على واجهة صارت WAN يجب أن يبقى ممكنًا.
    """
    if not ports:
        return [], ""
    wan = _resolve_wan_iface(nas)
    # نحتاج صفوف الواجهات مع type للقرار البنيوي؛ نلتقطها مرّة واحدة هنا.
    interfaces = _discover(nas)
    by_name = {(r.get("name") or "").strip(): r for r in interfaces
               if (r.get("name") or "").strip()}
    bad: list[str] = []
    clean: list[str] = []
    for p in ports:
        row = by_name.get(p, {"name": p})  # غير مكتشَفة ⇒ نُقيّم بالاسم فقط
        if pss.is_lan_port(row, wan_iface=wan):
            clean.append(p)
        else:
            bad.append(p)
    if bad:
        return clean, (
            "هذه الواجهات لا يُسمح بتطبيق الخدمة عليها (WAN/نفق): "
            + ", ".join(bad)
        )
    return clean, ""


def _states_for(nas_id: int) -> dict:
    """حالة كل خدمة مُسجّلة على هذا الراوتر (للبطاقات)."""
    return {s.slug: _get_state(nas_id, s.slug) for s in pss.list_services()}


def _probe_is_loop(status: str, lease_ip: str) -> bool:
    """نفس منطق pss._probe_from_row لكن على قراءة مخزّنة (poller)."""
    st = str(status or "").strip().lower()
    addr = str(lease_ip or "").strip()
    return st.startswith("bound") or (bool(addr) and not addr.startswith("0.0.0.0"))


def _loop_table_rows(nas_id: int, state: dict | None, loop_probes) -> list[dict]:
    """صفوف جدول «حالة المنافذ» لخدمة كشف اللوب — دمج ثلاثة مصادر:

      1. الفحص الحي (loop_probes) إن جرى الآن — المصدر الأصدق.
      2. آخر قراءة دورية مخزّنة (router_loop_probes) — من الـpoller.
      3. الحالة المحفوظة (tenant_settings) — «ما طُلب تفعيله».

    لكل منفذ صفّ واحد يجيب صراحةً: هل القاعدة مركّبة فعلًا على الراوتر؟
    وهل عليه لوب؟ ومتى آخر قراءة؟ — فلا يتناقض بانر «مفعّلة» مع الواقع.
    rule_installed: True/False من قراءة فعلية، None = لم تُقرأ بعد.
    """
    saved_ports = [p for p in ((state or {}).get("ports") or []) if p]
    try:
        repo_rows = {r["interface"]: r for r in
                     router_loop_probes_repo.list_for_router(_tid(), nas_id)}
    except Exception:  # noqa: BLE001 — جدول ناقص في بيئة قديمة
        repo_rows = {}
    live = {p.iface: p for p in (loop_probes or [])}

    ordered: list[str] = []
    for name in [*saved_ports, *sorted(set(repo_rows) | set(live))]:
        if name and name not in ordered:
            ordered.append(name)

    rows: list[dict] = []
    for name in ordered:
        row = {
            "port": name,
            "saved": name in saved_ports,
            "source": "",            # live | poller | ''
            "rule_installed": None,   # True/False/None=غير معروف بعد
            "is_loop": False,
            "status": "",
            "address": "",
            "server": "",
            "last_reading_at": "",
        }
        probe = live.get(name)
        stored = repo_rows.get(name)
        if probe is not None:
            row.update({
                "source": "live",
                "rule_installed": probe.status != "no-rule",
                "is_loop": probe.is_loop,
                "status": probe.status,
                "address": probe.address,
                "server": probe.dhcp_server or probe.gateway,
            })
        elif stored is not None:
            # الـpoller يخزّن أيضًا قراءة «no-rule» (منفذ مطلوب بلا
            # قاعدة) — نفسّرها هنا كقاعدة غير مركّبة لا كحالة فحص.
            stored_status = str(stored.get("last_status") or "")
            row.update({
                "source": "poller",
                "rule_installed": stored_status != "no-rule",
                "is_loop": _probe_is_loop(stored_status,
                                          stored.get("last_lease_ip")),
                "status": stored_status,
                "address": stored.get("last_lease_ip") or "",
                "server": stored.get("last_server_ip") or "",
                "last_reading_at": stored.get("last_reading_at") or "",
            })
        rows.append(row)
    return rows


def _loop_context(nas_id: int, service, state: dict | None,
                  loop_probes) -> dict:
    """سياق إدارة اللوب للقالب — لا شيء منه يُحسب لغير خدمة loop_detect."""
    if service is None or service.slug != "loop_detect":
        return {"loop_table": None, "loop_settings": None,
                "loop_history": None}
    try:
        history = router_loop_checks_repo.list_for_router(
            _tid(), nas_id, limit=20)
    except Exception:  # noqa: BLE001 — جدول ناقص في بيئة قديمة
        history = []
    return {
        "loop_table": _loop_table_rows(nas_id, state, loop_probes),
        "loop_settings": _loop_poll_settings(nas_id),
        "loop_history": history,
    }


def _render(nas: dict, *, service=None, plan=None, selected_ports=None,
            error=None, apply_result=None, interfaces=None,
            plan_mode: str = "apply", loop_probes=None, loop_error=None,
            detect_probes=None):
    """نقطة عرض موحّدة — تضمن تمرير حالة الخدمات وصفوف المنافذ في كل
    مسار (form/plan/apply/remove/loop-check) بلا تكرار. plan_mode يحدّد
    وجهة زر الدفع في المعاينة: 'apply' (تفعيل) أو 'remove' (إزالة).

    loop_probes: نتائج «فحص اللوب» للعرض في بانر النتائج (غير None فقط بعد
      ضغط «فحص اللوب»).
    detect_probes: قراءة حيّة تُستخدَم *فقط* لتحديد «مركّب/غير مركّب» في جدول
      المنافذ — حتى تتطابق صفحة الإعداد (GET) مع «فحص اللوب» بلا إظهار بانر
      نتائج فحص. جدول المنافذ يأخذ detect_probes إن وُجدت وإلّا loop_probes."""
    if interfaces is None:
        interfaces = _discover(nas)
    wan = _resolve_wan_iface(nas)
    state = _get_state(nas["id"], service.slug) if service else None
    table_probes = detect_probes if detect_probes is not None else loop_probes
    return render_template(
        "radius/port_script_services.html",
        nas=nas,
        services=pss.list_services(),
        service=service,
        interfaces=interfaces,
        interface_names=_interface_names(interfaces),
        port_rows=_port_rows(interfaces, wan_iface=wan),
        plan=plan,
        plan_mode=plan_mode,
        selected_ports=selected_ports or [],
        error=error,
        apply_result=apply_result,
        states=_states_for(nas["id"]),
        state=state,
        loop_probes=loop_probes,
        loop_error=loop_error,
        **_loop_context(nas["id"], service, state, table_probes),
    )


def mt_port_services_form(nas_id: int):
    nas = _load_nas(nas_id)
    if not nas:
        abort(404)
    slug = (request.args.get("slug") or "").strip()
    service = pss.get_service(slug)
    # تطابق «مركّب/غير مركّب» مع «فحص اللوب»: لخدمة loop_detect نقرأ الحالة
    # الحيّة من الراوتر (نفس مصدر loop-check: /ip dhcp-client الموسوم) فلا
    # يظهر منفذ متّصل (dhcp-client bound) «غير مركّب» على صفحة الإعداد بينما
    # «فحص اللوب» يراه «مركّب». أفضل-جهد: عند تعذّر القراءة (راوتر مفصول/قاطع
    # مفتوح) نسقط بصمت للحالة المخزّنة (poller) — لا نُفشل الصفحة. تُمرَّر
    # كـdetect_probes (تحديد التركيب فقط) لا كنتائج فحص (بلا بانر «فُحص»).
    detect_probes = None
    if service is not None and service.slug == "loop_detect":
        try:
            saved_ports = _get_state(nas_id, service.slug).get("ports") or []
            probes, err = pss.read_loop_status(
                nas, mac.dhcp_client_list,
                only_ports=(saved_ports or None))
            if not err:
                detect_probes = probes
        except Exception:  # noqa: BLE001 — التحديث الحيّ لا يكسر الصفحة أبدًا
            detect_probes = None
    return _render(nas, service=service, detect_probes=detect_probes)


def _ports_from_form() -> list[str]:
    """يقرأ المنافذ المختارة من النموذج: checkboxes (ports متعدّدة) أو
    حقل نصّي واحد مفصول بفواصل (مسار الإدخال اليدوي)."""
    raw = request.form.getlist("ports")
    out: list[str] = []
    for chunk in raw:
        for p in str(chunk).split(","):
            p = p.strip()
            if p:
                out.append(p)
    return out


def mt_port_services_plan(nas_id: int, slug: str):
    nas = _load_nas(nas_id)
    if not nas:
        abort(404)
    service = pss.get_service(slug)
    if service is None:
        abort(404)
    selected_ports = _ports_from_form()
    # وضع المعاينة: تفعيل (افتراضي) أو إزالة — يُعرض سكربت الإزالة
    # للمراجعة قبل دفعه.
    remove = request.form.get("mode") == "remove"
    plan = None
    error = None
    # حارس LAN-only: نمنع توليد خطة تفعيل على واجهة WAN/نفق حتى لو
    # صُيغ النموذج يدويًا. الإزالة لا تستدعيه (لتمكين تنظيف بقايا قديمة).
    if not remove and selected_ports:
        selected_ports, lan_err = _validate_lan_ports(nas, selected_ports)
        if lan_err:
            error = lan_err
    if error is None:
        try:
            plan = pss.build_plan(slug, selected_ports, remove=remove)
        except ValueError as e:
            error = str(e)
    return _render(nas, service=service, plan=plan,
                   selected_ports=selected_ports, error=error,
                   plan_mode=("remove" if remove else "apply"))


def _connect_client(nas: dict):
    from ..integration.mikrotik.client import MikrotikClient

    return MikrotikClient(
        host=resolve_connection_address(nas),
        port=int(nas.get("api_port") or 8728),
        username=nas.get("api_user") or "admin",
        password=nas.get("api_password") or "",
        use_tls=bool(nas.get("api_use_tls")),
        verify_tls=True,
        timeout=15.0,
    )


def _push_to_router(nas: dict, plan, comment: str):
    """يدفع سكربت الخطة إلى الراوتر عبر *نفس* منفّذ الأوامر الموجود
    (mt_programming.apply_commands) — لا مسار جديد. يفتح اتصالًا خامًا
    مثل mt_programming.apply لرؤية الخطوات واحدة واحدة.

    إرجاع: (apply_result | None, error | "")."""
    # اسم سكربت نظام مؤقّت فريد لكل عملية حتى لا يتعارض مع بقايا محاولة
    # سابقة لم تُنظَّف (يُحذَف بعد التشغيل ضمن أوامر الدفع).
    name = f"hr-pss-{plan.slug}-{uuid.uuid4().hex[:8]}"
    cmds = pss.build_push_commands(plan.script, name=name, comment=comment)
    client = _connect_client(nas)
    try:
        client.connect()
        return mtp.apply_commands(client, cmds), ""
    except Exception as e:  # noqa: BLE001
        return None, f"تعذّر الاتصال بالراوتر أو تنفيذ السكربت: {e}"
    finally:
        try:
            client.close()
        except Exception:  # noqa: BLE001
            pass


def _audit_push(nas_id: int, slug: str, *, action: str, ports: list[str],
                ok: bool) -> None:
    get_audit_service().record(
        actor=str(getattr(g, "admin_id", None) or "ui"),
        action=f"mt.port_services.{slug}.{action}",
        target_type="mikrotik_nas",
        target_id=str(nas_id),
        router_id=int(nas_id),
        payload={"slug": slug, "ports": ports, "ok": ok},
    )


def mt_port_services_apply(nas_id: int, slug: str):
    """تطبيق (تفعيل) سكربت الخدمة على الراوتر فعليًا.

    معطّل ما دامت الخدمة مبدئية (is_placeholder): لا سكربت فعلي يُدفَع
    بعد. حالما يُلصق سكربت المستخدم في قالب الخدمة ويُضبط
    is_placeholder=False، يعمل الدفع كاملًا عبر mt_programming."""
    nas = _load_nas(nas_id)
    if not nas:
        abort(404)
    service = pss.get_service(slug)
    if service is None:
        abort(404)
    selected_ports = _ports_from_form()
    confirmed = request.form.get("confirm") == "1"
    plan = None
    error = None
    apply_result = None

    # حارس LAN-only — نفس مبدأ plan: لا نسمح بـapply على WAN/نفق حتى
    # لو تجاوز المشغّل قائمة العرض. تركيب dhcp-client على WAN يكسر
    # التوجيه، وtTL=1 على نفق VPN يقطع الإدارة المركزية.
    if selected_ports:
        selected_ports, lan_err = _validate_lan_ports(nas, selected_ports)
        if lan_err:
            error = lan_err

    if error is None:
        try:
            plan = pss.build_plan(slug, selected_ports)
        except ValueError as e:
            error = str(e)

    if plan is not None:
        if service.is_placeholder:
            # بانتظار السكربت — لا شيء يُدفَع بعد.
            error = (
                "هذه الخدمة بقالب مبدئي — أضِف سكربت المستخدم في قالب "
                "الخدمة أولًا قبل التطبيق."
            )
        elif not confirmed:
            error = "يجب تأكيد العملية قبل التطبيق."
        else:
            apply_result, error = _push_to_router(nas, plan, service.comment)
            if apply_result is not None and apply_result.ok:
                # حفظ الحالة: مفعّلة على المنافذ المختارة.
                _set_state(nas_id, slug, enabled=True,
                           ports=plan.selected_ports)
            elif apply_result is not None and not error:
                error = (apply_result.error
                         or "فشل تطبيق السكربت — راجع الخطوات أدناه.")
            _audit_push(nas_id, slug, action="apply",
                        ports=plan.selected_ports,
                        ok=bool(apply_result and apply_result.ok))

    return _render(nas, service=service, plan=plan,
                   selected_ports=selected_ports, error=error,
                   apply_result=apply_result)


def mt_port_services_remove(nas_id: int, slug: str):
    """إزالة/تعطيل الخدمة على الراوتر — يدفع سكربت الإزالة
    (remove_template) عبر نفس المنفّذ، ثم يضبط الحالة «غير مفعّلة».

    معطّل أيضًا ما دامت الخدمة مبدئية (لا سكربت إزالة فعلي بعد)."""
    nas = _load_nas(nas_id)
    if not nas:
        abort(404)
    service = pss.get_service(slug)
    if service is None:
        abort(404)
    # عند الإزالة نستعمل المنافذ المحفوظة في الحالة إن لم تُرسَل صراحةً.
    selected_ports = _ports_from_form() or _get_state(nas_id, slug)["ports"]
    confirmed = request.form.get("confirm") == "1"
    plan = None
    error = None
    apply_result = None

    try:
        plan = pss.build_plan(slug, selected_ports, remove=True)
    except ValueError as e:
        error = str(e)

    if plan is not None:
        if service.is_placeholder:
            error = (
                "هذه الخدمة بقالب مبدئي — أضِف سكربت الإزالة في قالب "
                "الخدمة أولًا قبل التعطيل."
            )
        elif not confirmed:
            error = "يجب تأكيد عملية الإزالة قبل تنفيذها."
        else:
            apply_result, error = _push_to_router(nas, plan, service.comment)
            if apply_result is not None and apply_result.ok:
                _set_state(nas_id, slug, enabled=False, ports=[])
            elif apply_result is not None and not error:
                error = (apply_result.error
                         or "فشل تنفيذ سكربت الإزالة — راجع الخطوات أدناه.")
            _audit_push(nas_id, slug, action="remove",
                        ports=selected_ports,
                        ok=bool(apply_result and apply_result.ok))

    return _render(nas, service=service, plan=plan,
                   selected_ports=selected_ports, error=error,
                   apply_result=apply_result, plan_mode="remove")


def mt_port_services_loop_check(nas_id: int, slug: str):
    """فحص اللوب الحيّ — يقرأ /ip dhcp-client للإدخالات الموسومة
    HR-LoopDetect ويعرض حالة كل منفذ: bound (رجع IP) = لوب مكتشف،
    searching = لا لوب.

    يُعيد استخدام عميل RouterOS API الموجود (mikrotik_admin_client.
    dhcp_client_list) — نمرّره لـpss.read_loop_status بلا اختراع مسار
    قراءة جديد. المنافذ تُضيَّق على المختارة (أو المحفوظة في الحالة) إن
    وُجدت، وإلا تُعرض كل الإدخالات الموسومة."""
    nas = _load_nas(nas_id)
    if not nas:
        abort(404)
    service = pss.get_service(slug)
    if service is None:
        abort(404)
    # المنافذ: من النموذج أو من الحالة المحفوظة — لتضييق العرض عند توفّرها.
    selected_ports = _ports_from_form() or _get_state(nas_id, slug)["ports"]
    only = selected_ports or None
    # نمرّر صفّ الراوتر الخام (api_*) لأن mac.dhcp_client_list يبني
    # router_cfg من أعمدة api_port/api_user/...، لا من مخطّط _nas_for_mac.
    loop_probes, loop_error = pss.read_loop_status(
        nas, mac.dhcp_client_list, only_ports=only)
    _audit_push(nas_id, slug, action="loop_check",
                ports=selected_ports, ok=not loop_error)
    # سجل الفحوصات: كل فحص يدوي يُدوَّن بنتيجته وتفاصيل كل منفذ —
    # يجيب لاحقًا عن «كم فحص جرى ومتى وماذا وجد كل واحد».
    _record_loop_check(nas_id, source="manual",
                       probes=loop_probes, error=loop_error)
    return _render(nas, service=service, selected_ports=selected_ports,
                   loop_probes=loop_probes, loop_error=loop_error)


def _record_loop_check(nas_id: int, *, source: str, probes,
                       error: str = "") -> None:
    """يدوّن فحص لوب في السجل — لا يكسر الطلب أبدًا عند فشل الكتابة."""
    try:
        details = [{
            "iface": p.iface,
            "status": p.status,
            "is_loop": bool(p.is_loop),
            "address": p.address,
            "server": p.dhcp_server or p.gateway,
        } for p in (probes or [])]
        router_loop_checks_repo.insert_check(
            tenant_id=_tid(), router_id=nas_id, source=source,
            ok=not error, error=error or "", details=details)
    except Exception:  # noqa: BLE001 — السجل ثانوي، لا يفشل الفحص لأجله
        pass


def mt_port_services_apply_port(nas_id: int, slug: str):
    """تطبيق/إزالة الخدمة على *منفذ واحد* — JSON لواجهة التقدم.

    بدل دفع سكربت واحد لكل المنافذ (فلا يُعرف أين فشل)، تستدعي الواجهة
    هذا المسار منفذًا منفذًا فتعرض تقدمًا حيًّا: أي منفذ قيد التركيب،
    أيها نجح، وأيها فشل ولماذا. الحالة المحفوظة تُحدَّث تراكميًا بعد كل
    منفذ ناجح فقط — فلا يُحفَظ «مفعّلة على 8» بينما نجح التركيب على 5
    (مصدر التناقض الذي كان بين البانر الأخضر ونتيجة الفحص).
    """
    nas = _load_nas(nas_id)
    if not nas:
        abort(404)
    service = pss.get_service(slug)
    if service is None:
        abort(404)
    port = (request.form.get("port") or "").strip()
    remove = request.form.get("mode") == "remove"
    if not port:
        return jsonify({"ok": False, "port": "", "error": "حدّد منفذًا."}), 400
    if service.is_placeholder:
        return jsonify({
            "ok": False, "port": port,
            "error": "الخدمة بقالب مبدئي — لا سكربت يُدفَع بعد.",
        }), 400
    if not remove:
        _, lan_err = _validate_lan_ports(nas, [port])
        if lan_err:
            return jsonify({"ok": False, "port": port, "error": lan_err}), 400
    try:
        plan = pss.build_plan(slug, [port], remove=remove)
    except ValueError as e:
        return jsonify({"ok": False, "port": port, "error": str(e)}), 400

    apply_result, error = _push_to_router(nas, plan, service.comment)
    ok = bool(apply_result and apply_result.ok and not error)
    if apply_result is not None and not apply_result.ok and not error:
        error = apply_result.error or "فشل تنفيذ السكربت على الراوتر."

    if ok:
        # تحديث الحالة تراكميًا: تركيب يضيف المنفذ، إزالة تحذفه.
        ports = _get_state(nas_id, slug)["ports"]
        if remove:
            ports = [p for p in ports if p != port]
        elif port not in ports:
            ports.append(port)
        _set_state(nas_id, slug, enabled=bool(ports), ports=ports)

    _audit_push(nas_id, slug,
                action=("remove_port" if remove else "apply_port"),
                ports=[port], ok=ok)
    steps = []
    if apply_result is not None:
        steps = [{"path": s.path, "ok": bool(s.ok), "error": s.error or ""}
                 for s in apply_result.steps]
    return jsonify({
        "ok": ok,
        "port": port,
        "mode": "remove" if remove else "apply",
        "error": "" if ok else (error or "فشل غير معروف."),
        "steps": steps,
    })


def mt_port_services_loop_settings(nas_id: int, slug: str):
    """حفظ إعدادات الفحص الدوري (تفعيل + الفترة بالدقائق) ثم العودة
    لصفحة الخدمة. يقرؤها loop_probe_poller كل دورة فيحترمها فورًا."""
    nas = _load_nas(nas_id)
    if not nas:
        abort(404)
    if pss.get_service(slug) is None:
        abort(404)
    enabled = request.form.get("poll_enabled") in ("1", "on", "true", "yes")
    try:
        minutes = int(request.form.get("poll_minutes") or _POLL_DEFAULT_MINUTES)
    except ValueError:
        minutes = _POLL_DEFAULT_MINUTES
    _set_loop_poll_settings(nas_id, slug, enabled=enabled, minutes=minutes)
    get_audit_service().record(
        actor=str(getattr(g, "admin_id", None) or "ui"),
        action=f"mt.port_services.{slug}.poll_settings",
        target_type="mikrotik_nas",
        target_id=str(nas_id),
        router_id=int(nas_id),
        payload={"enabled": enabled, "minutes": minutes},
    )
    return redirect(url_for("radius.mt_port_services_form",
                            nas_id=nas_id) + f"?slug={slug}")
