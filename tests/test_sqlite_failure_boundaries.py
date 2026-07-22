"""Regression tests for SQLite write-failure UI boundaries."""

import datetime
import logging
import sqlite3
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PyQt6.QtWidgets import QDialog, QMessageBox, QWidget

from kgb_srs.review_controller import ReviewHistoryEntry

from .qt_helpers import qt_app as _qt_app


def test_failed_rollback_is_logged_without_masking_the_operation_failure(caplog):
    from kgb_srs.db import rollback_after_failure

    class _RollbackFailure:
        def rollback(self):
            raise sqlite3.OperationalError("rollback unavailable")

    with caplog.at_level(logging.WARNING, logger="kgb_srs.db"):
        assert rollback_after_failure(_RollbackFailure(), "card update") is None

    assert "card update" in caplog.text
    assert "rollback unavailable" in caplog.text


class _FailingCommitConnection(sqlite3.Connection):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fail_commit = False
        self.rollback_calls = 0

    def commit(self):
        if self.fail_commit:
            raise sqlite3.OperationalError("disk is full")
        return super().commit()

    def rollback(self):
        self.rollback_calls += 1
        return super().rollback()


@pytest.fixture
def conn():
    from kgb_srs.schema import init_db

    connection = sqlite3.connect(":memory:", factory=_FailingCommitConnection)
    init_db(connection)
    yield connection
    connection.fail_commit = False
    connection.close()


def _input_dialogs(*values):
    values = iter(values)

    class _InputDialog:
        def __init__(self, *_args, **_kwargs):
            self.text_value = next(values)

        def exec(self):
            return QDialog.DialogCode.Accepted

    return _InputDialog


def test_browse_edit_rejects_case_insensitive_duplicate_front(monkeypatch, conn):
    _qt_app()
    from kgb_srs.browse_dialog import BrowseCardsDialog
    from kgb_srs.catalog import DatabaseType
    import kgb_srs.browse_dialog as browse_dialog

    today = datetime.date.today().isoformat()
    conn.executemany(
        "INSERT INTO cards (id, front, back, box, next_review) VALUES (?, ?, ?, 1, ?)",
        [(1, "Existing", "first", today), (2, "Other", "second", today)],
    )
    conn.commit()
    controller = QWidget()
    controller.conn = conn
    controller.current_lang = "Test"
    controller._db_type = DatabaseType.KNOWLEDGE
    controller._refresh_current_card = Mock()
    dialog = BrowseCardsDialog(controller)
    dialog.table.selectRow(1)
    dialog.refresh_list = Mock()
    messages = []
    monkeypatch.setattr(
        browse_dialog,
        "DynamicInputDialog",
        _input_dialogs("eXiStInG", "updated translation"),
    )
    monkeypatch.setattr(
        QMessageBox,
        "information",
        lambda _parent, title, _message: messages.append(title),
    )

    dialog.edit_selected()

    assert conn.execute("SELECT front, back FROM cards WHERE id=2").fetchone() == (
        "Other",
        "second",
    )
    assert messages == ["Already Exists"]
    dialog.refresh_list.assert_not_called()
    controller._refresh_current_card.assert_not_called()
    dialog.close()


def test_browse_edit_rolls_back_and_does_not_refresh_after_commit_failure(
    monkeypatch, conn
):
    _qt_app()
    from kgb_srs.browse_dialog import BrowseCardsDialog
    from kgb_srs.catalog import DatabaseType
    import kgb_srs.browse_dialog as browse_dialog

    today = datetime.date.today().isoformat()
    conn.execute(
        "INSERT INTO cards (id, front, back, box, next_review) VALUES (1, 'Old', 'old', 1, ?)",
        (today,),
    )
    conn.commit()
    controller = QWidget()
    controller.conn = conn
    controller.current_lang = "Test"
    controller._db_type = DatabaseType.KNOWLEDGE
    controller._refresh_current_card = Mock()
    dialog = BrowseCardsDialog(controller)
    dialog.table.selectRow(0)
    dialog.refresh_list = Mock()
    warnings = []
    monkeypatch.setattr(
        browse_dialog, "DynamicInputDialog", _input_dialogs("New", "new translation")
    )
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        lambda _parent, title, _message: warnings.append(title),
    )
    conn.fail_commit = True

    dialog.edit_selected()

    assert conn.execute("SELECT front, back FROM cards WHERE id=1").fetchone() == (
        "Old",
        "old",
    )
    assert conn.rollback_calls == 1
    assert warnings == ["Could not update card"]
    dialog.refresh_list.assert_not_called()
    controller._refresh_current_card.assert_not_called()
    dialog.close()


