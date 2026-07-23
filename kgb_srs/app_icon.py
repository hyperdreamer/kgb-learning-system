"""Application identity and branded window icon."""

from pathlib import Path

from PyQt6.QtGui import QIcon


APPLICATION_NAME = "KGB 5-Box SRS System"
APP_ICON_PATH = Path(__file__).resolve().parent / "assets" / "app-icon.svg"


def get_app_icon() -> QIcon:
    """Load the scalable icon used by the application and its windows."""
    return QIcon(str(APP_ICON_PATH))


def configure_application(application) -> None:
    """Apply the application name and icon to a ``QApplication`` instance."""
    application.setApplicationName(APPLICATION_NAME)
    application.setApplicationDisplayName(APPLICATION_NAME)
    application.setWindowIcon(get_app_icon())
