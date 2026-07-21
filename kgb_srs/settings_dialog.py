"""Categorized application settings dialog."""

import os

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
    QInputDialog,
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

from .ai_provider import (
    AIProviderConfig,
    DEFAULT_AI_PROVIDER_NAME,
    create_ai_models_worker,
    create_ai_test_worker,
    delete_ai_provider,
    ensure_ai_provider_profiles,
    get_ai_provider_entry,
    list_ai_provider_names,
    rename_ai_provider,
    set_active_ai_provider,
    upsert_ai_provider,
)
from .config import (
    DIR_DB,
    ensure_database_root_structure,
    get_database_root,
    is_path_under_root,
    normalize_default_database,
    relative_db_path,
    save_settings,
)
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
        "AI Providers",
    )

    def __init__(self, settings, parent=None, current_size=None):
        super().__init__(parent)
        if parent is not None:
            self.setFont(parent.font())
        self.settings = settings
        # Staged AI provider bag (mutated by switch/add/rename/delete before Save).
        self._ai_stage = {
            "ai_active_provider": settings.get(
                "ai_active_provider", DEFAULT_AI_PROVIDER_NAME
            ),
            "ai_providers": {
                name: dict(entry)
                for name, entry in (
                    settings.get("ai_providers") or {}
                ).items()
                if isinstance(entry, dict)
            },
        }
        ensure_ai_provider_profiles(self._ai_stage)
        self._ai_loading_profile = False
        self.current_size = current_size
        self.current_voice = settings.get(
            "tts_voice", "en-US-AvaMultilingualNeural"
        )
        self.current_language = settings.get("tts_language", "") or ""
        self._all_voices = []  # (ShortName, Locale, Gender, FriendlyName)
        self.ai_test_worker = None
        self.ai_models_worker = None
        self._ai_test_token = None
        self._ai_models_refresh_token = None
        self.preview_tts_worker = None
        self._closing_workers = []
        self._deferred_close_action = None
        self._allow_deferred_close = False
        self._tts_temp_path = None
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

        # --- Database root directory ---
        root_value = (self.settings.get("database_root") or "").strip()
        if not root_value:
            root_value = DIR_DB
        self.database_root_input = QLineEdit(root_value)
        self.database_root_input.setObjectName("databaseRootInput")
        self.database_root_input.setPlaceholderText(
            "Directory that holds all databases"
        )
        self.database_root_browse_button = QPushButton("Browse…")
        self.database_root_browse_button.setObjectName(
            "databaseRootBrowseButton"
        )

        root_row = QWidget()
        root_row_layout = QHBoxLayout(root_row)
        root_row_layout.setContentsMargins(0, 0, 0, 0)
        root_row_layout.addWidget(self.database_root_input, 1)
        root_row_layout.addWidget(self.database_root_browse_button)
        layout.addRow("Database Directory:", root_row)
        self.database_root_browse_button.clicked.connect(
            self.browse_database_root
        )

        # --- Default database file (stored relative to database root) ---
        default_display = self._display_default_database(
            self.settings.get("default_database", ""),
            get_database_root(self.settings),
        )
        self.default_database_input = QLineEdit(default_display)
        self.default_database_input.setObjectName("defaultDatabaseInput")
        self.default_database_input.setPlaceholderText(
            "Relative path under Database Directory"
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
        self.database_root_input.textChanged.connect(
            self._on_database_root_text_changed
        )
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

        # --- Provider profile switcher ---
        self.ai_provider_combo = QComboBox()
        self.ai_provider_combo.setObjectName("aiProviderCombo")
        self.ai_provider_combo.setEditable(False)
        self.ai_provider_combo.setToolTip(
            "Switch between saved OpenAI-compatible provider profiles."
        )
        layout.addRow("Provider:", self.ai_provider_combo)

        profile_btns = QWidget()
        profile_btns_layout = QHBoxLayout(profile_btns)
        profile_btns_layout.setContentsMargins(0, 0, 0, 0)
        profile_btns_layout.setSpacing(6)
        self.ai_provider_add_btn = QPushButton("Add")
        self.ai_provider_add_btn.setObjectName("aiProviderAddButton")
        self.ai_provider_add_btn.setToolTip(
            "Save a new provider profile (copy of current fields)."
        )
        self.ai_provider_rename_btn = QPushButton("Rename")
        self.ai_provider_rename_btn.setObjectName("aiProviderRenameButton")
        self.ai_provider_delete_btn = QPushButton("Delete")
        self.ai_provider_delete_btn.setObjectName("aiProviderDeleteButton")
        self.ai_provider_delete_btn.setToolTip(
            "Delete the selected profile (at least one must remain)."
        )
        profile_btns_layout.addWidget(self.ai_provider_add_btn)
        profile_btns_layout.addWidget(self.ai_provider_rename_btn)
        profile_btns_layout.addWidget(self.ai_provider_delete_btn)
        profile_btns_layout.addStretch(1)
        layout.addRow("", profile_btns)

        self.ai_base_url_input = QLineEdit()
        self.ai_base_url_input.setObjectName("aiBaseUrlInput")
        layout.addRow("Base URL:", self.ai_base_url_input)

        model_row = QWidget()
        model_layout = QHBoxLayout(model_row)
        model_layout.setContentsMargins(0, 0, 0, 0)
        self.ai_model_input = QComboBox()
        self.ai_model_input.setObjectName("aiModelInput")
        self.ai_model_input.setEditable(True)
        self.ai_model_input.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.ai_model_input.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        self.ai_model_input.lineEdit().setPlaceholderText(
            "model id (or Refresh to list)"
        )
        self.ai_models_refresh_btn = QPushButton("Refresh")
        self.ai_models_refresh_btn.setObjectName("aiModelsRefreshButton")
        self.ai_models_refresh_btn.setToolTip(
            "Fetch available models from Base URL + API Key (/v1/models)."
        )
        model_layout.addWidget(self.ai_model_input, 1)
        model_layout.addWidget(self.ai_models_refresh_btn)
        layout.addRow("Model:", model_row)

        self.ai_api_key_input = SecretLineEdit("")
        self.ai_api_key_input.setObjectName("aiApiKeyInput")
        self.ai_api_key_input.setPlaceholderText(
            "sk-... (stored locally, never committed)"
        )
        layout.addRow("API Key:", self.ai_api_key_input)

        self.ai_timeout_input = QSpinBox()
        self.ai_timeout_input.setObjectName("aiTimeoutInput")
        self.ai_timeout_input.setRange(5, 120)
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
        self.ai_models_refresh_btn.clicked.connect(self._start_ai_models_refresh)

        self.ai_provider_combo.currentTextChanged.connect(
            self._on_ai_provider_selected
        )
        self.ai_provider_add_btn.clicked.connect(self._add_ai_provider)
        self.ai_provider_rename_btn.clicked.connect(self._rename_ai_provider)
        self.ai_provider_delete_btn.clicked.connect(self._delete_ai_provider)

        self._reload_ai_provider_combo()
        self._load_active_ai_profile_into_fields()
        self.pages.addWidget(page)

    def _current_ai_provider_name(self) -> str:
        name = self.ai_provider_combo.currentText().strip()
        if name:
            return name
        return str(
            self._ai_stage.get("ai_active_provider") or DEFAULT_AI_PROVIDER_NAME
        )

    def _capture_ai_fields_to_stage(
        self, name: str | None = None, *, make_active: bool = True
    ) -> None:
        """Write current form fields into the staged profile bag."""
        label = (name or self._current_ai_provider_name()).strip()
        if not label:
            label = DEFAULT_AI_PROVIDER_NAME
        upsert_ai_provider(
            self._ai_stage,
            label,
            base_url=self.ai_base_url_input.text().strip(),
            model=self._ai_model_text(),
            api_key=self.ai_api_key_input.text().strip(),
            timeout=self.ai_timeout_input.value(),
            make_active=make_active,
        )

    def _load_active_ai_profile_into_fields(self) -> None:
        entry = get_ai_provider_entry(self._ai_stage)
        self._ai_loading_profile = True
        try:
            self.ai_base_url_input.setText(entry.get("base_url", ""))
            model = str(entry.get("model", "") or "").strip()
            self._populate_ai_models([model] if model else [], keep=model)
            self.ai_api_key_input.setText(entry.get("api_key", ""))
            self.ai_timeout_input.setValue(int(entry.get("timeout", 30)))
        finally:
            self._ai_loading_profile = False
        self.ai_test_status_label.setText("")
        self.ai_test_status_label.setStyleSheet("")
        self._update_ai_profile_buttons()

    def _reload_ai_provider_combo(self) -> None:
        names = list_ai_provider_names(self._ai_stage)
        active = self._ai_stage.get("ai_active_provider", DEFAULT_AI_PROVIDER_NAME)
        self._ai_loading_profile = True
        try:
            self.ai_provider_combo.blockSignals(True)
            self.ai_provider_combo.clear()
            self.ai_provider_combo.addItems(names)
            idx = self.ai_provider_combo.findText(active)
            if idx < 0:
                idx = 0
            self.ai_provider_combo.setCurrentIndex(idx)
        finally:
            self.ai_provider_combo.blockSignals(False)
            self._ai_loading_profile = False
        self._update_ai_profile_buttons()

    def _update_ai_profile_buttons(self) -> None:
        count = len(self._ai_stage.get("ai_providers") or {})
        self.ai_provider_delete_btn.setEnabled(count > 1)

    def _on_ai_provider_selected(self, name: str) -> None:
        if self._ai_loading_profile:
            return
        name = (name or "").strip()
        if not name:
            return
        # Persist edits on the previous active profile before switching.
        previous = self._ai_stage.get("ai_active_provider")
        if previous and previous != name:
            self._capture_ai_fields_to_stage(previous, make_active=False)
        if not set_active_ai_provider(self._ai_stage, name):
            return
        self._load_active_ai_profile_into_fields()

    def _prompt_provider_name(self, title: str, initial: str = "") -> str | None:
        text, ok = QInputDialog.getText(
            self,
            title,
            "Provider name:",
            QLineEdit.EchoMode.Normal,
            initial,
        )
        if not ok:
            return None
        name = (text or "").strip()
        if not name:
            QMessageBox.warning(self, title, "Provider name cannot be empty.")
            return None
        return name

    def _add_ai_provider(self) -> None:
        # Keep current edits on the active profile first.
        self._capture_ai_fields_to_stage()
        name = self._prompt_provider_name("Add AI Provider")
        if not name:
            return
        if name in (self._ai_stage.get("ai_providers") or {}):
            QMessageBox.warning(
                self,
                "Add AI Provider",
                f"A provider named “{name}” already exists.",
            )
            return
        # New profile starts as a copy of the current form fields.
        upsert_ai_provider(
            self._ai_stage,
            name,
            base_url=self.ai_base_url_input.text().strip(),
            model=self._ai_model_text(),
            api_key=self.ai_api_key_input.text().strip(),
            timeout=self.ai_timeout_input.value(),
            make_active=True,
        )
        self._reload_ai_provider_combo()
        self._load_active_ai_profile_into_fields()

    def _rename_ai_provider(self) -> None:
        old = self._current_ai_provider_name()
        self._capture_ai_fields_to_stage(old)
        new = self._prompt_provider_name("Rename AI Provider", initial=old)
        if not new or new == old:
            return
        result = rename_ai_provider(self._ai_stage, old, new)
        if result is None:
            QMessageBox.warning(
                self,
                "Rename AI Provider",
                f"Could not rename to “{new}” "
                f"(name may already exist).",
            )
            return
        self._reload_ai_provider_combo()
        self._load_active_ai_profile_into_fields()

    def _delete_ai_provider(self) -> None:
        name = self._current_ai_provider_name()
        providers = self._ai_stage.get("ai_providers") or {}
        if len(providers) <= 1:
            QMessageBox.information(
                self,
                "Delete AI Provider",
                "At least one provider profile is required.",
            )
            return
        reply = QMessageBox.question(
            self,
            "Delete AI Provider",
            f"Delete provider “{name}”?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        if not delete_ai_provider(self._ai_stage, name):
            QMessageBox.warning(
                self,
                "Delete AI Provider",
                "Could not delete this provider.",
            )
            return
        self._reload_ai_provider_combo()
        self._load_active_ai_profile_into_fields()

    def browse_database_root(self):
        start = self.database_root_input.text().strip() or DIR_DB
        path = QFileDialog.getExistingDirectory(
            self,
            "Select Database Directory",
            start,
        )
        if path:
            self.database_root_input.setText(path)

    def _staged_root_path(self) -> str:
        """Absolute root implied by the staged Database Directory field."""
        root_text = self.database_root_input.text().strip()
        if not root_text:
            return DIR_DB
        return os.path.abspath(os.path.expanduser(root_text))

    @staticmethod
    def _display_default_database(value: str, root: str) -> str:
        """Show stored relative path; convert absolute-under-root to relative."""
        value = (value or "").strip()
        if not value:
            return ""
        if os.path.isabs(value) or value.startswith("~"):
            rel = relative_db_path(value, root)
            return rel or ""
        return os.path.normpath(value)

    def _on_database_root_text_changed(self, *_args):
        """Clear default DB if it would fall outside the new root."""
        current = self.default_database_input.text().strip()
        if not current:
            return
        root = self._staged_root_path()
        if not normalize_default_database(current, root):
            self.default_database_input.clear()

    def _pick_file_under_root(self, root: str, start: str) -> str:
        """Open a non-native file dialog that cannot navigate outside *root*.

        The system (native) file manager cannot be constrained; Qt's own dialog
        can. Sidebar is limited to the root, and directoryEntered snaps back
        when the user tries to leave it. Final selection is still validated by
        the caller.
        """
        root = os.path.abspath(os.path.expanduser(root))
        start = os.path.abspath(os.path.expanduser(start))
        if not is_path_under_root(start, root) or not os.path.isdir(start):
            start = root

        dialog = QFileDialog(self, "Select Default Database", start)
        dialog.setOption(QFileDialog.Option.DontUseNativeDialog, True)
        dialog.setFileMode(QFileDialog.FileMode.ExistingFile)
        dialog.setAcceptMode(QFileDialog.AcceptMode.AcceptOpen)
        dialog.setNameFilters(
            [f"Barsky DB (*{DB_SUFFIX})", "All Files (*)"]
        )
        # Hide places that let the user jump outside the root.
        dialog.setSidebarUrls([QUrl.fromLocalFile(root)])
        dialog.setDirectory(start)

        def _clamp_to_root(path: str) -> None:
            if not is_path_under_root(path, root):
                dialog.setDirectory(root)

        dialog.directoryEntered.connect(_clamp_to_root)

        if dialog.exec() != QDialog.DialogCode.Accepted:
            return ""
        selected = dialog.selectedFiles()
        return selected[0] if selected else ""

    def browse_database(self):
        root = self._staged_root_path()
        start = root if os.path.isdir(root) else DIR_DB
        path = self._pick_file_under_root(root, start)
        if not path:
            return
        if not is_path_under_root(path, root):
            QMessageBox.warning(
                self,
                "Default Database",
                "Default Database must be inside the configured "
                "Database Directory:\n"
                f"{root}",
            )
            return
        rel = relative_db_path(path, root)
        if rel is None:
            return
        self.default_database_input.setText(rel)

    # ------------------------------------------------------------------
    # Voice list / picker
    # ------------------------------------------------------------------
    def _start_voice_worker(self):
        if self._deferred_close_action is not None:
            return
        self.voice_worker = VoiceListWorker()
        self.voice_worker.voices_ready.connect(self._on_voices_ready)
        self.voice_worker.error.connect(self._on_voice_error)
        self.voice_worker.finished.connect(self._on_close_worker_finished)
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

    def _cleanup_tts_temp(self):
        """Best-effort unlink of the last preview TTS temp MP3."""
        from .tts import unlink_tts_temp

        self._tts_temp_path = unlink_tts_temp(self._tts_temp_path)

    def _preview_voice(self, short_name):
        if (
            not short_name
            or self.preview_tts_worker is not None
            or self._deferred_close_action is not None
        ):
            return
        # Stop any currently playing sample before starting a new one.
        self.preview_player.stop()
        self._cleanup_tts_temp()
        self._set_preview_controls_enabled(False)
        worker = TTSWorker(_PREVIEW_SAMPLE, short_name)
        self.preview_tts_worker = worker
        worker.audio_ready.connect(self._on_preview_finished)
        worker.error.connect(self._on_preview_error)
        # Real QThread.finished (not the payload signal) clears the ref.
        worker.finished.connect(self._on_preview_worker_done)
        worker.finished.connect(self._on_close_worker_finished)
        worker.finished.connect(worker.deleteLater)
        worker.start()

    def _on_preview_finished(self, file_path):
        self._tts_temp_path = file_path
        self.preview_player.setSource(QUrl.fromLocalFile(file_path))
        self.preview_player.play()

    def closeEvent(self, event):
        if not self._allow_deferred_close and self._defer_close_for_running_workers(
            "close"
        ):
            event.ignore()
            return
        # QDialog.closeEvent() calls reject(), so retain this guard through the
        # superclass call to avoid re-entering the worker-close deferral.
        self._allow_deferred_close = True
        try:
            self._cleanup_tts_temp()
            super().closeEvent(event)
        finally:
            self._allow_deferred_close = False

    def accept(self):
        """Accept immediately unless a worker thread must finish first."""
        if self._defer_close_for_running_workers("accept"):
            return
        self._cleanup_tts_temp()
        super().accept()

    def reject(self):
        """Reject immediately unless a worker thread must finish first."""
        if self._allow_deferred_close:
            super().reject()
            return
        if self._defer_close_for_running_workers("reject"):
            return
        self._cleanup_tts_temp()
        super().reject()

    def _running_workers(self):
        """Return this dialog's QThread workers that are currently running."""
        workers = []
        for name in (
            "voice_worker",
            "preview_tts_worker",
            "ai_test_worker",
            "ai_models_worker",
        ):
            worker = getattr(self, name, None)
            is_running = getattr(worker, "isRunning", None)
            if callable(is_running) and is_running():
                workers.append(worker)
        return workers

    def _defer_close_for_running_workers(self, action):
        """Remember a close action until every active QThread has finished."""
        workers = self._running_workers()
        if not workers:
            return False
        if self._deferred_close_action is None:
            self._deferred_close_action = action
        for worker in workers:
            if worker not in self._closing_workers:
                self._closing_workers.append(worker)
        return True

    def _on_close_worker_finished(self):
        """Complete a deferred close only after each QThread.finished signal."""
        worker = self.sender()
        if worker in self._closing_workers:
            self._closing_workers.remove(worker)
        if self._deferred_close_action is not None and not self._closing_workers:
            action = self._deferred_close_action
            self._deferred_close_action = None
            if action == "close":
                self._allow_deferred_close = True
                self.close()
            else:
                self._cleanup_tts_temp()
                if action == "accept":
                    super().accept()
                else:
                    super().reject()

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

    def _ai_model_text(self) -> str:
        """Current model id from the editable combo (typed or selected)."""
        return self.ai_model_input.currentText().strip()

    def _set_ai_model_text(self, model: str) -> None:
        """Set the model combo text without wiping discovered items."""
        value = (model or "").strip()
        idx = self.ai_model_input.findText(value)
        if idx >= 0:
            self.ai_model_input.setCurrentIndex(idx)
        else:
            self.ai_model_input.setEditText(value)

    def _populate_ai_models(self, models: list[str], *, keep: str | None = None) -> None:
        """Fill the model combo with *models*, preserving *keep* selection."""
        selected = (keep if keep is not None else self._ai_model_text()).strip()
        self.ai_model_input.blockSignals(True)
        try:
            self.ai_model_input.clear()
            if models:
                self.ai_model_input.addItems(list(models))
            if selected:
                idx = self.ai_model_input.findText(selected)
                if idx >= 0:
                    self.ai_model_input.setCurrentIndex(idx)
                else:
                    # Keep user's current/custom model even if not in the list.
                    self.ai_model_input.insertItem(0, selected)
                    self.ai_model_input.setCurrentIndex(0)
        finally:
            self.ai_model_input.blockSignals(False)

    def _staged_ai_config(self) -> AIProviderConfig:
        return AIProviderConfig(
            base_url=self.ai_base_url_input.text().strip(),
            model=self._ai_model_text(),
            api_key=self.ai_api_key_input.text().strip(),
            timeout_seconds=self.ai_timeout_input.value(),
        )

    def _ai_models_token(
        self, provider_name: str, config: AIProviderConfig
    ) -> tuple[str, str, str, str, int]:
        """Immutable identity for a pending model-list request."""
        return (
            provider_name,
            config.base_url,
            config.model,
            config.api_key,
            config.timeout_seconds,
        )

    def _start_ai_test(self):
        if self.ai_test_worker is not None or self._deferred_close_action is not None:
            return
        self.ai_test_button.setEnabled(False)
        self.ai_test_status_label.setStyleSheet("")
        self.ai_test_status_label.setText("Testing…")
        config = self._staged_ai_config()
        self._ai_test_token = self._ai_models_token(
            self._current_ai_provider_name(), config
        )
        worker = create_ai_test_worker(config)
        self.ai_test_worker = worker
        worker.result.connect(self._on_ai_test_result)
        worker.finished.connect(self._on_ai_test_finished)
        worker.finished.connect(self._on_close_worker_finished)
        worker.finished.connect(worker.deleteLater)
        worker.start()

    def _on_ai_test_result(self, ok, message, latency_ms):
        current_token = self._ai_models_token(
            self._current_ai_provider_name(), self._staged_ai_config()
        )
        if self._ai_test_token != current_token:
            return
        model = self._ai_model_text() or "model"
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
        self._ai_test_token = None
        self.ai_test_button.setEnabled(True)

    def _start_ai_models_refresh(self):
        """Fetch /models for the staged Base URL + API Key (nonblocking)."""
        if self.ai_models_worker is not None or self._deferred_close_action is not None:
            return
        self.ai_models_refresh_btn.setEnabled(False)
        self.ai_test_status_label.setStyleSheet("")
        self.ai_test_status_label.setText("Loading models…")
        config = self._staged_ai_config()
        provider_name = self._current_ai_provider_name()
        self._ai_models_refresh_token = self._ai_models_token(
            provider_name, config
        )
        worker = create_ai_models_worker(config)
        self.ai_models_worker = worker
        worker.result.connect(self._on_ai_models_result)
        worker.finished.connect(self._on_ai_models_finished)
        worker.finished.connect(self._on_close_worker_finished)
        worker.finished.connect(worker.deleteLater)
        worker.start()

    def _on_ai_models_result(self, ok, message, models):
        current_token = self._ai_models_token(
            self._current_ai_provider_name(), self._staged_ai_config()
        )
        if self._ai_models_refresh_token != current_token:
            return
        if ok:
            self._populate_ai_models(list(models or []), keep=self._ai_model_text())
            count = len(models or [])
            self.ai_test_status_label.setStyleSheet("color: #1a7f37;")
            self.ai_test_status_label.setText(f"OK — {count} model(s) available")
        else:
            self.ai_test_status_label.setStyleSheet("color: #cf222e;")
            self.ai_test_status_label.setText(f"Models — {message}")

    def _on_ai_models_finished(self):
        self.ai_models_worker = None
        self._ai_models_refresh_token = None
        self.ai_models_refresh_btn.setEnabled(True)

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
        root_text = self.database_root_input.text().strip()
        # Store empty when the user keeps the project default so upgrades stay
        # portable; get_database_root() still resolves empty → DIR_DB.
        if not root_text or os.path.abspath(os.path.expanduser(root_text)) == (
            os.path.abspath(DIR_DB)
        ):
            staged["database_root"] = ""
        else:
            staged["database_root"] = os.path.abspath(
                os.path.expanduser(root_text)
            )
        root = get_database_root(staged)
        staged["default_database"] = normalize_default_database(
            self.default_database_input.text().strip(),
            root,
        )
        staged["tts_voice"] = self.current_voice
        staged["tts_language"] = self.current_language or ""
        # Capture current form fields into the active staged profile first.
        self._capture_ai_fields_to_stage()
        ensure_ai_provider_profiles(self._ai_stage)
        staged["ai_providers"] = {
            name: dict(entry)
            for name, entry in self._ai_stage["ai_providers"].items()
        }
        staged["ai_active_provider"] = self._ai_stage["ai_active_provider"]
        # Profiles only — drop any legacy flat mirrors from the live dict copy.
        from .ai_provider import strip_legacy_ai_flat_keys

        strip_legacy_ai_flat_keys(staged)
        staged["explanation_language"] = (
            self.explanation_language_input.text().strip()
        )
        return staged

    def save_and_apply(self):
        staged = self._staged_settings()
        root = get_database_root(staged)
        try:
            ensure_database_root_structure(root)
        except OSError as exc:
            QMessageBox.critical(
                self,
                "Database Directory",
                f"Could not create database directory structure under:\n"
                f"{root}\n\n{exc}",
            )
            return
        try:
            save_settings(staged)
        except OSError as exc:
            QMessageBox.critical(self, "Settings Not Saved", str(exc))
            return
        self.settings.update(staged)
        self.accept()
