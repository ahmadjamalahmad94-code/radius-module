"""«منع استنساخ MAC» — منطق الفحص + القرار (feat/anti-mac-clone).

الهدف: إيقاف سرقة جلسة عبر استنساخ MAC (نسخ عنوان MAC من جهاز شرعي للوصول
من جهاز ثانٍ) دون إيقاف خاصّية الـMAC-cookie (إذ تُلغي تجربة الدخول السلس).

الفكرة: على أوّل دخول ناجح نَلْزَم MAC ↔ «بصمة الجهاز» = {OS family + DHCP
Option-60 (class id) + Hostname + User-Agent (إن وُجد) + النموذجي من سياق
الشبكة}. ثم على كل auth لاحق نُعيد التحقق:

  • نفس MAC + بصمة مطابقة            → جهاز حقيقي (سماح بلا احتكاك).
  • نفس MAC + بصمة مختلفة جدًّا       → استنساخ → رفض/تصعيد + تنبيه.
  • نفس MAC + جلسة متزامنة من سياق
    متباعد (different NAS + AP)        → استنساخ مؤكَّد → رفض + ركل الجلسة الأولى.

الخدمة tenant-scoped؛ مغلقة افتراضيًّا (toggle OFF). تستعمل tenant_settings
لجميع الإعدادات (لا جدول إعدادات منفصل) + الجدولين الجديدين في migration 125.
لا تكسر مسار الـauth أبدًا: كل استثناء يُلتقط ويُسقط الفحص (= سماح).
"""
from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from typing import Any, Optional

from ..db.connection import db
from ..db.repos import mac_clone_repo, tenants_repo

_LOG = logging.getLogger(__name__)

_TRUE = {"1", "true", "t", "on", "yes"}


# ════════════════════════════════════════════════════════════════════════
# مفاتيح الإعدادات في tenant_settings (نفس نمط access_control)
# ════════════════════════════════════════════════════════════════════════
SK_ENABLED            = "anti_mac_clone.enabled"            # 0/1 — توقّف الميزة كلّيًا
SK_MODE               = "anti_mac_clone.mode"               # monitor | enforce
SK_SCOPE              = "anti_mac_clone.scope"              # all | plans | groups
SK_SCOPE_PLAN_IDS     = "anti_mac_clone.scope_plan_ids"     # "1,3,5"
SK_SCOPE_GROUP_NAMES  = "anti_mac_clone.scope_group_names"  # "البرنزي,VIP"
SK_CONFIDENCE_MIN     = "anti_mac_clone.confidence_min"     # low | medium | high
SK_CONCURRENT_GUARD   = "anti_mac_clone.concurrent_guard"   # 0/1
SK_ALERT_ENABLED      = "anti_mac_clone.alert_enabled"      # 0/1
SK_COA_DISCONNECT     = "anti_mac_clone.coa_disconnect"     # 0/1 — اركل الجلسة عند الكشف
SK_RAW_LIMIT          = "anti_mac_clone.recent_events_limit"  # عرض

_DEFAULTS = {
    SK_ENABLED:           "0",         # مغلق افتراضيًّا
    SK_MODE:              "enforce",   # عند التفعيل: رفض الاستنساخ مباشرة
    SK_SCOPE:             "all",
    SK_SCOPE_PLAN_IDS:    "",
    SK_SCOPE_GROUP_NAMES: "",
    SK_CONFIDENCE_MIN:    "medium",
    SK_CONCURRENT_GUARD:  "1",
    SK_ALERT_ENABLED:     "1",
    SK_COA_DISCONNECT:    "1",
    SK_RAW_LIMIT:         "200",
}

_CONFIDENCE_RANK = {"low": 1, "medium": 2, "high": 3}


# ════════════════════════════════════════════════════════════════════════
# قراءة الإعدادات
# ════════════════════════════════════════════════════════════════════════
def _setting(tenant_id: int, key: str) -> str:
    raw = tenants_repo.get_setting(int(tenant_id), key, _DEFAULTS.get(key, ""))
    return str(raw or "").strip()


def _flag(tenant_id: int, key: str) -> bool:
    return _setting(tenant_id, key).lower() in _TRUE


def is_enabled(tenant_id: int) -> bool:
    """التفعيل العام للميزة لهذا المستأجر."""
    return _flag(tenant_id, SK_ENABLED)


