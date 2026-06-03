"""Graphics items: DropZoneItem, FlashCardItem.

These are QGraphicsItem subclasses for the canvas-based review UI.
"""

import os

from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QPushButton,
    QGraphicsProxyWidget,
    QGraphicsTextItem,
    QGraphicsRectItem,
    QTextEdit,
    QApplication,
)
from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtGui import QColor, QBrush, QPen

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

from .markdown_utils import build_review_html


class DropZoneItem(QGraphicsRectItem):
    """A drop zone (correct/incorrect) at the bottom of the canvas."""

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


class FlashCardItem(QGraphicsRectItem):
    """The draggable, flippable flash card in the center of the canvas."""

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

            try:
                if QWebEngineSettings is not None:
                    web_settings = self.text_widget.settings()
                    web_settings.setAttribute(
                        QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls,
                        True,
                    )
                    web_settings.setAttribute(
                        QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls,
                        True,
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
            self.text_widget.setStyleSheet(
                "background-color: transparent; border: none; color: black;"
            )
            self.text_widget.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextBrowserInteraction
            )

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
            include_mathjax=HAS_WEBENGINE,
        )

        if HAS_WEBENGINE:
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
