"""Regression tests for sentence-card schema persistence."""

import os
import sqlite3

import pytest

from kgb_srs.schema import (
    ensure_unfamiliar_items_table,
    get_sentence_card,
    init_db,
    insert_sentence_card,
    resolve_db_path,
    update_sentence_card,
)


class TestMigrationMeaningColumn:
    """Test that the migration safely adds a meaning column to unfamiliar_items."""

    @pytest.fixture
    def legacy_conn(self):
        """Simulate a legacy DB: cards + unfamiliar_items WITHOUT meaning column."""
        conn = sqlite3.connect(":memory:")
        init_db(conn)
        ensure_unfamiliar_items_table(conn)
        conn.commit()
        yield conn
        conn.close()

    @pytest.fixture
    def conn_with_meaning(self):
        """A DB after migration with meaning column."""
        conn = sqlite3.connect(":memory:")
        init_db(conn)
        ensure_unfamiliar_items_table(conn)
        # Call the migration function (will be created later)
        from kgb_srs.schema import migrate_unfamiliar_items_meaning
        migrate_unfamiliar_items_meaning(conn)
        conn.commit()
        yield conn
        conn.close()

    def test_migration_adds_meaning_column(self, legacy_conn):
        """After migration, the meaning column must exist."""
        from kgb_srs.schema import migrate_unfamiliar_items_meaning
        migrate_unfamiliar_items_meaning(legacy_conn)
        cur = legacy_conn.execute("PRAGMA table_info(unfamiliar_items)")
        cols = {row[1]: row[2] for row in cur.fetchall()}
        assert "meaning" in cols

    def test_migration_preserves_existing_data(self, legacy_conn):
        """Existing expression data must survive the migration."""
        cid = insert_sentence_card(legacy_conn, "Hello world", [("world", "the earth")])
        from kgb_srs.schema import migrate_unfamiliar_items_meaning
        migrate_unfamiliar_items_meaning(legacy_conn)
        cur = legacy_conn.execute(
            "SELECT expression, meaning FROM unfamiliar_items WHERE card_id=?",
            (cid,)
        )
        rows = cur.fetchall()
        assert len(rows) == 1
        assert rows[0][0] == "world"
        assert rows[0][1] == "the earth"

    def test_migration_idempotent(self, legacy_conn):
        """Calling migration twice must not fail."""
        from kgb_srs.schema import migrate_unfamiliar_items_meaning
        migrate_unfamiliar_items_meaning(legacy_conn)
        migrate_unfamiliar_items_meaning(legacy_conn)  # second call

    def test_meaning_column_not_null(self, conn_with_meaning):
        """Meaning must be NOT NULL with default ''."""
        cur = conn_with_meaning.execute("PRAGMA table_info(unfamiliar_items)")
        cols = {row[1]: row for row in cur.fetchall()}
        meaning_row = cols.get("meaning")
        assert meaning_row is not None
        # NOT NULL column has 'notnull' = 1
        assert meaning_row[3] == 1

    def test_fk_cascade_still_works(self, conn_with_meaning):
        """FK cascade must still work after migration."""
        from kgb_srs.schema import insert_sentence_card as isc
        # Use the updated insert that supports meanings
        cid = isc(conn_with_meaning, "Hello world",
                  [("world", "the earth")])
        conn_with_meaning.execute("DELETE FROM cards WHERE id=?", (cid,))
        conn_with_meaning.commit()
        cur = conn_with_meaning.execute(
            "SELECT COUNT(*) FROM unfamiliar_items WHERE card_id=?", (cid,))
        assert cur.fetchone()[0] == 0

    def test_unique_still_enforced(self, conn_with_meaning):
        """UNIQUE(card_id, expression) still enforced at DB level."""
        from kgb_srs.schema import insert_sentence_card as isc
        cid = isc(conn_with_meaning, "a test", [("a", "m1")])
        # Direct insert of duplicate expression at DB level still fails
        with pytest.raises(sqlite3.IntegrityError):
            conn_with_meaning.execute(
                "INSERT INTO unfamiliar_items (card_id, expression, meaning) "
                "VALUES (?, ?, ?)", (cid, "a", "m2"))