def get_settings(tenant_id: int) -> dict[str, str]:
    return {k: _setting(tenant_id, k) for k in _DEFAULTS}


def set_settings(tenant_id: int, values: dict[str, str], *,
                  by: int = 0) -> list[str]:
    """يحفظ مجموعة قيم. القيم غير المعروفة تُتجاهَل. يعيد قائمة المفاتيح المتغيّرة."""
    changed: list[str] = []
    for key, default in _DEFAULTS.items():
        if key not in values:
            continue
        raw = str(values.get(key) or "").strip()
        new = _normalize_setting_value(key, raw, default)
        old = tenants_repo.get_setting(int(tenant_id), key, default)
        if new != old:
            tenants_repo.set_setting(int(tenant_id), key, new, by=int(by))
            changed.append(key)
    return changed


def _normalize_setting_value(key: str, raw: str, default: str) -> str:
    """تطبيع قيمة إعداد قبل التخزين."""
    if key in (SK_ENABLED, SK_CONCURRENT_GUARD, SK_ALERT_ENABLED, SK_COA_DISCONNECT):
        return "1" if raw.lower() in _TRUE else "0"
    if key == SK_MODE:
        return raw if raw in ("monitor", "enforce") else default
    if key == SK_SCOPE:
        return raw if raw in ("all", "plans", "groups") else default
    if key == SK_CONFIDENCE_MIN:
        return raw if raw in ("low", "medium", "high") else default
    if key == SK_SCOPE_PLAN_IDS:
        # قائمة أرقام عرض/باقة بفاصلة، نُسقط ما ليس رقمًا
        ids = [p.strip() for p in raw.replace("،", ",").split(",")]
        ids = [p for p in ids if p.isdigit()]
        return ",".join(sorted(set(ids), key=int))
    if key == SK_SCOPE_GROUP_NAMES:
        names = [g.strip() for g in raw.replace("،", ",").split(",")]
        names = [g for g in names if g]
        return ",".join(sorted(set(names)))
    if key == SK_RAW_LIMIT:
        try:
            n = max(10, min(2000, int(raw or default)))
        except ValueError:
            n = int(default or 200)
        return str(n)
    return raw or default


# ════════════════════════════════════════════════════════════════════════
# مطابقة النطاق (scope) — مَن تنطبق عليه الميزة؟
# ════════════════════════════════════════════════════════════════════════
@dataclass(frozen=True)
class ScopeContext:
    source: str = "subscriber"     # subscriber | card
    plan_id: Optional[int] = None
    group: str = ""


def scope_applies(tenant_id: int, ctx: ScopeContext) -> bool:
    """هل تنطبق ميزة منع الاستنساخ على هذا المستخدم؟

    scope = all       → كل المشتركين/البطاقات
    scope = plans     → فقط أصحاب أحد الـ plan_ids في scope_plan_ids
    scope = groups    → فقط أعضاء أحد الـ groups في scope_group_names
    """
    scope = _setting(tenant_id, SK_SCOPE) or "all"
    if scope == "all":
        return True
    if scope == "plans":
        raw = _setting(tenant_id, SK_SCOPE_PLAN_IDS)
        if not raw:
            return False
        try:
            allowed = {int(p) for p in raw.split(",") if p.isdigit()}
        except ValueError:
            return False
        return ctx.plan_id is not None and int(ctx.plan_id) in allowed
    if scope == "groups":
        raw = _setting(tenant_id, SK_SCOPE_GROUP_NAMES)
        if not raw:
            return False
        allowed = {g.strip() for g in raw.split(",") if g.strip()}
        return bool(ctx.group) and ctx.group in allowed
    return True  # غير معروف → نسلك المسار الآمن (نطبّق الميزة عند التفعيل)


