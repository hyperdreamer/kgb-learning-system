"""Main application window – BarskyApp."""

import datetime
import logging
import os
import secrets
import sqlite3
import sys

from PyQt6.QtWidgets import (
    QMainWindow,
    QDialog,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QGraphicsView,
    QGraphicsScene,
    QMessageBox,
    QCheckBox,
    QMenu,
)
from PyQt6.QtCore import Qt, QTimer, QUrl
from PyQt6.QtGui import (
    QFont,
    QPainter,
    QPen,
    QColor,
    QBrush,
    QIcon,
    QShortcut,
    QKeySequence,
)
from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput

from .config import (
    load_settings,
    save_settings,
    normalize_settings_path,
    get_database_root,
    ensure_database_root_structure,
    resolve_default_database,
    relative_db_path,
)
from .db import find_databases, init_db, rollback_after_failure
from .senses import ProjectionOwnershipConflictError
from .tts import TTSWorker
from .dialogs import DynamicInputDialog  # still used for knowledge cards
from .forms import SentenceCardDialog, DBCreationDialog
from .graphics import DropZoneItem, FlashCardItem, HAS_WEBENGINE
from .markdown_utils import markdown_to_plain_text
from .schema import (
    insert_sentence_card,
    get_sentence_card,
    update_sentence_card,
    find_duplicate_sentence_card,
    validate_db_name,
    resolve_db_path,
    create_database_exclusively,
)
from .catalog import (
    DatabaseType,
    infer_database_type,
    read_database_type,
    write_database_type,
    build_catalog_tree,
    DB_DIR_LANGUAGE_SENTENCE,
    DB_DIR_LANGUAGE_WORD_PHRASE,
    DB_DIR_KNOWLEDGE,
)
from .settings_dialog import SettingsDialog
from .browse_dialog import (
    BrowseCardsDialog,
    _expression_labels,
    _fetch_expressions_for_card,
)
from .database_menu import (
    _DB_MENU_STYLESHEET,
    _compute_display_path,
    _expand_to_path,
    _menu_contains,
    _open_and_infer_type as _open_database_type,
    build_db_menu,
)
from .review_controller import ReviewControllerMixin
from .validation import (
    format_sentence_meaning_lines as _format_sentence_meaning_lines,
    highlight_unfamiliar_in_sentence as _highlight_sentence_for_items,
)


logger = logging.getLogger(__name__)


def _open_and_infer_type(db_path):
    """Compatibility wrapper for database-menu type discovery.

    The injection boundary retains monkeypatch-friendly public helpers while
    the menu construction itself lives in :mod:`kgb_srs.database_menu`.
    """
    return _open_database_type(
        db_path,
        connect=sqlite3.connect,
        read=read_database_type,
        infer=infer_database_type,
    )


def _rollback_quietly(conn):
    """Rollback without hiding the SQLite failure being handled."""
    rollback_after_failure(conn, "main-window card write")


# ---------------------------------------------------------------------------
# BarskyApp
# ---------------------------------------------------------------------------


