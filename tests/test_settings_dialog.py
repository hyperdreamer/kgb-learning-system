"""Focused tests for the categorized application settings dialog."""

import os
import threading

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PyQt6")

from PyQt6.QtCore import QObject, QThread, Qt, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QApplication,
    QLabel,
    QListWidget,
    QMainWindow,
    QPushButton,
    QStackedWidget,
    QWidget,
)


_APP = None


def _app():
    global _APP
    _APP = QApplication.instance() or QApplication([])
    return _APP


class FakeVoiceWorker(QObject):
    voices_ready = pyqtSignal(list)
    error = pyqtSignal(str)
    finished = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.started = False
        self.deleted = False

    def start(self):
        self.started = True

    def deleteLater(self):
        self.deleted = True


class DeletedVoiceWorker(FakeVoiceWorker):
    """Fake wrapper that raises once deleteLater has invalidated it."""

    def isRunning(self):
        if self.deleted:
            raise RuntimeError(
                "wrapped C/C++ object of type VoiceListWorker has been deleted"
            )
        return False


class FakeTTSWorker(QObject):
    audio_ready = pyqtSignal(str)
    finished = pyqtSignal()
    error = pyqtSignal(str)

    instances = []

    def __init__(self, text, voice):
        super().__init__()
        self.text = text
        self.voice = voice
        self.started = False
        self.deleted = False
        FakeTTSWorker.instances.append(self)

    def start(self):
        self.started = True

    def deleteLater(self):
        self.deleted = True


class FakeAITestWorker(QObject):
    result = pyqtSignal(bool, str, float)
    finished = pyqtSignal()

    instances = []

    def __init__(self, config):
        super().__init__()
        self.config = config
        self.started = False
        self.deleted = False
        FakeAITestWorker.instances.append(self)

    def start(self):
        self.started = True

    def deleteLater(self):
        self.deleted = True


class FakeAIModelsWorker(QObject):
    result = pyqtSignal(bool, str, list)
    finished = pyqtSignal()

    instances = []

    def __init__(self, config):
        super().__init__()
        self.config = config
        self.started = False
        self.deleted = False
        FakeAIModelsWorker.instances.append(self)

    def start(self):
        self.started = True

    def deleteLater(self):
        self.deleted = True


class BlockingVoiceWorker(QThread):
    """Controllable real QThread used to exercise dialog-close lifetime."""

    voices_ready = pyqtSignal(list)
    error = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.started_event = threading.Event()
        self.release_event = threading.Event()

    def run(self):
        self.started_event.set()
        self.release_event.wait()


class BlockingTTSWorker(QThread):
    """Controllable preview worker that remains alive until released."""

    audio_ready = pyqtSignal(str)
    error = pyqtSignal(str)

    def __init__(self, text, voice):
        super().__init__()
        self.text = text
        self.voice = voice
        self.started_event = threading.Event()
        self.release_event = threading.Event()

    def run(self):
        self.started_event.set()
        self.release_event.wait()


SAMPLE_VOICES = [
    ("en-US-AvaMultilingualNeural", "en-US", "Female", "Ava"),
    ("en-US-AndrewNeural", "en-US", "Male", "Andrew"),
    ("en-GB-SoniaNeural", "en-GB", "Female", "Sonia"),
    ("zh-CN-XiaoxiaoNeural", "zh-CN", "Female", "Xiaoxiao"),
    ("zh-CN-YunxiNeural", "zh-CN", "Male", "Yunxi"),
]


@pytest.fixture
def settings():
    return {
        "width": 900,
        "height": 700,
        "font_family": "Arial",
        "font_size": 14,
        "content_font_family": "Arial",
        "content_font_size": 18,
        "database_root": "",
        "default_database": "",
        "tts_voice": "en-US-AvaMultilingualNeural",
        "tts_language": "",
        "ai_active_provider": "Default",
        "ai_providers": {
            "Default": {
                "base_url": "https://api.openai.com/v1",
                "model": "gpt-4o-mini",
                "api_key": "secret",
                "timeout": 30,
            }
        },
        "explanation_language": "Chinese",
    }


def _dialog(
    monkeypatch,
    settings,
    save=None,
    current_size=None,
    ai_test_factory=None,
    ai_models_factory=None,
):
    _app()
    import kgb_srs.settings_dialog as module

    worker = FakeVoiceWorker()
    monkeypatch.setattr(module, "VoiceListWorker", lambda: worker)
    FakeTTSWorker.instances = []
    monkeypatch.setattr(module, "TTSWorker", FakeTTSWorker)
    if save is not None:
        monkeypatch.setattr(module, "save_settings", save)
    if ai_test_factory is not None:
        monkeypatch.setattr(module, "create_ai_test_worker", ai_test_factory)
    else:
        FakeAITestWorker.instances = []
        monkeypatch.setattr(
            module, "create_ai_test_worker", lambda config: FakeAITestWorker(config)
        )
    if ai_models_factory is not None:
        monkeypatch.setattr(module, "create_ai_models_worker", ai_models_factory)
    else:
        FakeAIModelsWorker.instances = []
        monkeypatch.setattr(
            module,
            "create_ai_models_worker",
            lambda config: FakeAIModelsWorker(config),
        )
    return module.SettingsDialog(settings, current_size=current_size), worker


def _voice_names(dialog):
    names = []
    for i in range(dialog.tts_voice_list.count()):
        item = dialog.tts_voice_list.item(i)
        name = item.data(Qt.ItemDataRole.UserRole)
        if name:
            names.append(name)
    return names


def _emit_voices(dialog, worker, voices=None):
    worker.voices_ready.emit(list(voices if voices is not None else SAMPLE_VOICES))
    _app().processEvents()


def test_settings_dialog_has_ordered_categories_and_mapped_controls(
    monkeypatch, settings
):

    dialog, worker = _dialog(monkeypatch, settings)

    sidebar = dialog.findChild(QListWidget, "settingsCategoryList")
    pages = dialog.findChild(QStackedWidget, "settingsPages")
    assert [sidebar.item(i).text() for i in range(sidebar.count())] == [
        "General",
        "Appearance",
        "Audio & Speech",
        "AI Providers",
        "About",
    ]
    assert pages.count() == 5
    expected_pages = {
        "databaseRootInput": 0,
        "databaseRootBrowseButton": 0,
        "defaultDatabaseInput": 0,
        "windowWidthInput": 1,
        "windowHeightInput": 1,
        "fontFamilyInput": 1,
        "fontSizeInput": 1,
        "contentFontFamilyInput": 1,
        "contentFontSizeInput": 1,
        "ttsLanguageFilter": 2,
        "ttsGenderAll": 2,
        "ttsGenderMale": 2,
        "ttsGenderFemale": 2,
        "ttsVoiceSearch": 2,
        "ttsVoiceList": 2,
        "aiBaseUrlInput": 3,
        "aiModelInput": 3,
        "aiModelsRefreshButton": 3,
        "aiApiKeyInput": 3,
        "aiTimeoutInput": 3,
        "explanationLanguageInput": 3,
        "aiTestButton": 3,
        "aiTestStatusLabel": 3,
    }
    for object_name, page_index in expected_pages.items():
        control = dialog.findChild(QObject, object_name)
        assert control is not None, object_name
        assert pages.widget(page_index).isAncestorOf(control), object_name
    assert dialog.findChild(QObject, "ttsVoiceInput") is None
    assert dialog.findChild(QObject, "ttsSelectedSummary") is None
    assert dialog.findChild(QObject, "ttsSelectedName") is None
    assert dialog.findChild(QObject, "ttsSelectedMeta") is None
    assert dialog.findChild(QObject, "ttsPreviewButton") is None
    assert dialog.findChild(QObject, "saveSettingsButton") is not None
    assert dialog.findChild(QObject, "cancelSettingsButton") is not None
    assert worker.started
    assert dialog.voice_worker is worker
    dialog.close()


