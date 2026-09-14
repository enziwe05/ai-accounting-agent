"""
Load the starter categories and rules into the database.

Reads data/default_categories.json and data/default_rules.json and inserts them.
Safe to run repeatedly — it skips anything that already exists, so it never
creates duplicates.

Usage:
    python seed_db.py
"""

import json
from pathlib import Path

from db import get_connection

DATA_DIR = Path(__file__).parent / "data"


def seed_categories(cur) -> dict:
    """Insert categories, return a {name: id} map for the rule step."""
    categories = json.loads((DATA_DIR / "default_categories.json").read_text("utf-8"))
    for cat in categories:
        # INSERT IGNORE skips rows whose unique name already exists.
        cur.execute(
            "INSERT IGNORE INTO categories (name, kind) VALUES (%s, %s)",
            (cat["name"], cat["kind"]),
        )
    cur.execute("SELECT id, name FROM categories")
    return {row["name"]: row["id"] for row in cur.fetchall()}


def seed_rules(cur, category_ids: dict) -> int:
    """Insert rules that don't already exist. Returns how many were added."""
    rules = json.loads((DATA_DIR / "default_rules.json").read_text("utf-8"))
    added = 0
    for rule in rules:
        if not isinstance(rule, dict):
            continue  # skip the human-readable NOTE strings at the top of the file
        category_id = category_ids.get(rule["category"])
        if category_id is None:
            print(f"  ! skipping rule for unknown category '{rule['category']}'")
            continue

        # Only insert if an identical rule isn't already there (idempotent).
        cur.execute(
            """SELECT id FROM category_rules
               WHERE match_field=%s AND match_type=%s AND pattern=%s AND category_id=%s""",
            (rule["match_field"], rule["match_type"], rule["pattern"], category_id),
        )
        if cur.fetchone():
            continue

        cur.execute(
            """INSERT INTO category_rules
                 (match_field, match_type, pattern, category_id, priority, notes)
               VALUES (%s, %s, %s, %s, %s, %s)""",
            (
                rule["match_field"],
                rule["match_type"],
                rule["pattern"],
                category_id,
                rule.get("priority", 100),
                rule.get("notes"),
            ),
        )
        added += 1
    return added


def main() -> None:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            category_ids = seed_categories(cur)
            added = seed_rules(cur, category_ids)
        print(f"Categories in database: {len(category_ids)}")
        print(f"New rules added this run: {added}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
