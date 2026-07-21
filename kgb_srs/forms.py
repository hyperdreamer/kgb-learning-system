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
    QToolButton,
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QSize
from PyQt6.QtGui import QPainter, QPen, QColor, QPixmap, QIcon, QFont

from .catalog import DatabaseType
from .validation import (
    validate_unfamiliar_items,
    deduplicate_unfamiliar_items,
    apply_ai_membership_claims,
)
from .ai_provider import (
    AIProviderConfig,
    AIClient,
    _make_http_call,
    build_sentence_prompt,
    build_sense_assignment_prompt,
    build_word_phrase_prompt,
    build_membership_prompt,
    AIMissingConfigError,
)
from .ai_parser import (
    parse_sentence_meanings,
    parse_sense_assignment,
    parse_word_phrase_meanings,
    parse_membership_claims,
    AIParseError,
    AIValidationError,
    MAX_WORD_PHRASE_MEANINGS,
)
from .senses import list_senses_for_expression, get_sense, create_or_get_sense
import json
import urllib.error


def _apply_ui_font(widget, settings: dict | None, parent=None) -> None:
    """Apply Appearance → UI Font to a dialog/widget.

    Prefer explicit settings (font_family / font_size). Fall back to the
    parent widget font when settings are incomplete so card editors still
    track the main window chrome font.
    """
    settings = settings or {}
    family = settings.get("font_family")
    size = settings.get("font_size")
    if family and size:
        try:
            widget.setFont(QFont(str(family), int(size)))
            return
        except (TypeError, ValueError):
            pass
    if parent is not None:
        widget.setFont(parent.font())


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
      3. Select one item, then optionally generate its contextual meaning via AI.
         The dialog stays open; generation is nonblocking (QThread).
         On completion, the selected item's meaning field is filled — Save is next.
      4. Validate that all unfamiliar items are in the sentence.
      5. Save is a separate user action; back text is derived from meanings.
    """

    def __init__(self, parent=None, title="Add Sentence Card",
                 sentence="", items=None, back="",
                 settings: dict | None = None,
                 conn=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumSize(560, 520)
        self._result_sentence = ""
        self._result_items: list = []
        self._result_back = ""
        self._settings = settings or {}
        self._conn = conn  # optional: sentence DB for sense inventory
        _apply_ui_font(self, self._settings, parent)
        self._ai_worker: _AIGenerateWorker | None = None
        # `back` is accepted for API compatibility with main_window but is
        # not shown or edited; meanings come from items pairs only.
        _ = back
        # Persistent meaning + sense_id store for every list item.
        # Meanings are AI-assigned (reuse existing sense or create new).
        self._meanings: dict[str, str] = {}
        self._sense_ids: dict[str, int | None] = {}
        if items:
            for item in items:
                if isinstance(item, tuple):
                    expr = str(item[0])
                    self._meanings[expr] = (
                        str(item[1]) if len(item) > 1 else ""
                    )
                    sid = None
                    if len(item) > 2 and item[2] is not None:
                        try:
                            sid = int(item[2])
                        except (TypeError, ValueError):
                            sid = None
                    self._sense_ids[expr] = sid
                else:
                    self._meanings[str(item)] = ""
                    self._sense_ids[str(item)] = None
        # Currently displayed expression in the single meaning editor.
        self._active_meaning_expr: str | None = None
        # Compatibility alias used by older tests / callers that inspect
        # dialog._meaning_widgets as [(expr, QTextEdit), ...].
        self._meaning_widgets: list[tuple[str, QTextEdit]] = []

        layout = QVBoxLayout(self)
        layout.setSpacing(8)

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
        # Extended selection still allows multi-remove; meaning editor
        # always follows the current (primary) list item.
        self._items_list.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection)
        self._items_list.setMaximumHeight(120)
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
        remove_btn.setEnabled(False)
        remove_btn.setToolTip("Select one or more items in the list to remove.")
        self._remove_btn = remove_btn
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

        # --- AI controls (selected item only) ---
        ai_row = QHBoxLayout()
        self._ai_status = QLabel("")
        self._ai_status.setStyleSheet("color: #666;")
        ai_row.addWidget(self._ai_status, stretch=1)
        self._generate_btn = QPushButton("🤖 Generate Meaning")
        self._generate_btn.setToolTip(
            "AI reuses a prior sense for this word/phrase when it fits this "
            "sentence, or creates a new sense (contextual). Primary path — "
            "meanings are not typed manually."
        )
        self._generate_btn.clicked.connect(self._generate_ai_meanings)
        self._generate_btn.setEnabled(False)
        ai_row.addWidget(self._generate_btn)
        self._ai_progress = QProgressBar()
        self._ai_progress.setRange(0, 0)  # indeterminate
        self._ai_progress.setVisible(False)
        self._ai_progress.setMaximumHeight(14)
        ai_row.addWidget(self._ai_progress)
        layout.addLayout(ai_row)

        # --- Meaning (single card for the selected item; AI-filled) ---
        layout.addWidget(QLabel("<b>Meaning</b>"))
        self._sense_source_label = QLabel("")
        self._sense_source_label.setStyleSheet("color: #607D8B;")
        self._sense_source_label.setWordWrap(True)
        layout.addWidget(self._sense_source_label)
        self._meanings_layout = QVBoxLayout()
        self._meanings_layout.setSpacing(8)
        self._meanings_layout.setContentsMargins(0, 0, 0, 0)
        self._meanings_container = QWidget()
        self._meanings_container.setLayout(self._meanings_layout)
        self._meanings_container.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        layout.addWidget(self._meanings_container)

        # --- Buttons ---
        btn_layout = QHBoxLayout()
        validate_btn = QPushButton("Validate")
        validate_btn.clicked.connect(self._validate)
        btn_layout.addWidget(validate_btn)
        self._validate_btn = validate_btn

        btn_layout.addStretch()

        self._cancel_btn = QPushButton("Cancel")
        self._cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(self._cancel_btn)

        self._save_btn = QPushButton("Save")
        self._save_btn.setStyleSheet(
            "background-color: #43A047; color: white; "
            "font-weight: bold; padding: 8px 20px;"
        )
        self._save_btn.clicked.connect(self._accept)
        btn_layout.addWidget(self._save_btn)
        layout.addLayout(btn_layout)

        if parent:
            w = min(max(580, int(parent.width() * 0.6)), 850)
            h = min(max(520, int(parent.height() * 0.75)), 750)
            self.resize(w, h)

        # Connect double-click on list to removal
        self._items_list.itemDoubleClicked.connect(self._remove_selected)
        self._items_list.itemSelectionChanged.connect(
            self._on_item_selection_changed
        )

        # Select first item when editing an existing card so Meaning shows.
        if self._items_list.count() > 0:
            self._items_list.setCurrentRow(0)

        self._on_item_selection_changed()

        # Check AI config availability (also gates Generate on selection)
        self._check_ai_available()

    # ------------------------------------------------------------------
    # Item management
    # ------------------------------------------------------------------

    def _get_items(self) -> list[str]:
        return [
            self._items_list.item(i).text()
            for i in range(self._items_list.count())
        ]

    def _selected_expression(self) -> str | None:
        """Primary selected list item (current row), or None."""
        item = self._items_list.currentItem()
        if item is None:
            selected = self._items_list.selectedItems()
            item = selected[0] if selected else None
        if item is None:
            return None
        text = item.text().strip()
        return text or None

    def _persist_active_meaning(self) -> None:
        """Write the visible meaning editor back into the store."""
        if self._active_meaning_expr is None:
            return
        if not self._meaning_widgets:
            return
        expr, edit = self._meaning_widgets[0]
        if expr != self._active_meaning_expr:
            return
        self._meanings[expr] = edit.toPlainText()

    def _update_remove_selected_enabled(self) -> None:
        """Dim Remove Selected when the list has no selection."""
        has_selection = bool(self._items_list.selectedItems())
        self._remove_btn.setEnabled(has_selection)

    def _update_generate_enabled(self) -> None:
        """Generate Meaning requires AI config + exactly one selected item."""
        if self._ai_worker is not None and self._ai_worker.isRunning():
            self._generate_btn.setEnabled(False)
            return
        ai_config = AIProviderConfig.from_settings(self._settings)
        has_selection = self._selected_expression() is not None
        self._generate_btn.setEnabled(bool(ai_config.configured and has_selection))

    def _on_item_selection_changed(self) -> None:
        """Selection drives Remove, Generate, and the single Meaning card."""
        self._update_remove_selected_enabled()
        self._rebuild_meaning_editors()
        self._update_generate_enabled()

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
                self._persist_active_meaning()
                self._meanings.setdefault(text, "")
                self._sense_ids.setdefault(text, None)
                self._items_list.addItem(text)
                self._item_entry.clear()
                self._status_label.setText("")
                # Select the newly added item so its Meaning card shows.
                self._items_list.setCurrentRow(self._items_list.count() - 1)
                self._on_item_selection_changed()

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
            self._persist_active_meaning()
            self._meanings.setdefault(selected, "")
            self._sense_ids.setdefault(selected, None)
            self._items_list.addItem(selected)
            self._status_label.setText(
                f"Added: {selected[:50]}")
            self._status_label.setStyleSheet("color: #393;")
            self._items_list.setCurrentRow(self._items_list.count() - 1)
            self._on_item_selection_changed()

    def _remove_selected(self):
        self._persist_active_meaning()
        for item in self._items_list.selectedItems():
            expr = item.text()
            self._items_list.takeItem(self._items_list.row(item))
            self._meanings.pop(expr, None)
            self._sense_ids.pop(expr, None)
        self._status_label.setText("")
        self._on_item_selection_changed()

    # ------------------------------------------------------------------
    # AI availability
    # ------------------------------------------------------------------

    def _check_ai_available(self):
        ai_config = AIProviderConfig.from_settings(self._settings)
        if ai_config.configured:
            self._ai_status.setText(
                f"AI configured ({ai_config.model})")
        else:
            self._ai_status.setText(
                "AI not configured — add 'ai_api_key' in Settings.")
        self._update_generate_enabled()

    # ------------------------------------------------------------------
    # Meaning editor (selected item only)
    # ------------------------------------------------------------------

    @staticmethod
    def _make_meaning_field(placeholder: str, min_height: int = 52) -> QTextEdit:
        """Soft-bordered multi-line field matching WordPhrase chrome."""
        edit = QTextEdit()
        edit.setAcceptRichText(False)
        edit.setMinimumHeight(min_height)
        edit.setMaximumHeight(72)
        edit.setPlaceholderText(placeholder)
        edit.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
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

    def _rebuild_meaning_editors(self):
        """Show a single meaning card for the selected list item only."""
        self._persist_active_meaning()

        while self._meanings_layout.count():
            item = self._meanings_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        self._meaning_widgets.clear()
        self._active_meaning_expr = None
        if hasattr(self, "_sense_source_label"):
            self._sense_source_label.setText("")

        items = self._get_items()
        if not items:
            empty = QLabel(
                "Add unfamiliar words/phrases, then select one and "
                "click Generate Meaning."
            )
            empty.setStyleSheet(
                "color: #90A4AE; font-style: italic; padding: 8px 2px;"
            )
            empty.setWordWrap(True)
            self._meanings_layout.addWidget(empty)
            self._meanings_layout.addStretch()
            return

        expr = self._selected_expression()
        if expr is None:
            empty = QLabel(
                "Select an unfamiliar word/phrase, then Generate Meaning."
            )
            empty.setStyleSheet(
                "color: #90A4AE; font-style: italic; padding: 8px 2px;"
            )
            empty.setWordWrap(True)
            self._meanings_layout.addWidget(empty)
            self._meanings_layout.addStretch()
            return

        # Keep store keys aligned with the current list.
        for key in list(self._meanings.keys()):
            if key not in items:
                del self._meanings[key]
                self._sense_ids.pop(key, None)
        self._meanings.setdefault(expr, "")
        self._sense_ids.setdefault(expr, None)

        card = QWidget()
        card.setObjectName("sentenceMeaningCard")
        # Needed so background/border QSS paints under all styles.
        card.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        card.setStyleSheet(
            "QWidget#sentenceMeaningCard {"
            "  background: #FAFBFC;"
            "  border: 1px solid #E0E6EA;"
            "  border-radius: 8px;"
            "}"
        )
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(10, 8, 10, 8)
        card_layout.setSpacing(4)

        expr_label = QLabel(expr)
        font = expr_label.font()
        font.setBold(True)
        expr_label.setFont(font)
        expr_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        card_layout.addWidget(expr_label)

        edit = self._make_meaning_field(
            f"Use Generate Meaning for '{expr}' in this sentence…"
        )
        edit.setPlainText(self._meanings.get(expr, ""))
        # AI-primary path: field is read-only display of assigned sense.
        # Store still syncs via textChanged (programmatic fills + repair).
        edit.setReadOnly(True)
        edit.setToolTip(
            "Filled by Generate Meaning (reuse prior sense or create new). "
            "Double-click to unlock manual repair if AI is wrong."
        )
        edit.textChanged.connect(self._on_active_meaning_changed)
        edit.mouseDoubleClickEvent = (  # type: ignore[method-assign]
            lambda event, e=edit: self._unlock_meaning_edit(e, event)
        )
        card_layout.addWidget(edit)
        self._meanings_layout.addWidget(card)
        self._meaning_widgets.append((expr, edit))
        self._active_meaning_expr = expr
        self._update_sense_source_label(expr)
        self._meanings_layout.addStretch()

    def _unlock_meaning_edit(self, edit: QTextEdit, event) -> None:
        """Escape hatch: double-click unlocks manual meaning repair."""
        edit.setReadOnly(False)
        edit.setStyleSheet(
            "QTextEdit {"
            "  border: 1px solid #FFA726;"
            "  border-radius: 6px;"
            "  padding: 2px 6px;"
            "  background: #FFF8E1;"
            "}"
            "QTextEdit:focus {"
            "  border: 1px solid #FB8C00;"
            "}"
        )
        # Clear linked sense — manual repair creates a new sense on save.
        if self._active_meaning_expr is not None:
            self._sense_ids[self._active_meaning_expr] = None
            if hasattr(self, "_sense_source_label"):
                self._sense_source_label.setText(
                    "Manual repair — will create/match sense on Save."
                )
        QTextEdit.mouseDoubleClickEvent(edit, event)

    def _update_sense_source_label(self, expr: str) -> None:
        if not hasattr(self, "_sense_source_label"):
            return
        sid = self._sense_ids.get(expr)
        meaning = (self._meanings.get(expr) or "").strip()
        if sid is not None:
            self._sense_source_label.setText(
                f"Sense #{sid}" + (" (linked)" if meaning else "")
            )
        elif meaning:
            self._sense_source_label.setText(
                "Meaning set — sense will be resolved on Save."
            )
        else:
            self._sense_source_label.setText(
                "No meaning yet — click Generate Meaning."
            )

    def _on_active_meaning_changed(self) -> None:
        """Keep the store in sync when the user repairs meaning manually."""
        if self._active_meaning_expr is None or not self._meaning_widgets:
            return
        expr, edit = self._meaning_widgets[0]
        if expr == self._active_meaning_expr:
            self._meanings[expr] = edit.toPlainText()
            # Manual edit detaches prior sense link.
            self._sense_ids[expr] = None

    # ------------------------------------------------------------------
    # AI generation (nonblocking, selected item only)
    # ------------------------------------------------------------------

    def _prior_senses_for(self, expr: str) -> list[tuple[int, str]]:
        """Load prior (sense_id, meaning) pairs from the sentence DB."""
        if self._conn is None:
            return []
        try:
            senses = list_senses_for_expression(self._conn, expr)
        except Exception:
            return []
        return [(s.id, s.meaning) for s in senses]

    def _generate_ai_meanings(self):
        """Assign a sense for the selected item: reuse prior or create new."""
        expr = self._selected_expression()
        if not expr:
            self._ai_status.setText("Select one unfamiliar item first.")
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

        self._persist_active_meaning()

        explanation = self._settings.get("explanation_language", "Chinese")
        prior = self._prior_senses_for(expr)
        prior_ids = [sid for sid, _ in prior]
        prompt = build_sense_assignment_prompt(
            sentence,
            expr,
            prior,
            explanation_language=explanation,
        )

        # Disable controls during generation
        self._generate_btn.setEnabled(False)
        self._sentence_edit.setEnabled(False)
        self._item_entry.setEnabled(False)
        self._add_sel_btn.setEnabled(False)
        self._items_list.setEnabled(False)
        self._save_btn.setEnabled(False)
        self._cancel_btn.setEnabled(False)
        self._ai_progress.setVisible(True)
        if prior:
            self._ai_status.setText(
                f"Checking {len(prior)} prior sense(s) for '{expr}'…"
            )
        else:
            self._ai_status.setText(
                f"Creating first sense for '{expr}'…"
            )
        self._ai_status.setStyleSheet("color: #666;")

        self._ai_worker = _AIGenerateWorker(ai_config, prompt)
        target_expr = expr

        def on_finished(raw_text):
            try:
                assignment = parse_sense_assignment(
                    raw_text, target_expr, prior_ids
                )
                if assignment.action == "reuse" and assignment.sense_id is not None:
                    sense = None
                    if self._conn is not None:
                        sense = get_sense(self._conn, assignment.sense_id)
                    if sense is None:
                        # Fall back to prior list text
                        meaning_text = next(
                            (m for sid, m in prior if sid == assignment.sense_id),
                            "",
                        )
                    else:
                        meaning_text = sense.meaning
                    if not meaning_text:
                        raise AIValidationError(
                            f"Reused sense #{assignment.sense_id} has no meaning text"
                        )
                    self._meanings[target_expr] = meaning_text
                    self._sense_ids[target_expr] = assignment.sense_id
                    status = (
                        f"Reused sense #{assignment.sense_id} for "
                        f"'{target_expr}'."
                    )
                else:
                    # create
                    meaning_text = assignment.meaning.strip()
                    if not meaning_text:
                        raise AIValidationError("Create action returned empty meaning")
                    # Materialize sense in inventory when DB is available.
                    sense_id = None
                    if self._conn is not None:
                        sense = create_or_get_sense(
                            self._conn, target_expr, meaning_text
                        )
                        sense_id = sense.id
                        meaning_text = sense.meaning
                    self._meanings[target_expr] = meaning_text
                    self._sense_ids[target_expr] = sense_id
                    if sense_id is not None:
                        status = (
                            f"Created sense #{sense_id} for '{target_expr}'."
                        )
                    else:
                        status = (
                            f"Created new meaning for '{target_expr}' "
                            f"(will link on Save)."
                        )

                if (
                    self._active_meaning_expr == target_expr
                    and self._meaning_widgets
                ):
                    edit = self._meaning_widgets[0][1]
                    edit.setReadOnly(True)
                    edit.setPlainText(self._meanings[target_expr])
                self._update_sense_source_label(target_expr)
                self._ai_status.setText(status + " Ready to save.")
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
        self._sentence_edit.setEnabled(True)
        self._item_entry.setEnabled(True)
        self._add_sel_btn.setEnabled(True)
        self._items_list.setEnabled(True)
        self._save_btn.setEnabled(True)
        self._cancel_btn.setEnabled(True)
        self._ai_progress.setVisible(False)
        self._update_generate_enabled()

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
            return

        missing_str = ", ".join(result.missing)
        ai_config = AIProviderConfig.from_settings(self._settings)
        if ai_config.configured:
            self._status_label.setText(
                f"❌ Local check missed: {missing_str}  "
                f"(Save can ask AI for residual forms)")
        else:
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
        if result.valid:
            self._finish_accept(sentence, items)
            return

        # Local-first residual: optional AI only for items local rules missed.
        ai_config = AIProviderConfig.from_settings(self._settings)
        missing_str = ", ".join(result.missing)
        if not ai_config.configured:
            QMessageBox.warning(
                self, "Validation",
                f"These items were not found in the sentence:\n\n"
                f"{missing_str}\n\n"
                "Please remove them or fix the sentence before saving."
            )
            return

        reply = QMessageBox.question(
            self,
            "Local check incomplete",
            f"Local rules could not match:\n\n{missing_str}\n\n"
            "Ask the AI provider to check residual inflected / irregular "
            "forms?\n\n(AI claims are verified against the sentence text.)",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        self._start_membership_ai_check(sentence, items, result.missing, ai_config)

    def _start_membership_ai_check(
        self,
        sentence: str,
        items: list[str],
        missing: list[str],
        ai_config: AIProviderConfig,
    ) -> None:
        """Run AI membership fallback for residual missing items only."""
        if getattr(self, "_membership_worker", None) is not None:
            return

        prompt = build_membership_prompt(sentence, missing)
        self._membership_sentence = sentence
        self._membership_items = items
        self._membership_missing = list(missing)

        self._status_label.setText(
            f"🤖 AI checking {len(missing)} residual item(s)…")
        self._status_label.setStyleSheet("color: #666;")
        self._ai_progress.setVisible(True)
        self._ai_progress.setRange(0, 0)
        self._save_btn.setEnabled(False)
        self._validate_btn.setEnabled(False)
        self._generate_btn.setEnabled(False)

        worker = _AIGenerateWorker(ai_config, prompt)
        worker.result.connect(self._on_membership_ai_result)
        worker.error.connect(self._on_membership_ai_error)
        worker.finished.connect(self._on_membership_ai_finished)
        self._membership_worker = worker
        worker.start()

    def _on_membership_ai_result(self, response_text: str) -> None:
        sentence = getattr(self, "_membership_sentence", "")
        items = getattr(self, "_membership_items", [])
        missing = getattr(self, "_membership_missing", [])
        try:
            claims = parse_membership_claims(response_text, missing)
            residual = apply_ai_membership_claims(sentence, missing, claims)
        except (AIParseError, AIValidationError) as e:
            QMessageBox.warning(
                self, "AI membership check",
                f"Could not use AI residual check:\n{e}\n\n"
                f"Still unmatched: {', '.join(missing)}"
            )
            return
        except Exception as e:
            QMessageBox.warning(
                self, "AI membership check",
                f"Unexpected error in AI residual check:\n{e}"
            )
            return

        if not residual.valid:
            missing_str = ", ".join(residual.missing)
            QMessageBox.warning(
                self, "Validation",
                f"These items were still not found after AI check:\n\n"
                f"{missing_str}\n\n"
                "Please remove them or fix the sentence before saving."
            )
            self._status_label.setText(
                f"❌ Still not found: {missing_str}")
            self._status_label.setStyleSheet("color: #c00;")
            return

        recovered = len(missing)
        self._status_label.setText(
            f"✅ AI residual check accepted {recovered} item(s).")
        self._status_label.setStyleSheet("color: #393;")
        self._finish_accept(sentence, items)

    def _on_membership_ai_error(self, message: str) -> None:
        missing = getattr(self, "_membership_missing", [])
        missing_str = ", ".join(missing) if missing else "(unknown)"
        QMessageBox.warning(
            self, "AI membership check",
            f"AI residual check failed:\n{message}\n\n"
            f"Still unmatched: {missing_str}"
        )
        self._status_label.setText(
            f"❌ AI residual check failed; still missing: {missing_str}")
        self._status_label.setStyleSheet("color: #c00;")

    def _on_membership_ai_finished(self) -> None:
        worker = getattr(self, "_membership_worker", None)
        self._membership_worker = None
        if worker is not None:
            worker.deleteLater()
        self._ai_progress.setVisible(False)
        self._save_btn.setEnabled(True)
        self._validate_btn.setEnabled(True)
        self._update_generate_enabled()

    def _finish_accept(self, sentence: str, items: list[str]) -> None:
        """Finalize Save after membership validation has passed."""
        self._persist_active_meaning()

        result_items: list[tuple[str, str, int | None]] = []
        for expr in items:
            meaning = (self._meanings.get(expr) or "").strip()
            if not meaning:
                QMessageBox.warning(
                    self, "Validation",
                    f"Generate a meaning for '{expr}' before saving.\n\n"
                    f"Select the item and click Generate Meaning."
                )
                # Focus the incomplete item so the user can fill it.
                for i in range(self._items_list.count()):
                    list_item = self._items_list.item(i)
                    if list_item is not None and list_item.text() == expr:
                        self._items_list.setCurrentRow(i)
                        break
                self._on_item_selection_changed()
                return
            sense_id = self._sense_ids.get(expr)
            result_items.append((expr, meaning, sense_id))

        self._result_sentence = sentence
        self._result_items = result_items
        # Back is derived from structured meanings (no separate editor).
        self._result_back = "\n\n".join(
            f"**{expr}**: {meaning}" for expr, meaning, _sid in result_items
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


# ---------------------------------------------------------------------------
# WordPhraseCardDialog
# ---------------------------------------------------------------------------

class WordPhraseCardDialog(QDialog):
    """Dialog for creating/editing a word/phrase-based card.

    Features:
      1. Enter the word/phrase (front) in a single text field.
      2. Up to MAX_WORD_PHRASE_MEANINGS meanings as tabs, each with meaning
         text and example text. At least 1 non‑empty meaning AND example
         is required.
      3. Optionally auto‑generate meanings via AI with a Generate button.
         The dialog stays open; generation is nonblocking (QThread).
         On completion, meanings populate editable tabs.
      4. Manual users can edit meaning/example fields, add more meaning
         tabs, or close a tab (keeping at least one).
      5. Save is only accepted when:
         - At least 1 tab has non‑empty meaning AND non‑empty example.
         - At most MAX_WORD_PHRASE_MEANINGS tabs exist.
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
        _apply_ui_font(self, self._settings, parent)
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
        self._add_meaning_btn.setToolTip(
            f"Add another meaning tab (max {MAX_WORD_PHRASE_MEANINGS})."
        )
        # clicked emits a bool; wrap so it is not bound to meaning=
        self._add_meaning_btn.clicked.connect(
            lambda _checked=False: self._add_meaning_row()
        )
        header_row.addWidget(self._add_meaning_btn)
        layout.addLayout(header_row)

        self._meanings_tabs = QTabWidget()
        self._meanings_tabs.setDocumentMode(True)
        self._meanings_tabs.setMovable(False)
        # Closable via owned per-tab X buttons (not the style's default
        # close widget, which can double-paint under Plasma/Breeze).
        self._meanings_tabs.setTabsClosable(False)
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
        if len(self._meaning_rows) >= MAX_WORD_PHRASE_MEANINGS:
            return

        page = QWidget()
        page_layout = QVBoxLayout(page)
        page_layout.setContentsMargins(10, 10, 10, 10)
        page_layout.setSpacing(6)

        meaning_label = QLabel("Meaning")
        # Color only — do not hard-code font-size so UI Font applies.
        meaning_label.setStyleSheet("color: #607D8B;")
        page_layout.addWidget(meaning_label)
        meaning_edit = self._make_meaning_field(
            "Meaning in your explanation language...", min_height=72
        )
        meaning_edit.setPlainText(meaning or "")
        page_layout.addWidget(meaning_edit, stretch=1)

        example_label = QLabel("Example sentence")
        example_label.setStyleSheet("color: #607D8B;")
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
        """Close a meaning tab by index (keep at least one)."""
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

    @staticmethod
    def _make_tab_close_button() -> QToolButton:
        """Flat X control we own (avoids style double-paint of default close)."""
        btn = QToolButton()
        btn.setObjectName("meaningTabClose")
        btn.setAutoRaise(True)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setFixedSize(16, 16)
        btn.setIconSize(QSize(10, 10))
        btn.setToolTip("Close this meaning")
        btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        # Draw a simple monochrome X so styles cannot add a second glyph.
        pix = QPixmap(10, 10)
        pix.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pix)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        pen = QPen(QColor("#546E7A"))
        pen.setWidth(1)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.drawLine(2, 2, 8, 8)
        painter.drawLine(8, 2, 2, 8)
        painter.end()
        btn.setIcon(QIcon(pix))
        btn.setStyleSheet(
            "QToolButton#meaningTabClose {"
            "  border: none;"
            "  background: transparent;"
            "  padding: 0;"
            "  margin: 0;"
            "}"
            "QToolButton#meaningTabClose:hover {"
            "  background: rgba(0, 0, 0, 0.10);"
            "  border-radius: 3px;"
            "}"
            "QToolButton#meaningTabClose:pressed {"
            "  background: rgba(0, 0, 0, 0.16);"
            "}"
        )
        return btn

    def _attach_owned_close_button(self, index: int):
        """Install one owned close X on tab *index* (replaces any style button)."""
        bar = self._meanings_tabs.tabBar()
        if bar is None or index < 0 or index >= self._meanings_tabs.count():
            return
        # Clear left-side slot so styles cannot leave a ghost close there.
        bar.setTabButton(index, bar.ButtonPosition.LeftSide, None)

        close_btn = self._make_tab_close_button()
        # Capture index at click time via the bar, not a stale default.
        close_btn.clicked.connect(
            lambda _checked=False, b=close_btn: self._on_owned_close_clicked(b)
        )
        bar.setTabButton(index, bar.ButtonPosition.RightSide, close_btn)

    def _on_owned_close_clicked(self, button: QToolButton):
        """Map an owned close button back to its current tab index."""
        bar = self._meanings_tabs.tabBar()
        if bar is None:
            return
        for idx in range(self._meanings_tabs.count()):
            if bar.tabButton(idx, bar.ButtonPosition.RightSide) is button:
                self._on_tab_close_requested(idx)
                return

    def _update_meaning_controls(self):
        """Sync Add button and owned per-tab close X with current count."""
        count = len(self._meaning_rows)
        self._add_meaning_btn.setEnabled(count < MAX_WORD_PHRASE_MEANINGS)
        # Keep setTabsClosable(False) so Qt never installs its own close
        # widgets (those can double-paint under Plasma/Breeze). We own the X.
        self._meanings_tabs.setTabsClosable(False)

        bar = self._meanings_tabs.tabBar()
        if bar is None:
            return

        show_close = count > 1
        for idx in range(self._meanings_tabs.count()):
            if show_close:
                existing = bar.tabButton(idx, bar.ButtonPosition.RightSide)
                if existing is None or not isinstance(existing, QToolButton):
                    self._attach_owned_close_button(idx)
                else:
                    existing.setVisible(True)
            else:
                # Sole tab: no close control at all.
                bar.setTabButton(idx, bar.ButtonPosition.RightSide, None)
                bar.setTabButton(idx, bar.ButtonPosition.LeftSide, None)

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
        self._add_meaning_btn.setEnabled(
            enabled and len(self._meaning_rows) < MAX_WORD_PHRASE_MEANINGS
        )
        self._meanings_tabs.setEnabled(enabled)
        self._validate_btn.setEnabled(enabled)
        self._save_btn.setEnabled(enabled)
        self._cancel_btn.setEnabled(enabled)
        for row in self._meaning_rows:
            row["meaning_edit"].setEnabled(enabled)
            row["example_edit"].setEnabled(enabled)
        # Close buttons follow tab enabled state via parent; re-sync ownership.
        if enabled:
            self._update_meaning_controls()

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
            "AI assigns senses in a shared catalog; a word/phrase "
            "dictionary can be derived from those senses."
        )
        group_layout.addWidget(self._sentence_radio)

        self._word_phrase_radio = QRadioButton(
            "Word/Phrase-based (derived only)"
        )
        self._word_phrase_radio.setToolTip(
            "Read-only dictionary projection of the shared sense catalog. "
            "Created automatically with each sentence database — "
            "manual add/edit is disabled."
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

        self._sentence_radio.setChecked(True)  # default: authoring path
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