class TestSentenceCRUDWithMeanings:
    """CRUD must accept/return expression+meaning pairs."""

    @pytest.fixture
    def conn(self):
        c = sqlite3.connect(":memory:")
        init_db(c)
        ensure_unfamiliar_items_table(c)
        from kgb_srs.schema import migrate_unfamiliar_items_meaning
        migrate_unfamiliar_items_meaning(c)
        yield c
        c.close()

    def test_insert_with_meanings(self, conn):
        """insert_sentence_card must accept (expression, meaning) pairs."""
        cid = insert_sentence_card(
            conn, "Je suis ici",
            [("suis", "am"), ("ici", "here")]
        )
        cur = conn.execute(
            "SELECT expression, meaning FROM unfamiliar_items "
            "WHERE card_id=? ORDER BY id", (cid,))
        rows = cur.fetchall()
        assert len(rows) == 2
        assert rows[0] == ("suis", "am")
        assert rows[1] == ("ici", "here")

    def test_insert_backward_compat_strings(self, conn):
        """insert_sentence_card accepts plain strings but requires meanings for sentence cards."""
        # Bare strings without meanings are now rejected
        with pytest.raises(ValueError, match="meaning"):
            insert_sentence_card(conn, "Hello world", ["world"])

    def test_get_returns_meanings(self, conn):
        """get_sentence_card must return meanings."""
        cid = insert_sentence_card(
            conn, "a b test", [("a", "meaning A"), ("b", "meaning B")]
        )
        result = get_sentence_card(conn, cid)
        front, back, box, items = result
        # items should now be list of (expression, meaning) tuples
        assert isinstance(items, list)
        assert len(items) == 2
        assert items[0][0] == "a"
        assert items[0][1] == "meaning A"
        assert items[1][0] == "b"
        assert items[1][1] == "meaning B"

    def test_update_with_meanings(self, conn):
        """update_sentence_card must accept (expression, meaning) pairs."""
        cid = insert_sentence_card(conn, "Old sentence", [("old", "old meaning")])
        update_sentence_card(
            conn, cid, front="New sentence", back="Rendered",
            items=[("new", "new meaning")]
        )
        result = get_sentence_card(conn, cid)
        assert result[0] == "New sentence"
        assert result[1] == "Rendered"
        assert result[3][0][0] == "new"
        assert result[3][0][1] == "new meaning"
        assert result[3][0][2] is not None

    def test_cards_back_is_rendered_representation(self, conn):
        """cards.back is a rendered/cache field, not the source of truth."""
        cid = insert_sentence_card(
            conn, "expr meaning text", [("expr", "meaning")], back="Rendered back"
        )
        cur = conn.execute("SELECT back FROM cards WHERE id=?", (cid,))
        assert cur.fetchone()[0] == "Rendered back"


class TestSentenceDuplicateDetection:
    """Duplicate detection based on normalized sentence + normalized set/order
    of expressions."""

    @pytest.fixture
    def conn(self):
        c = sqlite3.connect(":memory:")
        init_db(c)
        ensure_unfamiliar_items_table(c)
        from kgb_srs.schema import migrate_unfamiliar_items_meaning
        migrate_unfamiliar_items_meaning(c)
        yield c
        c.close()

    def test_duplicate_detected(self, conn):
        """Same sentence + same expressions -> duplicate."""
        from kgb_srs.schema import find_duplicate_sentence_card
        insert_sentence_card(conn, "Hello world", [("world", "the earth")])
        dup = find_duplicate_sentence_card(
            conn, "Hello world", [("world", "the earth")])
        assert dup is not None

    def test_different_expressions_not_duplicate(self, conn):
        """Same sentence, different expressions -> not duplicate."""
        from kgb_srs.schema import find_duplicate_sentence_card
        insert_sentence_card(conn, "Hello world", [("world", "the earth")])
        dup = find_duplicate_sentence_card(
            conn, "Hello world", [("hello", "greeting")])
        assert dup is None

    def test_different_sentence_not_duplicate(self, conn):
        """Different sentence, same expressions -> not duplicate."""
        from kgb_srs.schema import find_duplicate_sentence_card
        insert_sentence_card(conn, "Hello world", [("world", "the earth")])
        dup = find_duplicate_sentence_card(
            conn, "Goodbye world", [("world", "the earth")])
        assert dup is None

    def test_case_insensitive_duplicate(self, conn):
        """Case differences in sentence -> still duplicate."""
        from kgb_srs.schema import find_duplicate_sentence_card
        insert_sentence_card(conn, "Hello world", [("world", "earth")])
        dup = find_duplicate_sentence_card(
            conn, "HELLO WORLD", [("world", "earth")])
        assert dup is not None

    def test_subset_expressions_not_duplicate(self, conn):
        """Same sentence, subset of expressions -> not duplicate."""
        from kgb_srs.schema import find_duplicate_sentence_card
        insert_sentence_card(conn, "Hello world",
                            [("hello", "g"), ("world", "e")])
        dup = find_duplicate_sentence_card(
            conn, "Hello world", [("world", "earth")])
        assert dup is None


