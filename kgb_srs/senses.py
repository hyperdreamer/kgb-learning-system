"""Global expression-sense inventory for sentence databases.

Conceptual key for a dictionary unit:
    (normalized_expression, sense_id)

Sentence cards store contextual usages; each usage links to a global sense.
Word/phrase databases can be derived by projecting unique senses per
expression, using source sentences as examples.
"""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path
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


@dataclass(frozen=True)
class WordPhraseDuplicateGroup:
    """Read-only description of cards sharing one normalized W/P front."""

    normalized_front: str
    card_ids: tuple[int, ...]
    fronts: tuple[str, ...]

    def as_conflict(self) -> dict:
        """Return a JSON-friendly conflict record without SRS mutation."""
        return {
            "code": "normalized_word_phrase_front_duplicates",
            "normalized_front": self.normalized_front,
            "cards": [
                {"id": card_id, "front": front}
                for card_id, front in zip(self.card_ids, self.fronts)
            ],
        }


class WordPhraseDuplicateConflictError(ValueError):
    """Raised when an upsert would have to choose among SRS histories."""

    def __init__(self, group: WordPhraseDuplicateGroup):
        self.group = group
        self.conflict = group.as_conflict()
        super().__init__(
            "Multiple word/phrase cards share normalized front "
            f"{group.normalized_front!r}; resolve the duplicate histories "
            "manually before projecting."
        )


class ProjectionPathSafetyError(ValueError):
    """A projection path cannot safely be used or migrated automatically."""

    def __init__(self, conflict: dict):
        self.conflict = conflict
        super().__init__(conflict["message"])


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


def ensure_expression_senses_table(conn, *, commit: bool = True) -> None:
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
    if commit:
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
    ensure_expression_senses_table(conn, commit=False)
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


def get_sense(conn, sense_id: int, *, commit: bool = False) -> Sense | None:
    ensure_expression_senses_table(conn, commit=commit)
    cur = conn.cursor()
    cur.execute(
        "SELECT id, expression, meaning, expression_norm, meaning_norm "
        "FROM expression_senses WHERE id=?",
        (sense_id,),
    )
    row = cur.fetchone()
    return _row_to_sense(row) if row else None


