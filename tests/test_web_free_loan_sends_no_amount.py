"""Web «منح سلفة» → «مجانية» must send amount 0 (2026-09-28).

The hidden read-only amount field was filled with the auto price in BOTH
modes, so on a priced plan a «free» loan was recorded as a debt carrying
that value. The dialog now sends the value in debt mode only — matching the
API (/api/v1/accounts/<u>/loan) used by the mobile app."""
from pathlib import Path


def test_free_loan_dialog_sends_zero_amount():
    html = Path("app/templates/radius/users_list.html").read_text(encoding="utf-8")
    html = html.replace("\r\n", "\n")
    start = html.index("function syncLoanAmount()")
    loan_fn = html[start:html.index("\n  }\n", start)]
    # Only the loan dialog changed; the quota dialog is priced by its own mode.
    assert 'amtField.value = (debt && amount) ? amount.toFixed(2) : "0";' in loan_fn
