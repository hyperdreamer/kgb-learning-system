"""Main application window – BarskyApp."""

import os
import sqlite3
import datetime
import random
import re

from PyQt6.QtWidgets import (
    QMainWindow,
    QDialog,
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
    QFileDialog,
    QApplication,
)
from PyQt6.QtCore import Qt, QPointF, QTimer, QUrl
from PyQt6.QtGui import QFont, QFontDatabase, QPainter, QPen, QColor, QBrush, QIcon, QPixmap, QAction
from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput

from .config import load_settings, save_settings, DIR_DB
from .db import init_db, find_databases, DB_SUFFIX
from .tts import TTSWorker, VoiceListWorker
from .dialogs import DynamicInputDialog  # still used for knowledge cards
from .forms import SentenceCardDialog, DBCreationDialog, WordPhraseCardDialog
from .graphics import DropZoneItem, FlashCardItem, HAS_WEBENGINE
from .markdown_utils import markdown_to_plain_text
from .schema import (
    ensure_unfamiliar_items_table, migrate_unfamiliar_items_meaning,
    insert_sentence_card, get_sentence_card, update_sentence_card,
    find_duplicate_sentence_card, validate_db_name, safe_db_filename,
    resolve_db_path,
)
from .catalog import (DatabaseType, DatabaseCategory, infer_database_type,
                       read_database_type, write_database_type,
                       build_catalog_tree, DB_DIR_LANGUAGE_SENTENCE,
                       DB_DIR_LANGUAGE_WORD_PHRASE, DB_DIR_KNOWLEDGE)
from .validation import validate_unfamiliar_items, deduplicate_unfamiliar_items
from .search import search_sentence_cards, search_word_phrase_cards
from .ai_provider import AIProviderConfig

_DB_MENU_STYLESHEET = (
    "QMenu::item {"
    " padding-left: 12px;"
    " padding-right: 28px;"
    " padding-top: 6px;"
    " padding-bottom: 6px;"
    " }"
)


def _make_eye_icons(size=20):
    """Return (hidden_icon, visible_icon) — distinct QIcon objects.

    Painted with QPainter — no external assets, no Unicode emoji.

    hidden_icon:  eye outline + diagonal slash (password hidden).
    visible_icon: eye outline + centered hollow iris (plaintext visible).

    Returns two separate QIcon instances because QLineEdit renders
    QAction icons without consulting QIcon state (Off/On); explicit
    setIcon() switching is required on toggle.
    """
    from PyQt6.QtGui import QPainterPath

    grey = QColor(0x75, 0x75, 0x75)
    pen = QPen(grey, 1.5)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)

    margin = max(2, int(size * 0.22))
    cx = size / 2.0
    cy = size / 2.0
    ew = size / 2.0 - margin  # eye half-width
    eh = size / 2.0 - margin  # eye half-height

    def _eye_outline():
        p = QPainterPath()
        p.moveTo(cx - ew, cy)
        p.quadTo(cx, cy - eh, cx + ew, cy)
        p.quadTo(cx, cy + eh, cx - ew, cy)
        p.closeSubpath()
        return p

    def _render(draw_cb):
        pm = QPixmap(size, size)
        pm.fill(Qt.GlobalColor.transparent)
        qp = QPainter(pm)
        qp.setRenderHint(QPainter.RenderHint.Antialiasing)
        qp.setPen(pen)
        draw_cb(qp)
        qp.end()
        return pm

    # ── hidden (slashed eye): eye outline + diagonal slash ──
    def _draw_hidden(qp):
        qp.drawPath(_eye_outline())
        slop = margin * 0.7
        x1 = int(cx + ew - slop)
        y1 = int(cy - eh + slop)
        x2 = int(cx - ew + slop)
        y2 = int(cy + eh - slop)
        qp.drawLine(x1, y1, x2, y2)

    # ── visible (open eye): eye outline + centered outlined iris ──
    def _draw_visible(qp):
        qp.drawPath(_eye_outline())
        # No brush → outline only; pen already set by _render.
        pr = size * 0.14  # iris radius
        qp.drawEllipse(QPointF(cx, cy), pr, pr)

    hidden_icon = QIcon(_render(_draw_hidden))
    visible_icon = QIcon(_render(_draw_visible))
    return hidden_icon, visible_icon


class SecretLineEdit(QLineEdit):
    """Password input with a visibility toggle action rendered inside the
    QLineEdit at the trailing edge."""

    def __init__(self, text="", parent=None):
        super().__init__(text, parent)
        self.setEchoMode(QLineEdit.EchoMode.Password)

        self._icon_hidden, self._icon_visible = _make_eye_icons()
        self._toggle_action = QAction(self._icon_hidden, "Show API key", self)
        self._toggle_action.setCheckable(True)
        self._toggle_action.setToolTip("Show API key")

        self.addAction(self._toggle_action,
                       QLineEdit.ActionPosition.TrailingPosition)
        self._toggle_action.toggled.connect(self._on_toggled)

    def _on_toggled(self, checked):
        mode = QLineEdit.EchoMode.Normal if checked else QLineEdit.EchoMode.Password
        tip = "Hide API key" if checked else "Show API key"
        icon = self._icon_visible if checked else self._icon_hidden
        self.setEchoMode(mode)
        self._toggle_action.setToolTip(tip)
        self._toggle_action.setIcon(icon)
        self._toggle_action.setText(tip)


from .forms import SentenceCardDialog, DBCreationDialog, WordPhraseCardDialog


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

def _compute_display_path(db_path, db_type, legacy_display):
    """Compute the catalog display path for a database.

    For databases under canonical directories, builds from the canonical path.
    For legacy databases (e.g. db/Languages/English), prepends category/subtype
    to the legacy display.
    """
    from .catalog import display_path_for

    norm_path = os.path.normpath(db_path).replace("\\", "/")

    canon_dirs = {
        DatabaseType.LANGUAGE_SENTENCE: DB_DIR_LANGUAGE_SENTENCE.replace("\\", "/"),
        DatabaseType.LANGUAGE_WORD_PHRASE: DB_DIR_LANGUAGE_WORD_PHRASE.replace("\\", "/"),
        DatabaseType.KNOWLEDGE: DB_DIR_KNOWLEDGE.replace("\\", "/"),
    }
    canonical = canon_dirs.get(db_type, "")

    if canonical and canonical in norm_path:
        return display_path_for(db_path, db_type)

    # Legacy path: prepend category and, for language DBs, subtype.
    category = db_type.category_display
    legacy = legacy_display.replace("\\", "/")
    if db_type == DatabaseType.KNOWLEDGE:
        return os.path.join(category, legacy)
    return os.path.join(category, db_type.display, legacy)


def _open_and_infer_type(db_path):
    """Open a DB briefly to read or infer its type."""
    try:
        conn = sqlite3.connect(db_path)
        db_type = read_database_type(conn)
        conn.close()
        if db_type is not None:
            return db_type
    except Exception:
        pass
    return infer_database_type(db_path)


