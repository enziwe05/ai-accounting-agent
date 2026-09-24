"""
Load the starter categories and rules into the database.

Always loads the generic base set (data/default_categories.json +
data/default_rules.json). If SEED_PROFILE is set (e.g. SEED_PROFILE=construction),
it ALSO loads that industry pack (data/construction_categories.json +
data/construction_rules.json) on top — so a construction client gets Motor
Vehicle Repairs, Materials & Supplies, etc. as well as the generic categories.

Safe to run repeatedly — it skips anything that already exists, so it never
creates duplicates.

Usage:
    python seed_db.py                       # base set only
    SEED_PROFILE=construction python seed_db.py   # base + construction pack
"""

import json
import os
from pathlib import Path

from db import get_connection

DATA_DIR = Path(__file__).parent / "data"

# An optional industry pack layered on top of the base set. Set per company in
# its env file, e.g. SEED_PROFILE=construction. Blank means base set only.
SEED_PROFILE = os.getenv("SEED_PROFILE", "").strip()


def seed_categories(cur, filename: str) -> dict:
    """Insert categories from one file, return the full {name: id} map."""
    path = DATA_DIR / filename
    if path.exists():
        categories = json.loads(path.read_text("utf-8"))
        for cat in categories:
            # INSERT IGNORE skips rows whose unique name already exists.
            cur.execute(
                "INSERT IGNORE INTO categories (name, kind) VALUES (%s, %s)",
                (cat["name"], cat["kind"]),
            )
    else:
        print(f"  (no {filename} — skipping)")
    cur.execute("SELECT id, name FROM categories")
    return {row["name"]: row["id"] for row in cur.fetchall()}


def seed_rules(cur, filename: str, category_ids: dict) -> int:
    """Insert rules from one file that don't already exist. Returns how many were added."""
    path = DATA_DIR / filename
    if not path.exists():
        print(f"  (no {filename} — skipping)")
        return 0
    rules = json.loads(path.read_text("utf-8"))
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
    # Base set first, then the optional industry pack on top.
    files = [("default_categories.json", "default_rules.json")]
    if SEED_PROFILE:
        files.append((f"{SEED_PROFILE}_categories.json", f"{SEED_PROFILE}_rules.json"))
        print(f"Seeding base set + '{SEED_PROFILE}' pack.")

    conn = get_connection()
    try:
        added = 0
        category_ids = {}
        with conn.cursor() as cur:
            for cat_file, rule_file in files:
                category_ids = seed_categories(cur, cat_file)
                added += seed_rules(cur, rule_file, category_ids)
        print(f"Categories in database: {len(category_ids)}")
        print(f"New rules added this run: {added}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
