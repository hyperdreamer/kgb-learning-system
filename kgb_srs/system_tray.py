"""Desktop system-tray controls for the application window."""

from PyQt6.QtWidgets import QMenu, QSystemTrayIcon

from .app_icon import APPLICATION_NAME, get_app_icon


class SystemTrayController:
    """Own the tray icon and its window restore / application quit actions."""

    def __init__(self, window, tray_icon):
        self._window = window
        self._tray_icon = tray_icon

        menu = QMenu(window)
        show_action = menu.addAction("Show")
        show_action.triggered.connect(self.show_window)
        quit_action = menu.addAction("Quit")
        quit_action.triggered.connect(self.quit_application)

        self._tray_icon.setContextMenu(menu)
        self._tray_icon.setToolTip(APPLICATION_NAME)
        self._tray_icon.activated.connect(self._on_activated)
        self._tray_icon.show()

    def hide(self) -> None:
        """Remove the icon while a terminal application shutdown is pending."""
        self._tray_icon.hide()

    def show_window(self) -> None:
        """Restore the main window and make it the active desktop window."""
        self._window.showNormal()
        self._window.raise_()
        self._window.activateWindow()

    def quit_application(self) -> None:
        """Request the main window's terminal shutdown path."""
        self._window.quit_from_system_tray()

    def _on_activated(self, reason) -> None:
        """Restore the window for the usual left-click tray interactions."""
        restore_reasons = (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        )
        if reason in restore_reasons:
            self.show_window()


def create_system_tray(window):
    """Create and display a tray controller when the desktop supports one.

    Returning ``None`` on unsupported desktops keeps the standard window-close
    behavior, rather than hiding the only way a user could reach the app.
    """
    if not QSystemTrayIcon.isSystemTrayAvailable():
        return None

    tray_icon = QSystemTrayIcon(get_app_icon(), window)
    return SystemTrayController(window, tray_icon)
