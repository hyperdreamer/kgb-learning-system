"""Database schema initialization, migration, and CRUD helpers.

Key schema:
  cards(id INTEGER PK, front TEXT, back TEXT, box INTEGER, next_review DATE)
  settings(key TEXT PK, value TEXT)
  unfamiliar_items(id INTEGER PK, card_id INTEGER FK→cards ON DELETE CASCADE,
                   expression TEXT, meaning TEXT NOT NULL DEFAULT '',
                   sense_id INTEGER NULL FK→expression_senses,
                   UNIQUE(card_id, expression))
  expression_senses(id INTEGER PK, expression, meaning,
                    expression_norm, meaning_norm,
                    UNIQUE(expression_norm, meaning_norm))
"""

import os
import re
import sqlite3
import datetime

from .validation import normalize_sentence

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DB_SUFFIX = "_barsky.db"

# Characters forbidden in database names (path component safety).
_FORBIDDEN_DB_NAME_RE = re.compile(r"[\x00-\x1f/\\\\]|\.\.")


# ---------------------------------------------------------------------------
# Database name validation
# ---------------------------------------------------------------------------

def validate_db_name(name: str) -> bool:
    """Validate a database display name for use as a path component.

    Rejects:
      - Empty or whitespace-only names
      - Names containing '/', '\\', '..', NUL, or control characters
      - Names that could escape the canonical directory (absolute paths)

    Returns True if the name is safe to use as a filename component.
    """
    if not name or not name.strip():
        return False
    # Reject NUL bytes and control characters (including NUL)
    if "\x00" in name or any(ord(c) < 0x20 for c in name):
        return False
    # Reject path separators and traversal sequences
    if _FORBIDDEN_DB_NAME_RE.search(name):
        return False
    # Reject absolute-looking paths
    if os.path.isabs(name):
        return False
    # Reject names that are exactly '.' or start with '/'
    if name.strip() == ".":
        return False
    return True


def safe_db_filename(name: str) -> str:
    """Normalize a display name to a safe filename.

    Returns the safe filename or raises ValueError if the name is invalid.
    """
    if not validate_db_name(name):
        raise ValueError(
            f"Invalid database name: {name!r}. "
            "Names must not contain path separators, '..', NUL, "
            "or control characters."
        )
    return name.strip()


def resolve_db_path(base_dir: str, subdir: str, name: str) -> str:
    """Resolve a database file path, ensuring it stays within base_dir.

    Uses realpath and commonpath to harden against symlink escapes
    and path traversal.  The resolved path must be a direct child of
    the resolved canonical directory.

    Raises ValueError if the resolved path escapes base_dir.
    """
    safe_name = safe_db_filename(name)
    filename = f"{safe_name}{DB_SUFFIX}"

    # Resolve base_dir to its real path first
    real_base = os.path.realpath(base_dir)

    # Build the canonical directory and the target
    canon = os.path.realpath(os.path.join(real_base, subdir))
    target = os.path.realpath(os.path.join(canon, filename))

    # Ensure target is within real_base using commonpath
    common = os.path.commonpath([real_base, target])
    if common != real_base:
        raise ValueError(
            f"Path traversal detected: {name!r} resolves outside "
            f"base directory."
        )

    # Ensure target is a direct child of the canonical directory
    target_dir = os.path.dirname(target)
    if target_dir != canon:
        raise ValueError(
            f"Path traversal detected: {name!r} resolves outside "
            f"canonical directory."
        )
    return target


# ---------------------------------------------------------------------------
# Database initialization
# ---------------------------------------------------------------------------

def init_db(db_path_or_conn):
    """Initialize or open a database.

    Accepts either a path string (returns a new connection) or an existing
    sqlite3.Connection (returns it unchanged after ensuring schema).
    """
    if isinstance(db_path_or_conn, sqlite3.Connection):
        conn = db_path_or_conn
    else:
        is_new = os.path.isfile(db_path_or_conn) is False
        conn = sqlite3.connect(db_path_or_conn)
        if is_new:
            try:
                os.chmod(db_path_or_conn, 0o600)
            except OSError:
                pass

    conn.execute("PRAGMA foreign_keys = ON")

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


