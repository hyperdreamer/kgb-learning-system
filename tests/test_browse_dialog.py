"""Tests for the card browse dialog."""

import sqlite3

from PyQt6.QtWidgets import QWidget

from .qt_helpers import qt_app as _qt_app


def test_browse_dialog_hides_row_number_header():
    """The card ID column is the sole visible identifier for each row."""
    _qt_app()
    from kgb_srs.browse_dialog import BrowseCardsDialog
    from kgb_srs.catalog import DatabaseType
    from kgb_srs.schema import init_db

    conn = sqlite3.connect(":memory:")
    init_db(conn)
    controller = QWidget()
    controller.conn = conn
    controller.current_lang = "Test"
    controller._db_type = DatabaseType.KNOWLEDGE
    dialog = BrowseCardsDialog(controller)

    assert dialog.table.verticalHeader().isHidden()

    dialog.close()
    controller.close()
    conn.close()
