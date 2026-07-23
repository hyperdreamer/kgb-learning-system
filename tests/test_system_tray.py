"""Tests for desktop system-tray lifecycle behavior."""

from types import SimpleNamespace

import pytest

from tests.qt_helpers import qt_app


class _FakeSignal:
    def __init__(self):
        self._slots = []

    def connect(self, slot):
        self._slots.append(slot)

    def emit(self, *args):
        for slot in self._slots:
            slot(*args)


class _FakeTrayIcon:
    def __init__(self):
        self.activated = _FakeSignal()
        self.context_menu = None
        self.shown = False
        self.hidden = False
        self.tool_tip = ""

    def setContextMenu(self, menu):
        self.context_menu = menu

    def setToolTip(self, text):
        self.tool_tip = text

    def show(self):
        self.shown = True

    def hide(self):
        self.hidden = True


def test_system_tray_menu_restores_window_and_requests_quit():
    """The visible tray icon exposes Show/Quit controls and double-click restore."""
    qt_app()
    pytest.importorskip("PyQt6")
    from PyQt6.QtWidgets import QSystemTrayIcon, QWidget

    from kgb_srs.app_icon import APPLICATION_NAME
    from kgb_srs.system_tray import SystemTrayController

    class TrayWindow(QWidget):
        def __init__(self):
            super().__init__()
            self.show_normal_calls = 0
            self.raise_calls = 0
            self.activate_calls = 0
            self.quit_requests = 0

        def showNormal(self):
            self.show_normal_calls += 1

        def raise_(self):
            self.raise_calls += 1

        def activateWindow(self):
            self.activate_calls += 1

        def quit_from_system_tray(self):
            self.quit_requests += 1

    window = TrayWindow()
    tray_icon = _FakeTrayIcon()
    SystemTrayController(window, tray_icon)

    assert tray_icon.shown
    assert tray_icon.tool_tip == APPLICATION_NAME
    assert tray_icon.context_menu is not None

    actions = {action.text(): action for action in tray_icon.context_menu.actions()}
    assert set(actions) == {"Show", "Quit"}

    actions["Show"].trigger()
    tray_icon.activated.emit(QSystemTrayIcon.ActivationReason.DoubleClick)
    actions["Quit"].trigger()

    assert window.show_normal_calls == 2
    assert window.raise_calls == 2
    assert window.activate_calls == 2
    assert window.quit_requests == 1

    window.deleteLater()


def test_create_system_tray_uses_the_branded_icon_when_available(monkeypatch):
    """An available desktop tray receives and displays the app's branded icon."""
    qt_app()
    pytest.importorskip("PyQt6")
    from PyQt6.QtWidgets import QWidget

    import kgb_srs.system_tray as system_tray

    created_icons = []

    class AvailableTrayIcon(_FakeTrayIcon):
        @staticmethod
        def isSystemTrayAvailable():
            return True

        def __init__(self, icon, parent):
            super().__init__()
            self.icon = icon
            self.parent = parent
            created_icons.append(self)

    monkeypatch.setattr(system_tray, "QSystemTrayIcon", AvailableTrayIcon)
    window = QWidget()

    controller = system_tray.create_system_tray(window)

    assert controller is not None
    assert len(created_icons) == 1
    assert not created_icons[0].icon.isNull()
    assert created_icons[0].parent is window
    assert created_icons[0].shown

    window.deleteLater()


def test_create_system_tray_keeps_standard_close_behavior_when_unavailable(
    monkeypatch,
):
    """Unsupported desktops must not hide the app behind an inaccessible icon."""
    qt_app()
    pytest.importorskip("PyQt6")
    from PyQt6.QtWidgets import QWidget

    import kgb_srs.system_tray as system_tray

    created_icons = []

    class UnavailableTrayIcon(_FakeTrayIcon):
        @staticmethod
        def isSystemTrayAvailable():
            return False

        def __init__(self, *_args):
            super().__init__()
            created_icons.append(self)

    monkeypatch.setattr(system_tray, "QSystemTrayIcon", UnavailableTrayIcon)
    window = QWidget()

    assert system_tray.create_system_tray(window) is None
    assert created_icons == []

    window.deleteLater()