def test_about_page_reports_application_version(monkeypatch, settings):
    import kgb_srs.settings_dialog as module

    monkeypatch.setattr(module, "get_app_version", lambda: "2.1.0-dev")
    dialog, _ = _dialog(monkeypatch, settings)
    label = dialog.findChild(QLabel, "aboutVersionLabel")

    assert label is not None
    assert "2.1.0-dev" in label.text()
    dialog.close()


def test_live_window_size_overrides_stale_persisted_geometry(monkeypatch, settings):
    dialog, _ = _dialog(monkeypatch, settings, current_size=(1234, 876))

    assert dialog.window_width_input.value() == 1234
    assert dialog.window_height_input.value() == 876
    assert settings["width"] == 900
    assert settings["height"] == 700
    dialog.reject()


def test_switching_categories_does_not_save_or_mutate_settings(monkeypatch, settings):
    saved = []
    original = dict(settings)
    dialog, _ = _dialog(monkeypatch, settings, save=lambda staged: saved.append(staged))

    dialog.category_list.setCurrentRow(2)
    dialog.category_list.setCurrentRow(3)
    _app().processEvents()

    assert dialog.pages.currentIndex() == 3
    assert saved == []
    assert settings == original
    dialog.reject()


def test_successful_save_persists_all_staged_values_then_accepts(monkeypatch, settings):
    saved = []
    dialog, _ = _dialog(
        monkeypatch, settings, save=lambda staged: saved.append(dict(staged))
    )
    dialog.window_width_input.setValue(1024)
    # Outside the database root → normalized to empty on save.
    dialog.default_database_input.setText(" /tmp/test.barsky ")
    dialog.ai_api_key_input.setText(" new-key ")
    dialog.explanation_language_input.setText(" German ")

    dialog.save_button.click()

    assert len(saved) == 1
    assert saved[0]["width"] == 1024
    assert saved[0]["default_database"] == ""
    assert (
        saved[0]["ai_providers"][saved[0]["ai_active_provider"]]["api_key"] == "new-key"
    )
    assert "ai_api_key" not in saved[0]
    assert saved[0]["explanation_language"] == "German"
    assert settings == saved[0]
    assert dialog.result() == dialog.DialogCode.Accepted


def test_collect_staged_settings_synchronizes_active_ai_profile(monkeypatch, settings):
    """Collecting settings captures unsaved AI controls in the staged profile."""
    dialog, _ = _dialog(monkeypatch, settings)
    dialog.ai_base_url_input.setText(" https://example.test/v1 ")
    dialog.ai_model_input.setEditText(" staged-model ")
    dialog.ai_api_key_input.setText(" staged-key ")
    dialog.ai_timeout_input.setValue(12)

    staged = dialog._collect_staged_settings()
    active = staged["ai_active_provider"]

    assert staged["ai_providers"][active] == {
        "base_url": "https://example.test/v1",
        "model": "staged-model",
        "api_key": "staged-key",
        "timeout": 12,
    }
    assert dialog._ai_stage["ai_providers"][active] == staged["ai_providers"][active]
    assert settings["ai_providers"][active]["api_key"] == "secret"
    # Retain the prior private seam for existing extensions that used it.
    assert dialog._staged_settings() == staged
    dialog.reject()


def test_save_failure_keeps_live_settings_and_dialog_open(monkeypatch, settings):
    import kgb_srs.settings_dialog as module

    original = dict(settings)
    errors = []

    def fail(_staged):
        raise OSError("disk full")

    dialog, _ = _dialog(monkeypatch, settings, save=fail)
    monkeypatch.setattr(
        module.QMessageBox,
        "critical",
        lambda parent, title, message: errors.append((parent, title, message)),
    )
    dialog.window_width_input.setValue(1234)

    dialog.save_button.click()

    assert settings == original
    assert dialog.result() != dialog.DialogCode.Accepted
    assert errors == [(dialog, "Settings Not Saved", "disk full")]
    dialog.reject()


def test_database_browser_stages_relative_path_under_root(
    monkeypatch, settings, tmp_path
):
    dialog, _ = _dialog(monkeypatch, settings)
    # Point staged root at tmp so we control the under-root path.
    root = tmp_path / "dbs"
    root.mkdir()
    chosen = root / "Language-based" / "English_barsky.db"
    chosen.parent.mkdir(parents=True)
    chosen.write_bytes(b"")
    dialog.database_root_input.setText(str(root))

    calls = []

    def _fake_pick(root_path, start):
        calls.append((root_path, start))
        return str(chosen)

    monkeypatch.setattr(dialog, "_pick_file_under_root", _fake_pick)

    dialog.database_browse_button.click()

    expected_rel = os.path.normpath(os.path.join("Language-based", "English_barsky.db"))
    assert dialog.default_database_input.text() == expected_rel
    assert not os.path.isabs(dialog.default_database_input.text())
    assert settings["default_database"] == ""
    assert calls
    assert calls[0][0] == str(root)
    dialog.reject()


def test_database_browser_rejects_path_outside_root(monkeypatch, settings):
    import kgb_srs.settings_dialog as module

    dialog, _ = _dialog(monkeypatch, settings)
    warnings = []
    monkeypatch.setattr(
        module.QMessageBox,
        "warning",
        lambda parent, title, message: warnings.append((parent, title, message)),
    )
    monkeypatch.setattr(
        dialog,
        "_pick_file_under_root",
        lambda root_path, start: "/tmp/chosen.barsky",
    )

    dialog.database_browse_button.click()

    assert dialog.default_database_input.text() == ""
    assert settings["default_database"] == ""
    assert len(warnings) == 1
    assert warnings[0][1] == "Default Database"
    dialog.reject()