def find_sense_by_meaning(
    conn, expression: str, meaning: str, *, commit: bool = False
) -> Sense | None:
    """Exact normalized (expression, meaning) lookup."""
    ensure_expression_senses_table(conn, commit=commit)
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

    Nested callers pass commit=False so outer transactions stay intact.
    Unique conflicts use a SAVEPOINT instead of a full rollback.
    """
    ensure_expression_senses_table(conn, commit=commit)
    expr = (expression or "").strip()
    meaning_text = (meaning or "").strip()
    if not expr:
        raise ValueError("expression must be non-empty")
    if not meaning_text:
        raise ValueError("meaning must be non-empty")

    existing = find_sense_by_meaning(conn, expr, meaning_text, commit=commit)
    if existing is not None:
        return existing

    expr_norm = normalize_sentence(expr)
    meaning_norm = normalize_sentence(meaning_text)
    cur = conn.cursor()
    cur.execute("SAVEPOINT sense_ins")
    try:
        cur.execute(
            "INSERT INTO expression_senses "
            "(expression, meaning, expression_norm, meaning_norm) "
            "VALUES (?, ?, ?, ?)",
            (expr, meaning_text, expr_norm, meaning_norm),
        )
        sense_id = int(cur.lastrowid)
        cur.execute("RELEASE SAVEPOINT sense_ins")
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
        # Race / unique conflict: undo only the nested insert attempt.
        cur.execute("ROLLBACK TO SAVEPOINT sense_ins")
        cur.execute("RELEASE SAVEPOINT sense_ins")
        existing = find_sense_by_meaning(conn, expr, meaning_text, commit=commit)
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
        sense = get_sense(conn, preferred_sense_id, commit=commit)
        if sense is not None:
            if normalize_sentence(sense.expression) == normalize_sentence(expression):
                # Keep meaning text aligned with stored sense if empty.
                return sense
    return create_or_get_sense(conn, expression, meaning, commit=commit)


def list_all_senses(conn) -> list[Sense]:
    ensure_expression_senses_table(conn, commit=False)
    cur = conn.cursor()
    cur.execute(
        "SELECT id, expression, meaning, expression_norm, meaning_norm "
        "FROM expression_senses ORDER BY expression_norm, id"
    )
    return [_row_to_sense(r) for r in cur.fetchall()]


def group_senses_by_expression(conn) -> dict[str, list[Sense]]:
    """Map expression_norm → senses that still have item references."""
    grouped: dict[str, list[Sense]] = {}
    display_expr: dict[str, str] = {}
    for sense in list_all_senses(conn):
        if not sense_has_item_references(conn, sense.id):
            continue
        key = sense.expression_norm
        grouped.setdefault(key, []).append(sense)
        display_expr.setdefault(key, sense.expression)
    # Attach display forms via a side map is awkward for callers; return
    # grouped by norm key. Callers use sense.expression for display.
    return grouped


def sense_has_item_references(conn, sense_id: int) -> bool:
    """True when at least one unfamiliar_items row points at sense_id."""
    cur = conn.cursor()
    cur.execute(
        "SELECT 1 FROM unfamiliar_items WHERE sense_id = ? LIMIT 1",
        (sense_id,),
    )
    return cur.fetchone() is not None


def purge_orphan_senses(conn, *, commit: bool = False) -> int:
    """Delete expression_senses rows with no unfamiliar_items references.

    Returns the number of deleted rows.
    """
    from .schema import ensure_unfamiliar_items_table

    ensure_unfamiliar_items_table(conn, commit=commit)
    ensure_expression_senses_table(conn, commit=commit)
    cur = conn.cursor()
    cur.execute(
        """
        DELETE FROM expression_senses
        WHERE id NOT IN (
            SELECT sense_id FROM unfamiliar_items
            WHERE sense_id IS NOT NULL
        )
        """
    )
    deleted = cur.rowcount if cur.rowcount is not None and cur.rowcount >= 0 else 0
    if commit:
        conn.commit()
    return deleted


def example_sentences_for_sense(conn, sense_id: int, *, limit: int = 5) -> list[str]:
    """Sentences that use this sense (via unfamiliar_items.sense_id or meaning)."""
    ensure_expression_senses_table(conn, commit=False)
    sense = get_sense(conn, sense_id, commit=False)
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


def _highlight_expression_in_example(example: str, expression: str) -> str:
    """Bold the surface form of *expression* inside an example sentence."""
    from .validation import highlight_unfamiliar_in_sentence

    text = (example or "").strip()
    expr = (expression or "").strip()
    if not text:
        return ""
    if not expr:
        return text
    return highlight_unfamiliar_in_sentence(text, [expr])


# Em spaces used for example indent. ASCII spaces / Qt block-indent are not
# reliable in the review WebEngine view (Chromium ignores -qt-block-indent).
_EXAMPLE_INDENT = "\u2003\u2003"  # two em spaces ≈ 2em left indent


def build_word_phrase_back_from_senses(
    senses: Iterable[Sense],
    examples_by_sense_id: dict[int, list[str]] | None = None,
) -> str:
    """Render word/phrase card back text from senses + example sentences.

    Layout per sense::

        1. <meaning>

        <example with **surface form** bolded>

    The example sits on its own line under the meaning, indented with em
    spaces so both QTextDocument and WebEngine show a clear hierarchy.
    """
    parts: list[str] = []
    for i, sense in enumerate(senses, 1):
        examples = []
        if examples_by_sense_id:
            examples = examples_by_sense_id.get(sense.id, [])
        example = examples[0] if examples else ""
        meaning = (sense.meaning or "").strip()
        block = f"{i}. {meaning}" if meaning else f"{i}."
        if example:
            highlighted = _highlight_expression_in_example(example, sense.expression)
            block = f"{block}\n\n{_EXAMPLE_INDENT}{highlighted}"
        parts.append(block)
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
            s.id: example_sentences_for_sense(conn, s.id, limit=1) for s in senses
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

    On UPDATE only front/back change; SRS box and next_review are preserved.
    On INSERT new cards start at box=1 due today.

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
    normalized_front = normalize_sentence(front)
    cur = conn.cursor()
    matches = [
        row
        for row in cur.execute("SELECT id, front, back FROM cards ORDER BY id")
        if normalize_sentence(row[1] or "") == normalized_front
    ]
    if len(matches) > 1:
        raise WordPhraseDuplicateConflictError(
            WordPhraseDuplicateGroup(
                normalized_front=normalized_front,
                card_ids=tuple(int(row[0]) for row in matches),
                fronts=tuple(row[1] or "" for row in matches),
            )
        )
    if matches:
        row = matches[0]
        card_id = int(row[0])
        existing_front = row[1] or ""
        existing_back = row[2] or ""
        if existing_front != front or existing_back != back:
            cur.execute(
                "UPDATE cards SET front=?, back=? WHERE id=?",
                (front, back, card_id),
            )
        action = "updated"
    else:
        cur.execute(
            "INSERT INTO cards (front, back, box, next_review) VALUES (?, ?, 1, ?)",
            (front, back, today),
        )
        card_id = int(cur.lastrowid)
        action = "inserted"
    if commit:
        conn.commit()
    return card_id, action


def find_normalized_word_phrase_duplicates(
    conn,
) -> list[WordPhraseDuplicateGroup]:
    """Return normalized W/P-front duplicate groups without changing *conn*.

    Groups and their member cards are deterministically ordered by normalized
    front and card id respectively.  Normalization is deliberately shared
    with projection matching, so NFC/NFD and case-only variants are detected.
    """
    rows = conn.execute("SELECT id, front FROM cards ORDER BY id").fetchall()
    grouped: dict[str, list[tuple[int, str]]] = {}
    for card_id, front in rows:
        normalized = normalize_sentence(front or "")
        grouped.setdefault(normalized, []).append((int(card_id), front or ""))

    return [
        WordPhraseDuplicateGroup(
            normalized_front=normalized,
            card_ids=tuple(card_id for card_id, _front in cards),
            fronts=tuple(front for _card_id, front in cards),
        )
        for normalized, cards in sorted(grouped.items())
        if len(cards) > 1
    ]


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

    Returns stats: {expressions, senses, inserted, updated, pruned, conflicts}.
    """
    # Ensure source inventory exists and backfill from item meanings.
    ensure_expression_senses_table(source_conn)
    backfill_senses_from_items(source_conn)

    entries = derive_word_phrase_entries(source_conn)
    inserted = 0
    updated = 0
    sense_count = 0
    keep_fronts: set[str] = set()
    conflicts: list[dict] = []
    for front, back, senses in entries:
        sense_count += len(senses)
        keep_fronts.add(normalize_sentence(front))
        try:
            _id, action = upsert_word_phrase_card(
                target_conn, front, back, commit=False
            )
        except WordPhraseDuplicateConflictError as exc:
            conflicts.append(exc.conflict)
            continue
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

    if write_type:
        from .catalog import DatabaseType, write_database_type

        write_database_type(target_conn, DatabaseType.LANGUAGE_WORD_PHRASE)

    target_conn.commit()
    return {
        "expressions": len(entries),
        "senses": sense_count,
        "inserted": inserted,
        "updated": updated,
        "pruned": pruned,
        "conflicts": conflicts,
    }


