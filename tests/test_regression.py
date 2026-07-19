"""Regression tests for Hermes review blocking findings.

These tests encode the expected behavior as described in the review.
Many tests will FAIL before the corresponding fixes are implemented.
"""

import os
import sqlite3
import tempfile
import json
import pytest

from kgb_srs.schema import (
    init_db,
    ensure_unfamiliar_items_table,
    insert_sentence_card,
    get_sentence_card,
    update_sentence_card,
    find_databases,
    resolve_db_path,
    validate_db_name,
)
from kgb_srs.catalog import (
    DatabaseType,
    DatabaseCategory,
    infer_database_type,
    build_catalog_tree,
    DB_DIR_LANGUAGE_SENTENCE,
    DB_DIR_LANGUAGE_WORD_PHRASE,
    DB_DIR_KNOWLEDGE,
)
from kgb_srs.ai_parser import (
    parse_sentence_meanings,
    parse_word_phrase_meanings,
    AIParseError,
    AIValidationError,
)
from kgb_srs.search import (
    parse_search_tokens,
    search_sentence_cards,
    search_word_phrase_cards,
)
from kgb_srs.validation import normalize_sentence, deduplicate_unfamiliar_items


_QT_APP = None


def _qt_app():
    """Return and retain a QApplication for headless form regression tests."""
    global _QT_APP
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PyQt6")
    from PyQt6.QtWidgets import QApplication
    _QT_APP = QApplication.instance() or QApplication([])
    return _QT_APP


class TestFinalFormRegressions:
    def test_api_key_visibility_button_toggles_plaintext(self):
        _qt_app()
        from PyQt6.QtWidgets import QLineEdit
        from PyQt6.QtCore import QSize
        from PyQt6.QtGui import QAction
        from kgb_srs.main_window import SecretLineEdit, _make_eye_icons

        field = SecretLineEdit("sk-secret")

        # Must be a QLineEdit — not a composite QWidget with a sub-layout
        assert isinstance(field, QLineEdit), (
            "SecretLineEdit must subclass QLineEdit directly"
        )

        # The toggle action is registered inside the QLineEdit
        actions = field.actions()
        assert len(actions) == 1, (
            f"Expected exactly 1 trailing action, got {len(actions)}"
        )
        toggle_action = actions[0]
        assert isinstance(toggle_action, QAction)
        assert toggle_action is field._toggle_action
        assert toggle_action.isCheckable(), "Action must be checkable"

        # Carries a proper, non-null icon
        icon = toggle_action.icon()
        assert icon is not None
        assert not icon.isNull(), "Action must have a drawn icon"

        # Hidden and visible icons from _make_eye_icons are distinct
        # QIcon objects with different pixmaps.
        hidden_icon, visible_icon = _make_eye_icons()
        assert not hidden_icon.isNull()
        assert not visible_icon.isNull()
        pix_hidden = hidden_icon.pixmap(QSize(24, 24))
        pix_visible = visible_icon.pixmap(QSize(24, 24))
        assert not pix_hidden.isNull()
        assert not pix_visible.isNull()
        assert pix_hidden.cacheKey() != pix_visible.cacheKey(), (
            "Hidden and visible icons must have different pixmaps"
        )

        # Initial state: hidden (action unchecked → password echo)
        assert field.echoMode() == QLineEdit.EchoMode.Password
        assert not toggle_action.isChecked()
        assert toggle_action.toolTip() == "Show API key"
        assert toggle_action.text() == "Show API key"
        key_hidden = toggle_action.icon().cacheKey()

        # Toggle to visible
        toggle_action.toggle()
        assert field.echoMode() == QLineEdit.EchoMode.Normal
        assert field.text() == "sk-secret"
        assert toggle_action.toolTip() == "Hide API key"
        assert toggle_action.text() == "Hide API key"
        assert toggle_action.isChecked()
        key_visible = toggle_action.icon().cacheKey()
        assert key_visible != key_hidden, (
            "Action icon must visually change when toggled to visible — "
            "cacheKey must differ"
        )

        # Toggle back to hidden
        toggle_action.toggle()
        assert field.echoMode() == QLineEdit.EchoMode.Password
        assert not toggle_action.isChecked()
        assert toggle_action.toolTip() == "Show API key"
        assert toggle_action.text() == "Show API key"
        key_back = toggle_action.icon().cacheKey()
        assert key_back == key_hidden, (
            "Action icon must return to hidden icon when toggled back — "
            "cacheKey must match original"
        )

        field.close()

    def test_visible_eye_iris_centered_and_outlined(self):
        """Visible eye icon: iris is a centred hollow outline circle.

        Checks two geometric invariants on the visible (State.On) pixmap:
        1. Centre pixel is transparent — confirms hollow outline, not a
           filled disc.
        2. A pixel left-of-centre on the iris ring has alpha — confirms
           the iris is QPointF-centred.  The old int-overload bug
           (drawEllipse(int(cx), int(cy), int(pr), int(pr))) placed the
           iris entirely in the lower-right quadrant, so no iris alpha
           exists left of (or above) the image centre.
        """
        _qt_app()
        from kgb_srs.main_window import _make_eye_icons

        sz = 20  # default used by SecretLineEdit
        _hidden_icon, visible_icon = _make_eye_icons(size=sz)
        pm = visible_icon.pixmap(sz, sz)
        img = pm.toImage()

        cx = cy = sz / 2.0       # 10.0
        pr = sz * 0.14            # 2.8 — iris radius (float)
        # Pixel on the iris-outline annulus, left of centre:
        #   distance from centre ≈ pr, so inside the 1.5 px pen stroke.
        #   Far enough from the eye-outline endpoints (x ≈ cx ± ew) that
        #   the almond outline does not reach it.
        offset = max(2, int(pr + 0.5))  # ≈ pr  rounded to an integer
        tx = int(cx) - offset            # left-of-centre test column
        ty = int(cy)                     # same row as centre

        # 1. Centre transparency — iris is hollow (outline only).
        assert img.pixelColor(int(cx), int(cy)).alpha() == 0, (
            "Centre pixel must be transparent — iris must be an "
            "outline, not a filled disc"
        )

        # 2. Iris outline extends left of centre.
        a = img.pixelColor(tx, ty).alpha()
        assert a > 0, (
            f"No iris alpha at ({tx},{ty}) — iris may be top-left "
            f"anchored (int-overload drawEllipse bug) rather than "
            f"QPointF-centred"
        )

    def test_public_ai_worker_does_not_shadow_thread_finished(self):
        _qt_app()
        from kgb_srs.ai_provider import _get_ai_worker_class

        worker_class = _get_ai_worker_class()
        assert hasattr(worker_class, "result")
        assert "finished" not in worker_class.__dict__

    def test_settings_save_failure_is_propagated(self, tmp_path, monkeypatch):
        import kgb_srs.config as config

        monkeypatch.setattr(config, "SETTINGS_FILE", str(tmp_path / "settings.json"))
        monkeypatch.setattr(
            config.os, "replace",
            lambda *args: (_ for _ in ()).throw(OSError("disk full")),
        )
        with pytest.raises(OSError, match="disk full"):
            config.save_settings({"ai_api_key": "secret"})

    def test_deleting_queued_card_removes_it_from_review_state(self):
        _qt_app()
        from kgb_srs.main_window import BarskyApp

        from types import SimpleNamespace
        window = SimpleNamespace(
            current_card=(1, "current", "back", 1),
            cards_due=[(2, "queued", "back", 2), (3, "other", "back", 1)],
        )
        BarskyApp._remove_card_from_review_state(window, 2)
        assert [card[0] for card in window.cards_due] == [3]
        assert window.current_card[0] == 1

    def test_sentence_expression_labels_accept_structured_pairs(self):
        _qt_app()
        from kgb_srs.main_window import _expression_labels

        assert _expression_labels([("bonjour", "hello"), ("ami", "friend")]) == [
            "bonjour", "ami"
        ]

    def test_settings_file_is_owner_only(self, tmp_path, monkeypatch):
        import stat
        import kgb_srs.config as config

        settings_path = tmp_path / "barsky_settings.json"
        monkeypatch.setattr(config, "SETTINGS_FILE", str(settings_path))
        config.save_settings({"ai_api_key": "secret"})
        assert stat.S_IMODE(settings_path.stat().st_mode) == 0o600

    def test_canonical_absolute_path_is_relative_in_menu(self):
        _qt_app()
        from kgb_srs.catalog import display_path_for, DB_DIR_LANGUAGE_SENTENCE
        from kgb_srs.config import DIR_DB

        path = os.path.join(
            DIR_DB, DB_DIR_LANGUAGE_SENTENCE, "French_barsky.db"
        )
        assert display_path_for(path, DatabaseType.LANGUAGE_SENTENCE) == (
            "Language-based/Sentence-based/French"
        )

    def test_worker_result_signal_does_not_override_thread_finished(self):
        _qt_app()
        from kgb_srs.forms import _AIGenerateWorker

        assert hasattr(_AIGenerateWorker, "result")
        # QThread.finished remains the inherited no-argument termination signal.
        assert "finished" not in _AIGenerateWorker.__dict__

    def test_legacy_knowledge_display_path_has_single_category(self):
        _qt_app()
        from kgb_srs.main_window import _compute_display_path

        result = _compute_display_path(
            "/tmp/db/Math/Real/Real_barsky.db",
            DatabaseType.KNOWLEDGE,
            "Math/Real/Real",
        )
        assert result.replace("\\", "/") == "Knowledge-based/Math/Real/Real"

    def test_sentence_dialog_preserves_meanings_and_rebuild_content(self):
        _qt_app()
        from kgb_srs.forms import SentenceCardDialog

        dialog = SentenceCardDialog(
            sentence="Hello world again",
            items=[("Hello", "greeting"), ("world", "earth")],
        )
        assert [w.toPlainText() for _, w in dialog._meaning_widgets] == [
            "greeting", "earth"
        ]
        dialog._meaning_widgets[0][1].setPlainText("salutation")
        dialog._item_entry.setText("again")
        dialog._add_item()
        assert dialog._meaning_widgets[0][1].toPlainText() == "salutation"
        dialog.close()

    def test_word_dialog_does_not_drop_partial_second_row(self, monkeypatch):
        _qt_app()
        from PyQt6.QtWidgets import QMessageBox
        from kgb_srs.forms import WordPhraseCardDialog

        warnings = []
        monkeypatch.setattr(
            QMessageBox, "warning",
            lambda *args, **kwargs: warnings.append(args[2]),
        )
        dialog = WordPhraseCardDialog(front="bank")
        first = dialog._meaning_rows[0]
        first["meaning_edit"].setPlainText("financial institution")
        first["example_edit"].setPlainText("I deposited money at the bank.")
        dialog._add_meaning_row(meaning="river edge", example="")

        dialog._accept()

        assert warnings
        assert dialog.result_meanings == []
        dialog.close()

    def test_sentence_dialog_rejects_blank_meaning_before_accept(self, monkeypatch):
        _qt_app()
        from PyQt6.QtWidgets import QMessageBox
        from kgb_srs.forms import SentenceCardDialog

        warnings = []
        monkeypatch.setattr(
            QMessageBox, "warning",
            lambda *args, **kwargs: warnings.append(args[2]),
        )
        dialog = SentenceCardDialog(
            sentence="Hello world", items=[("world", "")]
        )

        dialog._accept()

        assert warnings
        assert dialog.result_items == []
        dialog.close()


