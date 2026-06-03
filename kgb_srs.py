import sys
import os
import sqlite3
import datetime
import random
import json
import asyncio
import tempfile
import uuid
import re
import html as html_lib

import edge_tts

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QComboBox, QPushButton, QGraphicsView, QGraphicsScene,
    QGraphicsRectItem, QMessageBox, QDialog, QTextEdit, QTableWidget,
    QTableWidgetItem, QHeaderView, QSpinBox, QFormLayout, QAbstractItemView,
    QGraphicsProxyWidget, QGraphicsTextItem, QCheckBox
)
from PyQt6.QtCore import Qt, QTimer, QThread, pyqtSignal, QUrl
from PyQt6.QtGui import QFont, QFontDatabase, QColor, QBrush, QPen, QPainter, QTextDocument
from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput

try:
    from PyQt6.QtWebEngineWidgets import QWebEngineView
    try:
        from PyQt6.QtWebEngineCore import QWebEngineSettings
    except ImportError:
        QWebEngineSettings = None
    HAS_WEBENGINE = True
except ImportError:
    QWebEngineView = None
    QWebEngineSettings = None
    HAS_WEBENGINE = False

# --- Database Root Directory ---
DIR_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "db")


# --- Markdown + MathJax Rendering Helpers ---
# Important fixes:
# 1. Math placeholders use only letters/numbers so Markdown cannot mangle them.
# 2. QWebEngine local pages are allowed to load remote MathJax CDN resources.
# 3. MathJax is explicitly asked to typeset after page load.
MATH_TOKEN_PREFIX = "BARSKYMATHPLACEHOLDER"


def _protect_math_segments(text):
    """
    Temporarily replaces MathJax/LaTeX math segments before Markdown parsing so
    Markdown emphasis/list rules do not alter math expressions.

    Supports:
      $inline$
      $$display$$
      \\(inline\\)
      \\[display\\]
    """
    text = text or ""
    token_map = {}

    def make_token():
        # Only letters/numbers: no underscores, hyphens, @, etc.
        return f"{MATH_TOKEN_PREFIX}{len(token_map)}TOKEN"

    def replace_pattern(pattern, source):
        def repl(match):
            token = make_token()
            token_map[token] = match.group(0)
            return token

        return re.sub(pattern, repl, source, flags=re.DOTALL)

    # Protect display math first, then inline math.
    text = replace_pattern(r"(?<!\\)\$\$(.*?)(?<!\\)\$\$", text)
    text = replace_pattern(r"\\\[(.*?)\\\]", text)
    text = replace_pattern(r"\\\((.*?)\\\)", text)
    text = replace_pattern(r"(?<!\\)\$(?!\$)(?:\\.|[^\n$\\])+(?<!\\)\$", text)

    return text, token_map


def _restore_math_segments(rendered_html, token_map):
    """
    Restores protected math segments into rendered HTML.

    The math text is HTML-escaped, but delimiters/backslashes remain visible in
    the DOM text, so MathJax can process them safely.
    """
    for token, math_text in token_map.items():
        rendered_html = rendered_html.replace(token, html_lib.escape(math_text, quote=False))
    return rendered_html


def _extract_body_fragment(full_html):
    match = re.search(r"<body[^>]*>(.*?)</body>", full_html, flags=re.IGNORECASE | re.DOTALL)
    return match.group(1) if match else full_html


def _set_qtextdocument_markdown(doc, markdown_text):
    """
    Uses Qt's Markdown support. GitHub dialect enables tables/task-list style
    behavior when available.
    """
    try:
        features = QTextDocument.MarkdownFeature.MarkdownDialectGitHub
        doc.setMarkdown(markdown_text, features)
    except Exception:
        try:
            doc.setMarkdown(markdown_text)
        except Exception:
            doc.setPlainText(markdown_text)


def markdown_to_html_fragment(markdown_text):
    """
    Converts Markdown to an HTML fragment while preserving MathJax delimiters.
    """
    markdown_text = (markdown_text or "").replace("\r\n", "\n").replace("\r", "\n")
    protected_text, token_map = _protect_math_segments(markdown_text)

    doc = QTextDocument()
    _set_qtextdocument_markdown(doc, protected_text)

    fragment = _extract_body_fragment(doc.toHtml())
    fragment = _restore_math_segments(fragment, token_map)
    return fragment


def _strip_math_delimiters(math_text):
    text = math_text.strip()

    if text.startswith("$$") and text.endswith("$$"):
        return text[2:-2].strip()
    if text.startswith(r"\[") and text.endswith(r"\]"):
        return text[2:-2].strip()
    if text.startswith(r"\(") and text.endswith(r"\)"):
        return text[2:-2].strip()
    if text.startswith("$") and text.endswith("$"):
        return text[1:-1].strip()

    return text


def markdown_to_plain_text(markdown_text):
    """
    Converts Markdown to plain text for TTS so TTS does not read Markdown marks
    such as **bold**, # heading, etc.
    """
    markdown_text = markdown_text or ""
    protected_text, token_map = _protect_math_segments(markdown_text)

    doc = QTextDocument()
    _set_qtextdocument_markdown(doc, protected_text)
    plain = doc.toPlainText()

    for token, math_text in token_map.items():
        plain = plain.replace(token, _strip_math_delimiters(math_text))

    plain = re.sub(r"\s+", " ", plain).strip()
    return plain


