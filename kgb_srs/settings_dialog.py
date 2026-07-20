"""Categorized application settings dialog."""

from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtGui import QFontDatabase
from PyQt6.QtMultimedia import QAudioOutput, QMediaPlayer
from PyQt6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from .ai_provider import AIProviderConfig, create_ai_test_worker
from .config import DIR_DB, save_settings
from .db import DB_SUFFIX
from .secret_line_edit import SecretLineEdit
from .tts import TTSWorker, VoiceListWorker

_PREVIEW_SAMPLE = "Hello. This is a preview of the selected voice."

_GENDER_BUTTON_STYLE = """
QPushButton {
    padding: 4px 12px;
    border: 1px solid #aaa;
    background: #f5f5f5;
}
QPushButton:checked {
    background: #2b6cb0;
    color: white;
    border-color: #2b6cb0;
}
QPushButton:hover:!checked {
    background: #e8e8e8;
}
"""


class _VoiceRowWidget(QWidget):
    """Compact list-row: voice name, locale/gender meta, per-row preview."""

    def __init__(self, short_name, locale, gender, on_preview, parent=None):
        super().__init__(parent)
        self.short_name = short_name
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 4, 6, 4)
        layout.setSpacing(8)

        text_col = QVBoxLayout()
        text_col.setContentsMargins(0, 0, 0, 0)
        text_col.setSpacing(0)
        name_label = QLabel(short_name)
        name_label.setStyleSheet("font-weight: 600;")
        meta_label = QLabel(f"{locale} · {gender}")
        meta_label.setStyleSheet("color: #666;")
        text_col.addWidget(name_label)
        text_col.addWidget(meta_label)
        layout.addLayout(text_col, 1)

        play_btn = QPushButton("▶")
        play_btn.setFixedWidth(32)
        play_btn.setToolTip("Preview this voice")
        play_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        play_btn.clicked.connect(lambda: on_preview(short_name))
        layout.addWidget(play_btn)
        self.preview_button = play_btn


