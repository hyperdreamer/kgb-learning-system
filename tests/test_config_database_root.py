"""Tests for database root resolution and structure helpers."""

import os
import tempfile

from kgb_srs.config import (
    CANONICAL_DB_SUBDIRS,
    DIR_DB,
    ensure_database_root_structure,
    get_database_root,
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
