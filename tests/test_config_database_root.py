"""Tests for database root resolution and structure helpers."""

import os
import tempfile

from kgb_srs.config import (
    CANONICAL_DB_SUBDIRS,
    DIR_DB,
    ensure_database_root_structure,
    get_database_root,
    is_path_under_root,
    relative_db_path,
    resolve_default_database,
    normalize_default_database,
)
from kgb_srs.schema import find_databases, DB_SUFFIX


class TestGetDatabaseRoot:
    def test_empty_settings_falls_back_to_project_db(self):
        assert get_database_root({}) == DIR_DB
        assert get_database_root({"database_root": ""}) == DIR_DB
        assert get_database_root({"database_root": "   "}) == DIR_DB

    def test_custom_root_is_expanded_and_absolutized(self, tmp_path):
        custom = tmp_path / "my_dbs"
        result = get_database_root({"database_root": str(custom)})
        assert result == os.path.abspath(str(custom))

    def test_tilde_is_expanded(self, monkeypatch, tmp_path):
        home = tmp_path / "home"
        home.mkdir()
        monkeypatch.setenv("HOME", str(home))
        result = get_database_root({"database_root": "~/cards"})
        assert result == os.path.join(str(home), "cards")


class TestEnsureDatabaseRootStructure:
    def test_creates_language_and_knowledge_subdirs(self, tmp_path):
        root = tmp_path / "root"
        result = ensure_database_root_structure(str(root))
        assert result == os.path.abspath(str(root))
        assert root.is_dir()
        assert (root / "Language-based" / "Sentence-based").is_dir()
        assert (root / "Language-based" / "Word-Phrase-based").is_dir()
        assert (root / "Knowledge-based").is_dir()
        for subdir in CANONICAL_DB_SUBDIRS:
            assert os.path.isdir(os.path.join(root, subdir))

    def test_is_idempotent_and_preserves_legacy(self, tmp_path):
        root = tmp_path / "root"
        legacy = root / "Languages"
        legacy.mkdir(parents=True)
        marker = legacy / "English_barsky.db"
        marker.write_text("x")

        ensure_database_root_structure(str(root))
        ensure_database_root_structure(str(root))

        assert marker.is_file()
        assert (root / "Language-based" / "Sentence-based").is_dir()


class TestFindDatabasesUsesRoot:
    def test_find_databases_respects_base_dir(self, tmp_path):
        root = tmp_path / "dbs"
        sent = root / "Language-based" / "Sentence-based"
        sent.mkdir(parents=True)
        db_path = sent / f"French{DB_SUFFIX}"
        db_path.write_bytes(b"")

        results = find_databases(str(root))
        assert len(results) == 1
        display, path = results[0]
        assert display.endswith("French")
        assert path == str(db_path)


class TestPathUnderRoot:
    def test_path_equals_root(self, tmp_path):
        root = str(tmp_path)
        assert is_path_under_root(root, root) is True

    def test_path_under_root(self, tmp_path):
        root = str(tmp_path)
        child = str(tmp_path / "Language-based" / "a_barsky.db")
        assert is_path_under_root(child, root) is True

    def test_path_outside_root(self, tmp_path):
        root = str(tmp_path / "dbs")
        outside = str(tmp_path / "other" / "a_barsky.db")
        assert is_path_under_root(outside, root) is False

    def test_empty_path_or_root(self, tmp_path):
        assert is_path_under_root("", str(tmp_path)) is False
        assert is_path_under_root(str(tmp_path), "") is False


class TestRelativeDbPath:
    def test_under_root_returns_relative(self, tmp_path):
        root = str(tmp_path)
        abs_path = str(tmp_path / "Language-based" / "English_barsky.db")
        rel = relative_db_path(abs_path, root)
        assert rel == os.path.normpath(
            os.path.join("Language-based", "English_barsky.db")
        )

    def test_outside_root_returns_none(self, tmp_path):
        root = str(tmp_path / "dbs")
        outside = str(tmp_path / "other" / "x_barsky.db")
        assert relative_db_path(outside, root) is None

    def test_path_equals_root_returns_dot(self, tmp_path):
        root = str(tmp_path)
        assert relative_db_path(root, root) == os.path.normpath(".")


class TestResolveDefaultDatabase:
    def test_empty(self, tmp_path):
        assert resolve_default_database({}) == ""
        assert resolve_default_database({"default_database": ""}) == ""
        assert resolve_default_database({"default_database": "  "}) == ""

    def test_relative_joins_root(self, tmp_path):
        settings = {
            "database_root": str(tmp_path),
            "default_database": os.path.join(
                "Language-based", "English_barsky.db"
            ),
        }
        expected = os.path.normpath(
            os.path.join(
                str(tmp_path), "Language-based", "English_barsky.db"
            )
        )
        assert resolve_default_database(settings) == expected

    def test_absolute_under_root(self, tmp_path):
        abs_db = str(tmp_path / "Language-based" / "English_barsky.db")
        settings = {
            "database_root": str(tmp_path),
            "default_database": abs_db,
        }
        assert resolve_default_database(settings) == os.path.abspath(abs_db)

    def test_absolute_outside_root(self, tmp_path):
        settings = {
            "database_root": str(tmp_path / "dbs"),
            "default_database": "/tmp/outside_barsky.db",
        }
        assert resolve_default_database(settings) == ""

    def test_relative_escaping_root(self, tmp_path):
        settings = {
            "database_root": str(tmp_path / "dbs"),
            "default_database": "../escape_barsky.db",
        }
        assert resolve_default_database(settings) == ""

    def test_relative_with_empty_root_uses_dir_db(self):
        settings = {
            "database_root": "",
            "default_database": "English_barsky.db",
        }
        assert resolve_default_database(settings) == os.path.normpath(
            os.path.join(DIR_DB, "English_barsky.db")
        )


class TestNormalizeDefaultDatabase:
    def test_empty(self, tmp_path):
        assert normalize_default_database("", str(tmp_path)) == ""
        assert normalize_default_database("  ", str(tmp_path)) == ""

    def test_absolute_under_root_becomes_relative(self, tmp_path):
        abs_db = str(tmp_path / "Language-based" / "English_barsky.db")
        rel = normalize_default_database(abs_db, str(tmp_path))
        assert rel == os.path.normpath(
            os.path.join("Language-based", "English_barsky.db")
        )
        assert not os.path.isabs(rel)

    def test_absolute_outside_becomes_empty(self, tmp_path):
        assert (
            normalize_default_database("/tmp/outside.db", str(tmp_path))
            == ""
        )

    def test_relative_under_root_normalized(self, tmp_path):
        value = os.path.join("Language-based", "English_barsky.db")
        assert normalize_default_database(value, str(tmp_path)) == (
            os.path.normpath(value)
        )

    def test_relative_escaping_root_becomes_empty(self, tmp_path):
        assert normalize_default_database("../escape.db", str(tmp_path)) == ""
