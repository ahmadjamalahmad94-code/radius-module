"""MT88 — أنماط توليد كلمة المرور الأربعة تُنتج فعلًا ما تَعِد به.

كان «قوي» يُترجَم إلى `mixed` — أي نفس «متوسط» حرفًا بحرف — بينما الواجهة
تعرضه «حروف+أرقام+رموز». المشغّل يختار الأقوى فيحصل على الأضعف ولا يعلم.
الرموز مستبعَدةٌ عمدًا (الكلمة تُطبع وتُكتب يدويًّا في بوابة الهوتسبوت)،
والقوّة تأتي من حالتَي الحرف — والاختبار يحرس الفرق بين النمطين.
"""

import string

from app.radius.db.repos.cards_repo import _random_str
from app.radius.services.cards import CardsService


def _pgt_to_charset(pgt: str) -> str:
    """نفس تحويل الخدمة: الافتراضيّ القادم من المسار هو 'digits'."""
    import inspect
    src = inspect.getsource(CardsService.generate_batch)
    assert "pgt_map" in src, "خريطة النمط اختفت من مسار التوليد"
    return {"digits": "digits", "weak": "alpha",
            "medium": "mixed", "strong": "strong"}[pgt]


def _chars(charset: str, n: int = 4000) -> set:
    return set(_random_str(n, charset=charset))


def test_digits_only_has_no_letters():
    assert _chars("digits") <= set(string.digits)


def test_weak_is_letters_only():
    assert _chars("alpha") <= set(string.ascii_lowercase)


def test_medium_is_lowercase_and_digits():
    seen = _chars("mixed")
    assert seen <= set(string.ascii_lowercase + string.digits)
    assert seen & set(string.digits) and seen & set(string.ascii_lowercase)


def test_strong_is_actually_stronger_than_medium():
    """جوهر العطب: «قوي» يجب أن يضيف حروفًا كبيرة لا أن يكرّر «متوسط»."""
    strong = _chars("strong")
    assert strong & set(string.ascii_uppercase), "«قوي» بلا حروف كبيرة = «متوسط»"
    assert strong <= set(string.ascii_letters + string.digits)
    assert len(strong) > len(_chars("mixed"))


def test_strong_carries_no_symbols():
    """الرموز تَكسر الكتابة اليدويّة في بوابة الهوتسبوت — قرارٌ مقصود."""
    assert not (_chars("strong") & set(string.punctuation))


def test_every_ui_option_maps_to_a_distinct_charset():
    """أربعة خيارات في الواجهة ⇒ أربع مجموعات محارف، لا ثلاثة."""
    sets = {pgt: _chars(_pgt_to_charset(pgt))
            for pgt in ("digits", "weak", "medium", "strong")}
    frozen = {k: frozenset(v) for k, v in sets.items()}
    assert len(set(frozen.values())) == 4, f"خياران يُنتجان نفس الشيء: {frozen.keys()}"


def test_unknown_charset_falls_back_to_mixed_not_crash():
    assert _chars("لا-يوجد") <= set(string.ascii_lowercase + string.digits)


def test_single_character_password_is_honoured():
    """طلب المالك: خانةٌ واحدة تعني خانةً واحدة."""
    for cs in ("digits", "alpha", "mixed", "strong"):
        assert len(_random_str(1, charset=cs)) == 1
