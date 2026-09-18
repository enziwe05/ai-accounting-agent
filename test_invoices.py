"""
Unit tests for the pure invoice helpers — no database, no network.

These cover the two bits of logic that must always be right: splitting VAT out of
a total, and deciding when an invoice counts as overdue.
"""

from datetime import date

from invoices import invoice_view_status, split_vat


# --- split_vat ---------------------------------------------------------------

def test_split_vat_exclusive_has_no_vat():
    subtotal, vat = split_vat(1000, vat_inclusive=False)
    assert subtotal == 1000.0
    assert vat == 0.0


def test_split_vat_inclusive_backs_out_15_percent():
    subtotal, vat = split_vat(5000, vat_inclusive=True)
    assert subtotal == 4347.83
    assert vat == 652.17
    # The pieces must add back up to the original total, to the cent.
    assert round(subtotal + vat, 2) == 5000.00


def test_split_vat_rounds_to_cents():
    subtotal, vat = split_vat(100, vat_inclusive=True)
    assert subtotal == 86.96
    assert vat == 13.04
    assert round(subtotal + vat, 2) == 100.00


# --- invoice_view_status -----------------------------------------------------

TODAY = date(2026, 9, 18)


def test_paid_and_cancelled_are_shown_as_is():
    assert invoice_view_status("paid", date(2020, 1, 1), TODAY) == "paid"
    assert invoice_view_status("cancelled", date(2020, 1, 1), TODAY) == "cancelled"
    assert invoice_view_status("draft", None, TODAY) == "draft"


def test_sent_before_due_is_outstanding():
    assert invoice_view_status("sent", date(2026, 10, 2), TODAY) == "outstanding"


def test_sent_past_due_is_overdue():
    assert invoice_view_status("sent", date(2026, 9, 1), TODAY) == "overdue"


def test_sent_due_today_is_not_yet_overdue():
    # Due today still counts as outstanding — grace until the day passes.
    assert invoice_view_status("sent", TODAY, TODAY) == "outstanding"


def test_sent_with_no_due_date_is_outstanding():
    assert invoice_view_status("sent", None, TODAY) == "outstanding"
