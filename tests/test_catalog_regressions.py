"""Regression tests for catalog paths and database-name validation."""

import os


from kgb_srs.catalog import (
    DB_DIR_KNOWLEDGE,
    DB_DIR_LANGUAGE_SENTENCE,
    DatabaseType,
    infer_database_type,
)
from .qt_helpers import qt_app as _qt_app


class TestCatalogPathNoDuplicate:
    """Catalog path must NOT produce Knowledge-based/Knowledge-based/..."""

    def test_canonical_knowledge_path(self):
        """Knowledge-based DB in canonical dir must NOT double-label."""
        path = os.path.join(DB_DIR_KNOWLEDGE, "Math", "Topology_barsky.db")
        db_type = infer_database_type(path)
        from kgb_srs.catalog import display_path_for

        display = display_path_for(path, db_type)
        parts = display.replace("\\", "/").split("/")
        # Must be: Knowledge-based/Math/Topology
        assert parts[0] == "Knowledge-based"
        assert parts[1] != "Knowledge-based"  # no duplicate!
        assert "Math" in parts
        assert "Topology" in parts

    def test_nested_canonical_english_path(self):
        """Canonical nested path preserved: Language-based/Sentence-based/FR/A1."""
        path = os.path.join(DB_DIR_LANGUAGE_SENTENCE, "FR", "A1_barsky.db")
        db_type = DatabaseType.LANGUAGE_SENTENCE
        from kgb_srs.catalog import display_path_for

        display = display_path_for(path, db_type)
        parts = display.replace("\\", "/").split("/")
        assert parts == ["Language-based", "Sentence-based", "FR", "A1"]

    def test_legacy_language_path(self):
        """Legacy Language path: Language-based/Word-Phrase-based/Languages/English."""
        path = os.path.join("db", "Languages", "English_barsky.db")
        db_type = DatabaseType.LANGUAGE_WORD_PHRASE
        from kgb_srs.catalog import display_path_for

        display = display_path_for(path, db_type)
        # Legacy detection should work
        assert "Languages" in display
        assert "English" in display

    def test_substring_detection_not_used(self):
        """Detection must use path components, not substring matching.
        A DB named 'Language-based_french' in a different dir should not
        trigger Language-based detection."""
        path = os.path.join("db", "Whatever", "Language-based_french_barsky.db")
        db_type = infer_database_type(path)
        # Should NOT be LANGUAGE_SENTENCE just because name contains 'Language-based'
        assert db_type == DatabaseType.KNOWLEDGE


class TestDBNameValidation:
    """Database names must be validated as safe path components."""

    def _validate_db_name(self, name):
        """Call the validation function (to be implemented)."""
        from kgb_srs.schema import validate_db_name

        return validate_db_name(name)

    def test_slash_rejected(self):
        assert self._validate_db_name("foo/bar") is False

    def test_backslash_rejected(self):
        assert self._validate_db_name("foo\\bar") is False

    def test_dotdot_rejected(self):
        assert self._validate_db_name("..") is False
        assert self._validate_db_name("../etc") is False
        assert self._validate_db_name("foo/../bar") is False

    def test_absolute_path_rejected(self):
        assert self._validate_db_name("/etc/passwd") is False

    def test_null_rejected(self):
        assert self._validate_db_name("foo\0bar") is False

    def test_valid_unicode_name_accepted(self):
        assert self._validate_db_name("Français") is True
        assert self._validate_db_name("中文数据库") is True
        assert self._validate_db_name("Real_Analysis") is True

    def test_empty_rejected(self):
        assert self._validate_db_name("") is False
        assert self._validate_db_name("   ") is False


class TestCatalogDisplayPathRegressions:
    def test_canonical_absolute_path_is_relative_in_menu(self):
        _qt_app()
        from kgb_srs.catalog import display_path_for, DB_DIR_LANGUAGE_SENTENCE
        from kgb_srs.config import DIR_DB

        path = os.path.join(DIR_DB, DB_DIR_LANGUAGE_SENTENCE, "French_barsky.db")
        assert display_path_for(path, DatabaseType.LANGUAGE_SENTENCE) == (
            "Language-based/Sentence-based/French"
        )

    def test_legacy_knowledge_display_path_has_single_category(self):
        _qt_app()
        from kgb_srs.main_window import _compute_display_path

        result = _compute_display_path(
            "/tmp/db/Math/Real/Real_barsky.db",
            DatabaseType.KNOWLEDGE,
            "Math/Real/Real",
        )
        assert result.replace("\\", "/") == "Knowledge-based/Math/Real/Real"
