"""Tests for database root resolution and structure helpers."""

import os
import json

import pytest

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


def test_load_settings_ignores_invalid_scalar_values_and_starts_app(
    tmp_path, monkeypatch
):
    """Malformed scalar settings retain defaults instead of breaking startup."""
    import kgb_srs.config as config

    settings_path = tmp_path / "barsky_settings.json"
    settings_path.write_text(
        json.dumps(
            {
                "width": None,
                "height": 0,
                "sentence_dialog_width": "wide",
                "sentence_dialog_height": -20,
                "font_size": True,
                "content_font_size": 0,
                "database_root": 42,
                "default_database": 99,
                "font_family": ["Arial"],
                "content_font_family": None,
                "tts_voice": 123,
                "tts_language": "fr-FR",
                "explanation_language": "French",
                "ai_active_provider": ["Default"],
                "ai_providers": [],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(config, "SETTINGS_FILE", str(settings_path))

    loaded = config.load_settings()
    for key in config.DEFAULT_SETTINGS:
        if key in {"tts_language", "explanation_language"}:
            continue
        assert loaded[key] == config.DEFAULT_SETTINGS[key]
    assert loaded["tts_language"] == "fr-FR"
    assert loaded["explanation_language"] == "French"

    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PyQt6")
    from PyQt6.QtWidgets import QApplication
    import kgb_srs.main_window as main_window

    monkeypatch.setattr(main_window, "HAS_WEBENGINE", False)
    app = QApplication.instance() or QApplication([])
    window = main_window.BarskyApp()
    try:
        assert window.width() == config.DEFAULT_SETTINGS["width"]
        assert window.height() == config.DEFAULT_SETTINGS["height"]
    finally:
        window.close()
        app.processEvents()


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

    def test_symlink_to_outside_root_is_not_contained(self, tmp_path):
        root = tmp_path / "root"
        outside = tmp_path / "outside"
        root.mkdir()
        outside.mkdir()
        outside_link = root / "outside-link"
        try:
            outside_link.symlink_to(outside, target_is_directory=True)
        except (NotImplementedError, OSError) as exc:
            pytest.skip(f"symlinks unavailable: {exc}")

        escaped = outside_link / "escaped_barsky.db"
        assert is_path_under_root(str(escaped), str(root)) is False
        assert relative_db_path(str(escaped), str(root)) is None
        assert resolve_default_database({
            "database_root": str(root),
            "default_database": str(escaped),
        }) == ""
        assert resolve_default_database({
            "database_root": str(root),
            "default_database": os.path.join(
                "outside-link", "escaped_barsky.db"
            ),
        }) == ""
        assert normalize_default_database(str(escaped), str(root)) == ""
        assert normalize_default_database(
            os.path.join("outside-link", "escaped_barsky.db"), str(root)
        ) == ""

        normal = root / "normal" / "inside_barsky.db"
        assert is_path_under_root(str(normal), str(root)) is True
        assert relative_db_path(str(normal), str(root)) == os.path.join(
            "normal", "inside_barsky.db"
        )
        assert resolve_default_database({
            "database_root": str(root),
            "default_database": os.path.join("normal", "inside_barsky.db"),
        }) == str(normal)
        assert normalize_default_database(str(normal), str(root)) == os.path.join(
            "normal", "inside_barsky.db"
        )


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
            "default_database": str(tmp_path / "outside_barsky.db"),
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
            normalize_default_database(
                str(tmp_path.parent / "outside.db"), str(tmp_path)
            )
            == ""
        )

    def test_relative_under_root_normalized(self, tmp_path):
        value = os.path.join("Language-based", "English_barsky.db")
        assert normalize_default_database(value, str(tmp_path)) == (
            os.path.normpath(value)
        )

    def test_relative_escaping_root_becomes_empty(self, tmp_path):
        assert normalize_default_database("../escape.db", str(tmp_path)) == ""