# ---------------------------------------------------------------------------
# Migration: unfamiliar_items
# ---------------------------------------------------------------------------

def ensure_unfamiliar_items_table(conn, *, commit: bool = True):
    """Create the unfamiliar_items table if it doesn't exist.

    Safe to call multiple times; uses IF NOT EXISTS.
    """
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute(
        """CREATE TABLE IF NOT EXISTS unfamiliar_items (
            id INTEGER PRIMARY KEY,
            card_id INTEGER NOT NULL,
            expression TEXT NOT NULL COLLATE NOCASE,
            meaning TEXT NOT NULL DEFAULT '',
            FOREIGN KEY (card_id) REFERENCES cards(id) ON DELETE CASCADE,
            UNIQUE(card_id, expression)
        )"""
    )
    if commit:
        conn.commit()


def migrate_unfamiliar_items_meaning(conn):
    """Add the 'meaning' column to unfamiliar_items if it doesn't exist.

    Safe to call on both legacy DBs (no meaning column) and new DBs
    (already has the column).  Idempotent — no error on repeated calls.
    Preserves all existing data.
    """
    conn.execute("PRAGMA foreign_keys = ON")
    cur = conn.cursor()
    cur.execute("PRAGMA table_info(unfamiliar_items)")
    cols = {row[1] for row in cur.fetchall()}

    if "meaning" not in cols:
        conn.execute(
            "ALTER TABLE unfamiliar_items ADD COLUMN meaning TEXT NOT NULL DEFAULT ''"
        )
        conn.commit()


def migrate_unfamiliar_items_surface_form(conn):
    """Add surface_form column for AI residual / irregular surface spans.

    Stores the exact sentence span accepted for a lemma (e.g. lie → lay)
    so review highlighting can bold it without local inflection rules.
    """
    conn.execute("PRAGMA foreign_keys = ON")
    cur = conn.cursor()
    cur.execute("PRAGMA table_info(unfamiliar_items)")
    cols = {row[1] for row in cur.fetchall()}
    if "surface_form" not in cols:
        conn.execute(
            "ALTER TABLE unfamiliar_items "
            "ADD COLUMN surface_form TEXT NOT NULL DEFAULT ''"
        )
        conn.commit()


def ensure_sentence_schema(conn) -> None:
    """Ensure all sentence-DB tables/columns (items + global senses)."""
    ensure_unfamiliar_items_table(conn)
    migrate_unfamiliar_items_meaning(conn)
    migrate_unfamiliar_items_surface_form(conn)
    from .senses import ensure_expression_senses_table

    ensure_expression_senses_table(conn)


# ---------------------------------------------------------------------------
# Validation helpers for sentence-card invariants
# ---------------------------------------------------------------------------

def _validate_expressions_in_sentence(
    sentence: str,
    items: list,
    verified_surfaces: dict[str, str] | None = None,
):
    """Validate that every expression appears in the sentence.

    Uses the same local inflection-tolerant rules as the dialog. When the
    dialog accepted residual items via a verified surface form (e.g. AI
    residual for irregulars), pass those lemma→surface pairs in
    *verified_surfaces* so insert/update does not re-reject them.

    Raises ValueError with the list of missing expressions.
    """
    from .validation import (
        normalize_sentence,
        surface_form_in_sentence,
        validate_unfamiliar_items,
    )

    if not sentence or not sentence.strip():
        raise ValueError("Sentence must be non-empty.")

    # Extract expression strings (items may be str or (str, meaning) tuples)
    exprs: list[str] = []
    for item in items:
        if isinstance(item, tuple):
            exprs.append(str(item[0]))
        else:
            exprs.append(str(item))

    result = validate_unfamiliar_items(sentence, exprs)
    if result.valid:
        return

    surfaces = verified_surfaces or {}
    still_missing: list[str] = []
    for expr in result.missing:
        surface = surfaces.get(expr) or surfaces.get(normalize_sentence(expr))
        if surface and surface_form_in_sentence(sentence, surface):
            continue
        still_missing.append(expr)

    if still_missing:
        missing_str = ", ".join(still_missing)
        raise ValueError(
            f"Expressions not found in sentence: {missing_str}"
        )


