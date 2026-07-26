"""MT63 — محتوى صفحة المنصّة يُدار من اللوحة.

يَقفل عقدين: البذرة = ما هو منشور (فلا يتغيّر شكل الصفحة عند التفعيل)،
وأيّ مسار سقوط يُنتج **نفس شكل** المسار العاديّ (درس MT62.1).
"""
from __future__ import annotations

from app.radius.services import landing_content as lc


def test_seed_has_every_key_the_template_reads():
    """القالب لا يرى مفتاحًا غائبًا — وإلّا ظهرت الصفحة ناقصة."""
    c = lc.get_content()
    for k in lc._TEXT_FIELDS:
        assert k in c and c[k], f"مفتاح مفقود/فارغ: {k}"
    for k in ("trust", "features", "steps"):
        assert isinstance(c[k], list) and c[k], f"{k} فارغة"
    for f in c["features"]:
        assert {"icon", "title", "text"} <= set(f)
    for s in c["steps"]:
        assert {"title", "text"} <= set(s)


def test_partial_input_falls_back_per_field_not_wholesale():
    """حقلٌ فارغ يرجع لبذرته وحده — لا تُمسح بقيّة ما كتبه المزوّد."""
    out = lc._clean({"title": "عنواني", "lede": ""})
    assert out["title"] == "عنواني"
    assert out["lede"] == lc._SEED["lede"]


def test_empty_rows_are_dropped_as_intentional_delete():
    """صفٌّ بلا عنوان ولا نصّ = حذفٌ مقصود من الواجهة."""
    out = lc._clean({
        "features": [{"icon": "wifi", "title": "أ", "text": "ب"},
                     {"icon": "x", "title": "", "text": ""}],
        "steps": [{"title": "", "text": ""}, {"title": "خ", "text": "ن"}],
        "trust": ["شارة", "   "],
    })
    assert len(out["features"]) == 1 and out["features"][0]["title"] == "أ"
    assert len(out["steps"]) == 1 and out["steps"][0]["title"] == "خ"
    assert out["trust"] == ["شارة"]


def test_icon_is_sanitised_and_lists_are_capped():
    """أيقونةٌ غريبة تُستبدل، والقوائم لا تنمو بلا حدّ (لصقٌ ضخم)."""
    out = lc._clean({"features": [{"icon": "<script>", "title": "t", "text": "x"}]})
    assert out["features"][0]["icon"] == "star"
    many = [{"title": f"t{i}", "text": "x"} for i in range(100)]
    assert len(lc._clean({"features": many})["features"]) <= lc._MAX_ITEMS


def test_never_raises_on_garbage():
    """مُدخَلٌ فاسد لا يُسقط صفحةً عامّة."""
    for bad in ({"features": "not-a-list"}, {"steps": [1, 2, 3]},
                {"trust": None}, {}):
        out = lc._clean(bad)
        assert isinstance(out["features"], list)
        assert isinstance(out["steps"], list)
        assert out["title"]
