"""مُخصِّص الـ Framed-IP الثابت لكل مستخدم (CHR Fleet — Phase 2 / T6).

RADIUS هو **المصدر الوحيد** لعنوان IP الخاص بكل مستخدم؛ الـ CHR لا يملك
pool محليًا (انظر `docs/chr_fleet/04_FIXED_IP_AND_SESSIONS.md`). الثابت الأساسي:

    «عنوان Framed-IP-Address ملك للمستخدم، لا للـ CHR».

أي أن RADIUS يُعيد نفس `Framed-IP-Address` لاسم مستخدم معيّن في **كل**
Access-Accept وعلى **كل** CHR، فيحصل المستخدم المتنقّل من CHR إلى آخر على
نفس الـ IP الداخلي (هدف G2: لا عناوين مكرّرة).

التصميم (مطابق للوثيقة 04 §4.2/§4.3 و 02 §2.12):

  • جدول `fixed_ip_pool(username PK, framed_ip UNIQUE, customer_id,
    assigned_at)` هو المرجع الموثوق هنا (اللوحة تعكسه للقراءة فقط).
  • لكل عميل (customer) شريحة خاصة `10.<cust>.0.0/16` مشتقّة من
    `customer_id` — فلا تتصادم العناوين بين العملاء.
  • داخل شريحة العميل يُشتقّ عنوان المستخدم **حتميًا** من اسمه (هاش
    مستقر)، فيكون ثابتًا عبر الزمن وعبر كل الـ CHRs.
  • الفرادة مضمونة بثلاث طبقات مستقلّة؛ هنا نملك الطبقة الثانية:
    `framed_ip` UNIQUE في القاعدة. عند تصادم هاشين لاسمين مختلفين نتفادى
    التكرار بـ probe حتمي لأقرب عنوان حُرّ، ويرفض القيد UNIQUE أي تكرار فعلي.
  • التخصيص **idempotent**: إعادة التخصيص لمستخدم قائم تُعيد عنوانه نفسه.
  • الإلغاء فقط عند حذف المستخدم/تحرير صريح (لا churn يكسر «نفس الـ IP أبدًا»).
"""
from __future__ import annotations

import hashlib
import ipaddress
import os
import sqlite3
from dataclasses import dataclass
from typing import Optional

from .core.errors import RadiusConflict, RadiusError, RadiusValidationError
from .db.connection import db
from .db.helpers import now_iso

# ─── الإعداد: النطاق الخاص القابل للضبط ───────────────────────────────
# الافتراضي 10.0.0.0/8 مقسّمًا شرائح /16 لكل عميل (10.<cust>.0.0/16).
_ENV_SUPERNET = "HOBERADIUS_FIXED_IP_SUPERNET"
_ENV_CUSTOMER_PREFIX = "HOBERADIUS_FIXED_IP_CUSTOMER_PREFIX"
_DEFAULT_SUPERNET = "10.0.0.0/8"
_DEFAULT_CUSTOMER_PREFIX = 16


@dataclass(frozen=True)
class FixedIpConfig:
    """نطاق الـ IP الخاص: شبكة-أمّ تُقسَّم شرائح متساوية، شريحة لكل عميل."""
    supernet: str = _DEFAULT_SUPERNET
    customer_prefix: int = _DEFAULT_CUSTOMER_PREFIX

    def network(self) -> ipaddress.IPv4Network:
        net = ipaddress.ip_network(self.supernet, strict=False)
        if not isinstance(net, ipaddress.IPv4Network):
            raise RadiusValidationError("النطاق الثابت يجب أن يكون IPv4")
        if not (net.prefixlen <= self.customer_prefix <= 32):
            raise RadiusValidationError(
                "customer_prefix خارج حدود الشبكة-الأمّ")
        return net

    def block_size(self) -> int:
        return 1 << (32 - self.customer_prefix)

    def customer_count(self) -> int:
        return self.network().num_addresses // self.block_size()