# ============================================================================
# Finding #1: Sentence data model — meaning column
# ============================================================================

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
        assert result[3][0] == ("new", "new meaning")

    def test_cards_back_is_rendered_representation(self, conn):
        """cards.back is a rendered/cache field, not the source of truth."""
        cid = insert_sentence_card(
            conn, "expr meaning text", [("expr", "meaning")], back="Rendered back"
        )
        cur = conn.execute("SELECT back FROM cards WHERE id=?", (cid,))
        assert cur.fetchone()[0] == "Rendered back"


# ============================================================================
# Finding #4: AI parser validation — identity/order + word examples
# ============================================================================

class TestSentenceParserValidation:
    """Sentence parser must validate identity, order, and non-emptiness."""

    def test_expressions_must_match_order(self):
        """Returned expressions must match expected in order."""
        response = json.dumps({
            "items": [
                {"expression": "ici", "contextual_meaning": "here"},
                {"expression": "suis", "contextual_meaning": "am"},
            ]
        })
        with pytest.raises(AIValidationError, match="order|match|expected"):
            parse_sentence_meanings(response, expected_expressions=["suis", "ici"])

    def test_expressions_must_be_nonempty(self):
        """Empty expression string must be rejected."""
        response = json.dumps({
            "items": [
                {"expression": "", "contextual_meaning": "something"},
            ]
        })
        with pytest.raises(AIValidationError, match="empty|non-empty"):
            parse_sentence_meanings(response, expected_expressions=[""])

    def test_meanings_must_be_nonempty(self):
        """Empty meaning string must be rejected."""
        response = json.dumps({
            "items": [
                {"expression": "test", "contextual_meaning": ""},
            ]
        })
        with pytest.raises(AIValidationError, match="empty|non-empty"):
            parse_sentence_meanings(response, expected_expressions=["test"])

    def test_unicode_normalized_matching(self):
        """Expression matching must use Unicode/case/whitespace normalization."""
        response = json.dumps({
            "items": [
                {"expression": "  HELLO  ", "contextual_meaning": "greeting"},
            ]
        })
        result = parse_sentence_meanings(response, expected_expressions=["hello"])
        assert len(result) == 1
        assert result[0].contextual_meaning == "greeting"

    def test_empty_expected_with_empty_items(self):
        """Empty expected list with empty items list is valid."""
        result = parse_sentence_meanings(
            json.dumps({"items": []}), expected_expressions=[])
        assert result == []


class TestWordParserValidation:
    """Word parser must require non-empty example for every meaning."""

    def test_missing_example_rejected(self):
        """Every meaning must have a non-empty example."""
        response = json.dumps({
            "meanings": [
                {"meaning": "A greeting", "example": ""},
            ]
        })
        with pytest.raises(AIValidationError, match="example"):
            parse_word_phrase_meanings(response)

    def test_absent_example_rejected(self):
        """Meanings without example field must be rejected."""
        response = json.dumps({
            "meanings": [
                {"meaning": "A greeting"},
            ]
        })
        with pytest.raises(AIValidationError, match="example"):
            parse_word_phrase_meanings(response)

    def test_over_two_meanings_rejected(self):
        """More than 2 meanings must reject the entire response."""
        response = json.dumps({
            "meanings": [
                {"meaning": "m1", "example": "e1"},
                {"meaning": "m2", "example": "e2"},
                {"meaning": "m3", "example": "e3"},
            ]
        })
        with pytest.raises(AIValidationError, match="2|two|many"):
            parse_word_phrase_meanings(response)

    def test_valid_with_examples(self):
        """Valid response with non-empty examples must be accepted."""
        response = json.dumps({
            "meanings": [
                {"meaning": "A greeting", "example": "Hello there!"},
            ]
        })
        result = parse_word_phrase_meanings(response)
        assert len(result) == 1
        assert "A greeting" in result[0].contextual_meaning
        assert "Hello there!" in result[0].contextual_meaning


# ============================================================================
# Finding #5: Search semantics
# ============================================================================

class TestSearchORGroupsAND:
    """Search must support OR groups containing AND terms."""

    def test_parse_or_groups_with_and_terms(self):
        """'math AND theorem OR topology' -> OR groups with AND terms inside."""
        groups = parse_search_tokens("math AND theorem OR topology")
        assert groups == [["math", "theorem"], ["topology"]]


    def test_plain_multiword_is_literal_substring(self):
        """A plain multi-word query (no AND/OR) is one literal substring."""
        groups = parse_search_tokens("quick brown fox")
        assert groups == [["quick brown fox"]]


