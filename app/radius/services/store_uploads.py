"""store_uploads — حفظ صور المتجر بأمان (وصولات الإيداع + مرفقات الشات).

الصور تُحفظ تحت static/uploads/store/<subdir>/ باسم عشوائي غير قابل
للتخمين (token_urlsafe) فلا يُسرّب رابطٌ متسلسلٌ صورَ زبون آخر، وتُقدَّم
عبر مسار static العادي. نتحقق من النوع (بايتات سحرية + الامتداد)
والحجم قبل الكتابة — لا نثق بامتداد العميل وحده.

الخدمة لا تحرّك مالًا ولا تلمس قاعدة البيانات؛ مجرد كتابة ملف مفحوص
وإرجاع مساره النسبي ورابطه ليخزّنهما المستدعي في جدوله.
"""
from __future__ import annotations

import secrets
from pathlib import Path
from typing import Any

# الحد الأقصى لحجم الصورة (6MB) — وصل/لقطة شاشة تكفيها بمراحل.
MAX_IMAGE_BYTES = 6 * 1024 * 1024

# الأنواع المسموحة: امتداد العرض ← توقيع البايتات المتوقّع.
_ALLOWED = ("png", "jpg", "jpeg", "webp", "gif")


class StoreUploadError(ValueError):
    """خطأ تحقّق آمن في رفع صورة المتجر (نوع/حجم)."""


def _sniff_ext(raw: bytes) -> str:
    """يستنتج امتداد الصورة من بايتاتها السحرية — لا نثق بامتداد
    العميل. يعيد '' إن لم تكن صورة من الأنواع المدعومة."""
    if raw[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if raw[:3] == b"\xff\xd8\xff":
        return "jpg"
    if raw[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return "webp"
    return ""


def _safe_subdir(subdir: str) -> str:
    """يقيّد المجلد الفرعي لأحرف آمنة فقط (يمنع اجتياز المسار)."""
    clean = "".join(c for c in str(subdir or "") if c.isalnum() or c in "_-")
    return clean or "misc"


def _static_root() -> Path:
    """جذر static للتطبيق الحالي (app/static)."""
    from flask import current_app
    root = current_app.static_folder
    if not root:
        raise StoreUploadError("تعذّر تحديد مجلد static.")
    return Path(root)


def save_store_image(file_storage: Any, *, subdir: str,
                     max_bytes: int = MAX_IMAGE_BYTES) -> dict[str, str]:
    """يحفظ صورة مرفوعة (Werkzeug FileStorage) ويعيد مسارها النسبي
    ورابطها. يرفع StoreUploadError عند نوع/حجم غير صالح.

    الإرجاع: {"path": "uploads/store/<subdir>/<rnd>.<ext>",
              "url":  "/static/uploads/store/<subdir>/<rnd>.<ext>"}
    """
    if file_storage is None or not getattr(file_storage, "filename", ""):
        raise StoreUploadError("لم تُرفق صورة.")
    raw = file_storage.read(max_bytes + 1)
    if not raw:
        raise StoreUploadError("الصورة فارغة أو غير قابلة للقراءة.")
    if len(raw) > max_bytes:
        raise StoreUploadError(
            f"حجم الصورة يتجاوز {max_bytes // (1024 * 1024)}MB.")
    ext = _sniff_ext(raw)
    if not ext:
        raise StoreUploadError("الملف ليس صورة مدعومة (PNG/JPG/WEBP/GIF).")
    sub = _safe_subdir(subdir)
    rel_dir = Path("uploads") / "store" / sub
    abs_dir = _static_root() / rel_dir
    abs_dir.mkdir(parents=True, exist_ok=True)
    name = f"{secrets.token_urlsafe(18)}.{ext}"
    (abs_dir / name).write_bytes(raw)
    rel_path = str(rel_dir / name).replace("\\", "/")
    return {"path": rel_path, "url": "/static/" + rel_path}


def store_image_url(rel_path: str) -> str:
    """رابط العرض لمسار صورة مخزَّن — فارغ يبقى فارغًا."""
    rel = str(rel_path or "").strip().lstrip("/")
    if not rel:
        return ""
    if rel.startswith("static/"):
        return "/" + rel
    return "/static/" + rel


__all__ = [
    "MAX_IMAGE_BYTES",
    "StoreUploadError",
    "save_store_image",
    "store_image_url",
]