def default_config() -> FixedIpConfig:
    """يقرأ الإعداد من البيئة مع الرجوع للافتراضي (10.0.0.0/8 شرائح /16)."""
    supernet = os.environ.get(_ENV_SUPERNET) or _DEFAULT_SUPERNET
    raw_prefix = os.environ.get(_ENV_CUSTOMER_PREFIX)
    try:
        prefix = int(raw_prefix) if raw_prefix else _DEFAULT_CUSTOMER_PREFIX
    except (TypeError, ValueError):
        prefix = _DEFAULT_CUSTOMER_PREFIX
    return FixedIpConfig(supernet=supernet, customer_prefix=prefix)


class FixedIpExhausted(RadiusError):
    """لا يوجد عنوان حُرّ في شريحة العميل — النطاق ممتلئ."""
    code = "fixed_ip_exhausted"
    http_status = 409


# ─── المخطّط ──────────────────────────────────────────────────────────

def ensure_schema() -> None:
    """يُنشئ جدول fixed_ip_pool إن لم يكن موجودًا. idempotent.

    `framed_ip` UNIQUE هو ضمان الفرادة على مستوى القاعدة (طبقة الدفاع 2
    في وثيقة 04 §4.3): عنوان واحد يقابل مستخدمًا واحدًا على الأكثر."""
    db().execute(
        """
        CREATE TABLE IF NOT EXISTS fixed_ip_pool (
            username    TEXT    PRIMARY KEY,
            framed_ip   TEXT    NOT NULL UNIQUE,
            customer_id INTEGER NOT NULL,
            assigned_at TEXT    NOT NULL
        )
        """
    )


# ─── اشتقاق حتمي ──────────────────────────────────────────────────────

def _usable_bounds(cfg: FixedIpConfig) -> tuple[int, int]:
    """مدى الإزاحات الصالحة داخل شريحة العميل [start, end] شاملًا.

    نستثني عنوان الشبكة (0)، وعنوان البوّابة/local-address للـ CHR (1)،
    وعنوان البثّ (الأخير) — انظر 04 §4.2."""
    size = cfg.block_size()
    start, end = 2, size - 2
    if end < start:
        raise RadiusValidationError(
            "شريحة العميل أصغر من أن تتّسع لمضيف واحد")
    return start, end


def customer_network(customer_id: int, cfg: Optional[FixedIpConfig] = None
                     ) -> ipaddress.IPv4Network:
    """شريحة العميل /N المشتقّة حتميًا من customer_id (10.<cust>.0.0/16)."""
    cfg = cfg or default_config()
    if customer_id is None or int(customer_id) < 0:
        raise RadiusValidationError("customer_id غير صالح")
    net = cfg.network()
    index = int(customer_id) % cfg.customer_count()
    base = int(net.network_address) + index * cfg.block_size()
    return ipaddress.ip_network((base, cfg.customer_prefix))


def _start_offset(username: str, customer_id: int, usable_count: int) -> int:
    """إزاحة البداية الحتمية للمستخدم داخل المدى الصالح (هاش مستقر)."""
    seed = f"{int(customer_id)}:{username}".encode("utf-8")
    digest = hashlib.sha256(seed).digest()
    return int.from_bytes(digest, "big") % usable_count


def _ip_at(net_base: int, offset: int) -> str:
    return str(ipaddress.IPv4Address(net_base + offset))


# ─── واجهة المُخصِّص ──────────────────────────────────────────────────

def framed_ip_for(username: str) -> Optional[str]:
    """قراءة فقط: عنوان المستخدم المخزَّن (يُستخدم وقت Access-Accept) أو None.

    لا يُخصِّص — يُعيد ما هو محفوظ فقط، فيبقى نفس العنوان على كل CHR."""
    if not username:
        return None
    ensure_schema()
    row = db().execute(
        "SELECT framed_ip FROM fixed_ip_pool WHERE username = ?",
        (username,),
    ).fetchone()
    return row["framed_ip"] if row else None


