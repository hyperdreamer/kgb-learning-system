"""Database creation dialog."""

import os

from PyQt6.QtWidgets import (
    QDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
)

from .catalog import DatabaseType


class DBCreationDialog(QDialog):
    """Dialog for creating a new database with category/subtype selection."""

    def __init__(self, parent=None, base_dir: str = ""):
        super().__init__(parent)
        self.setWindowTitle("Create New Database")
        self.setMinimumWidth(500)
        self._base_dir = base_dir
        self._selected_type: DatabaseType | None = None
        self._db_name = ""

        layout = QVBoxLayout(self)

        # Category / Subtype selection
        group = QGroupBox("Database Type")
        group_layout = QVBoxLayout(group)

        # Language-based group
        lang_label = QLabel("<b>Language-based</b>")
        group_layout.addWidget(lang_label)

        self._sentence_radio = QRadioButton("Sentence-based")
        self._sentence_radio.setToolTip(
            "Cards have a sentence with unfamiliar words/phrases. "
            "AI assigns senses in a shared catalog; a word/phrase "
            "dictionary is auto-created/linked as a projection."
        )
        group_layout.addWidget(self._sentence_radio)

        # Word/phrase DBs are projection-only and auto-linked from sentence
        # DBs — users cannot create orphan W/P databases from this dialog.

        group_layout.addSpacing(10)

        # Knowledge-based group
        know_label = QLabel("<b>Knowledge-based</b>")
        group_layout.addWidget(know_label)

        self._knowledge_radio = QRadioButton(
            "Knowledge-based (generic front/back)")
        self._knowledge_radio.setToolTip(
            "Traditional front/back cards. No language AI prompts."
        )
        group_layout.addWidget(self._knowledge_radio)

        self._sentence_radio.setChecked(True)  # default: authoring path
        layout.addWidget(group)

        # Database name
        name_layout = QHBoxLayout()
        name_layout.addWidget(QLabel("Name:"))
        self._name_edit = QLineEdit()
        self._name_edit.setPlaceholderText("e.g., French, Math_Topology")
        name_layout.addWidget(self._name_edit)
        layout.addLayout(name_layout)

        # Directory
        dir_layout = QHBoxLayout()
        dir_layout.addWidget(QLabel("Location:"))
        self._dir_label = QLabel("(auto)")
        self._dir_label.setStyleSheet("color: #888;")
        dir_layout.addWidget(self._dir_label, stretch=1)
        layout.addLayout(dir_layout)

        # Update the dir label when radio changes
        self._sentence_radio.toggled.connect(self._update_dir_label)
        self._knowledge_radio.toggled.connect(self._update_dir_label)
        self._update_dir_label()

        # Buttons
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        create_btn = QPushButton("Create")
        create_btn.setStyleSheet(
            "background-color: #43A047; color: white; "
            "font-weight: bold; padding: 8px 20px;"
        )
        create_btn.clicked.connect(self._on_create)
        btn_layout.addWidget(create_btn)

        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(cancel_btn)
        layout.addLayout(btn_layout)

    def _update_dir_label(self):
        if self._sentence_radio.isChecked():
            subdir = "Language-based/Sentence-based"
        else:
            subdir = "Knowledge-based"
        root = self._base_dir or "db"
        # Show a short path for the project default; otherwise the full root.
        from .config import DIR_DB
        if not self._base_dir or os.path.abspath(self._base_dir) == (
            os.path.abspath(DIR_DB)
        ):
            display_root = "db"
        else:
            display_root = root.rstrip("/\\")
        self._dir_label.setText(f"{display_root}/{subdir}/")

    def _on_create(self):
        from .schema import validate_db_name
        name = self._name_edit.text().strip()
        if not name:
            QMessageBox.warning(
                self, "Error", "Please enter a database name.")
            return

        if not validate_db_name(name):
            QMessageBox.warning(
                self, "Error",
                f"Invalid database name: {name!r}\n\n"
                "Names must not contain path separators (/ or \\), "
                "'..', control characters, or be absolute paths."
            )
            return

        if self._sentence_radio.isChecked():
            self._selected_type = DatabaseType.LANGUAGE_SENTENCE
        else:
            self._selected_type = DatabaseType.KNOWLEDGE

        self._db_name = name
        self.accept()

    @property
    def selected_type(self) -> DatabaseType | None:
        return self._selected_type

    @property
    def db_name(self) -> str:
        return self._db_name
