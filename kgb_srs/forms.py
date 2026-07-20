"""Subtype-specific input forms for card creation and editing.

Dialogs:
  SentenceCardDialog   — sentence + unfamiliar items selection/entry
                         with in-dialog nonblocking AI generation
  DBCreationDialog     — category/subtype selection for new databases
"""

import os

from PyQt6.QtWidgets import (
    QDialog,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QTextEdit,
    QPushButton,
    QListWidget,
    QAbstractItemView,
    QMessageBox,
    QFormLayout,
    QComboBox,
    QRadioButton,
    QButtonGroup,
    QGroupBox,
    QFileDialog,
    QProgressBar,
    QSizePolicy,
    QTabWidget,
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal

from .catalog import DatabaseType
from .validation import validate_unfamiliar_items, deduplicate_unfamiliar_items
from .ai_provider import (
    AIProviderConfig,
    AIClient,
    _make_http_call,
    build_sentence_prompt,
    build_word_phrase_prompt,
    AIMissingConfigError,
)
from .ai_parser import (
    parse_sentence_meanings,
    parse_word_phrase_meanings,
    AIParseError,
    AIValidationError,
)
import json
import urllib.error


# ---------------------------------------------------------------------------
# AI Worker (nonblocking generation)
# ---------------------------------------------------------------------------

class _AIGenerateWorker(QThread):
    """Background QThread for AI API calls."""

    result = pyqtSignal(str)     # emits response text
    error = pyqtSignal(str)      # emits error message

    def __init__(self, config: AIProviderConfig, prompt: str):
        super().__init__()
        self._config = config
        self._prompt = prompt

    def run(self):
        try:
            client = AIClient(self._config)
            url, headers, body = client.build_request(self._prompt)
            raw = _make_http_call(
                url, headers,
                json.dumps(body).encode("utf-8"),
                timeout=self._config.timeout_seconds,
            )
            content = client.parse_response(raw)
            self.result.emit(content)
        except AIMissingConfigError as e:
            self.error.emit(str(e))
        except urllib.error.URLError as e:
            self.error.emit(f"Network error: {e.reason}")
        except ValueError as e:
            self.error.emit(str(e))
        except Exception as e:
            self.error.emit(f"Unexpected error: {e}")


# ---------------------------------------------------------------------------
# SentenceCardDialog
# ---------------------------------------------------------------------------

class SentenceCardDialog(QDialog):
    """Dialog for creating/editing a sentence-based card.

    Flow:
      1. Enter the sentence.
      2. Select or manually type unfamiliar words/phrases from the sentence.
         - Use "Add selected text" to add highlighted text from the sentence.
      3. Optionally auto-generate meanings via AI with a Generate button.
         The dialog stays open; generation is nonblocking (QThread).
         On completion, meanings are populated in editable fields.
      4. Validate that all unfamiliar items are in the sentence.
      5. Save is a separate user action after preview/edit.
    """

    def __init__(self, parent=None, title="Add Sentence Card",
                 sentence="", items=None, back="",
                 settings: dict | None = None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumSize(560, 550)
        self._result_sentence = ""
        self._result_items: list = []
        self._result_back = ""
        self._settings = settings or {}
        self._ai_worker: _AIGenerateWorker | None = None
        # Parse items for initial meanings
        # items may be list[str] or list[tuple[str, str]]
        self._initial_meanings: dict[str, str] = {}
        if items:
            for item in items:
                if isinstance(item, tuple):
                    self._initial_meanings[str(item[0])] = str(item[1]) if len(item) > 1 else ""
                else:
                    self._initial_meanings[str(item)] = ""

        layout = QVBoxLayout(self)

        # --- Sentence ---
        layout.addWidget(QLabel("Sentence:"))
        self._sentence_edit = QTextEdit()
        self._sentence_edit.setPlainText(sentence)
        self._sentence_edit.setMaximumHeight(100)
        self._sentence_edit.setAcceptRichText(False)
        layout.addWidget(self._sentence_edit)

        # "Add selected text" button
        sel_row = QHBoxLayout()
        sel_row.addStretch()
        self._add_sel_btn = QPushButton("📋 Add selected text")
        self._add_sel_btn.setToolTip(
            "Select text inside the sentence box above and click to add it "
            "as an unfamiliar item."
        )
        self._add_sel_btn.clicked.connect(self._add_selected_text)
        sel_row.addWidget(self._add_sel_btn)
        layout.addLayout(sel_row)

        # --- Unfamiliar items ---
        layout.addWidget(QLabel(
            "Unfamiliar words/phrases (select from sentence or type below):"))
        self._items_list = QListWidget()
        self._items_list.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection)
        layout.addWidget(self._items_list)

        # Manual entry
        entry_layout = QHBoxLayout()
        self._item_entry = QLineEdit()
        self._item_entry.setPlaceholderText(
            "Type a word/phrase and press Add")
        self._item_entry.returnPressed.connect(self._add_item)
        entry_layout.addWidget(self._item_entry)

        add_btn = QPushButton("Add")
        add_btn.clicked.connect(self._add_item)
        entry_layout.addWidget(add_btn)

        remove_btn = QPushButton("Remove Selected")
        remove_btn.clicked.connect(self._remove_selected)
        entry_layout.addWidget(remove_btn)
        layout.addLayout(entry_layout)

        # Pre-populate items if editing
        if items:
            for item in items:
                if isinstance(item, tuple):
                    self._items_list.addItem(item[0])
                else:
                    self._items_list.addItem(item)

        # --- Validation ---
        self._status_label = QLabel("")
        self._status_label.setStyleSheet("color: #888;")
        layout.addWidget(self._status_label)

        # --- AI Generation controls ---
        ai_group = QGroupBox("AI Meaning Generation")
        ai_layout = QVBoxLayout(ai_group)

        self._generate_btn = QPushButton("🤖 Generate Meanings")
        self._generate_btn.setToolTip(
            "Uses AI to generate contextual meanings for each unfamiliar item."
        )
        self._generate_btn.clicked.connect(self._generate_ai_meanings)
        self._generate_btn.setEnabled(False)
        ai_layout.addWidget(self._generate_btn)

        self._ai_progress = QProgressBar()
        self._ai_progress.setRange(0, 0)  # indeterminate
        self._ai_progress.setVisible(False)
        self._ai_progress.setMaximumHeight(18)
        ai_layout.addWidget(self._ai_progress)

        self._ai_status = QLabel("")
        self._ai_status.setStyleSheet("color: #666;")
        ai_layout.addWidget(self._ai_status)

        layout.addWidget(ai_group)

        # --- Per-item meaning editors ---
        layout.addWidget(QLabel("Meanings (editable):"))
        self._meanings_layout = QVBoxLayout()
        self._meaning_widgets: list[tuple[str, QTextEdit]] = []
        self._meanings_container = QWidget()
        self._meanings_container.setLayout(self._meanings_layout)
        layout.addWidget(self._meanings_container)

        # --- Back (rendered/cache) ---
        layout.addWidget(QLabel("Back (contextual meanings — rendered):"))
        self._back_edit = QTextEdit()
        self._back_edit.setPlainText(back)
        self._back_edit.setAcceptRichText(False)
        self._back_edit.setMaximumHeight(100)
        layout.addWidget(self._back_edit)

        # --- Buttons ---
        btn_layout = QHBoxLayout()
        validate_btn = QPushButton("Validate")
        validate_btn.clicked.connect(self._validate)
        btn_layout.addWidget(validate_btn)

        btn_layout.addStretch()

        self._save_btn = QPushButton("Save")
        self._save_btn.setStyleSheet(
            "background-color: #ccffcc; font-weight: bold; padding: 10px;")
        self._save_btn.clicked.connect(self._accept)
        btn_layout.addWidget(self._save_btn)

        self._cancel_btn = QPushButton("Cancel")
        self._cancel_btn.setStyleSheet("padding: 10px;")
        self._cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(self._cancel_btn)
        layout.addLayout(btn_layout)

        if parent:
            w = min(max(580, int(parent.width() * 0.6)), 850)
            h = min(max(550, int(parent.height() * 0.75)), 750)
            self.resize(w, h)

        # Connect double-click on list to removal
        self._items_list.itemDoubleClicked.connect(self._remove_selected)

        # Check AI config availability
        self._check_ai_available()

        # Build initial meaning editors
        self._rebuild_meaning_editors()

    # ------------------------------------------------------------------
    # Item management
    # ------------------------------------------------------------------

    def _get_items(self) -> list[str]:
        return [
            self._items_list.item(i).text()
            for i in range(self._items_list.count())
        ]

    def _add_item(self):
        text = self._item_entry.text().strip()
        if text:
            existing = self._get_items()
            all_items = existing + [text]
            deduped = deduplicate_unfamiliar_items(all_items)
            if len(deduped) <= len(existing):
                self._status_label.setText(
                    "Item already in list (or duplicate after normalization).")
                self._status_label.setStyleSheet("color: #c00;")
            else:
                self._items_list.addItem(text)
                self._item_entry.clear()
                self._status_label.setText("")
                self._rebuild_meaning_editors()

    def _add_selected_text(self):
        """Add the currently selected text from the sentence editor."""
        cursor = self._sentence_edit.textCursor()
        selected = cursor.selectedText().strip()
        if not selected:
            self._status_label.setText(
                "No text selected in the sentence box.")
            self._status_label.setStyleSheet("color: #c00;")
            return

        existing = self._get_items()
        all_items = existing + [selected]
        deduped = deduplicate_unfamiliar_items(all_items)
        if len(deduped) <= len(existing):
            self._status_label.setText(
                "Selection already in list (or duplicate).")
            self._status_label.setStyleSheet("color: #c00;")
        else:
            self._items_list.addItem(selected)
            self._status_label.setText(
                f"Added: {selected[:50]}")
            self._status_label.setStyleSheet("color: #393;")
            self._rebuild_meaning_editors()

    def _remove_selected(self):
        for item in self._items_list.selectedItems():
            self._items_list.takeItem(self._items_list.row(item))
        self._status_label.setText("")
        self._rebuild_meaning_editors()

    # ------------------------------------------------------------------
    # AI availability
    # ------------------------------------------------------------------

    def _check_ai_available(self):
        ai_config = AIProviderConfig.from_settings(self._settings)
        if ai_config.configured:
            self._generate_btn.setEnabled(True)
            self._ai_status.setText(
                f"AI configured ({ai_config.model})")
        else:
            self._generate_btn.setEnabled(False)
            self._ai_status.setText(
                "AI not configured — add 'ai_api_key' in Settings.")

    # ------------------------------------------------------------------
    # Meaning editors
    # ------------------------------------------------------------------

    def _rebuild_meaning_editors(self):
        """Rebuild per-item meaning editor widgets, preserving existing content."""
        # Save current editor content keyed by expression
        saved: dict[str, str] = {}
        for expr, edit in self._meaning_widgets:
            saved[expr] = edit.toPlainText()

        # Clear existing widget containers and stretches
        while self._meanings_layout.count():
            item = self._meanings_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        self._meaning_widgets.clear()

        items = self._get_items()
        for i, expr in enumerate(items):
            row_layout = QHBoxLayout()
            label = QLabel(f"{expr}:")
            label.setMinimumWidth(100)
            row_layout.addWidget(label)

            edit = QTextEdit()
            edit.setAcceptRichText(False)
            edit.setMaximumHeight(60)
            edit.setPlaceholderText(f"Meaning for '{expr}'...")

            # Restore from saved, or from initial meanings, or blank
            existing = saved.get(expr)
            if existing:
                edit.setPlainText(existing)
            elif expr in self._initial_meanings:
                edit.setPlainText(self._initial_meanings[expr])

            row_layout.addWidget(edit, stretch=1)

            container = QWidget()
            container.setLayout(row_layout)
            self._meanings_layout.addWidget(container)
            self._meaning_widgets.append((expr, edit))

        # Add spacer at bottom
        self._meanings_layout.addStretch()

    # ------------------------------------------------------------------
    # AI generation (nonblocking)
    # ------------------------------------------------------------------

    def _generate_ai_meanings(self):
        """Start nonblocking AI generation for meanings."""
        items = self._get_items()
        if not items:
            self._ai_status.setText("Add at least one unfamiliar item first.")
            self._ai_status.setStyleSheet("color: #c00;")
            return

        sentence = self._sentence_edit.toPlainText().strip()
        if not sentence:
            self._ai_status.setText("Enter a sentence first.")
            self._ai_status.setStyleSheet("color: #c00;")
            return

        ai_config = AIProviderConfig.from_settings(self._settings)
        if not ai_config.configured:
            self._ai_status.setText("AI is not configured.")
            self._ai_status.setStyleSheet("color: #c00;")
            return

        explanation = self._settings.get("explanation_language", "Chinese")
        prompt = build_sentence_prompt(
            sentence, items, explanation_language=explanation)

        # Disable controls during generation
        self._generate_btn.setEnabled(False)
        self._sentence_edit.setEnabled(False)
        self._item_entry.setEnabled(False)
        self._add_sel_btn.setEnabled(False)
        self._items_list.setEnabled(False)
        self._save_btn.setEnabled(False)
        self._cancel_btn.setEnabled(False)
        self._ai_progress.setVisible(True)
        self._ai_status.setText("Generating meanings...")
        self._ai_status.setStyleSheet("color: #666;")

        self._ai_worker = _AIGenerateWorker(ai_config, prompt)

        def on_finished(raw_text):
            try:
                meanings = parse_sentence_meanings(raw_text, items)
                # Populate per-item meaning editors
                for i, (expr, edit) in enumerate(self._meaning_widgets):
                    if i < len(meanings):
                        edit.setPlainText(
                            meanings[i].contextual_meaning)
                # Also update back field
                formatted = "\n\n".join(
                    f"**{m.expression}**: {m.contextual_meaning}"
                    for m in meanings
                )
                self._back_edit.setPlainText(formatted)
                self._ai_status.setText(
                    f"Generated {len(meanings)} meaning(s). Review and edit before saving.")
                self._ai_status.setStyleSheet("color: #393;")
            except (AIParseError, AIValidationError) as e:
                self._ai_status.setText(f"AI parse error: {e}")
                self._ai_status.setStyleSheet("color: #c00;")

        def on_error(err):
            self._ai_status.setText(f"AI error: {err}")
            self._ai_status.setStyleSheet("color: #c00;")

        worker = self._ai_worker
        worker.result.connect(on_finished)
        worker.error.connect(on_error)
        worker.finished.connect(lambda: self._on_ai_thread_stopped(worker))
        worker.finished.connect(worker.deleteLater)
        worker.start()

    def _on_ai_thread_stopped(self, worker):
        if self._ai_worker is worker:
            self._ai_worker = None
            self._restore_ui_after_ai()
    def _restore_ui_after_ai(self):
        """Restore UI controls after AI generation completes/errors."""
        self._generate_btn.setEnabled(True)
        self._sentence_edit.setEnabled(True)
        self._item_entry.setEnabled(True)
        self._add_sel_btn.setEnabled(True)
        self._items_list.setEnabled(True)
        self._save_btn.setEnabled(True)
        self._cancel_btn.setEnabled(True)
        self._ai_progress.setVisible(False)

    # ------------------------------------------------------------------
    # Validation & Accept
    # ------------------------------------------------------------------

    def _validate(self):
        sentence = self._sentence_edit.toPlainText().strip()
        items = self._get_items()

        if not items:
            self._status_label.setText(
                "Add at least one unfamiliar item.")
            self._status_label.setStyleSheet("color: #c00;")
            return

        if not sentence:
            self._status_label.setText("Enter a sentence.")
            self._status_label.setStyleSheet("color: #c00;")
            return

        result = validate_unfamiliar_items(sentence, items)
        if result.valid:
            self._status_label.setText(
                f"✅ All {len(items)} items found in the sentence.")
            self._status_label.setStyleSheet("color: #393;")
        else:
            missing_str = ", ".join(result.missing)
            self._status_label.setText(
                f"❌ Not found in sentence: {missing_str}")
            self._status_label.setStyleSheet("color: #c00;")

    def _accept(self):
        sentence = self._sentence_edit.toPlainText().strip()
        items = self._get_items()

        if not sentence:
            QMessageBox.warning(
                self, "Validation",
                "Please enter a sentence before saving."
            )
            return

        if not items:
            QMessageBox.warning(
                self, "Validation",
                "Add at least one unfamiliar word or phrase before saving."
            )
            return

        result = validate_unfamiliar_items(sentence, items)
        if not result.valid:
            missing_str = ", ".join(result.missing)
            QMessageBox.warning(
                self, "Validation",
                f"These items were not found in the sentence:\n\n"
                f"{missing_str}\n\n"
                "Please remove them or fix the sentence before saving."
            )
            return

        # Build items with meanings from editors
        result_items: list[tuple[str, str]] = []
        for (expr, edit) in self._meaning_widgets:
            meaning = edit.toPlainText().strip()
            if not meaning:
                QMessageBox.warning(
                    self, "Validation",
                    f"Enter a contextual meaning for '{expr}' before saving."
                )
                return
            result_items.append((expr, meaning))

        # If meaning editors are out of sync, fall back to just expressions
        if len(result_items) != len(items):
            result_items = [(i, "") for i in items]

        self._result_sentence = sentence
        self._result_items = result_items
        # Keep the legacy/cache back field synchronized with structured rows.
        self._result_back = "\n\n".join(
            f"**{expr}**: {meaning}" for expr, meaning in result_items
        )
        self.accept()

    @property
    def result_sentence(self) -> str:
        return self._result_sentence

    @property
    def result_items(self) -> list:
        return self._result_items

    @property
    def result_back(self) -> str:
        return self._result_back

    def closeEvent(self, event):
        """Do not destroy the dialog while its blocking HTTP worker is active."""
        if self._ai_worker is not None and self._ai_worker.isRunning():
            event.ignore()
            return
        super().closeEvent(event)

    def reject(self):
        """Ignore Cancel while AI generation is active."""
        if self._ai_worker is not None and self._ai_worker.isRunning():
            return
        super().reject()

    def set_back_text(self, text: str):
        """Set the back/meaning text (e.g., after AI generation)."""
        self._back_edit.setPlainText(text)


# ---------------------------------------------------------------------------
# WordPhraseCardDialog
# ---------------------------------------------------------------------------

class WordPhraseCardDialog(QDialog):
    """Dialog for creating/editing a word/phrase-based card.

    Features:
      1. Enter the word/phrase (front) in a single text field.
      2. Up to 2 meanings as tabs, each with meaning text and example text.
         At least 1 non‑empty meaning AND example is required.
      3. Optionally auto‑generate meanings via AI with a Generate button.
         The dialog stays open; generation is nonblocking (QThread).
         On completion, meanings populate editable tabs.
      4. Manual users can edit meaning/example fields, add a second meaning
         tab, or close a tab (keeping at least one).
      5. Save is only accepted when:
         - At least 1 tab has non‑empty meaning AND non‑empty example.
         - At most 2 tabs exist.
    """

    def __init__(self, parent=None, title="Add Word/Phrase",
                 front="", meanings_data=None,
                 settings: dict | None = None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumSize(540, 460)
        self._result_front = ""
        self._result_meanings: list[tuple[str, str]] = []
        self._settings = settings or {}
        self._ai_worker: _AIGenerateWorker | None = None
        # [{meaning_edit, example_edit, page}]
        self._meaning_rows: list[dict] = []

        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        # --- Word/Phrase (Front) ---
        layout.addWidget(QLabel("Word / Phrase (Front):"))
        self._front_edit = QLineEdit()
        self._front_edit.setText(front)
        self._front_edit.setPlaceholderText("Enter the word or phrase to learn...")
        layout.addWidget(self._front_edit)

        # --- AI controls ---
        ai_row = QHBoxLayout()
        self._ai_status = QLabel("")
        ai_row.addWidget(self._ai_status, stretch=1)
        self._generate_btn = QPushButton("🤖 Generate Meanings")
        self._generate_btn.setToolTip(
            "Use AI to generate meanings with examples for this word/phrase."
        )
        self._generate_btn.clicked.connect(self._generate_ai_meanings)
        ai_row.addWidget(self._generate_btn)
        self._ai_progress = QProgressBar()
        self._ai_progress.setMaximum(0)
        self._ai_progress.setMaximumHeight(14)
        self._ai_progress.setVisible(False)
        ai_row.addWidget(self._ai_progress)
        layout.addLayout(ai_row)

        self._check_ai_availability()

        # --- Meanings (tabs) ---
        header_row = QHBoxLayout()
        header_row.addWidget(QLabel("<b>Meanings</b>"), stretch=1)
        self._add_meaning_btn = QPushButton("+ Add Meaning")
        self._add_meaning_btn.setToolTip("Add a second meaning tab (max 2).")
        # clicked emits a bool; wrap so it is not bound to meaning=
        self._add_meaning_btn.clicked.connect(
            lambda _checked=False: self._add_meaning_row()
        )
        header_row.addWidget(self._add_meaning_btn)
        layout.addLayout(header_row)

        self._meanings_tabs = QTabWidget()
        self._meanings_tabs.setDocumentMode(True)
        self._meanings_tabs.setMovable(False)
        self._meanings_tabs.setTabsClosable(True)
        self._meanings_tabs.tabCloseRequested.connect(self._on_tab_close_requested)
        self._meanings_tabs.setMinimumHeight(220)
        self._meanings_tabs.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        layout.addWidget(self._meanings_tabs, stretch=1)

        # Status
        self._status_label = QLabel("")
        self._status_label.setWordWrap(True)
        layout.addWidget(self._status_label)

        # --- Buttons ---
        btn_layout = QHBoxLayout()
        self._validate_btn = QPushButton("Validate")
        self._validate_btn.clicked.connect(self._validate)
        btn_layout.addWidget(self._validate_btn)

        btn_layout.addStretch()
        self._cancel_btn = QPushButton("Cancel")
        self._cancel_btn.clicked.connect(self._cancel_or_reject)
        btn_layout.addWidget(self._cancel_btn)

        self._save_btn = QPushButton("Save")
        self._save_btn.setStyleSheet(
            "background-color: #43A047; color: white; "
            "font-weight: bold; padding: 8px 20px;"
        )
        self._save_btn.clicked.connect(self._accept)
        btn_layout.addWidget(self._save_btn)
        layout.addLayout(btn_layout)

        # Pre-populate meanings if editing
        if meanings_data:
            for meaning, example in meanings_data:
                self._add_meaning_row(meaning=meaning, example=example)
        else:
            self._add_meaning_row()

        self._update_meaning_controls()

    # ------------------------------------------------------------------
    # Meaning tab management
    # ------------------------------------------------------------------

    @staticmethod
    def _make_meaning_field(placeholder: str, min_height: int = 72) -> QTextEdit:
        """Multi-line field with clean chrome; expands inside the tab page."""
        edit = QTextEdit()
        edit.setAcceptRichText(False)
        edit.setMinimumHeight(min_height)
        edit.setPlaceholderText(placeholder)
        edit.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        edit.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        edit.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        edit.setTabChangesFocus(True)
        doc = edit.document()
        if doc is not None:
            doc.setDocumentMargin(6)
        edit.setStyleSheet(
            "QTextEdit {"
            "  border: 1px solid #CFD8DC;"
            "  border-radius: 6px;"
            "  padding: 2px 6px;"
            "  background: #FFFFFF;"
            "}"
            "QTextEdit:focus {"
            "  border: 1px solid #42A5F5;"
            "}"
        )
        return edit

    def _add_meaning_row(self, meaning="", example=""):
        """Add a meaning tab with meaning + example fields."""
        if len(self._meaning_rows) >= 2:
            return

        page = QWidget()
        page_layout = QVBoxLayout(page)
        page_layout.setContentsMargins(10, 10, 10, 10)
        page_layout.setSpacing(6)

        meaning_label = QLabel("Meaning")
        meaning_label.setStyleSheet("color: #607D8B; font-size: 12px;")
        page_layout.addWidget(meaning_label)
        meaning_edit = self._make_meaning_field(
            "Meaning in your explanation language...", min_height=72
        )
        meaning_edit.setPlainText(meaning or "")
        page_layout.addWidget(meaning_edit, stretch=1)

        example_label = QLabel("Example sentence")
        example_label.setStyleSheet("color: #607D8B; font-size: 12px;")
        page_layout.addWidget(example_label)
        example_edit = self._make_meaning_field(
            "Example sentence showing usage...", min_height=72
        )
        example_edit.setPlainText(example or "")
        page_layout.addWidget(example_edit, stretch=1)

        tab_index = self._meanings_tabs.addTab(page, "")
        self._meaning_rows.append({
            "meaning_edit": meaning_edit,
            "example_edit": example_edit,
            "page": page,
        })
        self._rebuild_tab_labels()
        self._meanings_tabs.setCurrentIndex(tab_index)
        self._update_meaning_controls()
        meaning_edit.setFocus()

    def _on_tab_close_requested(self, index: int):
        """Close a meaning tab (keep at least one)."""
        if len(self._meaning_rows) <= 1:
            return
        if index < 0 or index >= len(self._meaning_rows):
            return
        row = self._meaning_rows.pop(index)
        self._meanings_tabs.removeTab(index)
        row["page"].deleteLater()
        self._rebuild_tab_labels()
        self._update_meaning_controls()

    def _rebuild_tab_labels(self):
        """Refresh Meaning N tab titles after add/remove."""
        for idx in range(self._meanings_tabs.count()):
            self._meanings_tabs.setTabText(idx, f"Meaning {idx + 1}")

    def _update_meaning_controls(self):
        """Sync Add button and tab-close affordance with current count."""
        count = len(self._meaning_rows)
        self._add_meaning_btn.setEnabled(count < 2)
        # Only allow closing when a second meaning exists.
        self._meanings_tabs.setTabsClosable(count > 1)
        bar = self._meanings_tabs.tabBar()
        if bar is None:
            return
        for idx in range(self._meanings_tabs.count()):
            close_btn = bar.tabButton(idx, bar.ButtonPosition.RightSide)
            if close_btn is not None:
                close_btn.setVisible(count > 1)

    # ------------------------------------------------------------------
    # AI availability
    # ------------------------------------------------------------------

    def _check_ai_availability(self):
        ai_config = AIProviderConfig.from_settings(self._settings)
        if ai_config.configured:
            self._generate_btn.setEnabled(True)
            self._ai_status.setText(f"AI configured ({ai_config.model})")
        else:
            self._generate_btn.setEnabled(False)
            self._ai_status.setText(
                "AI not configured — add 'ai_api_key' in Settings.")

    # ------------------------------------------------------------------
    # AI generation (nonblocking)
    # ------------------------------------------------------------------

    def _generate_ai_meanings(self):
        """Start nonblocking AI generation for meanings."""
        front = self._front_edit.text().strip()
        if not front:
            self._ai_status.setText("Enter a word/phrase first.")
            self._ai_status.setStyleSheet("color: #c00;")
            return

        ai_config = AIProviderConfig.from_settings(self._settings)
        if not ai_config.configured:
            self._ai_status.setText("AI is not configured.")
            self._ai_status.setStyleSheet("color: #c00;")
            return

        explanation = self._settings.get("explanation_language", "Chinese")
        prompt = build_word_phrase_prompt(
            front, explanation_language=explanation)

        # Disable controls during generation
        self._set_controls_enabled(False)
        self._generate_btn.setEnabled(False)
        self._ai_progress.setVisible(True)
        self._ai_status.setText("Generating meanings...")
        self._ai_status.setStyleSheet("color: #666;")

        self._ai_worker = _AIGenerateWorker(ai_config, prompt)
        def on_finished(raw_text):
            try:
                meanings = parse_word_phrase_meanings(raw_text)
                # Clear existing tabs
                self._clear_all_rows()
                # Populate from AI results
                for m in meanings:
                    # Extract meaning and example from contextual_meaning
                    meaning_text, example_text = self._split_meaning_example(
                        m.contextual_meaning)
                    self._add_meaning_row(meaning=meaning_text, example=example_text)
                if not self._meaning_rows:
                    self._add_meaning_row()
                self._ai_status.setText(
                    f"Generated {len(meanings)} meaning(s). Review and edit before saving."
                )
                self._ai_status.setStyleSheet("color: #393;")
            except (AIParseError, AIValidationError) as e:
                self._ai_status.setText(f"AI parse error: {e}")
                self._ai_status.setStyleSheet("color: #c00;")

        def on_error(err):
            self._ai_status.setText(f"AI error: {err}")
            self._ai_status.setStyleSheet("color: #c00;")

        worker = self._ai_worker
        worker.result.connect(on_finished)
        worker.error.connect(on_error)
        worker.finished.connect(lambda: self._on_ai_thread_stopped(worker))
        worker.finished.connect(worker.deleteLater)
        worker.start()

    def _on_ai_thread_stopped(self, worker):
        if self._ai_worker is worker:
            self._ai_worker = None
            self._restore_ui_after_ai()

    @staticmethod
    def _split_meaning_example(contextual_meaning: str) -> tuple[str, str]:
        """Split a contextual_meaning string into (meaning, example).

        The AI formats output like:
            "1. A domestic feline\\n*The cat sat on the mat.*"
        We extract the meaning text and example text.
        """
        import re
        text = contextual_meaning.strip()
        # Try to find pattern: "1. meaning\\n*example.*" or similar
        # First, strip the number prefix like "1. "
        text = re.sub(r'^\d+\.\s*', '', text)
        # Split on italic example: *...*
        example_match = re.search(r'\*(.+?)\*', text)
        if example_match:
            example = example_match.group(1).strip()
            meaning_text = text[:example_match.start()].strip()
            # Remove trailing newlines/punctuation from meaning
            meaning_text = meaning_text.rstrip('\n').rstrip()
            return meaning_text, example
        return text, ""

    def _clear_all_rows(self):
        """Remove all meaning tabs."""
        while self._meanings_tabs.count():
            page = self._meanings_tabs.widget(0)
            self._meanings_tabs.removeTab(0)
            if page is not None:
                page.deleteLater()
        self._meaning_rows.clear()
        self._update_meaning_controls()

    def _set_controls_enabled(self, enabled: bool):
        """Enable/disable all controls during AI generation or close."""
        self._front_edit.setEnabled(enabled)
        self._add_meaning_btn.setEnabled(enabled and len(self._meaning_rows) < 2)
        self._meanings_tabs.setEnabled(enabled)
        self._validate_btn.setEnabled(enabled)
        self._save_btn.setEnabled(enabled)
        self._cancel_btn.setEnabled(enabled)
        for row in self._meaning_rows:
            row["meaning_edit"].setEnabled(enabled)
            row["example_edit"].setEnabled(enabled)

    def _restore_ui_after_ai(self):
        """Restore controls; worker reference clears on thread termination."""
        self._set_controls_enabled(True)
        self._generate_btn.setEnabled(True)
        self._ai_progress.setVisible(False)
        self._update_meaning_controls()

    # ------------------------------------------------------------------
    # Close / Cancel safety
    # ------------------------------------------------------------------

    def _cancel_or_reject(self):
        """Ignore Cancel while AI generation is active."""
        if self._ai_worker is not None and self._ai_worker.isRunning():
            return
        super().reject()

    def closeEvent(self, event):
        """Do not destroy the dialog while its blocking HTTP worker is active."""
        if self._ai_worker is not None and self._ai_worker.isRunning():
            event.ignore()
            return
        super().closeEvent(event)

    # ------------------------------------------------------------------
    # Validation & Accept
    # ------------------------------------------------------------------

    def _get_rows_data(self) -> list[tuple[str, str]]:
        """Collect all meaning tabs as (meaning, example) tuples."""
        result = []
        for row in self._meaning_rows:
            meaning = row["meaning_edit"].toPlainText().strip()
            example = row["example_edit"].toPlainText().strip()
            if meaning or example:
                result.append((meaning, example))
        return result

    def _validate(self):
        rows = self._get_rows_data()
        if not rows:
            self._status_label.setText(
                "Add at least one meaning with an example sentence."
            )
            self._status_label.setStyleSheet("color: #c00;")
            return

        valid_count = 0
        for meaning, example in rows:
            if meaning and example:
                valid_count += 1

        if valid_count < 1:
            self._status_label.setText(
                "Each meaning must have both a meaning text and an example sentence."
            )
            self._status_label.setStyleSheet("color: #c00;")
            return

        self._status_label.setText(
            f"✅ {valid_count} valid meaning(s) ready to save."
        )
        self._status_label.setStyleSheet("color: #393;")

    def _accept(self):
        front = self._front_edit.text().strip()
        if not front:
            QMessageBox.warning(
                self, "Validation",
                "Please enter a word or phrase (front) before saving."
            )
            return

        rows = self._get_rows_data()
        if not rows:
            QMessageBox.warning(
                self, "Validation",
                "Add at least one meaning with both a meaning text "
                "and a non‑empty example sentence."
            )
            return

        if any(not meaning or not example for meaning, example in rows):
            QMessageBox.warning(
                self, "Validation",
                "Every non-empty meaning must contain both a meaning and an example."
            )
            return

        self._result_front = front
        self._result_meanings = rows
        self.accept()

    @property
    def result_front(self) -> str:
        return self._result_front

    @property
    def result_meanings(self) -> list[tuple[str, str]]:
        return self._result_meanings

    @property
    def result_back(self) -> str:
        """Build back text from meanings for backward compatibility."""
        parts = []
        for i, (meaning, example) in enumerate(self._result_meanings, 1):
            parts.append(f"{i}. {meaning}\n*{example}*")
        return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# DBCreationDialog
# ---------------------------------------------------------------------------
# DBCreationDialog
# ---------------------------------------------------------------------------

class DBCreationDialog(QDialog):
    """Dialog for creating a new database with category/subtype selection."""

    def __init__(self, parent=None, base_dir: str = ""):
        super().__init__(parent)
        self.setWindowTitle("Create New Database")
        self.setMinimumWidth(500)
        self._base_dir = base_dir
        self._selected_type: DatabaseType | None = None
        self._db_name = ""

        layout = QVBoxLayout(self)

        # Category / Subtype selection
        group = QGroupBox("Database Type")
        group_layout = QVBoxLayout(group)

        # Language-based group
        lang_label = QLabel("<b>Language-based</b>")
        group_layout.addWidget(lang_label)

        self._sentence_radio = QRadioButton("Sentence-based")
        self._sentence_radio.setToolTip(
            "Cards have a sentence with unfamiliar words/phrases. "
            "AI generates contextual meanings."
        )
        group_layout.addWidget(self._sentence_radio)

        self._word_phrase_radio = QRadioButton("Word/Phrase-based")
        self._word_phrase_radio.setToolTip(
            "Cards have a word or phrase on the front. "
            "AI generates meanings with examples."
        )
        group_layout.addWidget(self._word_phrase_radio)

        group_layout.addSpacing(10)

        # Knowledge-based group
        know_label = QLabel("<b>Knowledge-based</b>")
        group_layout.addWidget(know_label)

        self._knowledge_radio = QRadioButton(
            "Knowledge-based (generic front/back)")
        self._knowledge_radio.setToolTip(
            "Traditional front/back cards. No language AI prompts."
        )
        group_layout.addWidget(self._knowledge_radio)

        self._word_phrase_radio.setChecked(True)  # default
        layout.addWidget(group)

        # Database name
        name_layout = QHBoxLayout()
        name_layout.addWidget(QLabel("Name:"))
        self._name_edit = QLineEdit()
        self._name_edit.setPlaceholderText("e.g., French, Math_Topology")
        name_layout.addWidget(self._name_edit)
        layout.addLayout(name_layout)

        # Directory
        dir_layout = QHBoxLayout()
        dir_layout.addWidget(QLabel("Location:"))
        self._dir_label = QLabel("(auto)")
        self._dir_label.setStyleSheet("color: #888;")
        dir_layout.addWidget(self._dir_label, stretch=1)
        layout.addLayout(dir_layout)

        # Update the dir label when radio changes
        self._sentence_radio.toggled.connect(self._update_dir_label)
        self._word_phrase_radio.toggled.connect(self._update_dir_label)
        self._knowledge_radio.toggled.connect(self._update_dir_label)
        self._update_dir_label()

        # Buttons
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        create_btn = QPushButton("Create")
        create_btn.setStyleSheet(
            "background-color: #43A047; color: white; "
            "font-weight: bold; padding: 8px 20px;"
        )
        create_btn.clicked.connect(self._on_create)
        btn_layout.addWidget(create_btn)

        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(cancel_btn)
        layout.addLayout(btn_layout)

    def _update_dir_label(self):
        if self._sentence_radio.isChecked():
            subdir = "Language-based/Sentence-based"
        elif self._word_phrase_radio.isChecked():
            subdir = "Language-based/Word-Phrase-based"
        else:
            subdir = "Knowledge-based"
        root = self._base_dir or "db"
        # Show a short path for the project default; otherwise the full root.
        from .config import DIR_DB
        if not self._base_dir or os.path.abspath(self._base_dir) == (
            os.path.abspath(DIR_DB)
        ):
            display_root = "db"
        else:
            display_root = root.rstrip("/\\")
        self._dir_label.setText(f"{display_root}/{subdir}/")

    def _on_create(self):
        from .schema import validate_db_name
        name = self._name_edit.text().strip()
        if not name:
            QMessageBox.warning(
                self, "Error", "Please enter a database name.")
            return

        if not validate_db_name(name):
            QMessageBox.warning(
                self, "Error",
                f"Invalid database name: {name!r}\n\n"
                "Names must not contain path separators (/ or \\), "
                "'..', control characters, or be absolute paths."
            )
            return

        if self._sentence_radio.isChecked():
            self._selected_type = DatabaseType.LANGUAGE_SENTENCE
        elif self._word_phrase_radio.isChecked():
            self._selected_type = DatabaseType.LANGUAGE_WORD_PHRASE
        else:
            self._selected_type = DatabaseType.KNOWLEDGE

        self._db_name = name
        self.accept()

    @property
    def selected_type(self) -> DatabaseType | None:
        return self._selected_type

    @property
    def db_name(self) -> str:
        return self._db_name