def default_word_phrase_path_for_sentence(
    sentence_db_path: str,
    db_root: str,
) -> str:
    """Return the canonical W/P projection path for a sentence DB.

    Same relative path, under the Word-Phrase-based directory:
      .../Sentence-based/English_barsky.db
      → .../Word-Phrase-based/English_barsky.db
      .../Sentence-based/English/A1_barsky.db
      → .../Word-Phrase-based/English/A1_barsky.db
    """
    from .catalog import DB_DIR_LANGUAGE_SENTENCE, DB_DIR_LANGUAGE_WORD_PHRASE
    from .schema import DB_SUFFIX

    root_lexical = os.path.abspath(str(db_root or ""))
    source_lexical = os.path.abspath(str(sentence_db_path or ""))
    sentence_root_lexical = os.path.join(root_lexical, DB_DIR_LANGUAGE_SENTENCE)
    sentence_root_real = os.path.realpath(sentence_root_lexical)
    source_real = os.path.realpath(source_lexical)

    def contained(path: str, root: str) -> bool:
        try:
            return os.path.commonpath([path, root]) == root
        except ValueError:
            return False

    if not (
        contained(source_lexical, sentence_root_lexical)
        and contained(source_real, sentence_root_real)
    ):
        raise ProjectionPathSafetyError(
            {
                "code": "sentence_projection_source_outside_root",
                "message": "Sentence DB path is outside the validated "
                "Language-based/Sentence-based root.",
                "sentence_db_path": source_lexical,
                "sentence_root": sentence_root_lexical,
                "resolved_sentence_db_path": source_real,
                "resolved_sentence_root": sentence_root_real,
            }
        )

    relative_source = os.path.relpath(source_lexical, sentence_root_lexical)
    if relative_source in ("", os.curdir) or relative_source.startswith(
        os.pardir + os.sep
    ):
        raise ProjectionPathSafetyError(
            {
                "code": "sentence_projection_source_outside_root",
                "message": "Sentence DB path must be a file below the "
                "Language-based/Sentence-based root.",
                "sentence_db_path": source_lexical,
                "sentence_root": sentence_root_lexical,
            }
        )

    relative_parent, leaf = os.path.split(relative_source)
    if leaf.endswith(DB_SUFFIX):
        name = leaf[: -len(DB_SUFFIX)]
    else:
        name = os.path.splitext(leaf)[0] or "dictionary"
    target_lexical = os.path.abspath(
        os.path.join(
            root_lexical,
            DB_DIR_LANGUAGE_WORD_PHRASE,
            relative_parent,
            f"{name}{DB_SUFFIX}",
        )
    )
    target_real = os.path.realpath(target_lexical)
    root_real = os.path.realpath(root_lexical)
    if not (
        contained(target_lexical, root_lexical) and contained(target_real, root_real)
    ):
        raise ProjectionPathSafetyError(
            {
                "code": "sentence_projection_target_escapes_root",
                "message": "Canonical word/phrase projection path escapes "
                "the configured database root.",
                "sentence_db_path": source_lexical,
                "db_root": root_lexical,
                "canonical_path": target_lexical,
                "resolved_canonical_path": target_real,
                "resolved_db_root": root_real,
            }
        )
    return target_lexical