class BarskyApp(ReviewControllerMixin, QMainWindow):
    """Main application window for the KGB 5-Box SRS System."""

    @staticmethod
    def _icon(name, fallback=""):
        icon = QIcon.fromTheme(name)
        return icon if not icon.isNull() else QIcon()

    def __init__(self, settings_file=None):
        super().__init__()
        self.setWindowTitle("KGB 5-Box SRS System")

        self.settings_file = (
            normalize_settings_path(settings_file)
            if settings_file is not None
            else None
        )
        self.settings = load_settings(self.settings_file)
        self.resize(self.settings["width"], self.settings["height"])

        self.current_lang = None
        self.conn = None
        self.current_db_path = None
        self._db_type = None
        self.current_card = None
        self._current_card_transition = None
        self.cards_due = []
        self.is_current_flipped = False
        self.review_mode = ""

        self._paused_review_card = None
        self._paused_current_card_transition = None
        self._paused_review_mode = ""

        # Daily-review session state
        self._daily_review_history = []  # cards graded this session
        self._daily_queue_snapshot = []  # full original due queue (Restart)

        # Paused-session deep state (preserved across close/resume)
        self._paused_cards_due = []
        self._paused_daily_queue = []
        self._paused_review_history = []

        self.tts_worker = None
        self.voice_worker = None
        self._tts_temp_path = None
        # A close requested during TTS is completed by the worker's actual
        # finished signal.  Never block the GUI thread waiting for it.
        self._pending_close = False
        self._pending_close_worker = None
        self._close_completion_requested = False
        self._terminal_closing = False

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
            for display, path in find_databases(get_database_root(self.settings)):
                if path == default_db:
                    self.load_database(silent=True, db_path=default_db, display=display)
                    break

    # ------------------------------------------------------------------
    # Settings
    # ------------------------------------------------------------------
    def _save_settings(self):
        if self.settings_file is None:
            save_settings(self.settings)
        else:
            save_settings(self.settings, self.settings_file)

    def closeEvent(self, event):
        worker = self.tts_worker
        if worker is not None and worker.isRunning():
            # Keep the worker alive: it may still create a temp file which its
            # callback must unlink.  Presentation callbacks consult this flag
            # and therefore cannot play audio or show an error while closing.
            self._pending_close = True
            self._pending_close_worker = worker
            event.ignore()
            return

        # A worker may have emitted a queued payload after it stopped but
        # before this immediate close path ran.  Suppress that callback.
        self._terminal_closing = True

        self.settings["width"] = self.width()
        self.settings["height"] = self.height()
        if self.current_db_path:
            root = get_database_root(self.settings)
            rel = relative_db_path(self.current_db_path, root)
            self.settings["default_database"] = rel or ""
        try:
            self._save_settings()
        except OSError as exc:
            print(f"Could not save settings: {exc}", file=sys.stderr)

        self._stop_tts_playback()
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
                "#43A047",
                "#66BB6A",
                extra=f"padding: {dyn_pad}px; font-size: {fs}px;",
            )
        )
        self.restart_review_btn.setStyleSheet(
            self._button_style(
                "#1E88E5",
                "#42A5F5",
                extra=f"padding: {dyn_pad}px; font-size: {fs}px;",
            )
        )
        self.previous_review_btn.setStyleSheet(
            self._button_style(
                "#E53935",
                "#EF5350",
                extra=f"padding: {dyn_pad}px; font-size: {fs}px;",
            )
        )

        self.delete_entry_btn.setStyleSheet(
            self._button_style(
                "#D32F2F",
                "#F44336",
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
    # Database menu bridge
    # ------------------------------------------------------------------
    def build_db_menu(self, parent_menu):
        """Build the catalog menu through the focused menu module."""
        return build_db_menu(
            self,
            parent_menu,
            find=find_databases,
            infer=_open_and_infer_type,
        )

    _menu_contains = staticmethod(_menu_contains)
    _expand_to_path = staticmethod(_expand_to_path)

    # ------------------------------------------------------------------
    # UI Setup
    # ------------------------------------------------------------------
    def setup_ui(self):
        central_widget = QWidget()
        central_widget.setStyleSheet("QWidget { background-color: #FAFAFA; }")
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
        self.all_cards_checkbox.stateChanged.connect(self._on_all_cards_toggled)
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

        self.delete_entry_btn = action_btn(
            " Delete Entry", "edit-delete", self.delete_current_card
        )
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
        add("Alt+S", self._shortcut_primary)  # Start / Next
        add("Alt+R", self._shortcut_reveal)  # Reveal
        add("Alt+Left", self._shortcut_incorrect)
        add("Alt+Right", self._shortcut_correct)
        add("Alt+1", self._shortcut_incorrect)
        add("Alt+2", self._shortcut_correct)
        add("Alt+X", self._shortcut_close_review)  # Close / eXit review
        add("Alt+T", self._shortcut_restart)  # resTart
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
        if self.review_mode == "daily":
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
            QTimer.singleShot(
                10, lambda: self._expand_to_path(menu, self.current_db_path)
            )

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
                self.load_database(silent=False, db_path=db_path, display=display)
                return

        display = action.text() if hasattr(action, "text") else self._leaf_name(db_path)
        self.load_database(silent=False, db_path=db_path, display=display or db_path)

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
                self,
                "Invalid Name",
                f"Database name '{name}' contains invalid characters.\n\n"
                "Names must not contain /, \\, .., NUL, or control characters.",
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

        def initialize_database(conn):
            write_database_type(conn, db_type)
            if db_type == DatabaseType.LANGUAGE_SENTENCE:
                from .schema import ensure_sentence_schema

                ensure_sentence_schema(conn)

        try:
            os.makedirs(target_dir, exist_ok=True)
            create_database_exclusively(
                path, initialize_database, init_database=init_db
            )
        except FileExistsError:
            QMessageBox.warning(
                self,
                "Exists",
                f"A database named '{name}' already exists in this location.",
            )
            return
        except (OSError, sqlite3.Error) as exc:
            QMessageBox.warning(
                self,
                "Database Creation Failed",
                f"Could not create database '{name}':\n{path}\n\n{exc}",
            )
            return

        display = os.path.join(subdir, name)
        self.load_database(
            silent=False,
            db_path=path,
            display=display,
            offer_projection_adoption=False,
        )

    # ------------------------------------------------------------------
    # Database open / close
    # ------------------------------------------------------------------
    def _reset_review_session(self):
        """Reset state that belongs to one database review session."""
        self.current_card = None
        self._current_card_transition = None
        self.cards_due = []
        self.is_current_flipped = False
        self.review_mode = ""
        self._paused_review_card = None
        self._paused_current_card_transition = None
        self._paused_review_mode = ""
        self._daily_review_history = []
        self._daily_queue_snapshot = []
        self._paused_cards_due = []
        self._paused_daily_queue = []
        self._paused_review_history = []

    def _clear_database_state(self):
        """Clear all UI and session state tied to the current database."""
        if self.conn:
            self.conn.close()
        self.conn = None
        self.current_db_path = None
        self.current_lang = None
        self._db_type = None
        self._reset_review_session()
        self.db_btn.setText("📂 Select Database")

        for checkbox in (self.random_checkbox, self.all_cards_checkbox):
            checkbox.blockSignals(True)
            checkbox.setChecked(False)
            checkbox.setEnabled(False)
            checkbox.blockSignals(False)

        self.scene.clear()
        self.card_ui = None
        self._update_button_visibility()

    def _randomize_box_five_for_connection(self, conn):
        """Apply the occasional Box 5 reset while preparing a connection."""
        c = conn.cursor()
        c.execute("SELECT id FROM cards WHERE box = 5")
        mastered_cards = c.fetchall()
        if mastered_cards and secrets.randbelow(20) == 0:
            target = secrets.choice(mastered_cards)[0]
            today_str = datetime.date.today().isoformat()
            c.execute(
                "UPDATE cards SET box = 1, next_review = ? WHERE id = ?",
                (today_str, target),
            )
            conn.commit()

    def _offer_projection_adoption(self, source_conn, source_path, conflict) -> bool:
        """Offer explicit, backup-first adoption for one safe legacy conflict.

        Only a markerless canonical W/P projection is eligible.  Declining or
        failing adoption keeps sentence-database work independent of its
        derived projection.
        """
        if conflict.conflict.get("code") != "word_phrase_projection_marker_missing":
            return False

        from . import senses

        db_root = get_database_root(self.settings)
        target_path = senses.default_word_phrase_path_for_sentence(source_path, db_root)
        reply = QMessageBox.question(
            self,
            "Adopt Word/Phrase Projection?",
            "A legacy word/phrase database is at the canonical projection path but "
            "does not record which sentence database owns it.\n\n"
            f"Sentence source:\n{source_path}\n\n"
            f"Word/phrase target:\n{target_path}\n\n"
            "Adopting will first create a backup beside the target, then replace its "
            "derived cards from this sentence database. Continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return False

        try:
            _path, stats = senses.adopt_canonical_word_phrase_projection(
                source_conn, source_path, db_root
            )
        except Exception as exc:
            QMessageBox.warning(
                self,
                "Projection Adoption Failed",
                f"The sentence database remains usable.\n\n{exc}",
            )
            return False

        QMessageBox.information(
            self,
            "Projection Adopted",
            "The word/phrase projection was adopted and synchronized.\n\n"
            f"Backup retained at:\n{stats['backup_path']}",
        )
        return True

    def _adopt_database(
        self, conn, path, display, db_type, is_random, all_cards_review
    ):
        """Replace the active database after its candidate has fully prepared."""
        old_conn = self.conn
        if old_conn is not None:
            old_conn.close()

        self.conn = conn
        self.current_db_path = path
        self.current_lang = display
        self._db_type = db_type
        self.db_btn.setText(f"📂 {self._leaf_name(display)}")
        self._reset_review_session()

        self.random_checkbox.blockSignals(True)
        self.random_checkbox.setChecked(is_random)
        self.random_checkbox.setEnabled(True)
        self.random_checkbox.blockSignals(False)

        if hasattr(self, "all_cards_checkbox"):
            self.all_cards_checkbox.blockSignals(True)
            self.all_cards_checkbox.setChecked(all_cards_review)
            self.all_cards_checkbox.setEnabled(True)
            self.all_cards_checkbox.blockSignals(False)

        self.scene.clear()
        self.card_ui = None
        self._update_button_visibility()

    def load_database(
        self,
        silent=False,
        *,
        db_path=None,
        display=None,
        offer_projection_adoption=True,
    ):
        """Prepare a candidate database, then atomically adopt it on success."""
        candidate_path = db_path or self.current_db_path
        candidate_display = display or self.current_lang
        if not candidate_path:
            if not silent:
                QMessageBox.warning(self, "Error", "Load a database first.")
            return

        if not os.path.exists(candidate_path):
            if not silent:
                QMessageBox.warning(
                    self, "Error", f"Database file not found:\n{candidate_path}"
                )
            return

        candidate_conn = None
        projection_conflict = None
        try:
            candidate_conn = init_db(candidate_path)

            # --- Metadata inference / persistence ---
            db_type = read_database_type(candidate_conn)
            if db_type is None:
                db_type = infer_database_type(candidate_path)
                write_database_type(candidate_conn, db_type)

            if db_type == DatabaseType.LANGUAGE_SENTENCE:
                from .schema import ensure_sentence_schema
                from .senses import ensure_linked_word_phrase_database

                ensure_sentence_schema(candidate_conn)
                # Old sentence DBs without a link get one automatically.
                try:
                    ensure_linked_word_phrase_database(
                        candidate_conn,
                        candidate_path,
                        get_database_root(self.settings),
                        sync=True,
                    )
                except ProjectionOwnershipConflictError as conflict:
                    projection_conflict = conflict
                except Exception:
                    # Projection failures must not block sentence-database use.
                    logger.warning(
                        "Could not synchronize the linked word/phrase projection "
                        "while loading %s; sentence database remains usable.",
                        candidate_path,
                        exc_info=True,
                    )

            # --- Restore random review ---
            c = candidate_conn.cursor()
            c.execute("SELECT value FROM settings WHERE key = 'random_review'")
            random_review = c.fetchone()
            c.execute("SELECT value FROM settings WHERE key = 'all_cards_review'")
            all_cards_review = c.fetchone()
            self._randomize_box_five_for_connection(candidate_conn)
        except Exception as exc:
            if candidate_conn is not None:
                candidate_conn.close()
            if not silent:
                QMessageBox.warning(
                    self,
                    "Error",
                    f"Failed to open database:\n{candidate_path}\n\n{exc}",
                )
            return

        is_random = random_review[0] == "1" if random_review else True
        is_all_cards_review = (
            all_cards_review[0] == "1" if all_cards_review else False
        )
        self._adopt_database(
            candidate_conn,
            candidate_path,
            candidate_display,
            db_type,
            is_random,
            is_all_cards_review,
        )

        if projection_conflict is not None and not silent and offer_projection_adoption:
            self._offer_projection_adoption(
                candidate_conn, candidate_path, projection_conflict
            )

        if not silent:
            QMessageBox.information(self, "Success", f"Loaded database: {display}")
            if not HAS_WEBENGINE:
                if "Math" in display or "LaTeX" in display:
                    QMessageBox.warning(
                        self,
                        "Notice",
                        "For Markdown + MathJax rendering, install PyQt6-WebEngine:\n\n"
                        "pip install PyQt6-WebEngine",
                    )

        if self.isVisible():
            self.redraw_canvas()

    def randomize_box_five(self):
        self._randomize_box_five_for_connection(self.conn)

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
        """Persist the review scope for the active database.

        The active queue is unchanged; Start Review and Restart read the
        current checkbox state when building a queue.
        """
        if not self.conn:
            return
        is_all_cards_review = self.all_cards_checkbox.isChecked()
        c = self.conn.cursor()
        c.execute(
            "UPDATE settings SET value = ? WHERE key = 'all_cards_review'",
            ("1" if is_all_cards_review else "0",),
        )
        self.conn.commit()

    # ------------------------------------------------------------------
    # Canvas
    # ------------------------------------------------------------------
    def showEvent(self, event):
        super().showEvent(event)
        if not getattr(self, "_did_initial_canvas", False):
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
        zone_font_style = f"font-family: '{safe_ui_font}'; font-size: {ui_font_size}px;"

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
    def _stop_tts_playback(self):
        """Stop TTS playback before removing its source file."""
        player = getattr(self, "player", None)
        stop = getattr(player, "stop", None)
        if stop is not None:
            stop()

    def _cleanup_tts_temp(self):
        """Best-effort unlink of the last generated TTS temp MP3."""
        from .tts import unlink_tts_temp

        self._tts_temp_path = unlink_tts_temp(self._tts_temp_path)

    def _tts_is_closing(self) -> bool:
        """Whether TTS callbacks must not update review presentation state."""
        return bool(
            getattr(self, "_pending_close", False)
            or getattr(self, "_close_completion_requested", False)
            or getattr(self, "_terminal_closing", False)
        )

    def _on_tts_worker_finished(self, worker) -> None:
        """Release a completed TTS worker and resume a deferred window close."""
        if self.tts_worker is worker:
            self.tts_worker = None

        if getattr(self, "_pending_close_worker", None) is not worker:
            return

        self._pending_close_worker = None
        self._pending_close = False
        if not getattr(self, "_close_completion_requested", False):
            self._close_completion_requested = True
            # This schedules the normal close path; no GUI-thread wait.
            self.close()

    def speak_text(self, text, btn):
        if BarskyApp._tts_is_closing(self):
            return
        if self.tts_worker is not None and self.tts_worker.isRunning():
            # Avoid stacking workers; ignore while one is already generating.
            return

        # Drop the previous temp file before generating a new one.
        BarskyApp._stop_tts_playback(self)
        self._cleanup_tts_temp()

        btn.setEnabled(False)
        btn.setText("⏳ Preparing...")

        voice = self.settings.get("tts_voice", "en-US-AvaMultilingualNeural")
        worker = TTSWorker(text, voice)
        self.tts_worker = worker
        request_card = getattr(self, "current_card", None)
        request_card_ui = getattr(self, "card_ui", None)

        def on_audio_ready(file_path):
            if (
                BarskyApp._tts_is_closing(self)
                or self.tts_worker is not worker
                or getattr(self, "current_card", None) is not request_card
            ):
                from .tts import unlink_tts_temp

                unlink_tts_temp(file_path)
                return
            self._tts_temp_path = file_path
            self.player.setSource(QUrl.fromLocalFile(file_path))
            self.player.play()
            if getattr(self, "card_ui", None) is request_card_ui:
                btn.setEnabled(True)
                btn.setText("🔊 Listen")

        def on_error(err):
            if (
                BarskyApp._tts_is_closing(self)
                or self.tts_worker is not worker
                or getattr(self, "current_card", None) is not request_card
            ):
                return
            QMessageBox.warning(self, "TTS Error", f"Audio Error: {err}")
            if getattr(self, "card_ui", None) is request_card_ui:
                btn.setEnabled(True)
                btn.setText("🔊 Listen")

        def on_thread_finished():
            BarskyApp._on_tts_worker_finished(self, worker)

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
                self,
                "Edit Sentence Card",
                front,
                items,
                back,
                settings=self.settings,
                settings_file=self.settings_file,
                conn=self.conn,
            )
        else:
            dialog = SentenceCardDialog(
                self,
                "Add Sentence Card",
                settings=self.settings,
                settings_file=self.settings_file,
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
                    self,
                    "Duplicate Detected",
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
                    self.conn,
                    edit_card_id,
                    front=sentence,
                    back=back,
                    items=items,
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
        except (ValueError, sqlite3.Error) as exc:
            # Dialog validation passed, but insert/update still rejected
            # (e.g. residual surface not re-verified). Sentence-card helpers
            # own their transaction rollback.
            QMessageBox.warning(self, "Could not save card", str(exc))
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
            logger.warning(
                "Could not synchronize the linked word/phrase projection after "
                "a sentence save; the sentence change remains saved.",
                exc_info=True,
            )

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
            if (
                front_dialog.exec() != QDialog.DialogCode.Accepted
                or not front_dialog.text_value
            ):
                return
            front = front_dialog.text_value

        c = self.conn.cursor()
        if edit_card_id is None:
            try:
                c.execute(
                    "SELECT id, front, back, box FROM cards WHERE front = ? COLLATE NOCASE",
                    (front,),
                )
                existing_card = c.fetchone()
            except sqlite3.Error:
                _rollback_quietly(self.conn)
                QMessageBox.warning(
                    self, "Could not save card", "The card could not be saved."
                )
                return
            if existing_card:
                card_id, ex_front, ex_back, ex_box = existing_card
                QMessageBox.information(
                    self,
                    "Already Exists",
                    f"'{ex_front}' is already in your database (Box {ex_box}).\n\n"
                    "Opening Edit window.",
                )
                return self._add_knowledge_card(
                    edit_card_id=card_id, existing_front=ex_front
                )

        today_str = datetime.date.today().isoformat()

        if edit_card_id is not None:
            try:
                c.execute("SELECT back FROM cards WHERE id=?", (edit_card_id,))
                row = c.fetchone()
            except sqlite3.Error:
                _rollback_quietly(self.conn)
                QMessageBox.warning(
                    self, "Could not save card", "The card could not be saved."
                )
                return
            ex_back = row[0] if row else ""

            back_dialog = DynamicInputDialog(
                self,
                "Edit Knowledge Card",
                "Enter the back content. Markdown and MathJax supported:",
                ex_back,
            )
            if (
                back_dialog.exec() == QDialog.DialogCode.Accepted
                and back_dialog.text_value
            ):
                try:
                    c.execute(
                        "UPDATE cards SET front=?, back=?, box=1, next_review=? WHERE id=?",
                        (front, back_dialog.text_value, today_str, edit_card_id),
                    )
                    self.conn.commit()
                except sqlite3.Error:
                    _rollback_quietly(self.conn)
                    QMessageBox.warning(
                        self, "Could not save card", "The card could not be saved."
                    )
                    return
                QMessageBox.information(
                    self, "Updated", "Card updated and moved to Box 1."
                )
                self._refresh_current_card(edit_card_id)
        else:
            back_dialog = DynamicInputDialog(
                self,
                "Add Knowledge Card",
                "Enter the back content. Markdown and MathJax supported:",
            )
            if (
                back_dialog.exec() == QDialog.DialogCode.Accepted
                and back_dialog.text_value
            ):
                try:
                    c.execute(
                        "INSERT INTO cards (front, back, box, next_review) VALUES (?, ?, 1, ?)",
                        (front, back_dialog.text_value, today_str),
                    )
                    card_id = c.lastrowid
                    self.conn.commit()
                except sqlite3.Error:
                    _rollback_quietly(self.conn)
                    QMessageBox.warning(
                        self, "Could not save card", "The card could not be saved."
                    )
                    return
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
            # Card was deleted — remove from queue and clear stale display.
            self.cards_due = [cf for cf in self.cards_due if cf[0] != card_id]
            if self.current_card is not None and self.current_card[0] == card_id:
                self.current_card = None
                self.is_current_flipped = False
                card_ui = getattr(self, "card_ui", None)
                try:
                    scene = getattr(self, "scene", None)
                    if card_ui is not None and scene is not None:
                        scene.removeItem(card_ui)
                except RuntimeError:
                    pass
                finally:
                    self.card_ui = None
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
            self.cards_due = [cf for cf in self.cards_due if cf[0] != card_id]
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
            self._current_card_transition = None
        self._daily_review_history = [
            entry for entry in self._daily_review_history if entry.card[0] != card_id
        ]
        self._daily_queue_snapshot = _without(self._daily_queue_snapshot)
        self._paused_cards_due = _without(self._paused_cards_due)
        self._paused_daily_queue = _without(self._paused_daily_queue)
        self._paused_review_history = [
            entry for entry in self._paused_review_history if entry.card[0] != card_id
        ]

    def _delete_card_by_id(self, card_id):
        """Execute DELETE + commit, clean review state, clear matching paused.

        Does NOT show dialogs or call show_next_card — callers own those
        UI responsibilities.  Returns the integer card_id.
        """
        card_id = int(card_id)
        try:
            self.conn.cursor().execute("DELETE FROM cards WHERE id = ?", (card_id,))
            self.conn.commit()
        except sqlite3.Error:
            _rollback_quietly(self.conn)
            QMessageBox.warning(
                self, "Could not delete card", "The card could not be deleted."
            )
            return None
        self._remove_card_from_review_state(card_id)
        if (
            self._paused_review_card is not None
            and int(self._paused_review_card[0]) == card_id
        ):
            self._paused_review_card = None
            self._paused_current_card_transition = None
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
        """Open the focused browse/search dialog for the current database."""
        if self.conn is not None:
            dialog = BrowseCardsDialog(self)
            # Keep font assignment explicit at the integration boundary; it
            # ensures custom dialog subclasses retain the window UI font.
            dialog.setFont(self.font())
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
        self._paused_current_card_transition = None
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
        self._current_card_transition = None
        self.show_next_card()

    # ------------------------------------------------------------------
    # Settings Dialog
    # ------------------------------------------------------------------
    def open_settings_window(self):
        dialog_kwargs = {"current_size": (self.width(), self.height())}
        settings_file = getattr(self, "settings_file", None)
        if settings_file is not None:
            dialog_kwargs["settings_file"] = settings_file
        dialog = SettingsDialog(self.settings, self, **dialog_kwargs)
        # Keep the background voice-list thread alive even if the modal dialog
        # is closed before the request completes, matching the previous
        # MainWindow-owned worker lifetime.
        self.voice_worker = dialog.voice_worker
        if dialog.exec() == QDialog.DialogCode.Accepted:
            # Root may have changed — recreate canonical layout under the new
            # (or existing) directory. Structure creation already ran inside
            # the dialog, but re-run here so later create/scan paths are ready.
            try:
                ensure_database_root_structure(get_database_root(self.settings))
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
