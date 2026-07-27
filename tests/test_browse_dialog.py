"""Tests for the card browse dialog."""

import sqlite3

from PyQt6.QtCore import QCoreApplication, QEvent
from PyQt6.QtGui import QFont, QShortcut
from PyQt6.QtWidgets import QWidget

from .qt_helpers import qt_app as _qt_app


def _browse_controller(database_type):
    """Return a real QWidget controller and in-memory database for Browse."""
    conn = sqlite3.connect(":memory:")
    from kgb_srs.schema import init_db

    init_db(conn)
    controller = QWidget()
    controller.setFont(QFont("Arial", 17))
    controller.conn = conn
    controller.current_lang = "Test"
    controller._db_type = database_type
    controller.current_card = None
    reviewed = []
    controller._start_selected_card_review = reviewed.append
    return controller, conn, reviewed


def _add_card(conn, front="Alpha card"):
    cursor = conn.execute(
        "INSERT INTO cards (front, back, box, next_review) VALUES (?, ?, ?, ?)",
        (front, "Back", 1, "2026-07-27"),
    )
    conn.commit()
    return cursor.lastrowid


def _dispose(dialog, controller, conn):
    dialog.close()
    dialog.deleteLater()
    controller.close()
    controller.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    _qt_app().processEvents()
    conn.close()


def test_browse_dialog_uses_roles_without_changing_rows_or_shortcuts():
    """Browse keeps search/activation/shortcuts while actions use fixed roles."""
    _qt_app()
    from kgb_srs.browse_dialog import BrowseCardsDialog
    from kgb_srs.catalog import DatabaseType
    from kgb_srs.ui_theme import ROLE_PROPERTY, stylesheet

    controller, conn, reviewed = _browse_controller(DatabaseType.KNOWLEDGE)
    card_id = _add_card(conn)
    dialog = BrowseCardsDialog(controller)
    try:
        assert dialog.table.verticalHeader().isHidden()
        assert dialog.styleSheet() == stylesheet("Arial", 17)
        assert dialog.review_btn.property(ROLE_PROPERTY) == "primary"
        assert dialog.edit_btn.property(ROLE_PROPERTY) == "secondary"
        assert dialog.delete_btn.property(ROLE_PROPERTY) == "danger"
        assert dialog.review_btn.styleSheet() == ""
        assert dialog.edit_btn.styleSheet() == ""
        assert dialog.delete_btn.styleSheet() == ""

        dialog.filter_input.setText("alpha")
        assert dialog.table.rowCount() == 1
        assert dialog.table.item(0, 0).text() == str(card_id)

        activated = []
        dialog.edit_selected = lambda: activated.append("edit")
        dialog.activate_row()
        assert activated == ["edit"]

        shortcut_keys = {
            shortcut.key().toString() for shortcut in dialog.findChildren(QShortcut)
        }
        assert {"Alt+R", "Alt+E", "Alt+D"} <= shortcut_keys
        dialog.table.selectRow(0)
        review_shortcut = next(
            shortcut
            for shortcut in dialog.findChildren(QShortcut)
            if shortcut.key().toString() == "Alt+R"
        )
        review_shortcut.activated.emit()
        assert reviewed == [card_id]
    finally:
        _dispose(dialog, controller, conn)


def test_browse_dialog_keeps_word_phrase_actions_read_only_with_roles():
    """Word/Phrase rows still activate review while edit/delete stay disabled."""
    _qt_app()
    from kgb_srs.browse_dialog import BrowseCardsDialog
    from kgb_srs.catalog import DatabaseType
    from kgb_srs.ui_theme import ROLE_PROPERTY

    controller, conn, reviewed = _browse_controller(DatabaseType.LANGUAGE_WORD_PHRASE)
    card_id = _add_card(conn, front="Dictionary entry")
    dialog = BrowseCardsDialog(controller)
    try:
        assert dialog.review_btn.property(ROLE_PROPERTY) == "primary"
        assert dialog.edit_btn.property(ROLE_PROPERTY) == "secondary"
        assert dialog.delete_btn.property(ROLE_PROPERTY) == "danger"
        assert dialog.review_btn.isEnabled()
        assert not dialog.edit_btn.isEnabled()
        assert not dialog.delete_btn.isEnabled()
        assert "read-only" in dialog.edit_btn.toolTip().lower()
        assert "read-only" in dialog.delete_btn.toolTip().lower()

        dialog.table.selectRow(0)
        dialog.activate_row()
        assert reviewed == [card_id]
    finally:
        _dispose(dialog, controller, conn)
