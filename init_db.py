"""
Create the database and all seven tables.

Reads schema.sql and runs it against MAMP's MySQL. Safe to run repeatedly —
the schema uses IF NOT EXISTS, so re-running never destroys existing data.

Usage:
    python init_db.py
"""

import os
import re
from pathlib import Path

from db import get_connection

SCHEMA_FILE = Path(__file__).parent / "schema.sql"

# Which database to build. Each company points DB_NAME at its own database on the
# shared MySQL server, so their books stay completely separate.
DB_NAME = os.getenv("DB_NAME", "bookkeeper")

# Guard the name: it goes into SQL as an identifier, so only allow safe characters.
if not re.fullmatch(r"[A-Za-z0-9_]+", DB_NAME):
    raise SystemExit(f"Invalid DB_NAME {DB_NAME!r}: use only letters, numbers, underscores.")


def split_statements(sql: str) -> list[str]:
    """Split a .sql file into individual statements on semicolons.

    We first strip "--" line comments, because a comment might itself contain a
    semicolon (e.g. "local path now; R2/B2 key later") which would otherwise
    break a naive split. Comments are only removed in memory for execution —
    schema.sql keeps them for humans to read.
    """
    cleaned_lines = []
    for line in sql.splitlines():
        idx = line.find("--")
        if idx != -1:
            line = line[:idx]
        cleaned_lines.append(line)
    cleaned = "\n".join(cleaned_lines)
    return [s.strip() for s in cleaned.split(";") if s.strip()]


def _column_exists(cur, table: str, column: str) -> bool:
    cur.execute(
        """SELECT COUNT(*) AS n FROM information_schema.columns
           WHERE table_schema = %s AND table_name = %s
             AND column_name = %s""",
        (DB_NAME, table, column),
    )
    return cur.fetchone()["n"] > 0


def migrate(conn) -> None:
    """Bring an EXISTING database up to date.

    CREATE TABLE IF NOT EXISTS never alters a table that already exists, so new
    columns added to schema.sql after the first run must be applied here. Each
    step checks first, so this is safe to run repeatedly.
    """
    with conn.cursor() as cur:
        # message_log.wa_msg_id — lets a quoted/replied-to message be looked up.
        if not _column_exists(cur, "message_log", "wa_msg_id"):
            cur.execute("ALTER TABLE message_log "
                        "ADD COLUMN wa_msg_id VARCHAR(64) NULL, "
                        "ADD KEY idx_ml_msgid (wa_msg_id)")
            print("  migrated: added message_log.wa_msg_id")


def main() -> None:
    sql = SCHEMA_FILE.read_text(encoding="utf-8")
    statements = split_statements(sql)

    # Connect WITHOUT selecting a database (it may not exist yet), create the
    # company's database from DB_NAME, then select it and build the tables.
    conn = get_connection(include_database=False)
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"CREATE DATABASE IF NOT EXISTS `{DB_NAME}` "
                f"CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
            )
            cur.execute(f"USE `{DB_NAME}`")
            for stmt in statements:
                cur.execute(stmt)

        # Apply any migrations to already-existing tables.
        migrate(conn)

        # Confirm what we built.
        with conn.cursor() as cur:
            cur.execute(f"SHOW TABLES IN `{DB_NAME}`")
            tables = [list(row.values())[0] for row in cur.fetchall()]

        print(f"Database '{DB_NAME}' is ready. Tables:")
        for name in tables:
            print(f"  - {name}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
