"""Legacy word/phrase card dialog retained for import compatibility."""

from PyQt6.QtCore import QSize, Qt
from PyQt6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QTabWidget,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .ai_parser import (
    AIParseError,
    AIValidationError,
    MAX_WORD_PHRASE_MEANINGS,
    parse_word_phrase_meanings,
)
from .ai_provider import AIProviderConfig, build_word_phrase_prompt
from .form_helpers import _AIGenerateWorker, _apply_ui_font


def _create_ai_worker(config, prompt):
    """Create a worker through the public compatibility facade."""
    from .forms import _AIGenerateWorker as worker_class

    return worker_class(config, prompt)


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
        btn_layout.addStretch()
        self._cancel_btn = QPushButton("Cancel")
        self._cancel_btn.clicked.connect(self._cancel_or_reject)
        btn_layout.addWidget(self._cancel_btn)

        self._save_btn = QPushButton("Save")
        self._save_btn.setObjectName("wordPhraseSaveButton")
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
        self._front_edit.textChanged.connect(self._update_save_enabled)
        self._update_save_enabled()

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
            self._update_save_enabled()
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
        self._update_save_enabled()

    def _is_busy(self) -> bool:
        return self._ai_worker is not None and self._ai_worker.isRunning()

    def _update_save_enabled(self) -> None:
        """Dim Save when front is empty or AI is busy."""
        if self._is_busy():
            self._save_btn.setEnabled(False)
            return
        has_front = bool(self._front_edit.text().strip())
        self._save_btn.setEnabled(has_front)

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
                "AI not configured — set API key under Settings → AI Providers.")

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

        self._ai_worker = _create_ai_worker(ai_config, prompt)
        def on_finished(raw_text):
            try:
                meanings = parse_word_phrase_meanings(raw_text)
                # Clear existing tabs
                self._clear_all_rows()
                # Populate from AI results
                for m in meanings:
                    meaning_text = m.meaning
                    example_text = m.example
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

    def _clear_all_rows(self):
        """Remove all meaning tabs."""
        while self._meanings_tabs.count():
            page = self._meanings_tabs.widget(0)
            self._meanings_tabs.removeTab(0)
            if page is not None:
                page.deleteLater()
        self._meaning_rows.clear()
        self._update_meaning_controls()

    def _on_ai_thread_stopped(self, worker):
        if self._ai_worker is worker:
            self._ai_worker = None
            self._restore_ui_after_ai()

    def _set_controls_enabled(self, enabled: bool):
        """Enable/disable all controls during AI generation or close."""
        self._front_edit.setEnabled(enabled)
        self._add_meaning_btn.setEnabled(
            enabled and len(self._meaning_rows) < MAX_WORD_PHRASE_MEANINGS
        )
        self._meanings_tabs.setEnabled(enabled)
        self._cancel_btn.setEnabled(enabled)
        for row in self._meaning_rows:
            row["meaning_edit"].setEnabled(enabled)
            row["example_edit"].setEnabled(enabled)
        # Close buttons follow tab enabled state via parent; re-sync ownership.
        if enabled:
            self._update_meaning_controls()
        else:
            self._save_btn.setEnabled(False)

    def _restore_ui_after_ai(self):
        """Restore controls; worker reference clears on thread termination."""
        self._set_controls_enabled(True)
        self._generate_btn.setEnabled(True)
        self._ai_progress.setVisible(False)
        self._update_meaning_controls()
        self._update_save_enabled()

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
    # Validation & Accept (Save is the only gate; dialog stays open on fail)
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
