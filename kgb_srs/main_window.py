"""Main application window – BarskyApp."""

import os
import sqlite3
import datetime
import random

from PyQt6.QtWidgets import (
    QMainWindow,
    QDialog,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QGraphicsView,
    QGraphicsScene,
    QMessageBox,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QAbstractItemView,
    QCheckBox,
    QMenu,
)
from PyQt6.QtCore import Qt, QTimer, QUrl
from PyQt6.QtGui import QFont, QPainter, QPen, QColor, QBrush, QIcon, QShortcut, QKeySequence
from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput

from .config import (
    load_settings,
    save_settings,
    get_database_root,
    ensure_database_root_structure,
    resolve_default_database,
    relative_db_path,
)
from .db import init_db, find_databases
from .tts import TTSWorker
from .dialogs import DynamicInputDialog  # still used for knowledge cards
from .forms import SentenceCardDialog, DBCreationDialog
from .graphics import DropZoneItem, FlashCardItem, HAS_WEBENGINE
from .markdown_utils import markdown_to_plain_text
from .schema import (
    insert_sentence_card, get_sentence_card, update_sentence_card,
    find_duplicate_sentence_card, validate_db_name,
    resolve_db_path,
)
from .catalog import (DatabaseType, infer_database_type,
                       read_database_type, write_database_type,
                       build_catalog_tree, DB_DIR_LANGUAGE_SENTENCE,
                       DB_DIR_LANGUAGE_WORD_PHRASE, DB_DIR_KNOWLEDGE)
from .search import search_sentence_cards, search_word_phrase_cards
from .settings_dialog import SettingsDialog

