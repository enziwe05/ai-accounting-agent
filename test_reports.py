"""
Unit tests for the report aggregator (summarize). Pure — no DB, no network.
Run:  python -m pytest test_reports.py -v
"""

from reports import summarize


def test_income_expense_net():
    rows = [
        {"amount": 5000, "kind": "income", "category": "Sales"},
        {"amount": 800, "kind": "expense", "category": "Fuel"},
        {"amount": 200, "kind": "expense", "category": "Fuel"},
    ]
    r = summarize(rows)
    assert r.income_total == 5000.0
    assert r.expense_total == 1000.0
    assert r.net_profit == 4000.0


def test_spend_grouped_and_sorted_desc():
    rows = [
        {"amount": 100, "kind": "expense", "category": "Fuel"},
        {"amount": 900, "kind": "expense", "category": "Rent"},
        {"amount": 50, "kind": "expense", "category": "Fuel"},
    ]
    r = summarize(rows)
    # Rent (900) should come before Fuel (150)
    assert [c.category for c in r.spend_by_category] == ["Rent", "Fuel"]
    fuel = next(c for c in r.spend_by_category if c.category == "Fuel")
    assert fuel.total == 150.0 and fuel.count == 2


def test_uncategorized_counted_not_totalled():
    rows = [
        {"amount": 300, "kind": None, "category": None},
        {"amount": 700, "kind": "expense", "category": "Stock / Inventory"},
    ]
    r = summarize(rows)
    assert r.uncategorized_count == 1
    assert r.expense_total == 700.0
    assert r.transaction_count == 2


def test_empty_is_all_zero():
    r = summarize([])
    assert r.income_total == 0.0 and r.expense_total == 0.0 and r.net_profit == 0.0
    assert r.spend_by_category == [] and r.transaction_count == 0


def test_period_passed_through():
    r = summarize([], period_start="2026-02-01", period_end="2026-02-28")
    assert r.period_start == "2026-02-01" and r.period_end == "2026-02-28"