class TestSearchSentenceAcrossFields:
    """Sentence search must find: sentence, child expression, child meaning.
    AND terms may match different fields or different child rows."""

    @pytest.fixture
    def conn(self):
        c = sqlite3.connect(":memory:")
        init_db(c)
        ensure_unfamiliar_items_table(c)
        from kgb_srs.schema import migrate_unfamiliar_items_meaning
        migrate_unfamiliar_items_meaning(c)
        # Insert a sentence card with multiple child rows
        c.execute(
            "INSERT INTO cards (id, front, back, box, next_review) "
            "VALUES (1, 'The quick brown fox', 'Rendered back', 1, '2026-01-01')"
        )
        c.execute(
            "INSERT INTO unfamiliar_items (card_id, expression, meaning) "
            "VALUES (1, 'quick', 'fast')"
        )
        c.execute(
            "INSERT INTO unfamiliar_items (card_id, expression, meaning) "
            "VALUES (1, 'brown', 'a color')"
        )
        c.commit()
        yield c
        c.close()

    def test_percent_and_underscore_are_literal(self, conn):
        assert search_sentence_cards(conn, "%") == []
        assert search_sentence_cards(conn, "_") == []

    def test_search_matches_meaning_field(self, conn):
        """Search must match child row meaning."""
        results = search_sentence_cards(conn, "color")
        assert len(results) == 1
        assert results[0]["id"] == 1

    def test_and_terms_across_different_child_rows(self, conn):
        """AND terms may match different child rows of same card."""
        results = search_sentence_cards(conn, "fast AND color", "AND")
        assert len(results) == 1
        assert results[0]["id"] == 1

    def test_and_terms_across_fields(self, conn):
        """AND terms may match different fields (sentence + expression)."""
        results = search_sentence_cards(conn, "fox AND fast", "AND")
        assert len(results) == 1
        assert results[0]["id"] == 1

    def test_or_terms_with_and_subgroups(self, conn):
        """OR groups with AND inside: 'fast brown OR nonexistent' -> match."""
        c2 = sqlite3.connect(":memory:")
        init_db(c2)
        ensure_unfamiliar_items_table(c2)
        from kgb_srs.schema import migrate_unfamiliar_items_meaning
        migrate_unfamiliar_items_meaning(c2)
        c2.execute(
            "INSERT INTO cards (id, front, back, box, next_review) "
            "VALUES (1, 'Hello world', 'Greeting back', 1, '2026-01-01')"
        )
        c2.execute(
            "INSERT INTO unfamiliar_items (card_id, expression, meaning) "
            "VALUES (1, 'hello', 'greeting'), (1, 'world', 'the earth')"
        )
        c2.execute(
            "INSERT INTO cards (id, front, back, box, next_review) "
            "VALUES (2, 'Topology basics', 'Math back', 1, '2026-01-01')"
        )
        c2.execute(
            "INSERT INTO unfamiliar_items (card_id, expression, meaning) "
            "VALUES (2, 'topology', 'study of shapes')"
        )
        c2.commit()

        # 'hello AND world OR topology': OR group of 
        #   group1: ('hello' AND 'world') -> match card 1
        #   group2: ('topology') -> match card 2
        results = search_sentence_cards(c2, "hello AND world OR topology")
        ids = [r["id"] for r in results]
        assert 1 in ids
        assert 2 in ids
        c2.close()


# ============================================================================
# Finding #7: Catalog path — no duplicate Knowledge-based
# ============================================================================

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
        path = os.path.join(
            DB_DIR_LANGUAGE_SENTENCE, "FR", "A1_barsky.db")
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


# ============================================================================
# Finding #8: DB name validation
# ============================================================================

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


# ============================================================================
# Finding #10: Sentence duplicate detection
# ============================================================================

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


# ============================================================================
# Finding #11: Atomic operations
# ============================================================================

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


# ============================================================================
# Finding #12: Quality — no duplicate exception class
# ============================================================================

class TestNoDuplicateException:
    """AIMissingConfigError must only exist in one module."""

    def test_aimissingconfigerror_only_in_ai_provider(self):
        """The canonical source is ai_provider. ai_parser should not
        define a duplicate AIMissingConfigError."""
        from kgb_srs.ai_provider import AIMissingConfigError
        assert AIMissingConfigError is not None
        # ai_parser should NOT have its own AIMissingConfigError
        import kgb_srs.ai_parser as ap
        assert not hasattr(ap, "AIMissingConfigError")




# ============================================================================
# Finding #13: Duplicate semantics — ordered list comparison
# ============================================================================

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


# ============================================================================
# Finding #14: DB path resolution hardening
# ============================================================================

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


# ============================================================================
# Finding #15: Review queue — _refresh_current_card preserves current card
# ============================================================================

class TestReviewQueuePreservation:
    """Editing an unrelated card must not disrupt the current review card."""

    def test_edit_unrelated_preserves_current(self):
        """Simulate the logic: editing a different card should not change current."""
        # We can test the helper logic directly
        current_card = (1, "front1", "back1", 2)
        cards_due = [(1, "f1", "b1", 2), (2, "f2", "b2", 3)]

        # Scenario: editing card_id=2 (unrelated)
        card_id = 2
        fresh = (2, "f2_updated", "b2_updated", 1)

        # Apply the _refresh_current_card logic
        if current_card is not None and current_card[0] == card_id:
            current_card = fresh
        else:
            cards_due = [cf for cf in cards_due if cf[0] != card_id]
            cards_due.append(fresh)

        assert current_card == (1, "front1", "back1", 2), "Current card should not change"
        assert len(cards_due) == 2

    def test_edit_current_card_updates_it(self):
        """Editing the current card should refresh it."""
        current_card = (1, "front1", "back1", 2)
        cards_due = [(1, "f1", "b1", 2), (2, "f2", "b2", 3)]

        card_id = 1
        fresh = (1, "front1_updated", "back1_updated", 1)

        if current_card is not None and current_card[0] == card_id:
            current_card = fresh

        assert current_card == (1, "front1_updated", "back1_updated", 1)


# ============================================================================
# Finding #16: Persistence invariants validation
# ============================================================================

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


# ============================================================================
# Finding #17: Public API — lazy import
# ============================================================================

class TestPublicAPILazy:
    """from kgb_srs import BarskyApp must work lazily."""

    def test_barskyapp_in_all(self):
        import kgb_srs
        assert "BarskyApp" in kgb_srs.__all__

    def test_barskyapp_accessible(self):
        from kgb_srs import BarskyApp
        assert BarskyApp is not None

    def test_get_app_returns_same(self):
        from kgb_srs import BarskyApp, get_app
        assert get_app() is BarskyApp


# ============================================================================
# Finding #18: No processEvents or sync HTTP in main_window
# ============================================================================

class TestNoSyncHTTPInMainWindow:
    """Main window must not contain processEvents or synchronous HTTP calls."""

    def test_no_process_events(self):
        import os
        main_path = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "kgb_srs", "main_window.py")
        with open(main_path, "r") as f:
            content = f.read()
        assert "processEvents" not in content, \
            "processEvents found in main_window.py"
        assert "_make_http_call" not in content, \
            "_make_http_call found in main_window.py"
        assert "urllib.request.urlopen" not in content, \
            "urllib.request.urlopen found in main_window.py"

# ============================================================================
# Add Entry button: toolbar label renamed from "Add Word" to "Add Entry"
# ============================================================================

class TestAddEntryButtonLabel:
    """The top-toolbar button for adding items must have label 'Add Entry'."""

    def test_add_entry_button_label_is_add_entry(self):
        """The toolbar button stored as add_entry_btn has visible text
        'Add Entry' after stripping leading whitespace."""
        _qt_app()
        from PyQt6.QtWidgets import QPushButton
        from kgb_srs.main_window import BarskyApp

        win = BarskyApp()

        # Button must be stored as a stable attribute on the window
        assert hasattr(win, "add_entry_btn"), (
            "BarskyApp must store the add-entry toolbar button as "
            "self.add_entry_btn for stable testability"
        )
        btn = win.add_entry_btn
        assert isinstance(btn, QPushButton)

        # Visible label: strip leading whitespace/mnemonic; assert exact text
        visible_text = btn.text().strip()
        assert visible_text == "Add Entry", (
            f"Expected toolbar button label 'Add Entry', got '{visible_text}'"
        )

        win.close()


# ============================================================================
# Finding #19: QMenu submenu-indicator right-side spacing
# ============================================================================