def allocate_fixed_ip(username: str, customer_id: int,
                      cfg: Optional[FixedIpConfig] = None) -> str:
    """يُرجع الـ Framed-IP الثابت للمستخدم، مُخصِّصًا إيّاه مرّة واحدة.

    • idempotent: إن كان للمستخدم عنوان محفوظ يُعاد كما هو (نفس الـ IP أبدًا).
    • فريد: العنوان مشتقّ حتميًا من الاسم؛ وعند تصادم اسمين نتفادى التكرار
      بـ probe حتمي لأقرب عنوان حُرّ، ويرفض قيد UNIQUE أي تكرار فعلي.
    • يرفع FixedIpExhausted إذا امتلأت شريحة العميل.
    """
    username = (username or "").strip()
    if not username:
        raise RadiusValidationError("username مطلوب")
    cfg = cfg or default_config()
    ensure_schema()

    # (1) idempotency — أعِد العنوان المحفوظ إن وُجد.
    existing = framed_ip_for(username)
    if existing is not None:
        return existing

    # (2) المرشّح الحتمي ثم probe حتمي ضمن المدى الصالح.
    net = customer_network(int(customer_id), cfg)
    net_base = int(net.network_address)
    start, end = _usable_bounds(cfg)
    usable_count = end - start + 1
    base_idx = _start_offset(username, int(customer_id), usable_count)
    assigned_at = now_iso()

    for i in range(usable_count):
        offset = start + ((base_idx + i) % usable_count)
        candidate = _ip_at(net_base, offset)
        try:
            db().execute(
                "INSERT INTO fixed_ip_pool"
                "(username, framed_ip, customer_id, assigned_at) "
                "VALUES (?,?,?,?)",
                (username, candidate, int(customer_id), assigned_at),
            )
            return candidate
        except sqlite3.IntegrityError:
            # إمّا أن مستخدمًا آخر سبقنا لنفس username (سباق) → أعِد عنوانه؛
            # أو أن candidate محجوز لمستخدم آخر (قيد framed_ip UNIQUE) → probe.
            raced = framed_ip_for(username)
            if raced is not None:
                return raced
            continue

    raise FixedIpExhausted(
        f"شريحة العميل {net} ممتلئة — لا عنوان حُرّ للمستخدم {username!r}")


def release_fixed_ip(username: str) -> bool:
    """يحرّر عنوان المستخدم (فقط عند حذف المستخدم/تحرير صريح).

    يُعيد True إن حُذف صفّ. لا يُستدعى ضمن التشغيل العادي كي لا يُكسر
    وعد «نفس الـ IP أبدًا»."""
    username = (username or "").strip()
    if not username:
        return False
    ensure_schema()
    cur = db().execute(
        "DELETE FROM fixed_ip_pool WHERE username = ?", (username,))
    return cur.rowcount > 0


def assign_specific_ip(username: str, framed_ip: str, customer_id: int) -> str:
    """يربط عنوانًا محدّدًا باسم مستخدم (هجرة/استيراد). يرفع RadiusConflict
    إن كان العنوان محجوزًا لمستخدم آخر (قيد UNIQUE) — إثبات «رفض التكرار»."""
    username = (username or "").strip()
    if not username:
        raise RadiusValidationError("username مطلوب")
    try:
        ipaddress.IPv4Address(framed_ip)
    except (ipaddress.AddressValueError, ValueError):
        raise RadiusValidationError(f"عنوان غير صالح: {framed_ip!r}")
    ensure_schema()
    existing = framed_ip_for(username)
    if existing is not None:
        if existing == framed_ip:
            return existing
        raise RadiusConflict(
            f"للمستخدم {username!r} عنوان مختلف مخصَّص ({existing})")
    try:
        db().execute(
            "INSERT INTO fixed_ip_pool"
            "(username, framed_ip, customer_id, assigned_at) VALUES (?,?,?,?)",
            (username, framed_ip, int(customer_id), now_iso()),
        )
    except sqlite3.IntegrityError:
        raise RadiusConflict(
            f"العنوان {framed_ip} محجوز مسبقًا لمستخدم آخر")
    return framed_ip


__all__ = [
    "FixedIpConfig",
    "FixedIpExhausted",
    "default_config",
    "ensure_schema",
    "customer_network",
    "framed_ip_for",
    "allocate_fixed_ip",
    "release_fixed_ip",
    "assign_specific_ip",
]
