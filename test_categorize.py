"""
Unit tests for the rules engine (match_rule).

The matcher is a pure function, so these tests need no database and no network.
Run them with:  python -m pytest test_categorize.py -v
"""

from categorize import Rule, match_rule


def rule(pattern, category_name, category_id=1, match_field="vendor",
         match_type="contains", priority=100, rule_id=1):
    return Rule(
        id=rule_id,
        match_field=match_field,
        match_type=match_type,
        pattern=pattern,
        category_id=category_id,
        category_name=category_name,
        priority=priority,
    )


def test_contains_match():
    rules = [rule("Engen", "Fuel")]
    m = match_rule("Engen Matsapha", None, rules)
    assert m is not None and m.category_name == "Fuel"


def test_case_insensitive():
    rules = [rule("engen", "Fuel")]
    assert match_rule("ENGEN MATSAPHA", None, rules) is not None


def test_no_match_returns_none():
    rules = [rule("Engen", "Fuel")]
    assert match_rule("Some Random Shop", None, rules) is None


def test_priority_lower_wins():
    # Both patterns match; the lower-priority-number rule must win.
    rules = [
        rule("Shop", "General", priority=100, rule_id=1),
        rule("Mega Shop", "Groceries", priority=10, rule_id=2),
    ]
    m = match_rule("Mega Shop", None, rules)
    assert m.category_name == "Groceries"


def test_equals_requires_exact():
    rules = [rule("KFC", "Meals", match_type="equals")]
    assert match_rule("KFC", None, rules) is not None
    assert match_rule("KFC Manzini", None, rules) is None


def test_regex_match():
    rules = [rule(r"inv#\d+", "Rent", match_field="description", match_type="regex")]
    assert match_rule(None, "Rent Invoice INV#8786", rules) is not None
    assert match_rule(None, "Rent Invoice", rules) is None


def test_field_selection():
    # A description rule must not match against the vendor field.
    rules = [rule("Levies", "Rent", match_field="description")]
    assert match_rule("Levies", None, rules) is None            # vendor, no desc
    assert match_rule(None, "Levies for Sep", rules) is not None  # description


def test_empty_text_is_skipped():
    rules = [rule("Engen", "Fuel")]
    assert match_rule("", "", rules) is None
    assert match_rule(None, None, rules) is None


def test_broken_regex_does_not_crash():
    rules = [rule("(unclosed", "Oops", match_type="regex")]
    # Should simply not match rather than raising.
    assert match_rule("anything", None, rules) is None