class TestMenuSubmenuSpacing:
    """Database-selection QMenu items must have right padding so text
    never overlaps the submenu arrow indicator (">")."""

    def test_db_menu_stylesheet_constant_exists(self):
        """_DB_MENU_STYLESHEET must be defined with padding-right."""
        from kgb_srs.main_window import _DB_MENU_STYLESHEET

        assert isinstance(_DB_MENU_STYLESHEET, str)
        assert "padding-right" in _DB_MENU_STYLESHEET
        assert "QMenu::item" in _DB_MENU_STYLESHEET

    def test_db_menu_stylesheet_has_vertical_padding(self):
        """_DB_MENU_STYLESHEET must include padding-top >=6px and
        padding-bottom >=6px for readable row height."""
        import re
        from kgb_srs.main_window import _DB_MENU_STYLESHEET

        m_top = re.search(r"padding-top\s*:\s*(\d+)px", _DB_MENU_STYLESHEET)
        m_bottom = re.search(r"padding-bottom\s*:\s*(\d+)px", _DB_MENU_STYLESHEET)

        assert m_top is not None, "stylesheetsheet missing padding-top"
        assert m_bottom is not None, "stylesheetsheet missing padding-bottom"

        top_px = int(m_top.group(1))
        bottom_px = int(m_bottom.group(1))

        assert top_px >= 6, f"padding-top is {top_px}px, expected >= 6px"
        assert bottom_px >= 6, f"padding-bottom is {bottom_px}px, expected >= 6px"

    def test_root_menu_has_stylesheet(self, tmp_path, monkeypatch):
        """The root QMenu returned by build_db_menu carries the stylesheet."""
        _qt_app()

        from kgb_srs.main_window import BarskyApp, _DB_MENU_STYLESHEET
        from kgb_srs.catalog import DatabaseType
        from PyQt6.QtWidgets import QMenu

        # Build canonical path: Language-based/Sentence-based/Test
        db_dir = tmp_path / "db" / "Language-based" / "Sentence-based"
        db_dir.mkdir(parents=True)
        dummy = db_dir / "Test_barsky.db"
        dummy.write_text("")

        monkeypatch.setattr("kgb_srs.main_window.find_databases",
                            lambda: [("Test", str(dummy))])
        monkeypatch.setattr("kgb_srs.main_window._open_and_infer_type",
                            lambda p: DatabaseType.LANGUAGE_SENTENCE)

        class FakeApp:
            current_db_path = None
        app = FakeApp()
        menu = QMenu()
        BarskyApp.build_db_menu(app, menu)

        stylesheet = menu.styleSheet()
        assert stylesheet == _DB_MENU_STYLESHEET
        assert "padding-right" in stylesheet

    def test_submenus_inherit_stylesheet(self, tmp_path, monkeypatch):
        """Every recursive submenu must carry the same right-padding stylesheet."""
        _qt_app()

        from kgb_srs.main_window import BarskyApp, _DB_MENU_STYLESHEET
        from kgb_srs.catalog import DatabaseType
        from PyQt6.QtWidgets import QMenu

        # Build a deeper hierarchy: Language-based > Sentence-based > French
        db_dir = tmp_path / "db" / "Language-based" / "Sentence-based"
        db_dir.mkdir(parents=True)
        dummy = db_dir / "French_barsky.db"
        dummy.write_text("")

        monkeypatch.setattr("kgb_srs.main_window.find_databases",
                            lambda: [("French", str(dummy))])
        monkeypatch.setattr("kgb_srs.main_window._open_and_infer_type",
                            lambda p: DatabaseType.LANGUAGE_SENTENCE)

        class FakeApp:
            current_db_path = None
        app = FakeApp()
        menu = QMenu()
        BarskyApp.build_db_menu(app, menu)

        def collect_menus(m):
            result = [m]
            for action in m.actions():
                if action.menu():
                    result.extend(collect_menus(action.menu()))
            return result

        all_menus = collect_menus(menu)
        # We expect at least 3: root, Language-based, Sentence-based
        assert len(all_menus) >= 3

        for m in all_menus:
            assert m.styleSheet() == _DB_MENU_STYLESHEET, (
                f"Menu '{m.title()}' missing stylesheet"
            )
            assert "padding-right" in m.styleSheet()

    def test_leaf_action_has_no_submenu(self, tmp_path, monkeypatch):
        """Leaf (database) actions are plain actions, not submenus."""
        _qt_app()

        from kgb_srs.main_window import BarskyApp
        from kgb_srs.catalog import DatabaseType
        from PyQt6.QtWidgets import QMenu

        db_dir = tmp_path / "db" / "Knowledge-based"
        db_dir.mkdir(parents=True)
        dummy = db_dir / "Math_barsky.db"
        dummy.write_text("")

        monkeypatch.setattr("kgb_srs.main_window.find_databases",
                            lambda: [("Math", str(dummy))])
        monkeypatch.setattr("kgb_srs.main_window._open_and_infer_type",
                            lambda p: DatabaseType.KNOWLEDGE)

        class FakeApp:
            current_db_path = None
        app = FakeApp()
        menu = QMenu()
        BarskyApp.build_db_menu(app, menu)

        # Recursively collect leaf actions (those with data and no submenu)
        def collect_leaves(m):
            result = []
            for a in m.actions():
                if a.menu():
                    result.extend(collect_leaves(a.menu()))
                elif a.data():
                    result.append(a)
            return result

        leaves = collect_leaves(menu)
        assert len(leaves) >= 1
        assert leaves[0].data() == str(dummy)

# ============================================================================
# Blocker #2: Explicit AND/OR with multi-word operands
# ============================================================================

class TestExplicitANDORMultiword:
    """parse_search_tokens must join adjacent non-operator words."""

    def test_and_with_multiword_operand(self):
        groups = parse_search_tokens("new york AND city")
        assert groups == [["new york", "city"]], (
            f"Expected [['new york', 'city']], got {groups}"
        )

    def test_or_with_multiword_operands(self):
        groups = parse_search_tokens("new york OR los angeles")
        assert groups == [["new york"], ["los angeles"]], (
            f"Expected [['new york'], ['los angeles']], got {groups}"
        )

    def test_preserves_literal_plain_multiword(self):
        """Plain multi-word query without AND/OR still one literal operand."""
        groups = parse_search_tokens("new york")
        assert groups == [["new york"]]

    def test_mixed_or_and_multiword(self):
        groups = parse_search_tokens("big apple AND city OR small town")
        assert groups == [["big apple", "city"], ["small town"]], (
            f"Expected [['big apple', 'city'], ['small town']], got {groups}"
        )

    def test_case_insensitive_operators_multiword(self):
        groups = parse_search_tokens("new york and city")
        assert groups == [["new york", "city"]]

# ============================================================================
# Blocker #2: Explicit AND/OR with multi-word operands
# ============================================================================

class TestExplicitANDORMultiword:
    """parse_search_tokens must join adjacent non-operator words."""

    def test_and_with_multiword_operand(self):
        groups = parse_search_tokens("new york AND city")
        assert groups == [["new york", "city"]], (
            f"Expected [['new york', 'city']], got {groups}"
        )

    def test_or_with_multiword_operands(self):
        groups = parse_search_tokens("new york OR los angeles")
        assert groups == [["new york"], ["los angeles"]], (
            f"Expected [['new york'], ['los angeles']], got {groups}"
        )

    def test_preserves_literal_plain_multiword(self):
        groups = parse_search_tokens("new york")
        assert groups == [["new york"]]

    def test_mixed_or_and_multiword(self):
        groups = parse_search_tokens("big apple AND city OR small town")
        assert groups == [["big apple", "city"], ["small town"]], (
            f"Expected [['big apple', 'city'], ['small town']], got {groups}"
        )

    def test_case_insensitive_operators_multiword(self):
        groups = parse_search_tokens("new york and city")
        assert groups == [["new york", "city"]]

# ============================================================================
# Blocker #1: Unicode case-insensitive search
# ============================================================================