def _fetch_expressions_for_card(conn, card_id):
    """Fetch unfamiliar expressions for a sentence card.
    Returns list of (expression, meaning) tuples.
    """
    cur = conn.cursor()
    cur.execute(
        "SELECT expression, meaning FROM unfamiliar_items "
        "WHERE card_id=? ORDER BY id",
        (card_id,),
    )
    return [(r[0], r[1]) for r in cur.fetchall()]


def _expression_labels(items):
    """Return expression text from structured or legacy child items."""
    return [item[0] if isinstance(item, (tuple, list)) else item for item in items]

# ---------------------------------------------------------------------------
# BarskyApp
# ---------------------------------------------------------------------------

class BarskyApp(QMainWindow):
    """Main application window for the KGB 5-Box SRS System."""

    @staticmethod
    def _icon(name, fallback=""):
        icon = QIcon.fromTheme(name)
        return icon if not icon.isNull() else QIcon()

    def __init__(self):
        super().__init__()
        self.setWindowTitle("KGB 5-Box SRS System")

        self.settings = load_settings()
        self.resize(self.settings["width"], self.settings["height"])

        self.current_lang = None
        self.conn = None
        self.current_db_path = None
        self._db_type = None
        self.current_card = None
        self.cards_due = []
        self.is_current_flipped = False
        self.review_mode = ""

        self._paused_review_card = None
        self._paused_review_mode = ""

        # Daily-review session state
        self._daily_review_history = []       # cards graded this session
        self._daily_queue_snapshot = []       # full original due queue (Restart)

        # Paused-session deep state (preserved across close/resume)
        self._paused_cards_due = []
        self._paused_daily_queue = []
        self._paused_review_history = []

        self.tts_worker = None
        self.voice_worker = None

        self.player = QMediaPlayer()
        self.audio_output = QAudioOutput()
        self.player.setAudioOutput(self.audio_output)

        self.setup_ui()
        self.apply_font_settings()

        default_db = self.settings.get("default_database", "")
        if default_db:
            for display, path in find_databases():
                if path == default_db and os.path.exists(default_db):
                    self.current_db_path = default_db
                    self.current_lang = display
                    self.db_btn.setText(f"📂 {self._leaf_name(display)}")
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
        if self.current_db_path:
            self.settings["default_database"] = self.current_db_path
        self._save_settings()
        event.accept()

    # ------------------------------------------------------------------
    # Font / Styling
    # ------------------------------------------------------------------
    @staticmethod
    def _button_style(bg, hover=None):
        hover = hover or bg
        return (
            f"QPushButton {{"
            f"  background-color: {bg}; color: white; border: none; "
            f"  border-radius: 6px; padding: 8px 16px; font-weight: bold; "
            f"}}"
            f"QPushButton:hover {{ background-color: {hover}; }}"
            f"QPushButton:pressed {{ background-color: {bg}; }}"
            f"QPushButton:disabled {{"
            f"  background-color: #CFD8DC; color: #78909C; "
            f"}}"
        )

    def apply_font_settings(self):
        font_family = self.settings.get("font_family", "Arial")
        font_size = self.settings.get("font_size", 14)

        font = QFont(font_family, font_size)
        QApplication.setFont(font)

        dyn_pad = max(10, int(font_size * 0.8))
        fs = font_size + 2

        self.start_btn.setStyleSheet(
            self._button_style("#43A047", "#66BB6A") +
            f"padding: {dyn_pad}px; font-size: {fs}px;"
        )
        self.restart_review_btn.setStyleSheet(
            self._button_style("#1E88E5", "#42A5F5") +
            f"padding: {dyn_pad}px; font-size: {fs}px;"
        )
        self.previous_review_btn.setStyleSheet(
            self._button_style("#E53935", "#EF5350") +
            f"padding: {dyn_pad}px; font-size: {fs}px;"
        )

        self.delete_entry_btn.setStyleSheet(
            self._button_style("#D32F2F", "#F44336") +
            f"padding: {dyn_pad}px; font-size: {fs}px;"
        )

    # ------------------------------------------------------------------
    # Database Menu
    # ------------------------------------------------------------------
    def build_db_menu(self, parent_menu):
        """Build a hierarchical QMenu using catalog-based categories/subtypes."""
        dbs = find_databases()
        entries = []
        for display, full_path in dbs:
            db_type = _open_and_infer_type(full_path)
            dp = _compute_display_path(full_path, db_type, display)
            entries.append((dp, full_path, db_type))

        tree = build_catalog_tree(entries)
        current_path = getattr(self, "current_db_path", None)

        def populate_menu(menu, subtree):
            menu.setStyleSheet(_DB_MENU_STYLESHEET)
            items = sorted(
                subtree.items(),
                key=lambda kv: (
                    not isinstance(kv[1], dict),
                    kv[0].lower(),
                ),
            )
            for name, value in items:
                if isinstance(value, dict):
                    sub = QMenu(name, menu)
                    populate_menu(sub, value)
                    menu.addMenu(sub)
                else:
                    db_path, db_type = value
                    bullet = "● " if db_path == current_path else ""
                    label = f"{bullet}{name}"
                    action = menu.addAction(label)
                    action.setData(db_path)
            return menu

        return populate_menu(parent_menu, tree)

    @staticmethod
    def _menu_contains(menu, target_path):
        for action in menu.actions():
            if action.menu():
                if BarskyApp._menu_contains(action.menu(), target_path):
                    return True
            elif action.data() == target_path:
                return True
        return False

    @staticmethod
    def _expand_to_path(menu, target_path):
        for action in menu.actions():
            if action.menu():
                if BarskyApp._menu_contains(action.menu(), target_path):
                    menu.setActiveAction(action)
                    QTimer.singleShot(20, lambda m=action.menu(), t=target_path:
                                      BarskyApp._expand_to_path(m, t))
                    return
            elif action.data() == target_path:
                menu.setActiveAction(action)
                return

    # ------------------------------------------------------------------
    # UI Setup
    # ------------------------------------------------------------------
    def setup_ui(self):
        central_widget = QWidget()
        central_widget.setStyleSheet(
            "QWidget { background-color: #FAFAFA; }"
        )
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        main_layout.setSpacing(10)
        main_layout.setContentsMargins(10, 8, 10, 8)

        # --- Top bar ---
        top_frame = QWidget()
        top_frame.setStyleSheet(
            "QWidget { background-color: #ECEFF1; border-radius: 8px; }"
        )
        top_layout = QHBoxLayout(top_frame)
        top_layout.setContentsMargins(10, 6, 10, 6)
        top_layout.setSpacing(8)

        top_layout.addWidget(QLabel("Database:"))

        self.db_btn = QPushButton("📂 Select Database")
        self.db_btn.setIcon(self._icon("drive-harddisk"))
        self.db_btn.setStyleSheet(
            "QPushButton {"
            "  text-align: left; padding: 6px 14px; font-weight: bold;"
            "  background-color: #FFFFFF; border: 1px solid #CFD8DC;"
            "  border-radius: 6px;"
            "}"
            "QPushButton:hover { background-color: #F5F5F5; }"
        )
        self.db_btn.clicked.connect(self.show_db_menu)
        top_layout.addWidget(self.db_btn)

        self.new_db_btn = QPushButton(" New")
        self.new_db_btn.setIcon(self._icon("folder-new"))
        self.new_db_btn.setStyleSheet(
            "QPushButton {"
            "  background-color: #43A047; color: white; padding: 6px 14px;"
            "  font-weight: bold; border: none; border-radius: 6px;"
            "}"
            "QPushButton:hover { background-color: #66BB6A; }"
        )
        self.new_db_btn.clicked.connect(self.create_new_database)
        top_layout.addWidget(self.new_db_btn)

        self.random_checkbox = QCheckBox("Shuffle")
        self.random_checkbox.setEnabled(False)
        self.random_checkbox.stateChanged.connect(self.on_random_toggled)
        self.random_checkbox.setToolTip(
            "If unchecked, cards are reviewed in the order they were added."
        )
        top_layout.addWidget(self.random_checkbox)

        top_layout.addStretch()

        def action_btn(text, icon_name, handler):
            btn = QPushButton(text)
            btn.setIcon(self._icon(icon_name))
            btn.setStyleSheet(
                "QPushButton {"
                "  background-color: transparent; border: 1px solid #B0BEC5;"
                "  border-radius: 6px; padding: 6px 12px; font-weight: bold;"
                "}"
                "QPushButton:hover { background-color: #ECEFF1; }"
            )
            btn.clicked.connect(handler)
            return btn

        self.add_entry_btn = action_btn(" Add Entry", "list-add", self.add_word)
        top_layout.addWidget(self.add_entry_btn)

        self.delete_entry_btn = action_btn(" Delete Entry", "edit-delete", self.delete_current_card)
        self.delete_entry_btn.setEnabled(False)
        top_layout.addWidget(self.delete_entry_btn)

        top_layout.addWidget(
            action_btn(" Browse", "edit-find", self.browse_cards)
        )
        top_layout.addWidget(
            action_btn(" Settings", "preferences-system", self.open_settings_window)
        )
        main_layout.addWidget(top_frame)

        # --- Canvas ---
        self.view = QGraphicsView()
        self.view.setStyleSheet(
            "QGraphicsView {"
            "  background-color: #FAFAFA; border: 2px solid #E0E0E0;"
            "  border-radius: 10px;"
            "}"
        )
        self.view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.view.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.view.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.scene = QGraphicsScene(self)
        self.view.setScene(self.scene)
        self.view.setViewportUpdateMode(
            QGraphicsView.ViewportUpdateMode.BoundingRectViewportUpdate
        )

        self.view.setMinimumHeight(200)

        main_layout.addWidget(self.view, stretch=1)

        # --- Bottom buttons ---
        bottom_layout = QVBoxLayout()
        bottom_layout.setSpacing(6)

        self.start_btn = QPushButton(" Start Daily Review")
        self.start_btn.setIcon(self._icon("media-playback-start"))
        self.start_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.start_btn.clicked.connect(self._on_primary_button_clicked)
        main_layout.addWidget(self.start_btn)

        review_controls_layout = QHBoxLayout()
        review_controls_layout.setSpacing(6)

        self.restart_review_btn = QPushButton(" Restart")
        self.restart_review_btn.setIcon(self._icon("view-refresh"))
        self.restart_review_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.restart_review_btn.clicked.connect(self._restart_daily_review)

        self.previous_review_btn = QPushButton(" Previous")
        self.previous_review_btn.setIcon(self._icon("go-previous"))
        self.previous_review_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.previous_review_btn.clicked.connect(self._previous_daily_card)

        review_controls_layout.addWidget(self.restart_review_btn)
        review_controls_layout.addWidget(self.previous_review_btn)

        self.close_review_btn = QPushButton("×", self.view)
        self.close_review_btn.setToolTip("Close review")
        self.close_review_btn.setAccessibleName("Close review")
        self.close_review_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.close_review_btn.setEnabled(False)
        self.close_review_btn.setFixedSize(28, 28)
        self.close_review_btn.setStyleSheet(
            "QPushButton {"
            "  background-color: rgba(250, 250, 250, 200);"
            "  color: #757575;"
            "  border: 1px solid #E0E0E0;"
            "  border-radius: 4px;"
            "  font-size: 16px;"
            "  font-weight: bold;"
            "}"
            "QPushButton:hover {"
            "  background-color: #E0E0E0;"
            "  color: #424242;"
            "}"
            "QPushButton:pressed {"
            "  background-color: #BDBDBD;"
            "}"
            "QPushButton:disabled {"
            "  background-color: transparent;"
            "  color: #BDBDBD;"
            "  border: 1px solid transparent;"
            "}"
        )
        self.close_review_btn.clicked.connect(self.close_review)

        main_layout.addLayout(review_controls_layout)

        self.card_ui = None
        self.incorrect_zone = None
        self.correct_zone = None

        self._update_button_visibility()

    # ------------------------------------------------------------------
    # Database selection
    # ------------------------------------------------------------------
    def show_db_menu(self):
        menu = QMenu(self)
        self.build_db_menu(menu)

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

        if self.current_db_path and self._menu_contains(menu, self.current_db_path):
            QTimer.singleShot(10, lambda: self._expand_to_path(menu, self.current_db_path))

        menu.popup(pos)

    @staticmethod
    def _leaf_name(display):
        return display.replace("\\", "/").rsplit("/", 1)[-1]

    def select_database(self, action):
        db_path = action.data()
        if not db_path:
            return
        for display, path in find_databases():
            if path == db_path:
                self.current_db_path = db_path
                self.current_lang = display
                self.db_btn.setText(f"📂 {self._leaf_name(display)}")
                self.load_database(silent=False)
                return

    def create_new_database(self):
        """Show category/subtype selection dialog, then create DB with metadata."""
        dialog = DBCreationDialog(self, base_dir=DIR_DB)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        db_type = dialog.selected_type
        name = dialog.db_name

        if not validate_db_name(name):
            QMessageBox.warning(
                self, "Invalid Name",
                f"Database name '{name}' contains invalid characters.\n\n"
                "Names must not contain /, \\, .., NUL, or control characters."
            )
            return

        canon_map = {
            DatabaseType.LANGUAGE_SENTENCE: DB_DIR_LANGUAGE_SENTENCE,
            DatabaseType.LANGUAGE_WORD_PHRASE: DB_DIR_LANGUAGE_WORD_PHRASE,
            DatabaseType.KNOWLEDGE: DB_DIR_KNOWLEDGE,
        }
        subdir = canon_map[db_type]

        try:
            path = resolve_db_path(DIR_DB, subdir, name)
        except ValueError as e:
            QMessageBox.warning(self, "Invalid Name", str(e))
            return

        target_dir = os.path.dirname(path)
        os.makedirs(target_dir, exist_ok=True)

        if os.path.exists(path):
            QMessageBox.warning(
                self, "Exists",
                f"A database named '{name}' already exists in this location."
            )
            return

        conn = init_db(path)
        write_database_type(conn, db_type)
        if db_type == DatabaseType.LANGUAGE_SENTENCE:
            ensure_unfamiliar_items_table(conn)
            migrate_unfamiliar_items_meaning(conn)
        conn.close()

        display = os.path.join(subdir, name)

        self.current_db_path = path
        self.current_lang = display
        self.db_btn.setText(f"📂 {name}")
        self.load_database(silent=False)

    # ------------------------------------------------------------------
    # Database open / close
    # ------------------------------------------------------------------
    def load_database(self, silent=False):
        """Open the database, ensure metadata, and initialize review state."""
        if not self.current_db_path:
            if not silent:
                QMessageBox.warning(self, "Error", "Load a database first.")
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

        # --- Metadata inference / persistence ---
        db_type = read_database_type(self.conn)
        if db_type is None:
            db_type = infer_database_type(self.current_db_path)
            write_database_type(self.conn, db_type)

        self._db_type = db_type

        if db_type == DatabaseType.LANGUAGE_SENTENCE:
            ensure_unfamiliar_items_table(self.conn)
            migrate_unfamiliar_items_meaning(self.conn)

        # --- Restore random review ---
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
        self._paused_review_card = None
        self._paused_review_mode = ""
        self._daily_review_history = []
        self._daily_queue_snapshot = []
        self._paused_cards_due = []
        self._paused_daily_queue = []
        self._paused_review_history = []

        self.randomize_box_five()

        self._update_button_visibility()

        if not silent:
            QMessageBox.information(self, "Success", f"Loaded database: {self.current_lang}")
            if not HAS_WEBENGINE:
                if "Math" in self.current_lang or "LaTeX" in self.current_lang:
                    QMessageBox.warning(
                        self,
                        "Notice",
                        "For Markdown + MathJax rendering, install PyQt6-WebEngine:\n\n"
                        "pip install PyQt6-WebEngine",
                    )

        self.scene.clear()
        if self.isVisible():
            self.redraw_canvas()

    def randomize_box_five(self):
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
    def showEvent(self, event):
        super().showEvent(event)
        if not getattr(self, '_did_initial_canvas', False):
            self._did_initial_canvas = True
            QTimer.singleShot(0, self.redraw_canvas)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.redraw_canvas()

    def redraw_canvas(self):
        self.scene.clear()
        self.card_ui = None

        # Derive scene rect from viewport dimensions (not widget size)
        # so the scene matches the actual renderable area.
        vp_w = self.view.viewport().width()
        vp_h = self.view.viewport().height()
        self.scene.setSceneRect(0, 0, vp_w, vp_h)
        w = vp_w
        h = vp_h

        zone_h = 80
        # Clamp zone bottom anchor so the drop zone never starts
        # above the viewport origin when the view is too short.
        zone_y = max(0, h - 100)
        margin = 50

        max_zone_w = max(100, (w - 3 * margin) / 2)
        zone_w = min(max(260, int(w * 0.3)), int(max_zone_w))

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

        self._zone_y = zone_y

        if self.current_card:
            self.draw_card_ui()

        # Reposition the overlay close button at the top-right of the view.
        btn = self.close_review_btn
        btn_margin = 6
        btn_w = btn.width()
        btn_h = btn.height()
        btn.setGeometry(
            self.view.width() - btn_w - btn_margin,
            btn_margin,
            btn_w,
            btn_h,
        )
        btn.raise_()

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

        db_type = getattr(self, "_db_type", None)
        if db_type is None:
            return

        if db_type == DatabaseType.LANGUAGE_SENTENCE:
            self._add_sentence_card()
        elif db_type == DatabaseType.LANGUAGE_WORD_PHRASE:
            self._add_word_phrase_card()
        else:
            self._add_knowledge_card()

    def _add_sentence_card(self, edit_card_id=None):
        """Show the sentence-based card dialog."""
        if edit_card_id is not None:
            existing = get_sentence_card(self.conn, edit_card_id)
            if existing is None:
                return
            front, back, box, items = existing
            # Pass full (expression, meaning) pairs; the dialog will use
            # meanings to pre-populate meaning editors.
            dialog = SentenceCardDialog(
                self, "Edit Sentence Card", front, items, back,
                settings=self.settings,
            )
        else:
            dialog = SentenceCardDialog(
                self, "Add Sentence Card", settings=self.settings,
            )

        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        sentence = dialog.result_sentence
        items = dialog.result_items
        back = dialog.result_back

        # Duplicate detection for new cards
        if edit_card_id is None:
            dup_id = find_duplicate_sentence_card(self.conn, sentence, items)
            if dup_id is not None:
                reply = QMessageBox.question(
                    self, "Duplicate Detected",
                    "A card with the same sentence and expressions already exists.\n\n"
                    "Open it for editing instead?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                )
                if reply == QMessageBox.StandardButton.Yes:
                    return self._add_sentence_card(edit_card_id=dup_id)
                # else: continue creating new card

        if edit_card_id is not None:
            update_sentence_card(
                self.conn, edit_card_id,
                front=sentence, back=back, items=items,
            )
            QMessageBox.information(self, "Updated", "Card updated and moved to Box 1.")
            self._refresh_current_card(edit_card_id)
        else:
            card_id = insert_sentence_card(self.conn, sentence, items, back)
            QMessageBox.information(self, "Added", "Card added to Box 1.")
            self._show_new_card(card_id, sentence, back)

    def _add_word_phrase_card(self, edit_card_id=None, existing_front=""):
        """Show the word/phrase-based card dialog with inline AI meanings."""
        meanings_data = None

        if edit_card_id is not None:
            if not existing_front:
                c = self.conn.cursor()
                c.execute("SELECT front, back FROM cards WHERE id=?", (edit_card_id,))
                row = c.fetchone()
                if not row:
                    return
                existing_front = row[0]
                # Try to extract meaning/example pairs from existing back text
                meanings_data = self._parse_back_to_meanings(row[1])

            dialog = WordPhraseCardDialog(
                self,
                "Edit Word/Phrase",
                front=existing_front,
                meanings_data=meanings_data,
                settings=self.settings,
            )
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
            front = dialog.result_front
            back = dialog.result_back
            meanings = dialog.result_meanings
        else:
            dialog = WordPhraseCardDialog(
                self,
                "Add New Word/Phrase",
                settings=self.settings,
            )
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
            front = dialog.result_front
            back = dialog.result_back
            meanings = dialog.result_meanings

        if not front or not back:
            return

        c = self.conn.cursor()

        # Duplicate check for new cards
        if edit_card_id is None:
            c.execute(
                "SELECT id, front, back, box FROM cards WHERE front = ? COLLATE NOCASE",
                (front,),
            )
            existing_card = c.fetchone()
            if existing_card:
                card_id, ex_front, ex_back, ex_box = existing_card
                QMessageBox.information(
                    self, "Already Exists",
                    f"'{ex_front}' is already in your database (Box {ex_box}).\n\n"
                    "Opening Edit window."
                )
                return self._add_word_phrase_card(edit_card_id=card_id, existing_front=ex_front)

        today_str = datetime.date.today().isoformat()

        if edit_card_id is not None:
            c.execute(
                "UPDATE cards SET front=?, back=?, box=1, next_review=? WHERE id=?",
                (front, back, today_str, edit_card_id),
            )
            self.conn.commit()
            QMessageBox.information(self, "Updated", "Card updated and moved to Box 1.")
            self._refresh_current_card(edit_card_id)
        else:
            c.execute(
                "INSERT INTO cards (front, back, box, next_review) VALUES (?, ?, 1, ?)",
                (front, back, today_str),
            )
            card_id = c.lastrowid
            self.conn.commit()
            QMessageBox.information(self, "Added", "Word/phrase added to Box 1.")
            self._show_new_card(card_id, front, back)

    @staticmethod
    def _parse_back_to_meanings(back: str) -> list[tuple[str, str]] | None:
        """Try to extract (meaning, example) pairs from existing back text.

        Returns None if parsing fails, so dialog starts fresh.
        """
        if not back:
            return None
        import re
        result = []
        # Pattern: "1. meaning\n*example*" or just "meaning"
        parts = re.split(r'\n\n|\n(?=\d+\.)', back)
        for part in parts:
            part = part.strip()
            if not part:
                continue
            # Remove number prefix
            part = re.sub(r'^\d+\.\s*', '', part)
            # Extract example in *...*
            example_match = re.search(r'\*(.+?)\*', part)
            if example_match:
                example = example_match.group(1).strip()
                meaning = part[:example_match.start()].strip().rstrip('\n')
                result.append((meaning, example))
            else:
                result.append((part, ""))
        return result if result else None

    def _add_knowledge_card(self, edit_card_id=None, existing_front=""):
        """Add/edit a knowledge-based (generic front/back) card.

        Uses a simple front/back flow with no language AI prompts.
        This preserves the original generic card behavior for math,
        knowledge, and other non-language databases.
        """
        if edit_card_id is not None and existing_front:
            front = existing_front
        else:
            front_dialog = DynamicInputDialog(
                self,
                "Add New Knowledge Card",
                "Enter the front content. Markdown and MathJax are supported:",
            )
            if front_dialog.exec() != QDialog.DialogCode.Accepted or not front_dialog.text_value:
                return
            front = front_dialog.text_value

        c = self.conn.cursor()
        if edit_card_id is None:
            c.execute(
                "SELECT id, front, back, box FROM cards WHERE front = ? COLLATE NOCASE",
                (front,),
            )
            existing_card = c.fetchone()
            if existing_card:
                card_id, ex_front, ex_back, ex_box = existing_card
                QMessageBox.information(
                    self, "Already Exists",
                    f"'{ex_front}' is already in your database (Box {ex_box}).\n\n"
                    "Opening Edit window."
                )
                return self._add_knowledge_card(
                    edit_card_id=card_id, existing_front=ex_front)

        today_str = datetime.date.today().isoformat()

        if edit_card_id is not None:
            c.execute("SELECT back FROM cards WHERE id=?", (edit_card_id,))
            row = c.fetchone()
            ex_back = row[0] if row else ""

            back_dialog = DynamicInputDialog(
                self,
                "Edit Knowledge Card",
                "Enter the back content. Markdown and MathJax supported:",
                ex_back,
            )
            if back_dialog.exec() == QDialog.DialogCode.Accepted and back_dialog.text_value:
                c.execute(
                    "UPDATE cards SET front=?, back=?, box=1, next_review=? WHERE id=?",
                    (front, back_dialog.text_value, today_str, edit_card_id),
                )
                self.conn.commit()
                QMessageBox.information(self, "Updated", "Card updated and moved to Box 1.")
                self._refresh_current_card(edit_card_id)
        else:
            back_dialog = DynamicInputDialog(
                self,
                "Add Knowledge Card",
                "Enter the back content. Markdown and MathJax supported:",
            )
            if back_dialog.exec() == QDialog.DialogCode.Accepted and back_dialog.text_value:
                c.execute(
                    "INSERT INTO cards (front, back, box, next_review) VALUES (?, ?, 1, ?)",
                    (front, back_dialog.text_value, today_str),
                )
                card_id = c.lastrowid
                self.conn.commit()
                QMessageBox.information(self, "Added", "Knowledge card added to Box 1.")
                self._show_new_card(card_id, front, back_dialog.text_value)

    def _show_new_card(self, card_id, front, back):
        """Show a newly-added card immediately."""
        if self.current_card is not None:
            self.cards_due.insert(0, self.current_card)
        self.current_card = (card_id, front, back, 1)
        self.is_current_flipped = False
        if self.card_ui:
            self.scene.removeItem(self.card_ui)
            self.card_ui = None
        self.draw_card_ui()

    def _refresh_current_card(self, card_id):
        """Refresh after an edit without disrupting review queue.

        If *card_id* is the current card, refresh it in place.
        Otherwise, update or remove card_id from cards_due without
        changing the current card.
        """
        c = self.conn.cursor()
        c.execute("SELECT id, front, back, box FROM cards WHERE id=?", (card_id,))
        fresh = c.fetchone()
        if not fresh:
            # Card was deleted — remove from queue
            if self.current_card is not None:
                self.cards_due = [
                    cf for cf in self.cards_due if cf[0] != card_id
                ]
            return

        if self.current_card is not None and self.current_card[0] == card_id:
            # Same card — refresh in place
            self.current_card = fresh
            self.is_current_flipped = False
            if self.card_ui:
                self.scene.removeItem(self.card_ui)
                self.card_ui = None
            self.draw_card_ui()
        else:
            # Different card — update or remove from cards_due
            was_queued = any(cf[0] == card_id for cf in self.cards_due)
            self.cards_due = [
                cf for cf in self.cards_due if cf[0] != card_id
            ]
            if was_queued:
                # Preserve queue membership without replacing the active card.
                self.cards_due.append(fresh)

    def _remove_card_from_review_state(self, card_id):
        """Remove a deleted card from current and queued review state."""
        card_id = int(card_id)
        self.cards_due = [card for card in self.cards_due if card[0] != card_id]
        if self.current_card is not None and self.current_card[0] == card_id:
            self.current_card = None

    def _delete_card_by_id(self, card_id):
        """Execute DELETE + commit, clean review state, clear matching paused.

        Does NOT show dialogs or call show_next_card — callers own those
        UI responsibilities.  Returns the integer card_id.
        """
        card_id = int(card_id)
        self.conn.cursor().execute("DELETE FROM cards WHERE id = ?", (card_id,))
        self.conn.commit()
        self._remove_card_from_review_state(card_id)
        if (self._paused_review_card is not None
                and int(self._paused_review_card[0]) == card_id):
            self._paused_review_card = None
            self._paused_review_mode = ""
        return card_id

    # ------------------------------------------------------------------
    # Browse
    # ------------------------------------------------------------------
    def browse_cards(self):
        if not self.conn:
            return

        dialog = QDialog(self)
        dialog.setWindowTitle(f"Browse Cards: {self.current_lang}")
        dialog.resize(800, 500)

        layout = QVBoxLayout(dialog)

        filter_row = QHBoxLayout()
        filter_row.addWidget(QLabel("Search:"))
        filter_input = QLineEdit()
        filter_input.setPlaceholderText("Type to search; use AND or OR to combine terms")
        filter_row.addWidget(filter_input)
        layout.addLayout(filter_row)

        table = QTableWidget()
        table.setColumnCount(4)
        table.setHorizontalHeaderLabels(["ID", "Front", "Box", "Next Review"])
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        layout.addWidget(table)

        db_type_local = getattr(self, "_db_type", None)

        def refresh_list():
            table.setRowCount(0)
            c = self.conn.cursor()

            search_text_local = filter_input.text().strip()
            logic_local = "AND"
            if " OR " in search_text_local.upper():
                logic_local = "OR"
            elif " AND " in search_text_local.upper():
                logic_local = "AND"

            if search_text_local:
                db_now = getattr(self, "_db_type", None)
                if db_now == DatabaseType.LANGUAGE_SENTENCE:
                    results = search_sentence_cards(self.conn, search_text_local, logic_local)
                    for r in results:
                        row_idx = table.rowCount()
                        table.insertRow(row_idx)
                        table.setItem(row_idx, 0, QTableWidgetItem(str(r["id"])))
                        front_display = r["front"]
                        if r.get("expressions"):
                            front_display += " [" + ", ".join(r["expressions"]) + "]"
                        table.setItem(row_idx, 1, QTableWidgetItem(front_display))
                        table.setItem(row_idx, 2, QTableWidgetItem(str(r["box"])))
                        table.setItem(row_idx, 3, QTableWidgetItem(str(r["next_review"])))
                    return
                else:
                    results = search_word_phrase_cards(self.conn, search_text_local, logic_local)
                    for r in results:
                        row_idx = table.rowCount()
                        table.insertRow(row_idx)
                        table.setItem(row_idx, 0, QTableWidgetItem(str(r["id"])))
                        table.setItem(row_idx, 1, QTableWidgetItem(str(r["front"])))
                        table.setItem(row_idx, 2, QTableWidgetItem(str(r["box"])))
                        table.setItem(row_idx, 3, QTableWidgetItem(str(r["next_review"])))
                    return

            # No search text: show all
            c.execute("SELECT id, front, back, box, next_review FROM cards ORDER BY id")
            for card in c.fetchall():
                card_id, front, back, box, next_review = card
                row_idx = table.rowCount()
                table.insertRow(row_idx)
                table.setItem(row_idx, 0, QTableWidgetItem(str(card_id)))
                front_display_all = front
                db_now = getattr(self, "_db_type", None)
                if db_now == DatabaseType.LANGUAGE_SENTENCE:
                    exprs = _fetch_expressions_for_card(self.conn, card_id)
                    if exprs:
                        front_display_all += " [" + ", ".join(_expression_labels(exprs)) + "]"
                table.setItem(row_idx, 1, QTableWidgetItem(front_display_all))
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
            card_id = int(selected[0].text())

            db_type_edit = getattr(self, "_db_type", None)
            if db_type_edit == DatabaseType.LANGUAGE_SENTENCE:
                dialog.close()
                self._add_sentence_card(edit_card_id=card_id)
                return
            if db_type_edit == DatabaseType.LANGUAGE_WORD_PHRASE:
                dialog.close()
                self._add_word_phrase_card(edit_card_id=card_id)
                return

            c = self.conn.cursor()
            c.execute("SELECT front, back FROM cards WHERE id=?", (card_id,))
            card = c.fetchone()
            if not card:
                return

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

                self._refresh_current_card(card_id)

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
                deleted_current = (
                    self.current_card is not None
                    and str(self.current_card[0]) == str(card_id)
                )
                self._delete_card_by_id(card_id)
                refresh_list()

                if deleted_current:
                    self.show_next_card()

        edit_btn.clicked.connect(on_edit)
        del_btn.clicked.connect(on_delete)
        table.itemDoubleClicked.connect(lambda item: on_edit())

        dialog.exec()

    # ------------------------------------------------------------------
    # Button visibility
    # ------------------------------------------------------------------
    def _update_button_visibility(self):
        """Review-control state machine: idle vs active.

        IDLE   (no active review):
          - primary button → \"Start Daily Review\" or \"Resume Daily Review\"
          - Restart / Previous / Close → disabled

        ACTIVE (daily review in progress):
          - primary button → \"Next\"
          - Restart / Previous / Close → enabled
        """
        has_db = self.conn is not None
        has_card = self.current_card is not None
        is_active = self.review_mode == "daily"
        has_paused = self._paused_review_card is not None

        self.delete_entry_btn.setEnabled(has_db and has_card)

        if not has_db:
            self.start_btn.setEnabled(False)
            self.restart_review_btn.setEnabled(False)
            self.previous_review_btn.setEnabled(False)
            self.close_review_btn.setEnabled(False)
            return

        if is_active:
            # ── ACTIVE state ──
            self.start_btn.setText(" Next")
            self.start_btn.setIcon(self._icon("go-next"))
            self.restart_review_btn.setEnabled(True)
            self.previous_review_btn.setEnabled(True)
            self.close_review_btn.setEnabled(True)
        else:
            # ── IDLE state ──
            if has_paused:
                self.start_btn.setText(" Resume Daily Review")
            else:
                self.start_btn.setText(" Start Daily Review")
            self.start_btn.setIcon(self._icon("media-playback-start"))
            self.start_btn.setEnabled(True)
            self.restart_review_btn.setEnabled(False)
            self.previous_review_btn.setEnabled(False)
            self.close_review_btn.setEnabled(False)

    # ------------------------------------------------------------------
    # Review Flow
    # ------------------------------------------------------------------
    def _on_primary_button_clicked(self):
        """Dispatch primary button based on current state.

        - IDLE  → start (or resume) daily review.
        - ACTIVE → advance to next card in the daily queue.
        """
        if self.review_mode == "daily":
            self._advance_daily_queue()
        else:
            self.start_review()

    def _advance_daily_queue(self):
        """Skip the current card and advance to the next in the daily queue.

        The current (ungraded) card is returned to the end of the queue
        so it will be reviewed later in this session.
        """
        if not self.current_card or self.review_mode != "daily":
            return

        # Return ungraded card to end of queue.
        self.cards_due.append(self.current_card)
        self.show_next_card()

    def _previous_daily_card(self):
        """Navigate back to the previously graded card in this daily session.

        The current (ungraded) card goes to the front of the queue.
        If there is no history yet, this is a no-op.
        """
        if self.review_mode != "daily" or not self._daily_review_history:
            return

        # Push current (ungraded) card to front of queue.
        if self.current_card is not None:
            self.cards_due.insert(0, self.current_card)

        # Pop last card from history and show it.
        prev_card = self._daily_review_history.pop()

        if self.card_ui:
            self.scene.removeItem(self.card_ui)
            self.card_ui = None

        self.current_card = prev_card
        self.is_current_flipped = False
        self.draw_card_ui()

    def _restart_daily_review(self):
        """Restart the current daily session from the beginning.

        Resets the queue to the original due-card snapshot and clears
        review history.  Only has effect during an active daily review.
        """
        if self.review_mode != "daily":
            return

        if self.card_ui:
            self.scene.removeItem(self.card_ui)
            self.card_ui = None

        self.cards_due = list(self._daily_queue_snapshot)
        self._daily_review_history = []
        self.current_card = None

        self.show_next_card()

    def close_review(self):
        """Pause the active daily review and return to idle.

        The current card, remaining queue, original queue snapshot, and
        review history are all preserved so the session can be resumed
        exactly where it left off.  Closing does not modify the database.
        """
        if not self.current_card or self.review_mode != "daily":
            return

        self._paused_review_card = self.current_card
        self._paused_review_mode = self.review_mode
        self._paused_cards_due = list(self.cards_due)
        self._paused_daily_queue = list(self._daily_queue_snapshot)
        self._paused_review_history = list(self._daily_review_history)

        if self.card_ui:
            self.scene.removeItem(self.card_ui)
            self.card_ui = None

        self.current_card = None
        self.review_mode = ""
        self._daily_review_history = []
        self._daily_queue_snapshot = []

        self._update_button_visibility()

    def _resume_paused_card(self, cursor):
        """If a paused card exists, re-fetch it from DB and insert at
        front of cards_due, de-duplicating.  Clears paused state.
        If the paused card was deleted from DB, clears state silently.
        """
        paused = self._paused_review_card
        self._paused_review_card = None
        self._paused_review_mode = ""

        if paused is None:
            return

        cursor.execute(
            "SELECT id, front, back, box FROM cards WHERE id = ?",
            (paused[0],),
        )
        fresh = cursor.fetchone()
        if fresh is None:
            return  # card deleted — skip silently

        # De-dup: remove from cards_due if present
        paused_id = fresh[0]
        self.cards_due = [c for c in self.cards_due if c[0] != paused_id]
        # Insert at front
        self.cards_due.insert(0, fresh)

    def delete_current_card(self):
        if not self.current_card:
            QMessageBox.information(self, "Nothing to Delete", "No card is currently displayed.")
            return

        card_id, front, back, box = self.current_card
        preview = front[:80] + ("..." if len(front) > 80 else "")
        msg = (
            f"You are about to <b>permanently delete</b> the current card:\n\n"
            f"<b>ID:</b> {card_id} | <b>Box:</b> {box}\n"
            f"<b>Front:</b> {preview}\n\n"
            f"This action <span style='color:red;'>cannot be undone</span>.\n"
            f"Are you sure you want to delete it?"
        )
        reply = QMessageBox.question(
            self,
            "⚠ Permanently Delete Card",
            msg,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        self._delete_card_by_id(card_id)

        QMessageBox.information(self, "Deleted", f"Card #{card_id} has been permanently deleted.")
        self.show_next_card()

    def start_review(self):
        """Start (or resume) the daily review of due cards.

        On first start, queries all due cards and saves a snapshot for
        Restart.  On resume after close, restores the preserved queue,
        snapshot, and history so the session continues from where it was
        paused.
        """
        if not self.conn:
            return

        self.review_mode = "daily"

        if self.current_card is not None:
            if self.card_ui:
                self.scene.removeItem(self.card_ui)
                self.card_ui = None
            self.current_card = None

        c = self.conn.cursor()

        # Distinguish first-start from resume-after-close.
        resume_daily = (
            self._paused_review_card is not None
            and self._paused_review_mode == "daily"
        )

        if not resume_daily:
            # ── First start: query all due cards ──
            today_str = datetime.date.today().isoformat()
            c.execute(
                "SELECT id, front, back, box FROM cards WHERE next_review <= ?",
                (today_str,),
            )
            self.cards_due = c.fetchall()

            if self.random_checkbox.isChecked():
                random.shuffle(self.cards_due)
            else:
                self.cards_due.sort(key=lambda x: x[0])

            self._daily_review_history = []

        # Resume paused card (inserts at front, de-duplicates).
        self._resume_paused_card(c)

        if resume_daily:
            # Restore deep session state preserved by close_review().
            self._daily_queue_snapshot = list(self._paused_daily_queue)
            self._daily_review_history = list(self._paused_review_history)
            self._paused_cards_due = []
            self._paused_daily_queue = []
            self._paused_review_history = []
        else:
            # First start: snapshot the complete queue for Restart.
            self._daily_queue_snapshot = list(self.cards_due)

        if not self.cards_due:
            QMessageBox.information(self, "Done", "No cards due for review today!")
            self.review_mode = ""
            self._daily_queue_snapshot = []
            self._daily_review_history = []
            self._update_button_visibility()
            return

        self.show_next_card()


    def restart_current_review(self):
        """Restart the current daily review session (called from Restart button)."""
        if not self.conn:
            return
        self._restart_daily_review()

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

        # Queue exhausted — daily review is complete.
        QMessageBox.information(self, "Done", "You have finished your reviews.")
        self.current_card = None
        self.review_mode = ""
        self._daily_review_history = []
        self._daily_queue_snapshot = []
        self._update_button_visibility()

    def draw_card_ui(self):
        if not self.current_card:
            return

        card_id, front, back, box = self.current_card

        zone_y = getattr(self, "_zone_y", self.scene.height() - 100)

        w = max(400, self.scene.width())
        h = max(400, self.scene.height())

        cw = int(w * 0.90)
        ch = int(h * 0.75)

        available = zone_y - 20
        if ch > available:
            ch = max(200, available)

        cx = w / 2
        cy = available / 2

        self.card_ui = FlashCardItem(self, cx, cy, cw, ch)

        metadata_md = f"**Box {box}** | ID: `{card_id}`"

        if self.is_current_flipped:
            spoken_front = markdown_to_plain_text(front)
            spoken_back = markdown_to_plain_text(back)
            spoken_text = f"{spoken_front}. {spoken_back}".strip()

            if getattr(self, "_db_type", None) == DatabaseType.LANGUAGE_SENTENCE:
                display_md = self._build_sentence_card_display(
                    card_id, front, back, flipped=True, metadata=metadata_md
                )
            else:
                display_md = f"{metadata_md}\n\n{front}\n\n---\n\n{back}"

            self.card_ui.set_text(display_md, True, spoken_text)
        else:
            spoken_front = markdown_to_plain_text(front)

            if getattr(self, "_db_type", None) == DatabaseType.LANGUAGE_SENTENCE:
                display_md = self._build_sentence_card_display(
                    card_id, front, back, flipped=False, metadata=metadata_md
                )
            else:
                display_md = f"{metadata_md}\n\n{front}"

            self.card_ui.set_text(display_md, False, spoken_front)

        self.scene.addItem(self.card_ui)
        self._update_button_visibility()

    def _build_sentence_card_display(self, card_id, sentence, back, flipped, metadata):
        """Build display content for a sentence-based card."""
        items = _fetch_expressions_for_card(self.conn, card_id)

        if flipped:
            lines = [metadata, "", f"**{sentence}**", ""]
            for item in items:
                expr = item[0] if isinstance(item, tuple) else item
                meaning = item[1] if isinstance(item, tuple) and len(item) > 1 else ""
                if meaning:
                    lines.append(f"- **{expr}**: {meaning}")
                else:
                    lines.append(f"- **{expr}**")
            if back:
                lines.extend(["", "---", "", back])
            return "\n".join(lines)
        else:
            lines = [metadata, "", f"**{sentence}**", "", "Unfamiliar:"]
            for item in items:
                expr = item[0] if isinstance(item, tuple) else item
                lines.append(f"- *{expr}*")
            return "\n".join(lines)

    def flip_card(self):
        if not self.current_card:
            return

        self.is_current_flipped = True
        card_id, front, back, box = self.current_card

        metadata_md = f"**Box {box}** | ID: `{card_id}`"

        if getattr(self, "_db_type", None) == DatabaseType.LANGUAGE_SENTENCE:
            display_md = self._build_sentence_card_display(
                card_id, front, back, flipped=True, metadata=metadata_md
            )
        else:
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

        # Track graded card in daily session history (for Previous navigation).
        if self.review_mode == "daily":
            self._daily_review_history.append(self.current_card)

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
        lang_input.setPlaceholderText("No default database selected")
        lang_input.setReadOnly(True)

        browse_btn = QPushButton("Browse…")
        browse_btn.setStyleSheet("padding: 4px 12px;")

        db_row = QHBoxLayout()
        db_row.addWidget(lang_input)
        db_row.addWidget(browse_btn)

        def browse_db():
            path, _ = QFileDialog.getOpenFileName(
                dialog, "Select Default Database", DIR_DB,
                f"Barsky DB (*{DB_SUFFIX});;All Files (*)"
            )
            if path:
                lang_input.setText(path)

        browse_btn.clicked.connect(browse_db)

        tts_combo = QComboBox()
        tts_combo.setMinimumWidth(520)
        tts_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        tts_combo.view().setMinimumWidth(700)
        current_voice = self.settings.get("tts_voice", "en-US-AvaMultilingualNeural")

        voice_worker = VoiceListWorker()
        self.voice_worker = voice_worker

        def on_voices_ready(voices):
            tts_combo.clear()
            tts_combo.addItem("(loading voices…)", current_voice)
            tts_combo.model().removeRow(0)

            selected_idx = 0
            for i, (short_name, locale, gender, friendly) in enumerate(voices):
                label = f"{short_name}  ·  {locale}  ·  {gender}"
                tts_combo.addItem(label, short_name)
                if short_name == current_voice:
                    selected_idx = i
            tts_combo.setCurrentIndex(selected_idx)

        def on_error(msg):
            pass

        voice_worker.voices_ready.connect(on_voices_ready)
        voice_worker.error.connect(on_error)
        voice_worker.finished.connect(voice_worker.deleteLater)
        voice_worker.start()

        layout.addRow("Window Width:", w_input)
        layout.addRow("Window Height:", h_input)
        layout.addRow("Font Family:", font_combo)
        layout.addRow("Font Size:", size_input)
        layout.addRow("Default Database:", db_row)
        layout.addRow("TTS Voice (Edge-TTS):", tts_combo)

        # --- AI Provider Settings ---
        layout.addRow(QLabel("<b>AI Provider (OpenAI-compatible)</b>"))

        ai_base_input = QLineEdit(self.settings.get("ai_base_url", "https://api.openai.com/v1"))
        layout.addRow("Base URL:", ai_base_input)

        ai_model_input = QLineEdit(self.settings.get("ai_model", "gpt-4o-mini"))
        layout.addRow("Model:", ai_model_input)

        ai_key_input = SecretLineEdit(self.settings.get("ai_api_key", ""))
        ai_key_input.setPlaceholderText("sk-... (stored locally, never committed)")
        layout.addRow("API Key:", ai_key_input)

        ai_timeout_input = QSpinBox()
        ai_timeout_input.setRange(5, 120)
        ai_timeout_input.setValue(int(self.settings.get("ai_timeout", 30)))
        ai_timeout_input.setSuffix(" s")
        layout.addRow("Timeout:", ai_timeout_input)

        learned_lang_input = QLineEdit(self.settings.get("learned_language", "English"))
        layout.addRow("Learned Language:", learned_lang_input)

        explain_lang_input = QLineEdit(self.settings.get("explanation_language", "Chinese"))
        layout.addRow("Explanation Language:", explain_lang_input)

        save_btn = QPushButton("Save && Apply")
        save_btn.setStyleSheet("background-color: #ccffcc;")
        layout.addRow(save_btn)

        def save_and_apply():
            staged = dict(self.settings)
            staged["width"] = w_input.value()
            staged["height"] = h_input.value()
            staged["font_family"] = font_combo.currentText()
            staged["font_size"] = size_input.value()
            staged["default_database"] = lang_input.text().strip()
            staged["tts_voice"] = tts_combo.currentData() or current_voice
            staged["ai_base_url"] = ai_base_input.text().strip()
            staged["ai_model"] = ai_model_input.text().strip()
            staged["ai_api_key"] = ai_key_input.text().strip()
            staged["ai_timeout"] = ai_timeout_input.value()
            staged["learned_language"] = learned_lang_input.text().strip()
            staged["explanation_language"] = explain_lang_input.text().strip()

            try:
                save_settings(staged)
            except OSError as exc:
                QMessageBox.critical(dialog, "Settings Not Saved", str(exc))
                return
            self.settings.update(staged)
            self.resize(self.settings["width"], self.settings["height"])
            self.apply_font_settings()
            dialog.accept()

        save_btn.clicked.connect(save_and_apply)
        dialog.exec()
