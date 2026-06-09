"""رفع آمن لملفات الهوت سبوت الكبيرة إلى الراوتر.

المشكلة الجذرية: `/file/add contents=<html>` في نداء API واحد يحمل
HTML كبيرًا (شعار/خط base64 مضمّن) فبعض إصدارات RouterOS تقطع الاتصال
(Connection reset by peer) على الحمولة الضخمة — reset «ثابت» لا عابر،
فلا تنفع إعادة المحاولة وحدها. الحلول هنا (تُركّب معًا):

  1) extract_inline_images — تنزع صور data:base64 الكبيرة من الـHTML
     وتعيدها ملفات منفصلة، فيصغر login.html كثيرًا ويمرّ عبر API بأمان.
     الصور binary فتُرفع عبر FTP (الـAPI لا يحمل binary موثوقًا).

  2) ftp_upload — رفع متدفّق مجزّأ عبر FTP (STOR ثنائي بدفعات صغيرة):
     يتجاوز حدّ جملة API الواحدة ويحمل الـbinary، مع تقدّم حسب الأجزاء.
     FTP على RouterOS يستعمل نفس اعتماد مستخدم الراوتر — لا أسرار زائدة.

لا تخزين أسرار جديدة: إعداد FTP يُبنى من نفس عنوان/مستخدم/كلمة مرور
API الموجودة في nas_devices.
"""
from __future__ import annotations

import base64
import ftplib
import io
import re
from dataclasses import dataclass

# حدّ حجم الحمولة الآمن لنداء API الواحد (بايت). فوقه نفضّل FTP مباشرة
# بدل المخاطرة بـ reset. ~50KB هامش متحفّظ يمرّ على كل إصدارات RouterOS.
API_SAFE_BYTES = 50_000

# الحدّ الأدنى لحجم صورة مضمّنة تستحق فصلها لملف (أصغر منه لا يفيد).
MIN_ASSET_BYTES = 2_048

# دفعة الرفع عبر FTP — صغيرة فلا تصطدم حمولة واحدة بحدّ الراوتر.
FTP_BLOCKSIZE = 8_192


_DATA_IMG_RE = re.compile(
    r"data:image/(?P<ext>png|jpe?g|gif|webp|svg\+xml);base64,"
    r"(?P<b64>[A-Za-z0-9+/=]+)"
)

_EXT_MAP = {
    "png": "png", "jpg": "jpg", "jpeg": "jpg",
    "gif": "gif", "webp": "webp", "svg+xml": "svg",
}


@dataclass
class InlineAsset:
    """أصل (صورة) نُزع من HTML ليُرفع ملفًا منفصلًا."""
    filename: str   # اسم نسبي بجانب login.html (مثل hr-asset-1.png)
    data: bytes     # الـbinary المفكوك من base64


def extract_inline_images(html: str, *,
                          min_bytes: int = MIN_ASSET_BYTES,
                          prefix: str = "hr-asset"):
    """ينزع صور `data:...;base64,` الكبيرة من الـHTML ويستبدل كلّ واحدة
    بمسار نسبي لاسم ملف منفصل. يعيد (html_جديد, [InlineAsset...]).

    الصور الصغيرة (< min_bytes) تبقى مضمّنة (لا تستحق ملفًا/رحلة FTP).
    base64 غير الصالح يُترك كما هو (لا نكسر الصفحة)."""
    assets: list[InlineAsset] = []
    counter = {"n": 0}

    def _repl(m: "re.Match[str]") -> str:
        raw = m.group("b64")
        try:
            blob = base64.b64decode(raw, validate=True)
        except Exception:  # noqa: BLE001 — base64 معطوب → اتركه مضمّنًا
            return m.group(0)
        if len(blob) < min_bytes:
            return m.group(0)
        ext = _EXT_MAP.get(m.group("ext").lower(), "img")
        counter["n"] += 1
        name = f"{prefix}-{counter['n']}.{ext}"
        assets.append(InlineAsset(filename=name, data=blob))
        return name

    new_html = _DATA_IMG_RE.sub(_repl, html)
    return new_html, assets


