"""Database initialization and operations.

Re-exports from schema.py for backward compatibility.
"""

from .schema import (
    init_db,
    find_databases,
    DB_SUFFIX,
    ensure_unfamiliar_items_table,
    migrate_unfamiliar_items_meaning,
    insert_sentence_card,
    get_sentence_card,
    update_sentence_card,
    find_duplicate_sentence_card,
    validate_db_name,
    safe_db_filename,
    resolve_db_path,
)

__all__ = [
    "init_db",
    "find_databases",
    "DB_SUFFIX",
    "ensure_unfamiliar_items_table",
    "migrate_unfamiliar_items_meaning",
    "insert_sentence_card",
    "get_sentence_card",
    "update_sentence_card",
    "find_duplicate_sentence_card",
    "validate_db_name",
    "safe_db_filename",
    "resolve_db_path",
]
