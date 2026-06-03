"""Dialog windows: DynamicInputDialog, NewDatabaseDialog."""

import os

from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTextEdit,
    QMessageBox,
)

from .config import DIR_DB
from .db import make_db_path


class DynamicInputDialog(QDialog):
    """Resizable text input dialog for front/back content with Markdown/Math support."""

    def __init__(self, parent=None, title="Input Dialog", label_text="Enter text:", initial_text=""):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.text_value = None

        self.layout = QVBoxLayout(self)
        self.layout.addWidget(QLabel(label_text))

        self.text_edit = QTextEdit()
        self.text_edit.setPlainText(initial_text)
        self.text_edit.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        self.text_edit.setAcceptRichText(False)
        self.text_edit.setFontPointSize(QApplication.font().pointSize() + 2)
        self.text_edit.setToolTip(
            "Markdown and MathJax are supported during review display.\n"
            "Markdown examples: **bold**, *italic*, # heading, - lists, `code`, tables.\n"
            "Math examples: $x^2$, $$\\int_0^1 x dx$$, \\(a+b\\), \\[E=mc^2\\]."
        )

        if parent:
            self.max_w = max(800, int(parent.width() * 0.8))
            self.max_h = max(600, int(parent.height() * 0.8))
        else:
            self.max_w = 800
            self.max_h = 600

        self.min_w = 400
        self.min_h = 300

        self.text_edit.setMinimumWidth(self.min_w)
        self.text_edit.setMinimumHeight(self.min_h)
        self.layout.addWidget(self.text_edit)

        btn_layout = QHBoxLayout()
        ok_btn = QPushButton("OK")
        ok_btn.setStyleSheet("background-color: #ccffcc; font-weight: bold; padding: 10px;")
        ok_btn.clicked.connect(self.accept_input)

        cancel_btn = QPushButton("Cancel")
        cancel_btn.setStyleSheet("padding: 10px;")
        cancel_btn.clicked.connect(self.reject)

        btn_layout.addWidget(ok_btn)
        btn_layout.addWidget(cancel_btn)
        self.layout.addLayout(btn_layout)

        self.text_edit.document().documentLayout().documentSizeChanged.connect(self.adjust_size)

    def showEvent(self, event):
        super().showEvent(event)
        self.adjust_size()

    def adjust_size(self):
        try:
            self.text_edit.document().documentLayout().documentSizeChanged.disconnect(self.adjust_size)
        except Exception:
            pass

        fm = self.text_edit.fontMetrics()
        lines = self.text_edit.toPlainText().split('\n')
        max_line_w = max([fm.horizontalAdvance(line) for line in lines] + [0])

        new_w = max(self.min_w, min(max_line_w + 35, self.max_w))
        self.text_edit.setFixedWidth(new_w)

        doc = self.text_edit.document()
        doc.setTextWidth(self.text_edit.viewport().width())

        new_h = max(self.min_h, min(int(doc.size().height()) + 15, self.max_h))
        self.text_edit.setFixedHeight(new_h)

        self.layout.setSizeConstraint(QVBoxLayout.SizeConstraint.SetFixedSize)

        self.text_edit.document().documentLayout().documentSizeChanged.connect(self.adjust_size)

    def accept_input(self):
        self.text_value = self.text_edit.toPlainText().strip()
        self.accept()


class NewDatabaseDialog(QDialog):
    """Dialog to create a new database with optional subdirectory."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Create New Database")
        self.result_path = None
        self.result_display = None

        layout = QVBoxLayout(self)

        # Database name
        name_layout = QHBoxLayout()
        name_layout.addWidget(QLabel("Database Name:"))
        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText("e.g. English, Linear_Algebra, CN2EN")
        name_layout.addWidget(self.name_input)
        layout.addLayout(name_layout)

        # Subdirectory (optional)
        dir_layout = QHBoxLayout()
        dir_layout.addWidget(QLabel("Subdirectory:"))
        self.dir_input = QLineEdit()
        self.dir_input.setPlaceholderText("Optional, e.g. Languages, Math")
        dir_layout.addWidget(self.dir_input)
        layout.addLayout(dir_layout)

        # Preview
        self.preview_label = QLabel("")
        self.preview_label.setStyleSheet("color: #555; font-style: italic; padding: 4px;")
        layout.addWidget(self.preview_label)

        self.name_input.textChanged.connect(self.update_preview)
        self.dir_input.textChanged.connect(self.update_preview)

        # Buttons
        btn_layout = QHBoxLayout()
        self.create_btn = QPushButton("Create")
        self.create_btn.setStyleSheet(
            "background-color: #4CAF50; color: white; font-weight: bold; padding: 8px 16px;"
        )
        self.create_btn.clicked.connect(self.do_create)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.setStyleSheet("padding: 8px 16px;")
        cancel_btn.clicked.connect(self.reject)

        btn_layout.addWidget(self.create_btn)
        btn_layout.addWidget(cancel_btn)
        layout.addLayout(btn_layout)

        self.update_preview()

    def update_preview(self):
        name = self.name_input.text().strip()
        subdir = self.dir_input.text().strip()
        if not name:
            self.preview_label.setText("(enter a database name)")
            self.create_btn.setEnabled(False)
            return

        full_path = make_db_path(name, subdir)
        # Determine display path relative to DIR_DB
        rel_path = os.path.relpath(full_path, DIR_DB)
        # Strip the _barsky.db suffix for display
        display = rel_path[: -len("_barsky.db")] if rel_path.endswith("_barsky.db") else rel_path
        exists = os.path.exists(full_path)

        if exists:
            self.preview_label.setText(f"⚠ Already exists: {display}")
            self.create_btn.setEnabled(False)
        else:
            self.preview_label.setText(f"Will create: {display}")
            self.create_btn.setEnabled(True)

    def do_create(self):
        name = self.name_input.text().strip()
        subdir = self.dir_input.text().strip()
        if not name:
            return

        full_path = make_db_path(name, subdir)

        if os.path.exists(full_path):
            QMessageBox.warning(self, "Already Exists", f"Database already exists at:\n{full_path}")
            return

        # Create directories if needed
        os.makedirs(os.path.dirname(full_path), exist_ok=True)

        self.result_path = full_path
        # Build display name
        if subdir:
            safe_subdir = subdir.replace("\\", "/").strip("/")
            safe_name = name.replace(" ", "_").replace("/", "_").replace("\\", "_")
            self.result_display = f"{safe_subdir}/{safe_name}"
        else:
            self.result_display = name.replace(" ", "_").replace("/", "_").replace("\\", "_")
        self.accept()