def test_pick_file_under_root_clamps_navigation_outside_root(
    monkeypatch, settings, tmp_path
):
    """Non-native dialog snaps back when the user navigates above the root."""
    import kgb_srs.settings_dialog as module
    from PyQt6.QtCore import QUrl

    root = tmp_path / "dbs"
    root.mkdir()
    outside = tmp_path / "other"
    outside.mkdir()
    dialog, _ = _dialog(monkeypatch, settings)

    created = {}

    class _EnteredSignal:
        def connect(self, slot):
            created["clamp"] = slot

    class FakeFileDialog:
        Option = module.QFileDialog.Option
        FileMode = module.QFileDialog.FileMode
        AcceptMode = module.QFileDialog.AcceptMode

        def __init__(self, parent, title, start):
            self.parent = parent
            self.title = title
            self.start = start
            self._directory = start
            self._sidebar = []
            self._name_filters = []
            self._options = set()
            self._file_mode = None
            self._accept_mode = None
            self.directoryEntered = _EnteredSignal()
            created["dialog"] = self

        def setOption(self, option, on=True):
            if on:
                self._options.add(option)
            else:
                self._options.discard(option)

        def setFileMode(self, mode):
            self._file_mode = mode

        def setAcceptMode(self, mode):
            self._accept_mode = mode

        def setNameFilters(self, filters):
            self._name_filters = list(filters)

        def setSidebarUrls(self, urls):
            self._sidebar = list(urls)

        def setDirectory(self, path):
            self._directory = path

        def exec(self):
            clamp = created["clamp"]
            # Leaving the root snaps back.
            clamp(str(outside))
            assert self._directory == str(root)
            # Staying under the root does not force setDirectory.
            before = self._directory
            clamp(str(root / "Language-based"))
            assert self._directory == before
            return module.QDialog.DialogCode.Accepted

        def selectedFiles(self):
            return [str(root / "Language-based" / "English_barsky.db")]

    monkeypatch.setattr(module, "QFileDialog", FakeFileDialog)

    path = dialog._pick_file_under_root(str(root), str(root))

    assert path == str(root / "Language-based" / "English_barsky.db")
    fake = created["dialog"]
    assert FakeFileDialog.Option.DontUseNativeDialog in fake._options
    assert fake._sidebar == [QUrl.fromLocalFile(str(root))]
    dialog.reject()


def test_save_stores_relative_default_database_under_root(
    monkeypatch, settings, tmp_path
):
    saved = []
    root = tmp_path / "dbs"
    root.mkdir()
    abs_db = root / "Language-based" / "English_barsky.db"
    abs_db.parent.mkdir(parents=True)
    abs_db.write_bytes(b"")

    dialog, _ = _dialog(
        monkeypatch, settings, save=lambda staged: saved.append(dict(staged))
    )
    dialog.database_root_input.setText(str(root))
    dialog.default_database_input.setText(str(abs_db))

    dialog.save_button.click()

    assert len(saved) == 1
    expected_rel = os.path.normpath(os.path.join("Language-based", "English_barsky.db"))
    assert saved[0]["default_database"] == expected_rel
    assert not os.path.isabs(saved[0]["default_database"])
    assert saved[0]["database_root"] == str(root)
    assert dialog.result() == dialog.DialogCode.Accepted


def test_database_root_browser_stages_selected_directory(monkeypatch, settings):
    import kgb_srs.settings_dialog as module
    from kgb_srs.config import DIR_DB

    dialog, _ = _dialog(monkeypatch, settings)
    assert dialog.database_root_input.text() == DIR_DB
    calls = []
    monkeypatch.setattr(
        module.QFileDialog,
        "getExistingDirectory",
        lambda *args, **kwargs: calls.append(args) or "/tmp/my-databases",
    )

    dialog.database_root_browse_button.click()

    assert dialog.database_root_input.text() == "/tmp/my-databases"
    assert settings.get("database_root", "") == ""
    assert calls
    dialog.reject()


def test_save_creates_canonical_database_root_structure(
    monkeypatch, settings, tmp_path
):
    from kgb_srs.config import CANONICAL_DB_SUBDIRS

    saved = []
    root = tmp_path / "dbs"
    dialog, _ = _dialog(
        monkeypatch, settings, save=lambda staged: saved.append(dict(staged))
    )
    dialog.database_root_input.setText(str(root))

    dialog.save_button.click()

    assert len(saved) == 1
    assert saved[0]["database_root"] == str(root)
    for subdir in CANONICAL_DB_SUBDIRS:
        assert (root / subdir).is_dir()
    assert dialog.result() == dialog.DialogCode.Accepted


def test_save_stores_empty_database_root_for_project_default(monkeypatch, settings):
    from kgb_srs.config import DIR_DB

    saved = []
    dialog, _ = _dialog(
        monkeypatch, settings, save=lambda staged: saved.append(dict(staged))
    )
    dialog.database_root_input.setText(DIR_DB)

    dialog.save_button.click()

    assert len(saved) == 1
    assert saved[0]["database_root"] == ""
    assert dialog.result() == dialog.DialogCode.Accepted


def test_voice_results_preserve_configured_selection_and_worker_lifetime(
    monkeypatch, settings
):
    dialog, worker = _dialog(monkeypatch, settings)
    worker.voices_ready.emit(
        [
            ("other", "en-US", "Female", "Other"),
            (settings["tts_voice"], "en-US", "Female", "Ava"),
        ]
    )
    _app().processEvents()

    assert dialog.current_voice == settings["tts_voice"]
    assert settings["tts_voice"] in _voice_names(dialog)
    assert (
        dialog.tts_voice_list.currentItem().data(Qt.ItemDataRole.UserRole)
        == settings["tts_voice"]
    )
    worker.finished.emit()
    assert worker.deleted
    assert dialog.voice_worker is None
    dialog.reject()


def test_save_accepts_after_finished_voice_worker_wrapper_is_deleted(
    monkeypatch, settings
):
    """A deleted QThread wrapper does not break the dialog's save path."""
    import kgb_srs.settings_dialog as module

    _app()
    worker = DeletedVoiceWorker()
    monkeypatch.setattr(module, "VoiceListWorker", lambda: worker)
    monkeypatch.setattr(module, "save_settings", lambda _settings: None)
    dialog = module.SettingsDialog(settings)

    worker.finished.emit()
    assert worker.deleted
    # A queued finished handler can leave this stale reference briefly visible.
    dialog.voice_worker = worker

    dialog.save_and_apply()

    assert dialog.result() == dialog.DialogCode.Accepted
    assert dialog.voice_worker is None


def test_finished_old_voice_worker_does_not_clear_replacement(monkeypatch, settings):
    """An old worker finishing late preserves the newer worker reference."""
    dialog, old_worker = _dialog(monkeypatch, settings)
    replacement_worker = FakeVoiceWorker()
    dialog.voice_worker = replacement_worker

    old_worker.finished.emit()

    assert dialog.voice_worker is replacement_worker
    dialog.reject()


