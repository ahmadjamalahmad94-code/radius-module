"""r6ui: مخرجاتُ dt_local معزولةٌ بـLRI…PDI للعرض — فلا يجوز تقطيعُها ولا تفكيكُها.

الانحدار (5e8802b7): العزلُ أضاف محرفًا في أوّل النصّ فصار «[:10]» = «2026-10-0»
و«[11:16]» = « 17:0» في 15 تقريرًا، و«⁦2026»|int = 0 فيفرغ تاريخُ الانتهاء في
نموذجِ تعديلِ المشترك، و`new Date("⁦…")` = Invalid Date في نافذةِ التمديد.
"""
import glob
import re

LRI, PDI = "\u2066", "\u2069"


def test_raw_filters_are_registered_and_unisolated():
    from app import create_app
    app = create_app()
    f = app.jinja_env.filters
    assert "dt_local_raw" in f and "date_local_raw" in f
    out = f["dt_local_raw"]("2026-10-01T09:49:54Z")
    assert LRI not in out and PDI not in out
    assert re.match(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}", out), out
    iso = f["dt_local"]("2026-10-01T09:49:54Z")
    assert iso.startswith(LRI)  # العرضُ ما زال معزولًا


def test_no_template_slices_or_splits_an_isolated_dt_local():
    bad = []
    sliced_vars = re.compile(r"\{% set (\w+) = [^%]*\|(?:dt_local|date_local)\b(?!_raw)")
    for path in glob.glob("app/templates/**/*.html", recursive=True) + glob.glob("app/radius/templates/**/*.html", recursive=True):
        src = open(path, encoding="utf-8").read()
        if re.search(r"\|(?:dt_local|date_local)(?:\([^)]*\))?\)\s*\[", src):
            bad.append(path + ": inline slice")
        for m in sliced_vars.finditer(src):
            v = m.group(1)
            if re.search(re.escape(v) + r"\s*(\[|\.split\()", src):
                bad.append(f"{path}: {v}")
        if re.search(r"data-[\w-]+=\"\{\{[^}]*\|(?:dt_local|date_local)\(", src):
            bad.append(path + ": data-* attribute")
    assert not bad, bad
