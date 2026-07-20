"""Categorized application settings dialog."""

from PyQt6.QtGui import QFontDatabase
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from .config import DIR_DB, save_settings
from .db import DB_SUFFIX
from .secret_line_edit import SecretLineEdit
from .tts import VoiceListWorker


class SettingsDialog(QDialog):
    """Edit settings in categories while keeping changes staged until save."""

    CATEGORIES = (
        "General",
        "Appearance",
        "Audio & Speech",
        "AI Provider",
        "Languages",
    )

    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.current_voice = settings.get(
            "tts_voice", "en-US-AvaMultilingualNeural"
        )
        self.setWindowTitle("App Settings")
        self.setMinimumSize(620, 390)

        outer_layout = QVBoxLayout(self)
        content_layout = QHBoxLayout()
        outer_layout.addLayout(content_layout, 1)

        self.category_list = QListWidget()
        self.category_list.setObjectName("settingsCategoryList")
        self.category_list.addItems(self.CATEGORIES)
        self.category_list.setSizePolicy(
            QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Expanding
        )
        self.category_list.setMaximumWidth(
            self.category_list.sizeHintForColumn(0) + 32
        )
        content_layout.addWidget(self.category_list)

        self.pages = QStackedWidget()
        self.pages.setObjectName("settingsPages")
        content_layout.addWidget(self.pages, 1)

        self._build_general_page()
        self._build_appearance_page()
        self._build_audio_page()
        self._build_ai_page()
        self._build_languages_page()

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
        self.window_width_input = QSpinBox()
        self.window_width_input.setObjectName("windowWidthInput")
        self.window_width_input.setRange(400, 3000)
        self.window_width_input.setValue(self.settings["width"])
        layout.addRow("Window Width:", self.window_width_input)

        self.window_height_input = QSpinBox()
        self.window_height_input.setObjectName("windowHeightInput")
        self.window_height_input.setRange(400, 3000)
        self.window_height_input.setValue(self.settings["height"])
        layout.addRow("Window Height:", self.window_height_input)

        self.font_family_input = QComboBox()
        self.font_family_input.setObjectName("fontFamilyInput")
        self.font_family_input.addItems(QFontDatabase.families())
        self.font_family_input.setCurrentText(self.settings["font_family"])
        layout.addRow("Font Family:", self.font_family_input)

        self.font_size_input = QSpinBox()
        self.font_size_input.setObjectName("fontSizeInput")
        self.font_size_input.setRange(8, 36)
        self.font_size_input.setValue(self.settings["font_size"])
        layout.addRow("Font Size:", self.font_size_input)
        self.pages.addWidget(page)

    def _build_audio_page(self):
        page, layout = self._page()
        self.tts_voice_input = QComboBox()
        self.tts_voice_input.setObjectName("ttsVoiceInput")
        self.tts_voice_input.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.tts_voice_input.addItem("(loading voices…)", self.current_voice)
        layout.addRow("TTS Voice (Edge-TTS):", self.tts_voice_input)
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
        self.pages.addWidget(page)

    def _build_languages_page(self):
        page, layout = self._page()
        self.learned_language_input = QLineEdit(
            self.settings.get("learned_language", "English")
        )
        self.learned_language_input.setObjectName("learnedLanguageInput")
        layout.addRow("Learned Language:", self.learned_language_input)

        self.explanation_language_input = QLineEdit(
            self.settings.get("explanation_language", "Chinese")
        )
        self.explanation_language_input.setObjectName(
            "explanationLanguageInput"
        )
        layout.addRow("Explanation Language:", self.explanation_language_input)
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

    def _start_voice_worker(self):
        self.voice_worker = VoiceListWorker()
        self.voice_worker.voices_ready.connect(self._on_voices_ready)
        self.voice_worker.error.connect(self._on_voice_error)
        self.voice_worker.finished.connect(self.voice_worker.deleteLater)
        self.voice_worker.start()

    def _on_voices_ready(self, voices):
        self.tts_voice_input.clear()
        selected_index = 0
        for index, (short_name, locale, gender, _friendly) in enumerate(voices):
            label = f"{short_name}  ·  {locale}  ·  {gender}"
            self.tts_voice_input.addItem(label, short_name)
            if short_name == self.current_voice:
                selected_index = index
        if voices:
            self.tts_voice_input.setCurrentIndex(selected_index)
        else:
            self.tts_voice_input.addItem(
                "(voice list unavailable)", self.current_voice
            )

    @staticmethod
    def _on_voice_error(_message):
        pass

    def _staged_settings(self):
        staged = dict(self.settings)
        staged["width"] = self.window_width_input.value()
        staged["height"] = self.window_height_input.value()
        staged["font_family"] = self.font_family_input.currentText()
        staged["font_size"] = self.font_size_input.value()
        staged["default_database"] = (
            self.default_database_input.text().strip()
        )
        staged["tts_voice"] = (
            self.tts_voice_input.currentData() or self.current_voice
        )
        staged["ai_base_url"] = self.ai_base_url_input.text().strip()
        staged["ai_model"] = self.ai_model_input.text().strip()
        staged["ai_api_key"] = self.ai_api_key_input.text().strip()
        staged["ai_timeout"] = self.ai_timeout_input.value()
        staged["learned_language"] = (
            self.learned_language_input.text().strip()
        )
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