class SettingsDialog(QDialog):
    """Edit settings in categories while keeping changes staged until save."""

    CATEGORIES = (
        "General",
        "Appearance",
        "Audio & Speech",
        "AI Provider",
    )

    def __init__(self, settings, parent=None, current_size=None):
        super().__init__(parent)
        if parent is not None:
            self.setFont(parent.font())
        self.settings = settings
        self.current_size = current_size
        self.current_voice = settings.get(
            "tts_voice", "en-US-AvaMultilingualNeural"
        )
        self.current_language = settings.get("tts_language", "") or ""
        self._all_voices = []  # (ShortName, Locale, Gender, FriendlyName)
        self.ai_test_worker = None
        self.preview_tts_worker = None
        self.setWindowTitle("App Settings")
        self.setMinimumSize(620, 480)

        self.preview_player = QMediaPlayer(self)
        self.preview_audio = QAudioOutput(self)
        self.preview_player.setAudioOutput(self.preview_audio)

        outer_layout = QVBoxLayout(self)
        content_layout = QHBoxLayout()
        outer_layout.addLayout(content_layout, 1)

        self.category_list = QListWidget()
        self.category_list.setObjectName("settingsCategoryList")
        self.category_list.addItems(self.CATEGORIES)
        self.category_list.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.category_list.setSizePolicy(
            QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Expanding
        )
        # Size from font metrics rather than the pre-polish column hint, which
        # can grow after the native style initializes and elide larger fonts.
        longest_category = max(
            self.category_list.fontMetrics().horizontalAdvance(label)
            for label in self.CATEGORIES
        )
        category_width = longest_category + 112
        self.category_list.setMinimumWidth(category_width)
        self.category_list.setMaximumWidth(category_width)
        content_layout.addWidget(self.category_list)

        self.pages = QStackedWidget()
        self.pages.setObjectName("settingsPages")
        content_layout.addWidget(self.pages, 1)

        self._build_general_page()
        self._build_appearance_page()
        self._build_audio_page()
        self._build_ai_page()

        button_layout = QHBoxLayout()
        button_layout.addStretch()
        self.save_button = QPushButton("Save && Apply")
        self.save_button.setObjectName("saveSettingsButton")
        self.save_button.setStyleSheet("background-color: #ccffcc;")
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setObjectName("cancelSettingsButton")
        button_layout.addWidget(self.save_button)
        button_layout.addWidget(self.cancel_button)
        outer_layout.addLayout(button_layout)

        self.category_list.currentRowChanged.connect(self.pages.setCurrentIndex)
        self.category_list.setCurrentRow(0)
        self.save_button.clicked.connect(self.save_and_apply)
        self.cancel_button.clicked.connect(self.reject)

        self._start_voice_worker()

    @staticmethod
    def _page():
        page = QWidget()
        layout = QFormLayout(page)
        layout.setFieldGrowthPolicy(
            QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow
        )
        return page, layout

    def _build_general_page(self):
        page, layout = self._page()
        self.default_database_input = QLineEdit(
            self.settings.get("default_database", "")
        )
        self.default_database_input.setObjectName("defaultDatabaseInput")
        self.default_database_input.setPlaceholderText(
            "No default database selected"
        )
        self.default_database_input.setReadOnly(True)
        self.database_browse_button = QPushButton("Browse…")
        self.database_browse_button.setObjectName("databaseBrowseButton")

        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.addWidget(self.default_database_input, 1)
        row_layout.addWidget(self.database_browse_button)
        layout.addRow("Default Database:", row)
        self.database_browse_button.clicked.connect(self.browse_database)
        self.pages.addWidget(page)

    def _build_appearance_page(self):
        page, layout = self._page()
        current_width, current_height = self.current_size or (
            self.settings["width"], self.settings["height"]
        )
        self.window_width_input = QSpinBox()
        self.window_width_input.setObjectName("windowWidthInput")
        self.window_width_input.setRange(400, 3000)
        self.window_width_input.setValue(current_width)
        layout.addRow("Window Width:", self.window_width_input)

        self.window_height_input = QSpinBox()
        self.window_height_input.setObjectName("windowHeightInput")
        self.window_height_input.setRange(400, 3000)
        self.window_height_input.setValue(current_height)
        layout.addRow("Window Height:", self.window_height_input)

        self.font_family_input = QComboBox()
        self.font_family_input.setObjectName("fontFamilyInput")
        self.font_family_input.addItems(QFontDatabase.families())
        self.font_family_input.setCurrentText(self.settings["font_family"])
        layout.addRow("UI Font Family:", self.font_family_input)

        self.font_size_input = QSpinBox()
        self.font_size_input.setObjectName("fontSizeInput")
        self.font_size_input.setRange(8, 36)
        self.font_size_input.setValue(self.settings["font_size"])
        layout.addRow("UI Font Size:", self.font_size_input)

        self.content_font_family_input = QComboBox()
        self.content_font_family_input.setObjectName("contentFontFamilyInput")
        self.content_font_family_input.addItems(QFontDatabase.families())
        self.content_font_family_input.setCurrentText(
            self.settings.get("content_font_family", "Arial")
        )
        layout.addRow("Content Font Family:", self.content_font_family_input)

        self.content_font_size_input = QSpinBox()
        self.content_font_size_input.setObjectName("contentFontSizeInput")
        self.content_font_size_input.setRange(8, 48)
        self.content_font_size_input.setValue(
            int(self.settings.get("content_font_size", 18))
        )
        layout.addRow("Content Font Size:", self.content_font_size_input)
        self.pages.addWidget(page)

    def _build_audio_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        # --- Language filter ---
        lang_row = QHBoxLayout()
        lang_row.addWidget(QLabel("Language:"))
        self.tts_language_filter = QComboBox()
        self.tts_language_filter.setObjectName("ttsLanguageFilter")
        self.tts_language_filter.addItem("All languages", "")
        self.tts_language_filter.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        lang_row.addWidget(self.tts_language_filter, 1)
        layout.addLayout(lang_row)

        # --- Gender segmented control ---
        gender_row = QHBoxLayout()
        gender_row.addWidget(QLabel("Gender:"))
        self.tts_gender_all = QPushButton("All")
        self.tts_gender_all.setObjectName("ttsGenderAll")
        self.tts_gender_male = QPushButton("Male")
        self.tts_gender_male.setObjectName("ttsGenderMale")
        self.tts_gender_female = QPushButton("Female")
        self.tts_gender_female.setObjectName("ttsGenderFemale")
        self.tts_gender_group = QButtonGroup(self)
        for btn, value in (
            (self.tts_gender_all, ""),
            (self.tts_gender_male, "Male"),
            (self.tts_gender_female, "Female"),
        ):
            btn.setCheckable(True)
            btn.setStyleSheet(_GENDER_BUTTON_STYLE)
            btn.setProperty("genderFilter", value)
            self.tts_gender_group.addButton(btn)
            gender_row.addWidget(btn)
        self.tts_gender_group.setExclusive(True)
        self.tts_gender_all.setChecked(True)
        gender_row.addStretch()
        layout.addLayout(gender_row)

        # --- Search ---
        self.tts_voice_search = QLineEdit()
        self.tts_voice_search.setObjectName("ttsVoiceSearch")
        self.tts_voice_search.setPlaceholderText("Search by voice name…")
        self.tts_voice_search.setClearButtonEnabled(True)
        layout.addWidget(self.tts_voice_search)

        # --- Voice list ---
        self.tts_voice_list = QListWidget()
        self.tts_voice_list.setObjectName("ttsVoiceList")
        self.tts_voice_list.setMinimumHeight(160)
        self.tts_voice_list.setAlternatingRowColors(True)
        layout.addWidget(self.tts_voice_list, 1)
        self._set_voice_list_status("(loading voices…)")

        # Wire filters / selection / per-row preview (no redundant summary)
        self.tts_language_filter.currentIndexChanged.connect(
            self._on_language_filter_changed
        )
        self.tts_gender_group.buttonClicked.connect(self._refilter_voices)
        self.tts_voice_search.textChanged.connect(self._refilter_voices)
        self.tts_voice_list.currentItemChanged.connect(
            self._on_voice_selection_changed
        )

        self.pages.addWidget(page)

    def _build_ai_page(self):
        page, layout = self._page()
        self.ai_base_url_input = QLineEdit(
            self.settings.get("ai_base_url", "https://api.openai.com/v1")
        )
        self.ai_base_url_input.setObjectName("aiBaseUrlInput")
        layout.addRow("Base URL:", self.ai_base_url_input)

        self.ai_model_input = QLineEdit(
            self.settings.get("ai_model", "gpt-4o-mini")
        )
        self.ai_model_input.setObjectName("aiModelInput")
        layout.addRow("Model:", self.ai_model_input)

        self.ai_api_key_input = SecretLineEdit(
            self.settings.get("ai_api_key", "")
        )
        self.ai_api_key_input.setObjectName("aiApiKeyInput")
        self.ai_api_key_input.setPlaceholderText(
            "sk-... (stored locally, never committed)"
        )
        layout.addRow("API Key:", self.ai_api_key_input)

        self.ai_timeout_input = QSpinBox()
        self.ai_timeout_input.setObjectName("aiTimeoutInput")
        self.ai_timeout_input.setRange(5, 120)
        self.ai_timeout_input.setValue(
            int(self.settings.get("ai_timeout", 30))
        )
        self.ai_timeout_input.setSuffix(" s")
        layout.addRow("Timeout:", self.ai_timeout_input)

        self.explanation_language_input = QLineEdit(
            self.settings.get("explanation_language", "Chinese")
        )
        self.explanation_language_input.setObjectName(
            "explanationLanguageInput"
        )
        layout.addRow("Explanation Language:", self.explanation_language_input)

        self.ai_test_button = QPushButton("Test")
        self.ai_test_button.setObjectName("aiTestButton")
        self.ai_test_status_label = QLabel("")
        self.ai_test_status_label.setObjectName("aiTestStatusLabel")
        self.ai_test_status_label.setWordWrap(True)

        test_row = QWidget()
        test_layout = QHBoxLayout(test_row)
        test_layout.setContentsMargins(0, 0, 0, 0)
        test_layout.addWidget(self.ai_test_button)
        test_layout.addWidget(self.ai_test_status_label, 1)
        layout.addRow("", test_row)
        self.ai_test_button.clicked.connect(self._start_ai_test)
        self.pages.addWidget(page)

    def browse_database(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Default Database",
            DIR_DB,
            f"Barsky DB (*{DB_SUFFIX});;All Files (*)",
        )
        if path:
            self.default_database_input.setText(path)

    # ------------------------------------------------------------------
    # Voice list / picker
    # ------------------------------------------------------------------
    def _start_voice_worker(self):
        self.voice_worker = VoiceListWorker()
        self.voice_worker.voices_ready.connect(self._on_voices_ready)
        self.voice_worker.error.connect(self._on_voice_error)
        self.voice_worker.finished.connect(self.voice_worker.deleteLater)
        self.voice_worker.start()

    def _on_voices_ready(self, voices):
        self._all_voices = list(voices)
        self._populate_language_filter()
        self._refilter_voices()
        if not voices:
            self._set_voice_list_status("(voice list unavailable)")

    def _on_voice_error(self, _message):
        self._all_voices = []
        self._set_voice_list_status("(voice list unavailable)")

    def _populate_language_filter(self):
        preferred = self.current_language or ""
        self.tts_language_filter.blockSignals(True)
        self.tts_language_filter.clear()
        self.tts_language_filter.addItem("All languages", "")
        locales = sorted({locale for _, locale, _, _ in self._all_voices if locale})
        selected_index = 0
        for index, locale in enumerate(locales, start=1):
            self.tts_language_filter.addItem(locale, locale)
            if locale == preferred:
                selected_index = index
        # If preferred locale is no longer available, fall back to All.
        if preferred and selected_index == 0:
            self.current_language = ""
        self.tts_language_filter.setCurrentIndex(selected_index)
        self.tts_language_filter.blockSignals(False)

    def _on_language_filter_changed(self, *_args):
        self.current_language = self.tts_language_filter.currentData() or ""
        self._refilter_voices()

    def _selected_gender_filter(self):
        btn = self.tts_gender_group.checkedButton()
        if btn is None:
            return ""
        return btn.property("genderFilter") or ""

    def _filtered_voices(self):
        locale_filter = self.tts_language_filter.currentData() or ""
        gender_filter = self._selected_gender_filter()
        query = self.tts_voice_search.text().strip().lower()
        result = []
        for short_name, locale, gender, friendly in self._all_voices:
            if locale_filter and locale != locale_filter:
                continue
            if gender_filter and gender != gender_filter:
                continue
            if query:
                haystack = f"{short_name} {friendly}".lower()
                if query not in haystack:
                    continue
            result.append((short_name, locale, gender, friendly))
        return result

    def _set_voice_list_status(self, text):
        self.tts_voice_list.blockSignals(True)
        self.tts_voice_list.clear()
        item = QListWidgetItem(text)
        item.setFlags(Qt.ItemFlag.NoItemFlags)
        self.tts_voice_list.addItem(item)
        self.tts_voice_list.blockSignals(False)

    def _refilter_voices(self, *_args):
        if not self._all_voices:
            return
        filtered = self._filtered_voices()
        prefer = self.current_voice
        self.tts_voice_list.blockSignals(True)
        self.tts_voice_list.clear()
        select_row = -1
        for index, (short_name, locale, gender, _friendly) in enumerate(filtered):
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, short_name)
            item.setData(Qt.ItemDataRole.UserRole + 1, locale)
            item.setData(Qt.ItemDataRole.UserRole + 2, gender)
            row = _VoiceRowWidget(
                short_name, locale, gender, self._preview_voice
            )
            item.setSizeHint(row.sizeHint())
            self.tts_voice_list.addItem(item)
            self.tts_voice_list.setItemWidget(item, row)
            if short_name == prefer and select_row < 0:
                select_row = index
        self.tts_voice_list.blockSignals(False)

        if not filtered:
            self._set_voice_list_status("(no voices match filters)")
            return

        if select_row >= 0:
            self.tts_voice_list.setCurrentRow(select_row)
            self._on_voice_selection_changed(
                self.tts_voice_list.currentItem(), None
            )
        else:
            # Preferred voice is filtered out — keep it staged, clear list
            # selection so filters never silently reassign tts_voice.
            self.tts_voice_list.setCurrentRow(-1)

    def _on_voice_selection_changed(self, current, _previous):
        if current is None:
            return
        short_name = current.data(Qt.ItemDataRole.UserRole)
        if not short_name:
            return
        self.current_voice = short_name

    def _preview_voice(self, short_name):
        if not short_name or self.preview_tts_worker is not None:
            return
        # Stop any currently playing sample before starting a new one.
        self.preview_player.stop()
        self._set_preview_controls_enabled(False)
        worker = TTSWorker(_PREVIEW_SAMPLE, short_name)
        self.preview_tts_worker = worker
        worker.finished.connect(self._on_preview_finished)
        worker.error.connect(self._on_preview_error)
        worker.finished.connect(self._on_preview_worker_done)
        worker.error.connect(self._on_preview_worker_done)
        worker.finished.connect(worker.deleteLater)
        worker.error.connect(worker.deleteLater)
        worker.start()

    def _on_preview_finished(self, file_path):
        self.preview_player.setSource(QUrl.fromLocalFile(file_path))
        self.preview_player.play()

    def _on_preview_error(self, message):
        QMessageBox.warning(self, "TTS Preview", f"Audio Error: {message}")

    def _on_preview_worker_done(self, *_args):
        self.preview_tts_worker = None
        self._set_preview_controls_enabled(True)

    def _set_preview_controls_enabled(self, enabled):
        for i in range(self.tts_voice_list.count()):
            item = self.tts_voice_list.item(i)
            widget = self.tts_voice_list.itemWidget(item)
            if isinstance(widget, _VoiceRowWidget):
                widget.preview_button.setEnabled(enabled)

    # ------------------------------------------------------------------
    # AI test
    # ------------------------------------------------------------------
    def _staged_ai_config(self) -> AIProviderConfig:
        return AIProviderConfig(
            base_url=self.ai_base_url_input.text().strip(),
            model=self.ai_model_input.text().strip(),
            api_key=self.ai_api_key_input.text().strip(),
            timeout_seconds=self.ai_timeout_input.value(),
        )

    def _start_ai_test(self):
        if self.ai_test_worker is not None:
            return
        self.ai_test_button.setEnabled(False)
        self.ai_test_status_label.setStyleSheet("")
        self.ai_test_status_label.setText("Testing…")
        config = self._staged_ai_config()
        worker = create_ai_test_worker(config)
        self.ai_test_worker = worker
        worker.result.connect(self._on_ai_test_result)
        worker.finished.connect(self._on_ai_test_finished)
        worker.finished.connect(worker.deleteLater)
        worker.start()

    def _on_ai_test_result(self, ok, message, latency_ms):
        model = self.ai_model_input.text().strip() or "model"
        if ok:
            ms = int(round(latency_ms)) if latency_ms >= 0 else "?"
            text = f"OK — {ms} ms ({model})"
            self.ai_test_status_label.setStyleSheet("color: #1a7f37;")
        else:
            text = f"Failed — {message}"
            self.ai_test_status_label.setStyleSheet("color: #cf222e;")
        self.ai_test_status_label.setText(text)

    def _on_ai_test_finished(self):
        self.ai_test_worker = None
        self.ai_test_button.setEnabled(True)

    def _staged_settings(self):
        staged = dict(self.settings)
        staged["width"] = self.window_width_input.value()
        staged["height"] = self.window_height_input.value()
        staged["font_family"] = self.font_family_input.currentText()
        staged["font_size"] = self.font_size_input.value()
        staged["content_font_family"] = (
            self.content_font_family_input.currentText()
        )
        staged["content_font_size"] = self.content_font_size_input.value()
        staged["default_database"] = (
            self.default_database_input.text().strip()
        )
        staged["tts_voice"] = self.current_voice
        staged["tts_language"] = self.current_language or ""
        staged["ai_base_url"] = self.ai_base_url_input.text().strip()
        staged["ai_model"] = self.ai_model_input.text().strip()
        staged["ai_api_key"] = self.ai_api_key_input.text().strip()
        staged["ai_timeout"] = self.ai_timeout_input.value()
        staged["explanation_language"] = (
            self.explanation_language_input.text().strip()
        )
        return staged

    def save_and_apply(self):
        staged = self._staged_settings()
        try:
            save_settings(staged)
        except OSError as exc:
            QMessageBox.critical(self, "Settings Not Saved", str(exc))
            return
        self.settings.update(staged)
        self.accept()
