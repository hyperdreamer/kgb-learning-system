"""Entry point: KGB 5-Box SRS System.

Usage:
    python main.py
"""

import sys

from PyQt6.QtWidgets import QApplication

from kgb_srs.main_window import BarskyApp


def main():
    app = QApplication(sys.argv)
    window = BarskyApp()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
