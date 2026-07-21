"""Global expression-sense inventory for sentence databases.

Conceptual key for a dictionary unit:
    (normalized_expression, sense_id)

Sentence cards store contextual usages; each usage links to a global sense.
Word/phrase databases can be derived by projecting unique senses per
expression, using source sentences as examples.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Iterable

from .validation import normalize_sentence


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Sense:
    """One meaning of an expression in the global inventory."""

    id: int
    expression: str
    meaning: str
    expression_norm: str
    meaning_norm: str


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


def ensure_expression_senses_table(conn) -> None:
    """Create expression_senses and link column on unfamiliar_items.

    Idempotent. Safe on legacy DBs.
    """
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS expression_senses (
            id INTEGER PRIMARY KEY,
            expression TEXT NOT NULL,
            meaning TEXT NOT NULL,
            expression_norm TEXT NOT NULL,
            meaning_norm TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (date('now')),
            UNIQUE(expression_norm, meaning_norm)
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_expression_senses_expr_norm "
        "ON expression_senses(expression_norm)"
    )

    # Optional link from sentence items → global sense.
    cur = conn.cursor()
    cur.execute("PRAGMA table_info(unfamiliar_items)")
    cols = {row[1] for row in cur.fetchall()}
    if cols and "sense_id" not in cols:
        conn.execute(
            "ALTER TABLE unfamiliar_items ADD COLUMN sense_id INTEGER "
            "REFERENCES expression_senses(id)"
        )
    conn.commit()


# ---------------------------------------------------------------------------
# Lookup / create
# ---------------------------------------------------------------------------


def _row_to_sense(row) -> Sense:
    return Sense(
        id=int(row[0]),
        expression=str(row[1]),
        meaning=str(row[2]),
        expression_norm=str(row[3]),
        meaning_norm=str(row[4]),
    )


def list_senses_for_expression(conn, expression: str) -> list[Sense]:
    """Return all known senses for *expression* (normalized match)."""
    ensure_expression_senses_table(conn)
    expr_norm = normalize_sentence(expression)
    if not expr_norm:
        return []
    cur = conn.cursor()
    cur.execute(
        "SELECT id, expression, meaning, expression_norm, meaning_norm "
        "FROM expression_senses WHERE expression_norm=? ORDER BY id",
        (expr_norm,),
    )
    return [_row_to_sense(r) for r in cur.fetchall()]


def get_sense(conn, sense_id: int) -> Sense | None:
    ensure_expression_senses_table(conn)
    cur = conn.cursor()
    cur.execute(
        "SELECT id, expression, meaning, expression_norm, meaning_norm "
        "FROM expression_senses WHERE id=?",
        (sense_id,),
    )
    row = cur.fetchone()
    return _row_to_sense(row) if row else None


def find_sense_by_meaning(
    conn, expression: str, meaning: str
) -> Sense | None:
    """Exact normalized (expression, meaning) lookup."""
    ensure_expression_senses_table(conn)
    expr_norm = normalize_sentence(expression)
    meaning_norm = normalize_sentence(meaning)
    if not expr_norm or not meaning_norm:
        return None
    cur = conn.cursor()
    cur.execute(
        "SELECT id, expression, meaning, expression_norm, meaning_norm "
        "FROM expression_senses "
        "WHERE expression_norm=? AND meaning_norm=?",
        (expr_norm, meaning_norm),
    )
    row = cur.fetchone()
    return _row_to_sense(row) if row else None


def create_or_get_sense(
    conn,
    expression: str,
    meaning: str,
    *,
    commit: bool = True,
) -> Sense:
    """Insert a sense if missing; return existing on exact norm match.

    Identity is (expression_norm, meaning_norm). Display text keeps the
    first-seen wording.
    """
    ensure_expression_senses_table(conn)
    expr = (expression or "").strip()
    meaning_text = (meaning or "").strip()
    if not expr:
        raise ValueError("expression must be non-empty")
    if not meaning_text:
        raise ValueError("meaning must be non-empty")

    existing = find_sense_by_meaning(conn, expr, meaning_text)
    if existing is not None:
        return existing

    expr_norm = normalize_sentence(expr)
    meaning_norm = normalize_sentence(meaning_text)
    cur = conn.cursor()
    try:
        cur.execute(
            "INSERT INTO expression_senses "
            "(expression, meaning, expression_norm, meaning_norm) "
            "VALUES (?, ?, ?, ?)",
            (expr, meaning_text, expr_norm, meaning_norm),
        )
        sense_id = int(cur.lastrowid)
        if commit:
            conn.commit()
        return Sense(
            id=sense_id,
            expression=expr,
            meaning=meaning_text,
            expression_norm=expr_norm,
            meaning_norm=meaning_norm,
        )
    except Exception:
        # Race / unique conflict: re-read.
        conn.rollback()
        existing = find_sense_by_meaning(conn, expr, meaning_text)
        if existing is not None:
            return existing
        raise


def resolve_sense_for_item(
    conn,
    expression: str,
    meaning: str,
    preferred_sense_id: int | None = None,
    *,
    commit: bool = True,
) -> Sense:
    """Resolve a sense for a sentence item.

    Prefer an explicit sense_id when it matches the expression; otherwise
    create/get by (expression, meaning).
    """
    if preferred_sense_id is not None:
        sense = get_sense(conn, preferred_sense_id)
        if sense is not None:
            if normalize_sentence(sense.expression) == normalize_sentence(
                expression
            ):
                # Keep meaning text aligned with stored sense if empty.
                return sense
    return create_or_get_sense(conn, expression, meaning, commit=commit)


def list_all_senses(conn) -> list[Sense]:
    ensure_expression_senses_table(conn)
    cur = conn.cursor()
    cur.execute(
        "SELECT id, expression, meaning, expression_norm, meaning_norm "
        "FROM expression_senses ORDER BY expression_norm, id"
    )
    return [_row_to_sense(r) for r in cur.fetchall()]


def group_senses_by_expression(conn) -> dict[str, list[Sense]]:
    """Map expression_norm → senses (stable order)."""
    grouped: dict[str, list[Sense]] = {}
    display_expr: dict[str, str] = {}
    for sense in list_all_senses(conn):
        key = sense.expression_norm
        grouped.setdefault(key, []).append(sense)
        display_expr.setdefault(key, sense.expression)
    # Attach display forms via a side map is awkward for callers; return
    # grouped by norm key. Callers use sense.expression for display.
    return grouped


def example_sentences_for_sense(
    conn, sense_id: int, *, limit: int = 5
) -> list[str]:
    """Sentences that use this sense (via unfamiliar_items.sense_id or meaning)."""
    ensure_expression_senses_table(conn)
    sense = get_sense(conn, sense_id)
    if sense is None:
        return []

    cur = conn.cursor()
    examples: list[str] = []
    seen: set[str] = set()

    # Prefer explicit sense_id links.
    cur.execute(
        """
        SELECT DISTINCT c.front
        FROM unfamiliar_items ui
        JOIN cards c ON c.id = ui.card_id
        WHERE ui.sense_id = ?
        ORDER BY c.id
        """,
        (sense_id,),
    )
    for (front,) in cur.fetchall():
        text = (front or "").strip()
        key = normalize_sentence(text)
        if text and key not in seen:
            seen.add(key)
            examples.append(text)
            if len(examples) >= limit:
                return examples

    # Fallback: same expression_norm + same meaning_norm on items.
    cur.execute(
        """
        SELECT DISTINCT c.front
        FROM unfamiliar_items ui
        JOIN cards c ON c.id = ui.card_id
        WHERE lower(trim(ui.expression)) = lower(trim(?))
          AND lower(trim(ui.meaning)) = lower(trim(?))
        ORDER BY c.id
        """,
        (sense.expression, sense.meaning),
    )
    for (front,) in cur.fetchall():
        text = (front or "").strip()
        key = normalize_sentence(text)
        if text and key not in seen:
            seen.add(key)
            examples.append(text)
            if len(examples) >= limit:
                break
    return examples


def build_word_phrase_back_from_senses(
    senses: Iterable[Sense],
    examples_by_sense_id: dict[int, list[str]] | None = None,
) -> str:
    """Render word/phrase card back text from senses + example sentences."""
    parts: list[str] = []
    for i, sense in enumerate(senses, 1):
        examples = []
        if examples_by_sense_id:
            examples = examples_by_sense_id.get(sense.id, [])
        example = examples[0] if examples else ""
        if example:
            parts.append(f"{i}. {sense.meaning}\n*{example}*")
        else:
            parts.append(f"{i}. {sense.meaning}")
    return "\n\n".join(parts)


def derive_word_phrase_entries(conn) -> list[tuple[str, str, list[Sense]]]:
    """Project global senses into word/phrase entries.

    Returns list of (display_expression, back_text, senses).
    """
    grouped = group_senses_by_expression(conn)
    entries: list[tuple[str, str, list[Sense]]] = []
    for _norm, senses in grouped.items():
        if not senses:
            continue
        display = senses[0].expression
        examples_map = {
            s.id: example_sentences_for_sense(conn, s.id, limit=1)
            for s in senses
        }
        back = build_word_phrase_back_from_senses(senses, examples_map)
        entries.append((display, back, senses))
    # Stable order by expression
    entries.sort(key=lambda t: normalize_sentence(t[0]))
    return entries


def upsert_word_phrase_card(
    conn,
    front: str,
    back: str,
    *,
    commit: bool = True,
) -> tuple[int, str]:
    """Insert or update a word/phrase card by front (case-insensitive).

    Returns (card_id, action) where action is 'inserted' or 'updated'.
    """
    import datetime

    front = (front or "").strip()
    back = (back or "").strip()
    if not front:
        raise ValueError("front must be non-empty")
    if not back:
        raise ValueError("back must be non-empty")

    today = datetime.date.today().isoformat()
    cur = conn.cursor()
    cur.execute(
        "SELECT id FROM cards WHERE front = ? COLLATE NOCASE",
        (front,),
    )
    row = cur.fetchone()
    if row:
        card_id = int(row[0])
        cur.execute(
            "UPDATE cards SET front=?, back=?, box=1, next_review=? WHERE id=?",
            (front, back, today, card_id),
        )
        action = "updated"
    else:
        cur.execute(
            "INSERT INTO cards (front, back, box, next_review) "
            "VALUES (?, ?, 1, ?)",
            (front, back, today),
        )
        card_id = int(cur.lastrowid)
        action = "inserted"
    if commit:
        conn.commit()
    return card_id, action


# Settings key on sentence DBs: absolute path of linked word/phrase DB.
LINKED_WORD_PHRASE_DB_KEY = "linked_word_phrase_db"


def get_linked_word_phrase_db(conn) -> str | None:
    """Return the absolute path of the linked word/phrase DB, or None."""
    cur = conn.cursor()
    cur.execute(
        "SELECT value FROM settings WHERE key = ?",
        (LINKED_WORD_PHRASE_DB_KEY,),
    )
    row = cur.fetchone()
    if row is None:
        return None
    path = (row[0] or "").strip()
    return path or None


def set_linked_word_phrase_db(conn, path: str | None, *, commit: bool = True) -> None:
    """Persist or clear the linked word/phrase DB path on a sentence DB."""
    if path is None or not str(path).strip():
        conn.execute(
            "DELETE FROM settings WHERE key = ?",
            (LINKED_WORD_PHRASE_DB_KEY,),
        )
    else:
        conn.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
            (LINKED_WORD_PHRASE_DB_KEY, os.path.abspath(str(path).strip())),
        )
    if commit:
        conn.commit()


def derive_word_phrase_database(
    source_conn,
    target_conn,
    *,
    write_type: bool = True,
    prune_missing: bool = True,
) -> dict:
    """Copy unique (expression, sense) units into a word/phrase DB.

    *source_conn* is a sentence DB with expression_senses populated
    (or backfilled from unfamiliar_items).
    *target_conn* is the destination word/phrase DB (read-only for users;
    content is always derived from the shared sense catalog).

    Returns stats: {expressions, senses, inserted, updated, pruned}.
    """
    # Ensure source inventory exists and backfill from item meanings.
    ensure_expression_senses_table(source_conn)
    backfill_senses_from_items(source_conn)

    if write_type:
        from .catalog import DatabaseType, write_database_type

        write_database_type(target_conn, DatabaseType.LANGUAGE_WORD_PHRASE)

    entries = derive_word_phrase_entries(source_conn)
    inserted = 0
    updated = 0
    sense_count = 0
    keep_fronts: set[str] = set()
    for front, back, senses in entries:
        sense_count += len(senses)
        keep_fronts.add(normalize_sentence(front))
        _id, action = upsert_word_phrase_card(
            target_conn, front, back, commit=False
        )
        if action == "inserted":
            inserted += 1
        else:
            updated += 1

    pruned = 0
    if prune_missing:
        cur = target_conn.cursor()
        cur.execute("SELECT id, front FROM cards")
        for card_id, front in cur.fetchall():
            if normalize_sentence(front or "") not in keep_fronts:
                cur.execute("DELETE FROM cards WHERE id=?", (card_id,))
                pruned += 1

    target_conn.commit()
    return {
        "expressions": len(entries),
        "senses": sense_count,
        "inserted": inserted,
        "updated": updated,
        "pruned": pruned,
    }


def default_word_phrase_path_for_sentence(
    sentence_db_path: str,
    db_root: str,
) -> str:
    """Return the canonical W/P projection path for a sentence DB.

    Same leaf name, under the Word-Phrase-based directory:
      .../Sentence-based/English_barsky.db
      → .../Word-Phrase-based/English_barsky.db
    """
    from .catalog import DB_DIR_LANGUAGE_WORD_PHRASE
    from .schema import DB_SUFFIX

    leaf = os.path.basename(sentence_db_path or "")
    if leaf.endswith(DB_SUFFIX):
        name = leaf[: -len(DB_SUFFIX)]
    else:
        name = os.path.splitext(leaf)[0] or "dictionary"
    target_dir = os.path.join(os.path.abspath(db_root), DB_DIR_LANGUAGE_WORD_PHRASE)
    return os.path.join(target_dir, f"{name}{DB_SUFFIX}")


def ensure_linked_word_phrase_database(
    source_conn,
    sentence_db_path: str,
    db_root: str,
    *,
    sync: bool = True,
) -> tuple[str, dict | None]:
    """Ensure a sentence DB has a linked W/P projection file and optionally sync it.

    Creates the target file and settings link when missing. Returns
    ``(target_path, stats_or_None)``.
    """
    from .schema import init_db

    ensure_expression_senses_table(source_conn)
    backfill_senses_from_items(source_conn)

    path = get_linked_word_phrase_db(source_conn)
    if not path or not os.path.isfile(path):
        path = default_word_phrase_path_for_sentence(sentence_db_path, db_root)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        # Create empty DB shell if needed, then link.
        target = init_db(path)
        try:
            from .catalog import DatabaseType, write_database_type

            write_database_type(target, DatabaseType.LANGUAGE_WORD_PHRASE)
        finally:
            target.close()
        set_linked_word_phrase_db(source_conn, path)

    stats = None
    if sync:
        stats = sync_linked_word_phrase_database(source_conn)
    return path, stats


def sync_linked_word_phrase_database(source_conn) -> dict | None:
    """If the sentence DB has a linked W/P path, fully re-derive it.

    Creates the target file when the link exists but the file is missing.
    Returns stats dict, or None if no link.
    """
    path = get_linked_word_phrase_db(source_conn)
    if not path:
        return None

    from .schema import init_db

    os.makedirs(os.path.dirname(path), exist_ok=True)
    target = init_db(path)
    try:
        stats = derive_word_phrase_database(source_conn, target)
    finally:
        target.close()
    return stats


def ensure_all_sentence_databases_linked(db_root: str) -> list[dict]:
    """Startup/open backfill: link + sync W/P for every sentence DB under *db_root*.

    Returns a list of {sentence_path, word_phrase_path, stats} for each
    sentence DB processed. Failures are skipped (best-effort).
    """
    from .catalog import DatabaseType, infer_database_type, read_database_type
    from .schema import find_databases, init_db, ensure_sentence_schema

    results: list[dict] = []
    if not db_root or not os.path.isdir(db_root):
        return results

    for _display, path in find_databases(db_root):
        try:
            conn = init_db(path)
        except Exception:
            continue
        try:
            db_type = read_database_type(conn)
            if db_type is None:
                db_type = infer_database_type(path)
            if db_type != DatabaseType.LANGUAGE_SENTENCE:
                continue
            ensure_sentence_schema(conn)
            wp_path, stats = ensure_linked_word_phrase_database(
                conn, path, db_root, sync=True
            )
            results.append(
                {
                    "sentence_path": path,
                    "word_phrase_path": wp_path,
                    "stats": stats,
                }
            )
        except Exception:
            continue
        finally:
            try:
                conn.close()
            except Exception:
                pass
    return results


def backfill_senses_from_items(conn, *, commit: bool = True) -> int:
    """Create senses from existing unfamiliar_items meanings and link them.

    Returns number of item rows linked/updated.
    """
    ensure_expression_senses_table(conn)
    cur = conn.cursor()
    cur.execute(
        "SELECT id, expression, meaning, sense_id FROM unfamiliar_items"
    )
    rows = cur.fetchall()
    linked = 0
    for item_id, expression, meaning, sense_id in rows:
        meaning_text = (meaning or "").strip()
        expr = (expression or "").strip()
        if not expr or not meaning_text:
            continue
        if sense_id:
            # Keep existing link if still valid.
            if get_sense(conn, int(sense_id)) is not None:
                continue
        sense = create_or_get_sense(
            conn, expr, meaning_text, commit=False
        )
        cur.execute(
            "UPDATE unfamiliar_items SET sense_id=? WHERE id=?",
            (sense.id, item_id),
        )
        linked += 1
    if commit:
        conn.commit()
    return linked