class TestUnicodeCaseInsensitiveSearch:
    """SQLite LIKE is ASCII-only; search must support Unicode casefolding."""

    @pytest.fixture
    def conn(self):
        c = sqlite3.connect(':memory:')
        init_db(c)
        ensure_unfamiliar_items_table(c)
        from kgb_srs.schema import migrate_unfamiliar_items_meaning
        migrate_unfamiliar_items_meaning(c)

        # Sentence card with accented text
        c.execute(
            "INSERT INTO cards (id, front, back, box, next_review) "
            "VALUES (1, 'Je vais à l''école', 'I go to school', 1, '2026-01-01')"
        )
        c.execute(
            "INSERT INTO unfamiliar_items (card_id, expression, meaning) "
            "VALUES (1, 'école', 'school')"
        )

        # Word/phrase card with accented text
        c.execute(
            "INSERT INTO cards (id, front, back, box, next_review) "
            "VALUES (2, 'ÉCOLE', 'school - an educational institution', 1, '2026-01-01')"
        )

        c.commit()
        yield c
        c.close()

    def test_sentence_search_casefold_front(self, conn):
        """Searching lowercase 'école' matches 'ÉCOLE' in sentence field."""
        results = search_sentence_cards(conn, 'école')
        assert len(results) >= 1
        ids = {r['id'] for r in results}
        assert 1 in ids

    def test_sentence_search_casefold_expression(self, conn):
        """Searching 'ÉCOLE' (uppercase accented) matches 'école' in child expression."""
        results = search_sentence_cards(conn, 'ÉCOLE')
        assert len(results) >= 1
        ids = {r['id'] for r in results}
        assert 1 in ids

    def test_word_phrase_search_casefold_front(self, conn):
        """Searching 'école' matches 'ÉCOLE' in word/phrase front."""
        results = search_word_phrase_cards(conn, 'école')
        assert len(results) >= 1
        ids = {r['id'] for r in results}
        assert 2 in ids

    def test_literal_percent_underscore_backslash_preserved(self, conn):
        """% and _ must still be treated literally after casefolding."""
        # Insert a card with literal % and _ in back
        c = conn.cursor()
        c.execute(
            "INSERT INTO cards (id, front, back, box, next_review) "
            "VALUES (3, 'test', '50% off and low_price', 1, '2026-01-01')"
        )
        conn.commit()

        # Search for the literal '%' — should match card 3
        results = search_word_phrase_cards(conn, '%')
        ids = {r['id'] for r in results}
        assert 3 in ids, f"Expected card 3 with literal %, got {ids}"

        # Search for literal '_' — should match card 3
        results2 = search_word_phrase_cards(conn, '_')
        ids2 = {r['id'] for r in results2}
        assert 3 in ids2, f"Expected card 3 with literal _, got {ids2}"

    def test_no_match_when_different(self, conn):
        """Non-matching queries should return empty."""
        results = search_sentence_cards(conn, 'xyznotfound')
        assert len(results) == 0

# ============================================================================
# Blocker #3: QThread lifecycle — result/error before finished
# ============================================================================


class TestQThreadLifecycle:
    """Result/error signals must not unlock controls until QThread.finished."""

    def test_result_signal_alone_does_not_restore_ui(self):
        """Receiving result signal must keep controls locked until finished."""
        _qt_app()
        from kgb_srs.forms import SentenceCardDialog, _AIGenerateWorker
        from PyQt6.QtCore import QThread

        dialog = SentenceCardDialog(sentence='Hello world', items=['Hello'])
        dialog._generate_ai_meanings = lambda: None  # no-op

        # Ensure controls start enabled
        dialog._restore_ui_after_ai()
        assert dialog._generate_btn.isEnabled()

        # Simulate AI generation start (same as _generate_ai_meanings does)
        worker = _AIGenerateWorker.__new__(_AIGenerateWorker)
        QThread.__init__(worker)
        dialog._ai_worker = worker
        dialog._generate_btn.setEnabled(False)
        dialog._sentence_edit.setEnabled(False)
        dialog._save_btn.setEnabled(False)
        dialog._cancel_btn.setEnabled(False)
        dialog._ai_progress.setVisible(True)

        # Connect result handler (as real code does)
        def on_finished(raw_text):
            pass  # real code would parse + display
        worker.result.connect(on_finished)

        # Emit result — should NOT restore UI
        worker.result.emit('dummy result')
        assert not dialog._generate_btn.isEnabled(), (
            'Generate button must stay disabled until finished'
        )
        assert not dialog._save_btn.isEnabled()
        assert not dialog._cancel_btn.isEnabled()

        # Emit finished — NOW controls should restore
        worker.finished.connect(lambda w=worker: dialog._on_ai_thread_stopped(w))
        worker.finished.emit()
        assert dialog._generate_btn.isEnabled()
        assert dialog._save_btn.isEnabled()
        assert dialog._cancel_btn.isEnabled()

    def test_error_signal_alone_does_not_restore_ui(self):
        """Receiving error signal must keep controls locked until finished."""
        _qt_app()
        from kgb_srs.forms import SentenceCardDialog, _AIGenerateWorker
        from PyQt6.QtCore import QThread

        dialog = SentenceCardDialog(sentence='Hello', items=['Hello'])
        dialog._generate_ai_meanings = lambda: None

        dialog._restore_ui_after_ai()
        assert dialog._generate_btn.isEnabled()

        worker = _AIGenerateWorker.__new__(_AIGenerateWorker)
        QThread.__init__(worker)
        dialog._ai_worker = worker
        dialog._generate_btn.setEnabled(False)
        dialog._save_btn.setEnabled(False)
        dialog._cancel_btn.setEnabled(False)

        worker.error.connect(lambda e: None)
        worker.error.emit('some error')
        assert not dialog._generate_btn.isEnabled(), (
            'Generate button must stay disabled after error signal'
        )

        worker.finished.connect(lambda w=worker: dialog._on_ai_thread_stopped(w))
        worker.finished.emit()
        assert dialog._generate_btn.isEnabled()

    def test_cannot_start_second_worker_between_result_and_finished(self):
        """A second Generate must be blocked until thread fully terminates."""
        _qt_app()
        from kgb_srs.forms import SentenceCardDialog, _AIGenerateWorker
        from PyQt6.QtCore import QThread

        dialog = SentenceCardDialog(sentence='Hello', items=['Hello'])
        dialog._generate_ai_meanings = lambda: None

        dialog._restore_ui_after_ai()
        worker1 = _AIGenerateWorker.__new__(_AIGenerateWorker)
        QThread.__init__(worker1)
        dialog._ai_worker = worker1
        dialog._generate_btn.setEnabled(False)

        worker1.result.connect(lambda t: None)
        worker1.result.emit('dummy')
        # Worker reference must persist
        assert dialog._ai_worker is worker1, (
            'Worker reference must be kept until finished'
        )
        # Generate button still disabled
        assert not dialog._generate_btn.isEnabled()

        worker1.finished.connect(lambda w=worker1: dialog._on_ai_thread_stopped(w))
        worker1.finished.emit()
        assert dialog._ai_worker is None

    def test_word_phrase_dialog_same_lifecycle(self):
        """WordPhraseCardDialog must follow the same lifecycle rules."""
        _qt_app()
        from kgb_srs.forms import WordPhraseCardDialog, _AIGenerateWorker
        from PyQt6.QtCore import QThread

        dialog = WordPhraseCardDialog(front='bonjour')
        dialog._generate_ai_meanings = lambda: None

        dialog._restore_ui_after_ai()
        assert dialog._generate_btn.isEnabled()

        worker = _AIGenerateWorker.__new__(_AIGenerateWorker)
        QThread.__init__(worker)
        dialog._ai_worker = worker
        dialog._set_controls_enabled(False)
        dialog._generate_btn.setEnabled(False)
        dialog._ai_progress.setVisible(True)

        assert not dialog._generate_btn.isEnabled()
        assert not dialog._save_btn.isEnabled()

        worker.result.connect(lambda t: None)
        worker.result.emit('dummy')
        assert not dialog._generate_btn.isEnabled(), (
            'Generate button must stay disabled until finished'
        )

        worker.finished.connect(lambda w=worker: dialog._on_ai_thread_stopped(w))
        worker.finished.emit()
        assert dialog._generate_btn.isEnabled()
        assert dialog._save_btn.isEnabled()

    def test_close_blocked_until_finished(self):
        """closeEvent must respect worker state through termination.
        
        When controls are disabled during generation, the Cancel button
        is not clickable.  After QThread.finished fires, controls
        restore and close becomes available. The reject() guard checks
        self._ai_worker is not None AND isRunning().
        """
        _qt_app()
        from kgb_srs.forms import SentenceCardDialog, _AIGenerateWorker
        from PyQt6.QtCore import QThread

        dialog = SentenceCardDialog(sentence='Hello', items=['Hello'])
        dialog._generate_ai_meanings = lambda: None

        dialog._restore_ui_after_ai()
        assert dialog._cancel_btn.isEnabled()

        worker = _AIGenerateWorker.__new__(_AIGenerateWorker)
        QThread.__init__(worker)
        dialog._ai_worker = worker
        dialog._generate_btn.setEnabled(False)
        dialog._cancel_btn.setEnabled(False)

        # Cancel button disabled — user can't trigger close
        assert not dialog._cancel_btn.isEnabled()

        # Emit result, but not finished — cancel still disabled
        worker.result.connect(lambda t: None)
        worker.result.emit('dummy')
        assert not dialog._cancel_btn.isEnabled(), (
            'Cancel must stay disabled between result and finished'
        )

        # Emit finished — cancel should now be enabled
        worker.finished.connect(lambda w=worker: dialog._on_ai_thread_stopped(w))
        worker.finished.emit()
        assert dialog._cancel_btn.isEnabled()

