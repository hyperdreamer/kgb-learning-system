"""Graphics items: DropZoneItem, FlashCardItem.

These are QGraphicsItem subclasses for the canvas-based review UI.
"""

from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QPushButton,
    QGraphicsProxyWidget,
    QGraphicsTextItem,
    QGraphicsRectItem,
    QTextEdit,
)
from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtGui import (
    QColor, QBrush, QPen, QPainterPath, QIcon, QDesktopServices,
)

try:
    from PyQt6.QtWebEngineWidgets import QWebEngineView
    try:
        from PyQt6.QtWebEngineCore import QWebEnginePage, QWebEngineSettings
    except ImportError:
        QWebEnginePage = None
        QWebEngineSettings = None
    HAS_WEBENGINE = True
except ImportError:
    QWebEngineView = None
    QWebEnginePage = None
    QWebEngineSettings = None
    HAS_WEBENGINE = False

from .markdown_utils import build_review_html


class ReviewCardNavigationPolicy:
    """Strict navigation policy for untrusted review-card HTML.

    The embedded review view is display-only.  Explicit HTTP(S) links may be
    handed to the operating system, while every navigation stays out of the
    card (including local-file, data, JavaScript, and custom schemes).
    """

    BLOCKED_SCHEMES = frozenset({"file", "data", "javascript", "qrc", "mailto", "ftp"})
    EXTERNAL_SCHEMES = frozenset({"http", "https"})

    @staticmethod
    def _url_text(url: QUrl | str) -> str:
        return url.toString() if isinstance(url, QUrl) else str(url or "")

    @classmethod
    def should_open_externally(cls, url: QUrl | str) -> bool:
        """Return whether *url* is an HTTP(S) link suitable for the desktop."""
        parsed = QUrl(cls._url_text(url))
        return parsed.isValid() and parsed.scheme().lower() in cls.EXTERNAL_SCHEMES and bool(parsed.host())

    @classmethod
    def allows_embedded_navigation(cls, url: QUrl | str) -> bool:
        """Review cards are never allowed to navigate their embedded page."""
        return False


def route_review_card_link(url: QUrl | str, opener=QDesktopServices.openUrl) -> bool:
    """Open a permitted card link outside the application, returning success."""
    if not ReviewCardNavigationPolicy.should_open_externally(url):
        return False
    qurl = url if isinstance(url, QUrl) else QUrl(str(url))
    return bool(opener(qurl))


if QWebEnginePage is not None:
    class ReviewCardWebPage(QWebEnginePage):
        """Display-only page which delegates user links to the desktop."""

        def acceptNavigationRequest(self, url, navigation_type, is_main_frame):
            link_click = (
                navigation_type
                == QWebEnginePage.NavigationType.NavigationTypeLinkClicked
            )
            if link_click:
                route_review_card_link(url)
            return ReviewCardNavigationPolicy.allows_embedded_navigation(url)
else:
    ReviewCardWebPage = None


def configure_review_web_view(web_view) -> None:
    """Apply the review-card's no-script, no-local-content web policy."""
    if QWebEngineSettings is None:
        return
    settings = web_view.settings()
    settings.setAttribute(
        QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, False,
    )
    settings.setAttribute(
        QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, False,
    )
    settings.setAttribute(
        QWebEngineSettings.WebAttribute.JavascriptEnabled, False,
    )


# ── Reusable button stylesheet helper ────────────────────────────────────────

def _button_stylesheet(object_name, base_color, hover_color, pressed_color,
                       font_fam, font_sz, dyn_pad):
    """Return a full QPushButton stylesheet covering normal, hover, pressed,
    and disabled states."""
    shared = (
        f"color: white; "
        f"padding: {dyn_pad}px; "
        f"font-family: '{font_fam}'; "
        f"font-size: {font_sz}px; "
        f"font-weight: bold; "
        f"border-radius: 5px;"
    )

    return (
        f"QPushButton#{object_name} {{ background-color: {base_color}; {shared} }}\n"
        f"QPushButton#{object_name}:hover {{ background-color: {hover_color}; }}\n"
        f"QPushButton#{object_name}:pressed {{ background-color: {pressed_color}; }}\n"
        f"QPushButton#{object_name}:disabled "
        f"{{ background-color: #CFD8DC; color: #78909C; }}"
    )