def build_review_html(markdown_text, font_family, font_size, include_mathjax=True):
    """
    Builds the complete HTML document used by the review display.

    Markdown is rendered first, while math is preserved for MathJax.
    """
    html_body = markdown_to_html_fragment(markdown_text)
    safe_font = str(font_family).replace("\\", "\\\\").replace("'", "\\'")

    mathjax_script = ""
    mathjax_typeset_script = ""

    if include_mathjax:
        mathjax_script = """
<script>
window.MathJax = {
  tex: {
    inlineMath: [['$', '$'], ['\\\\(', '\\\\)']],
    displayMath: [['$$', '$$'], ['\\\\[', '\\\\]']],
    processEscapes: true
  },
  options: {
    skipHtmlTags: ['script', 'noscript', 'style', 'textarea', 'pre', 'code']
  },
  startup: {
    typeset: true
  }
};
</script>
<script id="MathJax-script" async
        src="https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-chtml.js"></script>
"""
        mathjax_typeset_script = """
<script>
(function waitForMathJaxAndTypeset() {
  if (window.MathJax && window.MathJax.typesetPromise) {
    window.MathJax.typesetPromise();
  } else {
    window.setTimeout(waitForMathJaxAndTypeset, 100);
  }
})();
</script>
"""

    return f"""
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
{mathjax_script}
<style>
  html, body {{
    margin: 0;
    padding: 0;
    background-color: transparent;
  }}

  body {{
    font-family: '{safe_font}', Arial, sans-serif;
    font-size: {font_size}px;
    color: #222;
    display: flex;
    justify-content: center;
    align-items: center;
    min-height: 100vh;
    box-sizing: border-box;
    overflow: auto;
  }}

  .content-wrapper {{
    text-align: left;
    display: inline-block;
    max-width: 100%;
    width: auto;
    padding: 20px;
    box-sizing: border-box;
    overflow-wrap: anywhere;
    word-wrap: break-word;
  }}

  .content-wrapper > *:first-child {{
    margin-top: 0;
  }}

  .content-wrapper > *:last-child {{
    margin-bottom: 0;
  }}

  p {{
    margin: 0.45em 0;
  }}

  h1, h2, h3, h4, h5, h6 {{
    margin: 0.65em 0 0.35em;
    line-height: 1.2;
  }}

  ul, ol {{
    margin: 0.45em 0 0.45em 1.3em;
    padding-left: 1.2em;
  }}

  li {{
    margin: 0.2em 0;
  }}

  blockquote {{
    border-left: 4px solid #bdbdbd;
    margin: 0.7em 0;
    padding: 0.2em 0 0.2em 0.8em;
    color: #555;
    background: rgba(0, 0, 0, 0.03);
  }}

  hr {{
    width: 100%;
    border: 0;
    border-top: 2px solid #cccccc;
    margin: 1em 0;
  }}

  pre {{
    background: #f6f8fa;
    border: 1px solid #dddddd;
    border-radius: 6px;
    padding: 10px;
    overflow-x: auto;
    white-space: pre-wrap;
  }}

  code {{
    background: #f3f3f3;
    border-radius: 4px;
    padding: 2px 4px;
    font-family: Consolas, Menlo, Monaco, monospace;
  }}

  pre code {{
    background: transparent;
    padding: 0;
  }}

  table {{
    border-collapse: collapse;
    margin: 0.7em 0;
    max-width: 100%;
  }}

  th, td {{
    border: 1px solid #cccccc;
    padding: 5px 8px;
  }}

  th {{
    background: #eeeeee;
    font-weight: bold;
  }}

  img {{
    max-width: 100%;
    height: auto;
  }}

  a {{
    color: #1565c0;
  }}
</style>
</head>
<body>
  <div class="content-wrapper">
    {html_body}
  </div>
  {mathjax_typeset_script}
</body>
</html>
"""


