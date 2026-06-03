"""Database initialization and operations."""

import os
import sqlite3

from .config import DIR_DB


# --- Database File Extension ---
DB_SUFFIX = "_barsky.db"


def init_db(db_path):
    """Initialize or open a database at the given path."""
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute(
        """CREATE TABLE IF NOT EXISTS cards
           (id INTEGER PRIMARY KEY, front TEXT, back TEXT,
            box INTEGER, next_review DATE)"""
    )
    c.execute(
        """CREATE TABLE IF NOT EXISTS settings
           (key TEXT PRIMARY KEY, value TEXT)"""
    )
    c.execute(
        "INSERT OR IGNORE INTO settings (key, value) VALUES ('random_review', '1')"
    )
    conn.commit()
    return conn


def find_databases():
    """Recursively find all _barsky.db files under DIR_DB.

    Returns list of (display_name, full_path) sorted by display name.
    """
    results = []
    if not os.path.isdir(DIR_DB):
        return results
    for root, dirs, files in os.walk(DIR_DB):
        for f in files:
            if f.endswith(DB_SUFFIX):
                full_path = os.path.join(root, f)
                db_name = f[: -len(DB_SUFFIX)]
                rel_dir = os.path.relpath(root, DIR_DB)
                if rel_dir == ".":
                    display = db_name
                else:
                    display = os.path.join(rel_dir, db_name)
                results.append((display, full_path))
    results.sort(key=lambda x: x[0].lower())
    return results
