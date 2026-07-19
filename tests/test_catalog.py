"""Tests for kgb_srs.catalog — database type metadata and menu model."""

import os
import sqlite3
import tempfile
import pytest

from kgb_srs.catalog import (
    DatabaseType,
    DatabaseCategory,
    infer_database_type,
    read_database_type,
    write_database_type,
    build_catalog_tree,
    DB_DIR_LANGUAGE_SENTENCE,
    DB_DIR_LANGUAGE_WORD_PHRASE,
    DB_DIR_KNOWLEDGE,
)


# ---------------------------------------------------------------------------
# DatabaseType enum
# ---------------------------------------------------------------------------

class TestDatabaseType:
    def test_canonical_values(self):
        assert DatabaseType.LANGUAGE_SENTENCE.value == "language_sentence"
        assert DatabaseType.LANGUAGE_WORD_PHRASE.value == "language_word_phrase"
        assert DatabaseType.KNOWLEDGE.value == "knowledge"

    def test_category_property(self):
        assert DatabaseType.LANGUAGE_SENTENCE.category == DatabaseCategory.LANGUAGE_BASED
        assert DatabaseType.LANGUAGE_WORD_PHRASE.category == DatabaseCategory.LANGUAGE_BASED
        assert DatabaseType.KNOWLEDGE.category == DatabaseCategory.KNOWLEDGE_BASED

    def test_subtypes(self):
        assert DatabaseType.LANGUAGE_SENTENCE.display == "Sentence-based"
        assert DatabaseType.LANGUAGE_WORD_PHRASE.display == "Word/Phrase-based"
        assert DatabaseType.KNOWLEDGE.display == "Knowledge-based"


# ---------------------------------------------------------------------------
# DatabaseCategory enum
# ---------------------------------------------------------------------------

class TestDatabaseCategory:
    def test_values(self):
        assert DatabaseCategory.LANGUAGE_BASED.value == "language_based"
        assert DatabaseCategory.KNOWLEDGE_BASED.value == "knowledge_based"


# ---------------------------------------------------------------------------
# infer_database_type
# ---------------------------------------------------------------------------

class TestInferDatabaseType:
    """Test metadata inference from database paths."""

    def test_language_sentence_path(self):
        path = os.path.join(DB_DIR_LANGUAGE_SENTENCE, "French_barsky.db")
        result = infer_database_type(path)
        assert result == DatabaseType.LANGUAGE_SENTENCE

    def test_language_word_phrase_path(self):
        path = os.path.join(DB_DIR_LANGUAGE_WORD_PHRASE, "English_barsky.db")
        result = infer_database_type(path)
        assert result == DatabaseType.LANGUAGE_WORD_PHRASE

    def test_knowledge_path(self):
        path = os.path.join(DB_DIR_KNOWLEDGE, "Real_Analysis_barsky.db")
        result = infer_database_type(path)
        assert result == DatabaseType.KNOWLEDGE

    def test_legacy_languages_defaults_to_word_phrase(self):
        path = os.path.join(
            os.path.dirname(DB_DIR_LANGUAGE_SENTENCE),
            "Languages",  # legacy db/Languages
            "English_barsky.db",
        )
        result = infer_database_type(path)
        assert result == DatabaseType.LANGUAGE_WORD_PHRASE

    def test_legacy_math_defaults_to_knowledge(self):
        path = "/some/path/db/Math/Topology_barsky.db"
        result = infer_database_type(path)
        assert result == DatabaseType.KNOWLEDGE

    def test_unknown_defaults_to_knowledge(self):
        path = "/some/path/db/Stuff/Whatever_barsky.db"
        result = infer_database_type(path)
        assert result == DatabaseType.KNOWLEDGE


# ---------------------------------------------------------------------------
# read_database_type / write_database_type
# ---------------------------------------------------------------------------