def _require_nonempty_meanings(items: list, operation: str):
    """Validate that no meaning is empty for sentence cards.

    Sentence cards carry contextual meanings as their content;
    empty meanings are not allowed for new/edited cards.

    Migration may preserve old rows with empty meaning; this
    enforcement is only for insert/update operations.
    """
    for item in items:
        if isinstance(item, tuple):
            expr = str(item[0])
            meaning = str(item[1]).strip() if len(item) > 1 else ""
            if not meaning:
                raise ValueError(
                    f"Empty meaning not allowed for sentence card {operation}: "
                    f"expression '{expr}' has no meaning."
                )
        else:
            # Bare string — no meaning provided
            expr = str(item)
            raise ValueError(
                f"Empty meaning not allowed for sentence card {operation}: "
                f"expression '{expr}' has no meaning. "
                "Provide meanings as (expression, meaning) tuples."
            )


# ---------------------------------------------------------------------------
# Duplicate detection
# ---------------------------------------------------------------------------

def find_duplicate_sentence_card(conn, sentence: str, items: list):
    """Find an existing card with the same normalized sentence and
    same normalized set/order of expressions.

    Returns the card_id of the duplicate, or None.

    *items* may be a list of strings or list of (expression, meaning) tuples.
    """
    norm_sentence = normalize_sentence(sentence)
    if not norm_sentence:
        return None

    # Normalize expression list
    if items and isinstance(items[0], tuple):
        norm_items = [normalize_sentence(t[0]) for t in items]
    else:
        norm_items = [normalize_sentence(i) for i in items]
    norm_items = [n for n in norm_items if n]

    if not norm_items:
        return None

    # Find candidate cards with matching sentence (normalized)
    cur = conn.cursor()
    cur.execute("SELECT id, front FROM cards")
    candidates = []
    for row in cur.fetchall():
        if normalize_sentence(row[1]) == norm_sentence:
            candidates.append(row[0])

    if not candidates:
        return None

    # For each candidate, check if expressions match (ordered list)
    for cid in candidates:
        cur.execute(
            "SELECT expression FROM unfamiliar_items WHERE card_id=? ORDER BY id",
            (cid,),
        )
        existing = [normalize_sentence(r[0]) for r in cur.fetchall()]
        existing = [e for e in existing if e]
        if existing == norm_items:
            return cid

    return None


# ---------------------------------------------------------------------------
# Sentence-card CRUD
# ---------------------------------------------------------------------------