# ════════════════════════════════════════════════════════════════════════
# بناء بصمة الـauth الحالية
# ════════════════════════════════════════════════════════════════════════
@dataclass
class AuthFingerprint:
    """البصمة المُلتقطة من طلب auth الحالي (محصورة بالإشارات المتاحة فعلًا)."""
    mac: str = ""
    vendor_oui: str = ""
    # إشارات الجهاز (محفوظة في binding):
    hostname: str = ""
    dhcp_class_id: str = ""
    os_family: str = ""
    device_brand: str = ""
    device_model: str = ""
    ua_hash: str = ""
    ua_sample: str = ""
    # سياق الشبكة:
    nas_ip: str = ""
    called_station: str = ""
    nas_port: str = ""
    nas_port_type: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "mac": self.mac, "vendor_oui": self.vendor_oui,
            "hostname": self.hostname, "dhcp_class_id": self.dhcp_class_id,
            "os_family": self.os_family,
            "device_brand": self.device_brand, "device_model": self.device_model,
            "ua_hash": self.ua_hash, "ua_sample": self.ua_sample,
            "nas_ip": self.nas_ip, "called_station": self.called_station,
            "nas_port": self.nas_port, "nas_port_type": self.nas_port_type,
        }


def hash_user_agent(ua: str) -> str:
    """SHA-256 hex لـ User-Agent. فارغ → فارغ."""
    s = (ua or "").strip()
    if not s:
        return ""
    return hashlib.sha256(s.encode("utf-8", errors="ignore")).hexdigest()


def ua_short_sample(ua: str) -> str:
    """عيّنة قصيرة للعرض (لا تحوي PII — بادئة 40 حرفًا)."""
    s = (ua or "").strip()
    return s[:40]


def _oui_of(mac: str) -> str:
    norm = mac_clone_repo.normalize_mac(mac)
    return norm[:8] if len(norm) >= 8 else ""


def build_fingerprint(*, tenant_id: int,
                      calling_station_id: str = "",
                      called_station_id: str = "",
                      nas_ip: str = "",
                      nas_port: str = "",
                      nas_port_type: str = "",
                      user_agent: str = "",
                      ) -> AuthFingerprint:
    """يبني بصمة من إشارات الطلب الحالي + إثراءها من device_fingerprints (الـDHCP).

    الإشارات المتاحة وقت الـauth:
      • Calling-Station-Id  → MAC العميل (مصدر vendor_oui).
      • Called-Station-Id    → MAC الواجهة على الراوتر (يعرّف AP/SSID/البوابة).
      • NAS-IP-Address       → الراوتر.
      • NAS-Port, NAS-Port-Type → المنفذ الفعلي (eth/wifi/ppp).
      • User-Agent           → من البوابة (إن وُجد).
      • device_fingerprints  → جدول مأهول من مزامنة DHCP في الخلفية — يحوي
        hostname / dhcp_class_id / os_family / device_brand/model.
    """
    mac = mac_clone_repo.normalize_mac(calling_station_id)
    fp = AuthFingerprint(
        mac=mac,
        vendor_oui=_oui_of(mac),
        nas_ip=(nas_ip or "").strip(),
        called_station=(called_station_id or "").strip(),
        nas_port=(nas_port or "").strip(),
        nas_port_type=(nas_port_type or "").strip(),
        ua_hash=hash_user_agent(user_agent),
        ua_sample=ua_short_sample(user_agent),
    )
    if mac:
        try:
            from ..db.repos import device_fingerprints_repo
            dfp = device_fingerprints_repo.get_by_mac(int(tenant_id), mac)
        except Exception:  # noqa: BLE001
            dfp = None
        if dfp:
            fp.hostname      = dfp.get("hostname") or ""
            fp.dhcp_class_id = dfp.get("dhcp_class_id") or ""
            fp.os_family     = dfp.get("os_family") or ""
            fp.device_brand  = dfp.get("device_brand") or ""
            fp.device_model  = dfp.get("device_model") or ""
    return fp


# ════════════════════════════════════════════════════════════════════════
# المقارنة + درجة الخطورة
# ════════════════════════════════════════════════════════════════════════
@dataclass
class Comparison:
    """نتيجة مقارنة بصمة الـauth الحالية بـ binding القائم."""
    score: int = 0                              # 0..100
    confidence: str = "low"                     # low | medium | high
    diverged: list[str] = field(default_factory=list)
    matched: list[str] = field(default_factory=list)

    def is_clone(self, threshold: str = "medium") -> bool:
        return (_CONFIDENCE_RANK.get(self.confidence, 0)
                >= _CONFIDENCE_RANK.get(threshold, 2))


