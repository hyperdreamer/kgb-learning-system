"""Main application window – BarskyApp."""

import os
import datetime
import random
import re

from PyQt6.QtWidgets import (
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QComboBox,
    QPushButton,
    QGraphicsView,
    QGraphicsScene,
    QMessageBox,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QSpinBox,
    QFormLayout,
    QAbstractItemView,
    QCheckBox,
    QMenu,
)
from PyQt6.QtCore import Qt, QTimer, QUrl
from PyQt6.QtGui import QFont, QFontDatabase, QPainter
from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput

from .config import load_settings, save_settings
from .db import init_db, find_databases, DB_SUFFIX
from .tts import TTSWorker
from .dialogs import DynamicInputDialog, NewDatabaseDialog
from .graphics import DropZoneItem, FlashCardItem, HAS_WEBENGINE
from .markdown_utils import markdown_to_plain_text


class BarskyApp(QMainWindow):
    """Main application window for the KGB 5-Box SRS System."""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("KGB 5-Box SRS System")

        self.settings = load_settings()
        self.resize(self.settings["width"], self.settings["height"])

        self.current_lang = None
        self.conn = None
        self.current_db_path = None
        self.current_card = None
        self.cards_due = []
        self.is_current_flipped = False
        self.review_mode = ""

        self.tts_worker = None

        self.player = QMediaPlayer()
        self.audio_output = QAudioOutput()
        self.player.setAudioOutput(self.audio_output)

        self.setup_ui()
        self.apply_font_settings()

        # Auto-load the default database if set
        default_db = self.settings.get("default_database", "")
        if default_db:
            for display, path in find_databases():
                if path == default_db and os.path.exists(default_db):
                    self.current_db_path = default_db
                    self.current_lang = display
                    self.db_btn.setText(f"📂 {display}")
                    self.load_database(silent=True)
                    break

    # ------------------------------------------------------------------
    # Settings
    # ------------------------------------------------------------------
    def _save_settings(self):
        save_settings(self.settings)

    def closeEvent(self, event):
        self.settings["width"] = self.width()
        self.settings["height"] = self.height()
        self._save_settings()
        event.accept()

    # ------------------------------------------------------------------
    # Font / Styling
    # ------------------------------------------------------------------
    def apply_font_settings(self):
        font_family = self.settings.get("font_family", "Arial")
        font_size = self.settings.get("font_size", 14)

        font = QFont(font_family, font_size)
        QApplication = __import__("PyQt6.QtWidgets", fromlist=["QApplication"]).QApplication
        QApplication.setFont(font)

        dyn_padding = max(10, int(font_size * 0.8))

        btn_styles = {
            "start_btn": "background-color: #4CAF50; color: white; border-radius: 5px;",
            "force_seq_btn": "background-color: #FF9800; color: white; border-radius: 5px;",
            "restart_review_btn": "background-color: #1E88E5; color: white; border-radius: 5px;",
            "force_rev_btn": "background-color: #F4511E; color: white; border-radius: 5px;",
        }

        for attr_name, style_base in btn_styles.items():
            btn = getattr(self, attr_name, None)
            if btn is not None:
                btn.setStyleSheet(
                    f"{style_base} "
                    f"padding: {dyn_padding}px; font-family: '{font_family}'; "
                    f"font-size: {font_size + 2}px; font-weight: bold;"
                )

    # ------------------------------------------------------------------
    # Database Menu
    # ------------------------------------------------------------------
    @staticmethod
    def build_db_menu(parent_menu):
        """Build a hierarchical QMenu from the database directory structure."""
        dbs = find_databases()
        if not dbs:
            no_action = parent_menu.addAction("(no databases found)")
            no_action.setEnabled(False)
            return parent_menu

        # Build a tree: {part: {subtree | leaf_path}}
        tree = {}
        for display, full_path in dbs:
            parts = display.replace("\\", "/").split("/")
            node = tree
            for part in parts[:-1]:
                if part not in node:
                    node[part] = {}
                node = node[part]
            node[parts[-1]] = full_path  # leaf

        def populate_menu(menu, subtree):
            items = sorted(
                subtree.items(),
                key=lambda kv: (not isinstance(kv[1], dict), kv[0].lower()),
            )
            for name, value in items:
                if isinstance(value, dict):
                    sub = QMenu(name, menu)
                    populate_menu(sub, value)
                    menu.addMenu(sub)
                else:
                    action = menu.addAction(name)
                    action.setData(value)  # full path
            return menu

        return populate_menu(parent_menu, tree)

    # ------------------------------------------------------------------
    # UI Setup
    # ------------------------------------------------------------------
    def setup_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)

        # --- Top bar ---
        top_frame = QWidget()
        top_layout = QHBoxLayout(top_frame)
        top_layout.setContentsMargins(0, 0, 0, 0)

        top_layout.addWidget(QLabel("Database:"))

        self.db_btn = QPushButton("📂 Select Database")
        self.db_btn.setStyleSheet(
            "text-align: left; padding: 6px 12px; font-weight: bold;"
        )
        self.db_btn.clicked.connect(self.show_db_menu)
        top_layout.addWidget(self.db_btn)

        self.new_db_btn = QPushButton("＋ New Database")
        self.new_db_btn.setStyleSheet(
            "background-color: #4CAF50; color: white; padding: 6px 12px; "
            "font-weight: bold; border-radius: 4px;"
        )
        self.new_db_btn.clicked.connect(self.create_new_database)
        top_layout.addWidget(self.new_db_btn)

        self.random_checkbox = QCheckBox("Review Randomly")
        self.random_checkbox.setEnabled(False)
        self.random_checkbox.stateChanged.connect(self.on_random_toggled)
        self.random_checkbox.setToolTip(
            "If unchecked, cards are reviewed in the order they were added."
        )
        top_layout.addWidget(self.random_checkbox)

        top_layout.addStretch()

        add_btn = QPushButton("Add Word")
        add_btn.clicked.connect(self.add_word)
        browse_btn = QPushButton("Browse and Edit")
        browse_btn.clicked.connect(self.browse_cards)
        settings_btn = QPushButton("Settings")
        settings_btn.clicked.connect(self.open_settings_window)

        top_layout.addWidget(add_btn)
        top_layout.addWidget(browse_btn)
        top_layout.addWidget(settings_btn)
        main_layout.addWidget(top_frame)

        # --- Canvas ---
        self.scene = QGraphicsScene()
        self.view = QGraphicsView(self.scene)
        self.view.setStyleSheet("background-color: #cfcfcf;")
        self.view.setRenderHint(QPainter.RenderHint.Antialiasing)
        main_layout.addWidget(self.view)

        # --- Review buttons ---
        self.start_btn = QPushButton("Start Daily Review")
        self.start_btn.clicked.connect(self.start_review)
        main_layout.addWidget(self.start_btn)

        forced_review_layout = QHBoxLayout()

        self.force_seq_btn = QPushButton("Next Item")
        self.force_seq_btn.clicked.connect(
            lambda: self.start_forced_review(direction="ASC")
        )

        self.restart_review_btn = QPushButton("Restart Current Review (1st Item)")
        self.restart_review_btn.clicked.connect(self.restart_current_review)

        self.force_rev_btn = QPushButton("Previous Item")
        self.force_rev_btn.clicked.connect(
            lambda: self.start_forced_review(direction="DESC")
        )

        forced_review_layout.addWidget(self.force_seq_btn)
        forced_review_layout.addWidget(self.restart_review_btn)
        forced_review_layout.addWidget(self.force_rev_btn)
        main_layout.addLayout(forced_review_layout)

        self.card_ui = None
        self.incorrect_zone = None
        self.correct_zone = None

    # ------------------------------------------------------------------
    # Database selection
    # ------------------------------------------------------------------
    def show_db_menu(self):
        """Show the hierarchical database selection menu below the button."""
        menu = QMenu(self)
        self.build_db_menu(menu)

        # Connect all leaf actions (those with data)
        def connect_menu(m):
            for action in m.actions():
                if action.menu():
                    connect_menu(action.menu())
                elif action.data():
                    action.triggered.connect(
                        lambda checked, a=action: self.select_database(a)
                    )

        connect_menu(menu)

        pos = self.db_btn.mapToGlobal(self.db_btn.rect().bottomLeft())
        menu.exec(pos)

    def select_database(self, action):
        """Called when a database is selected from the menu."""
        db_path = action.data()
        if not db_path:
            return
        for display, path in find_databases():
            if path == db_path:
                self.current_db_path = db_path
                self.current_lang = display
                self.db_btn.setText(f"📂 {display}")
                self.load_database(silent=False)
                return

    def create_new_database(self):
        """Open dialog to create a new database."""
        dialog = NewDatabaseDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            new_path = dialog.result_path
            if new_path:
                conn = init_db(new_path)
                conn.close()
                display = dialog.result_display
                self.current_db_path = new_path
                self.current_lang = display
                self.db_btn.setText(f"📂 {display}")
                self.load_database(silent=False)

    # ------------------------------------------------------------------
    # Database load
    # ------------------------------------------------------------------
    def load_database(self, silent=False):
        """Load a database from the current path."""
        if not self.current_db_path:
            if not silent:
                QMessageBox.warning(self, "Error", "Please select a database first.")
            return

        if not os.path.exists(self.current_db_path):
            if not silent:
                QMessageBox.warning(
                    self, "Error", f"Database file not found:\n{self.current_db_path}"
                )
            return

        if self.conn:
            self.conn.close()

        self.conn = init_db(self.current_db_path)

        c = self.conn.cursor()
        c.execute("SELECT value FROM settings WHERE key = 'random_review'")
        res = c.fetchone()

        is_random = True
        if res:
            is_random = res[0] == "1"

        self.random_checkbox.blockSignals(True)
        self.random_checkbox.setChecked(is_random)
        self.random_checkbox.setEnabled(True)
        self.random_checkbox.blockSignals(False)

        self.current_card = None
        self.cards_due = []
        self.review_mode = ""

        self.randomize_box_five()

        if not silent:
            QMessageBox.information(self, "Success", f"Loaded database: {self.current_lang}")
            if "Math" in self.current_lang or "LaTeX" in self.current_lang:
                if not HAS_WEBENGINE:
                    QMessageBox.warning(
                        self,
                        "Notice",
                        "For Markdown + MathJax rendering, install PyQt6-WebEngine:\n\n"
                        "pip install PyQt6-WebEngine",
                    )

        self.scene.clear()
        self.redraw_canvas()

    def randomize_box_five(self):
        """Randomly pull a 5% chance of a mastered card back for review."""
        c = self.conn.cursor()
        c.execute("SELECT id FROM cards WHERE box = 5")
        mastered_cards = c.fetchall()
        if mastered_cards and random.random() < 0.05:
            target = random.choice(mastered_cards)[0]
            today_str = datetime.date.today().isoformat()
            c.execute(
                "UPDATE cards SET box = 1, next_review = ? WHERE id = ?",
                (today_str, target),
            )
            self.conn.commit()

    def on_random_toggled(self, state):
        if not self.conn:
            return
        is_random = self.random_checkbox.isChecked()
        c = self.conn.cursor()
        c.execute(
            "UPDATE settings SET value = ? WHERE key = 'random_review'",
            ("1" if is_random else "0",),
        )
        self.conn.commit()

    # ------------------------------------------------------------------
    # Canvas
    # ------------------------------------------------------------------
    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.redraw_canvas()

    def redraw_canvas(self):
        self.scene.clear()
        self.card_ui = None

        self.scene.setSceneRect(
            0, 0, self.view.width() - 5, self.view.height() - 5
        )
        w = self.scene.width()
        h = self.scene.height()

        zone_y = h - 100
        zone_h = 80
        zone_w = max(260, w * 0.3)
        margin = 50

        self.incorrect_zone = DropZoneItem(
            margin,
            zone_y,
            zone_w,
            zone_h,
            QPen(QColor("red")),
            QBrush(QColor("#ffcccc")),
            "<div align='center'><b>Click or Drop Here</b><br>"
            "if <span style='color:red;'>INCORRECT</span><br>"
            "(Drops to Box 1 or 3)</div>",
            False,
            self,
        )
        self.scene.addItem(self.incorrect_zone)

        self.correct_zone = DropZoneItem(
            w - margin - zone_w,
            zone_y,
            zone_w,
            zone_h,
            QPen(QColor("green")),
            QBrush(QColor("#ccffcc")),
            "<div align='center'><b>Click or Drop Here</b><br>"
            "if <span style='color:green;'>CORRECT</span><br>"
            "(Advances 1 Box)</div>",
            True,
            self,
        )
        self.scene.addItem(self.correct_zone)

        if self.current_card:
            self.draw_card_ui()

    # ------------------------------------------------------------------
    # TTS
    # ------------------------------------------------------------------
    def speak_text(self, text, btn):
        btn.setEnabled(False)
        btn.setText("⏳ Preparing...")

        voice = self.settings.get("tts_voice", "en-US-AvaMultilingualNeural")
        self.tts_worker = TTSWorker(text, voice)

        def on_finished(file_path):
            self.player.setSource(QUrl.fromLocalFile(file_path))
            self.player.play()
            btn.setEnabled(True)
            btn.setText("🔊 Listen")

        def on_error(err):
            QMessageBox.warning(self, "TTS Error", f"Audio Error: {err}")
            btn.setEnabled(True)
            btn.setText("🔊 Listen")

        self.tts_worker.finished.connect(on_finished)
        self.tts_worker.error.connect(on_error)
        self.tts_worker.start()

    # ------------------------------------------------------------------
    # Add / Browse cards
    # ------------------------------------------------------------------
    def add_word(self):
        if not self.conn:
            QMessageBox.warning(self, "Error", "Load a database first.")
            return

        front_dialog = DynamicInputDialog(
            self,
            "Add New Word/Phrase",
            "Enter the word/phrase (Front). Markdown and MathJax are supported during review:",
        )
        if front_dialog.exec() != QDialog.DialogCode.Accepted or not front_dialog.text_value:
            return

        front = front_dialog.text_value
        c = self.conn.cursor()
        c.execute(
            "SELECT id, front, back, box FROM cards WHERE front = ? COLLATE NOCASE",
            (front,),
        )
        existing_card = c.fetchone()

        today_str = datetime.date.today().isoformat()

        if existing_card:
            card_id, ex_front, ex_back, ex_box = existing_card
            msg = (
                f"'{ex_front}' is already in your database (Box {ex_box}).\n\n"
                "Opening Edit window. Card will reset to Box 1."
            )
            QMessageBox.information(self, "Already Exists", msg)

            edit_front_dialog = DynamicInputDialog(self, "Edit Word", "Front:", ex_front)
            if edit_front_dialog.exec() != QDialog.DialogCode.Accepted or not edit_front_dialog.text_value:
                return
            new_front = edit_front_dialog.text_value

            dialog = DynamicInputDialog(
                self,
                "Edit Translation",
                "Enter the translation, meanings, or sample sentences. Markdown and MathJax are supported during review:",
                ex_back,
            )
            if dialog.exec() == QDialog.DialogCode.Accepted and dialog.text_value:
                c.execute(
                    "UPDATE cards SET front=?, back=?, box=1, next_review=? WHERE id=?",
                    (new_front, dialog.text_value, today_str, card_id),
                )
                self.conn.commit()
                QMessageBox.information(self, "Updated", "Card updated and moved to Box 1.")

                if self.current_card and str(self.current_card[0]) == str(card_id):
                    self.current_card = (card_id, new_front, dialog.text_value, 1)
                    self.is_current_flipped = False
                    if self.card_ui:
                        self.scene.removeItem(self.card_ui)
                        self.card_ui = None
                    self.draw_card_ui()
                elif self.current_card is not None:
                    self.cards_due = [
                        cf for cf in self.cards_due if cf[0] != card_id
                    ]
                    self.cards_due.insert(0, self.current_card)
                    self.current_card = (card_id, new_front, dialog.text_value, 1)
                    self.is_current_flipped = False
                    if self.card_ui:
                        self.scene.removeItem(self.card_ui)
                        self.card_ui = None
                    self.draw_card_ui()
        else:
            dialog = DynamicInputDialog(
                self,
                "Add Translation",
                "Enter the translation, meanings, or sample sentences. Markdown and MathJax are supported during review:",
            )
            if dialog.exec() == QDialog.DialogCode.Accepted and dialog.text_value:
                c.execute(
                    "INSERT INTO cards (front, back, box, next_review) VALUES (?, ?, 1, ?)",
                    (front, dialog.text_value, today_str),
                )
                card_id = c.lastrowid
                self.conn.commit()
                QMessageBox.information(self, "Added", "Word added to Box 1.")

                if self.current_card is not None:
                    self.cards_due.insert(0, self.current_card)
                    self.current_card = (card_id, front, dialog.text_value, 1)
                    self.is_current_flipped = False
                    if self.card_ui:
                        self.scene.removeItem(self.card_ui)
                        self.card_ui = None
                    self.draw_card_ui()

    def browse_cards(self):
        if not self.conn:
            return

        dialog = QDialog(self)
        dialog.setWindowTitle(f"Browse Cards: {self.current_lang}")
        dialog.resize(800, 600)
        layout = QVBoxLayout(dialog)

        filter_layout = QHBoxLayout()
        filter_label = QLabel("Filter:")
        filter_input = QLineEdit()
        filter_input.setPlaceholderText(
            "Search keywords (use ' AND ' / ' OR ' for multiple terms, e.g. 'math AND theorem')"
        )
        filter_layout.addWidget(filter_label)
        filter_layout.addWidget(filter_input)
        layout.addLayout(filter_layout)

        table = QTableWidget()
        table.setColumnCount(4)
        table.setHorizontalHeaderLabels(
            ["ID", "Front (Word/Phrase)", "Box", "Next Review Date"]
        )
        table.verticalHeader().setVisible(False)
        table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.ResizeToContents
        )
        table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.ResizeToContents
        )
        table.horizontalHeader().setSectionResizeMode(
            3, QHeaderView.ResizeMode.ResizeToContents
        )
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        layout.addWidget(table)

        def refresh_list():
            table.setRowCount(0)
            filter_text = filter_input.text().strip()

            c = self.conn.cursor()
            c.execute("SELECT id, front, back, box, next_review FROM cards")

            for row_data in c.fetchall():
                card_id, front, back, box, next_review = row_data

                if filter_text:
                    search_content = f"{front}\n{back}".lower()

                    or_parts = re.split(r"\s+OR\s+", filter_text, flags=re.IGNORECASE)
                    matched_any_or = False

                    for or_part in or_parts:
                        and_parts = re.split(
                            r"\s+AND\s+", or_part, flags=re.IGNORECASE
                        )
                        matched_all_and = True

                        for and_part in and_parts:
                            kw = and_part.strip().lower()
                            if kw and kw not in search_content:
                                matched_all_and = False
                                break

                        if matched_all_and:
                            matched_any_or = True
                            break

                    if not matched_any_or:
                        continue

                row_idx = table.rowCount()
                table.insertRow(row_idx)
                table.setItem(row_idx, 0, QTableWidgetItem(str(card_id)))
                table.setItem(row_idx, 1, QTableWidgetItem(str(front)))
                table.setItem(row_idx, 2, QTableWidgetItem(str(box)))
                table.setItem(row_idx, 3, QTableWidgetItem(str(next_review)))

        filter_input.textChanged.connect(refresh_list)
        refresh_list()

        btn_layout = QHBoxLayout()
        edit_btn = QPushButton("Edit Selected")
        del_btn = QPushButton("Delete Selected")
        del_btn.setStyleSheet("background-color: #ffcccc;")
        btn_layout.addWidget(edit_btn)
        btn_layout.addWidget(del_btn)
        layout.addLayout(btn_layout)

        def on_edit():
            selected = table.selectedItems()
            if not selected:
                return
            card_id = selected[0].text()

            c = self.conn.cursor()
            c.execute("SELECT front, back FROM cards WHERE id=?", (card_id,))
            card = c.fetchone()

            new_front_dialog = DynamicInputDialog(dialog, "Edit Word", "Front:", card[0])
            if new_front_dialog.exec() != QDialog.DialogCode.Accepted or not new_front_dialog.text_value:
                return
            new_front = new_front_dialog.text_value

            ml_dialog = DynamicInputDialog(
                dialog,
                "Edit Translation",
                "Enter the translation, meanings, or sample sentences. "
                "Markdown and MathJax are supported during review:",
                card[1],
            )
            if ml_dialog.exec() == QDialog.DialogCode.Accepted and ml_dialog.text_value:
                today_str = datetime.date.today().isoformat()
                c.execute(
                    "UPDATE cards SET front=?, back=?, box=1, next_review=? WHERE id=?",
                    (new_front, ml_dialog.text_value, today_str, card_id),
                )
                self.conn.commit()
                refresh_list()
                QMessageBox.information(
                    dialog, "Updated",
                    "Card has been updated and moved back to Box 1 for review today.",
                )

                if self.current_card and str(self.current_card[0]) == str(card_id):
                    self.current_card = (
                        int(card_id),
                        new_front,
                        ml_dialog.text_value,
                        1,
                    )
                    self.is_current_flipped = False
                    if self.card_ui:
                        self.scene.removeItem(self.card_ui)
                        self.card_ui = None
                    self.draw_card_ui()
                elif self.current_card is not None:
                    self.cards_due = [
                        cf for cf in self.cards_due if cf[0] != int(card_id)
                    ]
                    self.cards_due.append(
                        (int(card_id), new_front, ml_dialog.text_value, 1)
                    )

        def on_delete():
            selected = table.selectedItems()
            if not selected:
                return
            card_id = selected[0].text()
            reply = QMessageBox.question(
                dialog,
                "Confirm",
                "Delete this card?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply == QMessageBox.StandardButton.Yes:
                self.conn.cursor().execute("DELETE FROM cards WHERE id=?", (card_id,))
                self.conn.commit()
                refresh_list()

                if self.current_card and str(self.current_card[0]) == str(card_id):
                    self.show_next_card()

        edit_btn.clicked.connect(on_edit)
        del_btn.clicked.connect(on_delete)
        table.itemDoubleClicked.connect(lambda item: on_edit())

        dialog.exec()

    # ------------------------------------------------------------------
    # Review Flow
    # ------------------------------------------------------------------
    def start_review(self):
        if not self.conn:
            return

        self.review_mode = "daily"

        if self.current_card is not None:
            if self.card_ui:
                self.scene.removeItem(self.card_ui)
                self.card_ui = None
            self.current_card = None

        today_str = datetime.date.today().isoformat()

        c = self.conn.cursor()
        c.execute(
            "SELECT id, front, back, box FROM cards WHERE next_review <= ?",
            (today_str,),
        )
        self.cards_due = c.fetchall()

        if self.random_checkbox.isChecked():
            random.shuffle(self.cards_due)
        else:
            self.cards_due.sort(key=lambda x: x[0])

        if not self.cards_due:
            QMessageBox.information(self, "Done", "No cards due for review today!")
            self.review_mode = ""
            return

        self.show_next_card()

    def start_forced_review(self, direction="ASC", restart=False):
        if not self.conn:
            QMessageBox.warning(self, "Error", "Load a database first.")
            return

        target_mode = "force_seq" if direction == "ASC" else "force_rev"

        if restart:
            current_id = None
        elif self.current_card is not None:
            current_id = self.current_card[0]
        else:
            current_id = 0

        if self.current_card is not None:
            if self.card_ui:
                self.scene.removeItem(self.card_ui)
                self.card_ui = None
            self.current_card = None

        self.review_mode = target_mode

        c = self.conn.cursor()

        if current_id is not None and current_id != 0:
            if direction == "ASC":
                query = (
                    "SELECT id, front, back, box FROM cards WHERE id > ? ORDER BY id ASC"
                )
            else:
                query = (
                    "SELECT id, front, back, box FROM cards WHERE id < ? ORDER BY id DESC"
                )
            c.execute(query, (current_id,))
            self.cards_due = c.fetchall()

            if not self.cards_due:
                wrap_query = (
                    f"SELECT id, front, back, box FROM cards ORDER BY id {direction}"
                )
                c.execute(wrap_query)
                self.cards_due = c.fetchall()
        else:
            query = f"SELECT id, front, back, box FROM cards ORDER BY id {direction}"
            c.execute(query)
            self.cards_due = c.fetchall()

        if not self.cards_due:
            QMessageBox.information(self, "Empty", "There are no cards in the database.")
            self.review_mode = ""
            return

        self.show_next_card()

    def restart_current_review(self):
        if not self.conn:
            return
        self.start_forced_review(direction="ASC", restart=True)

    def show_next_card(self):
        if self.card_ui:
            self.scene.removeItem(self.card_ui)
            self.card_ui = None

        while self.cards_due:
            stale_card = self.cards_due.pop(0)
            card_id = stale_card[0]

            c = self.conn.cursor()
            c.execute("SELECT id, front, back, box FROM cards WHERE id = ?", (card_id,))
            fresh_card = c.fetchone()

            if fresh_card:
                self.current_card = fresh_card
                self.is_current_flipped = False
                self.draw_card_ui()
                return

        if self.review_mode == "force_seq":
            self.start_forced_review(direction="ASC", restart=True)
            return
        elif self.review_mode == "force_rev":
            self.start_forced_review(direction="DESC", restart=True)
            return

        QMessageBox.information(self, "Done", "You have finished your reviews.")
        self.current_card = None
        self.review_mode = ""

    def draw_card_ui(self):
        if not self.current_card:
            return

        card_id, front, back, box = self.current_card

        w = max(400, self.scene.width())
        h = max(400, self.scene.height())

        cw = int(w * 0.75)
        ch = int(h * 0.75)
        cx = w / 2
        cy = (h - 100) / 2

        self.card_ui = FlashCardItem(self, cx, cy, cw, ch)

        metadata_md = f"**Box {box}** | ID: `{card_id}`"

        if self.is_current_flipped:
            spoken_front = markdown_to_plain_text(front)
            spoken_back = markdown_to_plain_text(back)
            spoken_text = f"{spoken_front}. {spoken_back}".strip()
            display_md = f"{metadata_md}\n\n{front}\n\n---\n\n{back}"
            self.card_ui.set_text(display_md, True, spoken_text)
        else:
            spoken_front = markdown_to_plain_text(front)
            display_md = f"{metadata_md}\n\n{front}"
            self.card_ui.set_text(display_md, False, spoken_front)

        self.scene.addItem(self.card_ui)

    def flip_card(self):
        if not self.current_card:
            return

        self.is_current_flipped = True
        card_id, front, back, box = self.current_card

        metadata_md = f"**Box {box}** | ID: `{card_id}`"
        display_md = f"{metadata_md}\n\n{front}\n\n---\n\n{back}"

        spoken_front = markdown_to_plain_text(front)
        spoken_back = markdown_to_plain_text(back)
        spoken_text = f"{spoken_front}. {spoken_back}".strip()

        self.card_ui.set_text(display_md, True, spoken_text)

    def check_card_drop(self, card_item):
        if not self.incorrect_zone or not self.correct_zone:
            return
        card_rect = card_item.sceneBoundingRect()
        inc_rect = self.incorrect_zone.sceneBoundingRect()
        cor_rect = self.correct_zone.sceneBoundingRect()

        if card_rect.intersects(inc_rect):
            QTimer.singleShot(0, lambda: self.process_answer(correct=False))
        elif card_rect.intersects(cor_rect):
            QTimer.singleShot(0, lambda: self.process_answer(correct=True))
        else:
            card_item.setPos(
                self.scene.width() / 2, (self.scene.height() - 100) / 2
            )

    def process_answer(self, correct):
        if not self.current_card:
            return
        card_id, _, _, current_box = self.current_card
        today = datetime.date.today()

        new_box = min(current_box + 1, 5) if correct else (3 if current_box >= 3 else 1)
        intervals = {1: 1, 2: 3, 3: 7, 4: 30, 5: 365}
        next_review_str = (
            today + datetime.timedelta(days=intervals[new_box])
        ).isoformat()

        c = self.conn.cursor()
        c.execute(
            "UPDATE cards SET box = ?, next_review = ? WHERE id = ?",
            (new_box, next_review_str, card_id),
        )
        self.conn.commit()
        self.show_next_card()

    # ------------------------------------------------------------------
    # Settings Dialog
    # ------------------------------------------------------------------
    def open_settings_window(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("App Settings")
        layout = QFormLayout(dialog)

        w_input = QSpinBox()
        w_input.setRange(400, 3000)
        w_input.setValue(self.settings["width"])

        h_input = QSpinBox()
        h_input.setRange(400, 3000)
        h_input.setValue(self.settings["height"])

        font_combo = QComboBox()
        font_combo.addItems(QFontDatabase.families())
        font_combo.setCurrentText(self.settings["font_family"])

        size_input = QSpinBox()
        size_input.setRange(8, 36)
        size_input.setValue(self.settings["font_size"])

        lang_input = QLineEdit(self.settings.get("default_database", ""))
        lang_input.setPlaceholderText("Database path (set by selecting a database)")

        tts_input = QLineEdit(
            self.settings.get("tts_voice", "en-US-AvaMultilingualNeural")
        )
        tts_input.setPlaceholderText("e.g. en-US-AvaMultilingualNeural")

        layout.addRow("Window Width:", w_input)
        layout.addRow("Window Height:", h_input)
        layout.addRow("Font Family:", font_combo)
        layout.addRow("Font Size:", size_input)
        layout.addRow("Default Database:", lang_input)
        layout.addRow("TTS Voice (Edge-TTS):", tts_input)

        save_btn = QPushButton("Save & Apply")
        save_btn.setStyleSheet("background-color: #ccffcc;")
        layout.addRow(save_btn)

        def save_and_apply():
            self.settings["width"] = w_input.value()
            self.settings["height"] = h_input.value()
            self.settings["font_family"] = font_combo.currentText()
            self.settings["font_size"] = size_input.value()
            self.settings["default_database"] = lang_input.text().strip()
            self.settings["tts_voice"] = tts_input.text().strip()

            self._save_settings()
            self.resize(self.settings["width"], self.settings["height"])
            self.apply_font_settings()
            dialog.accept()

        save_btn.clicked.connect(save_and_apply)
        dialog.exec()
