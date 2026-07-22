"""Browse and search dialog for card databases."""

import datetime
import sqlite3

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from .catalog import DatabaseType
from .db import rollback_after_failure
from .dialogs import DynamicInputDialog
from .search import search_sentence_cards, search_word_phrase_cards


def _fetch_expressions_for_card(conn, card_id):
    from .schema import ensure_sentence_schema

    ensure_sentence_schema(conn, commit=False)
    cur = conn.cursor()
    cur.execute(
        "SELECT expression, meaning, sense_id, surface_form "
        "FROM unfamiliar_items WHERE card_id=? ORDER BY id",
        (card_id,),
    )
    return [(row[0], row[1], row[2], row[3] or "") for row in cur.fetchall()]


def _expression_labels(items):
    return [item[0] if isinstance(item, (tuple, list)) else item for item in items]


class BrowseCardsDialog(QDialog):
    """Modal browse/search view bound to a main-window controller."""

    def __init__(self, controller):
        super().__init__(controller)
        self.controller = controller
        self.setWindowTitle(f"Browse Cards: {controller.current_lang}")
        self.setFont(controller.font())
        self.resize(800, 500)

        layout = QVBoxLayout(self)
        filter_row = QHBoxLayout()
        filter_row.addWidget(QLabel("Search:"))
        self.filter_input = QLineEdit()
        self.filter_input.setPlaceholderText(
            "Type to search; use AND or OR to combine terms"
        )
        filter_row.addWidget(self.filter_input)
        layout.addLayout(filter_row)

        self.table = QTableWidget()
        self.table.setColumnCount(4)
        self.table.setHorizontalHeaderLabels(["ID", "Front", "Box", "Next Review"])
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        header.setMinimumSectionSize(48)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        layout.addWidget(self.table)

        button_row = QHBoxLayout()
        self.review_btn = QPushButton("Review Selected")
        self.edit_btn = QPushButton("Edit Selected")
        self.delete_btn = QPushButton("Delete Selected")
        self.delete_btn.setStyleSheet("background-color: #ffcccc;")
        self.review_btn.setToolTip("Review the selected card (Alt+R)")
        self.edit_btn.setToolTip("Edit the selected card (Alt+E)")
        self.delete_btn.setToolTip("Delete the selected card (Alt+D)")
        for button in (self.review_btn, self.edit_btn, self.delete_btn):
            button_row.addWidget(button)
        layout.addLayout(button_row)

        self._is_word_phrase = controller._db_type == DatabaseType.LANGUAGE_WORD_PHRASE
        if self._is_word_phrase:
            self.edit_btn.setEnabled(False)
            self.delete_btn.setEnabled(False)
            self.edit_btn.setToolTip("Word/phrase dictionary is read-only.")
            self.delete_btn.setToolTip("Word/phrase dictionary is read-only.")
            self.review_btn.setToolTip(
                "Open the selected dictionary entry for review (Alt+R / double-click)"
            )

        self.filter_input.textChanged.connect(self.refresh_list)
        self.review_btn.clicked.connect(self.review_selected)
        self.edit_btn.clicked.connect(self.edit_selected)
        self.delete_btn.clicked.connect(self.delete_selected)
        self.table.itemDoubleClicked.connect(self.activate_row)
        self._install_shortcuts()
        self.refresh_list()

    def _install_shortcuts(self):
        def add(key, slot):
            shortcut = QShortcut(QKeySequence(key), self)
            shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            shortcut.activated.connect(slot)

        add("Alt+R", self.review_selected)
        add(
            "Alt+E", lambda: self.edit_selected() if self.edit_btn.isEnabled() else None
        )
        add(
            "Alt+D",
            lambda: self.delete_selected() if self.delete_btn.isEnabled() else None,
        )

    def _search_logic(self):
        text = self.filter_input.text().strip()
        if " OR " in text.upper():
            return text, "OR"
        return text, "AND"

    def _add_row(self, card_id, front, box, next_review, expressions=()):
        row = self.table.rowCount()
        self.table.insertRow(row)
        if expressions:
            front += " [" + ", ".join(expressions) + "]"
        values = (card_id, front, box, next_review)
        for column, value in enumerate(values):
            self.table.setItem(row, column, QTableWidgetItem(str(value)))

    def refresh_list(self):
        self.table.setRowCount(0)
        conn = self.controller.conn
        search_text, logic = self._search_logic()
        db_type = self.controller._db_type
        if search_text:
            if db_type == DatabaseType.LANGUAGE_SENTENCE:
                results = search_sentence_cards(conn, search_text, logic)
                for result in results:
                    self._add_row(
                        result["id"],
                        result["front"],
                        result["box"],
                        result["next_review"],
                        result.get("expressions", ()),
                    )
            else:
                results = search_word_phrase_cards(conn, search_text, logic)
                for result in results:
                    self._add_row(
                        result["id"],
                        result["front"],
                        result["box"],
                        result["next_review"],
                    )
            return

        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, front, back, box, next_review FROM cards ORDER BY id"
        )
        for card_id, front, _back, box, next_review in cursor.fetchall():
            expressions = ()
            if db_type == DatabaseType.LANGUAGE_SENTENCE:
                expressions = _expression_labels(
                    _fetch_expressions_for_card(conn, card_id)
                )
            self._add_row(card_id, front, box, next_review, expressions)

    def _selected_card_id(self):
        selected = self.table.selectedItems()
        return int(selected[0].text()) if selected else None

    def review_selected(self):
        card_id = self._selected_card_id()
        if card_id is None:
            QMessageBox.information(
                self, "Nothing Selected", "Select a card to review."
            )
            return
        self.close()
        self.controller._start_selected_card_review(card_id)

    def edit_selected(self):
        card_id = self._selected_card_id()
        if card_id is None:
            return
        if self.controller._db_type == DatabaseType.LANGUAGE_SENTENCE:
            self.close()
            self.controller._add_sentence_card(edit_card_id=card_id)
            return
        if self._is_word_phrase:
            QMessageBox.information(
                self,
                "Read-only Word/Phrase Card",
                "This dictionary is derived from the shared sense catalog.\n\n"
                "Edit the expression/sense via sentence cards; the dictionary "
                "updates automatically. Manual edit is disabled.",
            )
            return

        card = self.controller.conn.execute(
            "SELECT front, back FROM cards WHERE id=?", (card_id,)
        ).fetchone()
        if card is None:
            return
        front_dialog = DynamicInputDialog(self, "Edit Word", "Front:", card[0])
        if (
            front_dialog.exec() != QDialog.DialogCode.Accepted
            or not front_dialog.text_value
        ):
            return
        back_dialog = DynamicInputDialog(
            self,
            "Edit Translation",
            "Enter the translation, meanings, or sample sentences. "
            "Markdown and MathJax are supported during review:",
            card[1],
        )
        if (
            back_dialog.exec() != QDialog.DialogCode.Accepted
            or not back_dialog.text_value
        ):
            return
        conn = self.controller.conn
        if self.controller._db_type == DatabaseType.KNOWLEDGE:
            try:
                duplicate = conn.execute(
                    "SELECT front FROM cards "
                    "WHERE front = ? COLLATE NOCASE AND id != ?",
                    (front_dialog.text_value, card_id),
                ).fetchone()
            except sqlite3.Error:
                rollback_after_failure(conn, "browse card update")
                QMessageBox.warning(
                    self, "Could not update card", "The card could not be updated."
                )
                return
            if duplicate is not None:
                QMessageBox.information(
                    self,
                    "Already Exists",
                    f"'{duplicate[0]}' is already in your database.",
                )
                return

        try:
            conn.execute(
                "UPDATE cards SET front=?, back=?, box=1, next_review=? WHERE id=?",
                (
                    front_dialog.text_value,
                    back_dialog.text_value,
                    datetime.date.today().isoformat(),
                    card_id,
                ),
            )
            conn.commit()
        except sqlite3.Error:
            rollback_after_failure(conn, "browse card update")
            QMessageBox.warning(
                self, "Could not update card", "The card could not be updated."
            )
            return

        self.refresh_list()
        QMessageBox.information(
            self,
            "Updated",
            "Card has been updated and moved back to Box 1 for review today.",
        )
        self.controller._refresh_current_card(card_id)

    def delete_selected(self):
        if self._is_word_phrase:
            QMessageBox.information(
                self,
                "Read-only Word/Phrase Card",
                "This dictionary is derived from the shared sense catalog.\n\n"
                "Remove senses via sentence cards; the dictionary updates "
                "automatically. Manual delete is disabled.",
            )
            return
        card_id = self._selected_card_id()
        if card_id is None:
            return
        reply = QMessageBox.question(
            self,
            "Confirm",
            "Delete this card?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        deleted_current = (
            self.controller.current_card is not None
            and self.controller.current_card[0] == card_id
        )
        if self.controller._delete_card_by_id(card_id) is None:
            return
        self.refresh_list()
        if deleted_current:
            self.controller.show_next_card()

    def activate_row(self, _item=None):
        if self._is_word_phrase:
            self.review_selected()
        else:
            self.edit_selected()