def test_window_close_hides_to_an_installed_system_tray():
    """A normal title-bar close keeps the process alive and does no shutdown work."""
    pytest.importorskip("PyQt6")
    from kgb_srs.main_window import BarskyApp

    hidden = []
    shutdown_work = []

    class Event:
        accepted = False
        ignored = False

        def accept(self):
            self.accepted = True

        def ignore(self):
            self.ignored = True

    window = SimpleNamespace(
        _quit_requested=False,
        _system_tray=object(),
        hide=lambda: hidden.append(True),
        tts_worker=None,
        settings={},
        current_db_path=None,
        width=lambda: 800,
        height=lambda: 600,
        _save_settings=lambda: shutdown_work.append("save"),
        _stop_tts_playback=lambda: shutdown_work.append("stop"),
        _cleanup_tts_temp=lambda: shutdown_work.append("cleanup"),
    )
    event = Event()

    BarskyApp.closeEvent(window, event)

    assert event.ignored
    assert not event.accepted
    assert hidden == [True]
    assert shutdown_work == []


def test_quit_from_system_tray_requests_one_terminal_window_close():
    """Quit from the tray bypasses hide-to-tray behavior and is idempotent."""
    pytest.importorskip("PyQt6")
    from kgb_srs.main_window import BarskyApp

    close_requests = []
    tray_icon = _FakeTrayIcon()
    window = SimpleNamespace(
        _quit_requested=False,
        _system_tray=tray_icon,
        close=lambda: close_requests.append(True),
    )

    BarskyApp.quit_from_system_tray(window)
    BarskyApp.quit_from_system_tray(window)

    assert window._quit_requested
    assert tray_icon.hidden
    assert close_requests == [True]


def test_terminal_tray_close_exits_the_qt_application(monkeypatch):
    """The explicit tray Quit completes process shutdown after window cleanup."""
    pytest.importorskip("PyQt6")
    import kgb_srs.main_window as main_window

    quit_calls = []

    class Application:
        def quit(self):
            quit_calls.append(True)

    class QApplicationStub:
        @staticmethod
        def instance():
            return Application()

    monkeypatch.setattr(main_window, "QApplication", QApplicationStub, raising=False)

    class Event:
        accepted = False
        ignored = False

        def accept(self):
            self.accepted = True

        def ignore(self):
            self.ignored = True

    window = SimpleNamespace(
        _quit_requested=True,
        _system_tray=_FakeTrayIcon(),
        tts_worker=None,
        settings={},
        current_db_path=None,
        width=lambda: 800,
        height=lambda: 600,
        _save_settings=lambda: None,
        _stop_tts_playback=lambda: None,
        _cleanup_tts_temp=lambda: None,
    )
    event = Event()

    main_window.BarskyApp.closeEvent(window, event)

    assert event.accepted
    assert not event.ignored
    assert quit_calls == [True]


def test_installing_a_tray_disables_auto_exit_when_the_window_hides(monkeypatch):
    """The hidden last window must not end the event loop before tray Quit."""
    pytest.importorskip("PyQt6")
    import kgb_srs.main_window as main_window

    quit_on_last_window_closed = []
    tray = object()

    class Application:
        def setQuitOnLastWindowClosed(self, enabled):
            quit_on_last_window_closed.append(enabled)

    class QApplicationStub:
        @staticmethod
        def instance():
            return Application()

    monkeypatch.setattr(main_window, "QApplication", QApplicationStub, raising=False)
    monkeypatch.setattr(
        main_window,
        "create_system_tray",
        lambda window: tray,
        raising=False,
    )
    window = SimpleNamespace(_system_tray=None)

    installed = main_window.BarskyApp.install_system_tray(window)

    assert installed is tray
    assert window._system_tray is tray
    assert quit_on_last_window_closed == [False]


def test_application_launcher_installs_the_tray_before_showing_the_window(
    monkeypatch,
):
    """A normal application launch wires tray lifecycle behavior into its window."""
    pytest.importorskip("PyQt6")
    from PyQt6 import QtWidgets

    import kgb_srs.app_icon as app_icon
    import kgb_srs.main_window as main_window
    import main

    events = []

    class Application:
        def __init__(self, argv):
            events.append(("application", argv))

        def exec(self):
            events.append(("exec",))
            return 17

    class Window:
        def __init__(self, *, settings_file):
            events.append(("window", settings_file))

        def install_system_tray(self):
            events.append(("install_tray",))

        def show(self):
            events.append(("show",))

    monkeypatch.setattr(QtWidgets, "QApplication", Application)
    monkeypatch.setattr(main_window, "BarskyApp", Window)
    monkeypatch.setattr(
        app_icon,
        "configure_application",
        lambda _application: events.append(("configure",)),
    )

    assert main.run_application("custom-settings.json") == 17
    assert [event[0] for event in events] == [
        "application",
        "configure",
        "window",
        "install_tray",
        "show",
        "exec",
    ]