def insert_sentence_card(
    conn,
    sentence: str,
    unfamiliar_items: list,
    back: str = "",
    verified_surfaces: dict[str, str] | None = None,
) -> int:
    """Insert a sentence-based card with its unfamiliar items.

    *unfamiliar_items* may be:
      - list of str (meaning defaults to '')
      - list of (expression, meaning) tuples

    *verified_surfaces* is an optional lemma→surface map for residual forms
    already checked against the sentence (e.g. AI membership).

    Items are deduplicated before insertion.  Returns the new card's id.

    Raises ValueError if sentence is empty or no items provided.
    """
    if not sentence or not sentence.strip():
        raise ValueError("Sentence must be non-empty.")
    if not unfamiliar_items:
        raise ValueError("At least one unfamiliar item is required.")

    # Validate that every expression appears in the sentence (local rules,
    # plus any dialog-verified residual surfaces).
    _validate_expressions_in_sentence(
        sentence, unfamiliar_items, verified_surfaces=verified_surfaces
    )

    # Reject empty meanings for sentence cards (newly created)
    _require_nonempty_meanings(unfamiliar_items, "insert")

    ensure_sentence_schema(conn)

    from .senses import create_or_get_sense

    # Normalize items to (expression, meaning, sense_id, surface_form)
    normalized: list[tuple[str, str, int | None, str]] = []
    for item in unfamiliar_items:
        if isinstance(item, tuple):
            expr = str(item[0])
            meaning = str(item[1]) if len(item) > 1 and item[1] else ""
            sense_id = None
            if len(item) > 2 and item[2] is not None:
                try:
                    sense_id = int(item[2])
                except (TypeError, ValueError):
                    sense_id = None
            surface = ""
            if len(item) > 3 and item[3]:
                surface = str(item[3]).strip()
            normalized.append((expr, meaning, sense_id, surface))
        else:
            normalized.append((str(item), "", None, ""))

    # Deduplicate by expression
    seen: set[str] = set()
    deduped: list[tuple[str, str, int | None, str]] = []
    for expr, meaning, sense_id, surface in normalized:
        key = normalize_sentence(expr)
        if key and key not in seen:
            seen.add(key)
            # Prefer verified_surfaces map when the item did not carry surface.
            if not surface and verified_surfaces:
                surface = (
                    verified_surfaces.get(expr)
                    or verified_surfaces.get(key)
                    or ""
                )
                surface = str(surface).strip()
            deduped.append((expr, meaning, sense_id, surface))

    if not deduped:
        raise ValueError("At least one non-empty unfamiliar item is required.")

    today = datetime.date.today().isoformat()

    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO cards (front, back, box, next_review) VALUES (?, ?, 1, ?)",
            (sentence, back, today),
        )
        card_id = cur.lastrowid

        for expr, meaning, preferred_sense_id, surface in deduped:
            sense = None
            if preferred_sense_id is not None:
                from .senses import get_sense

                pref = get_sense(conn, preferred_sense_id, commit=False)
                if pref is not None and normalize_sentence(
                    pref.expression
                ) == normalize_sentence(expr):
                    sense = pref
                    meaning = pref.meaning
            if sense is None:
                sense = create_or_get_sense(
                    conn, expr, meaning, commit=False
                )
            cur.execute(
                "INSERT INTO unfamiliar_items "
                "(card_id, expression, meaning, sense_id, surface_form) "
                "VALUES (?, ?, ?, ?, ?)",
                (card_id, expr, meaning, sense.id, surface or ""),
            )

        from .senses import purge_orphan_senses

        purge_orphan_senses(conn, commit=False)
        conn.commit()
        return card_id
    except Exception:
        conn.rollback()
        raise


def get_sentence_card(conn, card_id: int):
    """Retrieve a sentence card with its unfamiliar items.

    Returns (front, back, box, [(expression, meaning, sense_id, surface_form), ...])
    or None. *sense_id* / *surface_form* may be empty for legacy rows.
    """
    cur = conn.cursor()
    cur.execute(
        "SELECT front, back, box FROM cards WHERE id=?", (card_id,)
    )
    card = cur.fetchone()
    if card is None:
        return None

    # surface_form may be missing on very old connections before migration.
    ensure_sentence_schema(conn)
    cur.execute(
        "SELECT expression, meaning, sense_id, surface_form "
        "FROM unfamiliar_items WHERE card_id=? ORDER BY id",
        (card_id,),
    )
    items = [
        (row[0], row[1], row[2], row[3] or "")
        for row in cur.fetchall()
    ]

    return (card[0], card[1], card[2], items)