# الأوزان: كم نقطة خطورة على كل إشارة متباينة. مجموعها قد يتجاوز 100 لكنّنا
# نَحُدّه بـ100. الإشارات الحاسمة (OUI/UA hash/OS family) أعلى من السياق
# (NAS/Called) لأن السياق قد يتغيّر شرعيًّا (ترحال بين أجهزة AP).
_WEIGHTS: dict[str, int] = {
    "vendor_oui":     0,    # سيكون نفسه دائمًا (الكلاون يُنسَخ من نفس MAC)
    "os_family":      35,   # تغيير قوي جدًّا: مستحيل جهاز iOS يصير Android بنفس الـMAC
    "device_brand":   25,
    "device_model":   15,
    "dhcp_class_id":  20,
    "hostname":       10,
    "ua_hash":        25,
    # سياق:
    "nas_ip":         10,
    "called_station": 10,
    "nas_port":        3,
    "nas_port_type":   5,
}


def _val(d: dict | object, key: str) -> str:
    if isinstance(d, dict):
        return str(d.get(key) or "").strip()
    return str(getattr(d, key, "") or "").strip()


def compare(binding: dict, live: AuthFingerprint | dict) -> Comparison:
    """يقارن binding مع live fingerprint ويُرجع Comparison.

    يُحسب score من مجموع أوزان الإشارات المتباينة (لا يحتسب الإشارات الفارغة في
    أحد الجانبين — لا نعاقب على غياب معلومة). الثقة تُشتقّ من النقاط + قواعد
    صريحة (تباين OS family → high مباشرةً مهما كانت بقية الإشارات لأن نظام
    التشغيل ثابت بالعتاد).
    """
    diverged: list[str] = []
    matched: list[str] = []
    score = 0
    for key, weight in _WEIGHTS.items():
        a = _val(binding, key)
        b = _val(live, key)
        if not a or not b:
            continue  # غياب إشارة في أحد الطرفين ≠ تباين
        if a == b:
            matched.append(key)
        else:
            diverged.append(key)
            score += weight

    score = min(100, score)
    # قواعد ثقة صريحة:
    confidence = "low"
    if score >= 60 or "os_family" in diverged or "device_brand" in diverged:
        confidence = "high"
    elif score >= 30 or "dhcp_class_id" in diverged or "ua_hash" in diverged:
        confidence = "medium"
    elif score >= 10:
        confidence = "low"

    return Comparison(score=score, confidence=confidence,
                      diverged=diverged, matched=matched)


# ════════════════════════════════════════════════════════════════════════
# كشف الجلسات المتزامنة من سياق مختلف (impossible-travel)
# ════════════════════════════════════════════════════════════════════════
@dataclass
class ConcurrentSession:
    radacctid: int
    acctsessionid: str
    username: str
    nasipaddress: str
    nasportid: str
    calledstationid: str
    callingstationid: str
    framedipaddress: str


def find_concurrent_sessions(tenant_id: int, mac: str) -> list[ConcurrentSession]:
    """يُرجع الجلسات الحيّة (acctstoptime IS NULL) لنفس MAC. لا يقيّد بـusername
    عمدًا — الاستنساخ قد يأتي بنفس MAC لكن من ملف مختلف. آمن: أي خطأ يعيد قائمة
    فارغة (لا يكسر الـauth)."""
    mac = mac_clone_repo.normalize_mac(mac)
    if not mac:
        return []
    try:
        rows = db().execute(
            """
            SELECT radacctid, acctsessionid, username, nasipaddress, nasportid,
                   calledstationid, callingstationid, framedipaddress
              FROM radacct
             WHERE tenant_id = ?
               AND UPPER(REPLACE(callingstationid,'-',':')) = ?
               AND acctstoptime IS NULL
            """,
            (int(tenant_id), mac),
        ).fetchall()
    except Exception:  # noqa: BLE001
        _LOG.warning("anti_mac_clone: find_concurrent_sessions failed",
                     exc_info=True)
        return []
    return [
        ConcurrentSession(
            radacctid=int(r["radacctid"] or 0),
            acctsessionid=str(r["acctsessionid"] or ""),
            username=str(r["username"] or ""),
            nasipaddress=str(r["nasipaddress"] or ""),
            nasportid=str(r["nasportid"] or ""),
            calledstationid=str(r["calledstationid"] or ""),
            callingstationid=str(r["callingstationid"] or ""),
            framedipaddress=str(r["framedipaddress"] or ""),
        )
        for r in rows
    ]


