"""Dialog windows: DynamicInputDialog."""

from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTextEdit,
)


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