class TestAtomicOperations:
    """Card + child insert/update must be atomic (rollback on error)."""

    @pytest.fixture
    def conn(self):
        c = sqlite3.connect(":memory:")
        init_db(c)
        ensure_unfamiliar_items_table(c)
        from kgb_srs.schema import migrate_unfamiliar_items_meaning
        migrate_unfamiliar_items_meaning(c)
        yield c
        c.close()

    def test_rollback_on_duplicate_expression(self, conn):
        """Duplicate expressions are deduplicated - no partial state left."""
        from kgb_srs.schema import insert_sentence_card as isc
        card_count_before = conn.execute(
            "SELECT COUNT(*) FROM cards").fetchone()[0]

        # Insert with duplicate expressions — should deduplicate gracefully
        cid = isc(conn, "Test sentence with dup", [("dup", "m1"), ("dup", "m2")])
        card_count_after = conn.execute(
            "SELECT COUNT(*) FROM cards").fetchone()[0]
        assert card_count_after == card_count_before + 1

        # Should have exactly 1 child (the duplicate was deduplicated)
        child_count = conn.execute(
            "SELECT COUNT(*) FROM unfamiliar_items WHERE card_id=?",
            (cid,)).fetchone()[0]
        assert child_count == 1

    def test_empty_sentence_rejected(self, conn):
        """Empty sentence must be rejected before any DB operation."""
        from kgb_srs.schema import insert_sentence_card as isc
        with pytest.raises(ValueError):
            isc(conn, "", [("test", "meaning")])
        with pytest.raises(ValueError):
            isc(conn, "   ", [("test", "meaning")])

    def test_no_expressions_rejected(self, conn):
        """At least one expression must be provided."""
        from kgb_srs.schema import insert_sentence_card as isc
        with pytest.raises(ValueError):
            isc(conn, "Hello", [])
        with pytest.raises(ValueError):
            isc(conn, "Hello", None)


class TestDuplicateOrdered:
    """Duplicate detection uses normalized ordered expression list,
    not set comparison."""

    @pytest.fixture
    def conn(self):
        c = sqlite3.connect(":memory:")
        init_db(c)
        ensure_unfamiliar_items_table(c)
        from kgb_srs.schema import migrate_unfamiliar_items_meaning
        migrate_unfamiliar_items_meaning(c)
        yield c
        c.close()

    def test_same_order_is_duplicate(self, conn):
        """Same expressions in same order -> duplicate."""
        from kgb_srs.schema import find_duplicate_sentence_card
        insert_sentence_card(conn, "A B C", [("A", "m1"), ("B", "m2"), ("C", "m3")])
        dup = find_duplicate_sentence_card(
            conn, "A B C", [("A", "m1"), ("B", "m2"), ("C", "m3")])
        assert dup is not None

    def test_different_order_not_duplicate(self, conn):
        """Same expressions in different order -> NOT duplicate (ordered list)."""
        from kgb_srs.schema import find_duplicate_sentence_card
        insert_sentence_card(conn, "A B C", [("A", "m1"), ("B", "m2"), ("C", "m3")])
        dup = find_duplicate_sentence_card(
            conn, "A B C", [("C", "m3"), ("B", "m2"), ("A", "m1")])
        assert dup is None

    def test_matches_legacy_normalized_semantics_for_legacy_rows(self, conn):
        """The bounded query retains the old normalizing/filtering behavior."""
        from kgb_srs.schema import find_duplicate_sentence_card
        from kgb_srs.validation import normalize_sentence

        def add_card(front, expressions):
            card_id = conn.execute("INSERT INTO cards (front) VALUES (?)", (front,)).lastrowid
            conn.executemany(
                "INSERT INTO unfamiliar_items (card_id, expression) VALUES (?, ?)",
                [(card_id, expression) for expression in expressions],
            )
            return card_id

        first_match = add_card(
            "Straße\nCAFE\u0301", ["  ALPHA  ", "BETA\u0301"]
        )
        add_card("STRASSE café", ["beta\u0301", "alpha"])
        add_card("STRASSE café", ["", "alpha", "beta\u0301"])
        add_card("unrelated", ["alpha", "beta\u0301"])
        conn.commit()

        def legacy_match(sentence, items):
            norm_sentence = normalize_sentence(sentence)
            if not norm_sentence:
                return None
            if items and isinstance(items[0], tuple):
                norm_items = [normalize_sentence(item[0]) for item in items]
            else:
                norm_items = [normalize_sentence(item) for item in items]
            norm_items = [item for item in norm_items if item]
            if not norm_items:
                return None

            for card_id, front in conn.execute("SELECT id, front FROM cards"):
                if normalize_sentence(front) != norm_sentence:
                    continue
                existing = [
                    normalize_sentence(row[0])
                    for row in conn.execute(
                        "SELECT expression FROM unfamiliar_items "
                        "WHERE card_id=? ORDER BY id",
                        (card_id,),
                    )
                ]
                if [item for item in existing if item] == norm_items:
                    return card_id
            return None

        cases = [
            ("  STRASSE   café ", [("alpha", "ignored"), ("beta\u0301", "ignored")]),
            ("STRASSE café", ["beta\u0301", "alpha"]),
            ("STRASSE café", ["", "alpha", "beta\u0301"]),
            ("unrelated", ["alpha", "beta\u0301"]),
            ("   ", ["alpha"]),
            ("STRASSE café", ["   "]),
        ]

        for sentence, items in cases:
            assert find_duplicate_sentence_card(conn, sentence, items) == legacy_match(
                sentence, items
            )
        assert find_duplicate_sentence_card(
            conn, "STRASSE café", ["alpha", "beta\u0301"]
        ) == first_match

    def test_fetches_matching_cards_and_children_in_one_query(self, conn):
        """Candidate child rows are fetched once, never once per card."""
        from kgb_srs.schema import find_duplicate_sentence_card

        for index in range(5):
            card_id = conn.execute(
                "INSERT INTO cards (front) VALUES (?)", ("Same sentence",)
            ).lastrowid
            conn.execute(
                "INSERT INTO unfamiliar_items (card_id, expression) VALUES (?, ?)",
                (card_id, f"other-{index}"),
            )
        matching_id = conn.execute(
            "INSERT INTO cards (front) VALUES (?)", ("Same sentence",)
        ).lastrowid
        conn.execute(
            "INSERT INTO unfamiliar_items (card_id, expression) VALUES (?, ?)",
            (matching_id, "target"),
        )
        conn.commit()

        statements = []
        conn.set_trace_callback(statements.append)
        try:
            assert find_duplicate_sentence_card(
                conn, "  SAME\nsentence ", ["TARGET"]
            ) == matching_id
        finally:
            conn.set_trace_callback(None)

        assert len(statements) == 2
        assert "kgb_normalize_sentence(front)" in statements[0]
        assert "JOIN unfamiliar_items" in statements[1]