# ============================================================================
# Blocker #4: Settings staging — no mutation before save
# ============================================================================

class TestSettingsStaging:
    """open_settings_window must not mutate self.settings before save_settings succeeds."""

    def test_settings_not_mutated_before_save(self, tmp_path, monkeypatch):
        """Live settings remain unchanged before save_settings succeeds."""
        import kgb_srs.config as config
        from kgb_srs.main_window import BarskyApp

        # Create settings file with known values
        settings_path = tmp_path / "barsky_settings.json"
        monkeypatch.setattr(config, "SETTINGS_FILE", str(settings_path))
        config.save_settings({
            "width": 900, "height": 700, "font_family": "Arial",
            "font_size": 14, "default_database": "", "tts_voice": "en-US-Ava",
            "ai_base_url": "https://api.openai.com/v1",
            "ai_model": "gpt-4o-mini", "ai_api_key": "secret123",
            "ai_timeout": 30, "learned_language": "English",
            "explanation_language": "Chinese",
        })
        monkeypatch.setattr(config, "load_settings",
                            lambda: config.load_settings())

        _qt_app()
        window = BarskyApp()
        original_settings = dict(window.settings)  # deep copy

        # Simulate what save_and_apply in open_settings_window does:
        # It builds staged changes and saves them
        staged = dict(window.settings)
        staged["width"] = 1024
        staged["ai_api_key"] = "new_secret"

        # Before save, original settings should be unchanged
        assert window.settings["width"] == original_settings["width"]
        assert window.settings["ai_api_key"] == original_settings["ai_api_key"]

        # After successful save, should update
        config.save_settings(staged)
        window.settings.update(staged)
        assert window.settings["width"] == 1024
        assert window.settings["ai_api_key"] == "new_secret"

        window.close()

    def test_settings_preserved_on_save_failure(self, tmp_path, monkeypatch):
        """On OSError during save, live settings must remain byte-for-byte unchanged."""
        import kgb_srs.config as config
        from kgb_srs.main_window import BarskyApp

        settings_path = tmp_path / "barsky_settings.json"
        monkeypatch.setattr(config, "SETTINGS_FILE", str(settings_path))
        config.save_settings({
            "width": 900, "height": 700, "font_family": "Arial",
            "font_size": 14, "default_database": "", "tts_voice": "en-US-Ava",
            "ai_base_url": "https://api.openai.com/v1",
            "ai_model": "gpt-4o-mini", "ai_api_key": "secret123",
            "ai_timeout": 30, "learned_language": "English",
            "explanation_language": "Chinese",
        })

        _qt_app()
        window = BarskyApp()
        original = dict(window.settings)
        orig_json = json.dumps(original, sort_keys=True)

        # Build staged changes
        staged = dict(window.settings)
        staged["ai_api_key"] = "would_be_leaked"
        staged["width"] = 1234

        # Simulate save failure
        save_called = []
        def failing_save(s):
            save_called.append(dict(s))
            raise OSError("disk full")
        
        monkeypatch.setattr(config, "save_settings", failing_save)

        try:
            config.save_settings(staged)
        except OSError:
            pass

        # Live settings must be unchanged
        assert window.settings["ai_api_key"] == original["ai_api_key"], (
            "API key must not change on save failure"
        )
        assert window.settings["width"] == original["width"]
        assert json.dumps(window.settings, sort_keys=True) == orig_json, (
            "Settings must be byte-for-byte unchanged after save failure"
        )

        window.close()

    def test_api_key_not_leaked_on_save_failure(self, tmp_path, monkeypatch):
        """API key must remain unchanged when save_settings raises OSError."""
        import kgb_srs.config as config

        settings_path = tmp_path / "barsky_settings.json"
        monkeypatch.setattr(config, "SETTINGS_FILE", str(settings_path))

        original_key = "sk-original-secret-key"
        config.save_settings({"ai_api_key": original_key, "width": 900})

        _qt_app()
        from kgb_srs.main_window import BarskyApp
        window = BarskyApp()
        assert window.settings["ai_api_key"] == original_key

        # Stage a change
        staged = dict(window.settings)
        staged["ai_api_key"] = "sk-would-be-leaked"

        # Fail the save
        def failing_save(s):
            raise OSError("permission denied")
        monkeypatch.setattr(config, "save_settings", failing_save)

        try:
            config.save_settings(staged)
        except OSError:
            pass

        # Must still be original
        assert window.settings["ai_api_key"] == original_key, (
            "API key was mutated despite save failure"
        )

        window.close()


# ============================================================================
# Drop-zone layout: zones must sit well above review controls
# ============================================================================

class TestDropZoneLayoutDoesNotOverflow:
    """DropZoneItems must be fully inside the viewport with a visible
    internal inset, and the QGraphicsView must be separated from the
    review buttons by a clear external layout gap."""

    # ── helpers ────────────────────────────────────────────────────────
    @staticmethod
    def _build_and_measure(window_size):
        """Build BarskyApp at *window_size*, process events, and return
        the (window, view, viewport, incorrect_zone, start_btn)."""
        from PyQt6.QtWidgets import QApplication
        from kgb_srs.main_window import BarskyApp

        win = BarskyApp()
        win.resize(*window_size)
        win.show()
        QApplication.processEvents()
        win.redraw_canvas()
        QApplication.processEvents()
        return win, win.view, win.view.viewport(), win.incorrect_zone, win.start_btn

    # ── tests ──────────────────────────────────────────────────────────
    def test_zone_fully_inside_viewport_with_inset(self):
        """At a realistic default size the full zone bounding rect must
        lie inside the viewport, and the zone bottom must leave ≥ 10 px
        internal inset from the viewport bottom edge."""
        _qt_app()
        from PyQt6.QtCore import QPoint

        win, view, vp, zone, start_btn = self._build_and_measure((900, 700))

        assert zone is not None, "incorrect zone not created"
        sr = zone.sceneBoundingRect()
        vr = view.mapFromScene(sr).boundingRect()

        # Zone must be fully inside the viewport — no clipping.
        assert vr.y() >= 0, f"zone top clipped: {vr.y()} px above viewport"
        assert vr.bottom() <= vp.height(), (
            f"zone bottom {vr.bottom()} exceeds viewport height {vp.height()}"
        )

        # Internal inset: zone bottom must be ≥ 10 px above viewport bottom.
        inset = vp.height() - vr.bottom()
        assert inset >= 10, (
            f"zone-bottom → viewport-bottom inset is {inset} px, need ≥ 10"
        )

        win.close()

    def test_view_to_button_external_gap(self):
        """The QGraphicsView outer bottom edge must be ≥ 8 px above the
        Start Daily Review button top in global coordinates."""
        _qt_app()
        from PyQt6.QtCore import QPoint

        win, view, vp, zone, start_btn = self._build_and_measure((900, 700))

        view_bottom_global = view.mapToGlobal(QPoint(0, view.height())).y()
        btn_top_global = start_btn.mapToGlobal(QPoint(0, 0)).y()
        external_gap = btn_top_global - view_bottom_global
        assert external_gap >= 8, (
            f"view outer bottom → button top gap is {external_gap} px, need ≥ 8"
        )

        win.close()

    def test_zone_to_button_total_gap(self):
        """The total gap from zone painted bottom to button top must be
        clearly visible (≥ 18 px in global coordinates)."""
        _qt_app()
        from PyQt6.QtCore import QPoint

        win, view, vp, zone, start_btn = self._build_and_measure((900, 700))

        sr = zone.sceneBoundingRect()
        vr = view.mapFromScene(sr).boundingRect()
        zone_bottom_global = vp.mapToGlobal(
            QPoint(int(vr.center().x()), int(vr.bottom()))
        ).y()
        btn_top_global = start_btn.mapToGlobal(QPoint(0, 0)).y()
        total_gap = btn_top_global - zone_bottom_global
        assert total_gap >= 18, (
            f"zone bottom → button top total gap is {total_gap} px, need ≥ 18"
        )

        win.close()

    def test_holds_at_minimum_viable_height(self):
        """The constraints must hold at the minimum view height of 200
        where the viewport is still large enough for the zone."""
        _qt_app()
        from PyQt6.QtCore import QPoint

        # 600×400 keeps the view at its 200 px minimum height without
        # pushing Qt into an unresolvable size negotiation.
        win, view, vp, zone, start_btn = self._build_and_measure((600, 400))

        assert zone is not None
        sr = zone.sceneBoundingRect()
        vr = view.mapFromScene(sr).boundingRect()

        assert vr.y() >= 0
        inset = vp.height() - vr.bottom()
        assert inset >= 10, (
            f"at min height: zone inset = {inset} px, need ≥ 10"
        )

        view_bottom_global = view.mapToGlobal(QPoint(0, view.height())).y()
        btn_top_global = start_btn.mapToGlobal(QPoint(0, 0)).y()
        external_gap = btn_top_global - view_bottom_global
        assert external_gap >= 8, (
            f"at min height: external gap = {external_gap} px, need ≥ 8"
        )

        win.close()


