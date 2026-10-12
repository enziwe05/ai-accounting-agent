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


def seed_categories(cur, filename: str, company_id: int) -> dict:
    """Insert categories from one file for one company, return that company's
    full {name: id} map."""
    path = DATA_DIR / filename
    if path.exists():
        categories = json.loads(path.read_text("utf-8"))
        for cat in categories:
            # INSERT IGNORE skips rows whose (company_id, name) already exists.
            cur.execute(
                "INSERT IGNORE INTO categories (company_id, name, kind) VALUES (%s, %s, %s)",
                (company_id, cat["name"], cat["kind"]),
            )
    else:
        print(f"  (no {filename} — skipping)")
    cur.execute("SELECT id, name FROM categories WHERE company_id = %s", (company_id,))
    return {row["name"]: row["id"] for row in cur.fetchall()}


def seed_rules(cur, filename: str, category_ids: dict, company_id: int) -> int:
    """Insert one company's rules from a file that don't already exist. Returns
    how many were added."""
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

        # Only insert if an identical rule isn't already there for this company.
        cur.execute(
            """SELECT id FROM category_rules
               WHERE company_id=%s AND match_field=%s AND match_type=%s
                 AND pattern=%s AND category_id=%s""",
            (company_id, rule["match_field"], rule["match_type"], rule["pattern"], category_id),
        )
        if cur.fetchone():
            continue

        cur.execute(
            """INSERT INTO category_rules
                 (company_id, match_field, match_type, pattern, category_id, priority, notes)
               VALUES (%s, %s, %s, %s, %s, %s, %s)""",
            (
                company_id,
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


def seed_company(company_id: int, profile: str = "") -> int:
    """Seed one company's starter categories + rules (base set, plus an optional
    industry pack). Idempotent. Called at startup for M26 (company 1) and by the
    provisioning API for every new company so it starts with a working rules
    engine. Returns the number of rules added."""
    files = [("default_categories.json", "default_rules.json")]
    if profile:
        files.append((f"{profile}_categories.json", f"{profile}_rules.json"))

    conn = get_connection()
    try:
        added = 0
        category_ids: dict = {}
        with conn.cursor() as cur:
            for cat_file, rule_file in files:
                category_ids = seed_categories(cur, cat_file, company_id)
                added += seed_rules(cur, rule_file, category_ids, company_id)
        return added
    finally:
        conn.close()


def main() -> None:
    # Startup seeding is for M26 (company 1). New companies are seeded by the
    # provisioning API via seed_company(). SEED_PROFILE still layers an industry
    # pack on top for the base company if set.
    if SEED_PROFILE:
        print(f"Seeding company 1 base set + '{SEED_PROFILE}' pack.")
    added = seed_company(1, SEED_PROFILE)
    print(f"New rules added this run (company 1): {added}")


if __name__ == "__main__":
    main()