def is_divergent_context(session: ConcurrentSession,
                          live: AuthFingerprint) -> bool:
    """هل الجلسة الحيّة من سياق شبكي مختلف عن الـauth الحالي؟ (ترحال مستحيل)."""
    if not session:
        return False
    same_nas = (session.nasipaddress or "").strip() == (live.nas_ip or "").strip()
    same_ap = ((session.calledstationid or "").strip().upper()
                == (live.called_station or "").strip().upper())
    # سياق مختلف = راوتر مختلف، أو نفس الراوتر لكن AP/SSID مختلف.
    return (not same_nas) or (same_nas and not same_ap)


# ════════════════════════════════════════════════════════════════════════
# قرار الفحص (الواجهة العامة)
# ════════════════════════════════════════════════════════════════════════
@dataclass
class Verdict:
    """نتيجة فحص anti-mac-clone لطلب auth واحد."""
    action: str = "allow"            # allow | deny | monitor (= allow + log)
    reason: str = ""                 # رمز داخلي (مثل mac_clone_detected)
    message: str = ""                # نص عربي لـ Reply-Message
    confidence: str = "low"
    score: int = 0
    signals: dict = field(default_factory=dict)
    # (username, acctsessionid) للجلسات التي يجب ركلها عبر CoA-Disconnect.
    coa_kick: list[tuple[str, str]] = field(default_factory=list)


MSG_CLONE = "تنبيه أمني: تم رصد محاولة دخول من جهاز مختلف بنفس عنوان MAC — الدخول مرفوض"
MSG_STEPUP = "هذا الجهاز جديد — أعد إدخال كلمة المرور للتأكيد"


def _confidence_min(tenant_id: int) -> str:
    val = _setting(tenant_id, SK_CONFIDENCE_MIN) or "medium"
    return val if val in _CONFIDENCE_RANK else "medium"


def evaluate(tenant_id: int, *,
             username: str,
             source: str,
             plan_id: Optional[int],
             group: str,
             live: AuthFingerprint) -> Optional[Verdict]:
    """يفحص طلب auth ناجح كلمة المرور. يعيد None لو الميزة مغلقة أو خارج النطاق
    أو لا MAC معتبر؛ Verdict خلاف ذلك. لا يكسر الـauth أبدًا."""
    try:
        if not is_enabled(tenant_id):
            return None
        ctx = ScopeContext(source=source, plan_id=plan_id, group=group)
        if not scope_applies(tenant_id, ctx):
            return None
        if not live.mac:
            return None  # لا MAC = لا فحص

        mode = (_setting(tenant_id, SK_MODE) or "enforce").lower()
        threshold = _confidence_min(tenant_id)
        binding = mac_clone_repo.get_binding(tenant_id, username, live.mac)

        # حارس الجلسات المتزامنة — أقوى إشارة على وجود استنساخ.
        concurrent_clone = False
        coa_kick: list[tuple[str, str]] = []
        if _flag(tenant_id, SK_CONCURRENT_GUARD):
            for s in find_concurrent_sessions(tenant_id, live.mac):
                if is_divergent_context(s, live):
                    concurrent_clone = True
                    if s.username and s.acctsessionid:
                        coa_kick.append((s.username, s.acctsessionid))

        # أول مرّة نرى هذا (المستخدم، MAC) معًا → ربط نظيف (ما لم يكن هناك
        # تزامن من سياق مختلف، وهو دليل قوي على استنساخ من أوّل لحظة).
        if binding is None:
            if concurrent_clone:
                signals = {"reason": "concurrent_divergent_no_binding",
                           "live": live.to_dict()}
                if mode == "enforce":
                    return Verdict(action="deny", reason="mac_clone_detected",
                                   message=MSG_CLONE, confidence="high",
                                   score=100, signals=signals, coa_kick=coa_kick)
                return Verdict(action="monitor", reason="mac_clone_detected",
                               message="", confidence="high", score=100,
                               signals=signals, coa_kick=coa_kick)
            return Verdict(action="allow", reason="first_bind",
                           confidence="high", score=0, signals={"new": True})

        # binding موجود — قارن.
        cmp_ = compare(binding, live)
        signals = {"diverged": cmp_.diverged, "matched": cmp_.matched,
                   "score": cmp_.score, "concurrent_divergent": concurrent_clone}

        # تزامن من سياق مختلف يرفع الثقة إلى high مباشرةً (مع الاحتفاظ بدرجة المقارنة).
        if concurrent_clone:
            cmp_.confidence = "high"
            cmp_.score = max(cmp_.score, 80)
            signals["concurrent_lift"] = True

        if not cmp_.is_clone(threshold) and not concurrent_clone:
            return Verdict(action="allow", reason="verify_ok",
                           confidence=cmp_.confidence, score=cmp_.score,
                           signals=signals)

        # نطلق قرار «استنساخ»:
        if mode == "enforce":
            return Verdict(action="deny", reason="mac_clone_detected",
                           message=MSG_CLONE,
                           confidence=cmp_.confidence, score=cmp_.score,
                           signals=signals, coa_kick=coa_kick)
        # monitor — نسجّل ولا نمنع
        return Verdict(action="monitor", reason="mac_clone_detected",
                       message="",
                       confidence=cmp_.confidence, score=cmp_.score,
                       signals=signals, coa_kick=coa_kick)
    except Exception:  # noqa: BLE001 — never break auth
        _LOG.warning("anti_mac_clone.evaluate failed for %r", username,
                     exc_info=True)
        return None