def count_chunks(total: int, blocksize: int = FTP_BLOCKSIZE) -> int:
    """عدد دفعات الرفع المتوقّعة لحجم معطى (للعرض في شريط التقدّم)."""
    if total <= 0:
        return 0
    return (total + blocksize - 1) // blocksize


class FtpUploadError(Exception):
    """فشل رفع عبر FTP (اتصال/مصادقة/STOR) — يحمل سببًا واضحًا."""


def ftp_upload(host: str, user: str, password: str, remote_path: str,
               data: bytes, *, port: int = 21, timeout: float = 30.0,
               on_progress=None, blocksize: int = FTP_BLOCKSIZE,
               _ftp_factory=None) -> int:
    """رفع `data` إلى `remote_path` (نسبي لجذر ملفات الراوتر، مثل
    'hotspot/login.html') عبر FTP بدفعات صغيرة (STOR ثنائي).

    `on_progress(sent, total)` يُستدعى بعد كل دفعة. يعيد عدد البايت
    المرفوعة. يرمي FtpUploadError على أي فشل برسالة عربية واضحة.

    `_ftp_factory` نقطة حقن للاختبار (تُرجع كائن FTP وهميًا)."""
    total = len(data)
    sent = {"n": 0}
    bio = io.BytesIO(data)

    try:
        ftp = (_ftp_factory() if _ftp_factory is not None else ftplib.FTP())
    except Exception as e:  # noqa: BLE001
        raise FtpUploadError(f"تعذّر تهيئة FTP: {e}") from e

    try:
        ftp.connect(host, port, timeout)
        ftp.login(user, password)
        try:
            ftp.set_pasv(True)
        except Exception:  # noqa: BLE001 — بعض الكائنات الوهمية بلا الدالة
            pass

        def _cb(block: bytes) -> None:
            sent["n"] += len(block)
            if on_progress:
                try:
                    on_progress(sent["n"], total)
                except Exception:  # noqa: BLE001
                    pass

        ftp.storbinary("STOR " + remote_path, bio,
                       blocksize=blocksize, callback=_cb)
    except FtpUploadError:
        raise
    except Exception as e:  # noqa: BLE001
        raise FtpUploadError(_short_ftp_reason(e)) from e
    finally:
        try:
            ftp.quit()
        except Exception:  # noqa: BLE001
            try:
                ftp.close()
            except Exception:  # noqa: BLE001
                pass
    return sent["n"]


def _short_ftp_reason(e: BaseException) -> str:
    """رسالة عربية مختصرة لفشل FTP الشائع."""
    low = str(e).lower()
    if "refused" in low or "timed out" in low or "timeout" in low:
        return ("تعذّر الوصول إلى خدمة FTP على الراوتر (مرفوض/مهلة) — "
                "فعّل FTP على الراوتر أو افتح المنفذ 21 عبر النفق.")
    if "login" in low or "530" in low or "credentials" in low:
        return ("رُفض اعتماد FTP — تأكّد أن مستخدم API يملك صلاحية ftp "
                "على الراوتر.")
    return "فشل الرفع عبر FTP: " + str(e)


def ftp_config_from_nas(row, *, default_port: int = 21,
                        timeout: float = 30.0) -> dict | None:
    """يبني إعداد FTP من صفّ nas_devices (نفس عنوان/مستخدم/كلمة مرور
    API — لا أسرار زائدة). يعيد None إن نقص اعتماد جوهري.

    `host` يُمرَّر من المستدعي (بعد حلّ عنوان الاتصال/النفق) لأن منطق
    resolve_connection_address يعيش في طبقة الراوت."""
    user = (row.get("api_user") if hasattr(row, "get") else row["api_user"]) or ""
    pw = (row.get("api_password") if hasattr(row, "get")
          else row["api_password"]) or ""
    if not user:
        return None
    return {"user": user, "password": pw, "port": default_port,
            "timeout": timeout}
