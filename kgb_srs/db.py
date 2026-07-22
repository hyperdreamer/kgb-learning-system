"""Database initialization and operations.

Re-exports from schema.py for backward compatibility.
"""

import logging


from .schema import (
    init_db,
    find_databases,
    DB_SUFFIX,
    ensure_unfamiliar_items_table,
    migrate_unfamiliar_items_meaning,
    ensure_sentence_schema,
    insert_sentence_card,
    get_sentence_card,
    update_sentence_card,
    find_duplicate_sentence_card,
    validate_db_name,
    safe_db_filename,
    resolve_db_path,
)


logger = logging.getLogger(__name__)


def rollback_after_failure(conn, operation: str) -> None:
    """Attempt a rollback without masking the operation failure already handled."""
    try:
        conn.rollback()
    except Exception:
        logger.warning(
            "SQLite rollback failed after %s; preserving the original failure.",
            operation,
            exc_info=True,
        )


__all__ = [
    "init_db",
    "find_databases",
    "DB_SUFFIX",
    "ensure_unfamiliar_items_table",
    "migrate_unfamiliar_items_meaning",
    "ensure_sentence_schema",
    "insert_sentence_card",
    "get_sentence_card",
    "update_sentence_card",
    "find_duplicate_sentence_card",
    "validate_db_name",
    "safe_db_filename",
    "resolve_db_path",
    "rollback_after_failure",
]
