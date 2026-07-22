"""Dialog windows: DynamicInputDialog."""

from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTextEdit,
)


class DynamicInputDialog(QDialog):
    """Resizable text input dialog for front/back content with Markdown/Math support."""

    def __init__(
        self,
        parent=None,
        title="Input Dialog",
        label_text="Enter text:",
        initial_text="",
    ):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.text_value = None
        self.setMinimumSize(480, 340)

        layout = QVBoxLayout(self)

        label = QLabel(label_text)
        label.setWordWrap(True)
        layout.addWidget(label)

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
        layout.addWidget(self.text_edit, stretch=1)

        btn_layout = QHBoxLayout()
        ok_btn = QPushButton("OK")
        ok_btn.setStyleSheet(
            "background-color: #ccffcc; font-weight: bold; padding: 10px;"
        )
        ok_btn.clicked.connect(self.accept_input)

        cancel_btn = QPushButton("Cancel")
        cancel_btn.setStyleSheet("padding: 10px;")
        cancel_btn.clicked.connect(self.reject)

        btn_layout.addWidget(ok_btn)
        btn_layout.addWidget(cancel_btn)
        layout.addLayout(btn_layout)

        if parent:
            w = min(max(520, int(parent.width() * 0.55)), 900)
            h = min(max(380, int(parent.height() * 0.55)), 700)
        else:
            w = 600
            h = 420
        self.resize(w, h)

    def accept_input(self):
        self.text_value = self.text_edit.toPlainText().strip()
        self.accept()
