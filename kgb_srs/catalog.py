"""Database type catalog — metadata, inference, and menu hierarchy.

Defines the category/subtype hierarchy:
  Language-based → Sentence-based
  Language-based → Word/Phrase-based
  Knowledge-based → (generic, no subtype label)

Provides functions to infer, read, write, and organize database metadata.
"""

import os
from enum import Enum
from typing import Optional


# ---------------------------------------------------------------------------
# Canonical directories
# ---------------------------------------------------------------------------

# These are relative to DIR_DB.
DB_DIR_LANGUAGE_SENTENCE = os.path.join("Language-based", "Sentence-based")
DB_DIR_LANGUAGE_WORD_PHRASE = os.path.join("Language-based", "Word-Phrase-based")
DB_DIR_KNOWLEDGE = os.path.join("Knowledge-based")

# Legacy directory name used for detection.
_LEGACY_LANGUAGES = "Languages"


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class DatabaseCategory(Enum):
    LANGUAGE_BASED = "language_based"
    KNOWLEDGE_BASED = "knowledge_based"


class DatabaseType(Enum):
    """Canonical database_type values stored in the settings table."""

    LANGUAGE_SENTENCE = "language_sentence"
    LANGUAGE_WORD_PHRASE = "language_word_phrase"
    KNOWLEDGE = "knowledge"

    @property
    def category(self) -> DatabaseCategory:
        _map = {
            DatabaseType.LANGUAGE_SENTENCE: DatabaseCategory.LANGUAGE_BASED,
            DatabaseType.LANGUAGE_WORD_PHRASE: DatabaseCategory.LANGUAGE_BASED,
            DatabaseType.KNOWLEDGE: DatabaseCategory.KNOWLEDGE_BASED,
        }
        return _map[self]

    @property
    def display(self) -> str:
        _map = {
            DatabaseType.LANGUAGE_SENTENCE: "Sentence-based",
            DatabaseType.LANGUAGE_WORD_PHRASE: "Word/Phrase-based",
            DatabaseType.KNOWLEDGE: "Knowledge-based",
        }
        return _map[self]

    @property
    def category_display(self) -> str:
        _map = {
            DatabaseCategory.LANGUAGE_BASED: "Language-based",
            DatabaseCategory.KNOWLEDGE_BASED: "Knowledge-based",
        }
        return _map[self.category]


# ---------------------------------------------------------------------------
# Menu display path helper
# ---------------------------------------------------------------------------


def display_path_for(
    db_path: str,
    db_type: DatabaseType,
    base_dir: str | None = None,
) -> str:
    """Build the hierarchical display path for a database given its type.

    The display path is used in the menu tree.

    For Language-based types, output is:
        Language-based/<subtype>/<relative-path-within-canonical-dir>

    For Knowledge-based, output is:
        Knowledge-based/<relative-path-within-canonical-dir>
    (no duplicate subtype label — Knowledge-based is both category and
    its own leaf, with the legacy hierarchy underneath.)

    Examples:
        Language-based/Sentence-based/French
        Language-based/Sentence-based/FR/A1
        Language-based/Word-Phrase-based/Languages/English
        Knowledge-based/Math/Topology
    """
    canonicals = {
        DatabaseType.LANGUAGE_SENTENCE: DB_DIR_LANGUAGE_SENTENCE,
        DatabaseType.LANGUAGE_WORD_PHRASE: DB_DIR_LANGUAGE_WORD_PHRASE,
        DatabaseType.KNOWLEDGE: DB_DIR_KNOWLEDGE,
    }
    canonical = canonicals[db_type]

    from .config import DIR_DB, get_database_root

    if base_dir is None:
        base_dir = get_database_root()
    if not base_dir:
        base_dir = DIR_DB

    norm_path = os.path.realpath(db_path)
    canonical_abs = os.path.realpath(os.path.join(base_dir, canonical))
    category_disp = db_type.category_display

    try:
        contained = os.path.commonpath([norm_path, canonical_abs]) == canonical_abs
    except ValueError:
        contained = False

    if contained:
        relative = os.path.relpath(norm_path, canonical_abs)
    else:
        # Also support relative canonical paths in helper callers/tests.
        normalized = os.path.normpath(db_path)
        marker = os.path.normpath(canonical)
        if normalized == marker or normalized.startswith(marker + os.sep):
            relative = os.path.relpath(normalized, marker)
        else:
            parts = normalized.replace("\\", "/").split("/")
            if not os.path.isabs(normalized) and "db" in parts:
                relative = "/".join(parts[parts.index("db") + 1 :])
            else:
                relative = os.path.basename(normalized)
    rel_parts = relative.replace("\\", "/").split("/")

    # Strip the _barsky.db suffix from the last component
    if rel_parts:
        rel_parts[-1] = rel_parts[-1].removesuffix("_barsky.db")
    else:
        # DB is directly in the canonical dir
        leaf = os.path.basename(db_path).removesuffix("_barsky.db")
        rel_parts = [leaf]

    # For Language-based: prepend category + subtype
    # For Knowledge-based: prepend only category (no subtype duplication)
    if db_type.category == DatabaseCategory.LANGUAGE_BASED:
        components = [category_disp, db_type.display] + rel_parts
    else:
        components = [category_disp] + rel_parts

    return "/".join(components)