# --- Database Setup ---
def init_db(db_path):
    """Initialize or open a database at the given path."""
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS cards
                 (id INTEGER PRIMARY KEY, front TEXT, back TEXT, 
                 box INTEGER, next_review DATE)''')

    c.execute('''CREATE TABLE IF NOT EXISTS settings
                 (key TEXT PRIMARY KEY, value TEXT)''')

    c.execute('''INSERT OR IGNORE INTO settings (key, value) VALUES ('random_review', '1')''')

    conn.commit()
    return conn


# --- TTS Worker Thread ---
class TTSWorker(QThread):
    finished = pyqtSignal(str)
    error = pyqtSignal(str)

    def __init__(self, text, voice):
        super().__init__()
        self.text = text
        self.voice = voice

    async def generate_audio(self):
        unique_name = f"barsky_tts_{uuid.uuid4().hex[:8]}.mp3"
        temp_file = os.path.join(tempfile.gettempdir(), unique_name)

        communicate = edge_tts.Communicate(self.text, self.voice)
        await communicate.save(temp_file)
        return temp_file

    def run(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            file_path = loop.run_until_complete(self.generate_audio())
            self.finished.emit(file_path)
        except Exception as e:
            self.error.emit(str(e))
        finally:
            loop.close()


# --- Custom Dialog for Resizable Text (Front and Back) ---
class DynamicInputDialog(QDialog):
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


# --- Interactive Drop Zones/Buttons ---
class DropZoneItem(QGraphicsRectItem):
    def __init__(self, x, y, w, h, pen, brush, text_html, is_correct, app_ref):
        super().__init__()

        self.is_correct = is_correct
        self.app_ref = app_ref

        self.setAcceptHoverEvents(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        self.text_item = QGraphicsTextItem(self)
        self.text_item.setHtml(text_html)
        self.text_item.setTextWidth(w)

        text_rect = self.text_item.boundingRect()
        actual_h = max(h, text_rect.height() + 20)

        bottom_y = y + h
        adjusted_y = bottom_y - actual_h

        self.setRect(0, 0, w, actual_h)
        self.setPos(x, adjusted_y)
        self.setPen(pen)
        self.setBrush(brush)

        text_y = (actual_h - text_rect.height()) / 2
        self.text_item.setPos(0, text_y)

    def mousePressEvent(self, event):
        if self.app_ref.current_card:
            self.app_ref.process_answer(self.is_correct)
        super().mousePressEvent(event)


# --- Custom Graphics Item for the Card ---
class FlashCardItem(QGraphicsRectItem):
    def __init__(self, app_ref, cx, cy, cw, ch):
        super().__init__(-cw / 2, -ch / 2, cw, ch)
        self.app_ref = app_ref
        self.speech_text = ""
        self.setPos(cx, cy)

        self.setFlag(QGraphicsRectItem.GraphicsItemFlag.ItemIsMovable)
        self.setBrush(QBrush(QColor("white")))
        self.setPen(QPen(QColor("black"), 2))

        self.proxy = QGraphicsProxyWidget(self)
        self.container = QWidget()
        self.container.setStyleSheet("background-color: transparent;")

        self.layout = QVBoxLayout(self.container)
        self.layout.setContentsMargins(0, 0, 0, 0)

        if HAS_WEBENGINE:
            self.text_widget = QWebEngineView()
            self.text_widget.setStyleSheet("background-color: transparent;")

            # Critical for MathJax CDN when using a local base URL for relative images.
            try:
                if QWebEngineSettings is not None:
                    web_settings = self.text_widget.settings()
                    web_settings.setAttribute(
                        QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls,
                        True
                    )
                    web_settings.setAttribute(
                        QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls,
                        True
                    )
            except Exception:
                pass

            try:
                self.text_widget.page().setBackgroundColor(QColor("transparent"))
            except Exception:
                pass
        else:
            self.text_widget = QTextEdit()
            self.text_widget.setReadOnly(True)
            self.text_widget.setStyleSheet("background-color: transparent; border: none; color: black;")
            self.text_widget.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)

        self.btn_layout = QHBoxLayout()

        font_fam = self.app_ref.settings.get("font_family", "Arial")
        font_sz = self.app_ref.settings.get("font_size", 14)
        dyn_pad = max(10, int(font_sz * 0.6))

        self.tts_btn = QPushButton("🔊 Listen")
        self.tts_btn.setStyleSheet(
            f"background-color: #9C27B0; color: white; padding: {dyn_pad}px; "
            f"font-family: '{font_fam}'; font-size: {font_sz}px; "
            f"font-weight: bold; border-radius: 5px;"
        )
        self.tts_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.tts_btn.clicked.connect(self.trigger_tts)

        self.flip_btn = QPushButton("💡 Reveal Answer")
        self.flip_btn.setStyleSheet(
            f"background-color: #2196F3; color: white; padding: {dyn_pad}px; "
            f"font-family: '{font_fam}'; font-size: {font_sz}px; "
            f"font-weight: bold; border-radius: 5px;"
        )
        self.flip_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.flip_btn.clicked.connect(self.app_ref.flip_card)

        self.btn_layout.addWidget(self.tts_btn)
        self.btn_layout.addWidget(self.flip_btn)

        self.layout.addWidget(self.text_widget)
        self.layout.addLayout(self.btn_layout)

        self.top_margin = 35
        self.side_margin = 15
        self.bottom_margin = 15

        widget_w = int(cw - (self.side_margin * 2))
        widget_h = int(ch - self.top_margin - self.bottom_margin)
        self.container.setFixedSize(widget_w, widget_h)

        self.proxy.setWidget(self.container)
        self.proxy.setPos(-cw / 2 + self.side_margin, -ch / 2 + self.top_margin)

    def paint(self, painter, option, widget):
        super().paint(painter, option, widget)

        painter.save()
        painter.setPen(QPen(QColor("#bbbbbb"), 3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        grip_w = 40
        grip_x = -grip_w / 2
        grip_y = -self.rect().height() / 2 + 10

        for i in range(3):
            y = grip_y + (i * 6)
            painter.drawLine(int(grip_x), int(y), int(grip_x + grip_w), int(y))
        painter.restore()

    def trigger_tts(self):
        if self.speech_text:
            self.app_ref.speak_text(self.speech_text, self.tts_btn)

    def set_text(self, display_text, is_flipped, text_to_speak=""):
        if text_to_speak:
            self.speech_text = text_to_speak

        font_fam = self.app_ref.settings.get("font_family", "Arial")
        font_sz = self.app_ref.settings.get("font_size", 14)

        html_template = build_review_html(
            display_text,
            font_family=font_fam,
            font_size=font_sz + 4,
            include_mathjax=HAS_WEBENGINE
        )

        if HAS_WEBENGINE:
            # Local base URL lets Markdown images like ![](image.png) resolve,
            # while settings above allow the remote MathJax CDN to load.
            base_url = QUrl.fromLocalFile(os.getcwd() + os.sep)
            self.text_widget.setHtml(html_template, base_url)
        else:
            self.text_widget.setHtml(html_template)

        if is_flipped:
            self.flip_btn.hide()
        else:
            self.flip_btn.show()

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        self.app_ref.check_card_drop(self)


# --- New Database Dialog ---
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

        # Update preview on text changes
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

        # Sanitize: replace spaces/slashes with underscores
        safe_name = name.replace(" ", "_").replace("/", "_").replace("\\", "_")
        db_filename = f"{safe_name}_barsky.db"

        if subdir:
            safe_subdir = subdir.replace("\\", "/").strip("/")
            rel = f"{safe_subdir}/{db_filename}"
        else:
            rel = db_filename
            safe_subdir = ""

        full_path = os.path.join(DIR_DB, safe_subdir, db_filename) if safe_subdir else os.path.join(DIR_DB, db_filename)
        exists = os.path.exists(full_path)

        if exists:
            self.preview_label.setText(f"⚠ Already exists: {rel}")
            self.create_btn.setEnabled(False)
        else:
            self.preview_label.setText(f"Will create: {rel}")
            self.create_btn.setEnabled(True)

    def do_create(self):
        name = self.name_input.text().strip()
        subdir = self.dir_input.text().strip()
        if not name:
            return

        safe_name = name.replace(" ", "_").replace("/", "_").replace("\\", "_")
        db_filename = f"{safe_name}_barsky.db"

        if subdir:
            safe_subdir = subdir.replace("\\", "/").strip("/")
            target_dir = os.path.join(DIR_DB, safe_subdir)
        else:
            safe_subdir = ""
            target_dir = DIR_DB

        full_path = os.path.join(target_dir, db_filename)

        if os.path.exists(full_path):
            QMessageBox.warning(self, "Already Exists", f"Database already exists at:\n{full_path}")
            return

        # Create directories if needed
        os.makedirs(target_dir, exist_ok=True)

        self.result_path = full_path
        if safe_subdir:
            self.result_display = f"{safe_subdir}/{safe_name}"
        else:
            self.result_display = safe_name
        self.accept()


# --- Main Application Window ---
class BarskyApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("KGB 5-Box SRS System")

        script_dir = os.path.dirname(os.path.abspath(__file__))
        self.settings_file = os.path.join(script_dir, "barsky_settings.json")
        self.settings = {
            "width": 900,
            "height": 700,
            "font_family": "Arial",
            "font_size": 14,
            "default_database": "",
            "tts_voice": "en-US-AvaMultilingualNeural"
        }
        self.load_settings()

        self.resize(self.settings["width"], self.settings["height"])

        self.current_lang = None
        self.conn = None
        self.current_db_path = None
        self.current_card = None
        self.cards_due = []
        self.is_current_flipped = False
        self.review_mode = ''

        self.tts_worker = None

        self.player = QMediaPlayer()
        self.audio_output = QAudioOutput()
        self.player.setAudioOutput(self.audio_output)

        self.setup_ui()
        self.apply_font_settings()

        # Auto-load the default database if set
        default_db = self.settings.get("default_database", "")
        if default_db:
            # Try to find it in the current listing
            for display, path in self.find_databases():
                if path == default_db and os.path.exists(default_db):
                    self.current_db_path = default_db
                    self.current_lang = display
                    self.db_btn.setText(f"📂 {display}")
                    self.load_database(silent=True)
                    break

    def load_settings(self):
        if os.path.exists(self.settings_file):
            try:
                with open(self.settings_file, "r", encoding="utf-8") as f:
                    self.settings.update(json.load(f))
            except Exception as e:
                print(f"Error loading settings: {e}")

    def save_settings(self):
        try:
            with open(self.settings_file, "w", encoding="utf-8") as f:
                json.dump(self.settings, f, indent=2)
        except Exception as e:
            print(f"Error saving settings: {e}")

    def closeEvent(self, event):
        self.settings["width"] = self.width()
        self.settings["height"] = self.height()
        self.save_settings()
        event.accept()

    def apply_font_settings(self):
        font_family = self.settings.get("font_family", "Arial")
        font_size = self.settings.get("font_size", 14)

        font = QFont(font_family, font_size)
        QApplication.setFont(font)

        dyn_padding = max(10, int(font_size * 0.8))

        if hasattr(self, 'start_btn') and self.start_btn is not None:
            self.start_btn.setStyleSheet(
                f"background-color: #4CAF50; color: white; border-radius: 5px; "
                f"padding: {dyn_padding}px; font-family: '{font_family}'; "
                f"font-size: {font_size + 2}px; font-weight: bold;"
            )

        if hasattr(self, 'force_seq_btn') and self.force_seq_btn is not None:
            self.force_seq_btn.setStyleSheet(
                f"background-color: #FF9800; color: white; border-radius: 5px; "
                f"padding: {dyn_padding}px; font-family: '{font_family}'; "
                f"font-size: {font_size + 2}px; font-weight: bold;"
            )

        if hasattr(self, 'restart_review_btn') and self.restart_review_btn is not None:
            self.restart_review_btn.setStyleSheet(
                f"background-color: #1E88E5; color: white; border-radius: 5px; "
                f"padding: {dyn_padding}px; font-family: '{font_family}'; "
                f"font-size: {font_size + 2}px; font-weight: bold;"
            )

        if hasattr(self, 'force_rev_btn') and self.force_rev_btn is not None:
            self.force_rev_btn.setStyleSheet(
                f"background-color: #F4511E; color: white; border-radius: 5px; "
                f"padding: {dyn_padding}px; font-family: '{font_family}'; "
                f"font-size: {font_size + 2}px; font-weight: bold;"
            )

    @staticmethod
    def find_databases():
        """Recursively find all _barsky.db files under DIR_DB.
        Returns list of (display_name, full_path) sorted by display name."""
        results = []
        if not os.path.isdir(DIR_DB):
            return results
        for root, dirs, files in os.walk(DIR_DB):
            for f in files:
                if f.endswith("_barsky.db"):
                    full_path = os.path.join(root, f)
                    db_name = f[:-len("_barsky.db")]
                    rel_dir = os.path.relpath(root, DIR_DB)
                    if rel_dir == ".":
                        display = db_name
                    else:
                        display = os.path.join(rel_dir, db_name)
                    results.append((display, full_path))
        results.sort(key=lambda x: x[0].lower())
        return results

    def build_db_menu(self, parent_menu=None):
        """Build a hierarchical QMenu from the database directory structure."""
        from PyQt6.QtWidgets import QMenu
        if parent_menu is None:
            parent_menu = QMenu(self)
        
        dbs = self.find_databases()
        if not dbs:
            no_action = parent_menu.addAction("(no databases found)")
            no_action.setEnabled(False)
            return parent_menu

        # Build a tree: {part: {subtree | leaf_path}}
        tree = {}
        for display, full_path in dbs:
            parts = display.replace("\\", "/").split("/")
            node = tree
            for part in parts[:-1]:
                if part not in node:
                    node[part] = {}
                node = node[part]
            node[parts[-1]] = full_path  # leaf

        def populate_menu(menu, subtree):
            # Sort: submenus first, then leaves
            items = sorted(subtree.items(), key=lambda kv: (not isinstance(kv[1], dict), kv[0].lower()))
            for name, value in items:
                if isinstance(value, dict):
                    # Subdirectory
                    sub = QMenu(name, menu)
                    populate_menu(sub, value)
                    menu.addMenu(sub)
                else:
                    # Leaf database
                    action = menu.addAction(name)
                    action.setData(value)  # full path
            return menu

        return populate_menu(parent_menu, tree)

    def setup_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)

        top_frame = QWidget()
        top_layout = QHBoxLayout(top_frame)
        top_layout.setContentsMargins(0, 0, 0, 0)

        top_layout.addWidget(QLabel("Database:"))

        self.db_btn = QPushButton("📂 Select Database")
        self.db_btn.setStyleSheet(
            "text-align: left; padding: 6px 12px; font-weight: bold;"
        )
        self.db_btn.clicked.connect(self.show_db_menu)
        top_layout.addWidget(self.db_btn)

        self.new_db_btn = QPushButton("＋ New Database")
        self.new_db_btn.setStyleSheet(
            "background-color: #4CAF50; color: white; padding: 6px 12px; "
            "font-weight: bold; border-radius: 4px;"
        )
        self.new_db_btn.clicked.connect(self.create_new_database)
        top_layout.addWidget(self.new_db_btn)

        self.random_checkbox = QCheckBox("Review Randomly")
        self.random_checkbox.setEnabled(False)
        self.random_checkbox.stateChanged.connect(self.on_random_toggled)
        self.random_checkbox.setToolTip("If unchecked, cards are reviewed in the order they were added.")
        top_layout.addWidget(self.random_checkbox)

        top_layout.addStretch()

        add_btn = QPushButton("Add Word")
        add_btn.clicked.connect(self.add_word)
        browse_btn = QPushButton("Browse and Edit")
        browse_btn.clicked.connect(self.browse_cards)
        settings_btn = QPushButton("Settings")
        settings_btn.clicked.connect(self.open_settings_window)

        top_layout.addWidget(add_btn)
        top_layout.addWidget(browse_btn)
        top_layout.addWidget(settings_btn)
        main_layout.addWidget(top_frame)

        self.scene = QGraphicsScene()
        self.view = QGraphicsView(self.scene)
        self.view.setStyleSheet("background-color: #cfcfcf;")
        self.view.setRenderHint(QPainter.RenderHint.Antialiasing)
        main_layout.addWidget(self.view)

        self.start_btn = QPushButton("Start Daily Review")
        self.start_btn.clicked.connect(self.start_review)
        main_layout.addWidget(self.start_btn)

        forced_review_layout = QHBoxLayout()

        self.force_seq_btn = QPushButton("Next Item")
        self.force_seq_btn.clicked.connect(lambda: self.start_forced_review(direction='ASC'))

        self.restart_review_btn = QPushButton("Restart Current Review (1st Item)")
        self.restart_review_btn.clicked.connect(self.restart_current_review)

        self.force_rev_btn = QPushButton("Previous Item")
        self.force_rev_btn.clicked.connect(lambda: self.start_forced_review(direction='DESC'))

        forced_review_layout.addWidget(self.force_seq_btn)
        forced_review_layout.addWidget(self.restart_review_btn)
        forced_review_layout.addWidget(self.force_rev_btn)
        main_layout.addLayout(forced_review_layout)

        self.card_ui = None
        self.incorrect_zone = None
        self.correct_zone = None

    def on_random_toggled(self, state):
        if not self.conn:
            return

        is_random = self.random_checkbox.isChecked()
        c = self.conn.cursor()
        c.execute("UPDATE settings SET value = ? WHERE key = 'random_review'", ('1' if is_random else '0',))
        self.conn.commit()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.redraw_canvas()

    def redraw_canvas(self):
        self.scene.clear()
        self.card_ui = None

        self.scene.setSceneRect(0, 0, self.view.width() - 5, self.view.height() - 5)
        w = self.scene.width()
        h = self.scene.height()

        zone_y = h - 100
        zone_h = 80
        zone_w = max(260, w * 0.3)
        margin = 50

        self.incorrect_zone = DropZoneItem(
            margin, zone_y, zone_w, zone_h,
            QPen(QColor("red")), QBrush(QColor("#ffcccc")),
            "<div align='center'><b>Click or Drop Here</b><br>if <span style='color:red;'>INCORRECT</span><br>(Drops to Box 1 or 3)</div>",
            False, self
        )
        self.scene.addItem(self.incorrect_zone)

        self.correct_zone = DropZoneItem(
            w - margin - zone_w, zone_y, zone_w, zone_h,
            QPen(QColor("green")), QBrush(QColor("#ccffcc")),
            "<div align='center'><b>Click or Drop Here</b><br>if <span style='color:green;'>CORRECT</span><br>(Advances 1 Box)</div>",
            True, self
        )
        self.scene.addItem(self.correct_zone)

        if self.current_card:
            self.draw_card_ui()

    def speak_text(self, text, btn):
        btn.setEnabled(False)
        btn.setText("⏳ Preparing...")

        voice = self.settings.get("tts_voice", "en-US-AvaMultilingualNeural")
        self.tts_worker = TTSWorker(text, voice)

        def on_finished(file_path):
            self.player.setSource(QUrl.fromLocalFile(file_path))
            self.player.play()
            btn.setEnabled(True)
            btn.setText("🔊 Listen")

        def on_error(err):
            QMessageBox.warning(self, "TTS Error", f"Audio Error: {err}")
            btn.setEnabled(True)
            btn.setText("🔊 Listen")

        self.tts_worker.finished.connect(on_finished)
        self.tts_worker.error.connect(on_error)
        self.tts_worker.start()

    def show_db_menu(self):
        """Show the hierarchical database selection menu below the button."""
        from PyQt6.QtWidgets import QMenu
        menu = QMenu(self)
        self.build_db_menu(menu)
        
        # Connect all actions
        def connect_menu(m):
            for action in m.actions():
                if action.menu():
                    connect_menu(action.menu())
                elif action.data():
                    action.triggered.connect(
                        lambda checked, a=action: self.select_database(a)
                    )
        connect_menu(menu)
        
        # Show below the button
        pos = self.db_btn.mapToGlobal(self.db_btn.rect().bottomLeft())
        menu.exec(pos)

    def select_database(self, action):
        """Called when a database is selected from the menu."""
        db_path = action.data()
        if not db_path:
            return
        # Find the display name from our database listing
        for display, path in self.find_databases():
            if path == db_path:
                self.current_db_path = db_path
                self.current_lang = display
                self.db_btn.setText(f"📂 {display}")
                self.load_database(silent=False)
                return
        # Shouldn't happen, but fallback
        self.current_db_path = db_path
        self.current_lang = os.path.basename(db_path).replace("_barsky.db", "")
        self.db_btn.setText(f"📂 {self.current_lang}")
        self.load_database(silent=False)

    def create_new_database(self):
        """Open dialog to create a new database."""
        dialog = NewDatabaseDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            new_path = dialog.result_path
            if new_path:
                # Initialize the database file
                conn = init_db(new_path)
                conn.close()
                # Select it
                display = dialog.result_display
                self.current_db_path = new_path
                self.current_lang = display
                self.db_btn.setText(f"📂 {display}")
                self.load_database(silent=False)

    def load_database(self, silent=False):
        """Load a database from the current path."""
        if not self.current_db_path:
            if not silent:
                QMessageBox.warning(self, "Error", "Please select a database first.")
            return

        if not os.path.exists(self.current_db_path):
            if not silent:
                QMessageBox.warning(self, "Error", f"Database file not found:\n{self.current_db_path}")
            return

        if self.conn:
            self.conn.close()

        self.conn = init_db(self.current_db_path)

        c = self.conn.cursor()
        c.execute("SELECT value FROM settings WHERE key = 'random_review'")
        res = c.fetchone()

        is_random = True
        if res:
            is_random = (res[0] == '1')

        self.random_checkbox.blockSignals(True)
        self.random_checkbox.setChecked(is_random)
        self.random_checkbox.setEnabled(True)
        self.random_checkbox.blockSignals(False)

        self.current_card = None
        self.cards_due = []
        self.review_mode = ''

        self.randomize_box_five()

        if not silent:
            QMessageBox.information(self, "Success", f"Loaded database: {self.current_lang}")
            if "Math" in self.current_lang or "LaTeX" in self.current_lang:
                if not HAS_WEBENGINE:
                    QMessageBox.warning(
                        self,
                        "Notice",
                        "For Markdown + MathJax rendering, install PyQt6-WebEngine:\n\npip install PyQt6-WebEngine"
                    )

        self.scene.clear()
        self.redraw_canvas()

    def randomize_box_five(self):
        c = self.conn.cursor()
        c.execute("SELECT id FROM cards WHERE box = 5")
        mastered_cards = c.fetchall()
        if mastered_cards and random.random() < 0.05:
            target = random.choice(mastered_cards)[0]
            today_str = datetime.date.today().isoformat()
            c.execute("UPDATE cards SET box = 1, next_review = ? WHERE id = ?", (today_str, target))
            self.conn.commit()

    def add_word(self):
        if not self.conn:
            QMessageBox.warning(self, "Error", "Load a language database first.")
            return

        front_dialog = DynamicInputDialog(
            self,
            "Add New Word/Phrase",
            "Enter the word/phrase (Front). Markdown and MathJax are supported during review:"
        )
        if front_dialog.exec() != QDialog.DialogCode.Accepted or not front_dialog.text_value:
            return

        front = front_dialog.text_value
        c = self.conn.cursor()
        c.execute("SELECT id, front, back, box FROM cards WHERE front = ? COLLATE NOCASE", (front,))
        existing_card = c.fetchone()

        today_str = datetime.date.today().isoformat()

        if existing_card:
            card_id, ex_front, ex_back, ex_box = existing_card
            msg = f"'{ex_front}' is already in your database (Box {ex_box}).\n\nOpening Edit window. Card will reset to Box 1."
            QMessageBox.information(self, "Already Exists", msg)

            edit_front_dialog = DynamicInputDialog(self, "Edit Word", "Front:", ex_front)
            if edit_front_dialog.exec() != QDialog.DialogCode.Accepted or not edit_front_dialog.text_value:
                return
            new_front = edit_front_dialog.text_value

            dialog = DynamicInputDialog(
                self,
                "Edit Translation",
                "Enter the translation, meanings, or sample sentences. Markdown and MathJax are supported during review:",
                ex_back
            )
            if dialog.exec() == QDialog.DialogCode.Accepted and dialog.text_value:
                c.execute(
                    "UPDATE cards SET front=?, back=?, box=1, next_review=? WHERE id=?",
                    (new_front, dialog.text_value, today_str, card_id)
                )
                self.conn.commit()
                QMessageBox.information(self, "Updated", "Card updated and moved to Box 1.")

                if self.current_card and str(self.current_card[0]) == str(card_id):
                    self.current_card = (card_id, new_front, dialog.text_value, 1)
                    self.is_current_flipped = False
                    if self.card_ui:
                        self.scene.removeItem(self.card_ui)
                        self.card_ui = None
                    self.draw_card_ui()
                else:
                    if self.current_card is not None:
                        self.cards_due = [cf for cf in self.cards_due if cf[0] != card_id]
                        self.cards_due.insert(0, self.current_card)
                        self.current_card = (card_id, new_front, dialog.text_value, 1)
                        self.is_current_flipped = False

                        if self.card_ui:
                            self.scene.removeItem(self.card_ui)
                            self.card_ui = None
                        self.draw_card_ui()
        else:
            dialog = DynamicInputDialog(
                self,
                "Add Translation",
                "Enter the translation, meanings, or sample sentences. Markdown and MathJax are supported during review:"
            )
            if dialog.exec() == QDialog.DialogCode.Accepted and dialog.text_value:
                c.execute(
                    "INSERT INTO cards (front, back, box, next_review) VALUES (?, ?, 1, ?)",
                    (front, dialog.text_value, today_str)
                )
                card_id = c.lastrowid
                self.conn.commit()
                QMessageBox.information(self, "Added", "Word added to Box 1.")

                if self.current_card is not None:
                    self.cards_due.insert(0, self.current_card)
                    self.current_card = (card_id, front, dialog.text_value, 1)
                    self.is_current_flipped = False

                    if self.card_ui:
                        self.scene.removeItem(self.card_ui)
                        self.card_ui = None
                    self.draw_card_ui()

    def browse_cards(self):
        if not self.conn:
            return

        dialog = QDialog(self)
        dialog.setWindowTitle(f"Browse Cards: {self.current_lang}")
        dialog.resize(800, 600)
        layout = QVBoxLayout(dialog)

        filter_layout = QHBoxLayout()
        filter_label = QLabel("Filter:")
        filter_input = QLineEdit()
        filter_input.setPlaceholderText("Search keywords (use ' AND ' / ' OR ' for multiple terms, e.g. 'math AND theorem')")
        filter_layout.addWidget(filter_label)
        filter_layout.addWidget(filter_input)
        layout.addLayout(filter_layout)

        table = QTableWidget()
        table.setColumnCount(4)
        table.setHorizontalHeaderLabels(["ID", "Front (Word/Phrase)", "Box", "Next Review Date"])

        table.verticalHeader().setVisible(False)

        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)

        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        layout.addWidget(table)

        def refresh_list():
            table.setRowCount(0)
            filter_text = filter_input.text().strip()

            c = self.conn.cursor()
            c.execute("SELECT id, front, back, box, next_review FROM cards")

            for row_data in c.fetchall():
                card_id, front, back, box, next_review = row_data

                if filter_text:
                    search_content = f"{front}\n{back}".lower()

                    or_parts = re.split(r'\s+OR\s+', filter_text, flags=re.IGNORECASE)
                    matched_any_or = False

                    for or_part in or_parts:
                        and_parts = re.split(r'\s+AND\s+', or_part, flags=re.IGNORECASE)
                        matched_all_and = True

                        for and_part in and_parts:
                            kw = and_part.strip().lower()
                            if kw and kw not in search_content:
                                matched_all_and = False
                                break

                        if matched_all_and:
                            matched_any_or = True
                            break

                    if not matched_any_or:
                        continue

                row_idx = table.rowCount()
                table.insertRow(row_idx)
                table.setItem(row_idx, 0, QTableWidgetItem(str(card_id)))
                table.setItem(row_idx, 1, QTableWidgetItem(str(front)))
                table.setItem(row_idx, 2, QTableWidgetItem(str(box)))
                table.setItem(row_idx, 3, QTableWidgetItem(str(next_review)))

        filter_input.textChanged.connect(refresh_list)
        refresh_list()

        btn_layout = QHBoxLayout()
        edit_btn = QPushButton("Edit Selected")
        del_btn = QPushButton("Delete Selected")
        del_btn.setStyleSheet("background-color: #ffcccc;")

        btn_layout.addWidget(edit_btn)
        btn_layout.addWidget(del_btn)
        layout.addLayout(btn_layout)

        def on_edit():
            selected = table.selectedItems()
            if not selected:
                return
            card_id = selected[0].text()

            c = self.conn.cursor()
            c.execute("SELECT front, back FROM cards WHERE id=?", (card_id,))
            card = c.fetchone()

            new_front_dialog = DynamicInputDialog(dialog, "Edit Word", "Front:", card[0])
            if new_front_dialog.exec() != QDialog.DialogCode.Accepted or not new_front_dialog.text_value:
                return
            new_front = new_front_dialog.text_value

            ml_dialog = DynamicInputDialog(
                dialog,
                "Edit Translation",
                "Enter the translation, meanings, or sample sentences. Markdown and MathJax are supported during review:",
                card[1]
            )
            if ml_dialog.exec() == QDialog.DialogCode.Accepted and ml_dialog.text_value:
                today_str = datetime.date.today().isoformat()

                c.execute(
                    "UPDATE cards SET front=?, back=?, box=1, next_review=? WHERE id=?",
                    (new_front, ml_dialog.text_value, today_str, card_id)
                )
                self.conn.commit()
                refresh_list()
                QMessageBox.information(dialog, "Updated", "Card has been updated and moved back to Box 1 for review today.")

                if self.current_card and str(self.current_card[0]) == str(card_id):
                    self.current_card = (int(card_id), new_front, ml_dialog.text_value, 1)
                    self.is_current_flipped = False
                    if self.card_ui:
                        self.scene.removeItem(self.card_ui)
                        self.card_ui = None
                    self.draw_card_ui()
                else:
                    if self.current_card is not None:
                        self.cards_due = [cf for cf in self.cards_due if cf[0] != int(card_id)]
                        self.cards_due.append((int(card_id), new_front, ml_dialog.text_value, 1))

        def on_delete():
            selected = table.selectedItems()
            if not selected:
                return
            card_id = selected[0].text()
            reply = QMessageBox.question(
                dialog,
                'Confirm',
                'Delete this card?',
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )
            if reply == QMessageBox.StandardButton.Yes:
                self.conn.cursor().execute("DELETE FROM cards WHERE id=?", (card_id,))
                self.conn.commit()
                refresh_list()

                if self.current_card and str(self.current_card[0]) == str(card_id):
                    self.show_next_card()

        edit_btn.clicked.connect(on_edit)
        del_btn.clicked.connect(on_delete)
        table.itemDoubleClicked.connect(lambda item: on_edit())

        dialog.exec()

    # --- Review Flow ---
    def start_review(self):
        if not self.conn:
            return

        self.review_mode = 'daily'

        if self.current_card is not None:
            if self.card_ui:
                self.scene.removeItem(self.card_ui)
                self.card_ui = None
            self.current_card = None

        today_str = datetime.date.today().isoformat()

        c = self.conn.cursor()
        c.execute("SELECT id, front, back, box FROM cards WHERE next_review <= ?", (today_str,))
        self.cards_due = c.fetchall()

        if self.random_checkbox.isChecked():
            random.shuffle(self.cards_due)
        else:
            self.cards_due.sort(key=lambda x: x[0])

        if not self.cards_due:
            QMessageBox.information(self, "Done", "No cards due for review today!")
            self.review_mode = ''
            return

        self.show_next_card()

    def start_forced_review(self, direction='ASC', restart=False):
        if not self.conn:
            QMessageBox.warning(self, "Error", "Load a language database first.")
            return

        target_mode = 'force_seq' if direction == 'ASC' else 'force_rev'

        if restart:
            current_id = None
        else:
            if self.current_card is not None:
                current_id = self.current_card[0]
            else:
                current_id = 0

        if self.current_card is not None:
            if self.card_ui:
                self.scene.removeItem(self.card_ui)
                self.card_ui = None
            self.current_card = None

        self.review_mode = target_mode

        c = self.conn.cursor()

        if current_id is not None and current_id != 0:
            if direction == 'ASC':
                query = "SELECT id, front, back, box FROM cards WHERE id > ? ORDER BY id ASC"
            else:
                query = "SELECT id, front, back, box FROM cards WHERE id < ? ORDER BY id DESC"
            c.execute(query, (current_id,))
            self.cards_due = c.fetchall()

            if not self.cards_due:
                wrap_query = f"SELECT id, front, back, box FROM cards ORDER BY id {direction}"
                c.execute(wrap_query)
                self.cards_due = c.fetchall()
        else:
            query = f"SELECT id, front, back, box FROM cards ORDER BY id {direction}"
            c.execute(query)
            self.cards_due = c.fetchall()

        if not self.cards_due:
            QMessageBox.information(self, "Empty", "There are no cards in the database.")
            self.review_mode = ''
            return

        self.show_next_card()

    def restart_current_review(self):
        if not self.conn:
            return

        self.start_forced_review(direction='ASC', restart=True)

    def show_next_card(self):
        if self.card_ui:
            self.scene.removeItem(self.card_ui)
            self.card_ui = None

        while self.cards_due:
            stale_card = self.cards_due.pop(0)
            card_id = stale_card[0]

            c = self.conn.cursor()
            c.execute("SELECT id, front, back, box FROM cards WHERE id = ?", (card_id,))
            fresh_card = c.fetchone()

            if fresh_card:
                self.current_card = fresh_card
                self.is_current_flipped = False
                self.draw_card_ui()
                return

        if self.review_mode == 'force_seq':
            self.start_forced_review(direction='ASC', restart=True)
            return
        elif self.review_mode == 'force_rev':
            self.start_forced_review(direction='DESC', restart=True)
            return

        QMessageBox.information(self, "Done", "You have finished your reviews.")
        self.current_card = None
        self.review_mode = ''

    def draw_card_ui(self):
        if not self.current_card:
            return

        card_id, front, back, box = self.current_card

        w = max(400, self.scene.width())
        h = max(400, self.scene.height())

        cw = int(w * 0.75)
        ch = int(h * 0.75)
        cx = w / 2
        cy = (h - 100) / 2

        self.card_ui = FlashCardItem(self, cx, cy, cw, ch)

        metadata_md = f"**Box {box}** | ID: `{card_id}`"

        if self.is_current_flipped:
            spoken_front = markdown_to_plain_text(front)
            spoken_back = markdown_to_plain_text(back)
            spoken_text = f"{spoken_front}. {spoken_back}".strip()
            display_md = f"{metadata_md}\n\n{front}\n\n---\n\n{back}"
            self.card_ui.set_text(display_md, True, spoken_text)
        else:
            spoken_front = markdown_to_plain_text(front)
            display_md = f"{metadata_md}\n\n{front}"
            self.card_ui.set_text(display_md, False, spoken_front)

        self.scene.addItem(self.card_ui)

    def flip_card(self):
        if not self.current_card:
            return

        self.is_current_flipped = True
        card_id, front, back, box = self.current_card

        metadata_md = f"**Box {box}** | ID: `{card_id}`"
        display_md = f"{metadata_md}\n\n{front}\n\n---\n\n{back}"

        spoken_front = markdown_to_plain_text(front)
        spoken_back = markdown_to_plain_text(back)
        spoken_text = f"{spoken_front}. {spoken_back}".strip()

        self.card_ui.set_text(display_md, True, spoken_text)

    def check_card_drop(self, card_item):
        if not self.incorrect_zone or not self.correct_zone:
            return
        card_rect = card_item.sceneBoundingRect()
        inc_rect = self.incorrect_zone.sceneBoundingRect()
        cor_rect = self.correct_zone.sceneBoundingRect()

        if card_rect.intersects(inc_rect):
            QTimer.singleShot(0, lambda: self.process_answer(correct=False))
        elif card_rect.intersects(cor_rect):
            QTimer.singleShot(0, lambda: self.process_answer(correct=True))
        else:
            card_item.setPos(self.scene.width() / 2, (self.scene.height() - 100) / 2)

    def process_answer(self, correct):
        if not self.current_card:
            return
        card_id, _, _, current_box = self.current_card
        today = datetime.date.today()

        new_box = min(current_box + 1, 5) if correct else (3 if current_box >= 3 else 1)
        intervals = {1: 1, 2: 3, 3: 7, 4: 30, 5: 365}
        next_review_str = (today + datetime.timedelta(days=intervals[new_box])).isoformat()

        c = self.conn.cursor()
        c.execute(
            "UPDATE cards SET box = ?, next_review = ? WHERE id = ?",
            (new_box, next_review_str, card_id)
        )
        self.conn.commit()
        self.show_next_card()

    # --- Settings ---
    def open_settings_window(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("App Settings")
        layout = QFormLayout(dialog)

        w_input = QSpinBox()
        w_input.setRange(400, 3000)
        w_input.setValue(self.settings["width"])

        h_input = QSpinBox()
        h_input.setRange(400, 3000)
        h_input.setValue(self.settings["height"])

        font_combo = QComboBox()
        font_combo.addItems(QFontDatabase.families())
        font_combo.setCurrentText(self.settings["font_family"])

        size_input = QSpinBox()
        size_input.setRange(8, 36)
        size_input.setValue(self.settings["font_size"])

        lang_input = QLineEdit(self.settings.get("default_database", ""))
        lang_input.setPlaceholderText("Database path (set by selecting a database)")

        tts_input = QLineEdit(self.settings.get("tts_voice", "en-US-AvaMultilingualNeural"))
        tts_input.setPlaceholderText("e.g. en-US-AvaMultilingualNeural")

        layout.addRow("Window Width:", w_input)
        layout.addRow("Window Height:", h_input)
        layout.addRow("Font Family:", font_combo)
        layout.addRow("Font Size:", size_input)
        layout.addRow("Default Database:", lang_input)
        layout.addRow("TTS Voice (Edge-TTS):", tts_input)

        save_btn = QPushButton("Save & Apply")
        save_btn.setStyleSheet("background-color: #ccffcc;")
        layout.addRow(save_btn)

        def save_and_apply():
            self.settings["width"] = w_input.value()
            self.settings["height"] = h_input.value()
            self.settings["font_family"] = font_combo.currentText()
            self.settings["font_size"] = size_input.value()
            self.settings["default_database"] = lang_input.text().strip()
            self.settings["tts_voice"] = tts_input.text().strip()

            self.save_settings()
            self.resize(self.settings["width"], self.settings["height"])
            self.apply_font_settings()

            dialog.accept()

        save_btn.clicked.connect(save_and_apply)
        dialog.exec()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = BarskyApp()
    window.show()
    sys.exit(app.exec())