# ════════════════════════════════════════════════════════════════════════
# تطبيق النتيجة (تأثيرات جانبية: binding + event + alert + CoA)
# ════════════════════════════════════════════════════════════════════════
def apply_decision(tenant_id: int, *, username: str,
                    live: AuthFingerprint, verdict: Verdict) -> None:
    """يُسجّل الحدث + يُحدّث binding بحسب القرار + يطلق تنبيهًا + CoA إذا لزم.

    لا يكسر الـauth أبدًا (try/except حول كل الـside effects)."""
    if not verdict:
        return
    try:
        # كل فعل auth نسجّله — للتدقيق + لوحة الحوادث.
        mac_clone_repo.log_event(
            tenant_id=tenant_id, username=username, mac=live.mac,
            event_type=("clone_detected"
                        if verdict.reason == "mac_clone_detected"
                        else ("bind" if verdict.reason == "first_bind"
                              else "verify_ok")),
            decision=verdict.action,
            confidence=verdict.confidence, score=verdict.score,
            signals=verdict.signals,
            nas_ip=live.nas_ip, called_station=live.called_station,
            nas_port=live.nas_port, reason=verdict.reason,
        )
    except Exception:  # noqa: BLE001
        _LOG.warning("anti_mac_clone: failed to log event", exc_info=True)

    # binding: نُحدّث أو نُنشئ على المسارات السلمية. على clone_detected لا نكتب
    # فوق binding (نحافظ على «الجهاز الشرعي»)، لكن نزيد عدّاد mismatch.
    try:
        if verdict.reason == "mac_clone_detected":
            mac_clone_repo.bump_mismatch(tenant_id, username, live.mac)
        else:
            mac_clone_repo.upsert_binding(
                tenant_id=tenant_id, username=username, mac=live.mac,
                hostname=live.hostname, dhcp_class_id=live.dhcp_class_id,
                os_family=live.os_family, device_brand=live.device_brand,
                device_model=live.device_model,
                ua_hash=live.ua_hash, ua_sample=live.ua_sample,
                vendor_oui=live.vendor_oui,
                nas_ip=live.nas_ip, called_station=live.called_station,
                nas_port=live.nas_port, nas_port_type=live.nas_port_type,
                bind_confidence=("high" if verdict.reason == "first_bind"
                                 else "medium"),
            )
    except Exception:  # noqa: BLE001
        _LOG.warning("anti_mac_clone: binding upsert failed", exc_info=True)

    # تنبيه إدارة على الكشف الفعلي فقط (لا نُغرق بـverify_ok).
    if verdict.reason == "mac_clone_detected" and _flag(tenant_id, SK_ALERT_ENABLED):
        try:
            from .admin_alerts import dispatch
            dispatch(int(tenant_id), "mac_clone_detected", {
                "username": username,
                "mac": live.mac,
                "confidence": _ar_confidence(verdict.confidence),
                "score": str(verdict.score),
                "diverged": "، ".join(verdict.signals.get("diverged") or []) or "—",
                "nas_ip": live.nas_ip or "—",
                "called_station": live.called_station or "—",
            }, dedup_key=f"mac_clone:{username}:{live.mac}")
        except Exception:  # noqa: BLE001
            _LOG.warning("anti_mac_clone: admin alert failed", exc_info=True)

    # CoA-Disconnect للجلسة الحيّة المتزامنة (إن وُجدت + التفعيل عام + enforce).
    if (verdict.coa_kick and _flag(tenant_id, SK_COA_DISCONNECT)
            and verdict.action == "deny"):
        try:
            from .live_session_control import disconnect_live
            for kick_user, kick_sid in verdict.coa_kick:
                try:
                    disconnect_live(tenant_id=int(tenant_id),
                                    username=kick_user, session_id=kick_sid)
                    mac_clone_repo.log_event(
                        tenant_id=int(tenant_id), username=kick_user,
                        mac=live.mac, event_type="concurrent_kick",
                        decision="deny", confidence=verdict.confidence,
                        score=verdict.score,
                        signals={"kicked": kick_sid,
                                  "trigger_user": username},
                        nas_ip=live.nas_ip,
                        called_station=live.called_station,
                        nas_port=live.nas_port,
                        reason="CoA-Disconnect after clone detected",
                    )
                except Exception:  # noqa: BLE001
                    _LOG.warning("anti_mac_clone: CoA kick failed for %s/%s",
                                 kick_user, kick_sid, exc_info=True)
        except Exception:  # noqa: BLE001
            _LOG.warning("anti_mac_clone: CoA service unavailable",
                         exc_info=True)


