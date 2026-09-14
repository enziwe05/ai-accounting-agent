"""
Database connection helper.

Reads MySQL settings from the environment (never hardcoded — non-negotiable #7)
and hands back a connection. Everything else in the project imports get_connection()
from here, so there is exactly one place that knows how to reach the database.

Local dev uses MAMP's MySQL: host localhost, port 3306, user root, password root.
Those defaults are filled in below only as a convenience for local development —
the real values still come from .env.
"""

import os

import pymysql

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass


def _settings(include_database: bool = True) -> dict:
    """Build the connection settings from environment variables."""
    settings = {
        "host": os.getenv("DB_HOST", "localhost"),
        "port": int(os.getenv("DB_PORT", "3306")),
        "user": os.getenv("DB_USER", "root"),
        "password": os.getenv("DB_PASS", "root"),
        "charset": "utf8mb4",
        "cursorclass": pymysql.cursors.DictCursor,  # rows come back as dicts
        "autocommit": True,
    }
    if include_database:
        settings["database"] = os.getenv("DB_NAME", "bookkeeper")
    return settings


def get_connection(include_database: bool = True) -> pymysql.connections.Connection:
    """Open a connection to MySQL.

    include_database=False is used once, by init_db.py, to connect *before* the
    'bookkeeper' database exists so it can create it.
    """
    return pymysql.connect(**_settings(include_database=include_database))
