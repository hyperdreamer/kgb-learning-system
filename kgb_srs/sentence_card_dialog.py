"""Sentence-card entry dialog and its AI-assisted workflow."""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from .ai_parser import (
    AIParseError,
    AIValidationError,
    parse_membership_claims,
    parse_sense_assignment,
)
from .ai_provider import (
    AIProviderConfig,
    build_membership_prompt,
    build_sense_assignment_prompt,
)
from . import form_helpers
from .senses import get_sense, list_senses_for_expression
from .validation import (
    apply_ai_membership_claims,
    deduplicate_unfamiliar_items,
    surface_form_in_sentence,
    validate_unfamiliar_items,
)


def _create_ai_worker(config, prompt):
    """Create a worker through the canonical helper module."""
    return form_helpers.create_ai_worker(config, prompt)


class SentenceCardDialog(QDialog):
    """Dialog for creating/editing a sentence-based card.

    Flow:
      1. Enter the sentence.
      2. Select or manually type unfamiliar words/phrases from the sentence.
         - Use "Add selected text" to add highlighted text from the sentence.
      3. Adding a new item automatically generates its contextual meaning when
         AI is configured. Generate Meaning can replace it (or manual text).
         The dialog stays open; AI generation is nonblocking (QThread).
      4. Save runs membership + meaning checks. On failure the dialog stays
         open so the user can fix or Cancel. Save is dimmed while empty.
    """

    def __init__(
        self,
        parent=None,
        title="Add Sentence Card",
        sentence="",
        items=None,
        back="",
        settings: dict | None = None,
        conn=None,
        settings_file=None,
    ):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumSize(560, 520)
        self._result_sentence = ""
        self._result_items: list = []
        self._result_back = ""
        # Lemma → surface accepted by residual AI membership (re-verified at insert).
        self._result_verified_surfaces: dict[str, str] = {}
        self._settings = settings or {}
        self._settings_file = settings_file
        self._geometry_persisted = False
        self._conn = conn  # optional: sentence DB for sense inventory
        form_helpers.apply_ui_font(self, self._settings, parent)
        self._ai_worker: form_helpers._AIGenerateWorker | None = None
        # Membership results are only committed after the exact worker that
        # produced them has emitted ``finished``.
        self._membership_worker = None
        self._membership_pending_accept = None
        # `back` is accepted for API compatibility with main_window but is
        # not shown or edited; meanings come from items pairs only.
        _ = back
        # Persistent meaning + sense_id store for every list item.
        # Meanings may be typed by the user or supplied by AI.
        self._meanings: dict[str, str] = {}
        self._sense_ids: dict[str, int | None] = {}
        # Expression → previously AI-verified residual surface form.
        self._persisted_verified_surfaces: dict[str, str] = {}
        if items:
            for item in items:
                if isinstance(item, tuple):
                    expr = str(item[0])
                    self._meanings[expr] = str(item[1]) if len(item) > 1 else ""
                    sid = None
                    if len(item) > 2 and item[2] is not None:
                        try:
                            sid = int(item[2])
                        except (TypeError, ValueError):
                            sid = None
                    self._sense_ids[expr] = sid
                    if len(item) > 3 and item[3] is not None:
                        surface = str(item[3]).strip()
                        if surface:
                            self._persisted_verified_surfaces[expr] = surface
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
        layout.addWidget(
            QLabel("Unfamiliar words/phrases (select from sentence or type below):")
        )
        self._items_list = QListWidget()
        # Extended selection still allows multi-remove; meaning editor
        # always follows the current (primary) list item.
        self._items_list.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection
        )
        self._items_list.setMaximumHeight(120)
        layout.addWidget(self._items_list)

        # Manual entry
        entry_layout = QHBoxLayout()
        self._item_entry = QLineEdit()
        self._item_entry.setPlaceholderText("Type a word/phrase and press Add")
        self._item_entry.returnPressed.connect(self._add_item)
        entry_layout.addWidget(self._item_entry)

        self._add_btn = QPushButton("Add")
        self._add_btn.clicked.connect(self._add_item)
        entry_layout.addWidget(self._add_btn)

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
            "Generate a contextual meaning with AI. New items are generated "
            "automatically; click again to replace the current generated or "
            "manually entered meaning."
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

        # --- Meaning (single card for the selected item) ---
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
        btn_layout.addStretch()

        self._cancel_btn = QPushButton("Cancel")
        self._cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(self._cancel_btn)

        self._save_btn = QPushButton("Save")
        self._save_btn.setObjectName("sentenceSaveButton")
        self._save_btn.setStyleSheet(
            "background-color: #43A047; color: white; "
            "font-weight: bold; padding: 8px 20px;"
        )
        self._save_btn.clicked.connect(self._accept)
        btn_layout.addWidget(self._save_btn)
        layout.addLayout(btn_layout)

        # Restore last-used size (or defaults). Do this after widgets exist
        # so it is not overridden by layout defaults / parent-relative sizing.
        self._restore_dialog_geometry()

        # Connect double-click on list to removal
        self._items_list.itemDoubleClicked.connect(self._remove_selected)
        self._items_list.itemSelectionChanged.connect(self._on_item_selection_changed)
        # Dim Save while empty; re-evaluate as the user types / edits items.
        self._sentence_edit.textChanged.connect(self._update_save_enabled)

        # Select first item when editing an existing card so Meaning shows.
        if self._items_list.count() > 0:
            self._items_list.setCurrentRow(0)

        self._on_item_selection_changed()

        # Check AI config availability (also gates Generate on selection)
        self._check_ai_available()
        self._update_save_enabled()

    # ------------------------------------------------------------------
    # Item management
    # ------------------------------------------------------------------

    def _get_items(self) -> list[str]:
        return [
            self._items_list.item(i).text() for i in range(self._items_list.count())
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
        if self._is_busy():
            self._generate_btn.setEnabled(False)
            return
        ai_config = AIProviderConfig.from_settings(self._settings)
        has_selection = self._selected_expression() is not None
        self._generate_btn.setEnabled(bool(ai_config.configured and has_selection))

    def _is_busy(self) -> bool:
        """True until every background AI worker has emitted ``finished``."""
        if self._ai_worker is not None:
            return True
        membership = getattr(self, "_membership_worker", None)
        return membership is not None

    def _update_save_enabled(self) -> None:
        """Dim Save when empty or while AI is busy; stay open on failed Save."""
        if self._is_busy():
            self._save_btn.setEnabled(False)
            return
        has_sentence = bool(self._sentence_edit.toPlainText().strip())
        has_items = self._items_list.count() > 0
        self._save_btn.setEnabled(has_sentence and has_items)

    def _set_meaning_editor_enabled(self, enabled: bool) -> None:
        """Enable manual meaning entry unless an AI task is in progress."""
        for _, edit in self._meaning_widgets:
            edit.setEnabled(enabled)

    def _set_ai_controls_enabled(self, enabled: bool) -> None:
        """Lock every mutable dialog control while AI work is unresolved."""
        for control in (
            self._sentence_edit,
            self._item_entry,
            self._add_btn,
            self._add_sel_btn,
            self._items_list,
            self._generate_btn,
            self._save_btn,
            self._cancel_btn,
        ):
            control.setEnabled(enabled)
        self._remove_btn.setEnabled(enabled and bool(self._items_list.selectedItems()))
        self._set_meaning_editor_enabled(enabled)

    def _on_item_selection_changed(self) -> None:
        """Selection drives Remove, Generate, and the single Meaning card."""
        self._update_remove_selected_enabled()
        self._rebuild_meaning_editors()
        self._update_generate_enabled()
        self._update_save_enabled()

    def _add_item(self):
        text = self._item_entry.text().strip()
        if text:
            existing = self._get_items()
            all_items = existing + [text]
            deduped = deduplicate_unfamiliar_items(all_items)
            if len(deduped) <= len(existing):
                self._status_label.setText(
                    "Item already in list (or duplicate after normalization)."
                )
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
                self._generate_new_item_meaning()

    def _add_selected_text(self):
        """Add the currently selected text from the sentence editor."""
        cursor = self._sentence_edit.textCursor()
        selected = cursor.selectedText().strip()
        if not selected:
            self._status_label.setText("No text selected in the sentence box.")
            self._status_label.setStyleSheet("color: #c00;")
            return

        existing = self._get_items()
        all_items = existing + [selected]
        deduped = deduplicate_unfamiliar_items(all_items)
        if len(deduped) <= len(existing):
            self._status_label.setText("Selection already in list (or duplicate).")
            self._status_label.setStyleSheet("color: #c00;")
        else:
            self._persist_active_meaning()
            self._meanings.setdefault(selected, "")
            self._sense_ids.setdefault(selected, None)
            self._items_list.addItem(selected)
            self._status_label.setText(f"Added: {selected[:50]}")
            self._status_label.setStyleSheet("color: #393;")
            self._items_list.setCurrentRow(self._items_list.count() - 1)
            self._on_item_selection_changed()
            self._generate_new_item_meaning()

    def _generate_new_item_meaning(self) -> None:
        """Generate immediately after an item is added when AI is configured."""
        ai_config = AIProviderConfig.from_settings(self._settings)
        if ai_config.configured:
            self._generate_ai_meanings()

    def _remove_selected(self):
        self._persist_active_meaning()
        for item in self._items_list.selectedItems():
            expr = item.text()
            self._items_list.takeItem(self._items_list.row(item))
            self._meanings.pop(expr, None)
            self._sense_ids.pop(expr, None)
            self._persisted_verified_surfaces.pop(expr, None)
        self._status_label.setText("")
        self._on_item_selection_changed()

    # ------------------------------------------------------------------
    # AI availability
    # ------------------------------------------------------------------

    def _check_ai_available(self):
        ai_config = AIProviderConfig.from_settings(self._settings)
        if ai_config.configured:
            self._ai_status.setText(f"AI configured ({ai_config.model})")
        else:
            self._ai_status.setText(
                "AI not configured — set API key under Settings → AI Providers."
            )
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
                "Add unfamiliar words/phrases, then select one and type a "
                "meaning or click Generate Meaning."
            )
            empty.setStyleSheet("color: #90A4AE; font-style: italic; padding: 8px 2px;")
            empty.setWordWrap(True)
            self._meanings_layout.addWidget(empty)
            self._meanings_layout.addStretch()
            return

        expr = self._selected_expression()
        if expr is None:
            empty = QLabel(
                "Select an unfamiliar word/phrase, then type a meaning or click "
                "Generate Meaning."
            )
            empty.setStyleSheet("color: #90A4AE; font-style: italic; padding: 8px 2px;")
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
        expr_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        card_layout.addWidget(expr_label)

        edit = self._make_meaning_field(
            f"Type a meaning for '{expr}' or use Generate Meaning..."
        )
        edit.setPlainText(self._meanings.get(expr, ""))
        edit.setReadOnly(False)
        edit.setToolTip(
            "Type a contextual meaning, or use Generate Meaning to fill it with AI."
        )
        edit.textChanged.connect(self._on_active_meaning_changed)
        card_layout.addWidget(edit)
        self._meanings_layout.addWidget(card)
        self._meaning_widgets.append((expr, edit))
        self._active_meaning_expr = expr
        self._update_sense_source_label(expr)
        self._meanings_layout.addStretch()

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
                "No meaning yet - type one or click Generate Meaning."
            )

    def _on_active_meaning_changed(self) -> None:
        """Keep the store in sync when the user types or edits a meaning."""
        if getattr(self, "_programmatic_meaning_update", False):
            return
        if self._active_meaning_expr is None or not self._meaning_widgets:
            return
        expr, edit = self._meaning_widgets[0]
        if expr == self._active_meaning_expr:
            self._meanings[expr] = edit.toPlainText()
            # A user edit must not retain an AI-reused sense link.
            self._sense_ids[expr] = None
            self._update_sense_source_label(expr)

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
        """Generate and replace the selected item's meaning on AI success."""
        if self._is_busy():
            return

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

        # Prevent item mutation or another request until this worker finishes.
        self._set_ai_controls_enabled(False)
        self._ai_progress.setVisible(True)
        if prior:
            self._ai_status.setText(
                f"Checking {len(prior)} prior sense(s) for '{expr}'…"
            )
        else:
            self._ai_status.setText(f"Creating first sense for '{expr}'…")
        self._ai_status.setStyleSheet("color: #666;")

        self._ai_worker = _create_ai_worker(ai_config, prompt)
        target_expr = expr

        def on_finished(raw_text):
            try:
                assignment = parse_sense_assignment(raw_text, target_expr, prior_ids)
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
                    status = f"Reused sense #{assignment.sense_id} for '{target_expr}'."
                else:
                    # create
                    meaning_text = assignment.meaning.strip()
                    if not meaning_text:
                        raise AIValidationError("Create action returned empty meaning")
                    self._meanings[target_expr] = meaning_text
                    self._sense_ids[target_expr] = None
                    status = (
                        f"Created new meaning for '{target_expr}' (will link on Save)."
                    )

                if self._active_meaning_expr == target_expr and self._meaning_widgets:
                    edit = self._meaning_widgets[0][1]
                    self._programmatic_meaning_update = True
                    edit.blockSignals(True)
                    try:
                        edit.setPlainText(self._meanings[target_expr])
                    finally:
                        edit.blockSignals(False)
                        self._programmatic_meaning_update = False
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
        self._set_ai_controls_enabled(True)
        self._ai_progress.setVisible(False)
        self._update_generate_enabled()
        self._update_save_enabled()

    # ------------------------------------------------------------------
    # Validation & Accept (Save is the only gate; dialog stays open on fail)
    # ------------------------------------------------------------------

    def _accept(self):
        """Save: validate membership + meanings; stay open on any failure."""
        sentence = self._sentence_edit.toPlainText().strip()
        items = self._get_items()

        if not sentence:
            QMessageBox.warning(
                self, "Validation", "Please enter a sentence before saving."
            )
            return

        if not items:
            QMessageBox.warning(
                self,
                "Validation",
                "Add at least one unfamiliar word or phrase before saving.",
            )
            return

        # Meanings first: cheaper than membership / optional AI residual.
        self._persist_active_meaning()
        for expr in items:
            meaning = (self._meanings.get(expr) or "").strip()
            if not meaning:
                QMessageBox.warning(
                    self,
                    "Missing meaning",
                    f"Add a meaning for '{expr}' before saving.\n\n"
                    "Select the item, then type a meaning or click "
                    "Generate Meaning.",
                )
                for i in range(self._items_list.count()):
                    list_item = self._items_list.item(i)
                    if list_item is not None and list_item.text() == expr:
                        self._items_list.setCurrentRow(i)
                        break
                self._on_item_selection_changed()
                return

        result = validate_unfamiliar_items(sentence, items)
        retained_surfaces = {
            expr: surface
            for expr in result.missing
            if (surface := self._persisted_verified_surfaces.get(expr))
            and surface_form_in_sentence(sentence, surface)
        }
        missing = [expr for expr in result.missing if expr not in retained_surfaces]
        if not missing:
            self._finish_accept(sentence, items, verified_surfaces=retained_surfaces)
            return

        # Local-first residual: optional AI only for items local rules missed.
        ai_config = AIProviderConfig.from_settings(self._settings)
        missing_str = ", ".join(missing)
        if not ai_config.configured:
            QMessageBox.warning(
                self,
                "Validation",
                f"These items were not found in the sentence:\n\n"
                f"{missing_str}\n\n"
                "Please remove them or fix the sentence before saving.",
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

        self._start_membership_ai_check(
            sentence, items, missing, ai_config, retained_surfaces
        )

    def _start_membership_ai_check(
        self,
        sentence: str,
        items: list[str],
        missing: list[str],
        ai_config: AIProviderConfig,
        retained_surfaces: dict[str, str] | None = None,
    ) -> None:
        """Run AI membership fallback for residual missing items only."""
        if getattr(self, "_membership_worker", None) is not None:
            return

        prompt = build_membership_prompt(sentence, missing)
        self._membership_sentence = sentence
        self._membership_items = items
        self._membership_missing = list(missing)
        self._membership_retained_surfaces = dict(retained_surfaces or {})
        self._membership_pending_accept = None

        self._status_label.setText(f"🤖 AI checking {len(missing)} residual item(s)…")
        self._status_label.setStyleSheet("color: #666;")
        self._ai_progress.setVisible(True)
        self._ai_progress.setRange(0, 0)
        self._set_ai_controls_enabled(False)

        worker = _create_ai_worker(ai_config, prompt)
        worker.result.connect(
            lambda response_text, w=worker: self._on_membership_ai_result(
                response_text, w
            )
        )
        worker.error.connect(
            lambda message, w=worker: self._on_membership_ai_error(message, w)
        )
        worker.finished.connect(lambda w=worker: self._on_membership_ai_finished(w))
        self._membership_worker = worker
        worker.start()

    def _on_membership_ai_result(self, response_text: str, worker=None) -> None:
        """Validate a result but defer acceptance until its worker finishes."""
        if worker is not None and worker is not self._membership_worker:
            return
        sentence = getattr(self, "_membership_sentence", "")
        items = getattr(self, "_membership_items", [])
        missing = getattr(self, "_membership_missing", [])
        try:
            claims = parse_membership_claims(response_text, missing)
            residual = apply_ai_membership_claims(sentence, missing, claims)
        except (AIParseError, AIValidationError) as e:
            self._membership_pending_accept = None
            QMessageBox.warning(
                self,
                "AI membership check",
                f"Could not use AI residual check:\n{e}\n\n"
                f"Still unmatched: {', '.join(missing)}",
            )
            return
        except Exception as e:
            self._membership_pending_accept = None
            QMessageBox.warning(
                self,
                "AI membership check",
                f"Unexpected error in AI residual check:\n{e}",
            )
            return

        if not residual.valid:
            self._membership_pending_accept = None
            missing_str = ", ".join(residual.missing)
            QMessageBox.warning(
                self,
                "Validation",
                f"These items were still not found after AI check:\n\n"
                f"{missing_str}\n\n"
                "Please remove them or fix the sentence before saving.",
            )
            self._status_label.setText(f"❌ Still not found: {missing_str}")
            self._status_label.setStyleSheet("color: #c00;")
            return

        recovered = len(missing)
        self._status_label.setText(
            f"✅ AI residual check accepted {recovered} item(s); finalizing…"
        )
        self._status_label.setStyleSheet("color: #393;")
        verified_surfaces = dict(getattr(self, "_membership_retained_surfaces", {}))
        verified_surfaces.update(residual.accepted_surfaces or {})
        self._membership_pending_accept = (
            sentence,
            list(items),
            verified_surfaces,
        )

    def _on_membership_ai_error(self, message: str, worker=None) -> None:
        if worker is not None and worker is not self._membership_worker:
            return
        self._membership_pending_accept = None
        missing = getattr(self, "_membership_missing", [])
        missing_str = ", ".join(missing) if missing else "(unknown)"
        QMessageBox.warning(
            self,
            "AI membership check",
            f"AI residual check failed:\n{message}\n\nStill unmatched: {missing_str}",
        )
        self._status_label.setText(
            f"❌ AI residual check failed; still missing: {missing_str}"
        )
        self._status_label.setStyleSheet("color: #c00;")

    def _on_membership_ai_finished(self, worker=None) -> None:
        """Clear only the matching worker, then commit a queued valid result."""
        active_worker = getattr(self, "_membership_worker", None)
        if active_worker is None or (
            worker is not None and worker is not active_worker
        ):
            return

        pending_accept = self._membership_pending_accept
        self._membership_pending_accept = None
        self._membership_worker = None
        active_worker.deleteLater()
        self._ai_progress.setVisible(False)

        if pending_accept is not None:
            sentence, items, verified_surfaces = pending_accept
            self._finish_accept(sentence, items, verified_surfaces)
            return

        self._set_ai_controls_enabled(True)
        self._update_generate_enabled()
        self._update_save_enabled()

    def _finish_accept(
        self,
        sentence: str,
        items: list[str],
        verified_surfaces: dict[str, str] | None = None,
    ) -> None:
        """Finalize Save after membership validation has passed."""
        self._persist_active_meaning()

        surfaces = dict(verified_surfaces or {})
        result_items: list[tuple[str, str, int | None, str]] = []
        for expr in items:
            meaning = (self._meanings.get(expr) or "").strip()
            if not meaning:
                # Defense in depth — _accept already checks meanings first.
                QMessageBox.warning(
                    self,
                    "Missing meaning",
                    f"Add a meaning for '{expr}' before saving.\n\n"
                    "Select the item, then type a meaning or click "
                    "Generate Meaning.",
                )
                for i in range(self._items_list.count()):
                    list_item = self._items_list.item(i)
                    if list_item is not None and list_item.text() == expr:
                        self._items_list.setCurrentRow(i)
                        break
                self._on_item_selection_changed()
                return
            sense_id = self._sense_ids.get(expr)
            # AI residual surface (e.g. lie → lay) for highlight / order.
            surface = str(surfaces.get(expr) or "").strip()
            result_items.append((expr, meaning, sense_id, surface))

        self._result_sentence = sentence
        self._result_items = result_items
        self._result_verified_surfaces = surfaces
        # Back is derived from structured meanings (no separate editor).
        # Order by first surface appearance in the sentence; number when >1.
        from .validation import (
            format_sentence_meaning_lines,
            sort_items_by_sentence_order,
        )

        ordered = sort_items_by_sentence_order(sentence, result_items)
        self._result_back = "\n\n".join(format_sentence_meaning_lines(ordered))
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

    @property
    def result_verified_surfaces(self) -> dict[str, str]:
        """Lemma→surface pairs accepted by residual membership checks."""
        return dict(self._result_verified_surfaces)

    def _restore_dialog_geometry(self) -> None:
        """Open at last-used size (or defaults), never below the minimum."""
        from .config import DEFAULT_SETTINGS

        try:
            w = int(
                self._settings.get(
                    "sentence_dialog_width",
                    DEFAULT_SETTINGS["sentence_dialog_width"],
                )
            )
            h = int(
                self._settings.get(
                    "sentence_dialog_height",
                    DEFAULT_SETTINGS["sentence_dialog_height"],
                )
            )
        except (TypeError, ValueError):
            w = int(DEFAULT_SETTINGS["sentence_dialog_width"])
            h = int(DEFAULT_SETTINGS["sentence_dialog_height"])
        self.resize(max(self.minimumWidth(), w), max(self.minimumHeight(), h))

    def _persist_dialog_geometry(self) -> None:
        """Remember current size for the next open (main-window settings bag)."""
        if not self._settings:
            return
        # Only write when this is a real app settings dict (has main geometry).
        if "width" not in self._settings or "height" not in self._settings:
            return
        self._settings["sentence_dialog_width"] = self.width()
        self._settings["sentence_dialog_height"] = self.height()
        try:
            from .config import save_settings

            if self._settings_file is None:
                save_settings(self._settings)
            else:
                save_settings(self._settings, self._settings_file)
        except OSError:
            pass

    def _persist_dialog_geometry_once(self) -> None:
        """Persist geometry once for each completed dialog lifecycle."""
        if self._geometry_persisted:
            return
        self._geometry_persisted = True
        self._persist_dialog_geometry()

    def accept(self):
        self._persist_dialog_geometry_once()
        super().accept()

    def closeEvent(self, event):
        """Do not destroy the dialog before its HTTP worker emits finished."""
        if self._ai_worker is not None:
            event.ignore()
            return
        membership = getattr(self, "_membership_worker", None)
        if membership is not None:
            event.ignore()
            return
        self._persist_dialog_geometry_once()
        super().closeEvent(event)

    def reject(self):
        """Ignore Cancel until active AI work has emitted finished."""
        if self._ai_worker is not None:
            return
        membership = getattr(self, "_membership_worker", None)
        if membership is not None:
            return
        self._persist_dialog_geometry_once()
        super().reject()