def _ar_confidence(c: str) -> str:
    return {"low": "منخفضة", "medium": "متوسطة", "high": "عالية"}.get(c or "", c or "")


# ════════════════════════════════════════════════════════════════════════
# نقطة الواجهة لـ policy_engine
# ════════════════════════════════════════════════════════════════════════
def check_after_auth(tenant_id: int, *, username: str, source: str,
                      plan_id: Optional[int], group: str,
                      calling_station_id: str = "",
                      called_station_id: str = "",
                      nas_ip: str = "",
                      nas_port: str = "",
                      nas_port_type: str = "",
                      user_agent: str = "") -> Optional[Verdict]:
    """واجهة الاستدعاء من policy_engine بعد التحقّق من كلمة المرور.

    يبني البصمة، يفحص، يطبّق الآثار الجانبية (binding/event/alert/CoA)، ثم
    يعيد Verdict (action=allow/deny/monitor). على allow/monitor لا داعي
    لاستخدام رسالة معينة في Reply-Message. على deny ينبغي على policy_engine
    رفض الـauth وإعادة رسالة Verdict.message."""
    if not is_enabled(tenant_id):
        return None
    live = build_fingerprint(
        tenant_id=tenant_id,
        calling_station_id=calling_station_id,
        called_station_id=called_station_id,
        nas_ip=nas_ip, nas_port=nas_port, nas_port_type=nas_port_type,
        user_agent=user_agent,
    )
    verdict = evaluate(tenant_id, username=username, source=source,
                       plan_id=plan_id, group=group, live=live)
    if verdict is None:
        return None
    apply_decision(tenant_id, username=username, live=live, verdict=verdict)
    return verdict


__all__ = [
    # settings keys (للواجهة)
    "SK_ENABLED", "SK_MODE", "SK_SCOPE", "SK_SCOPE_PLAN_IDS",
    "SK_SCOPE_GROUP_NAMES", "SK_CONFIDENCE_MIN", "SK_CONCURRENT_GUARD",
    "SK_ALERT_ENABLED", "SK_COA_DISCONNECT", "SK_RAW_LIMIT",
    # data classes
    "AuthFingerprint", "Comparison", "Verdict",
    "ScopeContext", "ConcurrentSession",
    # API
    "is_enabled", "get_settings", "set_settings",
    "scope_applies", "build_fingerprint", "hash_user_agent",
    "compare", "find_concurrent_sessions", "is_divergent_context",
    "evaluate", "apply_decision", "check_after_auth",
    # messages
    "MSG_CLONE", "MSG_STEPUP",
]
