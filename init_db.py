"""
Create the database and all seven tables.

Reads schema.sql and runs it against MAMP's MySQL. Safe to run repeatedly —
the schema uses IF NOT EXISTS, so re-running never destroys existing data.

Usage:
    python init_db.py
"""

from pathlib import Path

from db import get_connection

SCHEMA_FILE = Path(__file__).parent / "schema.sql"


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


def main() -> None:
    sql = SCHEMA_FILE.read_text(encoding="utf-8")
    statements = split_statements(sql)

    # Connect WITHOUT selecting a database (it may not exist yet). The schema's
    # own "CREATE DATABASE ... ; USE bookkeeper;" lines then take over.
    conn = get_connection(include_database=False)
    try:
        with conn.cursor() as cur:
            for stmt in statements:
                cur.execute(stmt)

        # Confirm what we built.
        with conn.cursor() as cur:
            cur.execute("SHOW TABLES IN bookkeeper;")
            tables = [list(row.values())[0] for row in cur.fetchall()]

        print("Database 'bookkeeper' is ready. Tables:")
        for name in tables:
            print(f"  - {name}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