class TestDatabaseTypePersistence:
    """Test that database_type is stored and retrieved from settings table."""

    @pytest.fixture
    def conn(self):
        db_fd, db_path = tempfile.mkstemp(suffix=".db")
        os.close(db_fd)
        conn = sqlite3.connect(db_path)
        conn.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)")
        yield conn
        conn.close()
        os.unlink(db_path)

    def test_write_and_read(self, conn):
        write_database_type(conn, DatabaseType.LANGUAGE_SENTENCE)
        result = read_database_type(conn)
        assert result == DatabaseType.LANGUAGE_SENTENCE

    def test_missing_key_returns_none(self, conn):
        result = read_database_type(conn)
        assert result is None

    def test_invalid_value_returns_none(self, conn):
        conn.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
            ("database_type", "bogus_type"),
        )
        conn.commit()
        result = read_database_type(conn)
        assert result is None

    def test_overwrite(self, conn):
        write_database_type(conn, DatabaseType.LANGUAGE_WORD_PHRASE)
        write_database_type(conn, DatabaseType.KNOWLEDGE)
        result = read_database_type(conn)
        assert result == DatabaseType.KNOWLEDGE


# ---------------------------------------------------------------------------
# build_catalog_tree
# ---------------------------------------------------------------------------

class TestBuildCatalogTree:
    """Test the catalog tree structure used for database menus."""

    def test_empty_list(self):
        tree = build_catalog_tree([])
        assert tree == {
            "Language-based": {
                "Sentence-based": {},
                "Word/Phrase-based": {},
            },
            "Knowledge-based": {},
        }

    def test_word_phrase_databases_are_flat(self):
        entries = [
            ("Language-based/Word/Phrase-based/Languages/English",
             "/old/db/Languages/English_barsky.db",
             DatabaseType.LANGUAGE_WORD_PHRASE),
        ]
        tree = build_catalog_tree(entries)
        branch = tree["Language-based"]["Word/Phrase-based"]
        assert branch == {
            "English": ("/old/db/Languages/English_barsky.db",
                        DatabaseType.LANGUAGE_WORD_PHRASE)
        }

    def test_single_language_sentence(self):
        entry = ("Language-based/Sentence-based/French", "/path/French_barsky.db",
                 DatabaseType.LANGUAGE_SENTENCE)
        tree = build_catalog_tree([entry])
        assert "Language-based" in tree
        lang_branch = tree["Language-based"]
        assert "Sentence-based" in lang_branch
        sent_branch = lang_branch["Sentence-based"]
        assert "French" in sent_branch
        assert sent_branch["French"] == ("/path/French_barsky.db",
                                         DatabaseType.LANGUAGE_SENTENCE)

    def test_multiple_categories(self):
        entries = [
            ("Language-based/Sentence-based/FR", "/p/fr.db",
             DatabaseType.LANGUAGE_SENTENCE),
            ("Language-based/Word-Phrase-based/EN", "/p/en.db",
             DatabaseType.LANGUAGE_WORD_PHRASE),
            ("Knowledge-based/Math", "/p/math.db",
             DatabaseType.KNOWLEDGE),
        ]
        tree = build_catalog_tree(entries)
        assert set(tree.keys()) == {"Language-based", "Knowledge-based"}

        lb = tree["Language-based"]
        assert set(lb.keys()) == {"Sentence-based", "Word/Phrase-based"}

        kb = tree["Knowledge-based"]
        assert "Math" in kb

    def test_sorts_categories_language_first(self):
        entries = [
            ("Knowledge-based/X", "/p/x.db", DatabaseType.KNOWLEDGE),
            ("Language-based/Word-Phrase-based/Y", "/p/y.db",
             DatabaseType.LANGUAGE_WORD_PHRASE),
        ]
        tree = build_catalog_tree(entries)
        keys = list(tree.keys())
        assert keys[0] == "Language-based"
        assert keys[1] == "Knowledge-based"

    def test_legacy_paths_placed_in_correct_category(self):
        """Legacy paths like db/Languages/English should appear under
        Language-based/Word-Phrase-based."""
        entries = [
            # This would come from infer_database_type on a legacy path
            ("Language-based/Word-Phrase-based/Languages/English",
             "/old/db/Languages/English_barsky.db",
             DatabaseType.LANGUAGE_WORD_PHRASE),
        ]
        tree = build_catalog_tree(entries)
        assert "Language-based" in tree
        assert "Word/Phrase-based" in tree["Language-based"]
        wpb = tree["Language-based"]["Word/Phrase-based"]
        assert wpb["English"] == (
            "/old/db/Languages/English_barsky.db",
            DatabaseType.LANGUAGE_WORD_PHRASE,
        )