# ---------------------------------------------------------------------------
# Metadata inference from path
# ---------------------------------------------------------------------------


def infer_database_type(db_path: str) -> DatabaseType:
    """Infer the database_type from the file path.

    Uses path *component* comparison (not substring matching) to avoid
    false positives (e.g. a file named 'Language-based_french_barsky.db'
    in a different directory should not trigger Language-based detection).

    Rules (in priority order):
    1. If path components contain 'Language-based'/'Sentence-based'
       → LANGUAGE_SENTENCE
    2. If path components contain 'Language-based'/'Word-Phrase-based'
       → LANGUAGE_WORD_PHRASE
    3. If path components contain 'Knowledge-based'
       → KNOWLEDGE
    4. Legacy: path components contain 'Languages' → LANGUAGE_WORD_PHRASE
    5. Legacy: path components contain 'Math' → KNOWLEDGE
    6. Default → KNOWLEDGE
    """
    norm = os.path.normpath(db_path).replace("\\", "/")
    parts = norm.split("/")

    # Check canonical paths by component
    sent_parts = DB_DIR_LANGUAGE_SENTENCE.replace("\\", "/").split("/")
    wp_parts = DB_DIR_LANGUAGE_WORD_PHRASE.replace("\\", "/").split("/")
    know_parts = DB_DIR_KNOWLEDGE.replace("\\", "/").split("/")

    # Check if path contains the canonical component sequence
    def _has_subsequence(haystack, needle):
        for i in range(len(haystack) - len(needle) + 1):
            if haystack[i : i + len(needle)] == needle:
                return True
        return False

    if _has_subsequence(parts, sent_parts):
        return DatabaseType.LANGUAGE_SENTENCE
    if _has_subsequence(parts, wp_parts):
        return DatabaseType.LANGUAGE_WORD_PHRASE
    if _has_subsequence(parts, know_parts):
        return DatabaseType.KNOWLEDGE

    # Legacy detection: check for exact component match
    if _LEGACY_LANGUAGES in parts:
        return DatabaseType.LANGUAGE_WORD_PHRASE
    if "Math" in parts:
        return DatabaseType.KNOWLEDGE

    return DatabaseType.KNOWLEDGE


# ---------------------------------------------------------------------------
# Settings table read / write
# ---------------------------------------------------------------------------

_SETTINGS_KEY = "database_type"


def read_database_type(conn) -> Optional[DatabaseType]:
    """Read the database_type from the settings table.

    Returns None if the key is missing or the value is unrecognised.
    """
    cur = conn.cursor()
    cur.execute("SELECT value FROM settings WHERE key = ?", (_SETTINGS_KEY,))
    row = cur.fetchone()
    if row is None:
        return None
    try:
        return DatabaseType(row[0])
    except ValueError:
        return None


def write_database_type(conn, db_type: DatabaseType) -> None:
    """Write (or overwrite) the database_type in the settings table."""
    conn.execute(
        "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
        (_SETTINGS_KEY, db_type.value),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Catalog tree for menu building
# ---------------------------------------------------------------------------


def build_catalog_tree(
    entries: list[tuple[str, str, DatabaseType]],
) -> dict:
    """Build a hierarchical tree from (display_path, db_path, db_type) entries.

    The display_path uses '/' as separator
    (e.g. 'Language-based/Sentence-based/French').

    Returns a nested dict:
        {category: {subtype_or_leaf: ...}}
    Leaf nodes are stored as (db_path, db_type) tuples.

    Categories are ordered: Language-based before Knowledge-based.
    """
    # The category/type skeleton is part of the UI, not inferred from which
    # databases happen to exist today.  Keep empty categories visible.
    tree: dict = {
        "Language-based": {
            "Sentence-based": {},
            "Word/Phrase-based": {},
        },
        "Knowledge-based": {},
    }

    for display, db_path, db_type in entries:
        parts = [p for p in display.replace("\\", "/").split("/") if p]
        leaf = os.path.basename(db_path).removesuffix("_barsky.db")

        if db_type == DatabaseType.LANGUAGE_WORD_PHRASE:
            # Word/Phrase databases are deliberately flat.  In particular,
            # do not reproduce legacy directory components such as Languages.
            tree["Language-based"]["Word/Phrase-based"][leaf] = (db_path, db_type)
            continue

        if db_type == DatabaseType.LANGUAGE_SENTENCE:
            node = tree["Language-based"]["Sentence-based"]
            try:
                subtype_index = parts.index("Sentence-based")
                relative_parts = parts[subtype_index + 1 :]
            except ValueError:
                relative_parts = [leaf]
        else:
            node = tree["Knowledge-based"]
            relative_parts = parts[1:] if parts[:1] == ["Knowledge-based"] else [leaf]

        if not relative_parts:
            relative_parts = [leaf]
        for part in relative_parts[:-1]:
            node = node.setdefault(part, {})
        node[relative_parts[-1]] = (db_path, db_type)

    return tree