def update_sentence_card(
    conn,
    card_id: int,
    *,
    front: str,
    back: str,
    items: list,
    verified_surfaces: dict[str, str] | None = None,
) -> None:
    """Update a sentence card's front, back, and unfamiliar items.

    Resets the card to Box 1 with today's review date.

    *items* may be list of str or list of (expression, meaning) tuples.
    *verified_surfaces* is optional lemma→surface for residual forms.

    Raises ValueError if sentence is empty or no items provided.
    """
    if not front or not front.strip():
        raise ValueError("Sentence must be non-empty.")
    if not items:
        raise ValueError("At least one unfamiliar item is required.")

    # Validate that every expression appears in the sentence (local rules,
    # plus any dialog-verified residual surfaces).
    _validate_expressions_in_sentence(
        front, items, verified_surfaces=verified_surfaces
    )

    # Reject empty meanings for sentence cards (newly edited)
    _require_nonempty_meanings(items, "update")

    ensure_sentence_schema(conn)

    from .senses import create_or_get_sense, get_sense

    # Normalize items to (expression, meaning, sense_id, surface_form)
    normalized: list[tuple[str, str, int | None, str]] = []
    for item in items:
        if isinstance(item, tuple):
            expr = str(item[0])
            meaning = str(item[1]) if len(item) > 1 and item[1] else ""
            sense_id = None
            if len(item) > 2 and item[2] is not None:
                try:
                    sense_id = int(item[2])
                except (TypeError, ValueError):
                    sense_id = None
            surface = ""
            if len(item) > 3 and item[3]:
                surface = str(item[3]).strip()
            normalized.append((expr, meaning, sense_id, surface))
        else:
            normalized.append((str(item), "", None, ""))

    # Deduplicate by expression
    seen: set[str] = set()
    deduped: list[tuple[str, str, int | None, str]] = []
    for expr, meaning, sense_id, surface in normalized:
        key = normalize_sentence(expr)
        if key and key not in seen:
            seen.add(key)
            if not surface and verified_surfaces:
                surface = (
                    verified_surfaces.get(expr)
                    or verified_surfaces.get(key)
                    or ""
                )
                surface = str(surface).strip()
            deduped.append((expr, meaning, sense_id, surface))

    if not deduped:
        raise ValueError("At least one non-empty unfamiliar item is required.")

    today = datetime.date.today().isoformat()

    try:
        cur = conn.cursor()
        cur.execute(
            "UPDATE cards SET front=?, back=?, box=1, next_review=? WHERE id=?",
            (front, back, today, card_id),
        )

        # Replace unfamiliar items: delete old, insert new.
        cur.execute("DELETE FROM unfamiliar_items WHERE card_id=?", (card_id,))
        for expr, meaning, preferred_sense_id, surface in deduped:
            sense = None
            if preferred_sense_id is not None:
                pref = get_sense(conn, preferred_sense_id, commit=False)
                if pref is not None and normalize_sentence(
                    pref.expression
                ) == normalize_sentence(expr):
                    sense = pref
                    meaning = pref.meaning
            if sense is None:
                sense = create_or_get_sense(
                    conn, expr, meaning, commit=False
                )
            cur.execute(
                "INSERT INTO unfamiliar_items "
                "(card_id, expression, meaning, sense_id, surface_form) "
                "VALUES (?, ?, ?, ?, ?)",
                (card_id, expr, meaning, sense.id, surface or ""),
            )

        from .senses import purge_orphan_senses

        purge_orphan_senses(conn, commit=False)
        conn.commit()
    except Exception:
        conn.rollback()
        raise


# ---------------------------------------------------------------------------
# Database discovery
# ---------------------------------------------------------------------------

def find_databases(base_dir=None):
    """Recursively find all _barsky.db files under *base_dir*.

    If *base_dir* is None, uses the configured database root
    (``get_database_root()``), which defaults to project ``db/``.

    Returns list of (display_name, full_path) sorted by display name.
    """
    if base_dir is None:
        from .config import get_database_root
        base_dir = get_database_root()

    results = []
    if not os.path.isdir(base_dir):
        return results

    for root, dirs, files in os.walk(base_dir):
        for f in files:
            if f.endswith(DB_SUFFIX):
                full_path = os.path.join(root, f)
                db_name = f[: -len(DB_SUFFIX)]
                rel_dir = os.path.relpath(root, base_dir)
                if rel_dir == ".":
                    display = db_name
                else:
                    display = os.path.join(rel_dir, db_name)
                results.append((display, full_path))

    results.sort(key=lambda x: x[0].lower())
    return results