class DropZoneItem(QGraphicsRectItem):
    """A rounded drop zone (correct/incorrect) at the bottom of the canvas."""

    def __init__(self, x, y, w, h, pen, brush, text_html, is_correct, app_ref):
        super().__init__()

        self.is_correct = is_correct
        self.app_ref = app_ref
        self._hovered = False

        self.setAcceptHoverEvents(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        self.text_item = QGraphicsTextItem(self)
        self.text_item.setHtml(text_html)
        self.text_item.setTextWidth(w - 24)  # pad for rounded corners

        text_rect = self.text_item.boundingRect()
        actual_h = max(h, text_rect.height() + 20)

        bottom_y = y + h
        adjusted_y = bottom_y - actual_h
        # Never allow the zone to extend above the scene origin —
        # the viewport clips from below automatically.
        if adjusted_y < 0:
            adjusted_y = 0

        self.setRect(0, 0, w, actual_h)
        self.setPos(x, adjusted_y)
        self._pen = pen
        self._brush = brush
        self._brush_dim = QBrush(brush.color().darker(115))
        self.setPen(QPen(Qt.PenStyle.NoPen))
        self.setBrush(QBrush(Qt.BrushStyle.NoBrush))

        text_y = (actual_h - text_rect.height()) / 2
        self.text_item.setPos(12, text_y)

    def paint(self, painter, option, widget):
        painter.setRenderHint(painter.RenderHint.Antialiasing)
        rect = self.rect()
        radius = 12

        path = QPainterPath()
        path.addRoundedRect(rect, radius, radius)

        brush = self._brush_dim if self._hovered else self._brush
        painter.fillPath(path, brush)
        painter.setPen(self._pen if self._hovered else QPen(self._pen.color().darker(120), 2))
        painter.drawPath(path)

        # let text paint via child item

    def hoverEnterEvent(self, event):
        self._hovered = True
        self.update()
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event):
        self._hovered = False
        self.update()
        super().hoverLeaveEvent(event)

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
            if ReviewCardWebPage is not None:
                self.text_widget.setPage(ReviewCardWebPage(self.text_widget))
            configure_review_web_view(self.text_widget)

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

        self.tts_btn = QPushButton(" Listen")
        self.tts_btn.setIcon(QIcon.fromTheme("audio-volume-high"))
        self.tts_btn.setObjectName("ttsBtn")
        self.tts_btn.setToolTip("Speak this card (Alt+L)")
        self.tts_btn.setStyleSheet(
            _button_stylesheet(
                "ttsBtn", "#9C27B0", "#AB47BC", "#8E24AA",
                font_fam, font_sz, dyn_pad,
            )
        )
        self.tts_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.tts_btn.clicked.connect(self.trigger_tts)

        self.flip_btn = QPushButton(" Reveal Answer")
        self.flip_btn.setObjectName("revealBtn")
        self.flip_btn.setToolTip("Reveal the answer (Alt+R)")
        self.flip_btn.setStyleSheet(
            _button_stylesheet(
                "revealBtn", "#2196F3", "#42A5F5", "#1E88E5",
                font_fam, font_sz, dyn_pad,
            )
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

        font_fam = self.app_ref.settings.get("content_font_family", "Arial")
        font_sz = self.app_ref.settings.get("content_font_size", 18)

        html_template = build_review_html(
            display_text,
            font_family=font_fam,
            font_size=font_sz,
            include_mathjax=HAS_WEBENGINE,
        )

        if HAS_WEBENGINE:
            # A stable non-file origin ensures user Markdown is never based on
            # the application's working directory.
            self.text_widget.setHtml(html_template, QUrl("about:blank"))
        else:
            self.text_widget.setHtml(html_template)

        if is_flipped:
            self.flip_btn.hide()
        else:
            self.flip_btn.show()

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        self.app_ref.check_card_drop(self)