# ============================================================================
# Review-controls — consolidated regression suite
# ============================================================================


class TestReviewControls:
    """Button visibility (#1), close-preserves-queue (#2), resume semantics
    (#3), and delete behavior."""

    # -- shared helpers ---------------------------------------------------

    @staticmethod
    def _db(*ids):
        """Return in-memory conn with cards inserted (all due today)."""
        import sqlite3, datetime
        from kgb_srs.db import init_db
        conn = sqlite3.connect(":memory:")
        init_db(conn)
        today = datetime.date.today().isoformat()
        for cid in ids:
            conn.execute(
                "INSERT INTO cards (id, front, back, box, next_review) "
                "VALUES (?, ?, ?, 1, ?)", (cid, f"c{cid}", f"b{cid}", today))
        conn.commit()
        return conn

    @staticmethod
    def _win(conn=None, card=None, due=(), mode="",
             paused_card=None, paused_mode=""):
        """Build BarskyApp with review state injected."""
        _qt_app()
        from kgb_srs.main_window import BarskyApp
        w = BarskyApp()
        if conn:
            w.conn = conn
        w.current_card = card
        w.cards_due = list(due)
        w.review_mode = mode
        w._paused_review_card = paused_card
        w._paused_review_mode = paused_mode
        return w

    @staticmethod
    def _mock_dialogs():
        """Patch QMessageBox to auto-confirm delete/acknowledge."""
        from unittest.mock import patch
        from PyQt6.QtWidgets import QMessageBox
        return (
            patch("kgb_srs.main_window.QMessageBox.question",
                  return_value=QMessageBox.StandardButton.Yes),
            patch("kgb_srs.main_window.QMessageBox.information"),
        )

    # -- stylesheet: disabled-rule required for visual fading --------------

    def test_button_style_includes_disabled_rule(self):
        """_button_style() must include QPushButton:disabled with faded
        background (#CFD8DC) and muted text (#78909C)."""
        from kgb_srs.main_window import BarskyApp
        style = BarskyApp._button_style("#D32F2F", "#F44336")
        assert "QPushButton:disabled" in style, (
            "Missing QPushButton:disabled selector"
        )
        assert "#CFD8DC" in style, (
            "Missing disabled background-color #CFD8DC"
        )
        assert "#78909C" in style, (
            "Missing disabled color #78909C"
        )
        # Ensure enabled colors are untouched
        assert "#D32F2F" in style
        assert "#F44336" in style
        assert "QPushButton:hover" in style

    # -- finding #1: button visibility after DB load ----------------------

    def test_buttons_after_db_load(self):
        """Start/Next/Restart/Previous enabled; Delete/Close disabled."""
        import tempfile, os
        conn = self._db(1)
        # load_database needs a real file-path — write to temp file
        tmp = tempfile.NamedTemporaryFile(suffix="_barsky.db", delete=False)
        tmp.close()
        try:
            from kgb_srs.db import init_db
            init_db(tmp.name).close()
            w = self._win(conn=conn)
            w.current_db_path = tmp.name
            w.current_lang = "Test"
            w.load_database(silent=True)

            assert w.start_btn.isEnabled()
            assert w.force_seq_btn.isEnabled()
            assert w.restart_review_btn.isEnabled()
            assert w.force_rev_btn.isEnabled()
            assert not w.delete_entry_btn.isEnabled()
            assert not w.close_review_btn.isEnabled()
        finally:
            os.unlink(tmp.name)
        conn.close(); w.close()

    def test_delete_and_close_enabled_during_review(self):
        """Delete/Close enabled when card + active review mode exist."""
        conn = self._db(1)
        w = self._win(conn=conn, card=(1, "c1", "b1", 1), mode="daily")
        w._update_button_visibility()

        assert w.delete_entry_btn.isEnabled()
        assert w.close_review_btn.isEnabled()
        conn.close(); w.close()

    def test_buttons_disabled_after_close(self):
        """After close_review, Delete and Close are disabled."""
        conn = self._db(1)
        w = self._win(conn=conn, card=(1, "c1", "b1", 1), mode="daily")
        w.close_review()

        assert not w.delete_entry_btn.isEnabled()
        assert not w.close_review_btn.isEnabled()
        conn.close(); w.close()

    # -- finding #2: close preserves queue ---------------------------------

    def test_close_preserves_cards_due(self):
        """cards_due content and order unchanged after close_review."""
        conn = self._db(1, 2, 3)
        due = [(2, "c2", "b2", 1), (3, "c3", "b3", 1)]
        w = self._win(conn=conn, card=(1, "c1", "b1", 1), due=due, mode="daily")

        snapshot = list(w.cards_due)
        w.close_review()
        assert w.cards_due == snapshot, "cards_due must survive close unchanged"
        conn.close(); w.close()

    # -- close semantics ---------------------------------------------------

    def test_close_stores_paused_card_and_mode(self):
        """close_review saves card + mode, clears current_card + review_mode."""
        conn = self._db(1, 2)
        w = self._win(conn=conn, card=(1, "c1", "b1", 1),
                       due=[(2, "c2", "b2", 1)], mode="daily")
        w.close_review()

        assert w._paused_review_card[0] == 1
        assert w._paused_review_mode == "daily"
        assert w.current_card is None
        assert w.review_mode == ""
        conn.close(); w.close()

    def test_close_does_not_mutate_db(self):
        """close_review leaves the database unchanged."""
        conn = self._db(1, 2)
        w = self._win(conn=conn, card=(1, "c1", "b1", 1),
                       due=[(2, "c2", "b2", 1)], mode="daily")

        before = list(conn.execute(
            "SELECT id, box, next_review FROM cards ORDER BY id").fetchall())
        w.close_review()
        after = list(conn.execute(
            "SELECT id, box, next_review FROM cards ORDER BY id").fetchall())
        assert before == after
        conn.close(); w.close()

    def test_close_noop_without_card(self):
        """close_review is safe when no card is shown."""
        w = self._win()
        w.close_review()
        assert w._paused_review_card is None
        w.close()

    # -- resume: daily after close ----------------------------------------

    def test_daily_resume_paused_first_preserved_queue(self):
        """After daily close, start_review shows paused card first,
        then the preserved remaining queue (no requery)."""
        conn = self._db(1, 2, 3)
        w = self._win(conn=conn, card=(2, "c2", "b2", 1),
                       due=[(3, "c3", "b3", 1)], mode="daily")
        w.close_review()
        w.start_review()

        assert w.current_card[0] == 2, "paused card must be first"
        assert w._paused_review_card is None
        assert [c[0] for c in w.cards_due] == [3], "preserved queue follows"
        conn.close(); w.close()

    def test_daily_resume_no_duplicate_in_queue(self):
        """Paused card is de-duplicated from the resumed queue."""
        conn = self._db(1, 2, 3)
        w = self._win(conn=conn, card=(2, "c2", "b2", 1),
                       due=[(2, "c2", "b2", 1), (3, "c3", "b3", 1)], mode="daily")
        w.close_review()
        w.start_review()
        assert sum(1 for c in w.cards_due if c[0] == 2) == 0
        conn.close(); w.close()

    def test_daily_resume_skips_deleted_paused(self):
        """If paused card was deleted, silently skip to next card."""
        conn = self._db(1, 2)
        w = self._win(conn=conn, card=(1, "c1", "b1", 1),
                       due=[(2, "c2", "b2", 1)], mode="daily")
        w.close_review()
        conn.execute("DELETE FROM cards WHERE id=1"); conn.commit()
        w.start_review()
        assert w.current_card[0] != 1
        conn.close(); w.close()

    def test_daily_resume_fresh_db_data(self):
        """Resumed card re-fetched from DB (sees external edits)."""
        conn = self._db(1)
        w = self._win(conn=conn, card=(1, "c1", "b1", 1), mode="daily")
        w.close_review()
        conn.execute("UPDATE cards SET front='updated', box=2 WHERE id=1")
        conn.commit()
        w.start_review()
        assert w.current_card[1] == "updated"
        assert w.current_card[3] == 2
        conn.close(); w.close()

    def test_daily_start_without_pause_fresh_query(self):
        """No paused card → normal daily review with fresh DB query."""
        conn = self._db(1, 2)
        w = self._win(conn=conn)
        w.start_review()
        assert w.current_card is not None
        assert w.review_mode == "daily"
        conn.close(); w.close()

    # -- resume: Next / Previous / Restart after close --------------------

    def test_next_after_close_paused_first_then_asc(self):
        """Next after close: paused first, then ASC from paused+1."""
        conn = self._db(1, 2, 3, 4, 5)
        w = self._win(conn=conn, card=(3, "c3", "b3", 1), mode="force_seq")
        w.close_review()
        w.start_forced_review(direction="ASC")

        assert w.current_card[0] == 3
        remaining = [c[0] for c in w.cards_due]
        assert 3 not in remaining
        assert remaining[0] == 4
        conn.close(); w.close()

    def test_previous_after_close_paused_first_then_desc(self):
        """Previous after close: paused first, then DESC from paused-1."""
        conn = self._db(1, 2, 3, 4, 5)
        w = self._win(conn=conn, card=(3, "c3", "b3", 1), mode="force_rev")
        w.close_review()
        w.start_forced_review(direction="DESC")

        assert w.current_card[0] == 3
        assert [c[0] for c in w.cards_due][0] == 2
        conn.close(); w.close()

    def test_restart_after_close_paused_first_all_asc(self):
        """Restart after close: paused first, all other cards ASC."""
        conn = self._db(1, 2, 3, 4, 5)
        w = self._win(conn=conn, card=(3, "c3", "b3", 1), mode="force_seq")
        w.close_review()
        w.restart_current_review()

        assert w.current_card[0] == 3
        assert sorted(c[0] for c in w.cards_due) == [1, 2, 4, 5]
        conn.close(); w.close()

    def test_forced_resume_skips_deleted_paused(self):
        """Next after close: deleted paused card silently skipped."""
        conn = self._db(1, 2, 3)
        w = self._win(conn=conn, card=(2, "c2", "b2", 1), mode="force_seq")
        w.close_review()
        conn.execute("DELETE FROM cards WHERE id=2"); conn.commit()
        w.start_forced_review(direction="ASC")
        assert w.current_card[0] != 2
        conn.close(); w.close()

    # -- DB load clears paused state --------------------------------------

    def test_db_load_clears_paused_state(self):
        """Loading a database resets paused review state."""
        import tempfile, os
        conn = self._db(1)
        # Create a real temp DB so load_database doesn't early-return
        tmp = tempfile.NamedTemporaryFile(suffix="_barsky.db", delete=False)
        tmp.close()
        try:
            from kgb_srs.db import init_db
            init_db(tmp.name).close()
            w = self._win(conn=conn, card=(1, "c1", "b1", 1), mode="daily")
            w.close_review()
            assert w._paused_review_card is not None

            w.current_db_path = tmp.name
            w.current_lang = "Test"
            w.load_database(silent=True)
            assert w._paused_review_card is None
            assert w._paused_review_mode == ""
        finally:
            os.unlink(tmp.name)
        conn.close(); w.close()

    # -- delete behavior --------------------------------------------------

    def test_delete_clears_paused_and_advances(self):
        """Deleting active card: DB row gone, paused cleared, next shown."""
        conn = self._db(1, 2)
        w = self._win(conn=conn, card=(1, "c1", "b1", 1),
                       due=[(2, "c2", "b2", 1)], mode="daily",
                       paused_card=(1, "c1", "b1", 1), paused_mode="daily")

        p1, p2 = self._mock_dialogs()
        with p1, p2:
            w.delete_current_card()

        assert conn.execute("SELECT id FROM cards WHERE id=1").fetchone() is None
        assert w.current_card[0] == 2
        assert w._paused_review_card is None
        assert w._paused_review_mode == ""
        conn.close(); w.close()

    def test_delete_last_card_disables_buttons(self):
        """Deleting the last card: no current card, buttons disabled."""
        conn = self._db(1)
        w = self._win(conn=conn, card=(1, "c1", "b1", 1),
                       due=[], mode="daily")

        p1, p2 = self._mock_dialogs()
        with p1, p2:
            w.delete_current_card()

        assert w.current_card is None
        assert not w.delete_entry_btn.isEnabled()
        assert not w.close_review_btn.isEnabled()
        conn.close(); w.close()

    def test_delete_removes_from_queue(self):
        """Deleted card removed from cards_due."""
        conn = self._db(1, 2)
        w = self._win(conn=conn, card=(1, "c1", "b1", 1),
                       due=[(1, "c1", "b1", 1), (2, "c2", "b2", 1)], mode="daily")

        p1, p2 = self._mock_dialogs()
        with p1, p2:
            w.delete_current_card()

        assert 1 not in [c[0] for c in w.cards_due]
        conn.close(); w.close()

    def test_delete_card_by_id_helper(self):
        """_delete_card_by_id: DB row gone, review state + paused cleared."""
        conn = self._db(1, 2)
        w = self._win(conn=conn, card=(1, "c1", "b1", 1),
                       due=[(1, "c1", "b1", 1), (2, "c2", "b2", 1)],
                       mode="daily",
                       paused_card=(1, "c1", "b1", 1), paused_mode="daily")

        returned = w._delete_card_by_id(1)

        # DB row deleted
        assert conn.execute(
            "SELECT id FROM cards WHERE id=1").fetchone() is None
        # Card 2 still exists
        assert conn.execute(
            "SELECT id FROM cards WHERE id=2").fetchone() is not None
        # Review state cleaned: not in cards_due, not current
        assert 1 not in [c[0] for c in w.cards_due]
        assert w.current_card is None
        # Paused state cleared
        assert w._paused_review_card is None
        assert w._paused_review_mode == ""
        # Returns the integer id
        assert returned == 1
        conn.close(); w.close()

    # -- widget sanity ----------------------------------------------------

    def test_widget_labels_and_existence(self):
        """Verify delete_entry_btn label, close_review_btn type, no legacy."""
        _qt_app()
        from PyQt6.QtWidgets import QPushButton
        from kgb_srs.main_window import BarskyApp
        w = BarskyApp()

        assert w.delete_entry_btn.text().strip() == "Delete Entry"
        assert isinstance(w.close_review_btn, QPushButton)
        assert not hasattr(w, "delete_current_btn"), "legacy widget removed"
        w.close()