class TestDBPathHardening:
    """DB path resolution uses realpath and commonpath."""

    def test_traversal_via_dotdot_rejected(self, tmp_path):
        base = str(tmp_path)
        with pytest.raises(ValueError):
            resolve_db_path(base, "sub", "../../../etc/mydb")

    def test_commonpath_validation(self, tmp_path):
        base = str(tmp_path)
        os.makedirs(os.path.join(base, "Language-based", "Sentence-based"))
        result = resolve_db_path(base, "Language-based/Sentence-based", "French")
        real_base = os.path.realpath(base)
        assert os.path.commonpath([real_base, result]) == real_base

    def test_normal_resolution(self, tmp_path):
        base = str(tmp_path)
        result = resolve_db_path(base, "Knowledge-based", "Math")
        expected = os.path.realpath(os.path.join(base, "Knowledge-based", "Math_barsky.db"))
        assert result == expected


class TestPersistenceInvariants:
    """insert_sentence_card and update_sentence_card enforce invariants."""

    @pytest.fixture
    def conn(self):
        c = sqlite3.connect(":memory:")
        init_db(c)
        ensure_unfamiliar_items_table(c)
        from kgb_srs.schema import migrate_unfamiliar_items_meaning
        migrate_unfamiliar_items_meaning(c)
        yield c
        c.close()

    def test_expression_not_in_sentence_rejected_insert(self, conn):
        """Insert rejects expressions not found in sentence."""
        with pytest.raises(ValueError, match="not found"):
            insert_sentence_card(
                conn, "Hello world", [("not_there", "meaning")]
            )

    def test_expression_not_in_sentence_rejected_update(self, conn):
        """Update rejects expressions not found in sentence."""
        cid = insert_sentence_card(conn, "Hello world", [("world", "earth")])
        with pytest.raises(ValueError, match="not found"):
            update_sentence_card(
                conn, cid, front="Hello world", back="M",
                items=[("not_there", "meaning")],
            )

    def test_empty_meaning_rejected_insert(self, conn):
        """Insert rejects items with empty meaning."""
        with pytest.raises(ValueError, match="meaning"):
            insert_sentence_card(conn, "Hello world", [("world", "")])

    def test_empty_meaning_rejected_update(self, conn):
        """Update rejects items with empty meaning."""
        cid = insert_sentence_card(conn, "Hello world", [("world", "earth")])
        with pytest.raises(ValueError, match="meaning"):
            update_sentence_card(
                conn, cid, front="Hello world", back="M",
                items=[("world", "")],
            )

    def test_rollback_no_partial_write(self, conn):
        """A failed insert must not leave partial data."""
        count_before = conn.execute("SELECT COUNT(*) FROM cards").fetchone()[0]
        try:
            insert_sentence_card(
                conn, "Hello world", [("not_in_sentence", "meaning")]
            )
        except ValueError:
            pass
        count_after = conn.execute("SELECT COUNT(*) FROM cards").fetchone()[0]
        assert count_after == count_before