def test_sentence_save_reports_sqlite_error_without_progressing(monkeypatch, conn):
    from kgb_srs.main_window import BarskyApp
    import kgb_srs.main_window as main_window

    class _SentenceDialog:
        result_sentence = "Sentence"
        result_items = [("sentence", "meaning")]
        result_back = "back"
        result_verified_surfaces = {}

        def __init__(self, *_args, **_kwargs):
            pass

        def exec(self):
            return QDialog.DialogCode.Accepted

    window = SimpleNamespace(conn=conn, settings={}, settings_file=None)
    window._show_new_card = Mock()
    window._sync_linked_word_phrase_quiet = Mock()
    warnings = []
    monkeypatch.setattr(main_window, "SentenceCardDialog", _SentenceDialog)
    monkeypatch.setattr(
        main_window,
        "insert_sentence_card",
        Mock(side_effect=sqlite3.OperationalError("database is locked")),
    )
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        lambda _parent, title, _message: warnings.append(title),
    )

    BarskyApp._add_sentence_card(window)

    assert warnings == ["Could not save card"]
    window._show_new_card.assert_not_called()
    window._sync_linked_word_phrase_quiet.assert_not_called()


def test_generic_add_rolls_back_and_does_not_show_added_after_commit_failure(
    monkeypatch, conn
):
    from kgb_srs.main_window import BarskyApp
    import kgb_srs.main_window as main_window

    window = SimpleNamespace(conn=conn, _show_new_card=Mock())
    warnings = []
    monkeypatch.setattr(
        main_window, "DynamicInputDialog", _input_dialogs("Question", "Answer")
    )
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        lambda _parent, title, _message: warnings.append(title),
    )
    conn.fail_commit = True

    BarskyApp._add_knowledge_card(window)

    assert conn.execute("SELECT COUNT(*) FROM cards").fetchone()[0] == 0
    assert conn.rollback_calls == 1
    assert warnings == ["Could not save card"]
    window._show_new_card.assert_not_called()


def test_generic_edit_rolls_back_and_does_not_refresh_after_commit_failure(
    monkeypatch, conn
):
    from kgb_srs.main_window import BarskyApp
    import kgb_srs.main_window as main_window

    today = datetime.date.today().isoformat()
    conn.execute(
        "INSERT INTO cards (id, front, back, box, next_review) VALUES (1, 'Question', 'Old', 3, ?)",
        (today,),
    )
    conn.commit()
    window = SimpleNamespace(conn=conn, _refresh_current_card=Mock())
    warnings = []
    monkeypatch.setattr(main_window, "DynamicInputDialog", _input_dialogs("New answer"))
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        lambda _parent, title, _message: warnings.append(title),
    )
    conn.fail_commit = True

    BarskyApp._add_knowledge_card(window, edit_card_id=1, existing_front="Question")

    assert conn.execute("SELECT front, back, box FROM cards WHERE id=1").fetchone() == (
        "Question",
        "Old",
        3,
    )
    assert conn.rollback_calls == 1
    assert warnings == ["Could not save card"]
    window._refresh_current_card.assert_not_called()


def test_grading_rolls_back_and_keeps_review_state_after_commit_failure(
    monkeypatch, conn
):
    _qt_app()
    from kgb_srs.main_window import BarskyApp

    today = datetime.date.today().isoformat()
    conn.execute(
        "INSERT INTO cards (id, front, back, box, next_review) VALUES (1, 'Question', 'Answer', 1, ?)",
        (today,),
    )
    conn.commit()
    window = BarskyApp()
    window.conn = conn
    window.current_card = (1, "Question", "Answer", 1)
    window.review_mode = "daily"
    window.is_current_flipped = True
    window._daily_review_history = []
    window.show_next_card = Mock()
    warnings = []
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        lambda _parent, title, _message: warnings.append(title),
    )
    conn.fail_commit = True

    window.process_answer(correct=True)

    assert conn.execute("SELECT box FROM cards WHERE id=1").fetchone() == (1,)
    assert conn.rollback_calls == 1
    assert warnings == ["Could not grade card"]
    assert window.current_card == (1, "Question", "Answer", 1)
    assert window._daily_review_history == []
    window.show_next_card.assert_not_called()
    window.close()


def test_deletion_rolls_back_and_keeps_review_state_after_commit_failure(
    monkeypatch, conn
):
    from kgb_srs.main_window import BarskyApp

    today = datetime.date.today().isoformat()
    conn.execute(
        "INSERT INTO cards (id, front, back, box, next_review) VALUES (1, 'Question', 'Answer', 1, ?)",
        (today,),
    )
    conn.commit()
    window = SimpleNamespace(
        conn=conn,
        current_card=(1, "Question", "Answer", 1),
        cards_due=[(1, "Question", "Answer", 1)],
        _daily_review_history=[
            ReviewHistoryEntry((1, "Question", "Answer", 1), "graded")
        ],
        _daily_queue_snapshot=[(1, "Question", "Answer", 1)],
        _paused_cards_due=[(1, "Question", "Answer", 1)],
        _paused_daily_queue=[(1, "Question", "Answer", 1)],
        _paused_review_history=[
            ReviewHistoryEntry((1, "Question", "Answer", 1), "graded")
        ],
    )
    warnings = []
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        lambda _parent, title, _message: warnings.append(title),
    )
    conn.fail_commit = True

    result = BarskyApp._delete_card_by_id(window, 1)

    assert result is None
    assert conn.execute("SELECT id FROM cards WHERE id=1").fetchone() == (1,)
    assert conn.rollback_calls == 1
    assert warnings == ["Could not delete card"]
    assert window.current_card[0] == 1
    assert [card[0] for card in window.cards_due] == [1]
