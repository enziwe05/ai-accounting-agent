"""
Unit tests for the reconciliation matcher (match_line). Pure — no DB, no network.
Run:  python -m pytest test_reconcile.py -v
"""

from datetime import date

from reconcile import Candidate, match_line


def cand(tid, vendor, d, amount):
    return Candidate(transaction_id=tid, vendor=vendor, txn_date=d, amount=amount)


def test_exact_amount_and_close_date_matches():
    cands = [cand(1, "Spar Erica", date(2026, 2, 7), 679.61)]
    m = match_line(date(2026, 2, 10), 679.61, "POS Purchase Spar Erica", cands)
    assert m is not None and m.transaction_id == 1


def test_amount_mismatch_no_match():
    cands = [cand(1, "Spar", date(2026, 2, 7), 679.61)]
    assert match_line(date(2026, 2, 10), 500.00, "POS Purchase Spar", cands) is None


def test_date_outside_window_no_match():
    cands = [cand(1, "Spar", date(2026, 1, 1), 100.00)]
    # 40 days apart, tolerance default 4
    assert match_line(date(2026, 2, 10), 100.00, "POS Purchase Spar", cands) is None


def test_vendor_breaks_the_tie():
    # Two candidates, same amount & date; the vendor name in the description decides.
    cands = [
        cand(1, "Uber", date(2026, 2, 6), 67.00),
        cand(2, "Spar", date(2026, 2, 6), 67.00),
    ]
    m = match_line(date(2026, 2, 9), 67.00, "POS Purchase Uber 479056", cands)
    assert m.transaction_id == 1


def test_ambiguous_without_vendor_signal_returns_none():
    # Same amount, both within date window, neither vendor in the description.
    cands = [
        cand(1, "Shop A", date(2026, 2, 9), 50.00),
        cand(2, "Shop B", date(2026, 2, 9), 50.00),
    ]
    assert match_line(date(2026, 2, 9), 50.00, "Cash withdrawal", cands) is None


def test_closest_date_wins_when_amounts_equal():
    cands = [
        cand(1, "Shop A", date(2026, 2, 1), 50.00),
        cand(2, "Shop B", date(2026, 2, 8), 50.00),
    ]
    # No vendor signal, but pick the closest date to 2026-02-09 -> Shop B.
    m = match_line(date(2026, 2, 9), 50.00, "Cash", cands, days_tol=15)
    assert m is not None and m.transaction_id == 2


def test_cents_matter():
    cands = [cand(1, "Spar", date(2026, 2, 7), 679.61)]
    assert match_line(date(2026, 2, 8), 679.60, "Spar", cands) is None
