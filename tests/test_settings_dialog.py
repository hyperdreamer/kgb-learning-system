"""Focused tests for the categorized application settings dialog."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PyQt6")

from PyQt6.QtCore import QObject, Qt, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QApplication,
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


@pytest.fixture
def settings():
    return {
        "width": 900,
        "height": 700,
        "font_family": "Arial",
        "font_size": 14,
        "default_database": "",
        "tts_voice": "en-US-AvaMultilingualNeural",
        "ai_base_url": "https://api.openai.com/v1",
        "ai_model": "gpt-4o-mini",
        "ai_api_key": "secret",
        "ai_timeout": 30,
        "learned_language": "English",
        "explanation_language": "Chinese",
    }


def _dialog(monkeypatch, settings, save=None, current_size=None):
    _app()
    import kgb_srs.settings_dialog as module

    worker = FakeVoiceWorker()
    monkeypatch.setattr(module, "VoiceListWorker", lambda: worker)
    if save is not None:
        monkeypatch.setattr(module, "save_settings", save)
    return module.SettingsDialog(
        settings, current_size=current_size
    ), worker


def test_settings_dialog_has_ordered_categories_and_mapped_controls(monkeypatch, settings):
    from kgb_srs.settings_dialog import SettingsDialog

    dialog, worker = _dialog(monkeypatch, settings)

    sidebar = dialog.findChild(QListWidget, "settingsCategoryList")
    pages = dialog.findChild(QStackedWidget, "settingsPages")
    assert [sidebar.item(i).text() for i in range(sidebar.count())] == [
        "General", "Appearance", "Audio & Speech", "AI Provider", "Languages"
    ]
    assert pages.count() == 5
    expected_pages = {
        "defaultDatabaseInput": 0,
        "windowWidthInput": 1,
        "windowHeightInput": 1,
        "fontFamilyInput": 1,
        "fontSizeInput": 1,
        "ttsVoiceInput": 2,
        "aiBaseUrlInput": 3,
        "aiModelInput": 3,
        "aiApiKeyInput": 3,
        "aiTimeoutInput": 3,
        "learnedLanguageInput": 4,
        "explanationLanguageInput": 4,
    }
    for object_name, page_index in expected_pages.items():
        control = dialog.findChild(QObject, object_name)
        assert control is not None, object_name
        assert pages.widget(page_index).isAncestorOf(control), object_name
    assert dialog.findChild(QObject, "saveSettingsButton") is not None
    assert dialog.findChild(QObject, "cancelSettingsButton") is not None
    assert worker.started
    assert dialog.voice_worker is worker
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

    dialog.category_list.setCurrentRow(3)
    dialog.category_list.setCurrentRow(4)
    _app().processEvents()

    assert dialog.pages.currentIndex() == 4
    assert saved == []
    assert settings == original
    dialog.reject()


def test_successful_save_persists_all_staged_values_then_accepts(monkeypatch, settings):
    saved = []
    dialog, _ = _dialog(monkeypatch, settings, save=lambda staged: saved.append(dict(staged)))
    dialog.window_width_input.setValue(1024)
    dialog.default_database_input.setText(" /tmp/test.barsky ")
    dialog.ai_api_key_input.setText(" new-key ")
    dialog.learned_language_input.setText(" German ")

    dialog.save_button.click()

    assert len(saved) == 1
    assert saved[0]["width"] == 1024
    assert saved[0]["default_database"] == "/tmp/test.barsky"
    assert saved[0]["ai_api_key"] == "new-key"
    assert saved[0]["learned_language"] == "German"
    assert settings == saved[0]
    assert dialog.result() == dialog.DialogCode.Accepted


def test_save_failure_keeps_live_settings_and_dialog_open(monkeypatch, settings):
    import kgb_srs.settings_dialog as module

    original = dict(settings)
    errors = []

    def fail(_staged):
        raise OSError("disk full")

    dialog, _ = _dialog(monkeypatch, settings, save=fail)
    monkeypatch.setattr(
        module.QMessageBox, "critical",
        lambda parent, title, message: errors.append((parent, title, message)),
    )
    dialog.window_width_input.setValue(1234)

    dialog.save_button.click()

    assert settings == original
    assert dialog.result() != dialog.DialogCode.Accepted
    assert errors == [(dialog, "Settings Not Saved", "disk full")]
    dialog.reject()


def test_database_browser_stages_selected_path(monkeypatch, settings):
    import kgb_srs.settings_dialog as module

    dialog, _ = _dialog(monkeypatch, settings)
    calls = []
    monkeypatch.setattr(
        module.QFileDialog,
        "getOpenFileName",
        lambda *args: (calls.append(args) or ("/tmp/chosen.barsky", "")),
    )

    dialog.database_browse_button.click()

    assert dialog.default_database_input.text() == "/tmp/chosen.barsky"
    assert settings["default_database"] == ""
    assert calls
    dialog.reject()


def test_voice_results_preserve_configured_selection_and_worker_lifetime(monkeypatch, settings):
    dialog, worker = _dialog(monkeypatch, settings)
    worker.voices_ready.emit([
        ("other", "en-US", "Female", "Other"),
        (settings["tts_voice"], "en-US", "Female", "Ava"),
    ])

    assert dialog.tts_voice_input.currentData() == settings["tts_voice"]
    worker.finished.emit()
    assert worker.deleted
    assert dialog.voice_worker is worker
    dialog.reject()


def test_api_key_uses_existing_secret_line_edit(monkeypatch, settings):
    from kgb_srs.main_window import SecretLineEdit

    dialog, _ = _dialog(monkeypatch, settings)
    assert isinstance(dialog.ai_api_key_input, SecretLineEdit)
    assert SecretLineEdit.__module__ == "kgb_srs.secret_line_edit"
    dialog.reject()


def test_dialog_does_not_force_wide_voice_controls(monkeypatch, settings):
    dialog, _ = _dialog(monkeypatch, settings)
    assert dialog.tts_voice_input.minimumWidth() == 0
    assert dialog.tts_voice_input.view().minimumWidth() == 0
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
        results = [module.QDialog.DialogCode.Rejected, module.QDialog.DialogCode.Accepted]
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

    calls = []
    window = WindowStub()

    module.BarskyApp.open_settings_window(window)
    assert calls == []
    assert window.voice_worker is not None
    assert FakeDialog.current_sizes == [(1111, 888)]
    module.BarskyApp.open_settings_window(window)
    assert calls == [("resize", 777, 555), ("font",)]
    assert FakeDialog.current_sizes == [(1111, 888), (1111, 888)]


def test_font_settings_are_scoped_to_main_window_and_owned_dialog(monkeypatch, settings):
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
            "start_btn", "restart_review_btn", "previous_review_btn",
            "delete_entry_btn",
        ):
            setattr(window, name, QPushButton(window))
        window.settings = {"font_family": "Arial", "font_size": 23}
        window._button_style = BarskyApp._button_style

        BarskyApp.apply_font_settings(window)
        dialog = module.SettingsDialog(
            settings, parent=window, current_size=(900, 700)
        )

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

