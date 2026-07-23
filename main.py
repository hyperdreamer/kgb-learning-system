"""Entry point: KGB 5-Box SRS System."""

import argparse
import sys

from kgb_srs.config import SETTINGS_FILE, normalize_settings_path


def parse_args(argv=None):
    """Parse launcher options without importing the GUI."""
    parser = argparse.ArgumentParser(description="KGB 5-Box SRS System")
    parser.add_argument(
        "-c",
        "--config",
        type=normalize_settings_path,
        default=normalize_settings_path(SETTINGS_FILE),
        metavar="PATH",
        help="JSON settings file (default: %(default)s)",
    )
    return parser.parse_args(argv)


def run_application(settings_file):
    """Start the GUI using *settings_file* for all settings persistence."""
    from PyQt6.QtWidgets import QApplication
    from kgb_srs.app_icon import configure_application
    from kgb_srs.main_window import BarskyApp

    # argparse owns this command line; do not pass its options on to Qt.
    app = QApplication(sys.argv[:1])
    configure_application(app)
    window = BarskyApp(settings_file=settings_file)
    window.start_browser_capture_server()
    window.install_system_tray()
    window.show()
    return app.exec()


def main(argv=None):
    args = parse_args(argv)
    return run_application(args.config)


if __name__ == "__main__":
    sys.exit(main())