def _legacy_flat_word_phrase_path(sentence_db_path: str, db_root: str) -> str:
    """Return the pre-nested-mirroring projection location for migration checks."""
    from .catalog import DB_DIR_LANGUAGE_WORD_PHRASE
    from .schema import DB_SUFFIX

    leaf = os.path.basename(sentence_db_path)
    name = (
        leaf[: -len(DB_SUFFIX)]
        if leaf.endswith(DB_SUFFIX)
        else (os.path.splitext(leaf)[0] or "dictionary")
    )
    return os.path.abspath(
        os.path.join(db_root, DB_DIR_LANGUAGE_WORD_PHRASE, f"{name}{DB_SUFFIX}")
    )


def _raise_for_populated_legacy_flat_link(
    linked_path: str | None,
    sentence_db_path: str,
    db_root: str,
    canonical_path: str,
) -> None:
    """Block unsafe automatic migration from a populated old flat target."""
    if not linked_path:
        return
    legacy_path = _legacy_flat_word_phrase_path(sentence_db_path, db_root)
    # Direct children keep the legacy flat location.  Nested sources have a
    # different lexical mirror even if a local symlink happens to resolve the
    # two paths to the same inode.
    if os.path.abspath(legacy_path) == os.path.abspath(canonical_path):
        return
    if os.path.realpath(linked_path) != os.path.realpath(legacy_path):
        return

    card_count = 0
    if os.path.isfile(linked_path):
        try:
            target = sqlite3.connect(
                f"{Path(os.path.realpath(linked_path)).as_uri()}?mode=ro", uri=True
            )
            try:
                card_count = int(
                    target.execute("SELECT COUNT(*) FROM cards").fetchone()[0]
                )
            finally:
                target.close()
        except (OSError, ValueError, sqlite3.Error):
            # An unreadable legacy file cannot be proven empty, so retain the
            # link and require an explicit user decision instead of replacing it.
            card_count = -1
    if card_count:
        raise ProjectionPathSafetyError(
            {
                "code": "legacy_word_phrase_projection_conflict",
                "message": "A populated legacy flat word/phrase projection "
                "cannot be relinked automatically.",
                "linked_path": os.path.abspath(linked_path),
                "legacy_flat_path": legacy_path,
                "canonical_path": canonical_path,
                "card_count": card_count,
            }
        )


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
    canonical_path = default_word_phrase_path_for_sentence(sentence_db_path, db_root)
    path = get_linked_word_phrase_db(source_conn)
    _raise_for_populated_legacy_flat_link(
        path, sentence_db_path, db_root, canonical_path
    )

    # Path validation and legacy migration checks above must remain before
    # source schema/backfill writes, target initialization, or settings edits.
    ensure_expression_senses_table(source_conn)
    backfill_senses_from_items(source_conn)

    from .schema import init_db

    if (
        not path
        or not os.path.isfile(path)
        or os.path.realpath(path) != os.path.realpath(canonical_path)
    ):
        # A saved link is not authority to derive into that file.  Only the
        # matching canonical projection path may be opened and mutated.
        path = canonical_path
        os.makedirs(os.path.dirname(canonical_path), exist_ok=True)
        # Create empty DB shell if needed, then link.
        target = init_db(canonical_path)
        try:
            from .catalog import DatabaseType, write_database_type

            write_database_type(target, DatabaseType.LANGUAGE_WORD_PHRASE)
        finally:
            target.close()
        set_linked_word_phrase_db(source_conn, canonical_path)

    stats = None
    if sync:
        stats = sync_linked_word_phrase_database(
            source_conn,
            sentence_db_path=sentence_db_path,
            db_root=db_root,
        )
    return path, stats