_DB_MENU_STYLESHEET = (
    "QMenu::item {"
    " padding-left: 12px;"
    " padding-right: 28px;"
    " padding-top: 6px;"
    " padding-bottom: 6px;"
    " }"
)


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

    Returns list of (expression, meaning, sense_id, surface_form) tuples.
    """
    from .schema import ensure_sentence_schema

    ensure_sentence_schema(conn)
    cur = conn.cursor()
    cur.execute(
        "SELECT expression, meaning, sense_id, surface_form "
        "FROM unfamiliar_items WHERE card_id=? ORDER BY id",
        (card_id,),
    )
    return [
        (r[0], r[1], r[2], r[3] or "")
        for r in cur.fetchall()
    ]


def _expression_labels(items):
    """Return expression text from structured or legacy child items."""
    return [item[0] if isinstance(item, (tuple, list)) else item for item in items]


def _sort_items_by_sentence_order(sentence, items):
    """Order unfamiliar items by first surface appearance in *sentence*."""
    from .validation import sort_items_by_sentence_order
    return sort_items_by_sentence_order(sentence, items)


def _format_sentence_meaning_lines(items) -> list[str]:
    """Format expression+meaning lines for the card back."""
    from .validation import format_sentence_meaning_lines
    return format_sentence_meaning_lines(items)


def _highlight_sentence_for_items(sentence, items):
    """Bold matched surface forms of unfamiliar items inside *sentence*.

    Passes structured items so preferred ``surface_form`` (AI residual)
    can bold irregulars the local inflection map does not cover.
    """
    from .validation import highlight_unfamiliar_in_sentence
    return highlight_unfamiliar_in_sentence(sentence, items)

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
        self._tts_temp_path = None

        self.player = QMediaPlayer()
        self.audio_output = QAudioOutput()
        self.player.setAudioOutput(self.audio_output)

        self.setup_ui()
        self._install_shortcuts()
        self.apply_font_settings()

        # Ensure the configured database root has the canonical category/
        # subtype directories (Language-based/{Sentence,Word-Phrase}-based +
        # Knowledge-based). Legacy folders are left untouched.
        try:
            ensure_database_root_structure(get_database_root(self.settings))
        except OSError as exc:
            print(f"Could not create database directory structure: {exc}")

        # Auto-link + sync W/P projections for every sentence DB (new or old).
        try:
            self._ensure_all_word_phrase_projections()
        except Exception as exc:
            print(f"Word/phrase auto-link backfill failed: {exc}")

        default_db = resolve_default_database(self.settings)
        if default_db and os.path.exists(default_db):
            for display, path in find_databases(
                get_database_root(self.settings)
            ):
                if path == default_db:
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
            root = get_database_root(self.settings)
            rel = relative_db_path(self.current_db_path, root)
            self.settings["default_database"] = rel or ""
        self._save_settings()

        worker = self.tts_worker
        if worker is not None and worker.isRunning():
            try:
                worker.audio_ready.disconnect()
            except TypeError:
                pass
            try:
                worker.error.disconnect()
            except TypeError:
                pass
            worker.wait(2000)
            if self.tts_worker is worker:
                self.tts_worker = None

        self._cleanup_tts_temp()
        event.accept()

    # ------------------------------------------------------------------
    # Font / Styling
    # ------------------------------------------------------------------
    @staticmethod
    def _button_style(bg, hover=None, extra=""):
        hover = hover or bg
        return (
            f"QPushButton {{"
            f"  background-color: {bg}; color: white; border: none; "
            f"  border-radius: 6px; padding: 8px 16px; font-weight: bold; "
            f"  {extra}"
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
        self.setFont(font)

        dyn_pad = max(10, int(font_size * 0.8))
        fs = font_size + 2

        self.start_btn.setStyleSheet(
            self._button_style(
                "#43A047", "#66BB6A",
                extra=f"padding: {dyn_pad}px; font-size: {fs}px;",
            )
        )
        self.restart_review_btn.setStyleSheet(
            self._button_style(
                "#1E88E5", "#42A5F5",
                extra=f"padding: {dyn_pad}px; font-size: {fs}px;",
            )
        )
        self.previous_review_btn.setStyleSheet(
            self._button_style(
                "#E53935", "#EF5350",
                extra=f"padding: {dyn_pad}px; font-size: {fs}px;",
            )
        )

        self.delete_entry_btn.setStyleSheet(
            self._button_style(
                "#D32F2F", "#F44336",
                extra=f"padding: {dyn_pad}px; font-size: {fs}px;",
            )
        )

        # Toolbar chrome: re-apply stylesheets with explicit UI font so
        # size/family stay in sync when stylesheets would otherwise freeze
        # appearance independent of the window font.
        self._apply_toolbar_font_styles(font_family, font_size)

    def _toolbar_button_style(self, kind, font_family, font_size):
        """Build a toolbar button stylesheet that includes UI font."""
        font_bits = (
            f"font-family: '{font_family}'; "
            f"font-size: {font_size}px; "
            f"font-weight: bold;"
        )
        if kind == "db":
            return (
                "QPushButton {"
                f"  text-align: left; padding: 6px 14px; {font_bits}"
                "  background-color: #FFFFFF; border: 1px solid #CFD8DC;"
                "  border-radius: 6px;"
                "}"
                "QPushButton:hover { background-color: #F5F5F5; }"
            )
        if kind == "new_db":
            return (
                "QPushButton {"
                "  background-color: #43A047; color: white; padding: 6px 14px;"
                f"  {font_bits} border: none; border-radius: 6px;"
                "}"
                "QPushButton:hover { background-color: #66BB6A; }"
            )
        # generic action button (Add Entry, Browse, Settings, etc.)
        return (
            "QPushButton {"
            "  background-color: transparent; border: 1px solid #B0BEC5;"
            f"  border-radius: 6px; padding: 6px 12px; {font_bits}"
            "}"
            "QPushButton:hover { background-color: #ECEFF1; }"
        )

    def _apply_toolbar_font_styles(self, font_family, font_size):
        """Re-apply toolbar button stylesheets with current UI font."""
        if hasattr(self, "db_btn"):
            self.db_btn.setStyleSheet(
                self._toolbar_button_style("db", font_family, font_size)
            )
        if hasattr(self, "new_db_btn"):
            self.new_db_btn.setStyleSheet(
                self._toolbar_button_style("new_db", font_family, font_size)
            )
        for attr in ("add_entry_btn", "browse_btn", "settings_btn"):
            btn = getattr(self, attr, None)
            if btn is not None:
                btn.setStyleSheet(
                    self._toolbar_button_style("action", font_family, font_size)
                )
        # Parent stylesheets (top bar) can break font inheritance; set
        # explicitly on non-styled chrome widgets.
        font = QFont(font_family, font_size)
        for attr in ("db_label", "random_checkbox", "all_cards_checkbox"):
            widget = getattr(self, attr, None)
            if widget is not None:
                widget.setFont(font)
        # delete_entry_btn already styled as review-control red button above.

    # ------------------------------------------------------------------
    # Database Menu
    # ------------------------------------------------------------------
    def build_db_menu(self, parent_menu):
        """Build a hierarchical QMenu using catalog-based categories/subtypes."""
        settings = getattr(self, "settings", None) or {}
        dbs = find_databases(get_database_root(settings))
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

        self.db_label = QLabel("Database:")
        top_layout.addWidget(self.db_label)

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

        self.all_cards_checkbox = QCheckBox("All cards")
        self.all_cards_checkbox.setEnabled(False)
        self.all_cards_checkbox.setChecked(False)
        self.all_cards_checkbox.stateChanged.connect(
            self._on_all_cards_toggled
        )
        self.all_cards_checkbox.setToolTip(
            "When checked, Start Review includes every card in the database,\n"
            "not only cards due today. Grading still updates the schedule."
        )
        top_layout.addWidget(self.all_cards_checkbox)

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
        self.add_entry_btn.setToolTip("Add a new card (Alt+N)")
        top_layout.addWidget(self.add_entry_btn)

        self.delete_entry_btn = action_btn(" Delete Entry", "edit-delete", self.delete_current_card)
        self.delete_entry_btn.setEnabled(False)
        self.delete_entry_btn.setToolTip("Delete the displayed card (Alt+D)")
        top_layout.addWidget(self.delete_entry_btn)

        self.browse_btn = action_btn(" Browse", "edit-find", self.browse_cards)
        self.browse_btn.setToolTip("Browse / search cards (Alt+B)")
        top_layout.addWidget(self.browse_btn)
        self.settings_btn = action_btn(
            " Settings", "preferences-system", self.open_settings_window
        )
        self.settings_btn.setToolTip("Open settings (Alt+,)")
        top_layout.addWidget(self.settings_btn)
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
        self.start_btn.setToolTip(
            "Start / resume daily review, or Next during review (Alt+S)"
        )
        self.start_btn.clicked.connect(self._on_primary_button_clicked)
        main_layout.addWidget(self.start_btn)

        review_controls_layout = QHBoxLayout()
        review_controls_layout.setSpacing(6)

        self.restart_review_btn = QPushButton(" Restart")
        self.restart_review_btn.setIcon(self._icon("view-refresh"))
        self.restart_review_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.restart_review_btn.setToolTip("Restart this review session (Alt+T)")
        self.restart_review_btn.clicked.connect(self._restart_daily_review)

        self.previous_review_btn = QPushButton(" Previous")
        self.previous_review_btn.setIcon(self._icon("go-previous"))
        self.previous_review_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.previous_review_btn.setToolTip(
            "Previous card in this review sequence (reverse of Next) (Alt+P)"
        )
        self.previous_review_btn.clicked.connect(self._previous_daily_card)

        review_controls_layout.addWidget(self.restart_review_btn)
        review_controls_layout.addWidget(self.previous_review_btn)

        self.close_review_btn = QPushButton("×", self.view)
        self.close_review_btn.setToolTip("Close review (Alt+X)")
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
    # Keyboard shortcuts (all Alt+… so they never steal plain typing)
    # ------------------------------------------------------------------
    def _install_shortcuts(self) -> None:
        """Wire window-level Alt shortcuts for frequent review / chrome actions."""

        def add(key: str, slot, *, context=Qt.ShortcutContext.WindowShortcut):
            sc = QShortcut(QKeySequence(key), self)
            sc.setContext(context)
            sc.activated.connect(slot)
            return sc

        # Review flow
        add("Alt+S", self._shortcut_primary)       # Start / Next
        add("Alt+R", self._shortcut_reveal)        # Reveal
        add("Alt+Left", self._shortcut_incorrect)
        add("Alt+Right", self._shortcut_correct)
        add("Alt+1", self._shortcut_incorrect)
        add("Alt+2", self._shortcut_correct)
        add("Alt+X", self._shortcut_close_review)  # Close / eXit review
        add("Alt+T", self._shortcut_restart)       # resTart
        add("Alt+P", self._shortcut_previous)
        add("Alt+L", self._shortcut_listen)

        # Chrome
        add("Alt+B", self._shortcut_browse)
        add("Alt+N", self._shortcut_add_entry)
        add("Alt+D", self._shortcut_delete_entry)
        add("Alt+,", self._shortcut_settings)

    def _shortcut_primary(self):
        if not self.start_btn.isEnabled():
            return
        self._on_primary_button_clicked()

    def _shortcut_reveal(self):
        if not self.current_card or self.is_current_flipped:
            return
        self.flip_card()

    def _shortcut_incorrect(self):
        if not self.current_card or not self.is_current_flipped:
            return
        self.process_answer(correct=False)

    def _shortcut_correct(self):
        if not self.current_card or not self.is_current_flipped:
            return
        self.process_answer(correct=True)

    def _shortcut_close_review(self):
        if self.review_mode == "daily" and self.current_card is not None:
            self.close_review()

    def _shortcut_restart(self):
        if self.restart_review_btn.isEnabled():
            self._restart_daily_review()

    def _shortcut_previous(self):
        if self.previous_review_btn.isEnabled():
            self._previous_daily_card()

    def _shortcut_listen(self):
        card = getattr(self, "card_ui", None)
        if card is not None and hasattr(card, "trigger_tts"):
            card.trigger_tts()

    def _shortcut_browse(self):
        if self.conn is not None:
            self.browse_cards()

    def _shortcut_add_entry(self):
        if getattr(self, "add_entry_btn", None) is None:
            return
        if self.add_entry_btn.isVisible() and self.add_entry_btn.isEnabled():
            self.add_word()

    def _shortcut_delete_entry(self):
        if getattr(self, "delete_entry_btn", None) is None:
            return
        if self.delete_entry_btn.isVisible() and self.delete_entry_btn.isEnabled():
            self.delete_current_card()

    def _shortcut_settings(self):
        self.open_settings_window()

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
        for display, path in find_databases(get_database_root(self.settings)):
            if path == db_path:
                self.current_db_path = db_path
                self.current_lang = display
                self.db_btn.setText(f"📂 {self._leaf_name(display)}")
                self.load_database(silent=False)
                return

    def create_new_database(self):
        """Show category/subtype selection dialog, then create DB with metadata."""
        db_root = get_database_root(self.settings)
        try:
            ensure_database_root_structure(db_root)
        except OSError as exc:
            QMessageBox.warning(
                self,
                "Database Directory",
                f"Could not prepare database directory:\n{db_root}\n\n{exc}",
            )
            return

        dialog = DBCreationDialog(self, base_dir=db_root)
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
            path = resolve_db_path(db_root, subdir, name)
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
            from .schema import ensure_sentence_schema
            from .senses import ensure_linked_word_phrase_database

            ensure_sentence_schema(conn)
            ensure_linked_word_phrase_database(
                conn, path, db_root, sync=True
            )
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

        try:
            self.conn = init_db(self.current_db_path)

            # --- Metadata inference / persistence ---
            db_type = read_database_type(self.conn)
            if db_type is None:
                db_type = infer_database_type(self.current_db_path)
                write_database_type(self.conn, db_type)

            self._db_type = db_type

            if db_type == DatabaseType.LANGUAGE_SENTENCE:
                from .schema import ensure_sentence_schema
                from .senses import ensure_linked_word_phrase_database

                ensure_sentence_schema(self.conn)
                # Old sentence DBs without a link get one automatically.
                try:
                    ensure_linked_word_phrase_database(
                        self.conn,
                        self.current_db_path,
                        get_database_root(self.settings),
                        sync=True,
                    )
                except Exception:
                    pass

            # --- Restore random review ---
            c = self.conn.cursor()
            c.execute("SELECT value FROM settings WHERE key = 'random_review'")
            res = c.fetchone()
        except Exception as e:
            if self.conn:
                self.conn.close()
            self.conn = None
            if not silent:
                QMessageBox.warning(
                    self, "Error",
                    f"Failed to open database:\n{self.current_db_path}\n\n{e}"
                )
            return

        is_random = True
        if res:
            is_random = res[0] == "1"

        self.random_checkbox.blockSignals(True)
        self.random_checkbox.setChecked(is_random)
        self.random_checkbox.setEnabled(True)
        self.random_checkbox.blockSignals(False)

        # All-cards mode is session-only (not persisted) — default off so
        # normal Start Review stays due-only unless the user opts in.
        if hasattr(self, "all_cards_checkbox"):
            self.all_cards_checkbox.blockSignals(True)
            self.all_cards_checkbox.setChecked(False)
            self.all_cards_checkbox.setEnabled(True)
            self.all_cards_checkbox.blockSignals(False)

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

    def _on_all_cards_toggled(self, state):
        """Session option only — applies on the next Start Review / Restart.

        Does not rewrite the active queue mid-session; Restart re-reads it.
        """
        # Intentionally no DB write: keep SRS default (due-only) unless the
        # user opts in each time they open a database.
        return

    def _load_review_queue(self, cursor):
        """Return the card queue for a fresh review session.

        Due-only when *All cards* is unchecked; every card when checked.
        """
        all_cards = bool(
            getattr(self, "all_cards_checkbox", None)
            and self.all_cards_checkbox.isChecked()
        )
        if all_cards:
            cursor.execute(
                "SELECT id, front, back, box FROM cards ORDER BY id"
            )
        else:
            today_str = datetime.date.today().isoformat()
            cursor.execute(
                "SELECT id, front, back, box FROM cards "
                "WHERE next_review <= ?",
                (today_str,),
            )
        queue = list(cursor.fetchall())
        if self.random_checkbox.isChecked():
            random.shuffle(queue)
        else:
            queue.sort(key=lambda x: x[0])
        return queue

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

        ui_font_family = self.settings.get("font_family", "Arial")
        ui_font_size = self.settings.get("font_size", 14)
        # Escape quotes for safe inline CSS
        safe_ui_font = str(ui_font_family).replace("\\", "\\\\").replace("'", "\\'")
        zone_font_style = (
            f"font-family: '{safe_ui_font}'; font-size: {ui_font_size}px;"
        )

        self.incorrect_zone = DropZoneItem(
            margin,
            zone_y,
            zone_w,
            zone_h,
            QPen(QColor("red")),
            QBrush(QColor("#ffcccc")),
            f"<div align='center' style=\"{zone_font_style}\">"
            f"<b>Click or Drop Here</b><br>"
            f"if <span style='color:red;'>INCORRECT</span><br>"
            f"(Drops to Box 1 or 3)</div>",
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
            f"<div align='center' style=\"{zone_font_style}\">"
            f"<b>Click or Drop Here</b><br>"
            f"if <span style='color:green;'>CORRECT</span><br>"
            f"(Advances 1 Box)</div>",
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
    def _cleanup_tts_temp(self):
        """Best-effort unlink of the last generated TTS temp MP3."""
        from .tts import unlink_tts_temp

        self._tts_temp_path = unlink_tts_temp(self._tts_temp_path)

    def speak_text(self, text, btn):
        if self.tts_worker is not None and self.tts_worker.isRunning():
            # Avoid stacking workers; ignore while one is already generating.
            return

        # Drop the previous temp file before generating a new one.
        self._cleanup_tts_temp()

        btn.setEnabled(False)
        btn.setText("⏳ Preparing...")

        voice = self.settings.get("tts_voice", "en-US-AvaMultilingualNeural")
        worker = TTSWorker(text, voice)
        self.tts_worker = worker

        def on_audio_ready(file_path):
            self._tts_temp_path = file_path
            self.player.setSource(QUrl.fromLocalFile(file_path))
            self.player.play()
            btn.setEnabled(True)
            btn.setText("🔊 Listen")

        def on_error(err):
            QMessageBox.warning(self, "TTS Error", f"Audio Error: {err}")
            btn.setEnabled(True)
            btn.setText("🔊 Listen")

        def on_thread_finished():
            if self.tts_worker is worker:
                self.tts_worker = None

        worker.audio_ready.connect(on_audio_ready)
        worker.error.connect(on_error)
        worker.finished.connect(on_thread_finished)
        worker.finished.connect(worker.deleteLater)
        worker.start()

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
            QMessageBox.information(
                self,
                "Read-only Word/Phrase Database",
                "Word/phrase cards come only from the shared sense catalog.\n\n"
                "Add or edit sentences in a sentence-based database; the "
                "linked dictionary updates automatically. Manual add/edit "
                "is disabled.",
            )
        else:
            self._add_knowledge_card()

    def _add_sentence_card(self, edit_card_id=None):
        """Show the sentence-based card dialog."""
        if edit_card_id is not None:
            existing = get_sentence_card(self.conn, edit_card_id)
            if existing is None:
                return
            front, back, box, items = existing
            # Pass full (expression, meaning, sense_id) tuples; the dialog
            # uses meanings + sense links for AI reuse.
            dialog = SentenceCardDialog(
                self, "Edit Sentence Card", front, items, back,
                settings=self.settings,
                conn=self.conn,
            )
        else:
            dialog = SentenceCardDialog(
                self, "Add Sentence Card", settings=self.settings,
                conn=self.conn,
            )

        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        sentence = dialog.result_sentence
        items = dialog.result_items
        back = dialog.result_back
        verified_surfaces = getattr(dialog, "result_verified_surfaces", {}) or {}

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

        try:
            if edit_card_id is not None:
                update_sentence_card(
                    self.conn, edit_card_id,
                    front=sentence, back=back, items=items,
                    verified_surfaces=verified_surfaces,
                )
                QMessageBox.information(
                    self, "Updated", "Card updated and moved to Box 1."
                )
                self._refresh_current_card(edit_card_id)
            else:
                card_id = insert_sentence_card(
                    self.conn,
                    sentence,
                    items,
                    back,
                    verified_surfaces=verified_surfaces,
                )
                QMessageBox.information(self, "Added", "Card added to Box 1.")
                self._show_new_card(card_id, sentence, back)
        except ValueError as e:
            # Dialog validation passed, but insert/update still rejected
            # (e.g. residual surface not re-verified). Show a dialog instead
            # of an uncaught traceback from the Alt+ shortcut path.
            QMessageBox.warning(self, "Could not save card", str(e))
            return

        # Shared catalog → auto-sync linked word/phrase projection.
        self._sync_linked_word_phrase_quiet()

    def _sync_linked_word_phrase_quiet(self) -> None:
        """Ensure link + re-derive W/P projection after sentence changes."""
        if not self.conn:
            return
        if getattr(self, "_db_type", None) != DatabaseType.LANGUAGE_SENTENCE:
            return
        if not self.current_db_path:
            return
        try:
            from .senses import ensure_linked_word_phrase_database

            ensure_linked_word_phrase_database(
                self.conn,
                self.current_db_path,
                get_database_root(self.settings),
                sync=True,
            )
        except Exception:
            # Never block sentence save on projection failure.
            pass

    def _ensure_all_word_phrase_projections(self) -> None:
        """Startup: link + sync W/P for every sentence DB under the root."""
        from .senses import ensure_all_sentence_databases_linked

        ensure_all_sentence_databases_linked(get_database_root(self.settings))

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

        def _without(cards):
            return [card for card in cards if card[0] != card_id]

        self.cards_due = _without(self.cards_due)
        if self.current_card is not None and self.current_card[0] == card_id:
            self.current_card = None
        self._daily_review_history = _without(self._daily_review_history)
        self._daily_queue_snapshot = _without(self._daily_queue_snapshot)
        self._paused_cards_due = _without(self._paused_cards_due)
        self._paused_daily_queue = _without(self._paused_daily_queue)
        self._paused_review_history = _without(self._paused_review_history)

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
        if (
            getattr(self, "_db_type", None) == DatabaseType.LANGUAGE_SENTENCE
            and self.conn is not None
        ):
            from .senses import purge_orphan_senses

            purge_orphan_senses(self.conn, commit=True)
            self._sync_linked_word_phrase_quiet()
        return card_id

    # ------------------------------------------------------------------
    # Browse
    # ------------------------------------------------------------------
    def browse_cards(self):
        if not self.conn:
            return

        dialog = QDialog(self)
        dialog.setWindowTitle(f"Browse Cards: {self.current_lang}")
        dialog.setFont(self.font())
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
        header = table.horizontalHeader()
        # ID / Box / Next Review size to content (header + date); Front takes the rest.
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        header.setMinimumSectionSize(48)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        layout.addWidget(table)


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
        review_btn = QPushButton("Review Selected")
        edit_btn = QPushButton("Edit Selected")
        del_btn = QPushButton("Delete Selected")
        del_btn.setStyleSheet("background-color: #ffcccc;")
        review_btn.setToolTip("Review the selected card (Alt+R)")
        edit_btn.setToolTip("Edit the selected card (Alt+E)")
        del_btn.setToolTip("Delete the selected card (Alt+D)")
        btn_layout.addWidget(review_btn)
        btn_layout.addWidget(edit_btn)
        btn_layout.addWidget(del_btn)
        layout.addLayout(btn_layout)

        is_wp_browse = getattr(self, "_db_type", None) == DatabaseType.LANGUAGE_WORD_PHRASE
        if is_wp_browse:
            # Projection-only: no edit/delete in Browse; Review is the action.
            edit_btn.setEnabled(False)
            del_btn.setEnabled(False)
            edit_btn.setToolTip("Word/phrase dictionary is read-only.")
            del_btn.setToolTip("Word/phrase dictionary is read-only.")
            review_btn.setToolTip(
                "Open the selected dictionary entry for review (Alt+R / double-click)"
            )

        def on_review():
            selected = table.selectedItems()
            if not selected:
                QMessageBox.information(
                    dialog, "Nothing Selected", "Select a card to review."
                )
                return
            card_id = int(selected[0].text())
            dialog.close()
            self._start_selected_card_review(card_id)

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
                QMessageBox.information(
                    dialog,
                    "Read-only Word/Phrase Card",
                    "This dictionary is derived from the shared sense catalog.\n\n"
                    "Edit the expression/sense via sentence cards; the "
                    "dictionary updates automatically. Manual edit is disabled.",
                )
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
            if getattr(self, "_db_type", None) == DatabaseType.LANGUAGE_WORD_PHRASE:
                QMessageBox.information(
                    dialog,
                    "Read-only Word/Phrase Card",
                    "This dictionary is derived from the shared sense catalog.\n\n"
                    "Remove senses via sentence cards; the dictionary updates "
                    "automatically. Manual delete is disabled.",
                )
                return
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

        def on_row_activate(_item=None):
            # W/P is read-only: double-click reviews. Others edit as before.
            if getattr(self, "_db_type", None) == DatabaseType.LANGUAGE_WORD_PHRASE:
                on_review()
            else:
                on_edit()

        review_btn.clicked.connect(on_review)
        edit_btn.clicked.connect(on_edit)
        del_btn.clicked.connect(on_delete)
        table.itemDoubleClicked.connect(on_row_activate)

        # Dialog-local Alt shortcuts (safe while typing in Search).
        def add_dialog_shortcut(key: str, slot):
            sc = QShortcut(QKeySequence(key), dialog)
            sc.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            sc.activated.connect(slot)
            return sc

        def on_edit_key():
            if not edit_btn.isEnabled():
                return
            on_edit()

        def on_delete_key():
            if not del_btn.isEnabled():
                return
            on_delete()

        add_dialog_shortcut("Alt+R", on_review)
        add_dialog_shortcut("Alt+E", on_edit_key)
        add_dialog_shortcut("Alt+D", on_delete_key)

        dialog.exec()

    def _start_selected_card_review(self, card_id):
        """Open a one-card daily review session for *card_id* from Browse.

        Replaces any active/paused session with a single-card queue so the
        normal flip / grade / Close Review controls work.
        """
        if not self.conn:
            return
        card_id = int(card_id)
        c = self.conn.cursor()
        c.execute("SELECT id, front, back, box FROM cards WHERE id = ?", (card_id,))
        card = c.fetchone()
        if not card:
            QMessageBox.information(
                self, "Not Found", f"Card #{card_id} no longer exists."
            )
            return

        # Explicit browse review starts a fresh one-card session.
        self._paused_review_card = None
        self._paused_review_mode = ""
        self._paused_cards_due = []
        self._paused_daily_queue = []
        self._paused_review_history = []

        if self.card_ui:
            self.scene.removeItem(self.card_ui)
            self.card_ui = None

        self.review_mode = "daily"
        self.cards_due = [card]
        self._daily_queue_snapshot = [card]
        self._daily_review_history = []
        self.current_card = None
        self.show_next_card()

    # ------------------------------------------------------------------
    # Button visibility
    # ------------------------------------------------------------------
    def _update_button_visibility(self):
        """Review-control state machine: idle vs active.

        IDLE   (no active review):
          - primary button → "Start Daily Review" or "Resume Daily Review"
          - Restart / Previous / Close → disabled

        ACTIVE (daily review in progress):
          - primary button → "Next"
          - Restart / Close → enabled
          - Previous → enabled once the session path has a prior card
            (after Next or a grade — reverse of Next)
        """
        has_db = self.conn is not None
        has_card = self.current_card is not None
        is_active = self.review_mode == "daily"
        has_paused = self._paused_review_card is not None
        has_history = bool(self._daily_review_history)
        is_wp = (
            has_db
            and getattr(self, "_db_type", None) == DatabaseType.LANGUAGE_WORD_PHRASE
        )

        # W/P is a derived projection — no manual Add/Delete Entry.
        if hasattr(self, "add_entry_btn"):
            self.add_entry_btn.setVisible(has_db and not is_wp)
            self.add_entry_btn.setEnabled(has_db and not is_wp)
        if hasattr(self, "delete_entry_btn"):
            self.delete_entry_btn.setVisible(has_db and not is_wp)
            self.delete_entry_btn.setEnabled(has_db and has_card and not is_wp)

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
            # Reverse of Next: enable only when there is a prior step.
            self.previous_review_btn.setEnabled(has_history)
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
        so it will be reviewed later in this session.  It is also pushed
        onto the session path so Previous is the reverse of Next.
        """
        if not self.current_card or self.review_mode != "daily":
            return

        # Session path: Next leaves the current card behind.
        self._daily_review_history.append(self.current_card)
        # Return ungraded card to end of queue.
        self.cards_due.append(self.current_card)
        self._update_button_visibility()
        self.show_next_card()

    def _previous_daily_card(self):
        """Step back one card in this daily session path (reverse of Next).

        Works after Next (skip) or after a grade.  The current card returns
        to the front of the queue; the prior path entry is restored.
        If there is no prior step, this is a no-op.
        """
        if self.review_mode != "daily" or not self._daily_review_history:
            return

        # Skip deleted/missing history entries instead of showing a ghost card.
        prev_card = None
        while self._daily_review_history:
            candidate = self._daily_review_history.pop()
            prev_id = candidate[0]
            if self.conn is not None:
                c = self.conn.cursor()
                c.execute(
                    "SELECT id, front, back, box FROM cards WHERE id = ?",
                    (prev_id,),
                )
                fresh = c.fetchone()
                if fresh is None:
                    continue
                prev_card = fresh
            else:
                prev_card = candidate
            break

        if prev_card is None:
            self._update_button_visibility()
            return

        # Push current card to front of queue only after success.
        if self.current_card is not None:
            self.cards_due.insert(0, self.current_card)

        # Next-skip puts the prior card at the end of the queue; remove it
        # so we do not show a duplicate after restoring it as current.
        prev_id = prev_card[0]
        self.cards_due = [c for c in self.cards_due if c[0] != prev_id]

        if self.card_ui:
            self.scene.removeItem(self.card_ui)
            self.card_ui = None

        self.current_card = prev_card
        self.is_current_flipped = False
        self.draw_card_ui()
        self._update_button_visibility()

    def _restart_daily_review(self):
        """Restart the current daily session from the beginning.

        Re-reads the queue with the current *All cards* / Shuffle options
        (so toggling All cards then Restart picks up the new mode).
        Clears review history. Only has effect during an active daily review.
        """
        if self.review_mode != "daily":
            return

        if self.card_ui:
            self.scene.removeItem(self.card_ui)
            self.card_ui = None

        if self.conn is not None:
            c = self.conn.cursor()
            self.cards_due = self._load_review_queue(c)
            self._daily_queue_snapshot = list(self.cards_due)
        else:
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
        if self.review_mode != "daily":
            return

        # Queue may already be empty (finished session still active so
        # Previous can restore the last graded card). Pause whatever remains.
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
        if getattr(self, "_db_type", None) == DatabaseType.LANGUAGE_WORD_PHRASE:
            QMessageBox.information(
                self,
                "Read-only Word/Phrase Database",
                "Word/phrase cards are a projection of the shared sense catalog.\n\n"
                "Delete or change senses via sentence cards; this dictionary "
                "updates automatically. Manual delete is disabled.",
            )
            return

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
        # A finished queue may pause with no current card but with history /
        # snapshot still worth restoring for Previous / Restart.
        resume_daily = self._paused_review_mode == "daily" and (
            self._paused_review_card is not None
            or bool(self._paused_review_history)
            or bool(self._paused_cards_due)
            or bool(self._paused_daily_queue)
        )

        if resume_daily:
            # Restore deep session state preserved by close_review() before
            # re-inserting the paused card at the front of the queue.
            self.cards_due = list(self._paused_cards_due)
            self._daily_queue_snapshot = list(self._paused_daily_queue)
            self._daily_review_history = list(self._paused_review_history)
            self._paused_cards_due = []
            self._paused_daily_queue = []
            self._paused_review_history = []
            # Clear mode flag; _resume_paused_card clears the card pointer.
            self._paused_review_mode = ""
        else:
            # ── First start: due-only, or all cards when opted in ──
            self.cards_due = self._load_review_queue(c)

            self._daily_review_history = []
            # First start: snapshot the complete queue for Restart.
            self._daily_queue_snapshot = list(self.cards_due)

        # Resume paused card (inserts at front, de-duplicates).
        self._resume_paused_card(c)

        if not self.cards_due:
            # Finished-but-paused session: restore active shell so Previous
            # can still walk graded history. Do not treat as "nothing due".
            if resume_daily and (
                self._daily_review_history or self._daily_queue_snapshot
            ):
                self.current_card = None
                self._update_button_visibility()
                return
            all_mode = bool(
                getattr(self, "all_cards_checkbox", None)
                and self.all_cards_checkbox.isChecked()
            )
            empty_msg = (
                "No cards in this database."
                if all_mode
                else "No cards due for review today!"
            )
            QMessageBox.information(self, "Done", empty_msg)
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

        # Queue exhausted — no more ungraded due cards.
        # Keep daily mode + graded history so Previous can still restore the
        # last graded card (clearing history here made Previous a permanent
        # no-op after the final grade). Restart / Close still work.
        # If there is nothing to go back to either, fully end the session.
        if self._daily_review_history:
            QMessageBox.information(self, "Done", "You have finished your reviews.")
            self.current_card = None
            self._update_button_visibility()
            return

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
            elif getattr(self, "_db_type", None) == DatabaseType.LANGUAGE_WORD_PHRASE:
                display_md = self._build_word_phrase_card_display(
                    front, back, flipped=True, metadata=metadata_md
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
            elif getattr(self, "_db_type", None) == DatabaseType.LANGUAGE_WORD_PHRASE:
                display_md = self._build_word_phrase_card_display(
                    front, back, flipped=False, metadata=metadata_md
                )
            else:
                display_md = f"{metadata_md}\n\n{front}"

            self.card_ui.set_text(display_md, False, spoken_front)

        self.scene.addItem(self.card_ui)
        self._update_button_visibility()

    def _build_sentence_card_display(self, card_id, sentence, back, flipped, metadata):
        """Build display content for a sentence-based card.

        Front: sentence with matched surface forms bolded in place
        (e.g. lemma ``insist on`` → bold ``insists on``). No separate
        Unfamiliar list — the mark is the list.

        Back: same highlighted sentence, then each expression with its
        contextual meaning. Items are ordered by first appearance in the
        sentence. Multiple items are numbered and separated as distinct
        blocks so Markdown keeps them on separate lines.
        """
        items = _fetch_expressions_for_card(self.conn, card_id)
        ordered = _sort_items_by_sentence_order(sentence, items)
        highlighted = _highlight_sentence_for_items(sentence, ordered)

        if flipped:
            lines = [metadata, "", highlighted, "", "---", ""]
            meaning_lines = _format_sentence_meaning_lines(ordered)
            # Blank line between entries so Markdown does not collapse them.
            lines.append("\n\n".join(meaning_lines))
            # cards.back is a derived cache of the same expression+meaning
            # pairs — do not append it again under a second separator.
            return "\n".join(lines)

        # Front: focus on the sentence; bold only the learning targets.
        return f"{metadata}\n\n{highlighted}"

    def _build_word_phrase_card_display(self, front, back, flipped, metadata):
        """Build display content for a word/phrase dictionary card.

        Front: bold expression only (``**insist on**``).
        Back: bold expression, then sense list with indented examples
        (examples already embed bold surface forms from derive).
        """
        expr = (front or "").strip()
        bold_front = f"**{expr}**" if expr else ""
        if not flipped:
            return f"{metadata}\n\n{bold_front}"
        body = (back or "").strip()
        if body:
            return f"{metadata}\n\n{bold_front}\n\n---\n\n{body}"
        return f"{metadata}\n\n{bold_front}"

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
        elif getattr(self, "_db_type", None) == DatabaseType.LANGUAGE_WORD_PHRASE:
            display_md = self._build_word_phrase_card_display(
                front, back, flipped=True, metadata=metadata_md
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
        # Drop zones and other paths must not grade an unrevealed card.
        if not self.is_current_flipped:
            return

        card_id, front, back, _stale_box = self.current_card
        today = datetime.date.today()

        c = self.conn.cursor()
        # Always grade from the latest DB box so Previous/re-grade is correct.
        c.execute("SELECT box FROM cards WHERE id = ?", (card_id,))
        row = c.fetchone()
        if row is None:
            self.show_next_card()
            return
        current_box = int(row[0])

        new_box = min(current_box + 1, 5) if correct else (3 if current_box >= 3 else 1)
        intervals = {1: 1, 2: 3, 3: 7, 4: 30, 5: 365}
        next_review_str = (
            today + datetime.timedelta(days=intervals[new_box])
        ).isoformat()

        c.execute(
            "UPDATE cards SET box = ?, next_review = ? WHERE id = ?",
            (new_box, next_review_str, card_id),
        )
        self.conn.commit()

        # Session path: grade also leaves the current card behind (like Next).
        if self.review_mode == "daily":
            self._daily_review_history.append((card_id, front, back, new_box))
            # Reflect Previous availability immediately (before next card draw).
            self._update_button_visibility()

        self.show_next_card()

    # ------------------------------------------------------------------
    # Settings Dialog
    # ------------------------------------------------------------------
    def open_settings_window(self):
        dialog = SettingsDialog(
            self.settings, self, current_size=(self.width(), self.height())
        )
        # Keep the background voice-list thread alive even if the modal dialog
        # is closed before the request completes, matching the previous
        # MainWindow-owned worker lifetime.
        self.voice_worker = dialog.voice_worker
        if dialog.exec() == QDialog.DialogCode.Accepted:
            # Root may have changed — recreate canonical layout under the new
            # (or existing) directory. Structure creation already ran inside
            # the dialog, but re-run here so later create/scan paths are ready.
            try:
                ensure_database_root_structure(
                    get_database_root(self.settings)
                )
            except OSError as exc:
                QMessageBox.warning(
                    self,
                    "Database Directory",
                    f"Could not prepare database directory:\n{exc}",
                )
            self.resize(self.settings["width"], self.settings["height"])
            self.apply_font_settings()
            # Refresh drop-zone HTML and study-card content fonts even when
            # window size is unchanged (resizeEvent would not fire).
            self.redraw_canvas()
