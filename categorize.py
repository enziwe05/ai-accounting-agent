"""
Phase 3 — Categorization as code.

The rules engine decides categories in plain Python (non-negotiable #1). Only
when NO rule matches do we ask the model for a *suggestion*, and that suggestion
goes to the review_queue for a human — it is never applied automatically
(non-negotiables #1 and #2).

The matcher (`match_rule`) is a pure function: rules + text in, matched rule out.
No database, no network — which is exactly why it's easy to unit-test
(see test_categorize.py).
"""

import re
from dataclasses import dataclass

from pydantic import BaseModel, Field

from db import get_connection
from llm import MODEL, client


# --- The rules engine (pure, testable) --------------------------------------

@dataclass
class Rule:
    id: int
    match_field: str   # 'vendor' | 'description'
    match_type: str    # 'contains' | 'equals' | 'regex'
    pattern: str
    category_id: int
    category_name: str
    priority: int


def match_rule(vendor: str | None, description: str | None, rules: list[Rule]) -> Rule | None:
    """Return the first rule that matches, by ascending priority (lower wins).

    Matching is case-insensitive. This is the whole rules engine — deliberately
    simple and deterministic.
    """
    fields = {"vendor": vendor or "", "description": description or ""}

    for rule in sorted(rules, key=lambda r: r.priority):
        text = fields.get(rule.match_field, "")
        if not text:
            continue

        text_l = text.lower()
        pattern_l = rule.pattern.lower()

        if rule.match_type == "contains" and pattern_l in text_l:
            return rule
        if rule.match_type == "equals" and text_l.strip() == pattern_l.strip():
            return rule
        if rule.match_type == "regex":
            try:
                if re.search(rule.pattern, text, re.IGNORECASE):
                    return rule
            except re.error:
                continue  # a broken regex rule simply doesn't match

    return None


# --- The LLM fallback (suggestion only, never applied) ----------------------

class CategorySuggestion(BaseModel):
    category: str = Field(
        description="The best-fitting category NAME from the provided list, "
        "or 'Unknown' if none fit."
    )
    confidence: str = Field(description="One of: high, medium, low.")
    reason: str = Field(description="One short sentence explaining the choice.")


def suggest_category(vendor: str | None, description: str | None,
                     category_names: list[str]) -> CategorySuggestion:
    """Ask the model for a category suggestion — structured output, validated (#5)."""
    names = ", ".join(category_names)
    response = client.messages.parse(
        model=MODEL,
        max_tokens=500,
        system=(
            "You help categorize a South African small-business transaction for "
            "bookkeeping. Choose the single best-fitting category from the list you "
            "are given. If none fit well, answer 'Unknown'. Do not invent categories."
        ),
        messages=[
            {
                "role": "user",
                "content": f"Vendor: {vendor or '(unknown)'}\n"
                f"Description: {description or '(none)'}\n\n"
                f"Available categories: {names}\n\n"
                f"Which category fits best?",
            }
        ],
        output_format=CategorySuggestion,
    )
    return response.parsed_output


# --- Database glue -----------------------------------------------------------

def load_rules(cur) -> list[Rule]:
    cur.execute(
        """SELECT r.id, r.match_field, r.match_type, r.pattern,
                  r.category_id, c.name AS category_name, r.priority
           FROM category_rules r
           JOIN categories c ON c.id = r.category_id
           WHERE r.active = 1"""
    )
    return [Rule(**row) for row in cur.fetchall()]


def load_category_map(cur) -> dict:
    cur.execute("SELECT id, name FROM categories")
    return {row["name"]: row["id"] for row in cur.fetchall()}


def categorize_transaction(cur, txn_id: int) -> dict:
    """Categorize one transaction. Returns a small dict describing what happened."""
    cur.execute(
        """SELECT t.id, t.description, d.vendor
           FROM transactions t
           JOIN documents d ON d.id = t.document_id
           WHERE t.id = %s""",
        (txn_id,),
    )
    txn = cur.fetchone()
    if txn is None:
        raise ValueError(f"No transaction with id {txn_id}")

    rules = load_rules(cur)
    matched = match_rule(txn["vendor"], txn["description"], rules)

    if matched is not None:
        # A rule decided it — apply directly, mark sorted.
        cur.execute(
            "UPDATE transactions SET category_id=%s, status='sorted' WHERE id=%s",
            (matched.category_id, txn_id),
        )
        return {
            "outcome": "sorted",
            "category": matched.category_name,
            "by": f"rule (pattern '{matched.pattern}')",
        }

    # No rule matched → ask the model, but ONLY as a review-queue suggestion.
    category_map = load_category_map(cur)
    suggestion = suggest_category(txn["vendor"], txn["description"], list(category_map))
    suggested_id = category_map.get(suggestion.category)  # None if 'Unknown'

    cur.execute(
        """INSERT INTO review_queue
             (transaction_id, reason, suggested_category_id, suggestion_note)
           VALUES (%s, 'llm_suggested_category', %s, %s)""",
        (
            txn_id,
            suggested_id,
            f"Suggested '{suggestion.category}' ({suggestion.confidence}): {suggestion.reason}",
        ),
    )
    cur.execute("UPDATE transactions SET status='needs_review' WHERE id=%s", (txn_id,))

    return {
        "outcome": "needs_review",
        "suggested_category": suggestion.category,
        "confidence": suggestion.confidence,
        "reason": suggestion.reason,
        "by": "LLM suggestion (queued for human approval — not applied)",
    }


def categorize_pending() -> None:
    """CLI: categorize every transaction currently marked needs_review."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM transactions WHERE status='needs_review'")
            ids = [row["id"] for row in cur.fetchall()]

        if not ids:
            print("No transactions are waiting to be categorized.")
            return

        for txn_id in ids:
            with conn.cursor() as cur:
                result = categorize_transaction(cur, txn_id)
            print(f"Transaction #{txn_id}: {result}")
    finally:
        conn.close()


if __name__ == "__main__":
    categorize_pending()