def test_close_waits_for_running_workers_before_destroying_dialog(
    monkeypatch, settings
):
    """A close request waits for real QThread.finished signals from all workers."""
    _app()
    import kgb_srs.settings_dialog as module

    voice_worker = BlockingVoiceWorker()
    monkeypatch.setattr(module, "VoiceListWorker", lambda: voice_worker)
    monkeypatch.setattr(module, "TTSWorker", BlockingTTSWorker)
    dialog = module.SettingsDialog(settings)
    cleanup_calls = []
    monkeypatch.setattr(dialog, "_cleanup_tts_temp", lambda: cleanup_calls.append(True))
    preview_worker = None
    voice_finished = False
    preview_finished = False

    try:
        assert voice_worker.started_event.wait(1)
        dialog.show()
        _app().processEvents()
        dialog._preview_voice("en-US-AndrewNeural")
        preview_worker = dialog.preview_tts_worker
        assert preview_worker is not None
        assert preview_worker.started_event.wait(1)
        cleanup_calls.clear()

        assert dialog.close() is False
        assert dialog.isVisible()
        assert cleanup_calls == []

        voice_worker.release_event.set()
        assert voice_worker.wait(1000)
        voice_finished = True
        _app().processEvents()
        assert dialog.isVisible()
        assert cleanup_calls == []

        preview_worker.release_event.set()
        assert preview_worker.wait(1000)
        preview_finished = True
        _app().processEvents()
        assert not dialog.isVisible()
        assert cleanup_calls == [True]
    finally:
        if not voice_finished:
            voice_worker.release_event.set()
            voice_worker.wait(1000)
        if preview_worker is not None and not preview_finished:
            preview_worker.release_event.set()
            preview_worker.wait(1000)
        dialog.close()


def test_deferred_save_freezes_controls_and_keeps_persisted_snapshot(
    monkeypatch, settings
):
    """A worker-delayed accept cannot silently discard apparent later edits."""
    _app()
    import kgb_srs.settings_dialog as module

    voice_worker = BlockingVoiceWorker()
    saved = []
    monkeypatch.setattr(module, "VoiceListWorker", lambda: voice_worker)
    monkeypatch.setattr(
        module, "save_settings", lambda staged: saved.append(dict(staged))
    )
    dialog = module.SettingsDialog(settings)
    finished = False

    try:
        assert voice_worker.started_event.wait(1)
        dialog.window_width_input.setValue(1111)
        dialog.save_and_apply()

        assert saved[0]["width"] == 1111
        assert dialog._deferred_close_action == "accept"
        assert not dialog.isEnabled()
        assert not dialog.window_width_input.isEnabled()
        assert not dialog.database_root_input.isEnabled()

        # Programmatic changes are still possible in Qt, but user editing is
        # frozen and the deferred accept must retain the saved snapshot.
        dialog.window_width_input.setValue(1555)
        voice_worker.release_event.set()
        assert voice_worker.wait(1000)
        finished = True
        _app().processEvents()

        assert dialog.result() == dialog.DialogCode.Accepted
        assert len(saved) == 1
        assert saved[0]["width"] == 1111
        assert settings["width"] == 1111
    finally:
        if not finished:
            voice_worker.release_event.set()
            voice_worker.wait(1000)
        dialog.close()


def test_api_key_uses_existing_secret_line_edit(monkeypatch, settings):
    from kgb_srs.secret_line_edit import SecretLineEdit

    dialog, _ = _dialog(monkeypatch, settings)
    assert isinstance(dialog.ai_api_key_input, SecretLineEdit)
    assert SecretLineEdit.__module__ == "kgb_srs.secret_line_edit"
    dialog.reject()


def test_audio_page_list_is_usable_without_forcing_huge_width(monkeypatch, settings):
    dialog, _ = _dialog(monkeypatch, settings)
    assert dialog.minimumHeight() >= 480
    assert dialog.tts_voice_list.minimumWidth() == 0
    assert dialog.minimumWidth() < 900
    dialog.reject()