def sync_linked_word_phrase_database(
    source_conn,
    *,
    sentence_db_path: str | None = None,
    db_root: str | None = None,
) -> dict | None:
    """If the sentence DB has a linked W/P path, fully re-derive it.

    When the sentence DB path and root are supplied, the saved target must
    resolve to this sentence DB's canonical projection path. Legacy direct
    callers may omit them, but then only an existing target explicitly marked
    as a word/phrase DB is eligible for synchronization.

    Returns stats dict, or None when no safe linked target is available.
    """
    path = get_linked_word_phrase_db(source_conn)
    if not path:
        return None

    canonical_path = None
    if sentence_db_path is not None and db_root is not None:
        canonical_path = default_word_phrase_path_for_sentence(
            sentence_db_path, db_root
        )
        _raise_for_populated_legacy_flat_link(
            path, sentence_db_path, db_root, canonical_path
        )
        # Do not open a saved target unless it is the owned canonical file.
        if os.path.realpath(path) != os.path.realpath(canonical_path):
            return None
    else:
        # Legacy callers did not provide enough context to establish path
        # ownership. Inspect existing metadata read-only before opening the
        # target for a destructive projection refresh.
        if not os.path.isfile(path):
            return None
        try:
            target = sqlite3.connect(
                f"{Path(os.path.realpath(path)).as_uri()}?mode=ro", uri=True
            )
            try:
                from .catalog import DatabaseType, read_database_type

                if read_database_type(target) != DatabaseType.LANGUAGE_WORD_PHRASE:
                    return None
            finally:
                target.close()
        except (OSError, ValueError, sqlite3.Error):
            return None

    from .schema import init_db

    target_path = canonical_path or path
    os.makedirs(os.path.dirname(target_path), exist_ok=True)
    target = init_db(target_path)
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
    ensure_expression_senses_table(conn, commit=commit)
    cur = conn.cursor()
    cur.execute("SELECT id, expression, meaning, sense_id FROM unfamiliar_items")
    rows = cur.fetchall()
    linked = 0
    for item_id, expression, meaning, sense_id in rows:
        meaning_text = (meaning or "").strip()
        expr = (expression or "").strip()
        if not expr or not meaning_text:
            continue
        if sense_id:
            # Keep existing link if still valid.
            if get_sense(conn, int(sense_id), commit=commit) is not None:
                continue
        sense = create_or_get_sense(conn, expr, meaning_text, commit=False)
        cur.execute(
            "UPDATE unfamiliar_items SET sense_id=? WHERE id=?",
            (sense.id, item_id),
        )
        linked += 1
    if commit:
        conn.commit()
    return linked