def test_large_app_font_keeps_category_labels_visible(monkeypatch, settings):
    _app()
    parent = QMainWindow()
    parent.setFont(QFont("Arial", 18))
    dialog = None
    try:
        import kgb_srs.settings_dialog as module

        worker = FakeVoiceWorker()
        monkeypatch.setattr(module, "VoiceListWorker", lambda: worker)
        dialog = module.SettingsDialog(settings, parent=parent)
        dialog.show()
        _app().processEvents()
        longest_label_width = dialog.category_list.sizeHintForColumn(0)

        assert dialog.category_list.viewport().width() >= longest_label_width
        assert (
            dialog.category_list.horizontalScrollBarPolicy()
            == Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
    finally:
        if dialog is not None:
            dialog.reject()
        parent.close()


def test_main_window_opens_extracted_dialog_and_applies_only_when_accepted(monkeypatch):
    _app()
    import kgb_srs.main_window as module

    class FakeDialog:
        results = [
            module.QDialog.DialogCode.Rejected,
            module.QDialog.DialogCode.Accepted,
        ]
        current_sizes = []

        def __init__(self, settings, parent=None, current_size=None):
            self.settings = settings
            self.parent = parent
            self.voice_worker = object()
            self.current_sizes.append(current_size)

        def exec(self):
            return self.results.pop(0)

    monkeypatch.setattr(module, "SettingsDialog", FakeDialog)

    class WindowStub:
        settings = {"width": 777, "height": 555}

        def width(self):
            return 1111

        def height(self):
            return 888

        def resize(self, width, height):
            calls.append(("resize", width, height))

        def apply_font_settings(self):
            calls.append(("font",))

        def redraw_canvas(self):
            calls.append(("redraw",))

    calls = []
    window = WindowStub()

    module.BarskyApp.open_settings_window(window)
    assert calls == []
    assert window.voice_worker is not None
    assert FakeDialog.current_sizes == [(1111, 888)]
    module.BarskyApp.open_settings_window(window)
    assert calls == [("resize", 777, 555), ("font",), ("redraw",)]
    assert FakeDialog.current_sizes == [(1111, 888), (1111, 888)]


def test_font_settings_are_scoped_to_main_window_and_owned_dialog(
    monkeypatch, settings
):
    app = _app()
    original_font = QFont(app.font())
    baseline = QFont("Sans Serif", 9)
    app.setFont(baseline)
    baseline_font = (app.font().family(), app.font().pointSize())

    from kgb_srs.main_window import BarskyApp
    import kgb_srs.settings_dialog as module

    worker = FakeVoiceWorker()
    monkeypatch.setattr(module, "VoiceListWorker", lambda: worker)
    window = QMainWindow()
    try:
        child = QWidget(window)
        for name in (
            "start_btn",
            "restart_review_btn",
            "previous_review_btn",
            "delete_entry_btn",
        ):
            setattr(window, name, QPushButton(window))
        window.settings = {"font_family": "Arial", "font_size": 23}
        window._button_style = BarskyApp._button_style
        window._toolbar_button_style = BarskyApp._toolbar_button_style
        window._apply_toolbar_font_styles = lambda ff, fs: (
            BarskyApp._apply_toolbar_font_styles(window, ff, fs)
        )

        BarskyApp.apply_font_settings(window)
        dialog = module.SettingsDialog(settings, parent=window, current_size=(900, 700))

        assert (app.font().family(), app.font().pointSize()) == baseline_font
        assert window.font().pointSize() == 23
        assert child.font().pointSize() == 23
        assert dialog.font().pointSize() == 23
        dialog.reject()
    finally:
        window.close()
        app.setFont(original_font)


def test_cancel_button_rejects_without_saving(monkeypatch, settings):
    saved = []
    original = dict(settings)
    dialog, _ = _dialog(monkeypatch, settings, save=lambda staged: saved.append(staged))
    dialog.window_width_input.setValue(1200)

    dialog.cancel_button.click()

    assert saved == []
    assert settings == original
    assert dialog.result() == dialog.DialogCode.Rejected


def test_appearance_page_has_ui_and_content_font_controls(monkeypatch, settings):
    """Appearance page exposes separate UI and content font controls."""
    from PyQt6.QtWidgets import QFormLayout, QLabel

    dialog, _ = _dialog(monkeypatch, settings)
    pages = dialog.findChild(QStackedWidget, "settingsPages")
    appearance = pages.widget(1)
    layout = appearance.layout()
    assert isinstance(layout, QFormLayout)

    labels = {}
    for row in range(layout.rowCount()):
        label_item = layout.itemAt(row, QFormLayout.ItemRole.LabelRole)
        field_item = layout.itemAt(row, QFormLayout.ItemRole.FieldRole)
        if label_item is None or field_item is None:
            continue
        label_w = label_item.widget()
        field_w = field_item.widget()
        if isinstance(label_w, QLabel) and field_w is not None:
            labels[label_w.text().rstrip(":")] = field_w.objectName()

    assert labels.get("UI Font Family") == "fontFamilyInput"
    assert labels.get("UI Font Size") == "fontSizeInput"
    assert labels.get("Content Font Family") == "contentFontFamilyInput"
    assert labels.get("Content Font Size") == "contentFontSizeInput"

    # QComboBox may not resolve missing families; value() / staged path is
    # the source of truth. Check size control and that family control exists.
    assert dialog.content_font_size_input.value() == settings["content_font_size"]
    assert dialog.content_font_size_input.minimum() <= 8
    assert dialog.content_font_size_input.maximum() >= 36
    assert dialog.content_font_family_input.count() > 0
    dialog.reject()


def test_content_font_settings_are_staged_and_saved(monkeypatch, settings):
    """Save stages and persists content_font_family / content_font_size."""
    saved = []
    dialog, _ = _dialog(
        monkeypatch, settings, save=lambda staged: saved.append(dict(staged))
    )

    # Prefer a family that exists in the combobox
    families = [
        dialog.content_font_family_input.itemText(i)
        for i in range(dialog.content_font_family_input.count())
    ]
    target_family = (
        "Courier New"
        if "Courier New" in families
        else (families[1] if len(families) > 1 else families[0])
    )
    dialog.content_font_family_input.setCurrentText(target_family)
    dialog.content_font_size_input.setValue(24)

    dialog.save_button.click()

    assert len(saved) == 1
    assert saved[0]["content_font_family"] == target_family
    assert saved[0]["content_font_size"] == 24
    assert settings["content_font_family"] == target_family
    assert settings["content_font_size"] == 24
    assert dialog.result() == dialog.DialogCode.Accepted


def test_default_settings_include_content_font_keys():
    from kgb_srs.config import DEFAULT_SETTINGS

    assert DEFAULT_SETTINGS["content_font_family"] == "Arial"
    assert DEFAULT_SETTINGS["content_font_size"] == 18
    assert DEFAULT_SETTINGS["tts_language"] == ""


def test_ai_test_button_uses_staged_values_and_disables_while_running(
    monkeypatch, settings
):
    saved = []
    dialog, _ = _dialog(
        monkeypatch, settings, save=lambda staged: saved.append(dict(staged))
    )
    dialog.ai_base_url_input.setText(" https://example.test/v1 ")
    dialog.ai_model_input.setEditText(" staged-model ")
    dialog.ai_api_key_input.setText(" staged-key ")
    dialog.ai_timeout_input.setValue(12)

    dialog.ai_test_button.click()
    _app().processEvents()

    assert len(FakeAITestWorker.instances) == 1
    worker = FakeAITestWorker.instances[0]
    assert worker.started
    assert dialog.ai_test_button.isEnabled() is False
    assert dialog.ai_test_status_label.text() == "Testing…"
    assert worker.config.base_url == "https://example.test/v1"
    assert worker.config.model == "staged-model"
    assert worker.config.api_key == "staged-key"
    assert worker.config.timeout_seconds == 12
    assert saved == []
    assert (
        settings["ai_providers"][settings["ai_active_provider"]]["api_key"] == "secret"
    )
    dialog.reject()


def test_ai_test_success_updates_status_and_reenables_button(monkeypatch, settings):
    dialog, _ = _dialog(monkeypatch, settings)
    dialog.ai_model_input.setEditText("gpt-test")
    dialog.ai_test_button.click()
    worker = FakeAITestWorker.instances[0]

    worker.result.emit(True, "OK — gpt-test reachable", 245.4)
    worker.finished.emit()
    _app().processEvents()

    assert dialog.ai_test_status_label.text() == "OK — 245 ms (gpt-test)"
    assert dialog.ai_test_button.isEnabled() is True
    assert dialog.ai_test_worker is None
    assert worker.deleted
    dialog.reject()


def test_ai_test_failure_updates_status_and_reenables_button(monkeypatch, settings):
    dialog, _ = _dialog(monkeypatch, settings)
    dialog.ai_test_button.click()
    worker = FakeAITestWorker.instances[0]

    worker.result.emit(False, "invalid API key", 12.0)
    worker.finished.emit()
    _app().processEvents()

    assert dialog.ai_test_status_label.text() == "Failed — invalid API key"
    assert dialog.ai_test_button.isEnabled() is True
    assert dialog.ai_test_worker is None
    assert worker.deleted
    dialog.reject()


def test_ai_test_missing_api_key_fails_without_hanging(monkeypatch, settings):
    """Missing key is reported via the worker result and does not hang the UI."""
    dialog, _ = _dialog(monkeypatch, settings)
    dialog.ai_api_key_input.setText("   ")
    dialog.ai_test_button.click()
    worker = FakeAITestWorker.instances[0]

    assert worker.config.api_key == ""
    # Simulate what the real worker would emit for a missing key.
    worker.result.emit(False, "API key is not set", -1.0)
    worker.finished.emit()
    _app().processEvents()

    assert dialog.ai_test_status_label.text() == "Failed — API key is not set"
    assert dialog.ai_test_button.isEnabled() is True
    dialog.reject()


def test_ai_provider_combo_loads_profiles_and_switches(monkeypatch, settings):
    settings["ai_providers"] = {
        "OpenAI": {
            "base_url": "https://api.openai.com/v1",
            "model": "gpt-4o-mini",
            "api_key": "sk-openai",
            "timeout": 30,
        },
        "TokenHub": {
            "base_url": "https://tokenhub.example/v1",
            "model": "deepseek-v4",
            "api_key": "sk-th",
            "timeout": 20,
        },
    }
    settings["ai_active_provider"] = "TokenHub"

    dialog, _ = _dialog(monkeypatch, settings)
    assert dialog.ai_provider_combo.count() == 2
    assert dialog.ai_provider_combo.currentText() == "TokenHub"
    assert dialog.ai_model_input.currentText() == "deepseek-v4"
    assert dialog.ai_api_key_input.text() == "sk-th"

    # Edit TokenHub, switch to OpenAI, then back — TokenHub edit kept.
    dialog.ai_model_input.setEditText("deepseek-edited")
    dialog.ai_provider_combo.setCurrentText("OpenAI")
    _app().processEvents()
    assert dialog.ai_model_input.currentText() == "gpt-4o-mini"
    assert dialog.ai_api_key_input.text() == "sk-openai"

    dialog.ai_provider_combo.setCurrentText("TokenHub")
    _app().processEvents()
    assert dialog.ai_model_input.currentText() == "deepseek-edited"
    dialog.reject()


def test_ai_provider_profiles_saved_on_apply(monkeypatch, settings):
    saved = []
    settings["ai_providers"] = {
        "OpenAI": {
            "base_url": "https://api.openai.com/v1",
            "model": "gpt-4o-mini",
            "api_key": "sk-openai",
            "timeout": 30,
        },
        "TokenHub": {
            "base_url": "https://tokenhub.example/v1",
            "model": "deepseek-v4",
            "api_key": "sk-th",
            "timeout": 20,
        },
    }
    settings["ai_active_provider"] = "OpenAI"
    dialog, _ = _dialog(
        monkeypatch, settings, save=lambda staged: saved.append(dict(staged))
    )
    dialog.ai_provider_combo.setCurrentText("TokenHub")
    _app().processEvents()
    dialog.ai_model_input.setEditText("flash-2")
    dialog.save_button.click()
    _app().processEvents()

    assert len(saved) == 1
    assert saved[0]["ai_active_provider"] == "TokenHub"
    assert "ai_model" not in saved[0]
    assert (
        saved[0]["ai_providers"][saved[0]["ai_active_provider"]]["model"] == "flash-2"
    )
    assert saved[0]["ai_providers"]["TokenHub"]["model"] == "flash-2"
    assert saved[0]["ai_providers"]["OpenAI"]["model"] == "gpt-4o-mini"
    assert settings["ai_active_provider"] == "TokenHub"


def test_ai_provider_delete_disabled_for_last_profile(monkeypatch, settings):
    dialog, _ = _dialog(monkeypatch, settings)
    # Fixture migrates to a single Default profile.
    assert dialog.ai_provider_combo.count() == 1
    assert dialog.ai_provider_delete_btn.isEnabled() is False
    dialog.reject()


def test_ai_models_refresh_uses_staged_values_and_disables_while_running(
    monkeypatch, settings
):
    saved = []
    dialog, _ = _dialog(
        monkeypatch, settings, save=lambda staged: saved.append(dict(staged))
    )
    dialog.ai_base_url_input.setText(" https://example.test/v1 ")
    dialog.ai_model_input.setEditText("keep-me")
    dialog.ai_api_key_input.setText(" staged-key ")
    dialog.ai_timeout_input.setValue(12)

    dialog.ai_models_refresh_btn.click()
    _app().processEvents()

    assert len(FakeAIModelsWorker.instances) == 1
    worker = FakeAIModelsWorker.instances[0]
    assert worker.started
    assert dialog.ai_models_refresh_btn.isEnabled() is False
    assert dialog.ai_test_status_label.text() == "Loading models…"
    assert worker.config.base_url == "https://example.test/v1"
    assert worker.config.api_key == "staged-key"
    assert worker.config.timeout_seconds == 12
    assert saved == []
    dialog.reject()


def test_ai_models_refresh_success_populates_combo_and_keeps_current(
    monkeypatch, settings
):
    dialog, _ = _dialog(monkeypatch, settings)
    dialog.ai_model_input.setEditText("my-custom-model")
    dialog.ai_models_refresh_btn.click()
    worker = FakeAIModelsWorker.instances[0]

    worker.result.emit(
        True,
        "3 model(s)",
        ["deepseek-v4-flash-202605", "gpt-4o-mini", "my-custom-model"],
    )
    worker.finished.emit()
    _app().processEvents()

    items = [
        dialog.ai_model_input.itemText(i) for i in range(dialog.ai_model_input.count())
    ]
    assert "deepseek-v4-flash-202605" in items
    assert "gpt-4o-mini" in items
    assert dialog.ai_model_input.currentText() == "my-custom-model"
    assert dialog.ai_test_status_label.text() == "OK — 3 model(s) available"
    assert dialog.ai_models_refresh_btn.isEnabled() is True
    assert dialog.ai_models_worker is None
    assert worker.deleted
    dialog.reject()


def test_ai_models_refresh_discards_result_after_provider_switch(monkeypatch, settings):
    settings["ai_providers"] = {
        "Profile A": {
            "base_url": "https://a.example/v1",
            "model": "a-model",
            "api_key": "a-key",
            "timeout": 30,
        },
        "Profile B": {
            "base_url": "https://b.example/v1",
            "model": "b-model",
            "api_key": "b-key",
            "timeout": 20,
        },
    }
    settings["ai_active_provider"] = "Profile A"
    dialog, _ = _dialog(monkeypatch, settings)
    dialog.ai_models_refresh_btn.click()
    worker = FakeAIModelsWorker.instances[0]

    dialog.ai_provider_combo.setCurrentText("Profile B")
    _app().processEvents()
    before_items = [
        dialog.ai_model_input.itemText(i) for i in range(dialog.ai_model_input.count())
    ]
    assert dialog.ai_model_input.currentText() == "b-model"

    worker.result.emit(True, "2 model(s)", ["a-model-new", "a-model-other"])
    worker.finished.emit()
    _app().processEvents()

    after_items = [
        dialog.ai_model_input.itemText(i) for i in range(dialog.ai_model_input.count())
    ]
    assert after_items == before_items == ["b-model"]
    assert dialog.ai_model_input.currentText() == "b-model"
    assert dialog.ai_models_refresh_btn.isEnabled() is True
    assert dialog.ai_models_worker is None
    dialog.reject()


def test_ai_test_discards_result_after_provider_switch(monkeypatch, settings):
    settings["ai_providers"] = {
        "Profile A": {
            "base_url": "https://a.example/v1",
            "model": "a-model",
            "api_key": "a-key",
            "timeout": 30,
        },
        "Profile B": {
            "base_url": "https://b.example/v1",
            "model": "b-model",
            "api_key": "b-key",
            "timeout": 20,
        },
    }
    settings["ai_active_provider"] = "Profile A"
    dialog, _ = _dialog(monkeypatch, settings)
    dialog.ai_test_button.click()
    worker = FakeAITestWorker.instances[0]

    dialog.ai_provider_combo.setCurrentText("Profile B")
    _app().processEvents()
    assert dialog.ai_test_status_label.text() == ""
    assert dialog.ai_model_input.currentText() == "b-model"

    worker.result.emit(True, "Profile A reachable", 123.0)
    worker.finished.emit()
    _app().processEvents()

    assert dialog.ai_test_status_label.text() == ""
    assert dialog.ai_test_button.isEnabled() is True
    assert dialog.ai_test_worker is None
    dialog.reject()


def test_ai_models_refresh_failure_updates_status_and_reenables(monkeypatch, settings):
    dialog, _ = _dialog(monkeypatch, settings)
    dialog.ai_models_refresh_btn.click()
    worker = FakeAIModelsWorker.instances[0]

    worker.result.emit(False, "invalid API key", [])
    worker.finished.emit()
    _app().processEvents()

    assert dialog.ai_test_status_label.text() == "Models — invalid API key"
    assert dialog.ai_models_refresh_btn.isEnabled() is True
    assert dialog.ai_models_worker is None
    dialog.reject()


# ---------------------------------------------------------------------------
# Voice picker: filters, selection, preview, empty/error states
# ---------------------------------------------------------------------------


def test_gender_filter_narrows_voice_list(monkeypatch, settings):
    dialog, worker = _dialog(monkeypatch, settings)
    _emit_voices(dialog, worker)

    dialog.tts_gender_male.click()
    _app().processEvents()
    male = _voice_names(dialog)
    assert male == ["en-US-AndrewNeural", "zh-CN-YunxiNeural"]

    dialog.tts_gender_female.click()
    _app().processEvents()
    female = _voice_names(dialog)
    assert female == [
        "en-US-AvaMultilingualNeural",
        "en-GB-SoniaNeural",
        "zh-CN-XiaoxiaoNeural",
    ]

    dialog.tts_gender_all.click()
    _app().processEvents()
    assert _voice_names(dialog) == [v[0] for v in SAMPLE_VOICES]
    dialog.reject()


def test_language_filter_narrows_voice_list(monkeypatch, settings):
    dialog, worker = _dialog(monkeypatch, settings)
    _emit_voices(dialog, worker)

    # Language combo: All + sorted locales en-GB, en-US, zh-CN
    index = dialog.tts_language_filter.findData("zh-CN")
    assert index > 0
    dialog.tts_language_filter.setCurrentIndex(index)
    _app().processEvents()
    assert _voice_names(dialog) == [
        "zh-CN-XiaoxiaoNeural",
        "zh-CN-YunxiNeural",
    ]

    dialog.tts_language_filter.setCurrentIndex(0)  # All languages
    _app().processEvents()
    assert len(_voice_names(dialog)) == len(SAMPLE_VOICES)
    dialog.reject()


def test_search_filter_narrows_voice_list(monkeypatch, settings):
    dialog, worker = _dialog(monkeypatch, settings)
    _emit_voices(dialog, worker)

    dialog.tts_voice_search.setText("sonia")
    _app().processEvents()
    assert _voice_names(dialog) == ["en-GB-SoniaNeural"]

    dialog.tts_voice_search.setText("YUNXI")
    _app().processEvents()
    assert _voice_names(dialog) == ["zh-CN-YunxiNeural"]

    dialog.tts_voice_search.clear()
    _app().processEvents()
    assert len(_voice_names(dialog)) == len(SAMPLE_VOICES)
    dialog.reject()


def test_selecting_list_item_stages_voice_short_name(monkeypatch, settings):
    saved = []
    dialog, worker = _dialog(
        monkeypatch, settings, save=lambda staged: saved.append(dict(staged))
    )
    _emit_voices(dialog, worker)

    # Select Andrew (row 1 in unfiltered list)
    target = "en-US-AndrewNeural"
    for i in range(dialog.tts_voice_list.count()):
        item = dialog.tts_voice_list.item(i)
        if item.data(Qt.ItemDataRole.UserRole) == target:
            dialog.tts_voice_list.setCurrentRow(i)
            break
    _app().processEvents()

    assert dialog.current_voice == target
    assert dialog._collect_staged_settings()["tts_voice"] == target
    assert settings["tts_voice"] == "en-US-AvaMultilingualNeural"  # not saved yet

    dialog.save_button.click()
    assert saved[0]["tts_voice"] == target
    assert settings["tts_voice"] == target


def test_initial_configured_voice_remains_selected_after_voices_ready(
    monkeypatch, settings
):
    settings["tts_voice"] = "zh-CN-YunxiNeural"
    dialog, worker = _dialog(monkeypatch, settings)
    assert dialog.current_voice == "zh-CN-YunxiNeural"

    _emit_voices(dialog, worker)

    assert dialog.current_voice == "zh-CN-YunxiNeural"
    assert (
        dialog.tts_voice_list.currentItem().data(Qt.ItemDataRole.UserRole)
        == "zh-CN-YunxiNeural"
    )
    dialog.reject()


def test_preview_row_button_starts_tts_worker_without_saving(monkeypatch, settings):
    saved = []
    dialog, worker = _dialog(
        monkeypatch, settings, save=lambda staged: saved.append(dict(staged))
    )
    _emit_voices(dialog, worker)

    # Select a non-default voice, then preview via its row button
    target = "en-GB-SoniaNeural"
    preview_btn = None
    for i in range(dialog.tts_voice_list.count()):
        item = dialog.tts_voice_list.item(i)
        if item.data(Qt.ItemDataRole.UserRole) == target:
            dialog.tts_voice_list.setCurrentRow(i)
            row = dialog.tts_voice_list.itemWidget(item)
            preview_btn = row.preview_button
            break
    _app().processEvents()
    assert preview_btn is not None

    preview_btn.click()
    _app().processEvents()

    assert len(FakeTTSWorker.instances) == 1
    tts = FakeTTSWorker.instances[0]
    assert tts.started
    assert tts.voice == target
    assert "preview" in tts.text.lower() or "Hello" in tts.text
    assert preview_btn.isEnabled() is False
    assert saved == []
    assert settings["tts_voice"] == "en-US-AvaMultilingualNeural"

    tts.audio_ready.emit("/tmp/fake-preview.mp3")
    tts.finished.emit()
    _app().processEvents()
    assert preview_btn.isEnabled() is True
    assert dialog.preview_tts_worker is None
    assert saved == []
    dialog.reject()


def test_filters_do_not_save_settings(monkeypatch, settings):
    saved = []
    original = dict(settings)
    dialog, worker = _dialog(
        monkeypatch, settings, save=lambda staged: saved.append(dict(staged))
    )
    _emit_voices(dialog, worker)

    dialog.tts_gender_male.click()
    dialog.tts_language_filter.setCurrentIndex(
        dialog.tts_language_filter.findData("en-US")
    )
    dialog.tts_voice_search.setText("Andrew")
    dialog.category_list.setCurrentRow(0)
    dialog.category_list.setCurrentRow(2)
    _app().processEvents()

    assert saved == []
    assert settings == original
    dialog.reject()


def test_language_filter_is_remembered_on_save_and_restore(monkeypatch, settings):
    saved = []
    settings["tts_language"] = "zh-CN"
    dialog, worker = _dialog(
        monkeypatch, settings, save=lambda staged: saved.append(dict(staged))
    )
    _emit_voices(dialog, worker)

    assert dialog.current_language == "zh-CN"
    assert dialog.tts_language_filter.currentData() == "zh-CN"
    assert _voice_names(dialog) == [
        "zh-CN-XiaoxiaoNeural",
        "zh-CN-YunxiNeural",
    ]

    # Change language filter, then save
    en_index = dialog.tts_language_filter.findData("en-US")
    dialog.tts_language_filter.setCurrentIndex(en_index)
    _app().processEvents()
    assert dialog.current_language == "en-US"
    assert dialog._collect_staged_settings()["tts_language"] == "en-US"
    assert settings["tts_language"] == "zh-CN"  # not saved yet

    dialog.save_button.click()
    assert saved[0]["tts_language"] == "en-US"
    assert settings["tts_language"] == "en-US"


def test_empty_voice_list_still_allows_save_of_current_voice(monkeypatch, settings):
    saved = []
    dialog, worker = _dialog(
        monkeypatch, settings, save=lambda staged: saved.append(dict(staged))
    )
    worker.voices_ready.emit([])
    _app().processEvents()

    assert dialog.current_voice == settings["tts_voice"]
    assert dialog._collect_staged_settings()["tts_voice"] == settings["tts_voice"]
    assert dialog._collect_staged_settings()["tts_language"] == ""

    dialog.save_button.click()
    assert saved[0]["tts_voice"] == settings["tts_voice"]
    assert dialog.result() == dialog.DialogCode.Accepted


def test_voice_error_still_allows_save_of_current_voice(monkeypatch, settings):
    saved = []
    dialog, worker = _dialog(
        monkeypatch, settings, save=lambda staged: saved.append(dict(staged))
    )
    worker.error.emit("network down")
    _app().processEvents()

    assert dialog.current_voice == settings["tts_voice"]
    assert dialog._collect_staged_settings()["tts_voice"] == settings["tts_voice"]
    dialog.save_button.click()
    assert saved[0]["tts_voice"] == settings["tts_voice"]


def test_preview_cleanup_unlinks_temp_on_next_preview_and_close(
    tmp_path, monkeypatch, settings
):
    """R2-2: settings preview unlinks previous temp MP3 and cleans on close."""
    _app()
    from kgb_srs import settings_dialog as module
    from kgb_srs.settings_dialog import SettingsDialog

    FakeTTSWorker.instances = []
    monkeypatch.setattr(module, "TTSWorker", FakeTTSWorker)
    monkeypatch.setattr(module, "VoiceListWorker", FakeVoiceWorker)

    dialog = SettingsDialog(settings)
    try:
        old = tmp_path / "barsky_tts_preview_old.mp3"
        new = tmp_path / "barsky_tts_preview_new.mp3"
        old.write_bytes(b"old")
        new.write_bytes(b"new")
        dialog._tts_temp_path = str(old)

        # Force a preview start; FakeTTSWorker does not auto-emit.
        dialog._preview_voice("en-US-AndrewNeural")
        assert not old.exists()
        assert len(FakeTTSWorker.instances) == 1
        tts = FakeTTSWorker.instances[0]
        tts.audio_ready.emit(str(new))
        assert dialog._tts_temp_path == str(new)
        assert new.exists()

        dialog.close()
        assert not new.exists()
        assert dialog._tts_temp_path is None
    finally:
        dialog.close()


def test_deferred_close_discards_preview_audio_and_suppresses_error(
    tmp_path, monkeypatch, settings
):
    """Late preview signals cannot restart playback or show a modal on close."""
    from unittest.mock import MagicMock

    dialog, _worker = _dialog(monkeypatch, settings)
    preview = tmp_path / "late-preview.mp3"
    preview.write_bytes(b"audio")
    dialog.preview_player.setSource = MagicMock()
    dialog.preview_player.play = MagicMock()
    dialog._deferred_close_action = "close"

    dialog._on_preview_finished(str(preview))

    assert not preview.exists()
    assert dialog._tts_temp_path is None
    dialog.preview_player.setSource.assert_not_called()
    dialog.preview_player.play.assert_not_called()

    warning = MagicMock()
    monkeypatch.setattr("kgb_srs.settings_dialog.QMessageBox.warning", warning)
    dialog._on_preview_error("late failure")
    warning.assert_not_called()
    dialog._deferred_close_action = None
    dialog.reject()


@pytest.mark.parametrize("action", ["close", "accept", "reject"])
def test_immediate_dialog_exit_discards_queued_preview_callbacks(
    tmp_path, monkeypatch, settings, action
):
    """Completed preview workers cannot present queued payloads after exit."""
    from unittest.mock import MagicMock

    dialog, _worker = _dialog(monkeypatch, settings)
    preview = tmp_path / "barsky_tts_preview_after_exit.mp3"
    preview.write_bytes(b"audio")
    dialog.preview_player.setSource = MagicMock()
    dialog.preview_player.play = MagicMock()
    warning = MagicMock()
    monkeypatch.setattr("kgb_srs.settings_dialog.QMessageBox.warning", warning)

    dialog._preview_voice("en-US-AndrewNeural")
    preview_worker = dialog.preview_tts_worker
    assert preview_worker is not None

    getattr(dialog, action)()
    preview_worker.audio_ready.emit(str(preview))
    preview_worker.error.emit("late failure")

    assert dialog._terminal_closing
    assert not preview.exists()
    assert dialog._tts_temp_path is None
    dialog.preview_player.setSource.assert_not_called()
    dialog.preview_player.play.assert_not_called()
    warning.assert_not_called()


@pytest.mark.parametrize("action", ["close", "accept", "reject"])
def test_dialog_exit_stops_preview_before_temp_cleanup(monkeypatch, settings, action):
    """Every immediate dialog exit stops preview playback before cleanup."""
    from unittest.mock import MagicMock

    dialog, _worker = _dialog(monkeypatch, settings)
    calls = []
    dialog.preview_player.stop = MagicMock(side_effect=lambda: calls.append("stop"))
    monkeypatch.setattr(dialog, "_cleanup_tts_temp", lambda: calls.append("cleanup"))

    getattr(dialog, action)()

    assert calls[:2] == ["stop", "cleanup"]
